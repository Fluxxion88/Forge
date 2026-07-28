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
