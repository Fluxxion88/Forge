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


def load_for_review(form_id: str, prefer_draft: bool = False) -> tuple[dict[str, Any], Path, str]:
    """The artifact the UI shows. APPROVED wins unless a draft is explicitly asked for.

    Defaulting to the draft is how a stray version gets minted: the draft is a byte-copy
    of the approved artifact right after approval, so an operator demoing the UI sees
    something that looks unapproved, presses Approve, and mints a v4 that differs from
    v3 in nothing but its timestamp. That already happened once (v1/v2). So: approved by
    default, draft only on `?draft=1`, and `approve()` refuses a no-op.
    """
    versions = sorted(
        int(m.group(1))
        for p in APPROVED_DIR.glob(f"{form_id}.v*.json")
        if (m := re.search(r"\.v(\d+)\.json$", p.name))
    )
    draft_path = BINDINGS_DIR / f"{form_id}.json"
    if not prefer_draft and versions:
        path = APPROVED_DIR / f"{form_id}.v{versions[-1]}.json"
        return json.loads(path.read_text(encoding="utf-8")), path, "approved"
    if draft_path.exists():
        return load_draft(form_id), draft_path, "draft"
    if versions:
        path = APPROVED_DIR / f"{form_id}.v{versions[-1]}.json"
        return json.loads(path.read_text(encoding="utf-8")), path, "approved"
    raise FileNotFoundError(f"no binding for {form_id}: no approved version and no draft")


def draft_matches_approved(form_id: str) -> bool:
    """True when the draft would approve to an artifact identical to the newest approved
    one — the condition under which approving again is pure noise."""
    versions = sorted(
        int(m.group(1))
        for p in APPROVED_DIR.glob(f"{form_id}.v*.json")
        if (m := re.search(r"\.v(\d+)\.json$", p.name))
    )
    draft_path = BINDINGS_DIR / f"{form_id}.json"
    if not versions or not draft_path.exists():
        return False
    approved = json.loads(
        (APPROVED_DIR / f"{form_id}.v{versions[-1]}.json").read_text(encoding="utf-8")
    )
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    keys = ("bindings", "unbound", "exclusiveGroups")
    return all(draft.get(k) == approved.get(k) for k in keys)


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
    if draft_matches_approved(form_id):
        versions = sorted(
            int(m.group(1))
            for p in APPROVED_DIR.glob(f"{form_id}.v*.json")
            if (m := re.search(r"\.v(\d+)\.json$", p.name))
        )
        raise ValueError(
            f"refusing to approve: the draft is identical to the approved "
            f"v{versions[-1]} (same bindings, unbound list and exclusive groups). "
            "Approving would mint a version that differs only by timestamp. Edit "
            "something first, or there is nothing to approve."
        )
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


def review_state(
    form_id: str, estate_id: str, prefer_draft: bool = False
) -> dict[str, Any]:
    """Everything the page needs: rows sorted worst-first, group check, renders."""
    from .bind import load_calibration
    from .walkthrough import STATUS_COPY

    artifact, artifact_path, source = load_for_review(form_id, prefer_draft)
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
                "statusLabel": STATUS_COPY[status]["label"],
                "statusHint": STATUS_COPY[status]["hint"],
                "statusTerm": STATUS_COPY[status]["term"],
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
                "statusLabel": STATUS_COPY["unbound"]["label"],
                "statusHint": STATUS_COPY["unbound"]["hint"],
                "statusTerm": STATUS_COPY["unbound"]["term"],
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
        "artifactSource": source,           # "approved" or "draft"
        "artifactPath": rel(artifact_path),
        "draftMatchesApproved": draft_matches_approved(form_id),
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


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Forge — form compiler</title>
<style>
 :root { --line:#e2e2e6; --dim:#666; --ink:#1a1a1a; --accent:#1657d0; }
 * { box-sizing: border-box; }
 body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 0; color: var(--ink); height: 100vh; display: flex;
        flex-direction: column; }
 nav { flex: 0 0 auto; border-bottom: 1px solid var(--line); background: #fafafa;
       display: flex; align-items: center; gap: 16px; padding: 0 14px; }
 .brand { font-weight: 700; padding: 10px 0; white-space: nowrap; }
 .brand span { font-weight: 400; color: var(--dim); font-size: 12px; margin-left: 6px; }
 #tabs { display: flex; gap: 2px; margin-left: 8px; }
 #tabs button { border: 0; background: none; font: inherit; font-size: 12.5px;
   font-weight: 600; letter-spacing: .03em; padding: 12px 11px; cursor: pointer;
   color: var(--dim); border-bottom: 3px solid transparent; }
 #tabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
 #tabs button .n { display: inline-block; width: 17px; height: 17px; line-height: 17px;
   border-radius: 50%; background: #ddd; color: #444; font-size: 10.5px;
   text-align: center; margin-right: 5px; }
 #tabs button.on .n { background: var(--accent); color: #fff; }
 .pickers { margin-left: auto; font-size: 12px; color: var(--dim); white-space: nowrap; }
 select { font: inherit; font-size: 12px; padding: 3px 4px; }
 #panels { flex: 1 1 auto; overflow: hidden; position: relative; }
 .panel { display: none; height: 100%; overflow: auto; padding: 18px 22px 40px; }
 .panel.on { display: block; }
 .panel.review.on { display: flex; padding: 0; }
 h2 { font-size: 19px; margin: 0 0 4px; }
 .thesis { font-size: 15px; line-height: 1.45; margin: 0 0 18px; padding: 11px 14px;
   background: #eef3ff; border-left: 4px solid var(--accent); border-radius: 0 4px 4px 0;
   max-width: 1000px; }
 .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
   gap: 12px; max-width: 1100px; margin-bottom: 20px; }
 .card { border: 1px solid var(--line); border-radius: 6px; padding: 11px 13px; }
 .card .k { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
   color: var(--dim); margin-bottom: 3px; }
 .card .v { font-size: 16px; font-weight: 600; }
 .card .sub { font-size: 12px; color: var(--dim); margin-top: 3px; }
 table { border-collapse: collapse; width: 100%; max-width: 1300px; }
 th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
   color: var(--dim); padding: 7px 8px; border-bottom: 1px solid #ccc;
   position: sticky; top: 0; background: #fff; z-index: 2; }
 td { padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top;
   font-size: 13px; }
 code { font-size: 11.5px; background: #f3f3f5; padding: 1px 4px; border-radius: 3px;
   word-break: break-all; }
 .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; }
 .note { color: var(--dim); font-size: 11.5px; }
 .pill { display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px;
   border-radius: 11px; }
 .pill.yes { background: #e2f4e6; color: #0a6b2b; }
 .pill.no  { background: #f0f0f2; color: #555; }
 .pill.ok  { background: #e2f4e6; color: #0a6b2b; }
 .pill.warn{ background: #fdf0d8; color: #8a5800; }
 .pill.bad { background: #fbe3e7; color: #a3001d; }
 .reason { background: #fafafa; border-left: 3px solid #ccc; padding: 8px 11px;
   margin-top: 6px; font-size: 12.5px; color: #333; max-width: 760px; }
 img.shot { max-width: 100%; border: 1px solid var(--line); border-radius: 4px;
   background: #fff; }
 .sidebyside { display: flex; gap: 18px; flex-wrap: wrap; align-items: flex-start; }
 .sidebyside > div { flex: 1 1 380px; min-width: 320px; }
 .refusal { background: #1e1e22; color: #eaeaea; padding: 12px 14px; border-radius: 5px;
   font-family: ui-monospace, Menlo, monospace; font-size: 12px; white-space: pre-wrap; }
 h3 { font-size: 14.5px; margin: 22px 0 8px; }
 .roundhdr { display: flex; align-items: baseline; gap: 10px; margin: 18px 0 6px; }
 .roundhdr h3 { margin: 0; }
 ul.findings { margin: 6px 0 10px; padding-left: 20px; }
 ul.findings li { margin-bottom: 5px; font-size: 13px; }
 /* ---- REVIEW tab: the split pane, unchanged behaviour ---- */
 #left { width: 50%; display: flex; flex-direction: column; background: #3b3b3f; }
 #pagebar { padding: 8px 12px; background: #2b2b2f; color: #eee; display: flex;
            gap: 8px; align-items: center; flex: 0 0 auto; font-size: 12px; }
 #pagebar button { font-size: 12px; padding: 4px 12px; border-radius: 4px;
                   border: 1px solid #666; background: #4a4a50; color: #eee; cursor: pointer; }
 #pagebar button.on { background: #eee; color: #222; font-weight: 600; }
 #pagebar a { color: #9bd; margin-left: auto; font-size: 12px; }
 #pagebar .pin { color: #ffd479; margin-left: 10px; }
 #sheet { flex: 1 1 auto; overflow: auto; padding: 14px; }
 #stage { position: relative; line-height: 0; }
 #stage img { width: 100%; background: white; box-shadow: 0 2px 10px rgba(0,0,0,.55); }
 #ov { position: absolute; left: 0; top: 0; width: 100%; height: 100%;
       pointer-events: none; }
 .box { position: absolute; border-radius: 1px; box-shadow: 0 0 0 1px rgba(255,255,255,.7); }
 .box.ok   { background: rgba(38,120,255,.28);  border: 1.5px solid #1657d0; }
 .box.warn { background: rgba(255,176,32,.34);  border: 1.5px solid #a86200; }
 .box.bad  { background: rgba(220,32,64,.30);   border: 1.5px solid #b00020; }
 .box.pinned { box-shadow: 0 0 0 2px rgba(255,255,255,.9), 0 0 12px rgba(0,0,0,.5); }
 #right { width: 50%; overflow: auto; padding: 14px 16px 0; }
 tbody tr { cursor: pointer; }
 tbody tr.hl td { background: #dbe8ff; box-shadow: inset 3px 0 0 #1657d0; }
 tbody tr.hl.low td { background: #fdefd0; box-shadow: inset 3px 0 0 #a86200; }
 tbody tr.hl.flag td { background: #ffdfe4; box-shadow: inset 3px 0 0 #b00020; }
 tbody tr.flag td { background: #fff6f6; }
 tbody tr.low td { background: #fffaec; }
 tbody tr.done td { opacity: .55; }
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
 .banner { padding: 9px 11px; border-radius: 5px; margin: 8px 0; font-size: 13px; }
 .violation { background: #b00020; color: #fff; font-weight: 600; }
 .warnbox { background: #fff4d6; border: 1px solid #e8d59b; }
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
 .legend { font-size: 11.5px; color: var(--dim); margin: 6px 0 2px; }
 .legend i { display: inline-block; width: 9px; height: 9px; margin: 0 3px 0 9px;
             border-radius: 2px; }
</style></head><body>
<nav>
  <div class="brand">Forge <span>we do not fill forms with AI — we compile form-fillers, once</span></div>
  <div id="tabs"></div>
  <div class="pickers">
    estate <select id="estatePick"></select>
    form <select id="formPick"></select>
  </div>
</nav>
<div id="panels">
  <section class="panel" data-tab="estate"></section>
  <section class="panel" data-tab="forms"></section>
  <section class="panel review" data-tab="review">
    <div id="left">
      <div id="pagebar"><span>Filled form</span><span id="pagebtns"></span>
        <span class="pin" id="pinnote"></span>
        <a id="pdflink" href="#" target="_blank">open PDF</a></div>
      <div id="sheet"><div id="stage"><img id="pageimg"><div id="ov"></div></div></div>
    </div>
    <div id="right">
      <h2 id="rtitle"></h2>
      <div class="note" id="rmeta"></div>
      <div class="legend">Hover a row to find it on the form · click to pin · Esc to unpin
        <i style="background:rgba(38,120,255,.5);border:1px solid #1657d0"></i>has a data source
        <i style="background:rgba(255,176,32,.55);border:1px solid #a86200"></i>needs a check
        <i style="background:rgba(220,32,64,.5);border:1px solid #b00020"></i>no data source</div>
      <div id="banners"></div>
      <table id="rows"><thead><tr>
        <th>Line</th><th>What the form asks</th><th>Where the value comes from</th>
        <th>Value</th><th>Outcome</th><th></th>
      </tr></thead><tbody></tbody></table>
      <div id="footer">
        <label>Approved by <input id="who" placeholder="your name" size="16"></label>
        <button id="approve"></button>
        <div class="note" style="margin-top:7px">You are approving the <b>binding</b>, not
          this document — one approval covers every future estate that uses this form.</div>
      </div>
    </div>
  </section>
  <section class="panel" data-tab="reuse"></section>
  <section class="panel" data-tab="loop"></section>
  <section class="panel" data-tab="anvil"></section>
</div>
<script>
const TABS = [
  ["estate", "ESTATE"], ["forms", "FORMS NEEDED"], ["review", "REVIEW"],
  ["reuse", "REUSE"], ["loop", "SELF-CORRECTION"], ["anvil", "SPONSOR RUNTIME"],
];
const qs = new URLSearchParams(location.search);
let form = qs.get('form') || 'irs-f56';
let estate = qs.get('estate') || 'estate-05-in-formal-probate';
const preferDraft = qs.get('draft') === '1';
let tab = qs.get('tab') || 'estate';
let S = null, W = null, page = 0, pinned = null, hovered = null;

const esc = t => String(t ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const asset = p => '/asset/' + String(p).split('/').map(encodeURIComponent).join('/');
const el = t => document.querySelector(`.panel[data-tab="${t}"]`);

function paintTabs() {
  document.getElementById('tabs').innerHTML = TABS.map(([id, label], i) =>
    `<button data-t="${id}" class="${id === tab ? 'on' : ''}">
       <span class="n">${i + 1}</span>${label}</button>`).join('');
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => {
    tab = b.dataset.t;
    const u = new URL(location); u.searchParams.set('tab', tab);
    history.replaceState({}, '', u);
    paintTabs(); showTab();
  });
}
function showTab() {
  document.querySelectorAll('.panel').forEach(p =>
    p.classList.toggle('on', p.dataset.tab === tab));
  if (tab === 'review') { paintPage(); paintOverlay(); }
}

// ------------------------------------------------------------------ 1. ESTATE
function paintEstate() {
  const e = W.estate;
  el('estate').innerHTML = `
    <h2>${esc(e.decedentName || e.estateId)}</h2>
    <p class="thesis">This is the record Forge was handed. Every value on every form
      below comes from this one file — nothing is typed twice, and nothing is invented.</p>
    <div class="grid">
      <div class="card"><div class="k">Who died</div><div class="v">${esc(e.decedentName)}</div>
        <div class="sub">${e.dateOfDeath ? 'died ' + esc(e.dateOfDeath) : ''}${
          e.residence ? ' · lived in ' + esc(e.residence) : ''}</div></div>
      <div class="card"><div class="k">Who is acting</div><div class="v">${esc(e.fiduciaryName)}</div>
        <div class="sub">${esc(e.fiduciaryTitle || '')}</div></div>
      <div class="card"><div class="k">Where</div><div class="v">${esc(e.jurisdictionPlain)}</div>
        <div class="sub">${esc(e.routePlain || '')}</div></div>
      <div class="card"><div class="k">The estate as a taxpayer</div>
        <div class="v">${esc(e.entityName || '—')}</div>
        <div class="sub">${esc(e.einPlain)}</div></div>
    </div>
    <h3>Where these facts came from</h3>
    <table><tbody>
      <tr><td style="width:210px">The estate record</td><td><code>${esc(e.sources.estateRecord)}</code>
        <div class="note">Supplied by the organisers. Forge reads it; it never edits it.</div></td></tr>
      <tr><td>The instruction to act</td><td><code>${esc(e.sources.workOrder)}</code>
        <div class="note">Written by <b>${esc(e.sources.generatedBy)}</b> — the other half of
          the system, which decides <i>which</i> forms are needed and why. Forge decides
          <i>how</i> each one gets filled.</div></td></tr>
      <tr><td>Authority to act</td><td><code>${esc(e.authorityBasis)}</code>
        <div class="note">This single value moves a tick-box on Form 56 and swaps which
          date line is used. You will see it do that on the REUSE tab.</div></td></tr>
    </tbody></table>
    <p class="note" style="margin-top:16px;max-width:760px">${esc(e.provenanceNote)}</p>`;
}

// ------------------------------------------------------------- 2. FORMS NEEDED
function paintForms() {
  const f = W.forms, c = f.counts;
  const rows = f.rows.map(r => {
    const st = r.compile;
    let right;
    if (r.applicable === false) {
      right = `<span class="pill no">not needed</span>`;
    } else if (st.status === 'approved') {
      right = `<span class="pill ok">ready — approved v${st.highestApproved}</span>
        <div class="note">${st.boundCount} of ${st.boundCount + st.unboundCount} boxes have a
        data source · approved by ${esc(st.approvedBy || '')}</div>`;
    } else if (st.status === 'draft') {
      right = `<span class="pill warn">drafted — needs a human</span>
        <div class="note">${st.boundCount} of ${st.boundCount + st.unboundCount} boxes have a
        data source · nobody has approved it, so Forge will not fill it</div>`;
    } else {
      right = `<span class="pill bad">not compiled</span>
        <div class="note">Forge will refuse to produce this form rather than guess</div>`;
    }
    return `<tr>
      <td style="width:170px"><b>${esc(r.title)}</b>
        <div class="note">${esc(r.filename)}</div></td>
      <td style="width:120px">${r.applicable
        ? `<span class="pill yes">needed</span>${r.priority ? `<div class="note">file #${r.priority}</div>` : ''}`
        : `<span class="pill no">skipped</span>`}</td>
      <td>${r.applicable === false
        ? `<div class="reason">${esc(r.reason)}</div>`
        : `${r.blastRadiusPlain ? `<div>${esc(r.blastRadiusPlain)}${
             r.reversibilityPlain ? ' · ' + esc(r.reversibilityPlain) : ''}</div>` : ''}
           <div class="note">Judged by ${esc(f.decidedBy)}, not by Forge</div>`}</td>
      <td style="width:280px">${right}</td></tr>`;
  }).join('');
  el('forms').innerHTML = `
    <h2>Forms needed for this estate</h2>
    <p class="thesis">Of ${c.total} forms, ${c.needed} are needed and ${c.refused} are
      deliberately skipped — each with a reason in writing. A system that refuses to
      produce a document, and says why, is safer than one that produces four.</p>
    <table><thead><tr><th>Form</th><th>Needed?</th><th>Why / how much it matters</th>
      <th>Can Forge produce it?</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="note" style="margin-top:14px;max-width:820px">
      The needed/skipped decision is not Forge's to make — it arrives in the work order
      from the half of the system that reads the documents and knows the law. Forge only
      decides how an unfamiliar form gets filled. That seam is what lets either half be
      replaced without touching the other.</p>`;
}

// ------------------------------------------------------------------- 4. REUSE
function paintReuse() {
  const r = W.reuse;
  if (!r.available) {
    el('reuse').innerHTML = `<h2>Reuse</h2><p class="thesis">Not generated yet — run
      <code>forge reuse-proof --binding-version 3</code>.</p>`;
    return;
  }
  const rows = r.rows.map(x => `<tr>
     <td>${esc(x.estateId.replace(/^estate-\d+-/, ''))}<div class="note">${esc(x.jurisdiction)}</div></td>
     <td><code>${esc(x.authorityBasis)}</code></td>
     <td><b>${esc(x.line1)}</b></td>
     <td>${x.date2a ? esc(x.date2a) : '<span class="note">—</span>'}</td>
     <td>${x.date2b ? esc(x.date2b) : '<span class="note">—</span>'}</td>
     <td>${esc(x.fiduciaryTitle || '')}</td>
     <td>${x.fieldsFilled}</td>
     <td>${x.elapsedMs} ms</td>
     <td><span class="pill ok">${x.llmCallsAtRuntime}</span></td>
     <td class="mono note">${esc((r.bindingSha256 || '').slice(0, 12))}…</td></tr>`).join('');
  el('reuse').innerHTML = `
    <h2>One binding, five estates</h2>
    <p class="thesis">The same approved file produced all five of these documents. The
      data differs, the state differs, the legal route differs — the binding does not,
      and no AI ran. That last column is the same sha256 on every row.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      <div class="card"><div class="k">Estates filled</div><div class="v">${r.rows.length}</div>
        <div class="sub">from one artifact</div></div>
      <div class="card"><div class="k">AI calls while filling</div>
        <div class="v" style="color:#0a6b2b">${r.totalLlmCalls}</div>
        <div class="sub">counted, not asserted</div></div>
      <div class="card"><div class="k">Contradictions found</div><div class="v">${r.violations}</div>
        <div class="sub">tick-box rules checked every time</div></div>
      <div class="card"><div class="k">Time per estate</div>
        <div class="v">${Math.min(...r.rows.map(x => x.elapsedMs))}–${
          Math.max(...r.rows.map(x => x.elapsedMs))} ms</div>
        <div class="sub">vs 485 s to compile the form once</div></div>
    </div>
    <table><thead><tr><th>Estate</th><th>Authority in the record</th><th>Line 1 box</th>
      <th>Line 2a date of death</th><th>Line 2b date of appointment</th>
      <th>Signing title</th><th>Boxes filled</th><th>Time</th><th>AI calls</th>
      <th>Binding sha256</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="note" style="margin:10px 0 18px">Binding: <code>${esc(r.bindingRef)}</code>
      · sha256 <span class="mono">${esc(r.bindingSha256)}</span></p>
    <h3>The same region of the paper, five times</h3>
    ${r.strip ? `<img class="shot" src="${asset(r.strip)}" alt="Section A across five estates">`
              : '<p class="note">strip image missing</p>'}`;
}

// ---------------------------------------------------------- 5. SELF-CORRECTION
function paintLoop() {
  const l = W.loop;
  if (!l.available) {
    el('loop').innerHTML = `<h2>Self-correction</h2><p class="thesis">No loop history on
      disk yet.</p>`;
    return;
  }
  const rounds = l.history.map(h => `
    <div class="roundhdr"><h3>Round ${h.round}</h3>
      <span class="pill ${h.findingCount ? 'bad' : 'ok'}">${
        h.findingCount ? h.findingCount + ' problem(s) found' : 'nothing wrong'}</span>
      <span class="note">${h.fieldsFilled} boxes filled${
        h.repair ? ` · ${h.repair.changed.length} binding(s) rewritten after this round` : ''}</span></div>
    ${h.findingCount ? `<ul class="findings">${h.findings.map(f =>
      `<li><b>${esc(f.target)}</b> — ${esc(f.problem)}</li>`).join('')}</ul>`
      : `<p class="note">The reviewer looked at the page and found nothing to fix. This is
         where the loop stops.</p>`}
    <div class="sidebyside">${(h.renders || []).map(p =>
      `<div><img class="shot" src="${asset(p)}" alt="round ${h.round}"></div>`).join('')}</div>`).join('');
  el('loop').innerHTML = `
    <h2>How the binding got corrected</h2>
    <p class="thesis">A first draft of a form-filler is usually wrong somewhere. Forge
      fills the form, renders it to an image, and asks a reviewer to look at the
      <b>picture</b> — not at the code that made it — then fixes what it finds and repeats.
      ${l.rounds} rounds here, and it stops when a round finds nothing.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      <div class="card"><div class="k">Rounds</div><div class="v">${l.rounds}</div>
        <div class="sub">stopped when clean</div></div>
      <div class="card"><div class="k">Problems found in round 1</div>
        <div class="v">${l.history[0] ? l.history[0].findingCount : '—'}</div>
        <div class="sub">read off the rendered page</div></div>
      <div class="card"><div class="k">Build-time AI calls</div><div class="v">${l.modelCalls}</div>
        <div class="sub">${Math.round(l.elapsedSeconds)} s, once for this form</div></div>
      <div class="card"><div class="k">Estate used</div>
        <div class="v" style="font-size:13px">${esc(l.estateId.replace(/^estate-\d+-/, ''))}</div>
        <div class="sub">deliberately not the one it was calibrated on</div></div>
    </div>
    <p class="note" style="max-width:900px;margin-bottom:4px">This run started from a
      deliberately naive first draft — the older, weaker instructions, with none of
      tonight's lessons folded in — so the loop had real mistakes to catch. Showing it
      converge on a pre-corrected draft would prove nothing.</p>
    ${rounds}`;
}

// -------------------------------------------------------------- 6. SPONSOR RUNTIME
function paintAnvil() {
  const a = W.anvil;
  if (!a.available) {
    el('anvil').innerHTML = `<h2>Sponsor runtime</h2><p class="thesis">No drift report on
      disk yet.</p>`;
    return;
  }
  el('anvil').innerHTML = `
    <h2>The failure that looks like success</h2>
    <p class="thesis">The same approved binding also drives Anvil, the sponsor's filling
      service — including on the hardest of the four forms. But its fill endpoint drops a
      value it does not recognise <b>without saying so</b>: you get a finished-looking PDF
      with one box empty. Forge checks first and refuses.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      <div class="card"><div class="k">Boxes Anvil recognised</div>
        <div class="v">${a.detectedFieldCount ?? '—'}</div>
        <div class="sub">on a two-layer PDF that defeats most tools</div></div>
      <div class="card"><div class="k">Values we meant to send</div><div class="v">${a.valuesIntended}</div></div>
      <div class="card"><div class="k">Values that arrived</div>
        <div class="v" style="color:#a3001d">${a.valuesActuallySent}</div>
        <div class="sub">one was dropped in silence</div></div>
      <div class="card"><div class="k">What came back</div>
        <div class="v">${Math.round(a.beforeBytes / 1024)} KB</div>
        <div class="sub">valid PDF, HTTP 200, no warning</div></div>
    </div>
    <p style="max-width:900px">The scenario is the ordinary one: <b>the tax authority
      renames a field</b>. Our approved binding still asks for
      <code>${esc((a.renamedFrom || '').split('.').pop())}</code>; the service now calls it
      <code>${esc((a.renamedTo || '').split('.').pop())}</code>. Nothing errors.</p>
    <div class="sidebyside">
      <div>
        <h3>Without the check — a hole in the page</h3>
        ${a.holeImage ? `<img class="shot" src="${asset(a.holeImage)}" alt="the hole">` : ''}
        <p class="note">Line 2a — the date of death, on a filing that establishes who may
          act for the estate — is blank. Everything around it is correct, which is exactly
          why nobody would catch it.</p>
      </div>
      <div>
        <h3>With the check — it refuses</h3>
        <div class="refusal">${esc(a.afterError || '')}</div>
        <table style="margin-top:12px"><tbody>
          <tr><td>Requests actually sent</td><td><b>${a.fillRequestsSentAfter}</b>
            <div class="note">not sent-then-discarded — never sent</div></td></tr>
          <tr><td>File written</td><td><b>${a.afterPdfExists ? 'yes' : 'no'}</b>
            <div class="note">nothing that looks finished but is not</div></td></tr>
          <tr><td>Cast on file</td><td class="mono">${esc(a.castEid || '—')}</td></tr>
        </tbody></table>
      </div>
    </div>`;
}

// ------------------------------------------------------------------ 3. REVIEW
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
function focusRow(idx) {
  const boxes = (S.rows[idx].boxes || []).filter(b => !b.unsupported);
  if (boxes.length && !boxes.some(b => b.page === page)) {
    page = boxes[0].page;
    paintPage();
  }
  paintOverlay();
}
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && pinned !== null) { pinned = null; paintOverlay(); }
});
function sourceCell(r) {
  if (!r.source) return '<span class="note">nothing in the record matches this box</span>';
  const k = r.source.kind;
  let main;
  if (k === 'path') main = `<code>${esc(r.source.path)}</code>`;
  else if (k === 'constant') main = `always <code>${esc(JSON.stringify(r.source.value))}</code>`;
  else if (k === 'template')
    main = `<code>${esc(r.source.pattern)}</code><div class="note">${
      (r.source.paths||[]).map(esc).join(' · ')}</div>`;
  else if (k === 'condition')
    main = `tick when <code>${esc(r.source.path)}</code> is ${
      esc(JSON.stringify(r.source.equals))}`;
  else if (k === 'contains')
    main = `tick when <code>${esc(r.source.path)}</code> includes ${
      esc(JSON.stringify(r.source.includes))}`;
  else if (k === 'absent') main = `tick when <code>${esc(r.source.path)}</code> is missing`;
  else main = `<code>${esc(JSON.stringify(r.source))}</code>`;
  let g = '';
  if (r.when) {
    const want = r.when.equalsAny
      ? 'one of ' + esc(JSON.stringify(r.when.equalsAny))
      : esc(JSON.stringify(r.when.equals));
    g = `<div class="note">only when <code>${esc(r.when.path)}</code> is ${want}</div>`;
  }
  return main + g;
}
function paintReview() {
  const s = S;
  document.getElementById('rtitle').textContent =
    `${s.formId} — ${s.artifactSource === 'approved'
      ? 'approved v' + s.version : 'draft, not yet approved'}`;
  const c = s.counts;
  document.getElementById('rmeta').innerHTML =
    `<code>${esc(s.artifactPath)}</code> · estate <b>${esc(s.estateId)}</b><br>` +
    `${c.filled} boxes filled · ${c.conditionFalse} correctly blank (answer is no) · ` +
    `${c.guardedOff} correctly blank (line does not apply) · ` +
    `${c.absent} blank for want of data · ${c.unbound} with no data source`;
  const b = [];
  s.groupViolations.forEach(g => b.push(`<div class="banner violation">⚠ ${esc(g)}</div>`));
  if (s.requiredUnbound.length)
    b.push(`<div class="banner violation">Cannot approve — no data source for: ${
      s.requiredUnbound.map(esc).join(', ')}</div>`);
  if (s.requiredAbsent.length)
    b.push(`<div class="banner warnbox">${s.requiredAbsent.length} required line(s) are
      blank for <b>${esc(s.estateId)}</b> because this record has no value for them. The
      binding is right; the record is short. Does not block approval.</div>`);
  if (s.renderStale)
    b.push(`<div class="banner warnbox">The picture is older than the binding — re-run
      <code>forge propose ${esc(form)} --estate ${esc(s.estateId)} --from-draft</code>.</div>`);
  if (s.artifactSource === 'approved')
    b.push(`<div class="banner warnbox">Showing the <b>approved</b> artifact, which is
      frozen and cannot be edited. Add <code>&draft=1</code> to the URL to review a draft.</div>`);
  else if (s.draftMatchesApproved)
    b.push(`<div class="banner warnbox">This draft is identical to the approved version —
      approving it again would create a version that differs only by its timestamp, so
      approval is blocked.</div>`);
  document.getElementById('banners').innerHTML = b.join('');

  page = Math.min(page, Math.max(0, s.pages.length - 1));
  document.getElementById('pagebtns').innerHTML = s.pages.length
    ? s.pages.map((p, i) => `<button data-p="${i}">Page ${i+1}</button>`).join('')
    : '<span class="note" style="color:#ddd">no render on disk</span>';
  document.querySelectorAll('#pagebtns button').forEach(bt =>
    bt.onclick = () => { page = +bt.dataset.p; paintPage(); paintOverlay(); });
  document.getElementById('pdflink').href = `/render/${form}/draft.pdf`;

  document.querySelector('#rows tbody').innerHTML = s.rows.map((r, i) => {
    const cls = [r.status === 'unbound' ? 'flag' : (r.confidence === 'low' ? 'low' : ''),
                 r.reviewed ? 'done' : ''].filter(Boolean).join(' ');
    const badges = (r.status === 'unbound' ? '<span class="badge b-unbound">NO SOURCE</span>' : '')
      + (r.confidence === 'low' ? '<span class="badge b-low">CHECK ME</span>' : '')
      + (r.required ? '<span class="badge b-req">REQUIRED</span>' : '');
    const editable = s.artifactSource === 'draft';
    return `<tr class="${cls}" data-idx="${i}">
      <td class="item">${esc(r.itemNumber ?? '—')}</td>
      <td>${badges}${esc(r.label)}<div class="note">${esc(r.qualifiedName)}</div></td>
      <td>${sourceCell(r)}</td>
      <td><span class="val">${esc(r.value ?? '')}</span>${
        r.reason ? `<div class="note">${esc(r.reason)}</div>` : ''}</td>
      <td class="s-${r.status}" title="${esc(r.statusHint)} (${esc(r.statusTerm)})">${
        esc(r.statusLabel)}</td>
      <td class="acts">${editable ? `
        <button class="${r.reviewed ? 'ok' : ''}" data-a="rev" data-q="${esc(r.qualifiedName)}"
          data-v="${r.reviewed ? 0 : 1}">${r.reviewed ? '✓' : 'looks right'}</button>
        ${r.editablePath ? `<button data-a="edit" data-q="${esc(r.qualifiedName)}"
          data-p="${esc(r.editablePath)}">change source</button>` : ''}
        ${r.status !== 'unbound' ? `<button data-a="unbind"
          data-q="${esc(r.qualifiedName)}">no source</button>` : ''}` : ''}
      </td></tr>`;
  }).join('');

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
    ev.stopPropagation();
    const q = btn.dataset.q, a = btn.dataset.a;
    if (a === 'rev') return act(q, {reviewed: btn.dataset.v === '1'});
    if (a === 'edit') {
      const p = prompt(`Which value should fill "${q}"?`, btn.dataset.p);
      if (p && p !== btn.dataset.p) return act(q, {path: p});
      return;
    }
    const note = prompt(`Why has this box no data source? (what would fill it)`, '');
    if (note !== null) return act(q, {markUnbound: true, note});
  });

  const ap = document.getElementById('approve');
  const frozen = s.artifactSource === 'approved';
  const noop = s.draftMatchesApproved;
  ap.textContent = frozen ? 'Already approved — frozen'
    : (noop ? 'Nothing to approve' : (s.approveBlocked ? 'Cannot approve yet'
      : `Approve this binding as v${s.nextVersion}`));
  ap.disabled = frozen || noop || s.approveBlocked;
  ap.onclick = async () => {
    const r = await fetch('/api/approve', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({form, approvedBy: document.getElementById('who').value})});
    const j = await r.json();
    alert(r.ok ? `Approved: ${j.path}` : `Refused: ${j.detail}`);
    load();
  };
}
async function act(qn, patch) {
  const r = await fetch('/api/row', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({form, qualifiedName: qn, patch})});
  if (!r.ok) alert('Refused: ' + ((await r.json()).detail || r.status));
  load();
}

// ------------------------------------------------------------------ boot
function paintPickers() {
  const eSel = document.getElementById('estatePick');
  eSel.innerHTML = (S.estates || []).map(e =>
    `<option value="${e}"${e === estate ? ' selected' : ''}>${
      e.replace(/^estate-\d+-/, '')}</option>`).join('');
  eSel.onchange = () => { estate = eSel.value; navigate(); };
  const fSel = document.getElementById('formPick');
  const forms = (W.forms.rows || []).map(r => r.formId);
  fSel.innerHTML = forms.map(f =>
    `<option value="${f}"${f === form ? ' selected' : ''}>${f}</option>`).join('');
  fSel.onchange = () => { form = fSel.value; navigate(); };
}
function navigate() {
  const u = new URL(location);
  u.searchParams.set('form', form); u.searchParams.set('estate', estate);
  u.searchParams.set('tab', tab);
  location.href = u.toString();
}
async function load() {
  const wres = await fetch(`/api/walkthrough?estate=${estate}&form=${form}`);
  W = await wres.json();
  const sres = await fetch(
    `/api/state?form=${form}&estate=${estate}${preferDraft ? '&draft=1' : ''}`);
  if (sres.ok) {
    S = await sres.json();
    S.renderToken = Date.now();
    paintReview();
  } else {
    S = {estates: W.estates || [], rows: [], pages: []};
    document.getElementById('right').innerHTML =
      `<h2>${esc(form)} is not compiled</h2><p class="thesis">No approved binding and no
       draft for this form, so Forge will not fill it. See the FORMS NEEDED tab.</p>`;
  }
  paintEstate(); paintForms(); paintReuse(); paintLoop(); paintAnvil();
  paintPickers(); paintTabs(); showTab();
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
    def state(form: str, estate: str, draft: int = 0) -> dict[str, Any]:
        try:
            return review_state(form, estate, prefer_draft=bool(draft))
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/walkthrough")
    def walkthrough_data(estate: str, form: str = "irs-f56") -> dict[str, Any]:
        from . import walkthrough as wt
        from .registry import ESTATES_DIR as ED

        try:
            return {
                "estate": wt.estate_panel(estate),
                "forms": wt.forms_panel(estate),
                "reuse": wt.reuse_panel(),
                "loop": wt.loop_panel(),
                "anvil": wt.anvil_panel(),
                "estates": sorted(q.stem for q in ED.glob("*.json")),
            }
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

    @app.get("/asset/{path:path}")
    def asset(path: str) -> Any:
        """Serve demo assets by their repo-relative path (out/demo/..., out/renders/...).

        Confined to out/ — the walkthrough panels reference generated evidence, and
        nothing outside the output tree is ever servable."""
        from .registry import OUT, ROOT

        target = (ROOT / path).resolve()
        if not str(target).startswith(str(OUT.resolve())) or not target.is_file():
            raise HTTPException(404, "no such asset")
        return FileResponse(target)

    return app


def serve(port: int = 8000, form: str = "irs-f56", estate: str = "estate-05-in-formal-probate") -> int:
    import uvicorn

    print(f"http://127.0.0.1:{port}/?form={form}&estate={estate}", flush=True)
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0
