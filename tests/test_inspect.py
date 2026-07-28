"""Fixture tests against the real PDFs in inputs/forms/. docs/00-DOMAIN.md §2 and §3."""

from __future__ import annotations

import shutil

import pytest

from forge.inspect import EXPECTED, inspect_all, summary_line
from forge.pdfmeta import read_form
from forge.registry import FORMS, get_form


@pytest.fixture(scope="module")
def forms():
    return {f.form_id: read_form(f.path) for f in FORMS}


@pytest.mark.parametrize("form_id", sorted(EXPECTED))
def test_field_counts_match_domain_fixtures(forms, form_id):
    total, text, button, tooltips = EXPECTED[form_id]
    counts = forms[form_id].counts
    assert (counts["total"], counts["text"], counts["button"], counts["tooltips"]) == (
        total,
        text,
        button,
        tooltips,
    )


def test_gate_output_is_exact(forms, capsys):
    """docs/04-BUILD-PLAN.md phase 0 specifies the output byte for byte."""
    expected = (
        "irs-ss4        93 fields   45 text   44 button    0 tooltips\n"
        "irs-f56        76 fields   39 text   33 button    0 tooltips\n"
        "irs-f8821      52 fields   33 text   12 button    5 tooltips\n"
        "ca-dmv-dl142   51 fields   14 text   14 button   28 tooltips\n"
        "PASS\n"
    )
    rc = inspect_all()
    assert capsys.readouterr().out == expected
    assert rc == 0


def test_form56_button_on_values(forms):
    """docs/00-DOMAIN.md §3.2: on-values are /1../4, discovered per field, never /Yes."""
    buttons = forms["irs-f56"].of_type("button")
    on_values = {b.on_value for b in buttons}
    assert "/2" in on_values
    assert "/Yes" not in on_values
    assert None not in on_values
    group = {
        b.qualified_name.rsplit(".", 1)[-1]: b.on_value
        for b in buttons
        if b.qualified_name.startswith("topmostSubform[0].Page1[0].c1_1[")
    }
    assert group["c1_1[0]"] == "/1"
    assert group["c1_1[1]"] == "/2"
    assert group["c1_1[2]"] == "/3"
    assert group["c1_1[3]"] == "/4"


def test_every_button_on_every_form_has_a_discovered_on_value(forms):
    for form_id, info in forms.items():
        missing = [b.qualified_name for b in info.of_type("button") if b.on_value is None]
        assert missing == [], f"{form_id} has buttons with no discoverable onValue"


def test_dl142_is_keyed_by_qualified_name(forms):
    """docs/00-DOMAIN.md §3.3: short names collide on DL 142."""
    fields = forms["ca-dmv-dl142"].fields
    qualified = [f.qualified_name for f in fields]
    assert len(set(qualified)) == len(qualified) == 51
    short = [q.rsplit(".", 1)[-1] for q in qualified]
    assert len(set(short)) < len(short), "expected short-name collisions on DL 142"
    parent = "Address person DL/ID to be cancel"
    assert parent in qualified
    assert f"{parent}.0" in qualified


def test_containers_carry_no_field_type(forms):
    for info in forms.values():
        for f in info.fields:
            if f.type == "container":
                assert f.ft is None


def test_xfa_and_need_appearances_match_domain(forms):
    assert forms["irs-f56"].has_xfa is True
    assert forms["irs-ss4"].has_xfa is True
    assert forms["irs-f8821"].has_xfa is True
    assert forms["ca-dmv-dl142"].has_xfa is False
    for info in forms.values():
        assert info.need_appearances is None


def test_text_fields_have_a_page_and_rect(forms):
    for form_id, info in forms.items():
        for f in info.of_type("text"):
            assert f.page is not None, f"{form_id} {f.qualified_name} has no page"
            assert f.rect is not None, f"{form_id} {f.qualified_name} has no rect"


def test_registry_rejects_unknown_form_id():
    with pytest.raises(KeyError):
        get_form("irs-1040")


def test_poppler_is_available():
    """Phase 1 rasterises with pdftoppm. Ground truth, per CLAUDE.md hard rule 3."""
    assert shutil.which("pdftoppm"), "poppler is not installed (brew install poppler)"


def test_summary_line_column_layout(forms):
    line = summary_line("irs-f56", forms["irs-f56"])
    assert line == "irs-f56        76 fields   39 text   33 button    0 tooltips"
