"""Stage 3 — the approval UI. FastAPI, one HTML page, no framework, no build step.

The reviewer approves the BINDING, not the document: one approval covers every
future estate that uses this form. Spec: docs/02-SPEC.md §3, plus the phase 3
obligations recorded in §2.1 (exclusiveGroups violations shown prominently,
guardedOff distinguished from absent).

Approved versions are immutable: the file is written once, refused if it exists,
and chmod'd read-only.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .estatepath import EstateData
from .fill import resolve_all
from .registry import (
    APPROVED_DIR,
    BINDINGS_DIR,
    ESTATES_DIR,
    RENDERS_DIR,
    estate_path,
    load_work_order,
    rel,
)

# ------------------------------------------------------------------ state


def list_drafts() -> list[str]:
    return sorted(p.stem for p in BINDINGS_DIR.glob("*.json"))


def load_draft(form_id: str) -> dict[str, Any]:
    p = BINDINGS_DIR / f"{form_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no draft binding for {form_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def next_version(form_id: str) -> int:
    versions = [
        int(m.group(1))
        for p in APPROVED_DIR.glob(f"{form_id}.v*.json")
        if (m := re.search(r"\.v(\d+)\.json$", p.name))
    ]
    return max(versions, default=0) + 1


def approve(form_id: str, approved_by: str) -> dict[str, Any]:
    """Freeze the draft: version, attribute, copy to approved/, never touch again."""
    if not approved_by or not approved_by.strip():
        raise ValueError("approvedBy is required — an unattributed artifact is not approved")
    artifact = load_draft(form_id)
    version = next_version(form_id)
    artifact["version"] = version
    artifact["status"] = "approved"
    artifact["approvedBy"] = approved_by.strip()
    artifact["approvedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    out = APPROVED_DIR / f"{form_id}.v{version}.json"
    if out.exists():  # never modified again — belt and braces with the chmod below
        raise FileExistsError(f"{rel(out)} already exists; approved versions are immutable")
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    out.chmod(0o444)
    return {"formId": form_id, "version": version, "path": rel(out)}


def rendered_pages(form_id: str) -> list[str]:
    """Paths (relative to RENDERS_DIR) of the pages to show on the left.

    A one-pass `forge propose` writes `draft-page-<p>.png`; the convergence loop
    writes `round-<n>-page-<p>.png`. Prefer the draft — a stale `round-1-*` from an
    earlier, crashed loop must never be shown as if it were this draft's output.
    """
    d = RENDERS_DIR / form_id
    if not d.is_dir():
        return []
    draft = sorted(p for p in d.glob("draft-page-*.png"))
    if draft:
        return [str(p.relative_to(RENDERS_DIR)) for p in draft]
    rounds = [
        (int(m.group(1)), p)
        for p in d.glob("round-*-page-*.png")
        if (m := re.search(r"round-(\d+)-page", p.name))
    ]
    if not rounds:
        return []
    final = max(n for n, _ in rounds)
    return sorted(str(p.relative_to(RENDERS_DIR)) for n, p in rounds if n == final)


def overlay_boxes(
    field: dict[str, Any], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Map a calibrated field's widget rectangles onto the rendered page image.

    Returned as PERCENTAGES of the image, never pixels: the page is shown scaled and
    the window gets resized on a projector, and percentages survive that.

    Two coordinate systems meet here:
      - PDF user space: origin BOTTOM-left, y increasing upwards, units of points.
      - the PNG: origin TOP-left, y increasing downwards.
    So Y is flipped. And the flip is against the **CropBox**, not the page height,
    because pdftoppm rasterises the crop and a crop need not start at the origin —
    DL 142's is [0, 3.55556, 612, 792]. Hence the `- cx0` and `cy1 -` terms rather
    than a bare page height.
    """
    boxes: list[dict[str, Any]] = []
    widgets = field.get("widgets")
    if not widgets:  # pre-backfill artifact: fall back to the single stored rect
        widgets = (
            [{"page": field.get("page"), "rect": field["rect"]}]
            if field.get("rect")
            else []
        )
    for w in widgets:
        page_index, rect = w.get("page"), w.get("rect")
        if rect is None or page_index is None or page_index >= len(pages):
            continue
        p = pages[page_index]
        if p.get("rotate"):
            # a rotated page needs the axes swapped too; no form here has one, and
            # drawing a wrong box is worse than drawing none
            boxes.append({"page": page_index, "unsupported": f"page rotated {p['rotate']}°"})
            continue
        cx0, cy0, cx1, cy1 = p["cropBox"]
        width_pt, height_pt = p["widthPt"], p["heightPt"]
        if not width_pt or not height_pt:
            continue
        x0, x1 = sorted((rect[0], rect[2]))
        y0, y1 = sorted((rect[1], rect[3]))
        box = {
            "page": page_index,
            "left": round((x0 - cx0) / width_pt * 100, 4),
            "top": round((cy1 - y1) / height_pt * 100, 4),
            "width": round((x1 - x0) / width_pt * 100, 4),
            "height": round((y1 - y0) / height_pt * 100, 4),
        }
        # a widget outside the rendered crop cannot be drawn honestly — say so
        box["offCrop"] = (
            box["left"] < -0.5
            or box["top"] < -0.5
            or box["left"] + box["width"] > 100.5
            or box["top"] + box["height"] > 100.5
        )
        boxes.append(box)
    return boxes


def render_is_stale(form_id: str) -> bool:
    """True when the draft binding was written after the images were rendered."""
    pages = rendered_pages(form_id)
    draft = BINDINGS_DIR / f"{form_id}.json"
    if not pages or not draft.exists():
        return False
    newest_render = max((RENDERS_DIR / p).stat().st_mtime for p in pages)
    return draft.stat().st_mtime > newest_render + 1


def work_order_context(form_id: str, estate_id: str) -> dict[str, Any]:
    """Warrant's context for the header. Forge never computes these — docs/01."""
    try:
        order = load_work_order(estate_id)
    except FileNotFoundError:
        return {"available": False}
    entry = next((f for f in order["forms"] if f["formId"] == form_id), None)
    return {
        "available": True,
        "jurisdiction": order.get("jurisdiction"),
        "route": order.get("route"),
        "applicable": (entry or {}).get("applicable"),
        "reason": (entry or {}).get("reason"),
        "priority": (entry or {}).get("priority"),
        "blastRadius": (entry or {}).get("blastRadius"),
        "reversibility": (entry or {}).get("reversibility"),
    }


def review_state(form_id: str, estate_id: str) -> dict[str, Any]:
    """Everything the page needs: rows sorted worst-first, group check, renders."""
    from .bind import load_calibration

    artifact = load_draft(form_id)
    estate = EstateData.load(estate_path(estate_id))
    result = resolve_all(artifact, estate)
    by_name = {f.qualified_name: f for f in result.fields}

    cal = load_calibration(form_id)
    cal_pages = cal.get("pages") or []
    cal_by_name = {f["qualifiedName"]: f for f in cal["fields"]}

    def boxes_for(qualified_name: str | None) -> list[dict[str, Any]]:
        f = cal_by_name.get(qualified_name or "")
        return overlay_boxes(f, cal_pages) if f else []

    rows = []
    for b in artifact["bindings"]:
        f = by_name[b["qualifiedName"]]
        # Three ways to be empty, and a reviewer must be able to tell them apart:
        #   guarded-off      a `when` guard was false, so this branch does not apply
        #   condition-false  the data IS present and answers no — the box is correctly clear
        #   absent           no data at all; this is the one that needs a human (rule 4)
        if f.guarded_off:
            status = "guarded-off"
        elif f.filled:
            status = "filled"
        elif f.present:
            status = "condition-false"
        else:
            status = "absent"
        rows.append(
            {
                "qualifiedName": b["qualifiedName"],
                "itemNumber": b.get("itemNumber"),
                "label": b.get("label"),
                "sourceKind": b["source"]["kind"],
                "source": b["source"],
                "when": b.get("when"),
                "required": b.get("required", False),
                "confidence": b.get("confidence", "medium"),
                "status": status,
                "value": f.value if f.format != "checkbox" else ("☑" if f.checked else "☐"),
                "reason": f.reason,
                "note": b.get("note"),
                "reviewed": bool(b.get("reviewed")),
                # the single path an editor may retarget; template/constant have none
                "editablePath": b["source"].get("path"),
                "boxes": boxes_for(b["qualifiedName"]),
            }
        )
    for u in artifact["unbound"]:
        rows.append(
            {
                "qualifiedName": u.get("qualifiedName"),
                "itemNumber": None,
                "label": u.get("label"),
                "sourceKind": "unbound",
                "source": None,
                "when": None,
                "required": False,
                "confidence": "low",
                "status": "unbound",
                "value": None,
                "reason": u.get("whatWouldFillIt") or u.get("reason"),
                "note": u.get("reason"),
                "reviewed": bool(u.get("reviewed")),
                "editablePath": None,
                "boxes": boxes_for(u.get("qualifiedName")),
            }
        )

    # worst first: unbound, then low confidence, then the rest
    rank = {"unbound": 0}
    rows.sort(
        key=lambda r: (
            rank.get(r["status"], 2),
            0 if r["confidence"] == "low" else 1,
            str(r["itemNumber"] or "zzz"),
        )
    )

    violations = result.group_violations
    required_unfilled = [
        r["qualifiedName"]
        for r in rows
        if r["required"] and r["status"] in ("absent", "unbound")
    ]
    # docs/02 §3: the footer gate is "any required field unbound". A required field
    # whose data is merely absent for THIS estate does not block approval of the
    # binding — the binding is right, the record is short — so `unbound` is the gate.
    required_unbound = [
        r["qualifiedName"] for r in rows if r["required"] and r["status"] == "unbound"
    ]

    return {
        "formId": form_id,
        "estateId": estate_id,
        "estates": sorted(p.stem for p in ESTATES_DIR.glob("*.json")),
        "forms": list_drafts(),
        "workOrder": work_order_context(form_id, estate_id),
        "status": artifact["status"],
        "version": artifact["version"],
        "nextVersion": next_version(form_id),
        "rows": rows,
        "groupViolations": violations,
        "requiredUnbound": required_unbound,
        "requiredAbsent": [
            r["qualifiedName"]
            for r in rows
            if r["required"] and r["status"] == "absent"
        ],
        "approveBlocked": bool(violations or required_unbound),
        "pages": rendered_pages(form_id),
        "pageGeometry": cal_pages,
        # the table re-resolves live on every request; the image does not. Say so
        # rather than letting a reviewer approve against a picture of an older binding.
        "renderStale": render_is_stale(form_id),
        "counts": {
            "bound": len(artifact["bindings"]),
            "unbound": len(artifact["unbound"]),
            "filled": sum(1 for f in result.fields if f.filled),
            "guardedOff": sum(1 for f in result.fields if f.guarded_off),
            "absent": sum(1 for r in rows if r["status"] == "absent"),
            "conditionFalse": sum(1 for r in rows if r["status"] == "condition-false"),
            "lowConfidence": sum(1 for r in rows if r["confidence"] == "low"),
            "reviewed": sum(1 for r in rows if r["reviewed"]),
            "total": len(rows),
        },
    }


def update_binding_row(form_id: str, qualified_name: str, patch: dict[str, Any]) -> None:
    """Row actions from the UI: approve the row, edit its source path, or mark it
    unbound with a note. Every action edits the JSON artifact and nothing else —
    there is no other store (CLAUDE.md rule 6)."""
    p = BINDINGS_DIR / f"{form_id}.json"
    artifact = load_draft(form_id)
    bound = {b["qualifiedName"]: b for b in artifact["bindings"]}
    unbound = {u.get("qualifiedName"): u for u in artifact["unbound"]}

    if "reviewed" in patch:
        target = bound.get(qualified_name) or unbound.get(qualified_name)
        if target is None:
            raise ValueError(f"{qualified_name} is not in this binding")
        target["reviewed"] = bool(patch["reviewed"])
    elif patch.get("markUnbound"):
        b = bound.get(qualified_name)
        if b is None:
            raise ValueError(f"{qualified_name} is already unbound")
        artifact["bindings"] = [
            x for x in artifact["bindings"] if x["qualifiedName"] != qualified_name
        ]
        artifact["unbound"].append(
            {
                "qualifiedName": qualified_name,
                "label": b.get("label"),
                "reason": patch.get("note") or "marked unbound in review",
                "whatWouldFillIt": patch.get("note"),
            }
        )
    elif "path" in patch:
        b = bound.get(qualified_name)
        if b is None:
            raise ValueError(f"{qualified_name} is unbound; nothing to retarget")
        if "path" not in b["source"]:
            raise ValueError(
                f"{qualified_name} has source kind {b['source']['kind']!r}, which has no "
                "single path to edit — mark it unbound with a note instead"
            )
        new_path = str(patch["path"]).strip()
        if not new_path:
            raise ValueError("a source path cannot be empty")
        b["source"]["path"] = new_path
        b["note"] = ((b.get("note") or "") + " [path edited in review]").strip()
        b["reviewed"] = False  # an edited row is no longer the row that was approved
    else:
        raise ValueError(f"no recognised action in patch {patch!r}")

    artifact["status"] = "draft"
    p.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ web


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Forge review</title>
<style>
 :root { --line:#e6e6e6; --dim:#6b6b6b; }
 * { box-sizing: border-box; }
 body { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 0; display: flex; height: 100vh; color: #1a1a1a; }
 #left { width: 50%; display: flex; flex-direction: column; background: #3b3b3f; }
 #pagebar { padding: 8px 12px; background: #2b2b2f; color: #eee; display: flex;
            gap: 8px; align-items: center; flex: 0 0 auto; }
 #pagebar button { font-size: 13px; padding: 4px 12px; border-radius: 4px;
                   border: 1px solid #666; background: #4a4a50; color: #eee; cursor: pointer; }
 #pagebar button.on { background: #eee; color: #222; font-weight: 600; }
 #pagebar a { color: #9bd; margin-left: auto; font-size: 12px; }
 #sheet { flex: 1 1 auto; overflow: auto; padding: 14px; }
 /* the overlay is a percentage-positioned layer exactly coincident with the image,
    so it stays aligned at any scale — a projector resize must not break it */
 #stage { position: relative; line-height: 0; }
 #stage img { width: 100%; background: white; box-shadow: 0 2px 10px rgba(0,0,0,.55); }
 #ov { position: absolute; left: 0; top: 0; width: 100%; height: 100%;
       pointer-events: none; }
 .box { position: absolute; border-radius: 1px; transition: opacity .08s;
        box-shadow: 0 0 0 1px rgba(255,255,255,.7); }
 .box.ok   { background: rgba(38,120,255,.28);  border: 1.5px solid #1657d0; }
 .box.warn { background: rgba(255,176,32,.34);  border: 1.5px solid #a86200; }
 .box.bad  { background: rgba(220,32,64,.30);   border: 1.5px solid #b00020; }
 .box.pinned { box-shadow: 0 0 0 2px rgba(255,255,255,.9), 0 0 12px rgba(0,0,0,.5); }
 #pagebar .pin { color: #ffd479; font-size: 12px; margin-left: 10px; }
 #right { width: 50%; overflow: auto; padding: 14px 16px 0; }
 h1 { font-size: 17px; margin: 0 0 2px; }
 h1 .st { font-size: 12px; font-weight: 500; color: var(--dim); }
 .chips { margin: 6px 0 8px; display: flex; flex-wrap: wrap; gap: 5px; }
 .chip { font-size: 11.5px; padding: 2px 8px; border-radius: 10px; background: #f0f0f2;
         border: 1px solid var(--line); color: #333; }
 .chip b { font-weight: 600; }
 .chip.hi { background: #fde8e8; border-color: #f3bcbc; }
 .chip.med { background: #fff3d9; border-color: #f0dcae; }
 .counts { color: var(--dim); font-size: 12.5px; margin-bottom: 8px; }
 .banner { padding: 9px 11px; border-radius: 5px; margin: 8px 0; font-size: 13px; }
 .violation { background: #b00020; color: #fff; font-weight: 600; }
 .warn { background: #fff4d6; border: 1px solid #e8d59b; }
 table { border-collapse: collapse; width: 100%; }
 th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
      color: var(--dim); padding: 6px 6px; border-bottom: 1px solid #ccc;
      position: sticky; top: 0; background: #fff; }
 td { padding: 6px; border-bottom: 1px solid var(--line); vertical-align: top;
      font-size: 13px; }
 tr.flag td { background: #fff6f6; }
 tr.low td { background: #fffaec; }
 tr.done td { opacity: .55; }
 tbody tr { cursor: pointer; }
 tbody tr.hl td { background: #dbe8ff; box-shadow: inset 3px 0 0 #1657d0; }
 tbody tr.hl.low td { background: #fdefd0; box-shadow: inset 3px 0 0 #a86200; }
 tbody tr.hl.flag td { background: #ffdfe4; box-shadow: inset 3px 0 0 #b00020; }
 tbody tr.pin td { box-shadow: inset 4px 0 0 #111; }
 .legend { font-size: 11.5px; color: var(--dim); margin: 6px 0 2px; }
 .legend i { display: inline-block; width: 9px; height: 9px; margin: 0 3px 0 9px;
             border-radius: 2px; vertical-align: 0; }
 .badge { display: inline-block; font-size: 10px; font-weight: 700; padding: 1px 5px;
          border-radius: 3px; margin-right: 4px; vertical-align: 1px; }
 .b-unbound { background: #b00020; color: #fff; }
 .b-low { background: #a3730a; color: #fff; }
 .b-req { background: #333; color: #fff; }
 .item { font-weight: 700; white-space: nowrap; }
 .val { font-weight: 600; }
 .s-filled { color: #0a7a2f; } .s-absent { color: #a35c00; font-weight: 600; }
 .s-condition-false { color: #666; } .s-guarded-off { color: #4a55c0; }
 .s-unbound { color: #b00020; font-weight: 700; }
 code { font-size: 11.5px; background: #f4f4f6; padding: 1px 4px; border-radius: 3px;
        word-break: break-all; }
 .note { color: var(--dim); font-size: 11.5px; word-break: break-word; }
 .acts { white-space: nowrap; }
 .acts button { font-size: 11px; padding: 2px 7px; margin-right: 3px; cursor: pointer;
                border: 1px solid #bbb; background: #fafafa; border-radius: 3px; }
 .acts button.ok { background: #0a7a2f; border-color: #0a7a2f; color: #fff; }
 #footer { position: sticky; bottom: 0; background: #fff; border-top: 2px solid #ccc;
           padding: 11px 0 13px; margin-top: 6px; }
 #footer button { font-size: 15px; padding: 9px 18px; border-radius: 5px;
                  border: 1px solid #0a7a2f; background: #0a7a2f; color: #fff; cursor: pointer; }
 #footer button:disabled { background: #ddd; border-color: #ccc; color: #777;
                           cursor: not-allowed; }
 input { font: inherit; padding: 4px 6px; border: 1px solid #bbb; border-radius: 3px; }
</style></head><body>
<div id="left">
  <div id="pagebar"><span>Filled form</span><span id="pagebtns"></span>
    <span class="pin" id="pinnote"></span>
    <a id="pdflink" href="#" target="_blank">open PDF</a></div>
  <div id="sheet"><div id="stage"><img id="pageimg"><div id="ov"></div></div></div>
</div>
<div id="right">
 <h1 id="title"></h1>
 <div class="chips" id="chips"></div>
 <div class="counts" id="counts"></div>
 <div class="legend">Hover a row to find it on the form · click to pin · Esc to unpin
   <i style="background:rgba(38,120,255,.5);border:1px solid #1657d0"></i>bound
   <i style="background:rgba(255,176,32,.55);border:1px solid #a86200"></i>low confidence
   <i style="background:rgba(220,32,64,.5);border:1px solid #b00020"></i>unbound</div>
 <div id="banners"></div>
 <table id="rows"><thead><tr>
   <th>Item</th><th>Printed label</th><th>Source</th><th>Value</th><th>Status</th><th></th>
 </tr></thead><tbody></tbody></table>
 <div id="footer">
   <label>Approved by <input id="who" placeholder="your name" size="18"></label>
   <button id="approve"></button>
   <div class="note" style="margin-top:7px">You are approving the <b>binding</b>, not this
   document — one approval covers every future estate that uses this form.</div>
 </div>
</div>
<script>
const qs = new URLSearchParams(location.search);
const form = qs.get('form') || 'irs-f56';
const estate = qs.get('estate') || 'estate-05-in-formal-probate';
let page = 0;
let S = null;          // last state from /api/state
let pinned = null;     // pinned row index, survives the mouse leaving
let hovered = null;
const esc = t => String(t ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// ---- the traceability overlay: row on the right -> box on the paper on the left
function boxClass(row) {
  if (row.status === 'unbound') return 'bad';
  if (row.confidence === 'low') return 'warn';
  return 'ok';
}

function paintPage() {
  const img = document.getElementById('pageimg');
  const want = S.pages.length ? `/render/${S.pages[page]}?t=${S.renderToken}` : '';
  if (img.getAttribute('src') !== want) img.setAttribute('src', want);
  document.querySelectorAll('#pagebtns button').forEach(b =>
    b.classList.toggle('on', +b.dataset.p === page));
}

function paintOverlay() {
  const ov = document.getElementById('ov');
  const idx = pinned !== null ? pinned : hovered;
  document.querySelectorAll('#rows tbody tr').forEach(tr => {
    tr.classList.toggle('hl', +tr.dataset.idx === idx);
    tr.classList.toggle('pin', pinned !== null && +tr.dataset.idx === pinned);
  });
  document.getElementById('pinnote').textContent =
    pinned !== null ? 'pinned — click again or press Esc to release' : '';
  if (idx === null || idx === undefined || !S) { ov.innerHTML = ''; return; }
  const row = S.rows[idx];
  const cls = boxClass(row);
  ov.innerHTML = (row.boxes || [])
    .filter(b => b.page === page && !b.offCrop && !b.unsupported)
    .map(b => `<div class="box ${cls}${pinned !== null ? ' pinned' : ''}"
       style="left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%"></div>`)
    .join('');
}

/** Highlight a row's field, switching page first if the field is elsewhere. */
function focusRow(idx) {
  const row = S.rows[idx];
  const boxes = (row.boxes || []).filter(b => !b.unsupported);
  if (boxes.length && !boxes.some(b => b.page === page)) {
    page = boxes[0].page;      // the field is on another page: go there, then draw
    paintPage();
  }
  paintOverlay();
}

document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && pinned !== null) { pinned = null; paintOverlay(); }
});

function sourceCell(r) {
  if (!r.source) return '<code>unbound</code>';
  const k = r.source.kind;
  let main;
  if (k === 'path') main = `<code>${esc(r.source.path)}</code>`;
  else if (k === 'constant') main = `constant <code>${esc(JSON.stringify(r.source.value))}</code>`;
  else if (k === 'template')
    main = `<code>${esc(r.source.pattern)}</code><div class="note">${
      (r.source.paths||[]).map(esc).join(' · ')}</div>`;
  else if (k === 'condition')
    main = `<code>${esc(r.source.path)}</code><div class="note">== ${
      esc(JSON.stringify(r.source.equals))}</div>`;
  else if (k === 'absent') main = `absent(<code>${esc(r.source.path)}</code>)`;
  else main = `<code>${esc(JSON.stringify(r.source))}</code>`;
  const g = r.when ? `<div class="note">when <code>${esc(r.when.path)}</code> == ${
    esc(JSON.stringify(r.when.equals))}</div>` : '';
  return `<div class="note">${esc(k)}</div>${main}${g}`;
}

const STATUS_TEXT = {
  'filled': 'filled',
  'absent': 'empty — DATA ABSENT',
  'condition-false': 'clear — fact says no',
  'guarded-off': 'empty — guard false',
  'unbound': 'UNBOUND',
};

async function act(qn, patch) {
  const r = await fetch('/api/row', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({form, qualifiedName: qn, patch})});
  if (!r.ok) alert('Refused: ' + ((await r.json()).detail || r.status));
  load();
}

async function load() {
  const res = await fetch(`/api/state?form=${form}&estate=${estate}`);
  if (!res.ok) {
    document.getElementById('right').innerHTML =
      `<h1>No draft binding for ${esc(form)}</h1><p class="note">${
        esc((await res.json()).detail)}</p>`;
    return;
  }
  S = await res.json();
  S.renderToken = Date.now();  // cache-bust the image once per load, not per paint
  const s = S;
  const w = s.workOrder || {};

  document.getElementById('title').innerHTML =
    `${esc(s.formId)} <span class="st">— draft, would freeze as v${s.nextVersion}</span>`;

  const chips = [];
  chips.push(`<span class="chip">estate <b>${esc(s.estateId)}</b></span>`);
  if (w.available) {
    if (w.jurisdiction)
      chips.push(`<span class="chip">jurisdiction <b>${esc(w.jurisdiction.state)}${
        w.jurisdiction.county ? ' / ' + esc(w.jurisdiction.county) : ''}</b></span>`);
    if (w.route) chips.push(`<span class="chip">route <b>${esc(w.route)}</b></span>`);
    if (w.priority != null) chips.push(`<span class="chip">filing order <b>${w.priority}</b></span>`);
    if (w.blastRadius) chips.push(`<span class="chip ${w.blastRadius==='high'?'hi':
      (w.blastRadius==='medium'?'med':'')}">blast radius <b>${esc(w.blastRadius)}</b></span>`);
    if (w.reversibility) chips.push(`<span class="chip ${
      w.reversibility==='irreversible'?'hi':''}">
      <b>${esc(w.reversibility)}</b></span>`);
  } else {
    chips.push('<span class="chip med">no work order — Warrant context unavailable</span>');
  }
  chips.push(...s.estates.map(e => `<span class="chip"><a href="?form=${form}&estate=${e}">${
    esc(e)}</a></span>`));
  document.getElementById('chips').innerHTML = chips.join('');

  const c = s.counts;
  document.getElementById('counts').textContent =
    `${c.bound} of ${c.total} fields bound · ${c.filled} filled for this estate · ` +
    `${c.guardedOff} empty by guard · ${c.conditionFalse} correctly clear · ` +
    `${c.absent} empty for want of data · ${c.unbound} unbound · ` +
    `${c.lowConfidence} low confidence · ${c.reviewed} row(s) approved`;

  // left: one page at a time
  page = Math.min(page, Math.max(0, s.pages.length - 1));
  document.getElementById('pagebtns').innerHTML = s.pages.length
    ? s.pages.map((p, i) => `<button data-p="${i}">Page ${i+1}</button>`).join('')
    : '<span class="note" style="color:#ddd">no render on disk</span>';
  document.querySelectorAll('#pagebtns button').forEach(b =>
    b.onclick = () => { page = +b.dataset.p; paintPage(); paintOverlay(); });
  document.getElementById('pdflink').href = `/render/${form}/draft.pdf`;

  const b = [];
  s.groupViolations.forEach(g => b.push(`<div class="banner violation">⚠ ${esc(g)}</div>`));
  if (s.requiredUnbound.length)
    b.push(`<div class="banner violation">Approval blocked — required field(s) unbound: ${
      s.requiredUnbound.map(esc).join(', ')}</div>`);
  if (s.requiredAbsent.length)
    b.push(`<div class="banner warn">${s.requiredAbsent.length} required field(s) are bound but
      empty for <b>${esc(s.estateId)}</b>: the record is short, the binding is not wrong.
      This does not block approval.</div>`);
  if (s.renderStale)
    b.push(`<div class="banner warn">The binding has changed since these images were
      rendered — the table is live, the picture is not. Re-run
      <code>forge propose ${esc(form)} --estate ${esc(s.estateId)}</code> before approving.</div>`);
  document.getElementById('banners').innerHTML = b.join('');

  document.querySelector('#rows tbody').innerHTML = s.rows.map((r, i) => {
    const cls = [r.status === 'unbound' ? 'flag' : (r.confidence === 'low' ? 'low' : ''),
                 r.reviewed ? 'done' : ''].filter(Boolean).join(' ');
    const badges = (r.status === 'unbound' ? '<span class="badge b-unbound">UNBOUND</span>' : '')
      + (r.confidence === 'low' ? '<span class="badge b-low">LOW</span>' : '')
      + (r.required ? '<span class="badge b-req">REQ</span>' : '');
    return `<tr class="${cls}" data-idx="${i}">
      <td class="item">${esc(r.itemNumber ?? '—')}</td>
      <td>${badges}${esc(r.label)}<div class="note">${esc(r.qualifiedName)}</div></td>
      <td>${sourceCell(r)}</td>
      <td><span class="val">${esc(r.value ?? '')}</span>${
        r.reason ? `<div class="note">${esc(r.reason)}</div>` : ''}</td>
      <td class="s-${r.status}">${esc(STATUS_TEXT[r.status] || r.status)}</td>
      <td class="acts">
        <button class="${r.reviewed ? 'ok' : ''}" data-a="rev" data-q="${esc(r.qualifiedName)}"
          data-v="${r.reviewed ? 0 : 1}">${r.reviewed ? '✓' : 'approve'}</button>
        ${r.editablePath ? `<button data-a="edit" data-q="${esc(r.qualifiedName)}"
          data-p="${esc(r.editablePath)}">edit</button>` : ''}
        ${r.status !== 'unbound' ? `<button data-a="unbind"
          data-q="${esc(r.qualifiedName)}">unbind</button>` : ''}
      </td></tr>`;
  }).join('');

  // hover to trace, click to pin — the row is the handle, so the whole <tr> listens
  document.querySelectorAll('#rows tbody tr').forEach(tr => {
    const idx = +tr.dataset.idx;
    tr.onmouseenter = () => { hovered = idx; if (pinned === null) focusRow(idx); };
    tr.onmouseleave = () => { hovered = null; if (pinned === null) paintOverlay(); };
    tr.onclick = () => {
      pinned = (pinned === idx) ? null : idx;
      if (pinned !== null) focusRow(idx); else paintOverlay();
    };
  });

  document.querySelectorAll('.acts button').forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();  // an action is not a request to pin the row
    const q = btn.dataset.q, a = btn.dataset.a;
    if (a === 'rev') return act(q, {reviewed: btn.dataset.v === '1'});
    if (a === 'edit') {
      const p = prompt(`Source path for ${q}`, btn.dataset.p);
      if (p && p !== btn.dataset.p) return act(q, {path: p});
      return;
    }
    const note = prompt(`Why is ${q} unbound? (what would fill it)`, '');
    if (note !== null) return act(q, {markUnbound: true, note});
  });

  const ap = document.getElementById('approve');
  ap.textContent = s.approveBlocked ? 'Approval blocked'
    : `Approve binding as v${s.nextVersion}`;
  ap.disabled = s.approveBlocked;
  ap.onclick = async () => {
    const r = await fetch('/api/approve', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({form, approvedBy: document.getElementById('who').value})});
    const j = await r.json();
    alert(r.ok ? `Approved: ${j.path}` : `Refused: ${j.detail}`);
    load();
  };

  paintPage();
  paintOverlay();
}
load();
</script></body></html>"""


def build_app():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI(title="Forge review")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def state(form: str, estate: str) -> dict[str, Any]:
        try:
            return review_state(form, estate)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/approve")
    def do_approve(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return approve(body["form"], body.get("approvedBy") or "")
        except (ValueError, FileExistsError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/row")
    def do_row(body: dict[str, Any]) -> dict[str, str]:
        try:
            update_binding_row(body["form"], body["qualifiedName"], body.get("patch") or {})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc))
        return {"ok": "true"}

    @app.get("/render/{path:path}")
    def render(path: str) -> Any:
        target = (RENDERS_DIR / path).resolve()
        if not str(target).startswith(str(RENDERS_DIR.resolve())) or not target.exists():
            raise HTTPException(404, "no such render")
        return FileResponse(target)

    return app


def serve(port: int = 8000, form: str = "irs-f56", estate: str = "estate-05-in-formal-probate") -> int:
    import uvicorn

    print(f"http://127.0.0.1:{port}/?form={form}&estate={estate}", flush=True)
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0
