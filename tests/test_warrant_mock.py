"""The seam with Warrant. docs/01-CONTRACT.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.estatepath import EstateData
from forge.registry import ESTATES_DIR, estate_path
from forge.warrant_mock import build_work_order

ESTATE_IDS = sorted(p.stem for p in ESTATES_DIR.glob("*.json"))
FORM_IDS = {"irs-f56", "irs-ss4", "irs-f8821", "ca-dmv-dl142"}


@pytest.mark.parametrize("estate_id", ESTATE_IDS)
def test_work_order_shape(estate_id):
    order = build_work_order(estate_id)
    assert order["estateId"] == estate_id
    assert set(order) >= {
        "estateId",
        "estatePath",
        "jurisdiction",
        "route",
        "generatedAt",
        "forms",
    }
    assert {f["formId"] for f in order["forms"]} == FORM_IDS
    for f in order["forms"]:
        assert set(f) == {
            "formId",
            "applicable",
            "reason",
            "priority",
            "blastRadius",
            "reversibility",
        }
        if f["applicable"]:
            assert f["reason"] is None
            assert f["blastRadius"] in {"low", "medium", "high"}
            assert f["reversibility"] in {"reversible", "irreversible"}
        else:
            assert f["reason"], "an inapplicable form must say why"
            assert f["priority"] is None


@pytest.mark.parametrize("estate_id", ESTATE_IDS)
def test_applicability_is_copied_from_the_data_never_decided(estate_id):
    estate = json.loads(estate_path(estate_id).read_text())
    order = build_work_order(estate_id)
    forms = {f["formId"]: f for f in order["forms"]}

    assert forms["ca-dmv-dl142"]["applicable"] == estate["formDL142"]["applicable"]
    if not forms["ca-dmv-dl142"]["applicable"]:
        assert forms["ca-dmv-dl142"]["reason"] == estate["formDL142"]["notApplicableReason"]

    # SS-4 applies for an EIN; an estate that already has one is not applying.
    assert forms["irs-ss4"]["applicable"] == (not estate["estateEntity"]["ein"])


def test_domain_denominators_hold_across_the_sample_set():
    """docs/00-DOMAIN.md §4: DL 142 applies to 2 of 5, SS-4 is live for 2 of 5."""
    applicable = {form_id: 0 for form_id in FORM_IDS}
    for estate_id in ESTATE_IDS:
        for f in build_work_order(estate_id)["forms"]:
            applicable[f["formId"]] += int(f["applicable"])
    assert applicable["ca-dmv-dl142"] == 2
    assert applicable["irs-ss4"] == 2
    assert applicable["irs-f56"] == 5
    assert applicable["irs-f8821"] == 5


def test_mock_stays_under_100_lines():
    """docs/01-CONTRACT.md: past 100 lines, legal reasoning is leaking in."""
    src = Path(__file__).resolve().parents[1] / "src" / "forge" / "warrant_mock.py"
    assert len(src.read_text().splitlines()) < 100


def test_jurisdiction_and_route_come_from_the_estate():
    order = build_work_order("estate-05-in-formal-probate")
    assert order["jurisdiction"] == {"state": "IN", "county": "Marion"}
    assert order["route"] == "FORMAL_PROBATE"


def test_resolver_distinguishes_absent_from_empty():
    estate = EstateData.load(estate_path("estate-05-in-formal-probate"))

    present = estate.resolve("decedent.name.full")
    assert present.present and present.value

    null_valued = estate.resolve("estateEntity.tradeName")
    assert null_valued.present is True and null_valued.value is None

    missing = estate.resolve("estateEntity.thisKeyDoesNotExist")
    assert missing.present is False and missing.value is None and missing.reason

    indexed = estate.resolve("taxMatters.authorizationRows[0].taxFormNumber")
    assert indexed.present

    out_of_range = estate.resolve("taxMatters.authorizationRows[99].taxFormNumber")
    assert out_of_range.present is False

    assert len(estate.log) == 5, "every resolution is recorded"
