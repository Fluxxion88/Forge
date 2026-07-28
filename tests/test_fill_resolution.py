"""The deterministic fill semantics: five kinds, when-guards, exclusive groups."""

from __future__ import annotations

import pytest

from forge.estatepath import EstateData
from forge.fill import check_groups, resolve_all, resolve_one

ESTATE = {
    "decedent": {"name": {"full": "Walter Dean Prescott"}, "dateOfDeath": "2026-01-23"},
    "authority": {"basis": "CourtAppointmentTestate", "dateOfAppointment": "2026-07-28"},
    "estateEntity": {"ein": None},
    "addr": {"city": "Indianapolis", "state": "IN", "zip": None},
}


def _estate():
    return EstateData(dict(ESTATE), "test-estate")


def _b(qname="f1", **kw):
    base = {"qualifiedName": qname, "itemNumber": None, "label": None, "format": "text"}
    base.update(kw)
    return base


def test_path_kind_and_date_format():
    r = resolve_one(_b(source={"kind": "path", "path": "decedent.dateOfDeath"}, format="date"), _estate())
    assert r.value == "01/23/2026" and r.present


def test_absent_path_leaves_empty_with_reason():
    r = resolve_one(_b(source={"kind": "path", "path": "decedent.name.first"}), _estate())
    assert not r.filled and r.value is None and r.reason


def test_null_is_absent_not_false():
    r = resolve_one(_b(source={"kind": "path", "path": "estateEntity.ein"}), _estate())
    assert not r.present and not r.filled


def test_template_skips_missing_parts():
    r = resolve_one(
        _b(source={"kind": "template", "pattern": "{0}, {1} {2}", "paths": ["addr.city", "addr.state", "addr.zip"]}),
        _estate(),
    )
    assert r.value == "Indianapolis, IN"
    assert "addr.zip" in r.reason


def test_condition_checkbox():
    e = _estate()
    yes = resolve_one(
        _b("c1", source={"kind": "condition", "path": "authority.basis", "equals": "CourtAppointmentTestate"},
           format="checkbox", onValue="/1"), e)
    no = resolve_one(
        _b("c2", source={"kind": "condition", "path": "authority.basis", "equals": "TrustInstrument"},
           format="checkbox", onValue="/5"), e)
    assert yes.checked and not no.checked


def test_absent_kind_marks_when_null_or_missing():
    e = _estate()
    assert resolve_one(_b("c3", source={"kind": "absent", "path": "estateEntity.ein"},
                          format="checkbox", onValue="/1"), e).checked
    assert not resolve_one(_b("c4", source={"kind": "absent", "path": "decedent.name.full"},
                              format="checkbox", onValue="/1"), e).checked


def test_when_guard_gates_any_kind():
    e = _estate()
    on_branch = resolve_one(
        _b(source={"kind": "path", "path": "decedent.dateOfDeath"}, format="date",
           when={"path": "authority.basis", "equals": "CourtAppointmentTestate"}), e)
    off_branch = resolve_one(
        _b(source={"kind": "path", "path": "authority.dateOfAppointment"}, format="date",
           when={"path": "authority.basis", "equals": "TrustInstrument"}), e)
    assert on_branch.value == "01/23/2026"
    assert off_branch.guarded_off and not off_branch.filled and "guard failed" in off_branch.reason


def test_exclusive_group_exactly_one():
    e = _estate()
    art = {
        "bindings": [
            _b("g[0]", source={"kind": "condition", "path": "authority.basis", "equals": "CourtAppointmentTestate"},
               format="checkbox", onValue="/1"),
            _b("g[1]", source={"kind": "condition", "path": "authority.basis", "equals": "TrustInstrument"},
               format="checkbox", onValue="/2"),
        ],
        "exclusiveGroups": [
            {"label": "authority", "rule": "exactlyOne", "members": ["g[0]", "g[1]"], "when": None}
        ],
    }
    result = resolve_all(art, e)
    assert result.group_violations == []

    # break it: both members now match
    art["bindings"][1]["source"] = {"kind": "condition", "path": "authority.basis",
                                    "equals": "CourtAppointmentTestate"}
    violations = resolve_all(art, e).group_violations
    assert len(violations) == 1 and "exactly one" in violations[0]

    # break it the other way: none match
    for b in art["bindings"]:
        b["source"]["equals"] = "Nope"
    assert len(resolve_all(art, e).group_violations) == 1


def test_at_most_one_allows_zero():
    e = _estate()
    resolved = [
        resolve_one(_b("g[0]", source={"kind": "condition", "path": "authority.basis", "equals": "X"},
                       format="checkbox", onValue="/1"), e)
    ]
    groups = check_groups(
        [{"label": "optional", "rule": "atMostOne", "members": ["g[0]"], "when": None}], resolved, e
    )
    assert groups[0].ok


def test_group_when_guard_scopes_enforcement():
    e = _estate()
    resolved = []  # nothing checked at all
    enforced = check_groups(
        [{"label": "g", "rule": "exactlyOne", "members": ["a", "b"],
          "when": {"path": "authority.basis", "equals": "CourtAppointmentTestate"}}], resolved, e)
    lifted = check_groups(
        [{"label": "g", "rule": "exactlyOne", "members": ["a", "b"],
          "when": {"path": "authority.basis", "equals": "TrustInstrument"}}], resolved, e)
    assert not enforced[0].ok
    assert lifted[0].ok


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        resolve_one(_b(source={"kind": "lambda", "code": "x"}), _estate())
