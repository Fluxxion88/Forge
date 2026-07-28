"""`forge inspect` — enumerate fields and check them against the domain fixtures.

The expected counts come from docs/00-DOMAIN.md §2, which was produced by opening the
actual files. If the code disagrees with them, the code is wrong.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .pdfmeta import FormInfo, read_form
from .registry import FORMS, Form, get_form, rel, sha256_of

# form_id -> (total, text, button, tooltips). docs/00-DOMAIN.md §2.
EXPECTED: dict[str, tuple[int, int, int, int]] = {
    "irs-ss4": (93, 45, 44, 0),
    "irs-f56": (76, 39, 33, 0),
    "irs-f8821": (52, 33, 12, 5),
    "ca-dmv-dl142": (51, 14, 14, 28),
}


def summary_line(form_id: str, info: FormInfo) -> str:
    c = info.counts
    return (
        f"{form_id:<15}{c['total']:>2} fields   {c['text']:>2} text   "
        f"{c['button']:>2} button   {c['tooltips']:>2} tooltips"
    )


def _check_counts(form_id: str, info: FormInfo) -> list[str]:
    if form_id not in EXPECTED:
        return []
    total, text, button, tooltips = EXPECTED[form_id]
    c = info.counts
    problems = []
    for label, want, got in (
        ("total", total, c["total"]),
        ("text", text, c["text"]),
        ("button", button, c["button"]),
        ("tooltips", tooltips, c["tooltips"]),
    ):
        if want != got:
            problems.append(f"{form_id}: expected {want} {label}, enumerated {got}")
    return problems


def _check_f56_on_values(info: FormInfo) -> list[str]:
    """docs/04-BUILD-PLAN.md phase 0: at least one /2, and never /Yes."""
    buttons = info.of_type("button")
    on_values = [b.on_value for b in buttons]
    problems = []
    if "/2" not in on_values:
        problems.append("irs-f56: no button reports an onValue of /2")
    yes = [b.qualified_name for b in buttons if b.on_value == "/Yes"]
    if yes:
        problems.append(f"irs-f56: {len(yes)} button(s) report an onValue of /Yes: {yes[:3]}")
    missing = [b.qualified_name for b in buttons if b.on_value is None]
    if missing:
        problems.append(
            f"irs-f56: {len(missing)} button(s) have no discoverable onValue: {missing[:3]}"
        )
    return problems


def field_dump(form: Form) -> dict[str, Any]:
    info = read_form(form.path)
    return {
        "formId": form.form_id,
        "sourceFile": rel(form.path),
        "sourceSha256": sha256_of(form.path),
        "pageCount": info.page_count,
        "hasXfa": info.has_xfa,
        "needAppearances": info.need_appearances,
        "counts": info.counts,
        "fields": [f.to_dict() for f in info.fields],
    }


def inspect_one(form_id: str, as_json: bool = False) -> int:
    form = get_form(form_id)
    info = read_form(form.path)
    if as_json:
        print(json.dumps(field_dump(form), indent=2))
        return 0

    c = info.counts
    print(f"{form.form_id}  —  {rel(form.path)}")
    print(f"  sha256          {sha256_of(form.path)}")
    print(f"  pages           {info.page_count}")
    print(f"  XFA hybrid      {info.has_xfa}")
    print(f"  NeedAppearances {info.need_appearances}")
    print(
        f"  fields          {c['total']} total   {c['text']} text   {c['button']} button"
        f"   {c['container']} container (no /FT)   {c['tooltips']} with tooltip"
    )
    buttons = info.of_type("button")
    if buttons:
        distinct = sorted({b.on_value or "<none>" for b in buttons})
        print(f"  button onValues {', '.join(distinct)}")
        unresolved = [b.qualified_name for b in buttons if b.on_value is None]
        if unresolved:
            print(f"  UNRESOLVED onValue on {len(unresolved)} button(s):")
            for q in unresolved:
                print(f"    {q}")

    problems = _check_counts(form.form_id, info)
    if form.form_id == "irs-f56":
        problems += _check_f56_on_values(info)
    for p in problems:
        print(f"  MISMATCH {p}", file=sys.stderr)
    print("PASS" if not problems else "FAIL")
    return 0 if not problems else 1


def inspect_all() -> int:
    problems: list[str] = []
    for form in FORMS:
        if not form.path.exists():
            problems.append(f"{form.form_id}: missing input {rel(form.path)}")
            print(f"{form.form_id:<15}MISSING {rel(form.path)}")
            continue
        info = read_form(form.path)
        print(summary_line(form.form_id, info))
        problems += _check_counts(form.form_id, info)
        if form.form_id == "irs-f56":
            problems += _check_f56_on_values(info)

    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("FAIL")
        return 1
    print("PASS")
    return 0
