# Forge — project constitution

Read this file first, every session. Then read `docs/` in numeric order before writing any code.

## What this is

Forge is a **compiler for government forms**. It takes a blank PDF whose fields are
machine-named and meaningless, and produces a reviewed, versioned, reusable artifact that
maps structured estate data onto that form. A human approves the artifact once. After that,
filling the form is deterministic and involves no model.

Built for the Alix "Agents of Administration" hackathon, Track 3, San Francisco, 27–28 July 2026.

This repository is one half of a two-part system. The other half (`Warrant`, built separately)
decides **which** forms an estate needs and supplies verified facts. Forge decides **how** an
unfamiliar form gets filled. The seam between them is specified in `docs/01-CONTRACT.md` and
must not be crossed casually — we merge the two repositories tomorrow and every deviation from
the contract becomes merge pain.

## The one idea

Everyone else at this hackathon will ask a model to fill a form at request time. That model
will be wrong roughly one time in ten, on every estate, forever, and nobody will be watching.

Forge spends the model **once per form, at build time**, under supervision, and freezes the
result. At request time there is no model, no prompt, no temperature and no variance. The error
is caught once by a human and never recurs.

Say it in one line: *we do not fill forms with AI; we use AI to build form-fillers, once, and a
human signs off.*

## Hard rules

These are not style preferences. Violating any of them breaks the thesis of the project.

1. **No model call in the fill path.** Calibration and binding synthesis may call a model.
   `forge fill` must not, and asserts this at runtime by counting calls. There is a test.
2. **Verify empirically, never by assertion.** A step is done when a command prints evidence,
   not when the code looks correct. Rendering a PDF and looking at the raster is verification.
   Reading back the value you just wrote is not.
3. **Never verify a filled PDF with `pdftotext`.** Text extraction can report a field's value
   dictionary even when no viewer draws anything on the page. Rasterize with `pdftoppm` and
   inspect the image.
4. **Unknown is not false.** A field with no supporting data is left empty and reported, with
   the specific data path that would fill it. Never invent a plausible value.
5. **Ambiguity is an output.** If the binding for a field cannot be determined confidently, that
   is a result to surface, not a coin to flip. Confident and wrong is the worst outcome.
6. **Artifacts are data, not code.** A binding is JSON. Never generate Python or TypeScript as
   the reusable artifact — a product manager has to review it, and nobody reviews generated code.
7. **Every artifact is versioned and attributed.** `version`, `approvedBy`, `approvedAt`,
   `sourceFormSha256`. An unattributed artifact is not approved.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Compiler | Python 3.11+ | `pypdf` and `pdftoppm` are where the PDF work actually is |
| PDF read/write | `pypdf` | AcroForm enumeration and value writing |
| Rasterise | `pdftoppm` (poppler) | Ground truth for verification |
| Review UI | FastAPI + one HTML page | Local, no build step, no framework |
| Artifacts | JSON on disk | Language-agnostic, so the TypeScript side reads them directly |
| Runtime fill | `pypdf` locally, Anvil over API | Two paths, same binding |

Do not add a frontend framework. Do not add a database. Do not add an ORM. The artifacts are
files; that is the whole persistence layer, and it is what makes tomorrow's merge trivial.

## Layout

```
CLAUDE.md
docs/                     specification, read in order
inputs/
  forms/                  the four blank PDFs, unmodified
  estates/                the five sample estate JSON files
artifacts/
  calibration/<form>.json field geometry and meaning, machine-produced
  bindings/<form>.json    draft binding, machine-produced
  approved/<form>.v1.json frozen binding, human-approved
out/
  fills/                  filled PDFs
  renders/                rasterised pages used for verification
  reports/                benchmark output
src/forge/
  inspect.py  calibrate.py  bind.py  loop.py  fill.py  review.py  bench.py
  warrant_mock.py         stand-in for the other half — see docs/01-CONTRACT.md
tests/
```

## CLI surface

Build these as subcommands of a single `forge` entry point. Every one prints machine-checkable
output and exits non-zero on failure.

```
forge inspect  <form>                 enumerate fields; print counts and types
forge calibrate <form>                sentinel pass; write artifacts/calibration/<form>.json
forge bind     <form> --estate <id>   synthesise binding; run convergence loop
forge fill     <form> --estate <id>   fill using approved binding; asserts zero model calls
forge review                          serve the approval UI on localhost
forge bench                           run every applicable (estate, form) pair; write report
```

## Definition of done, per step

A step is complete when the gate command in `docs/04-BUILD-PLAN.md` prints `PASS`. Not before.
If a gate cannot pass, stop and report why rather than proceeding to the next phase — a broken
foundation with three phases built on top costs more than an honest stop.

## Tone of the work

We have until 15:30 tomorrow. Prefer a narrow thing that demonstrably works over a broad thing
that plausibly works. When you have a choice between adding a capability and proving an existing
one, prove the existing one.
