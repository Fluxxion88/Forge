"""Stage 5 — benchmark across every applicable (estate, form) pair.

Honest denominators from docs/00-DOMAIN.md §5, with the applicable-pair count at 14:
SS-4 is not produced for the three estates that already hold an EIN, and refusing to
produce it is a result, not a gap. No accuracy figure is reported unless a recorded
human check exists; absent that, the column reads "not measured".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import llm
from .estatepath import EstateData
from .fill import fill_pdf, load_approved
from .registry import (
    BINDINGS_DIR,
    CALIBRATION_DIR,
    ESTATES_DIR,
    FILLS_DIR,
    REPORTS_DIR,
    get_form,
    load_work_order,
    rel,
)


def _loop_stats(form_id: str) -> dict[str, Any]:
    p = REPORTS_DIR / f"{form_id}-loop.json"
    if not p.exists():
        return {}
    r = json.loads(p.read_text(encoding="utf-8"))
    return {
        "rounds": r.get("rounds"),
        "converged": r.get("converged"),
        "loopSeconds": r.get("elapsedSeconds"),
        "loopModelCalls": r.get("modelCalls"),
    }


def _calibration_stats(form_id: str) -> dict[str, Any]:
    p = CALIBRATION_DIR / f"{form_id}.json"
    if not p.exists():
        return {}
    c = json.loads(p.read_text(encoding="utf-8"))
    return {"calibrationModelCalls": c.get("modelCalls"), "fields": len(c.get("fields", []))}


def run_bench() -> int:
    estates = sorted(p.stem for p in ESTATES_DIR.glob("*.json"))
    rows: list[dict[str, Any]] = []
    pair_count = 0
    applicable_count = 0

    for estate_id in estates:
        order = load_work_order(estate_id)
        for entry in order["forms"]:
            pair_count += 1
            form_id = entry["formId"]
            row: dict[str, Any] = {
                "estateId": estate_id,
                "formId": form_id,
                "applicable": entry["applicable"],
                "skipReason": entry["reason"],
            }
            if not entry["applicable"]:
                rows.append(row)
                continue
            applicable_count += 1
            try:
                binding, binding_path = load_approved(form_id)
            except FileNotFoundError:
                # "no approved binding" covers two materially different states and the
                # report must not blur them: a form nobody has compiled at all, versus a
                # form that IS compiled and is waiting on a human. Both refuse to fill —
                # docs/02 §4 never falls back to a draft — but only the first is a gap in
                # the system. Conflating them made 9 pairs look uncompiled when 2 forms
                # were in fact drafted and queued for approval.
                draft = BINDINGS_DIR / f"{form_id}.json"
                if draft.exists():
                    d = json.loads(draft.read_text(encoding="utf-8"))
                    row["status"] = "compiled, awaiting human approval"
                    row["fieldsBound"] = len(d.get("bindings") or [])
                    row["fieldsUnbound"] = len(d.get("unbound") or [])
                    row["draft"] = rel(draft)
                else:
                    row["status"] = "not compiled (no binding at all)"
                rows.append(row)
                continue

            form = get_form(form_id)
            calls_before = llm.client.count
            t0 = time.monotonic()
            with llm.forbid_model_calls():
                estate = EstateData.load(ESTATES_DIR / f"{estate_id}.json")
                out_pdf = FILLS_DIR / f"{estate_id}-{form_id}.pdf"
                result = fill_pdf(binding, estate, form.path, out_pdf)
            elapsed_ms = round((time.monotonic() - t0) * 1000)

            row.update(
                {
                    "status": "filled",
                    "bindingVersion": binding["version"],
                    "fieldsBound": len(binding["bindings"]),
                    "fieldsUnbound": len(binding["unbound"]),
                    "fieldsFilled": sum(1 for f in result.fields if f.filled),
                    "fieldsEmptyReported": len(result.empty) + len(binding["unbound"]),
                    "groupViolations": result.group_violations,
                    "fillMs": elapsed_ms,
                    "llmCallsAtRuntime": llm.client.count - calls_before,
                    "pdf": rel(out_pdf),
                }
            )
            rows.append(row)

    build_stats = {
        f: {**_calibration_stats(f), **_loop_stats(f)}
        for f in sorted({r["formId"] for r in rows})
    }

    report = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "denominators": {
            "totalFieldsAcrossForms": 272,
            "fieldsWithNoSemanticHint": "~220",
            "buttonFieldsWithDiscoveredOnValues": 103,
            "pairs": pair_count,
            "applicablePairs": applicable_count,
            "note": "applicable=14: SS-4 is refused for the three estates already holding an EIN",
        },
        "accuracy": "not measured — no recorded human check exists yet; measuring it means "
        "a human comparing each filled render against the blank form field by field "
        "and recording the result per pair",
        "buildStats": build_stats,
        "rows": rows,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    # markdown table
    lines = [
        "# Forge benchmark",
        "",
        f"Generated {report['generatedAt']}. {applicable_count} applicable pairs of "
        f"{pair_count}; SS-4 correctly refused where an EIN already exists.",
        "",
        "A pair that is not filled is in one of two states, and they are not the same "
        "thing: **compiled, awaiting human approval** means the binding exists and a "
        "person has to sign it off before anything is produced (`forge fill` never falls "
        "back to a draft); **not compiled** means there is no binding at all.",
        "",
        "| Estate | Form | Applicable | Filled | Empty (reported) | Fill ms | LLM calls at fill |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["applicable"] and r.get("status") == "filled":
            lines.append(
                f"| {r['estateId']} | {r['formId']} | yes | {r['fieldsFilled']} "
                f"| {r['fieldsEmptyReported']} | {r['fillMs']} | **{r['llmCallsAtRuntime']}** |"
            )
        elif r["applicable"]:
            bound = (
                f"{r['fieldsBound']} bound / {r['fieldsUnbound']} unbound — "
                if r.get("fieldsBound") is not None else ""
            )
            lines.append(
                f"| {r['estateId']} | {r['formId']} | yes | — | — | — "
                f"| {bound}{r['status']} |"
            )
        else:
            reason = (r["skipReason"] or "")[:60]
            lines.append(f"| {r['estateId']} | {r['formId']} | no — {reason} | | | | |")
    lines += [
        "",
        "## Build cost per form (once, ever)",
        "",
        "| Form | Calibration calls | Loop rounds | Converged | Loop calls | Loop seconds |",
        "|---|---|---|---|---|---|",
    ]
    for f, s in build_stats.items():
        lines.append(
            f"| {f} | {s.get('calibrationModelCalls', '—')} | {s.get('rounds', '—')} "
            f"| {s.get('converged', '—')} | {s.get('loopModelCalls', '—')} "
            f"| {s.get('loopSeconds', '—')} |"
        )
    lines += ["", f"Accuracy: {report['accuracy']}", ""]
    (REPORTS_DIR / "benchmark.md").write_text("\n".join(lines), encoding="utf-8")

    zero = all(
        r.get("llmCallsAtRuntime", 0) == 0 for r in rows if r.get("status") == "filled"
    )
    filled = sum(1 for r in rows if r.get("status") == "filled")
    print(f"wrote {rel(REPORTS_DIR / 'benchmark.json')}")
    print(f"wrote {rel(REPORTS_DIR / 'benchmark.md')}")
    awaiting = sum(1 for r in rows if r.get("status") == "compiled, awaiting human approval")
    uncompiled = sum(1 for r in rows if r.get("status") == "not compiled (no binding at all)")
    print(
        f"{pair_count} pairs, {applicable_count} applicable, {filled} filled, "
        f"{awaiting} compiled-awaiting-approval, {uncompiled} with no binding at all, "
        f"llmCallsAtRuntime all zero: {zero}"
    )
    print("PASS" if zero and filled > 0 else "FAIL")
    return 0 if zero and filled > 0 else 1
