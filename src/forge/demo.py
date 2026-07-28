"""6.6 — demo assets under out/demo/. No model calls; pure assembly.

Built to run at any point in the pipeline: every asset that cannot be produced yet
is reported as skipped with the reason, and the command is re-run after more forms
land. The headline estate is estate-03-oh-trust-administration (operator decision —
estate-05 is the calibration estate, so demoing it proves nothing about reuse).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .registry import (
    CALIBRATION_DIR,
    ESTATES_DIR,
    FILLS_DIR,
    OUT,
    REPORTS_DIR,
    RENDERS_DIR,
    rel,
)

DEMO = OUT / "demo"
HEADLINE_ESTATE = "estate-03-oh-trust-administration"
HEADLINE_FORM = "irs-f56"


def _section_a_bbox_px(dpi: int = 150) -> tuple[int, int, int, int] | None:
    """Locate Form 56 Section A (the line-1 authority checkbox group) from the
    calibration rects — data-driven, not hardcoded."""
    cal_path = CALIBRATION_DIR / f"{HEADLINE_FORM}.json"
    if not cal_path.exists():
        return None
    cal = json.loads(cal_path.read_text(encoding="utf-8"))
    rects = [
        f["rect"] for f in cal["fields"]
        if f["page"] == 0 and ".c1_1[" in f["qualifiedName"] and f["rect"]
    ]
    dates = [
        f["rect"] for f in cal["fields"]
        if f["page"] == 0 and f["qualifiedName"].endswith(("f1_19[0]", "f1_20[0]")) and f["rect"]
    ]
    if not rects:
        return None
    xs = [r[0] for r in rects + dates] + [r[2] for r in rects + dates]
    ys = [r[1] for r in rects + dates] + [r[3] for r in rects + dates]
    scale = dpi / 72.0
    page_h = 792.0
    x0 = max(0, min(xs) - 24)
    x1 = min(612.0, max(xs) + 320)  # include the printed captions right of the boxes
    y0 = min(ys) - 12
    y1 = max(ys) + 26
    return (
        round(x0 * scale),
        round((page_h - y1) * scale),
        round((x1 - x0) * scale),
        round((y1 - y0) * scale),
    )


def _crop_section_a(estate_id: str, out_png: Path) -> bool:
    """Crop Section A out of an estate's filled Form 56 render via pdftoppm."""
    src_pdf = FILLS_DIR / f"{estate_id}-{HEADLINE_FORM}.pdf"
    bbox = _section_a_bbox_px()
    if not src_pdf.exists() or bbox is None:
        return False
    x, y, w, h = bbox
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tmp_prefix = out_png.with_suffix("")
    subprocess.run(
        ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1",
         "-x", str(x), "-y", str(y), "-W", str(w), "-H", str(h),
         str(src_pdf), str(tmp_prefix)],
        check=True, capture_output=True,
    )
    produced = sorted(out_png.parent.glob(f"{out_png.stem}-*.png"))
    if not produced:
        return False
    produced[0].replace(out_png)
    for extra in produced[1:]:
        extra.unlink()
    return True


def build_headline() -> list[str]:
    """Same binding, five estates: Section A ticks differ, binding does not."""
    notes = []
    crops_dir = DEMO / "headline-section-a"
    made = []
    for estate in sorted(p.stem for p in ESTATES_DIR.glob("*.json")):
        out_png = crops_dir / f"{estate}.png"
        if _crop_section_a(estate, out_png):
            made.append(estate)
        else:
            notes.append(f"headline: no filled PDF for {estate} yet (out/fills/)")
    if made:
        index = [
            "# Same binding, different estates — Form 56 Section A",
            "",
            f"One approved binding. The authority checkbox differs per estate; "
            f"the binding file is byte-identical for all {len(made)}. "
            f"Headline estate: **{HEADLINE_ESTATE}** (trustee, box 1e; the calibration "
            "estate estate-05 ticks 1a — opposite branch of line 2a/2b as well).",
            "",
        ]
        for estate in made:
            index.append(f"## {estate}\n\n![]({rel(crops_dir / (estate + '.png'))})\n")
        (DEMO / "headline.md").write_text("\n".join(index), encoding="utf-8")
        notes.append(f"headline: {len(made)} Section A crops written")
    return notes


def build_loop_history() -> list[str]:
    """Round 1 wrong next to the final round right — the loop's provenance."""
    report_path = REPORTS_DIR / f"{HEADLINE_FORM}-loop.json"
    if not report_path.exists():
        return [f"loop history: {rel(report_path)} does not exist yet"]
    r = json.loads(report_path.read_text(encoding="utf-8"))
    lines = [
        f"# The convergence loop, {r['formId']} vs {r['estateId']}",
        "",
        f"Converged: **{r['converged']}** in {r['rounds']} round(s), "
        f"{r['modelCalls']} model calls, {r['elapsedSeconds']}s.",
        "",
    ]
    for h in r["history"]:
        findings = h["deterministicFindings"] + h["modelFindings"]
        lines.append(f"## Round {h['round']} — {len(findings)} finding(s), "
                     f"{h['fieldsFilled']} fields filled")
        for f in findings:
            lines.append(f"- **{f.get('target')}**: {f.get('problem')}")
        if h.get("diffFromPreviousRound"):
            d = h["diffFromPreviousRound"]
            lines.append(
                f"- repair diff: +{len(d['added'])} added, -{len(d['removed'])} removed, "
                f"~{len(d['changed'])} changed"
            )
        for png in h["renders"]:
            lines.append(f"\n![]({png})\n")
    (DEMO / "loop-history.md").write_text("\n".join(lines), encoding="utf-8")
    return ["loop history: written"]


def build_benchmark() -> list[str]:
    src = REPORTS_DIR / "benchmark.md"
    if not src.exists():
        return ["benchmark.md: not generated yet (forge bench)"]
    shutil.copy(src, DEMO / "benchmark.md")
    return ["benchmark.md: copied"]


def build_runbook() -> list[str]:
    (DEMO / "RUNBOOK.md").write_text(RUNBOOK, encoding="utf-8")
    return ["RUNBOOK.md: written"]


RUNBOOK = """# Demo runbook

Slot is three minutes. Every live command below completes in under two seconds.
The convergence loop is NEVER run live — a critique round costs about a minute.
Its history is pre-rendered in `loop-history.md`; scroll it instead.

Pre-demo (once, before the slot):

    source .venv/bin/activate
    forge inspect --all            # sanity: 4 forms, exact counts, PASS

Live sequence:

 1. The problem — open `inputs/forms/Form 56 June 2026.pdf`, point at a field name:
    `topmostSubform[0].Page1[0].f1_04[0]`. Nothing says what it means.

 2. The compiled artifact — open `artifacts/approved/irs-f56.v1.json`. Point at:
    a binding (path → box), a `when` guard, an `exclusiveGroup`, `approvedBy`.
    "A human approved this once. It is data. No model runs from here on."

 3. The fill —

        forge fill irs-f56 --estate estate-03-oh-trust-administration

    Point at the sidecar: `llmCallsAtRuntime: 0` (measured by a counter wired into
    the model client, and there is a test that fails if it ever isn't).

 4. Reuse — same command, different estate:

        forge fill irs-f56 --estate estate-01-nj-ancillary-probate

    Open `out/demo/headline.md`: same binding, five estates, different ticks.

 5. Honesty — in the sidecar, the `empty` array: every blank field names the data
    path that would fill it. Unknown is not false.

 6. (If Anvil key present) The sponsor path —

        forge fill ca-dmv-dl142 --estate estate-02-ca-intestate-independent-admin --via anvil

    Then the catch: `reconciliation-catch.md` — a deliberate alias mismatch, and
    Forge refusing to produce a PDF with an invisible hole in it.

 7. Close on `benchmark.md`: build cost once per form, fill cost milliseconds,
    model calls at fill time: zero, everywhere, measured.
"""


def build_reconciliation_catch() -> list[str]:
    """Static narration + the stub-verified behaviour; refreshed with live output
    when a key arrives."""
    txt = """# The reconciliation catch

Anvil's fill endpoint fails silently: a value posted to an alias the template does
not have is dropped — no error, and the returned PDF looks complete with one empty
box. On a real filing that is a rejection and another month of a family's life.

`forge fill --via anvil` therefore reconciles first, in both directions, and
refuses to fill on any drift.

Status: verified against a stub transport (tests/test_anvil.py::
test_fill_refuses_on_missing_alias — asserts no fill request is even sent).
LIVE demonstration pending ANVIL_API_KEY; once present:

    forge anvil-register ca-dmv-dl142          # registers cast with our aliases
    forge fill ca-dmv-dl142 --estate estate-02-ca-intestate-independent-admin --via anvil
    # then deliberately break one alias in the draft, re-register, and watch the
    # fill REFUSE rather than return a clean-looking PDF with a hole.
"""
    (DEMO / "reconciliation-catch.md").write_text(txt, encoding="utf-8")
    return ["reconciliation-catch.md: written (stub-verified; live pending key)"]


def build_all() -> int:
    DEMO.mkdir(parents=True, exist_ok=True)
    notes = []
    for step in (build_headline, build_loop_history, build_benchmark,
                 build_reconciliation_catch, build_runbook):
        notes += step()
    for n in notes:
        print(n)
    print(f"demo assets under {rel(DEMO)}")
    return 0
