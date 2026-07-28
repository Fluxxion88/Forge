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

The six source kinds — NOTHING else is executable:
  {"kind": "path", "path": "<estate JSON path>"}                      direct value
  {"kind": "constant", "value": <literal>}                            never varies
  {"kind": "template", "pattern": "{0}, {1}", "paths": [<p0>, <p1>]}  joined values
  {"kind": "condition", "path": <p>, "equals": <literal>}             checkbox: mark iff equal
  {"kind": "contains", "path": <p>, "includes": <literal>}            checkbox: mark iff the
                                                                      ARRAY at <p> contains it
  {"kind": "absent", "path": <p>}                                     checkbox: mark iff absent/null

CHOOSE BETWEEN condition AND contains BY THE SHAPE OF THE DATA, and look it up in the
estate record below — do not guess. If the path holds an array (a "check all that
apply" group: taxMatters.taxTypes, taxMatters.federalFormNumbers), you MUST use
`contains`; `condition` compares with == and is never true against a list, which
silently produces a checkbox that can never be marked on any estate. If the path
holds a scalar, use `condition`. Likewise a `when` guard only ever compares scalars.

The literal in `equals`/`includes` must be copied from a value you can SEE in the
estate record. If the option has no corresponding value anywhere in the record (a
tax type this estate does not have), you cannot know the enum spelling — bind it
anyway with your best reading of the convention, set "confidence": "low", and say in
"note" that the literal is unverified. Do not silently present a guessed enum
spelling as high confidence.

Optional guard on ANY binding, either shape:
  "when": {"path": <p>, "equals": <literal>}          applies iff the path equals it
  "when": {"path": <p>, "equalsAny": [<l1>, <l2>, …]}  applies iff the path is one of them
Use it for conditional value placement. Form 56 line 2a takes the date of death on the
1a/1b/1d branch and line 2b the date of appointment on the 1c/1e/1f/1g branch — those
are THREE and FOUR authority values respectively, so they need equalsAny, not equals.
A single-literal guard there silently blanks the date on every other branch.

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


# The binding language as it stood before tonight's lessons were folded into it: five
# kinds, single-literal guards, no warning about array-shaped paths or unverified enum
# spellings. Used only by `forge bind --naive`, so the convergence loop has genuine
# mistakes to find. Feeding the loop the corrected prompt and then showing it "converge"
# in one round would be theatre.
NAIVE_BINDING_LANGUAGE = """Each binding object:
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
applies only when the guard path resolves to exactly that value.

"format": "date" reformats ISO dates (2026-01-23) to MM/DD/YYYY. Checkbox bindings use
"format": "checkbox".

Rules:
- Prefer generic estate paths (decedent.*, fiduciary.*, authority.*, estateEntity.*) over
  the per-form block where both hold the same value.
- UNKNOWN IS NOT FALSE. A field with no supporting estate data goes in "unbound":
  {"qualifiedName": ..., "label": ..., "reason": ..., "whatWouldFillIt": ...}.
- Mutually exclusive checkbox sets go in "exclusiveGroups":
  {"label": ..., "rule": "exactlyOne"|"atMostOne", "members": [qualifiedNames], "when": null}
- Do not bind pushbuttons (Print/Clear)."""

NAIVE_LANGUAGE_NOTE = """
(This proposal is deliberately run WITHOUT the accumulated guidance about array-shaped
paths, enum spellings and multi-value branches. The convergence loop exists to catch
what a first pass gets wrong; feeding it a pre-corrected proposal would prove nothing.)
"""


def propose_prompt(
    form_id: str, cal: dict[str, Any], estate_json: str, estate_id: str, naive: bool = False
) -> str:
    language = NAIVE_BINDING_LANGUAGE + NAIVE_LANGUAGE_NOTE if naive else BINDING_LANGUAGE
    return f"""You are compiling a reusable form-filler for government form {form_id}
({cal['sourceFile']}). Map estate-data JSON paths onto the form's fields.

The calibrated fields (name, type, page, printed item number, caption, meaning):
{calibration_digest(cal)}

The estate record this binding will first be tested against ({estate_id}) — bind to its
PATHS, not its values; the same binding must work for every future estate:
{estate_json}

{language}

Answer with ONLY a JSON object:
{{"bindings": [...], "unbound": [...], "exclusiveGroups": [...]}}
Your ENTIRE reply must be that JSON object and nothing else — no prose, no
explanation. Do not attempt to read or edit any file; a non-JSON reply is
discarded unread."""


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
        if kind not in {"path", "constant", "template", "condition", "contains", "absent"}:
            problems.append(f"{q}: unknown source kind {kind!r}, dropped")
            continue
        if kind == "contains" and "includes" not in src:
            problems.append(f"{q}: contains without an 'includes' literal, dropped")
            continue
        fmt = b.get("format") or ("checkbox" if f["type"] == "button" else "text")
        if f["type"] == "button":
            if kind not in {"condition", "contains", "absent", "constant"}:
                problems.append(f"{q}: checkbox bound with kind {kind!r}, dropped")
                continue
            fmt = "checkbox"
            b["onValue"] = f["onValue"]  # calibration is the only authority here
        elif fmt == "checkbox":
            problems.append(f"{q}: text field bound as checkbox, coerced to text")
            fmt = "text"
        when = b.get("when")
        if when is not None and (
            not isinstance(when, dict)
            or "path" not in when
            or not ("equals" in when or "equalsAny" in when)
        ):
            problems.append(f"{q}: malformed when-guard {when!r}, guard dropped")
            when = None
        if when is not None and "equalsAny" in when and not isinstance(when["equalsAny"], list):
            problems.append(f"{q}: when-guard equalsAny must be a list, guard dropped")
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


def propose(
    form_id: str, cal: dict[str, Any], estate_json: str, estate_id: str, naive: bool = False
) -> dict[str, Any]:
    reply = llm.client.call(
        purpose=f"bind:{form_id}:propose",
        prompt=propose_prompt(form_id, cal, estate_json, estate_id, naive=naive),
        timeout=420,
    )
    return llm.extract_json_object(reply)


def propose_one_page(
    form_id: str, cal: dict[str, Any], page: int, estate_json: str, estate_id: str,
    naive: bool = False,
) -> dict[str, Any]:
    """Timeout fallback: propose for a single page's fields. docs/05 §2: when a call
    times out, halve the batch."""
    page_cal = dict(cal, fields=[f for f in cal["fields"] if f["page"] == page])
    prompt = propose_prompt(form_id, page_cal, estate_json, estate_id, naive=naive) + (
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


def unbind_dead_bindings(artifact: dict[str, Any], estate: Any) -> list[str]:
    """Move provably non-functional bindings to `unbound`. Deterministic, no model.

    Two ways to get the shape wrong, and both fail silently as an empty box:
      - `condition` (or a `when` guard) against an ARRAY path: `==` is never true
        against a list, so the box can never be marked. This is what happened to all
        15 of Form 56 items 3 and 4 on the first f56 binding.
      - `contains` against a SCALAR path: there is no array to search.
    Either way the checkbox is provably unmarkable on every estate, and it renders
    exactly like a box whose data is missing. CLAUDE.md rule 5: ambiguity is an
    output. So the binding goes to `unbound`, naming the shape mismatch, and the
    reviewer sees it at the top of the list instead of on a filed form.
    """
    moved: list[str] = []
    kept: list[dict[str, Any]] = []
    for b in artifact["bindings"]:
        dead: list[tuple[str, str, Any, Any, str]] = []
        src = b["source"]
        if src["kind"] == "condition":
            r = estate.resolve(src["path"])
            if r.present and isinstance(r.value, list):
                dead.append(("source", src["path"], r.value, src.get("equals"), "list"))
        if src["kind"] == "contains":
            r = estate.resolve(src["path"])
            if r.present and not isinstance(r.value, list):
                dead.append(("source", src["path"], r.value, src.get("includes"), "scalar"))
        guard = b.get("when")
        if guard:
            r = estate.resolve(guard["path"])
            if r.present and isinstance(r.value, list):
                wanted = guard["equalsAny"] if "equalsAny" in guard else guard.get("equals")
                dead.append(("when-guard", guard["path"], r.value, wanted, "list"))
        if not dead:
            kept.append(b)
            continue
        where, path, value, literal, shape = dead[0]
        if shape == "list":
            reason = (
                f"{where} compares {path} to the literal {literal!r} with equality, but "
                f"that path holds a list ({value!r}). Equality against a list is never "
                f"true, so this box could never be marked on any estate."
            )
            fix = (
                f"Rebind with the `contains` kind: "
                f'{{"kind": "contains", "path": "{path}", "includes": {literal!r}}}.'
            )
        else:
            reason = (
                f"{where} uses `contains` on {path}, but that path holds "
                f"{type(value).__name__} ({value!r}), not a list. There is no array to "
                f"search, so this box could never be marked."
            )
            fix = (
                f"Rebind with the `condition` kind: "
                f'{{"kind": "condition", "path": "{path}", "equals": {literal!r}}}.'
            )
        artifact["unbound"].append(
            {
                "qualifiedName": b["qualifiedName"],
                "label": b.get("label"),
                "reason": reason,
                "whatWouldFillIt": fix,
            }
        )
        moved.append(f"{b.get('itemNumber')} {b['qualifiedName']} ({shape})")
    artifact["bindings"] = kept
    return moved


# --------------------------------------------------------------- one-pass propose
# `forge propose` is the loop's step 1 on its own: propose, validate, write the
# draft, then fill and rasterise ONCE so a human has something to look at. No
# critique, no repair, no rounds. `forge fill` stays approved-only (docs/02 §4) —
# the build-time fill here goes through the same fill_pdf() the runtime uses.


def run_propose(
    form_id: str,
    estate_id: str,
    whole_form_first: bool = False,
    from_draft: bool = False,
) -> int:
    import time

    from .calibrate import _Progress
    from .estatepath import EstateData
    from .fill import fill_pdf
    from .fillwriter import render_pages
    from .registry import RENDERS_DIR, estate_path, get_form

    form = get_form(form_id)
    cal = load_calibration(form_id)
    estate = EstateData.load(estate_path(estate_id))
    estate_json = json.dumps(estate.data, indent=1)
    progress = _Progress()
    t0 = time.monotonic()

    if from_draft:
        # No model. Re-validate, re-fill and re-render the existing draft — this is
        # what you run after editing a row in the review UI, when the table has moved
        # on but the picture has not.
        draft_file = BINDINGS_DIR / f"{form_id}.json"
        if not draft_file.exists():
            print(f"FAIL: --from-draft but no draft at {rel(draft_file)}")
            return 1
        existing = json.loads(draft_file.read_text(encoding="utf-8"))
        proposal = {k: existing.get(k) or [] for k in ("bindings", "unbound", "exclusiveGroups")}
        print(f"[from-draft] re-validating {rel(draft_file)}; no model call", flush=True)
        return _finish_propose(
            form_id, form, cal, estate, estate_id, proposal, t0, RENDERS_DIR,
            fill_pdf, render_pages,
        )

    proposal = None
    if whole_form_first:
        print(f"[propose] whole-form binding for {form_id} against {estate_id}", flush=True)
        proposal = progress.call_with_retry(
            "propose", len(cal["fields"]),
            lambda: propose(form_id, cal, estate_json, estate_id),
            attempts=1,
        )
    if proposal is None:
        # docs/05 §2 "halve the batch". For irs-f56 the whole-form call is a KNOWN
        # 420s timeout (out/reports/calls/00{1,2}-bind-irs-f56-propose.error.txt),
        # so per-page is the default entry point, not a fallback.
        pages = sorted({f["page"] for f in cal["fields"] if f["page"] is not None})
        parts = []
        for page in pages:
            n = sum(1 for f in cal["fields"] if f["page"] == page)
            part = progress.call_with_retry(
                f"propose page {page}", n,
                lambda page=page: propose_one_page(form_id, cal, page, estate_json, estate_id),
            )
            if part:
                parts.append(part)
        if not parts:
            print("FAIL: no proposal obtained")
            return 1
        proposal = merge_proposals(parts)

    return _finish_propose(
        form_id, form, cal, estate, estate_id, proposal, t0, RENDERS_DIR,
        fill_pdf, render_pages,
    )


def _finish_propose(
    form_id, form, cal, estate, estate_id, proposal, t0, RENDERS_DIR, fill_pdf, render_pages
) -> int:
    import time

    body, problems = validate(proposal, cal)
    artifact = make_artifact(form_id, cal, body)
    for p in problems:
        print(f"  validation: {p}", flush=True)
    dead = unbind_dead_bindings(artifact, estate)
    for d in dead:
        print(f"  DEAD BINDING unbound (list path, scalar equality): {d}", flush=True)
    draft_path = write_draft(artifact)

    render_dir = RENDERS_DIR / form_id
    out_pdf = render_dir / "draft.pdf"
    result = fill_pdf(artifact, estate, form.path, out_pdf)
    pngs = render_pages(out_pdf, render_dir, "draft", dpi=150)

    filled = sum(1 for f in result.fields if f.filled)
    print(f"wrote {draft_path}")
    print(f"wrote {rel(out_pdf)}")
    for p in pngs:
        print(f"wrote {rel(p)}")
    print(
        f"{len(artifact['bindings'])} bound, {len(artifact['unbound'])} unbound, "
        f"{filled} filled for {estate_id}, "
        f"{sum(1 for f in result.fields if f.guarded_off)} guarded-off, "
        f"{round(time.monotonic() - t0, 1)}s, {llm.client.count} model call(s)"
    )
    for v in result.group_violations:
        print(f"GROUP VIOLATION: {v}")
    return 0
