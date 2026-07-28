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


def review_state(form_id: str, estate_id: str) -> dict[str, Any]:
    """Everything the page needs: rows sorted worst-first, group check, renders."""
    artifact = load_draft(form_id)
    estate = EstateData.load(estate_path(estate_id))
    result = resolve_all(artifact, estate)
    by_name = {f.qualified_name: f for f in result.fields}

    rows = []
    for b in artifact["bindings"]:
        f = by_name[b["qualifiedName"]]
        if f.guarded_off:
            status = "guarded-off"
        elif f.filled:
            status = "filled"
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
    renders = sorted(
        str(p.relative_to(RENDERS_DIR)) for p in (RENDERS_DIR / form_id).glob("round-*.png")
    )
    # show the final round's pages
    final_round = max(
        (int(m.group(1)) for r in renders if (m := re.search(r"round-(\d+)-page", r))),
        default=None,
    )
    pages = [r for r in renders if final_round and f"round-{final_round}-page" in r]

    return {
        "formId": form_id,
        "estateId": estate_id,
        "estates": sorted(p.stem for p in ESTATES_DIR.glob("*.json")),
        "status": artifact["status"],
        "version": artifact["version"],
        "nextVersion": next_version(form_id),
        "rows": rows,
        "groupViolations": violations,
        "requiredUnfilled": required_unfilled,
        "approveBlocked": bool(violations or required_unfilled),
        "pages": pages,
        "counts": {
            "bound": len(artifact["bindings"]),
            "unbound": len(artifact["unbound"]),
            "filled": sum(1 for f in result.fields if f.filled),
            "guardedOff": sum(1 for f in result.fields if f.guarded_off),
        },
    }


def update_binding_row(form_id: str, qualified_name: str, patch: dict[str, Any]) -> None:
    """Row actions from the UI: edit the source path, or mark unbound with a note."""
    p = BINDINGS_DIR / f"{form_id}.json"
    artifact = load_draft(form_id)
    if patch.get("markUnbound"):
        kept = [b for b in artifact["bindings"] if b["qualifiedName"] != qualified_name]
        moved = [b for b in artifact["bindings"] if b["qualifiedName"] == qualified_name]
        if moved:
            artifact["bindings"] = kept
            artifact["unbound"].append(
                {
                    "qualifiedName": qualified_name,
                    "label": moved[0].get("label"),
                    "reason": patch.get("note") or "marked unbound in review",
                    "whatWouldFillIt": patch.get("note"),
                }
            )
    elif "path" in patch:
        for b in artifact["bindings"]:
            if b["qualifiedName"] == qualified_name and "path" in b["source"]:
                b["source"]["path"] = patch["path"]
                b["note"] = (b.get("note") or "") + " [path edited in review]"
    artifact["status"] = "draft"
    p.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ web


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Forge review</title>
<style>
 body { font: 14px/1.45 -apple-system, sans-serif; margin: 0; display: flex; height: 100vh; }
 #left { width: 52%; overflow: auto; background: #444; padding: 12px; }
 #left img { width: 100%; background: white; margin-bottom: 12px; box-shadow: 0 1px 6px rgba(0,0,0,.5); }
 #right { width: 48%; overflow: auto; padding: 16px; }
 h1 { font-size: 17px; margin: 0 0 4px; }
 .meta { color: #666; margin-bottom: 10px; }
 .violation { background: #b00020; color: white; padding: 10px 12px; border-radius: 6px;
              margin: 10px 0; font-weight: 600; }
 table { border-collapse: collapse; width: 100%; }
 th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
 tr.unbound { background: #fff3f3; }
 tr.low { background: #fff8e6; }
 .s-filled { color: #0a7a2f; } .s-absent { color: #b06a00; }
 .s-guarded-off { color: #5561c9; } .s-unbound { color: #b00020; font-weight: 600; }
 code { font-size: 12px; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
 #footer { position: sticky; bottom: 0; background: white; border-top: 2px solid #ddd;
           padding: 12px 0; }
 button { font-size: 15px; padding: 8px 18px; }
 button:disabled { opacity: .4; }
 .note { color: #666; font-size: 12px; }
</style></head><body>
<div id="left"></div>
<div id="right">
 <h1 id="title"></h1>
 <div class="meta" id="meta"></div>
 <div id="violations"></div>
 <table id="rows"><thead><tr>
   <th>Item</th><th>Label</th><th>Source</th><th>Value for this estate</th><th>Status</th>
 </tr></thead><tbody></tbody></table>
 <div id="footer">
   <label>Approved by <input id="who" placeholder="name"></label>
   <button id="approve"></button>
   <div class="note">You are approving the <b>binding</b>, not this document —
   one approval covers every future estate that uses this form.</div>
 </div>
</div>
<script>
const qs = new URLSearchParams(location.search);
const form = qs.get('form') || 'irs-f56';
const estate = qs.get('estate') || 'estate-05-in-formal-probate';
async function load() {
  const s = await (await fetch(`/api/state?form=${form}&estate=${estate}`)).json();
  document.getElementById('title').textContent = `${s.formId} — draft → v${s.nextVersion}`;
  document.getElementById('meta').innerHTML =
    `estate <b>${s.estateId}</b> · ${s.counts.bound} bound (${s.counts.filled} filled, ` +
    `${s.counts.guardedOff} guarded-off) · ${s.counts.unbound} unbound · ` +
    s.estates.map(e => `<a href="?form=${form}&estate=${e}">${e.split('-')[1]}</a>`).join(' ');
  document.getElementById('left').innerHTML =
    s.pages.map(p => `<img src="/render/${p}">`).join('');
  const v = document.getElementById('violations');
  v.innerHTML = s.groupViolations.map(g => `<div class="violation">⚠ ${g}</div>`).join('')
    + (s.requiredUnfilled.length ?
       `<div class="violation">Required unfilled: ${s.requiredUnfilled.join(', ')}</div>` : '');
  const tb = document.querySelector('#rows tbody');
  tb.innerHTML = s.rows.map(r => `
    <tr class="${r.status==='unbound'?'unbound':(r.confidence==='low'?'low':'')}">
     <td>${r.itemNumber ?? ''}</td>
     <td>${r.label ?? ''}<div class="note">${r.qualifiedName ?? ''}</div></td>
     <td><code>${r.source ? JSON.stringify(r.source) : 'unbound'}</code>
         ${r.when ? `<div class="note">when ${JSON.stringify(r.when)}</div>` : ''}</td>
     <td>${r.value ?? ''}<div class="note">${r.reason ?? ''}</div></td>
     <td class="s-${r.status}">${r.status}</td></tr>`).join('');
  const btn = document.getElementById('approve');
  btn.textContent = s.approveBlocked ? 'Approval blocked' : `Approve binding as v${s.nextVersion}`;
  btn.disabled = s.approveBlocked;
  btn.onclick = async () => {
    const who = document.getElementById('who').value;
    const r = await fetch('/api/approve', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({form, approvedBy: who})});
    const j = await r.json();
    alert(r.ok ? `Approved: ${j.path}` : `Refused: ${j.detail}`);
    load();
  };
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
        update_binding_row(body["form"], body["qualifiedName"], body.get("patch") or {})
        return {"ok": "true"}

    @app.get("/render/{path:path}")
    def render(path: str) -> Any:
        target = (RENDERS_DIR / path).resolve()
        if not str(target).startswith(str(RENDERS_DIR.resolve())) or not target.exists():
            raise HTTPException(404, "no such render")
        return FileResponse(target)

    return app


def serve(port: int = 8000) -> int:
    import uvicorn

    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0
