"""Anvil reconciliation logic against a stub transport — the parts that must be
right before a key exists. The silent-drop refusal is the $1,000 demo."""

from __future__ import annotations

import pytest

from forge.anvil import (
    MissingCredential,
    alias_map,
    cast_field_ids,
    field_id_map,
    fill_via_anvil,
    reconcile,
    require_key,
)

ARTIFACT = {
    "bindings": [
        {"qualifiedName": "top[0].Page1[0].f1_01[0]", "label": "Name", "source": {"kind": "path", "path": "decedent.name.full"}},
        {"qualifiedName": "top[0].Page1[0].f1_02[0]", "label": "City", "source": {"kind": "path", "path": "decedent.residenceAddress.city"}},
        {"qualifiedName": "top[0].Page1[0].c1_1[0]", "label": "DL box", "source": {"kind": "condition", "path": "x", "equals": "y"}},
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


def _cast(short_names, allowed_aliases=None):
    """Live cast shape: `name` is the short PDF field name, `id` Anvil's own handle.

    A real cast came back with all 72 of our aliases on `allowedAliasIds` and still
    dropped every value, so these stubs carry that field only to prove reconciliation
    ignores it."""
    cast = {
        "eid": "cast123",
        "fieldInfo": {
            "fields": [
                {"name": n, "id": f"id-{i}", "type": "shortText", "pageNum": 0}
                for i, n in enumerate(short_names)
            ]
        },
    }
    if allowed_aliases is not None:
        cast["allowedAliasIds"] = allowed_aliases
    return cast


def test_aliases_are_path_derived_and_unique():
    m = alias_map(ARTIFACT)
    assert m["top[0].Page1[0].f1_01[0]"] == "decedent_name_full"
    assert m["top[0].Page1[0].f1_02[0]"] == "decedent_residenceaddress_city"
    assert len(set(m.values())) == 3


def test_reconcile_reports_both_directions():
    cast = _cast(["f1_01[0]", "f1_02[0]", "mystery_extra"])
    drift = reconcile(ARTIFACT, cast)
    assert drift["boundButMissingFromCast"] == ["c1_1[0]"]
    assert drift["inCastButNeverBound"] == ["mystery_extra"]


def test_reconcile_ignores_allowedAliasIds():
    """The trap that hid inside reconciliation itself: a live cast echoed every alias
    back on allowedAliasIds while the fill dropped all 32 values. A cast that lists our
    aliases but does not have the FIELD must still be reported as drift."""
    cast = _cast(["f1_01[0]"], allowed_aliases=list(alias_map(ARTIFACT).values()))
    drift = reconcile(ARTIFACT, cast)
    assert drift["boundButMissingFromCast"] == ["c1_1[0]", "f1_02[0]"]


def test_fill_refuses_on_missing_alias(tmp_path):
    """The silent-drop failure mode: an alias the cast lacks would vanish without
    error. Reconciliation must refuse, not produce a PDF with a hole."""
    cast = _cast(["f1_01[0]", "f1_02[0]"])  # the field behind c1_1[0] is missing
    t = StubTransport()
    with pytest.raises(RuntimeError, match="SILENTLY dropped"):
        fill_via_anvil(ARTIFACT, {"top[0].Page1[0].f1_01[0]": "Walter"}, cast, t, tmp_path / "x.pdf")
    assert t.filled is None, "no fill request may be sent on drift"


def test_fill_writes_binary_when_reconciled(tmp_path):
    cast = _cast(["f1_01[0]", "f1_02[0]", "c1_1[0]"])
    t = StubTransport()
    out = tmp_path / "ok.pdf"
    res = fill_via_anvil(ARTIFACT, {"top[0].Page1[0].f1_01[0]": "Walter", "top[0].Page1[0].c1_1[0]": True}, cast, t, out)
    assert out.read_bytes().startswith(b"%PDF")
    assert res["bytes"] > 0
    _, payload = t.filled
    # keyed by Anvil's internal field id, NOT by our alias: a live fill keyed by the
    # alias returned a valid PDF with every value dropped
    ids = field_id_map(ARTIFACT, cast)
    assert payload["data"][ids["top[0].Page1[0].f1_01[0]"]] == "Walter"
    assert payload["data"][ids["top[0].Page1[0].c1_1[0]"]] is True


def test_field_id_map_refuses_duplicate_short_names():
    """DL 142 has duplicate short names (docs/00-DOMAIN.md §3.3); a positional or
    short-name mapping over a collision would mis-file values."""
    cast = _cast(["f1_01[0]", "f1_01[0]"])
    with pytest.raises(RuntimeError, match="duplicate short name"):
        field_id_map(ARTIFACT, cast)


def test_missing_key_is_a_named_ask(monkeypatch, tmp_path):
    monkeypatch.delenv("ANVIL_API_KEY", raising=False)
    import forge.anvil as anvil

    monkeypatch.setattr(anvil, "ROOT", tmp_path)  # no .env here
    with pytest.raises(MissingCredential, match="ANVIL_API_KEY"):
        require_key()


def test_cast_field_ids_reads_detected_names():
    assert cast_field_ids({"fieldInfo": [{"name": "f1_01[0]", "id": "x"}]}) == {"f1_01[0]"}
    assert cast_field_ids(
        {"fieldInfo": {"fields": [{"name": "c1_1[0]", "id": "y"}]}}
    ) == {"c1_1[0]"}
    # allowedAliasIds must not be mistaken for the fields the cast actually has
    assert cast_field_ids(
        {"allowedAliasIds": ["decedent_name_full"], "fieldInfo": {"fields": []}}
    ) == set()


def test_reconcile_refuses_ambiguous_short_names():
    """Two bound fields whose last name segment is identical — the DL 142 shape."""
    artifact = {
        "bindings": [
            {"qualifiedName": "Address to cancel.0", "label": "A",
             "source": {"kind": "path", "path": "x"}},
            {"qualifiedName": "City to cancel.0", "label": "B",
             "source": {"kind": "path", "path": "y"}},
        ],
        "unbound": [], "exclusiveGroups": [],
    }
    with pytest.raises(RuntimeError, match="ambiguous"):
        reconcile(artifact, _cast(["0"]))
