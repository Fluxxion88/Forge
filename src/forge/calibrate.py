"""Stage 1 — calibration. Sentinel pass, rasterise, semantic pass, crop escalation.

Phase 1 of docs/04-BUILD-PLAN.md; specification in docs/02-SPEC.md §1.

The semantic pass runs one vision call per rendered page. Any field that comes back
with `confidence: low` or no `printedLabel` is escalated: we re-render a crop around
each of the field's widget rectangles, extended ~120pt to the left and ~40pt
vertically so the printed caption is in frame, and re-query in small batches. A null
`itemNumber` alone never escalates — many forms number nothing. A field that still
resolves to nothing lands in `unresolved` — a first-class output, not an error.

STATUS NOTE (2026-07-27): the escalation path has never completed end to end — both
DL 142 runs that exercised it timed out, and after the predicate fix neither DL 142
nor Form 56 fired it. Treat it as untested; SS-4 or 8821 will exercise it first.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from . import llm
from .fillwriter import render_pages, write_filled
from .pdfmeta import FieldRecord, read_form
from .registry import CALIBRATION_DIR, RENDERS_DIR, get_form, rel, sha256_of

DPI = 150
SCALE = DPI / 72.0
# Crop margins in PDF points: captions sit left of and above/below the box.
CROP_LEFT_PT = 120.0
CROP_RIGHT_PT = 15.0
CROP_VERT_PT = 40.0
ESCALATION_BATCH = 4  # crops per model call; 8-image batches were timing out at 180s
MAX_CONCURRENT_CALLS = 2  # parallel image uploads thrash a slow connection
GATE_PCT = 90.0

VALID_CONFIDENCE = {"high", "medium", "low"}


class _Progress:
    """Stdout progress around every model call; safe under the 2-worker pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self._last_elapsed: float | None = None

    def timed_call(self, label: str, fields: int, fn):
        with self._lock:
            self._n += 1
            n = self._n
            prev = f"{self._last_elapsed:.1f}s" if self._last_elapsed is not None else "n/a"
            print(f"[call {n}] {label} ({fields} field(s)) — prev call took {prev}", flush=True)
        t0 = time.monotonic()
        try:
            return fn()
        finally:
            with self._lock:
                self._last_elapsed = time.monotonic() - t0

    def call_with_retry(self, label: str, fields: int, fn, attempts: int = 2) -> str | None:
        """Retries on failure; exhausting attempts skips the batch rather than
        killing the run — the affected fields surface in `unresolved`."""
        for attempt in range(1, attempts + 1):
            try:
                return self.timed_call(f"{label} (attempt {attempt})", fields, fn)
            except llm.ModelCallFailed as exc:
                print(f"  WARN {label} attempt {attempt} failed: {exc}", flush=True)
        print(f"  WARN {label} skipped after {attempts} attempt(s)", flush=True)
        return None


@dataclass
class Entry:
    """One calibratable field and everything the semantic passes learn about it."""

    rec: FieldRecord
    ident: str  # short id the model reports back: the token for text, B<n> for buttons
    token: str | None  # sentinel actually written, text fields only
    item_number: str | None = None
    printed_label: str | None = None
    meaning: str | None = None
    confidence: str = "low"
    escalated: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.item_number or self.printed_label)

    @property
    def needs_escalation(self) -> bool:
        """Escalate on low confidence or a missing label — NEVER on a null
        itemNumber alone. Many forms (DL 142 among them) number nothing at all,
        and on those the itemNumber trigger fires for every field."""
        return self.confidence == "low" or not self.printed_label


def _px_bbox(rect: list[float], page_height: float) -> list[int]:
    """PDF rect (origin bottom-left) -> pixel bbox (origin top-left) at DPI."""
    x0, y0, x1, y1 = rect
    return [
        round(x0 * SCALE),
        round((page_height - y1) * SCALE),
        round(x1 * SCALE),
        round((page_height - y0) * SCALE),
    ]


def assign_tokens(fields: list[FieldRecord]) -> dict[str, str]:
    """Unique sentinel per text field. /MaxLen is deliberately ignored here: a
    truncated token (e.g. "07" in a 2-char state box) is ambiguous and unrecoverable.
    Overflow is fine — the sentinel render is a diagnostic snapshot, not a filing.
    MaxLen enforcement belongs to `forge fill` only."""
    return {f.qualified_name: f"Z{i:03d}" for i, f in enumerate(fields) if f.type == "text"}


def _apply(entry: Entry, row: dict[str, Any], escalated: bool) -> None:
    conf = row.get("confidence")
    entry.item_number = row.get("itemNumber") or entry.item_number
    entry.printed_label = row.get("printedLabel") or entry.printed_label
    entry.meaning = row.get("meaning") or entry.meaning
    entry.confidence = conf if conf in VALID_CONFIDENCE else "low"
    entry.escalated = escalated


def _entry_lines(entries: list[Entry], page: int, page_height: float) -> str:
    lines = []
    for e in entries:
        boxes = [
            _px_bbox(w.rect, page_height)
            for w in e.rec.widgets
            if w.page == page and w.rect
        ]
        if e.rec.type == "text":
            what = f'text box showing the token "{e.token}"'
        else:
            what = "checkbox (shown ticked in image B)"
        hint = f' tooltipHint="{e.rec.tooltip}"' if e.rec.tooltip else ""
        if len(boxes) > 1:
            lines.append(
                f"- id={e.ident}  {what}  appears {len(boxes)} times (one field, "
                f"duplicate copies) at pixelBboxes={boxes}{hint}"
            )
        else:
            lines.append(f"- id={e.ident}  {what}  pixelBbox={boxes[0]}{hint}")
    return "\n".join(lines)


PAGE_RULES = """For every entry report:
- itemNumber: the item number/letter printed on the paper next to that field, e.g. "1b", "7a", "9". null if the form does not number that area.
- printedLabel: the caption printed on the form for that field, verbatim. null if none.
- meaning: one short plain-English sentence saying what belongs in the field.
- confidence: "high" | "medium" | "low".

Rules:
- Locate text fields by their unique token; verify with the pixel bounding box (origin top-left, 150 dpi). Locate checkboxes by bounding box in image B.
- If you cannot locate an entry or cannot read its caption, return null values with confidence "low". NEVER guess: a wrong itemNumber is worse than a missing one, because a reviewer trusts it.
- Answer with ONLY a JSON array: [{"id": ..., "itemNumber": ..., "printedLabel": ..., "meaning": ..., "confidence": ...}, ...] — one object per entry, ids exactly as given."""


def _page_pass(
    form_id: str,
    page: int,
    text_png: Path,
    btn_png: Path,
    entries: list[Entry],
    page_height: float,
    progress: _Progress,
) -> None:
    duplicated = any(
        sum(1 for w in e.rec.widgets if w.page == page and w.rect) > 1 for e in entries
    )
    dup_note = (
        "\nNOTE: this page prints the form TWICE (an identical tear-off copy below a"
        " cut line). An entry listing two bounding boxes is ONE field rendered in both"
        " copies — report it once. Entries with a single bounding box that look alike"
        " across the two copies are DIFFERENT fields; disambiguate by bounding box.\n"
        if duplicated
        else ""
    )
    prompt = f"""You are calibrating page {page} of government form {form_id}.

First use the Read tool to view BOTH images:
- image A (every text field filled with a unique sentinel token): {text_png}
- image B (every checkbox ticked): {btn_png}
{dup_note}
Entries on this page:
{_entry_lines(entries, page, page_height)}

{PAGE_RULES}"""
    reply = progress.call_with_retry(
        f"page-pass page {page}",
        len(entries),
        lambda: llm.client.call(
            purpose=f"calibrate:{form_id}:page{page}", prompt=prompt, images=2
        ),
    )
    if reply is None:
        return
    try:
        rows = {str(r.get("id")): r for r in llm.extract_json_array(reply)}
    except llm.ModelCallFailed as exc:
        print(f"  WARN page {page}: {exc}", flush=True)
        return
    for e in entries:
        row = rows.get(e.ident)
        if row:
            _apply(e, row, escalated=False)


def _crop_pass(
    form_id: str, batch: list[tuple[Entry, list[Path]]], batch_no: int, progress: _Progress
) -> None:
    lines = []
    for e, crop_paths in batch:
        what = (
            f'the text box containing the token "{e.token}"'
            if e.rec.type == "text"
            else "the ticked checkbox"
        )
        paths = ", ".join(str(p) for p in crop_paths)
        extra = " (same field shown from both duplicate copies)" if len(crop_paths) > 1 else ""
        lines.append(f"- id={e.ident}  image(s): {paths}{extra}  — find {what} and its printed caption")
    prompt = f"""You are calibrating form {form_id}. Each image below is a CROP around one form field, extended left and vertically so the printed caption is in frame. The field of interest sits toward the RIGHT side of the crop.

Use the Read tool to view every image:
{chr(10).join(lines)}

{PAGE_RULES}"""
    n_images = sum(len(paths) for _, paths in batch)
    reply = progress.call_with_retry(
        f"crop-pass batch {batch_no}",
        len(batch),
        lambda: llm.client.call(
            purpose=f"calibrate:{form_id}:crops", prompt=prompt, images=n_images
        ),
    )
    if reply is None:
        return
    try:
        rows = {str(r.get("id")): r for r in llm.extract_json_array(reply)}
    except llm.ModelCallFailed as exc:
        print(f"  WARN crop batch {batch_no}: {exc}", flush=True)
        return
    for e, _ in batch:
        row = rows.get(e.ident)
        if row:
            _apply(e, row, escalated=True)


def _render_crops(
    src_pdf: Path, out_dir: Path, e: Entry, heights: list[float], widths: list[float]
) -> list[Path]:
    """One crop per widget — never just the first. On tear-off forms the copies can
    differ in caption context, and near-identical single-widget fields (the two DATE
    boxes on DL 142) are only distinguishable by their own widget's rect."""
    crops: list[Path] = []
    for wi, w in enumerate(e.rec.widgets):
        if w.page is None or w.rect is None:
            continue
        page_height, page_width = heights[w.page], widths[w.page]
        x0, y0, x1, y1 = w.rect
        cx0 = max(0.0, x0 - CROP_LEFT_PT)
        cx1 = min(page_width, x1 + CROP_RIGHT_PT)
        cy0 = max(0.0, y0 - CROP_VERT_PT)
        cy1 = min(page_height, y1 + CROP_VERT_PT)
        px = round(cx0 * SCALE)
        py = round((page_height - cy1) * SCALE)
        pw = max(1, round((cx1 - cx0) * SCALE))
        ph = max(1, round((cy1 - cy0) * SCALE))
        crops += render_pages(
            src_pdf, out_dir, f"crop-{e.ident}-w{wi}", dpi=DPI,
            crop_px=(px, py, pw, ph), page=w.page,
        )
    return crops


def calibrate(form_id: str) -> int:
    form = get_form(form_id)
    info = read_form(form.path)
    render_dir = RENDERS_DIR / form_id
    render_dir.mkdir(parents=True, exist_ok=True)

    # --- sentinel passes: two filled PDFs, rendered separately (docs/02-SPEC.md §1.2)
    tokens = assign_tokens(info.fields)
    text_pdf = render_dir / "sentinel-text.pdf"
    btn_pdf = render_dir / "sentinel-btn.pdf"
    missing = write_filled(form.path, text_pdf, texts=tokens)
    buttons = {
        f.qualified_name: f.on_value
        for f in info.of_type("button")
        if f.on_value and not f.is_pushbutton
    }
    missing += write_filled(form.path, btn_pdf, buttons=buttons)
    if missing:
        print(f"FAIL: could not locate field nodes: {missing[:5]}")
        return 1

    text_pngs = render_pages(text_pdf, render_dir, "sentinel-text", dpi=DPI)
    btn_pngs = render_pages(btn_pdf, render_dir, "sentinel-btn", dpi=DPI)
    print(f"sentinel renders: {len(text_pngs)} text page(s), {len(btn_pngs)} button page(s)")

    # --- entries: every /Tx and /Btn field
    reader = PdfReader(str(form.path))
    heights = [float(p.mediabox.height) for p in reader.pages]
    widths = [float(p.mediabox.width) for p in reader.pages]
    entries: list[Entry] = []
    btn_count = 0
    for f in info.fields:
        if f.type == "text":
            entries.append(
                Entry(rec=f, ident=tokens[f.qualified_name], token=tokens[f.qualified_name])
            )
        elif f.type == "button":
            entries.append(Entry(rec=f, ident=f"B{btn_count}", token=None))
            btn_count += 1
    placeable = [e for e in entries if e.rec.page is not None and e.rec.rect is not None]

    # --- semantic pass: one call per page (docs/02-SPEC.md §1.3)
    progress = _Progress()
    calls_before = llm.client.count
    for page in sorted({e.rec.page for e in placeable}):
        page_entries = [e for e in placeable if e.rec.page == page]
        _page_pass(
            form_id, page, text_pngs[page], btn_pngs[page], page_entries, heights[page], progress
        )
        resolved_here = sum(1 for e in page_entries if e.resolved)
        if resolved_here == 0:
            # Fail loudly: a page pass that resolves nothing means the model did not
            # usefully see the page. Escalating every field to crops would hide that.
            print(
                f"ABORT: page pass resolved 0 of {len(page_entries)} fields on page "
                f"{page}. The vision transport is not seeing the render. Inspect the "
                f"prompt/reply pair under {rel(llm.CALL_LOG_DIR)}/ and the image "
                f"{rel(text_pngs[page])} before rerunning."
            )
            return 1

    # --- crop escalation: confidence low or printedLabel missing. Batched crops, at
    # most MAX_CONCURRENT_CALLS calls in flight — parallel uploads thrash slow links.
    crop_dir = render_dir / "crops"
    to_escalate = [e for e in placeable if e.needs_escalation]
    print(f"escalating {len(to_escalate)}/{len(placeable)} field(s) via caption crops")
    with_crops = [
        (e, _render_crops(text_pdf if e.rec.type == "text" else btn_pdf, crop_dir, e,
                          heights, widths))
        for e in to_escalate
    ]
    with_crops = [(e, paths) for e, paths in with_crops if paths]
    batches = [
        with_crops[i : i + ESCALATION_BATCH]
        for i in range(0, len(with_crops), ESCALATION_BATCH)
    ]
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS) as pool:
        futures = [
            pool.submit(_crop_pass, form_id, b, i, progress) for i, b in enumerate(batches)
        ]
        for f in futures:
            f.result()

    # --- artifact (docs/02-SPEC.md §1.4)
    unresolved = sorted(
        e.rec.qualified_name for e in entries if not e.resolved or e.confidence == "low"
    )
    artifact = {
        "formId": form_id,
        "sourceFile": rel(form.path),
        "sourceSha256": sha256_of(form.path),
        "calibratedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": llm.DEFAULT_MODEL,
        "modelCalls": llm.client.count - calls_before,
        "dpi": DPI,
        "pageCount": info.page_count,
        "fields": [
            {
                "qualifiedName": e.rec.qualified_name,
                "type": e.rec.type,
                "page": e.rec.page,
                "rect": e.rec.rect,
                "onValue": e.rec.on_value if e.rec.type == "button" else None,
                "isPushbutton": e.rec.is_pushbutton if e.rec.type == "button" else False,
                "maxLen": e.rec.max_len,
                "tooltip": e.rec.tooltip,
                "sentinel": e.ident,
                "itemNumber": e.item_number,
                "printedLabel": e.printed_label,
                "meaning": e.meaning,
                "confidence": e.confidence,
                "escalated": e.escalated,
            }
            for e in entries
        ],
        "unresolved": unresolved,
    }
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    out = CALIBRATION_DIR / f"{form_id}.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # --- gate (docs/02-SPEC.md §1.5)
    labelled = sum(1 for e in entries if e.resolved)
    pct = 100.0 * labelled / len(entries) if entries else 0.0
    print(f"wrote {rel(out)}")
    print(
        f"{form_id}: {labelled}/{len(entries)} fields labelled ({pct:.1f}%), "
        f"{len(unresolved)} unresolved, {artifact['modelCalls']} model call(s)"
    )
    ok = pct >= GATE_PCT and bool(text_pngs) and bool(btn_pngs)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1
