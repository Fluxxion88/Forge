"""Deterministic binding resolution and fill. No model, ever — CLAUDE.md hard rule 1.

This module is the runtime. The convergence loop (build time) and `forge fill`
(request time) both execute exactly this code against a binding artifact; the only
difference is that `forge fill` runs inside `forbid_model_calls()`.

Semantics, per docs/02-SPEC.md §2.1:
  - five source kinds: path, constant, template, condition, absent
  - an optional `when: {path, equals}` guard on any binding; a failed guard leaves
    the field empty and records guarded-off
  - `exclusiveGroups` with rule exactlyOne | atMostOne; violations are recorded and
    it is the caller's job to treat them as fatal (the loop fails the round, the
    review UI shouts)
  - unknown is not false: absent data leaves a field empty and reports the path
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from .estatepath import EstateData
from .fillwriter import render_pages, write_filled

ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

SOURCE_KINDS = {"path", "constant", "template", "condition", "absent"}


@dataclass
class ResolvedField:
    qualified_name: str
    item_number: str | None
    label: str | None
    format: str
    value: str | None = None  # text fields: what gets written
    checked: bool = False  # checkboxes: whether the on-value gets set
    on_value: str | None = None
    present: bool = False  # did the source data exist
    guarded_off: bool = False
    paths: list[str] = dc_field(default_factory=list)
    reason: str | None = None

    @property
    def filled(self) -> bool:
        return (self.value not in (None, "")) or self.checked

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualifiedName": self.qualified_name,
            "itemNumber": self.item_number,
            "label": self.label,
            "format": self.format,
            "value": self.value,
            "checked": self.checked,
            "onValue": self.on_value,
            "present": self.present,
            "guardedOff": self.guarded_off,
            "paths": self.paths,
            "reason": self.reason,
        }


@dataclass
class GroupResult:
    label: str
    rule: str
    members_set: list[str]
    enforced: bool

    @property
    def ok(self) -> bool:
        if not self.enforced:
            return True
        n = len(self.members_set)
        return n == 1 if self.rule == "exactlyOne" else n <= 1

    @property
    def problem(self) -> str | None:
        if self.ok:
            return None
        n = len(self.members_set)
        want = "exactly one" if self.rule == "exactlyOne" else "at most one"
        return (
            f"exclusive group {self.label!r} requires {want} box marked; "
            f"{n} are set: {self.members_set or '(none)'}"
        )


@dataclass
class FillResult:
    fields: list[ResolvedField]
    groups: list[GroupResult]

    @property
    def texts(self) -> dict[str, str]:
        return {
            f.qualified_name: f.value
            for f in self.fields
            if f.format != "checkbox" and f.value not in (None, "")
        }

    @property
    def buttons(self) -> dict[str, str]:
        return {
            f.qualified_name: f.on_value
            for f in self.fields
            if f.format == "checkbox" and f.checked and f.on_value
        }

    @property
    def group_violations(self) -> list[str]:
        return [g.problem for g in self.groups if not g.ok]

    @property
    def empty(self) -> list[ResolvedField]:
        return [f for f in self.fields if not f.filled]


def _format_value(raw: Any, fmt: str) -> str:
    s = str(raw)
    if fmt == "date":
        m = ISO_DATE.fullmatch(s.strip())
        if m:
            y, mo, d = m.groups()
            return f"{mo}/{d}/{y}"
    return s


def _guard_passes(when: dict[str, Any] | None, estate: EstateData) -> bool:
    if not when:
        return True
    r = estate.resolve(when["path"])
    return r.present and r.value == when["equals"]


def resolve_one(binding: dict[str, Any], estate: EstateData) -> ResolvedField:
    src = binding["source"]
    kind = src["kind"]
    fmt = binding.get("format") or "text"
    out = ResolvedField(
        qualified_name=binding["qualifiedName"],
        item_number=binding.get("itemNumber"),
        label=binding.get("label"),
        format=fmt,
        on_value=binding.get("onValue"),
    )

    if not _guard_passes(binding.get("when"), estate):
        out.guarded_off = True
        w = binding.get("when") or {}
        out.reason = f"guard failed: {w.get('path')} != {w.get('equals')!r}"
        return out

    if kind == "constant":
        out.present = True
        out.value = _format_value(src["value"], fmt)
        if fmt == "checkbox":
            out.checked = bool(src["value"])
        return out

    if kind == "path":
        r = estate.resolve(src["path"])
        out.paths = [src["path"]]
        out.present = r.present and r.value not in (None, "")
        if out.present:
            if fmt == "checkbox":
                out.checked = bool(r.value)
            else:
                out.value = _format_value(r.value, fmt)
        else:
            out.reason = r.reason or f"{src['path']} is empty"
        return out

    if kind == "template":
        parts = []
        missing = []
        for p in src["paths"]:
            r = estate.resolve(p)
            if r.present and r.value not in (None, ""):
                parts.append(str(r.value))
            else:
                parts.append("")
                missing.append(p)
        out.paths = list(src["paths"])
        rendered = src["pattern"].format(*parts)
        rendered = re.sub(r"\s{2,}", " ", rendered).strip(" ,;/-")
        out.present = any(p != "" for p in parts)
        out.value = rendered if out.present else None
        if missing:
            out.reason = f"missing: {', '.join(missing)}"
        return out

    if kind == "condition":
        r = estate.resolve(src["path"])
        out.paths = [src["path"]]
        out.present = r.present
        out.checked = r.present and r.value == src["equals"]
        if not r.present:
            out.reason = r.reason
        return out

    if kind == "absent":
        r = estate.resolve(src["path"])
        out.paths = [src["path"]]
        out.present = True  # the *answer* (present/absent) always exists
        out.checked = (not r.present) or r.value is None
        return out

    raise ValueError(f"unknown source kind {kind!r} on {binding['qualifiedName']}")


def check_groups(
    groups: list[dict[str, Any]], resolved: list[ResolvedField], estate: EstateData
) -> list[GroupResult]:
    by_name = {f.qualified_name: f for f in resolved}
    results = []
    for g in groups or []:
        members_set = [
            q for q in g["members"] if (f := by_name.get(q)) is not None and f.checked
        ]
        results.append(
            GroupResult(
                label=g.get("label") or "unnamed group",
                rule=g.get("rule") or "exactlyOne",
                members_set=members_set,
                enforced=_guard_passes(g.get("when"), estate),
            )
        )
    return results


def resolve_all(binding_artifact: dict[str, Any], estate: EstateData) -> FillResult:
    resolved = [resolve_one(b, estate) for b in binding_artifact["bindings"]]
    groups = check_groups(binding_artifact.get("exclusiveGroups") or [], resolved, estate)
    return FillResult(fields=resolved, groups=groups)


def fill_pdf(
    binding_artifact: dict[str, Any],
    estate: EstateData,
    form_path: str | Path,
    out_pdf: str | Path,
) -> FillResult:
    """Resolve and write. Raises if a bound field cannot be located in the PDF —
    a binding that names a ghost field must never fill silently."""
    result = resolve_all(binding_artifact, estate)
    missing = write_filled(form_path, out_pdf, texts=result.texts, buttons=result.buttons)
    if missing:
        raise RuntimeError(f"binding names fields the PDF does not have: {missing}")
    return result


# --------------------------------------------------------------------------- CLI
# The request-time entry point. Everything below runs inside forbid_model_calls().


def _fill_anvil(binding: dict[str, Any], estate: EstateData, out_pdf: Path) -> "FillResult":
    """Sponsor path: same binding, same resolution, Anvil executes. Reconciliation
    runs first and refuses on drift — see docs/03-ANVIL.md."""
    from . import anvil

    cast_eid = binding.get("anvilCastEid")
    if not cast_eid:
        raise RuntimeError(
            f"binding for {binding['formId']} has no anvilCastEid; "
            f"run: forge anvil-register {binding['formId']}"
        )
    transport = anvil.HttpTransport()
    cast = transport.graphql(anvil.CAST_QUERY, {"eid": cast_eid})["cast"]
    result = resolve_all(binding, estate)
    values: dict[str, Any] = {}
    for f in result.fields:
        if f.format == "checkbox":
            if f.checked:
                values[f.qualified_name] = True
        elif f.value not in (None, ""):
            values[f.qualified_name] = f.value
    anvil.fill_via_anvil(binding, values, cast, transport, out_pdf)
    return result


def load_approved(form_id: str) -> tuple[dict[str, Any], Path]:
    """Highest approved version, or a clear refusal. Never falls back to a draft."""
    from .registry import APPROVED_DIR

    candidates = sorted(
        APPROVED_DIR.glob(f"{form_id}.v*.json"),
        key=lambda p: int(re.search(r"\.v(\d+)\.json$", p.name).group(1)),
    )
    if not candidates:
        raise FileNotFoundError(
            f"form {form_id} has not been compiled: no approved binding under "
            f"artifacts/approved/. Run the loop and approve it in forge review."
        )
    best = candidates[-1]
    return json.loads(best.read_text(encoding="utf-8")), best


def run_fill(form_id: str, estate_id: str, via: str = "local") -> int:
    from . import llm
    from .registry import FILLS_DIR, RENDERS_DIR, get_form, load_work_order, rel, sha256_of

    t0 = time.monotonic()
    order = load_work_order(estate_id)
    entry = next((f for f in order["forms"] if f["formId"] == form_id), None)
    if entry is None:
        print(f"work order for {estate_id} does not mention {form_id}")
        return 1
    if not entry["applicable"]:
        print(f"{form_id} is not applicable to {estate_id}: {entry['reason']}")
        return 0  # a correct refusal, not a failure — and no file is produced

    form = get_form(form_id)
    binding, binding_path = load_approved(form_id)
    if binding["sourceFormSha256"] != sha256_of(form.path):
        print(f"FAIL: {rel(binding_path)} was compiled against a different PDF")
        return 1

    calls_before = llm.client.count
    with llm.forbid_model_calls():
        estate = EstateData.load(Path(order["estatePath"]))
        suffix = "-anvil" if via == "anvil" else ""
        out_pdf = FILLS_DIR / f"{estate_id}-{form_id}{suffix}.pdf"
        FILLS_DIR.mkdir(parents=True, exist_ok=True)
        if via == "anvil":
            result = _fill_anvil(binding, estate, out_pdf)
        else:
            result = fill_pdf(binding, estate, form.path, out_pdf)

    # rasterise so the output is inspectable (outside the timed no-model block is
    # fine, but poppler is not a model — keep it inside the honesty boundary anyway)
    render_dir = RENDERS_DIR / "fills"
    pngs = render_pages(out_pdf, render_dir, f"{estate_id}-{form_id}", dpi=150)

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    sidecar = {
        "estateId": estate_id,
        "formId": form_id,
        "via": via,
        "bindingVersion": binding["version"],
        "bindingPath": rel(binding_path),
        "fieldsTotal": len(binding["bindings"]) + len(binding["unbound"]),
        "fieldsFilled": sum(1 for f in result.fields if f.filled),
        "fieldsEmpty": len(result.empty) + len(binding["unbound"]),
        "empty": [
            {
                "itemNumber": f.item_number,
                "label": f.label,
                "whatWouldFillIt": f.reason,
                "guardedOff": f.guarded_off,
            }
            for f in result.empty
        ]
        + [
            {
                "itemNumber": None,
                "label": u.get("label"),
                "whatWouldFillIt": u.get("whatWouldFillIt"),
                "guardedOff": False,
            }
            for u in binding["unbound"]
        ],
        "groupViolations": result.group_violations,
        "llmCallsAtRuntime": llm.client.count - calls_before,  # measured, not asserted
        "elapsedMs": elapsed_ms,
        "renders": [rel(p) for p in pngs],
    }
    sidecar_path = FILLS_DIR / f"{estate_id}-{form_id}.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {rel(out_pdf)}")
    print(f"wrote {rel(sidecar_path)}")
    print(
        f"{sidecar['fieldsFilled']} filled, {sidecar['fieldsEmpty']} empty, "
        f"llmCallsAtRuntime={sidecar['llmCallsAtRuntime']}, {elapsed_ms}ms"
    )
    if result.group_violations:
        for v in result.group_violations:
            print(f"GROUP VIOLATION: {v}")
        return 1
    return 0
