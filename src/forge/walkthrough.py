"""Data for the demo walkthrough panels. Reads artifacts and reports; no model calls.

The review UI is the only thing a judge sees, so it has to carry the whole argument in
the order the argument is made: this estate → these forms are needed, and here is why
one is refused → here is the binding a human approved → here is the same binding on
five estates → here is the loop that corrected it → here is the sponsor runtime and the
failure it hides.

Everything here is assembled from files already on disk. Nothing is computed for
display that is not also the thing the pipeline actually used.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .estatepath import EstateData
from .registry import (
    APPROVED_DIR,
    BINDINGS_DIR,
    FORMS,
    OUT,
    REPORTS_DIR,
    estate_path,
    get_form,
    load_work_order,
    rel,
)

DEMO = OUT / "demo"

# Plain English for the jargon. The technical term survives as a tooltip; the label
# a judge reads leads with the meaning.
STATUS_COPY: dict[str, dict[str, str]] = {
    "filled": {
        "label": "filled from the record",
        "term": "filled",
        "hint": "a value from the estate record was written into this box",
    },
    "absent": {
        "label": "no data in this estate",
        "term": "absent",
        "hint": "the binding knows where this comes from, but this estate has no value "
        "there. Left blank and reported — never invented.",
    },
    "condition-false": {
        "label": "correctly blank — the record says no",
        "term": "condition-false",
        "hint": "a tick-box whose answer is genuinely 'no' for this estate",
    },
    "guarded-off": {
        "label": "correctly blank — this line does not apply",
        "term": "guarded-off",
        "hint": "the form itself says this line only applies on the other branch "
        "(e.g. line 2b applies only when 1c/1e/1f/1g is ticked)",
    },
    "unbound": {
        "label": "no data source found",
        "term": "unbound",
        "hint": "nothing in the estate record corresponds to this box. Reported rather "
        "than guessed.",
    },
}

BLAST_COPY = {
    "high": "a mistake here is serious",
    "medium": "a mistake here has moderate consequences",
    "low": "low stakes",
}
REVERSIBILITY_COPY = {
    "irreversible": "hard to undo once filed",
    "reversible": "can be corrected later",
}
ROUTE_COPY = {
    "FORMAL_PROBATE": "formal probate — the court supervises the estate",
    "ANCILLARY_PROBATE": "ancillary probate — a second state's court, for property held there",
    "INDEPENDENT_ADMINISTRATION": "independent administration — court-appointed, minimal supervision",
    "TRUST_ADMINISTRATION": "trust administration — a trustee acts under a trust document, "
    "not a court order",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_versions(form_id: str) -> list[int]:
    return sorted(
        int(m.group(1))
        for p in APPROVED_DIR.glob(f"{form_id}.v*.json")
        if (m := re.search(r"\.v(\d+)\.json$", p.name))
    )


def compile_state(form_id: str) -> dict[str, Any]:
    """Is this form compiled, and how far along? The FORMS NEEDED panel's right column."""
    versions = approved_versions(form_id)
    draft = BINDINGS_DIR / f"{form_id}.json"
    state: dict[str, Any] = {
        "formId": form_id,
        "approvedVersions": versions,
        "highestApproved": versions[-1] if versions else None,
        "hasDraft": draft.exists(),
    }
    if versions:
        path = APPROVED_DIR / f"{form_id}.v{versions[-1]}.json"
        art = json.loads(path.read_text(encoding="utf-8"))
        state.update(
            {
                "status": "approved",
                "headline": f"compiled and approved — version {versions[-1]}",
                "approvedBy": art.get("approvedBy"),
                "approvedAt": art.get("approvedAt"),
                "boundCount": len(art.get("bindings") or []),
                "unboundCount": len(art.get("unbound") or []),
                "artifact": rel(path),
                "sha256": _sha256(path),
            }
        )
    elif draft.exists():
        art = json.loads(draft.read_text(encoding="utf-8"))
        state.update(
            {
                "status": "draft",
                "headline": "drafted, waiting for a human to approve it",
                "boundCount": len(art.get("bindings") or []),
                "unboundCount": len(art.get("unbound") or []),
                "artifact": rel(draft),
                "sha256": _sha256(draft),
            }
        )
    else:
        state.update(
            {
                "status": "not-compiled",
                "headline": "not compiled yet — Forge will not produce this form",
                "boundCount": None,
                "unboundCount": None,
                "artifact": None,
            }
        )
    return state


def estate_panel(estate_id: str) -> dict[str, Any]:
    """Panel 1 — who this is, in plain language, and where the facts came from."""
    est = EstateData.load(estate_path(estate_id))
    order = load_work_order(estate_id)

    def g(path: str) -> Any:
        r = est.resolve(path)
        return r.value if r.present else None

    juris = order.get("jurisdiction") or {}
    return {
        "estateId": estate_id,
        "decedentName": g("decedent.name.full"),
        "dateOfDeath": g("decedent.dateOfDeath"),
        "residence": ", ".join(
            x for x in (
                g("decedent.residenceAddress.city"),
                g("decedent.residenceAddress.state"),
            ) if x
        ),
        "fiduciaryName": g("fiduciary.name.full"),
        "fiduciaryTitle": g("fiduciary.title") or g("form56.signature.title"),
        "entityName": g("estateEntity.legalName"),
        "ein": g("estateEntity.ein"),
        "einPlain": (
            f"the estate already has a tax number ({g('estateEntity.ein')})"
            if g("estateEntity.ein")
            else "the estate has no tax number yet — one must be applied for"
        ),
        "jurisdiction": juris,
        "jurisdictionPlain": (
            f"{juris.get('county')} County, {juris.get('state')}"
            if juris.get("county") else juris.get("state")
        ),
        "route": order.get("route"),
        "routePlain": ROUTE_COPY.get(order.get("route") or "", order.get("route")),
        "authorityBasis": g("authority.basis"),
        "sources": {
            "estateRecord": rel(estate_path(estate_id)),
            "workOrder": f"artifacts/workorders/{estate_id}.json",
            "generatedBy": order.get("generatedBy") or "warrant_mock",
            "generatedAt": order.get("generatedAt"),
        },
        "provenanceNote": (
            (est.resolve("provenance.note").value if est.resolve("provenance.note").present
             else None)
            or "Synthetic record. SSNs come from the 900 block, which has never been "
               "issued — safe to show on a projector."
        ),
    }


def forms_panel(estate_id: str) -> dict[str, Any]:
    """Panel 2 — the work order: every form, applicable or refused, with the reason
    verbatim, next to whether Forge can actually produce it."""
    order = load_work_order(estate_id)
    by_id = {f["formId"]: f for f in order["forms"]}
    rows = []
    for form in FORMS:
        entry = by_id.get(form.form_id) or {}
        applicable = entry.get("applicable")
        state = compile_state(form.form_id)
        rows.append(
            {
                "formId": form.form_id,
                "title": form.title,
                "filename": form.filename,
                "applicable": applicable,
                "reason": entry.get("reason"),
                "priority": entry.get("priority"),
                "blastRadius": entry.get("blastRadius"),
                "blastRadiusPlain": BLAST_COPY.get(entry.get("blastRadius") or ""),
                "reversibility": entry.get("reversibility"),
                "reversibilityPlain": REVERSIBILITY_COPY.get(entry.get("reversibility") or ""),
                "compile": state,
                # what a judge should take away from this row
                "verdict": (
                    "not needed for this estate" if applicable is False
                    else state["headline"]
                ),
            }
        )
    applicable_rows = [r for r in rows if r["applicable"]]
    return {
        "estateId": estate_id,
        "route": order.get("route"),
        "decidedBy": order.get("generatedBy") or "warrant_mock",
        "rows": rows,
        "counts": {
            "total": len(rows),
            "needed": len(applicable_rows),
            "refused": sum(1 for r in rows if r["applicable"] is False),
            "readyToProduce": sum(
                1 for r in applicable_rows if r["compile"]["status"] == "approved"
            ),
            "draftOnly": sum(
                1 for r in applicable_rows if r["compile"]["status"] == "draft"
            ),
            "notCompiled": sum(
                1 for r in applicable_rows if r["compile"]["status"] == "not-compiled"
            ),
        },
    }


def reuse_panel() -> dict[str, Any]:
    """Panel 4 — the five-estate proof, read from the sidecars the run actually wrote."""
    reuse_dir = DEMO / "reuse"
    strip = DEMO / "reuse-section-a.png"
    rows = []
    for p in sorted(reuse_dir.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.append(
            {
                "estateId": d["estateId"],
                "jurisdiction": d.get("jurisdiction"),
                "route": d.get("route"),
                "authorityBasis": d.get("authorityBasis"),
                "line1": ", ".join(d.get("line1Ticked") or []) or "none",
                "date2a": d.get("date2a"),
                "date2b": d.get("date2b"),
                "fiduciaryTitle": d.get("fiduciaryTitle"),
                "fieldsFilled": d.get("fieldsFilled"),
                "elapsedMs": d.get("elapsedMs"),
                "llmCallsAtRuntime": d.get("llmCallsAtRuntime"),
                "groupViolations": d.get("groupViolations") or [],
            }
        )
    versions = approved_versions("irs-f56")
    binding_path = (
        APPROVED_DIR / f"irs-f56.v{versions[-1]}.json" if versions else None
    )
    return {
        "available": bool(rows) and strip.exists(),
        "strip": rel(strip) if strip.exists() else None,
        "rows": rows,
        "bindingRef": rel(binding_path) if binding_path else None,
        "bindingSha256": _sha256(binding_path) if binding_path else None,
        "totalLlmCalls": sum(r["llmCallsAtRuntime"] or 0 for r in rows),
        "violations": sum(len(r["groupViolations"]) for r in rows),
        "report": rel(DEMO / "reuse.md") if (DEMO / "reuse.md").exists() else None,
    }


def loop_panel() -> dict[str, Any]:
    """Panel 5 — the round history: what was wrong, and the render that showed it."""
    candidates = sorted(REPORTS_DIR.glob("irs-f56-loop.*.json")) + [
        REPORTS_DIR / "irs-f56-loop.json"
    ]
    path = next((c for c in candidates if c.exists()), None)
    if path is None:
        return {"available": False}
    r = json.loads(path.read_text(encoding="utf-8"))
    rounds = []
    for h in r["history"]:
        findings = (h.get("deterministicFindings") or []) + (h.get("modelFindings") or [])
        rounds.append(
            {
                "round": h["round"],
                "findings": [
                    {"target": f.get("target"), "problem": f.get("problem")}
                    for f in findings
                ],
                "findingCount": len(findings),
                "fieldsFilled": h.get("fieldsFilled"),
                "renders": h.get("renders") or [],
                "repair": h.get("diffFromPreviousRound"),
            }
        )
    return {
        "available": True,
        "formId": r["formId"],
        "estateId": r["estateId"],
        "converged": r["converged"],
        "rounds": r["rounds"],
        "modelCalls": r["modelCalls"],
        "elapsedSeconds": r["elapsedSeconds"],
        "report": rel(path),
        "history": rounds,
        "naive": True,
    }


def anvil_panel() -> dict[str, Any]:
    """Panel 6 — the silent-failure demonstration and the refusal."""
    report = DEMO / "anvil-drift" / "report.json"
    if not report.exists():
        return {"available": False}
    d = json.loads(report.read_text(encoding="utf-8"))
    hole = DEMO / "anvil-drift" / "before-the-hole.png"
    reg_path = Path("artifacts/anvil/irs-f56.json")
    registry = (
        json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else None
    )
    return {
        "available": True,
        "renamedFrom": d.get("renamedFrom"),
        "renamedTo": d.get("renamedTo"),
        "valuesIntended": d.get("valuesIntended"),
        "valuesActuallySent": d.get("valuesActuallySent"),
        "silentlyDropped": d.get("silentlyDropped") or [],
        "beforeBytes": d.get("beforeBytes"),
        "beforeLooksValid": d.get("beforeLooksValid"),
        "afterRefused": d.get("afterRefused"),
        "afterError": d.get("afterError"),
        "fillRequestsSentAfter": d.get("fillRequestsSentAfter"),
        "afterPdfExists": d.get("afterPdfExists"),
        "holeImage": rel(hole) if hole.exists() else None,
        "castEid": (registry or {}).get("castEid"),
        "detectedFieldCount": (registry or {}).get("detectedFieldCount"),
        "report": rel(report),
    }
