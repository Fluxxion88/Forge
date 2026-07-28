# Forge — a compiler for government forms

Forge takes a blank government PDF whose fields are machine-named and meaningless
(`topmostSubform[0].Page1[0].f1_04[0]`) and produces a reviewed, versioned, reusable JSON
artifact that maps structured estate data onto that form. A model is spent **once per form, at
build time, under human supervision**, and the result is frozen. After a human approves it,
filling that form for any estate is deterministic: no prompt, no temperature, no variance —
`forge fill` asserts zero model calls at runtime with a counter, and there is a test that fails
if one ever happens. The one-line version: *we do not fill forms with AI; we use AI to build
form-fillers, once, and a human signs off.*

This repository is one half of a two-part system. The other half (**Warrant**) decides *which*
forms an estate needs and supplies verified facts; Forge decides *how* an unfamiliar form gets
filled. The seam is plain JSON on disk — see **[MERGE.md](MERGE.md)** before merging.

---

## Quickstart

```bash
git clone https://github.com/Fluxxion88/Forge.git
cd Forge

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # the [dev] extra is what brings in pytest

forge inspect --all          # phase 0 gate: 4 forms, exact field counts, prints PASS
```

> Use `.[dev]`, not a bare `.`. With a plain `pip install -e .` the `pytest` on your PATH is
> whichever one your system has, it will not have Forge importable, and every test file errors
> on collection. Verified by cloning this repo into a clean venv both ways.

You also need **poppler** for `pdftoppm` (rasterising is how every result gets verified):

```bash
brew install poppler          # macOS
# apt-get install poppler-utils on Debian/Ubuntu
```

Then start the review UI and open the URL it prints on its own line:

```bash
forge review --port 8078
# http://127.0.0.1:8078/?form=irs-f56&estate=estate-05-in-formal-probate
```

That URL is a six-tab walkthrough: **ESTATE → FORMS NEEDED → REVIEW → REUSE →
SELF-CORRECTION → SPONSOR RUNTIME**. Hovering a row in REVIEW highlights the exact box that
field fills on the rendered page.

Optional, for the Anvil path only: copy `.env.example` to `.env` and fill it in. Everything
except `forge fill --via anvil` works without it.

```bash
pytest -q                     # 74 tests
```

## CLI surface

Every subcommand prints machine-checkable output and exits non-zero on failure.

| Command | What it does |
|---|---|
| `forge inspect <form> \| --all` | Enumerate a form's AcroForm fields; print counts and types. The phase 0 gate. |
| `forge mock-workorder --estate <id> \| --all` | Stand in for Warrant: write a work order from the applicability flags already in the estate data. **Delete on merge.** |
| `forge calibrate <form>` | Sentinel pass + vision: discover what each opaque field means. Writes `artifacts/calibration/<form>.json`. Costs model calls. |
| `forge calibrate <form> --geometry-only` | No model: backfill measured page boxes and per-widget rectangles into an existing calibration, leaving every label untouched. |
| `forge propose <form> --estate <id>` | One pass: synthesise a binding, write the draft, fill and rasterise once. No critique, no rounds. |
| `forge propose <form> --estate <id> --from-draft` | No model: re-validate, re-fill and re-render the existing draft (use after editing rows in the UI). |
| `forge bind <form> --estate <id>` | The convergence loop: fill → render → critique the **image** → repair → repeat. `--naive`, `--max-rounds N`, `--label X` to isolate a run's outputs. Expensive; never run live in a demo. |
| `forge fill <form> --estate <id>` | Fill from the **approved** binding. Asserts zero model calls. `--binding-version N` pins an exact version; `--via anvil` uses the sponsor runtime. |
| `forge review --port 8078` | Serve the walkthrough / approval UI on localhost. |
| `forge reuse-proof --binding-version N` | Fill every estate from one binding and build `out/demo/reuse.md` plus the comparison strip. No model. |
| `forge anvil-register <form> [--binding-version N]` | Upload the blank PDF to Anvil as a published cast and record it in `artifacts/anvil/`. |
| `forge bench` | Run every applicable (estate, form) pair; write `out/reports/benchmark.{json,md}`. |
| `forge demo` | Assemble the demo assets under `out/demo/`. |

## What lives where

| Directory | Contents |
|---|---|
| `inputs/forms/` | The four blank PDFs, **byte-identical fixtures — never edit**. |
| `inputs/estates/` | The five sample estate records, same schema, synthetic data. |
| `artifacts/calibration/<form>.json` | What each opaque field *means*: item number, printed label, plain-English meaning, confidence, per-widget rectangles, measured page geometry. Machine-produced, human-checkable. |
| `artifacts/bindings/<form>.json` | The **draft** binding — data paths mapped onto fields. Editable, not yet approved. |
| `artifacts/approved/<form>.v<N>.json` | The **frozen** binding. Immutable, `chmod 0444`, carries `approvedBy` / `approvedAt`. This is what `forge fill` uses and what Warrant reads. |
| `artifacts/workorders/<estateId>.json` | Warrant → Forge: which forms this estate needs, and why the others are skipped. |
| `artifacts/anvil/<formId>.json` | The published Anvil cast id, tied to the sha256 of the binding it was registered from. Separate file because approved artifacts are immutable. |
| `out/fills/` | Filled PDFs and their sidecars (`llmCallsAtRuntime`, timing, and every blank field with the data path that would fill it). |
| `out/renders/` | Rasterised pages. Verification is done by looking at these — never by reading values back, and never with `pdftotext`. |
| `out/reports/` | Benchmark, loop histories, `decisions.md`, and every raw model prompt/reply under `calls/`. |
| `out/demo/` | Demo assets: `RUNBOOK.md`, `reuse.md`, `loop-history.md`, `anvil.md` and their images. |

## Docs, in reading order

1. **[docs/00-DOMAIN.md](docs/00-DOMAIN.md)** — the four forms, the five estates, and three PDF
   traps verified by experiment (XFA hybrids, per-field checkbox on-values, duplicate short
   names). Every number was counted, not estimated; if code disagrees with it, the code is wrong.
2. **[docs/01-CONTRACT.md](docs/01-CONTRACT.md)** — the seam with Warrant. Who decides what.
3. **[docs/02-SPEC.md](docs/02-SPEC.md)** — the engine: calibration, the binding language (six
   source kinds), the convergence loop, review, fill.
4. **[docs/03-ANVIL.md](docs/03-ANVIL.md)** — the sponsor runtime. Read `out/demo/anvil.md`
   alongside it; two documented behaviours did not survive contact with the live API.
5. **[docs/04-BUILD-PLAN.md](docs/04-BUILD-PLAN.md)** — phases and their gates.
6. **[docs/05-AUTONOMOUS-RUN.md](docs/05-AUTONOMOUS-RUN.md)** — how the unattended build was run.
7. **[CLAUDE.md](CLAUDE.md)** — the project constitution. Seven hard rules; read it first if you
   are going to change anything.
8. **[MERGE.md](MERGE.md)** — what Warrant writes, what it may read, and how to delete the mock.

Also worth reading: **`out/reports/decisions.md`** — every judgement call made during the build,
with its reason. And **`out/demo/RUNBOOK.md`** — the exact demo sequence, pinned to
`--binding-version 3`.

## Current status — honestly

| | State |
|---|---|
| **irs-f56** (Form 56) | **Approved at v3.** 67 of 72 fields bound. Proven across all five estates: zero rule violations, zero model calls at fill time, ~40 ms each. v1 is retained in `artifacts/approved/` along with its failing report, because the defects it shipped with are part of the story. |
| **irs-f8821** (Form 8821) | **Draft, awaiting approval.** 39 of 45 bound. One proposal pass, no convergence loop run against it. |
| **irs-ss4** (Form SS-4) | **Draft, awaiting approval.** 77 of 89 bound, 39 of those flagged low confidence. One proposal pass, no loop. |
| **ca-dmv-dl142** (DL 142) | **Calibrated only — no binding at all.** 28/28 fields labelled; nothing maps data onto it, so Forge will refuse to produce it. |
| **Crop escalation** | **Never executed.** 0 of 234 fields across all four forms triggered it; every page resolved on the whole-page pass. The code path is untested in practice and should be expected to break the first time a form needs it. |
| **Accuracy** | **Not measured, and not claimed.** No human has compared a filled render against the blank form field by field. An unverified accuracy number is exactly the failure this project exists to prevent. |
| **Anvil** | Live and working on Form 56 (the XFA hybrid), including the reconciliation refusal. Not exercised on DL 142, whose duplicate short field names would need a qualified-name mapping first. |
| **Two enum-literal risks** | 10 literals on Form 56 items 3–4 are unverified against a schema (no estate exercises them) and carry `confidence: low`. The referenced `estate-form-data.schema.json` is not in `inputs/`. |

Three defects in the first approved binding were found only by running it across all five
estates rather than one, and all three were the same species: a literal or a guard too narrow.
That is why `forge reuse-proof` exists and why `exclusiveGroups` are checked on every fill.
