"""The sentinel-write technique, verified by raster diff. CLAUDE.md hard rules 2 and 3."""

from __future__ import annotations

import pytest

from forge.calibrate import _px_bbox, assign_tokens
from forge.fillwriter import pgm_payload_diff, render_pages, write_filled
from forge.pdfmeta import read_form
from forge.registry import get_form


@pytest.mark.parametrize("form_id", ["ca-dmv-dl142", "irs-f56"])
def test_sentinels_change_rendered_pixels(form_id, tmp_path):
    """Writing values must change the raster, not just the value dictionary —
    pdftotext-style verification is banned for exactly this reason."""
    form = get_form(form_id)
    info = read_form(form.path)

    blank = render_pages(form.path, tmp_path, "blank", dpi=72, gray=True, page=0)[0]

    tokens = assign_tokens(info.fields)
    text_pdf = tmp_path / "text.pdf"
    assert write_filled(form.path, text_pdf, texts=tokens) == []
    text_render = render_pages(text_pdf, tmp_path, "text", dpi=72, gray=True, page=0)[0]
    assert pgm_payload_diff(blank, text_render) > 100

    buttons = {
        f.qualified_name: f.on_value
        for f in info.of_type("button")
        if f.on_value and not f.is_pushbutton
    }
    btn_pdf = tmp_path / "btn.pdf"
    assert write_filled(form.path, btn_pdf, buttons=buttons) == []
    btn_render = render_pages(btn_pdf, tmp_path, "btn", dpi=72, gray=True, page=0)[0]
    assert pgm_payload_diff(blank, btn_render) > 100


def test_missing_field_names_are_returned_not_ignored(tmp_path):
    form = get_form("ca-dmv-dl142")
    missing = write_filled(form.path, tmp_path / "x.pdf", texts={"no.such.field": "Z001"})
    assert missing == ["no.such.field"]


def test_button_value_must_be_a_name(tmp_path):
    form = get_form("irs-f56")
    info = read_form(form.path)
    qname = info.of_type("button")[0].qualified_name
    with pytest.raises(ValueError):
        write_filled(form.path, tmp_path / "x.pdf", buttons={qname: "True"})


def test_tokens_are_unique_and_never_truncated():
    """A truncated sentinel is ambiguous on the page; /MaxLen applies to fill, not
    calibration. state.0 (/MaxLen 2) must still get a full Z-token."""
    info = read_form(get_form("ca-dmv-dl142").path)
    tokens = assign_tokens(info.fields)
    assert len(set(tokens.values())) == len(tokens) == 14
    assert all(len(t) == 4 and t.startswith("Z") for t in tokens.values())


def test_px_bbox_flips_y_axis():
    # A 10pt-tall box whose top edge is 100pt below the top of a 792pt page.
    bbox = _px_bbox([72.0, 682.0, 172.0, 692.0], 792.0)
    assert bbox == [150, 208, 358, 229]  # 150 dpi: pt * 150/72, y measured from top
