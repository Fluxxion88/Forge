"""Stage 2 — the convergence loop: fill, render, critique the IMAGE, repair, repeat.

Per docs/02-SPEC.md §2.2. The critique reads the rasterised page, never the JSON it
just wrote. Exclusive-group violations are found deterministically in code and fail
the round even when the critique misses them. Every round's renders and findings
stay on disk — the loop history is the demo, not a debug artifact.

Stop conditions: zero findings; 6 rounds; or two consecutive rounds with identical
findings (stuck — reported, not spun).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import bind, llm
from .calibrate import _Progress
from .estatepath import EstateData
from .fill import FillResult, fill_pdf
from .registry import REPORTS_DIR, RENDERS_DIR, estate_path, get_form, rel
from .fillwriter import render_pages

MAX_ROUNDS = 6

FINDING_KINDS = """Judge the IMAGE against each box's printed purpose and the estate
facts. Report ONLY genuine problems, as a JSON array (empty array [] if the page is
correct). Each finding: {"target": "<item number or printed label>",
"problem": "<one sentence>", "mustFix": true|false}. Look for:
- content in a box that does not match that box's printed purpose (e.g. the
  decedent's name where the caption says fiduciary, a city in a street-address box)
- content that contradicts the estate facts listed above
- a value in the wrong box, or shifted by one row/column
- text overflowing its rectangle or clipped
- a date rendered in a format the form does not use (this form wants MM/DD/YYYY)
- a checkbox marked that contradicts the estate facts, or a choice group with no mark
- a name or address split across the wrong lines
- a box left EMPTY — even one on the deliberately-empty list — whose printed purpose
  clearly has supporting data among the estate facts; name the data
An empty box with no available supporting data is correct, not a finding. Do not
invent problems on a correct page."""


def _box_table(cal: dict[str, Any]) -> str:
    """What the BLANK form says each box is for. This came from calibration, a
    separate pass that knows nothing about the binding — it is the independent
    standard the critique judges against, so the loop cannot mark its own homework."""
    lines = []
    for f in cal["fields"]:
        if f.get("isPushbutton"):
            continue
        where = f"item {f['itemNumber']}" if f["itemNumber"] else "unnumbered"
        kind = "checkbox" if f["type"] == "button" else "text box"
        lines.append(
            f"- page {f['page']}, {where} ({kind}): \"{f['printedLabel']}\" — {f['meaning']}"
        )
    return "\n".join(lines)


def _deliberately_empty(result: FillResult) -> str:
    lines = []
    for f in result.empty:
        where = f"item {f.item_number}" if f.item_number else (f.label or f.qualified_name)[:60]
        lines.append(f"- {where}: {f.reason or 'no supporting data'}")
    return "\n".join(lines) or "(none)"


def _critique(
    form_id: str, round_no: int, pngs: list[Path], cal: dict[str, Any],
    result: FillResult, estate_summary: str, progress: _Progress,
) -> list[dict[str, Any]] | None:
    """Model findings from the RENDERED image, or None if the call failed.

    Deliberately NOT given the binding or its intended values: the standard is what
    the blank form says each box is for, plus the estate facts."""
    image_lines = "\n".join(f"- page {i}: {p}" for i, p in enumerate(pngs))
    prompt = f"""You are auditing a filled government form {form_id} (round {round_no}).
FIRST use the Read tool to view every rendered page:
{image_lines}

What each box on this form is FOR, from a separate calibration of the BLANK form:
{_box_table(cal)}

Verified estate facts this filling must agree with:
{estate_summary}

Boxes the filler left empty on purpose (flag one only if the facts above clearly
support filling it):
{_deliberately_empty(result)}

{FINDING_KINDS}"""
    reply = progress.call_with_retry(
        f"critique round {round_no}",
        len(result.fields),
        lambda: llm.client.call(
            purpose=f"bind:{form_id}:critique:r{round_no}", prompt=prompt,
            images=len(pngs), timeout=300,
        ),
    )
    if reply is None:
        return None
    try:
        return llm.extract_json_array(reply)
    except llm.ModelCallFailed as exc:
        print(f"  WARN critique round {round_no} unparseable: {exc}", flush=True)
        return None


def _repair(
    form_id: str, round_no: int, artifact: dict[str, Any],
    findings: list[dict[str, Any]], estate_json: str, progress: _Progress,
) -> dict[str, Any] | None:
    prompt = f"""You are repairing the binding for government form {form_id}. Round
{round_no} produced these findings against the rendered page:
{json.dumps(findings, indent=1)}

The current binding artifact body:
{json.dumps({k: artifact[k] for k in ("bindings", "unbound", "exclusiveGroups")}, indent=1)}

The estate record (bind to PATHS, not values):
{estate_json}

{bind.BINDING_LANGUAGE}

Revise the binding to address every finding. Keep everything that is correct. If a
finding cannot be fixed with the five source kinds plus when-guards, move that field
to "unbound" with an explanation — never approximate.
Answer with ONLY a JSON object: {{"bindings": [...], "unbound": [...], "exclusiveGroups": [...]}}"""
    reply = progress.call_with_retry(
        f"repair round {round_no}",
        len(findings),
        lambda: llm.client.call(
            purpose=f"bind:{form_id}:repair:r{round_no}", prompt=prompt, timeout=420
        ),
    )
    if reply is None:
        return None
    try:
        return llm.extract_json_object(reply)
    except llm.ModelCallFailed as exc:
        print(f"  WARN repair round {round_no} unparseable: {exc}", flush=True)
        return None


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    o = {b["qualifiedName"]: b for b in old["bindings"]}
    n = {b["qualifiedName"]: b for b in new["bindings"]}
    return {
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "changed": sorted(
            q for q in set(o) & set(n)
            if (o[q]["source"], o[q].get("when"), o[q].get("format"))
            != (n[q]["source"], n[q].get("when"), n[q].get("format"))
        ),
    }


def _estate_summary(estate: EstateData) -> str:
    keys = [
        "decedent.name.full", "decedent.ssn", "decedent.dateOfDeath",
        "decedent.residenceAddress.line1", "decedent.residenceAddress.city",
        "decedent.residenceAddress.state", "decedent.residenceAddress.zip",
        "fiduciary.name.full", "fiduciary.title",
        "fiduciary.address.line1", "fiduciary.address.city",
        "fiduciary.address.state", "fiduciary.address.zip",
        "fiduciary.daytimePhone.number",
        "estateEntity.legalName", "estateEntity.ein",
        "authority.basis", "authority.hasWill", "authority.dateOfAppointment",
        "authority.proceeding.courtName", "authority.proceeding.docketNumber",
        "authority.proceeding.courtAddress.line1", "authority.proceeding.courtAddress.city",
        "authority.proceeding.courtAddress.state", "authority.proceeding.courtAddress.zip",
        "form56.signature.title", "taxMatters.authorizationRows[0].taxFormNumber",
    ]
    lines = []
    for k in keys:
        r = estate.resolve(k)
        if r.present:
            lines.append(f"- {k} = {r.value!r}")
    return "\n".join(lines)


def run_loop(form_id: str, estate_id: str, from_draft: bool = False) -> int:
    form = get_form(form_id)
    cal = bind.load_calibration(form_id)
    estate = EstateData.load(estate_path(estate_id))
    estate_json = json.dumps(estate.data, indent=1)
    # a from-draft stress run keeps its own renders and report; the original
    # loop history is part of the demo and must never be overwritten
    render_dir = RENDERS_DIR / form_id / estate_id if from_draft else RENDERS_DIR / form_id
    render_dir.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    progress = _Progress()
    t0 = time.monotonic()

    if from_draft:
        # Stress an existing draft against a different estate: no re-proposal, the
        # loop starts at fill/critique. This is how reuse gets tested before approval.
        from .registry import BINDINGS_DIR

        draft_file = BINDINGS_DIR / f"{form_id}.json"
        if not draft_file.exists():
            print(f"FAIL: --from-draft but no draft at {rel(draft_file)}")
            return 1
        artifact = json.loads(draft_file.read_text(encoding="utf-8"))
        problems: list[str] = []
        print(f"[from-draft] stressing existing draft against {estate_id}", flush=True)
    else:
        print(f"[propose] drafting binding for {form_id} against {estate_id}", flush=True)
        proposal = progress.call_with_retry(
            "propose", len(cal["fields"]),
            lambda: bind.propose(form_id, cal, estate_json, estate_id),
            attempts=1,  # a whole-form timeout repeats; fall back to pages fast
        )
        if proposal is None:
            # docs/05 §2: a timed-out call is retried at half the batch — per page here
            print("[propose] whole-form call failed twice; falling back to per-page proposals")
            pages = sorted({f["page"] for f in cal["fields"] if f["page"] is not None})
            parts = []
            for page in pages:
                n = sum(1 for f in cal["fields"] if f["page"] == page)
                part = progress.call_with_retry(
                    f"propose page {page}", n,
                    lambda page=page: bind.propose_one_page(
                        form_id, cal, page, estate_json, estate_id
                    ),
                )
                if part:
                    parts.append(part)
            if not parts:
                print("FAIL: no proposal obtained even per-page")
                return 1
            proposal = bind.merge_proposals(parts)
        body, problems = bind.validate(proposal, cal)
        artifact = bind.make_artifact(form_id, cal, body)

    history: list[dict[str, Any]] = []
    prev_findings_key: str | None = None
    rounds_used = 0
    converged = False

    for round_no in range(1, MAX_ROUNDS + 1):
        rounds_used = round_no
        round_pdf = render_dir / f"round-{round_no}.pdf"
        result = fill_pdf(artifact, estate, form.path, round_pdf)
        pngs = render_pages(round_pdf, render_dir, f"round-{round_no}", dpi=150)

        det_findings = [
            {"target": "exclusiveGroups", "problem": p, "mustFix": True}
            for p in result.group_violations
        ] + [{"target": "validation", "problem": p, "mustFix": True} for p in problems]

        model_findings = _critique(
            form_id, round_no, pngs, cal, result, _estate_summary(estate), progress
        )
        if model_findings is None:
            print("FAIL: critique unavailable; cannot verify the round — stopping")
            break
        findings = det_findings + model_findings

        history.append(
            {
                "round": round_no,
                "renders": [rel(p) for p in pngs],
                "deterministicFindings": det_findings,
                "modelFindings": model_findings,
                "fieldsFilled": sum(1 for f in result.fields if f.filled),
                "fieldsEmpty": len(result.empty),
                "diffFromPreviousRound": None,
            }
        )
        print(
            f"round {round_no}: {len(findings)} finding(s) "
            f"({len(det_findings)} deterministic, {len(model_findings)} from the image), "
            f"{history[-1]['fieldsFilled']} filled",
            flush=True,
        )

        if not findings:
            converged = True
            break

        findings_key = json.dumps(sorted(f["problem"] for f in findings))
        if findings_key == prev_findings_key:
            print("STUCK: two consecutive rounds with identical findings — reporting, not spinning")
            break
        prev_findings_key = findings_key

        if round_no == MAX_ROUNDS:
            break

        revised = _repair(form_id, round_no, artifact, findings, estate_json, progress)
        if revised is None:
            print("FAIL: repair unavailable — stopping with findings outstanding")
            break
        new_body, problems = bind.validate(revised, cal)
        history[-1]["diffFromPreviousRound"] = _diff(artifact, new_body)
        artifact = bind.make_artifact(form_id, cal, new_body)

    draft_path = bind.write_draft(artifact)
    report = {
        "formId": form_id,
        "estateId": estate_id,
        "converged": converged,
        "rounds": rounds_used,
        "elapsedSeconds": round(time.monotonic() - t0, 1),
        "modelCalls": llm.client.count,
        "draft": draft_path,
        "history": history,
    }
    report_path = REPORTS_DIR / (
        f"{form_id}-loop.{estate_id}.json" if from_draft else f"{form_id}-loop.json"
    )
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {draft_path}")
    print(f"wrote {rel(report_path)}")
    if converged:
        print(f"converged in {rounds_used} round(s)")
        if rounds_used == 1:
            print(
                "NOTE: convergence in a single round means the loop never visibly "
                "corrected anything — it demonstrates nothing about self-repair. "
                "Treat the binding as plausible, not proven; the review step is "
                "doing all the work."
            )
        print("PASS")
        return 0
    print(f"did not converge after {rounds_used} round(s)")
    print("FAIL")
    return 1
