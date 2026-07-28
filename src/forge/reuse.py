"""The reuse proof: one binding, five estates, no model.

The single most important demo asset. It runs the real fill path over every estate
in inputs/estates/ using ONE binding artifact, then crops the same region out of each
resulting page so the differences are visible side by side: the line-1 authority box
moves, the 2a/2b date flips branch, the fiduciary's title changes — and the binding
file is byte-identical across all five, which the report states with its sha256.

No model is consulted anywhere in here; the fills run inside forbid_model_calls().
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import llm
from .estatepath import EstateData
from .fill import fill_pdf, load_approved
from .fillwriter import render_pages
from .registry import (
    APPROVED_DIR,
    BINDINGS_DIR,
    CALIBRATION_DIR,
    ESTATES_DIR,
    OUT,
    estate_path,
    get_form,
    load_work_order,
    rel,
)

DEMO = OUT / "demo"
FORM_ID = "irs-f56"
DPI = 150

# Cold-start evidence, read off the call-log mtimes in out/reports/calls/ and the
# wall time the propose command printed. Recorded here rather than recomputed so the
# report cannot quietly drift from what was actually observed.
COLD = {
    "calibrateSeconds": 83,        # 19:54:16 -> 19:55:39, 2 vision calls, both pages
    "calibrateModelCalls": 2,
    "proposeSeconds": 402,         # 21:09:13 -> 21:15:54, per-page, 2 calls
    "proposeModelCalls": 2,
    "wastedOnTimeoutsSeconds": 840,  # two whole-form attempts at 420s, before the
                                     # per-page fallback existed; excluded from the
                                     # headline because a fresh run no longer pays it
}


def _label_font(size: int = 15):
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _bbox_px(cal: dict[str, Any], page: int, picks, pad_left=24, pad_right=320,
             pad_top=26, pad_bottom=12) -> tuple[int, int, int, int] | None:
    """Pixel crop box for a set of calibrated fields — driven by the artifact's rects,
    never hardcoded. Y is flipped against the page's measured crop box."""
    rects = [f["rect"] for f in cal["fields"] if f["page"] == page and picks(f) and f["rect"]]
    if not rects:
        return None
    pages = cal.get("pages") or []
    cy1 = pages[page]["cropBox"][3] if pages else 792.0
    page_w = pages[page]["widthPt"] if pages else 612.0
    xs = [r[0] for r in rects] + [r[2] for r in rects]
    ys = [r[1] for r in rects] + [r[3] for r in rects]
    scale = DPI / 72.0
    x0 = max(0.0, min(xs) - pad_left)
    x1 = min(page_w, max(xs) + pad_right)
    y_top = min(cy1, max(ys) + pad_top)
    y_bot = max(0.0, min(ys) - pad_bottom)
    return (
        round(x0 * scale),
        round((cy1 - y_top) * scale),
        round((x1 - x0) * scale),
        round((y_top - y_bot) * scale),
    )


def _crops(cal: dict[str, Any]) -> dict[str, tuple[int, tuple[int, int, int, int]]]:
    """The two regions the proof needs: Section A (line 1 + 2a/2b) and Part IV title."""
    out: dict[str, tuple[int, tuple[int, int, int, int]]] = {}
    section_a = _bbox_px(
        cal, 0,
        lambda f: ".c1_1[" in f["qualifiedName"]
        or f["qualifiedName"].endswith(("f1_19[0]", "f1_20[0]")),
        # 44pt of left pad, not 24: the printed "Section A—Authority" rule starts well
        # to the left of the first checkbox rect and gets clipped otherwise
        pad_left=44,
    )
    if section_a:
        out["section-a"] = (0, section_a)
    title = _bbox_px(
        cal, 1, lambda f: "Title" in (f["printedLabel"] or ""),
        pad_left=250, pad_right=40, pad_top=14, pad_bottom=22,
    )
    if title:
        out["title"] = (1, title)
    return out


def _fill_one(
    binding: dict[str, Any], estate_id: str, form_path: Path, reuse_dir: Path
) -> dict[str, Any]:
    """One real fill, measured. Identical code path to `forge fill`."""
    estate = EstateData.load(estate_path(estate_id))
    out_pdf = reuse_dir / f"{estate_id}.pdf"
    calls_before = llm.client.count
    t0 = time.monotonic()
    with llm.forbid_model_calls():
        result = fill_pdf(binding, estate, form_path, out_pdf)
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    pngs = render_pages(out_pdf, reuse_dir, estate_id, dpi=DPI)

    order = load_work_order(estate_id)
    by_item = {f.item_number: f for f in result.fields if f.item_number}
    line1 = sorted(
        f.item_number for f in result.fields
        if f.checked and (f.item_number or "").startswith("1")
        and len(f.item_number or "") == 2
    )
    title = next(
        (f.value for f in result.fields if "Title" in (f.label or "") and f.value), None
    )
    return {
        "estateId": estate_id,
        "jurisdiction": f"{order['jurisdiction']['state']}/{order['jurisdiction']['county']}",
        "route": order["route"],
        "authorityBasis": EstateData.load(estate_path(estate_id)).resolve("authority.basis").value,
        "line1Ticked": line1,
        "date2a": (by_item.get("2a").value if by_item.get("2a") else None),
        "date2b": (by_item.get("2b").value if by_item.get("2b") else None),
        "fiduciaryTitle": title,
        "fieldsFilled": sum(1 for f in result.fields if f.filled),
        "fieldsEmpty": len(result.empty),
        "guardedOff": sum(1 for f in result.fields if f.guarded_off),
        "groupViolations": result.group_violations,
        "llmCallsAtRuntime": llm.client.count - calls_before,
        "elapsedMs": elapsed_ms,
        "pdf": rel(out_pdf),
        "renders": [rel(p) for p in pngs],
    }


def _strip(
    rows: list[dict[str, Any]], crops: dict, out_png: Path, title_text: str, reuse_dir: Path
) -> Path:
    """Stack the five estates' crops so the differences line up vertically.

    Vertical, not horizontal: Section A is ~550pt wide and ~110pt tall, so five of
    them across would be 2700px+ wide and unreadable on a projector. Stacked, the
    line-1 column and the 2a/2b rows sit directly above one another, which is what
    makes the difference legible at a glance.
    """
    font, small = _label_font(17), _label_font(13)
    band_h, gap, margin = 30, 14, 16
    tiles: list[tuple[dict, Image.Image, Image.Image | None]] = []
    for r in rows:
        page0 = Image.open(reuse_dir / f"{r['estateId']}-page-0.png")
        a_page, a_box = crops["section-a"]
        sec = page0.crop((a_box[0], a_box[1], a_box[0] + a_box[2], a_box[1] + a_box[3]))
        ttl = None
        if "title" in crops:
            t_page, t_box = crops["title"]
            pg = Image.open(reuse_dir / f"{r['estateId']}-page-{t_page}.png")
            ttl = pg.crop((t_box[0], t_box[1], t_box[0] + t_box[2], t_box[1] + t_box[3]))
        tiles.append((r, sec, ttl))

    body_w = max(t[1].width for t in tiles)
    ttl_w = max((t[2].width for t in tiles if t[2]), default=0)
    width = margin * 2 + body_w + (gap + ttl_w if ttl_w else 0)
    header_h = 54
    height = header_h + sum(band_h + max(t[1].height, t[2].height if t[2] else 0) + gap
                            for t in tiles) + margin

    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.text((margin, 12), title_text, fill=(0, 0, 0), font=font)
    d.text((margin, 34), "Form 56 Section A (line 1 authority, line 2a/2b date) and "
                         "Part IV title — one binding, five estates",
           fill=(90, 90, 90), font=small)

    y = header_h
    for r, sec, ttl in tiles:
        ticked = ", ".join(r["line1Ticked"]) or "NONE"
        which = "2a " + r["date2a"] if r["date2a"] else ("2b " + r["date2b"] if r["date2b"] else "no date")
        bad = bool(r["groupViolations"]) or not r["line1Ticked"]
        d.rectangle([0, y, width, y + band_h], fill=(255, 235, 238) if bad else (238, 244, 255))
        d.text((margin, y + 7),
               f"{r['estateId']}   ·   {r['jurisdiction']}   ·   {r['authorityBasis']}   →   "
               f"box {ticked}   ·   {which}   ·   {r['fiduciaryTitle'] or '—'}"
               + ("   ·   GROUP VIOLATION" if bad else ""),
               fill=(150, 0, 25) if bad else (20, 40, 90), font=small)
        y += band_h
        canvas.paste(sec, (margin, y))
        if ttl:
            canvas.paste(ttl, (margin + body_w + gap, y))
        d.rectangle([margin - 1, y - 1, margin + sec.width, y + sec.height],
                    outline=(200, 200, 200))
        y += max(sec.height, ttl.height if ttl else 0) + gap

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return out_png


def _md_table(rows: list[dict[str, Any]]) -> list[str]:
    head = ("| estate | jurisdiction | route | authority.basis | line 1 | line 2a | line 2b "
            "| fiduciary title | filled | empty | model calls | elapsed |")
    sep = "|" + "---|" * 12
    out = [head, sep]
    for r in rows:
        out.append(
            f"| {r['estateId']} | {r['jurisdiction']} | {r['route']} | `{r['authorityBasis']}` "
            f"| **{', '.join(r['line1Ticked']) or 'NONE'}** | {r['date2a'] or '—'} "
            f"| {r['date2b'] or '—'} | {r['fiduciaryTitle'] or '—'} | {r['fieldsFilled']} "
            f"| {r['fieldsEmpty']} | **{r['llmCallsAtRuntime']}** | {r['elapsedMs']} ms |"
        )
    return out


def run_reuse_proof(binding_version: int | None = 1, use_draft: bool = False) -> int:
    form = get_form(FORM_ID)
    if use_draft:
        path = BINDINGS_DIR / f"{FORM_ID}.json"
        binding = json.loads(path.read_text(encoding="utf-8"))
        label = f"DRAFT (would be v{binding.get('version')}, not yet approved)"
    else:
        binding, path = load_approved(FORM_ID, binding_version)
        label = f"approved v{binding['version']}, approved by {binding['approvedBy']}"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    cal = json.loads((CALIBRATION_DIR / f"{FORM_ID}.json").read_text(encoding="utf-8"))
    crops = _crops(cal)
    # The authoritative run — the highest approved version — owns the unsuffixed
    # names, so out/demo/reuse.md is always "the current truth" and never a stale
    # sibling. Older approved versions keep a suffix and stay as history.
    if use_draft:
        suffix = "-draft"
    else:
        highest = max(
            int(m.group(1))
            for pth in APPROVED_DIR.glob(f"{FORM_ID}.v*.json")
            if (m := re.search(r"\.v(\d+)\.json$", pth.name))
        )
        suffix = "" if binding["version"] == highest else f"-v{binding['version']}"
    # per-run directory: a v1 run must not overwrite the draft run's sidecars, or the
    # two reports end up pointing at each other's evidence
    reuse_dir = DEMO / f"reuse{suffix}"
    reuse_dir.mkdir(parents=True, exist_ok=True)

    estates = sorted(p.stem for p in ESTATES_DIR.glob("*.json"))
    rows = [_fill_one(binding, e, form.path, reuse_dir) for e in estates]
    for r in rows:
        (reuse_dir / f"{r['estateId']}.json").write_text(
            json.dumps(r, indent=2) + "\n", encoding="utf-8"
        )
        flag = " GROUP VIOLATION" if r["groupViolations"] else ""
        print(
            f"{r['estateId']:44} box {','.join(r['line1Ticked']) or 'NONE':4} "
            f"{r['fieldsFilled']:>3} filled  llmCalls={r['llmCallsAtRuntime']}  "
            f"{r['elapsedMs']}ms{flag}"
        )

    strip = _strip(rows, crops, DEMO / f"reuse-section-a{suffix}.png",
                   f"One binding, five estates — {label}", reuse_dir)

    warm = [r["elapsedMs"] for r in rows]
    cold_s = COLD["calibrateSeconds"] + COLD["proposeSeconds"]
    violations = sum(len(r["groupViolations"]) for r in rows)

    md = [
        "# The reuse proof — one binding, five estates, no model",
        "",
        f"Binding: `{rel(path)}` — {label}  ",
        f"sha256: `{sha}`  ",
        f"Form: `{rel(form.path)}` (sha256 asserted equal to the binding's "
        f"`sourceFormSha256` before every fill)",
        "",
        "The same file, byte for byte, produced all five documents below. The data "
        "differs, the jurisdiction differs, the probate route differs, the authority "
        "differs — the binding does not. Nothing here consults a model: every fill runs "
        "inside `forbid_model_calls()`, and the `model calls` column is a counter wired "
        "into the model client, not a literal.",
        "",
        *_md_table(rows),
        "",
        f"**Model calls at fill time: {sum(r['llmCallsAtRuntime'] for r in rows)} across all "
        f"{len(rows)} estates.** Wall time {min(warm)}–{max(warm)} ms per estate.",
        "",
        "## What differs, and why that is the point",
        "",
        "- **Line 1** — the authority box moves with `authority.basis`: 1a court "
        "appointment of a testate estate (estates 01, 05), 1b intestate (estate 02), "
        "1e valid trust instrument (estates 03, 04). One `condition` binding per box, "
        "seven boxes, one `exactlyOne` exclusive group holding them together.",
        "- **Line 2a vs 2b** — the same `when` guard sends the date of death to 2a on the "
        "1a/1b/1d branch and the date of appointment to 2b on the 1c/1e/1f/1g branch. "
        "Estates 01/02/05 take one branch, 03/04 the other. No code decided that; a "
        "guard in the artifact did.",
        "- **Fiduciary title** — Executor, Administrator, Successor Trustee, Personal "
        "Representative, straight from `form56.signature.title`.",
        "",
        f"![Section A across five estates]({strip.name})",
        "",
        "## Cold versus warm, honestly",
        "",
        "| | wall time | model calls | measured how |",
        "|---|---|---|---|",
        f"| Cold: first estate on an uncompiled form | **{cold_s} s** "
        f"({COLD['calibrateSeconds']} s calibrate + {COLD['proposeSeconds']} s propose) "
        f"| {COLD['calibrateModelCalls'] + COLD['proposeModelCalls']} "
        "| calibrate from `out/reports/calls/` mtimes (19:54:16 → 19:55:39); propose from "
        "the wall time the command printed (402.0 s) |",
        f"| Warm: every estate after the first | **{sum(warm)//len(warm)} ms** "
        f"(range {min(warm)}–{max(warm)}) | 0 | measured per fill, this run |",
        "",
        f"That is roughly **{round(cold_s / (sum(warm) / len(warm) / 1000)):,}×**. The "
        "compile cost is paid once per form, by a machine, under human review. Every "
        "estate after it is deterministic.",
        "",
        "Two things this table deliberately does not hide:",
        "",
        f"- The first proposal attempt spent a further **{COLD['wastedOnTimeoutsSeconds']} s** "
        "on two whole-form calls that timed out at 420 s each before the per-page fallback "
        "existed. It is excluded from the headline because a run today does not pay it, but "
        "it was real time on the clock tonight.",
        "- Cold cost is *build* cost. It buys a reviewed artifact, not one filled form.",
    ]
    if violations:
        md += [
            "",
            "## Group violations in this run",
            "",
            f"**{violations} estate(s) failed the `exactlyOne` check on line 1.** The fill "
            "still produced a PDF and reported the violation rather than filing something "
            "with no authority box marked. Detail per estate is in "
            f"`{reuse_dir.name}/<estate>.json`.",
        ]
        for r in rows:
            for v in r["groupViolations"]:
                md.append(f"- `{r['estateId']}` — {v}")

    DEMO.mkdir(parents=True, exist_ok=True)
    out_md = DEMO / f"reuse{suffix}.md"
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {rel(out_md)}")
    print(f"wrote {rel(strip)}")
    print(
        f"5 estates, {sum(r['llmCallsAtRuntime'] for r in rows)} model calls, "
        f"{violations} group violation(s), warm {min(warm)}-{max(warm)}ms"
    )
    return 1 if violations else 0
