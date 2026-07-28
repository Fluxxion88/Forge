"""The fixed form registry and repository paths.

`formId` slugs are fixed by docs/01-CONTRACT.md and are the vocabulary shared with
Warrant. Nothing may invent a new one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

INPUTS = ROOT / "inputs"
FORMS_DIR = INPUTS / "forms"
ESTATES_DIR = INPUTS / "estates"

ARTIFACTS = ROOT / "artifacts"
CALIBRATION_DIR = ARTIFACTS / "calibration"
BINDINGS_DIR = ARTIFACTS / "bindings"
APPROVED_DIR = ARTIFACTS / "approved"
WORKORDERS_DIR = ARTIFACTS / "workorders"

OUT = ROOT / "out"
FILLS_DIR = OUT / "fills"
RENDERS_DIR = OUT / "renders"
REPORTS_DIR = OUT / "reports"


@dataclass(frozen=True)
class Form:
    form_id: str
    filename: str
    title: str

    @property
    def path(self) -> Path:
        return FORMS_DIR / self.filename


# Order is the order docs/04-BUILD-PLAN.md expects `forge inspect --all` to print.
FORMS: tuple[Form, ...] = (
    Form("irs-ss4", "Form SS-4 Dec 2025.pdf", "Application for Employer Identification Number"),
    Form("irs-f56", "Form 56 June 2026.pdf", "Notice Concerning Fiduciary Relationship"),
    Form("irs-f8821", "Form 8821 Jan 2021.pdf", "Tax Information Authorization"),
    Form("ca-dmv-dl142", "DL 142 R7 93.pdf", "Notice of Release of Liability / licence cancellation"),
)

FORMS_BY_ID: dict[str, Form] = {f.form_id: f for f in FORMS}


def get_form(form_id: str) -> Form:
    try:
        return FORMS_BY_ID[form_id]
    except KeyError:
        known = ", ".join(FORMS_BY_ID)
        raise KeyError(f"unknown formId {form_id!r}; the registry is fixed: {known}") from None


def estate_path(estate_id: str) -> Path:
    """Resolve an estate id to its file under inputs/estates/."""
    p = ESTATES_DIR / f"{estate_id}.json"
    if not p.exists():
        known = sorted(q.stem for q in ESTATES_DIR.glob("*.json"))
        raise FileNotFoundError(f"unknown estateId {estate_id!r}; known: {', '.join(known)}")
    return p


def load_work_order(estate_id: str) -> dict[str, Any]:
    """Read what Warrant (or its mock) decided for this estate. See docs/01-CONTRACT.md."""
    p = WORKORDERS_DIR / f"{estate_id}.json"
    if not p.exists():
        raise FileNotFoundError(
            f"no work order at {rel(p)}; run: forge mock-workorder --estate {estate_id}"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    """Repository-relative path, for writing into artifacts."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
