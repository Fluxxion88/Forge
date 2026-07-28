"""Anvil reconciliation logic against a stub transport — the parts that must be
right before a key exists. The silent-drop refusal is the $1,000 demo."""

from __future__ import annotations

import pytest

from forge.anvil import (
    MissingCredential,
    alias_map,
    cast_field_ids,
    fill_via_anvil,
    reconcile,
    require_key,
)

ARTIFACT = {
    "bindings": [
        {"qualifiedName": "a.0", "label": "Name", "source": {"kind": "path", "path": "decedent.name.full"}},
        {"qualifiedName": "b.0", "label": "City", "source": {"kind": "path", "path": "decedent.residenceAddress.city"}},
        {"qualifiedName": "c.0", "label": "DL box", "source": {"kind": "condition", "path": "x", "equals": "y"}},
    ],
    "unbound": [],
    "exclusiveGroups": [],
}


class StubTransport:
    def __init__(self):
        self.filled = None

    def graphql(self, query, variables):
        raise AssertionError("not used in these tests")

    def fill(self, cast_eid, payload):
        self.filled = (cast_eid, payload)
        return b"%PDF-1.7 stub bytes"


def _cast(ids):
    return {"eid": "cast123", "fieldInfo": {"fields": [{"aliasId": i} for i in ids]}}


def test_aliases_are_path_derived_and_unique():
    m = alias_map(ARTIFACT)
    assert m["a.0"] == "decedent_name_full"
    assert m["b.0"] == "decedent_residenceaddress_city"
    assert len(set(m.values())) == 3


def test_reconcile_reports_both_directions():
    m = alias_map(ARTIFACT)
    cast = _cast([m["a.0"], m["b.0"], "mystery_extra"])
    drift = reconcile(ARTIFACT, cast)
    assert drift["boundButMissingFromCast"] == [m["c.0"]]
    assert drift["inCastButNeverBound"] == ["mystery_extra"]


def test_fill_refuses_on_missing_alias(tmp_path):
    """The silent-drop failure mode: an alias the cast lacks would vanish without
    error. Reconciliation must refuse, not produce a PDF with a hole."""
    m = alias_map(ARTIFACT)
    cast = _cast([m["a.0"], m["b.0"]])  # c.0's alias missing
    t = StubTransport()
    with pytest.raises(RuntimeError, match="SILENTLY dropped"):
        fill_via_anvil(ARTIFACT, {"a.0": "Walter"}, cast, t, tmp_path / "x.pdf")
    assert t.filled is None, "no fill request may be sent on drift"


def test_fill_writes_binary_when_reconciled(tmp_path):
    m = alias_map(ARTIFACT)
    cast = _cast(list(m.values()))
    t = StubTransport()
    out = tmp_path / "ok.pdf"
    res = fill_via_anvil(ARTIFACT, {"a.0": "Walter", "c.0": True}, cast, t, out)
    assert out.read_bytes().startswith(b"%PDF")
    assert res["bytes"] > 0
    _, payload = t.filled
    assert payload["data"][m["a.0"]] == "Walter"


def test_missing_key_is_a_named_ask(monkeypatch, tmp_path):
    monkeypatch.delenv("ANVIL_API_KEY", raising=False)
    import forge.anvil as anvil

    monkeypatch.setattr(anvil, "ROOT", tmp_path)  # no .env here
    with pytest.raises(MissingCredential, match="ANVIL_API_KEY"):
        require_key()


def test_cast_field_ids_tolerates_shapes():
    assert cast_field_ids({"fieldInfo": [{"id": "x"}]}) == {"x"}
    assert cast_field_ids({"fieldInfo": {"fields": [{"aliasId": "y"}]}}) == {"y"}
