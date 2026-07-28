"""Approval, versioning, immutability. docs/02-SPEC.md §3."""

from __future__ import annotations

import json

import pytest

import forge.review as review


ARTIFACT = {
    "formId": "irs-f56",
    "version": 1,
    "status": "draft",
    "sourceFormSha256": "x",
    "calibrationRef": "artifacts/calibration/irs-f56.json",
    "createdAt": "2026-07-28T00:00:00Z",
    "approvedBy": None,
    "approvedAt": None,
    "anvilCastEid": None,
    "bindings": [],
    "unbound": [],
    "exclusiveGroups": [],
}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    drafts = tmp_path / "bindings"
    approved = tmp_path / "approved"
    drafts.mkdir()
    monkeypatch.setattr(review, "BINDINGS_DIR", drafts)
    monkeypatch.setattr(review, "APPROVED_DIR", approved)
    (drafts / "irs-f56.json").write_text(json.dumps(ARTIFACT))
    return drafts, approved


def test_approval_versions_and_attributes(dirs):
    _, approved = dirs
    out = review.approve("irs-f56", "Pat Reviewer")
    assert out["version"] == 1
    frozen = json.loads((approved / "irs-f56.v1.json").read_text())
    assert frozen["status"] == "approved"
    assert frozen["approvedBy"] == "Pat Reviewer"
    assert frozen["approvedAt"]

    # next approval becomes v2, never overwrites v1
    out2 = review.approve("irs-f56", "Pat Reviewer")
    assert out2["version"] == 2
    assert (approved / "irs-f56.v1.json").exists()
    assert (approved / "irs-f56.v2.json").exists()


def test_unattributed_approval_is_refused(dirs):
    with pytest.raises(ValueError):
        review.approve("irs-f56", "   ")


def test_approved_file_is_read_only(dirs):
    _, approved = dirs
    review.approve("irs-f56", "Pat")
    mode = (approved / "irs-f56.v1.json").stat().st_mode & 0o777
    assert mode == 0o444


# --- overlay geometry: PDF points (origin bottom-left) -> image % (origin top-left)


def _pages(crop=(0.0, 0.0, 612.0, 792.0), rotate=0):
    return [
        {
            "index": 0,
            "cropBox": list(crop),
            "mediaBox": [0.0, 0.0, 612.0, 792.0],
            "widthPt": crop[2] - crop[0],
            "heightPt": crop[3] - crop[1],
            "rotate": rotate,
        }
    ]


def test_overlay_flips_y_against_the_crop_box():
    from forge.review import overlay_boxes

    # a 100x50pt box whose TOP edge sits 92pt below the top of a 792pt page
    field = {"widgets": [{"page": 0, "rect": [61.2, 650.0, 161.2, 700.0]}]}
    (b,) = overlay_boxes(field, _pages())
    # percentages are rounded to 4 dp in the artifact, hence abs=1e-4
    assert b["left"] == pytest.approx(10.0, abs=1e-4)  # 61.2 / 612
    assert b["top"] == pytest.approx((792 - 700) / 792 * 100, abs=1e-4)
    assert b["width"] == pytest.approx(100 / 612 * 100, abs=1e-4)
    assert b["height"] == pytest.approx(50 / 792 * 100, abs=1e-4)
    assert b["offCrop"] is False


def test_overlay_subtracts_a_non_zero_crop_origin():
    """DL 142's CropBox is [0, 3.55556, 612, 792]; pdftoppm renders the crop."""
    from forge.review import overlay_boxes

    crop = (0.0, 3.55556, 612.0, 792.0)
    field = {"widgets": [{"page": 0, "rect": [0.0, 3.55556, 612.0, 792.0]}]}
    (b,) = overlay_boxes(field, _pages(crop))
    # a widget filling the crop exactly must map to the whole image
    assert b["left"] == pytest.approx(0.0, abs=1e-4)
    assert b["top"] == pytest.approx(0.0, abs=1e-4)
    assert b["width"] == pytest.approx(100.0, abs=1e-4)
    assert b["height"] == pytest.approx(100.0, abs=1e-4)


def test_overlay_returns_every_widget():
    from forge.review import overlay_boxes

    field = {
        "widgets": [
            {"page": 0, "rect": [10, 700, 110, 720]},
            {"page": 0, "rect": [10, 300, 110, 320]},
        ]
    }
    boxes = overlay_boxes(field, _pages())
    assert len(boxes) == 2
    assert boxes[0]["top"] < boxes[1]["top"]  # higher on the page = smaller top %


def test_overlay_flags_a_widget_outside_the_rendered_crop():
    from forge.review import overlay_boxes

    field = {"widgets": [{"page": 0, "rect": [700, 700, 800, 720]}]}  # x beyond 612
    (b,) = overlay_boxes(field, _pages())
    assert b["offCrop"] is True


def test_overlay_refuses_a_rotated_page_rather_than_drawing_it_wrong():
    from forge.review import overlay_boxes

    field = {"widgets": [{"page": 0, "rect": [10, 700, 110, 720]}]}
    (b,) = overlay_boxes(field, _pages(rotate=90))
    assert "unsupported" in b and "left" not in b


def test_overlay_falls_back_to_the_single_rect_pre_backfill():
    from forge.review import overlay_boxes

    field = {"page": 0, "rect": [61.2, 650.0, 161.2, 700.0]}
    (b,) = overlay_boxes(field, _pages())
    assert b["left"] == pytest.approx(10.0, abs=1e-4)
