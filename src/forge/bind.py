"""Stage 2 — binding synthesis: propose a draft, validate it into shape.

The artifact is data, never code. The model proposes; this module enforces the
shape: only the five source kinds, only fields that exist in the calibration,
checkbox on-values ALWAYS taken from calibration and never from the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import llm
from .registry import BINDINGS_DIR, CALIBRATION_DIR, rel

BINDING_LANGUAGE = """Each binding object:
{
  "qualifiedName": "<must be a calibrated field's qualifiedName, verbatim>",
  "itemNumber": <copy from calibration>,
  "label": <copy from calibration>,
  "source": <one of the five kinds below>,
  "format": "text" | "date" | "checkbox",
  "required": true|false,
  "confidence": "high" | "medium" | "low",
  "note": <string or null>,
  "when": <optional guard, see below, or omit>
}

The five source kinds — NOTHING else is executable:
  {"kind": "path", "path": "<estate JSON path>"}                      direct value
  {"kind": "constant", "value": <literal>}                            never varies
  {"kind": "template", "pattern": "{0}, {1}", "paths": [<p0>, <p1>]}  joined values
  {"kind": "condition", "path": <p>, "equals": <literal>}             checkbox: mark iff equal
  {"kind": "absent", "path": <p>}                                     checkbox: mark iff absent/null

Optional guard on ANY binding: "when": {"path": <p>, "equals": <literal>} — the binding
applies only when the guard path resolves to exactly that value. Use it for conditional
value placement (e.g. Form 56 line 2a date-of-death applies only on the 1a/1b/1d branch;
line 2b date-of-appointment only on the 1c/1e/1f/1g branch).

"format": "date" reformats ISO dates (2026-01-23) to MM/DD/YYYY. Checkbox bindings use
"format": "checkbox".

Rules:
- Prefer generic estate paths (decedent.*, fiduciary.*, authority.*, estateEntity.*) over
  the per-form block where both hold the same value; use the form-specific block
  (form56.*) for answers only that form asks.
- UNKNOWN IS NOT FALSE. A field with no supporting estate data goes in "unbound":
  {"qualifiedName": ..., "label": ..., "reason": ..., "whatWouldFillIt": ...}.
  Never invent a value. Never bind a guess.
- Mutually exclusive checkbox sets go in "exclusiveGroups":
  {"label": ..., "rule": "exactlyOne"|"atMostOne", "members": [qualifiedNames], "when": null}
  Use exactlyOne when the form demands a choice; atMostOne inside sections that may not
  apply to a given estate.
- Do not bind pushbuttons (Print/Clear)."""


def calibration_digest(cal: dict[str, Any]) -> str:
    """Compact per-field lines the model can bind against."""
    lines = []
    for f in cal["fields"]:
        if f.get("isPushbutton"):
            continue
        bits = [
            f["qualifiedName"],
            f["type"],
            f"page={f['page']}",
            f"item={f['itemNumber']}",
            f"label={f['printedLabel']!r}",
            f"meaning={f['meaning']!r}",
        ]
        if f["type"] == "button":
            bits.append(f"onValue={f['onValue']}")
        if f.get("maxLen"):
            bits.append(f"maxLen={f['maxLen']}")
        lines.append("  ".join(str(b) for b in bits))
    return "\n".join(lines)


def load_calibration(form_id: str) -> dict[str, Any]:
    p = CALIBRATION_DIR / f"{form_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no calibration at {rel(p)}; run: forge calibrate {form_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def propose_prompt(form_id: str, cal: dict[str, Any], estate_json: str, estate_id: str) -> str:
    return f"""You are compiling a reusable form-filler for government form {form_id}
({cal['sourceFile']}). Map estate-data JSON paths onto the form's fields.

The calibrated fields (name, type, page, printed item number, caption, meaning):
{calibration_digest(cal)}

The estate record this binding will first be tested against ({estate_id}) — bind to its
PATHS, not its values; the same binding must work for every future estate:
{estate_json}

{BINDING_LANGUAGE}

Answer with ONLY a JSON object:
{{"bindings": [...], "unbound": [...], "exclusiveGroups": [...]}}"""


def validate(
    proposal: dict[str, Any], cal: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Force the proposal into shape. Returns (clean_artifact_body, problems).

    Problems are returned for the repair step, not raised — a partly-wrong draft is
    the loop's normal input.
    """
    by_name = {f["qualifiedName"]: f for f in cal["fields"]}
    problems: list[str] = []
    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for b in proposal.get("bindings") or []:
        q = b.get("qualifiedName")
        f = by_name.get(q)
        if f is None:
            problems.append(f"binding for unknown field {q!r} dropped")
            continue
        if q in seen:
            problems.append(f"duplicate binding for {q} dropped")
            continue
        src = b.get("source") or {}
        kind = src.get("kind")
        if kind not in {"path", "constant", "template", "condition", "absent"}:
            problems.append(f"{q}: unknown source kind {kind!r}, dropped")
            continue
        fmt = b.get("format") or ("checkbox" if f["type"] == "button" else "text")
        if f["type"] == "button":
            if kind not in {"condition", "absent", "constant"}:
                problems.append(f"{q}: checkbox bound with kind {kind!r}, dropped")
                continue
            fmt = "checkbox"
            b["onValue"] = f["onValue"]  # calibration is the only authority here
        elif fmt == "checkbox":
            problems.append(f"{q}: text field bound as checkbox, coerced to text")
            fmt = "text"
        when = b.get("when")
        if when is not None and (
            not isinstance(when, dict) or "path" not in when or "equals" not in when
        ):
            problems.append(f"{q}: malformed when-guard {when!r}, guard dropped")
            when = None
        bindings.append(
            {
                "qualifiedName": q,
                "itemNumber": b.get("itemNumber", f["itemNumber"]),
                "label": b.get("label", f["printedLabel"]),
                "source": src,
                "format": fmt,
                "onValue": f["onValue"] if f["type"] == "button" else None,
                "required": bool(b.get("required", False)),
                "confidence": b.get("confidence", "medium"),
                "note": b.get("note"),
                "when": when,
            }
        )
        seen.add(q)

    groups = []
    for g in proposal.get("exclusiveGroups") or []:
        members = [m for m in g.get("members") or [] if m in by_name]
        bad = [m for m in g.get("members") or [] if m not in by_name]
        if bad:
            problems.append(f"group {g.get('label')!r}: unknown members {bad} dropped")
        if len(members) < 2:
            problems.append(f"group {g.get('label')!r} has <2 valid members, dropped")
            continue
        rule = g.get("rule") if g.get("rule") in {"exactlyOne", "atMostOne"} else "exactlyOne"
        groups.append(
            {"label": g.get("label") or "unnamed", "rule": rule,
             "members": members, "when": g.get("when")}
        )

    unbound = []
    for u in proposal.get("unbound") or []:
        unbound.append(
            {
                "qualifiedName": u.get("qualifiedName"),
                "label": u.get("label"),
                "reason": u.get("reason") or "not stated",
                "whatWouldFillIt": u.get("whatWouldFillIt"),
            }
        )
    # every calibrated non-pushbutton field is accounted for: bound or unbound
    accounted = seen | {u["qualifiedName"] for u in unbound}
    for f in cal["fields"]:
        if f.get("isPushbutton") or f["qualifiedName"] in accounted:
            continue
        unbound.append(
            {
                "qualifiedName": f["qualifiedName"],
                "label": f["printedLabel"],
                "reason": "proposal did not mention this field",
                "whatWouldFillIt": None,
            }
        )
        problems.append(f"{f['qualifiedName']} unaccounted for; added to unbound")

    return {"bindings": bindings, "unbound": unbound, "exclusiveGroups": groups}, problems


def make_artifact(form_id: str, cal: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "formId": form_id,
        "version": 1,
        "status": "draft",
        "sourceFormSha256": cal["sourceSha256"],
        "calibrationRef": rel(CALIBRATION_DIR / f"{form_id}.json"),
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "approvedBy": None,
        "approvedAt": None,
        "anvilCastEid": None,
        "bindings": body["bindings"],
        "unbound": body["unbound"],
        "exclusiveGroups": body["exclusiveGroups"],
    }


def write_draft(artifact: dict[str, Any]) -> str:
    BINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    out = BINDINGS_DIR / f"{artifact['formId']}.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return rel(out)


def propose(form_id: str, cal: dict[str, Any], estate_json: str, estate_id: str) -> dict[str, Any]:
    reply = llm.client.call(
        purpose=f"bind:{form_id}:propose",
        prompt=propose_prompt(form_id, cal, estate_json, estate_id),
        timeout=420,
    )
    return llm.extract_json_object(reply)


def propose_one_page(
    form_id: str, cal: dict[str, Any], page: int, estate_json: str, estate_id: str
) -> dict[str, Any]:
    """Timeout fallback: propose for a single page's fields. docs/05 §2: when a call
    times out, halve the batch."""
    page_cal = dict(cal, fields=[f for f in cal["fields"] if f["page"] == page])
    prompt = propose_prompt(form_id, page_cal, estate_json, estate_id) + (
        f"\n\nNOTE: you are binding ONLY the fields on page {page} (listed above). "
        "Emit exclusiveGroups only for groups whose members all appear on this page."
    )
    reply = llm.client.call(
        purpose=f"bind:{form_id}:propose:page{page}", prompt=prompt, timeout=420
    )
    return llm.extract_json_object(reply)


def merge_proposals(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"bindings": [], "unbound": [], "exclusiveGroups": []}
    for p in parts:
        for key in merged:
            merged[key].extend(p.get(key) or [])
    return merged
