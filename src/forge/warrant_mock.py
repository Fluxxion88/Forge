"""Stand-in for Warrant. Deleted at the merge; the real thing writes the same file.

Performs NO legal reasoning. It reads applicability flags that are already in the
estate data and copies reasons straight through. See docs/01-CONTRACT.md.
Hard limit: under 100 lines. If it grows past that, reasoning is leaking in — stop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .estatepath import EstateData
from .registry import WORKORDERS_DIR, estate_path, rel

# Placeholders. Warrant replaces both columns with its own assessment.
RISK: dict[str, tuple[str, str]] = {
    "irs-f56": ("high", "irreversible"),
    "irs-ss4": ("high", "irreversible"),
    "irs-f8821": ("medium", "reversible"),
    "ca-dmv-dl142": ("medium", "irreversible"),
}

PRIORITY = {"irs-ss4": 1, "irs-f56": 2, "irs-f8821": 3, "ca-dmv-dl142": 4}


def _applicability(estate: EstateData, form_id: str) -> tuple[bool, str | None]:
    """Read a flag. Never decide one."""
    if form_id == "ca-dmv-dl142":
        flag = estate.resolve("formDL142.applicable")
        reason = estate.resolve("formDL142.notApplicableReason")
        if flag.present and flag.value is False:
            return False, reason.value if reason.present else "flagged not applicable"
        if flag.present and flag.value is True:
            return True, None
        return False, "formDL142.applicable is absent from the estate record"
    if form_id == "irs-ss4":
        # The estate record states the EIN. An estate that already has one is not
        # applying for one. This is a data read, not a rule.
        ein = estate.resolve("estateEntity.ein")
        if ein.present and ein.value:
            return False, f"The estate already holds EIN {ein.value}; SS-4 applies for a new one."
        return True, None
    # form56 and form8821 carry no applicability flag in the sample schema.
    return True, None


def build_work_order(estate_id: str) -> dict[str, Any]:
    path = estate_path(estate_id)
    estate = EstateData.load(path)

    state = estate.resolve("authority.proceeding.courtAddress.state")
    county = estate.resolve("authority.proceeding.courtAddress.county")
    if not state.present:
        state = estate.resolve("decedent.residenceAddress.state")
        county = estate.resolve("decedent.residenceAddress.county")
    route = estate.resolve("authority.administrationPath")

    forms = []
    for form_id in ("irs-f56", "irs-ss4", "irs-f8821", "ca-dmv-dl142"):
        applicable, reason = _applicability(estate, form_id)
        blast, reversibility = RISK[form_id]
        forms.append(
            {
                "formId": form_id,
                "applicable": applicable,
                "reason": None if applicable else reason,
                "priority": PRIORITY[form_id] if applicable else None,
                "blastRadius": blast if applicable else None,
                "reversibility": reversibility if applicable else None,
            }
        )

    return {
        "estateId": estate.estate_id,
        "estatePath": rel(path),
        "jurisdiction": {
            "state": state.value if state.present else None,
            "county": county.value if county.present else None,
        },
        "route": route.value if route.present else None,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "warrant_mock",
        "forms": forms,
    }


def write_work_order(estate_id: str) -> str:
    order = build_work_order(estate_id)
    WORKORDERS_DIR.mkdir(parents=True, exist_ok=True)
    out = WORKORDERS_DIR / f"{order['estateId']}.json"
    out.write_text(json.dumps(order, indent=2) + "\n", encoding="utf-8")
    return rel(out)
