# MERGE.md — combining Forge with Warrant

Written for the merge on 2026-07-28, and revised at the end of the build night — the binding
schema and the CLI both moved after the first draft of this file. Everything the other
repository needs is plain JSON on disk; no Forge Python module is imported across the seam.

## What Warrant must write

One work order per estate at `artifacts/workorders/<estateId>.json`:

```json
{
  "estateId": "estate-05-in-formal-probate",
  "estatePath": "inputs/estates/estate-05-in-formal-probate.json",
  "jurisdiction": { "state": "IN", "county": "Marion" },
  "route": "FORMAL_PROBATE",
  "generatedAt": "2026-07-28T02:00:31Z",
  "generatedBy": "warrant_mock",
  "forms": [
    { "formId": "irs-f56", "applicable": true, "reason": null,
      "priority": 2, "blastRadius": "high", "reversibility": "irreversible" },
    { "formId": "ca-dmv-dl142", "applicable": false,
      "reason": "The decedent held an Indiana driver licence. DL 142 is a California DMV form…",
      "priority": null, "blastRadius": null, "reversibility": null }
  ]
}
```

- `formId` registry is fixed: `irs-f56`, `irs-ss4`, `irs-f8821`, `ca-dmv-dl142`
  (`src/forge/registry.py::FORMS`).
- `reason` is **required** when `applicable` is false. It is rendered verbatim on the FORMS
  NEEDED tab of the review UI, so write it as a sentence a non-engineer will read aloud.
- `generatedBy` is displayed in the UI as the source of the needed/skipped decision. The mock
  writes `"warrant_mock"`; write your own identifier.
- `blastRadius` (`low|medium|high`) and `reversibility` (`reversible|irreversible`) are consumed
  by the review UI only, and are shown translated into plain English. Forge never computes them.
- Every form in the registry should appear, including the skipped ones — the UI lists all four
  and a missing entry renders as unknown rather than as "not needed".

## What Warrant may read

**Approved bindings** at `artifacts/approved/<formId>.v<N>.json`. Highest N wins; files are
immutable and mode `0444`. A form with no approved binding has not been compiled, and
`forge fill` exits non-zero rather than falling back to a draft.

Schema is `docs/02-SPEC.md` §2.1. Two changes landed on 2026-07-27, after this file was first
written:

- **Six source kinds, not five.** `path`, `constant`, `template`, `condition`, **`contains`**,
  `absent`. `contains` is `{path, includes}` and marks a checkbox when the **array** at `path`
  holds `includes`. It was added because Form 56 items 3 and 4 store "check all that apply"
  answers as arrays, and scalar `condition` against a list is never true — fifteen checkboxes
  had been bound, validated, and were unmarkable on every estate.
- **The `when` guard has two shapes.** `{path, equals}` as before, and now
  `{path, equalsAny: [...]}`. Form 56 line 2a applies on the 1a/1b/1d branch and 2b on the
  1c/1e/1f/1g branch — three and four values respectively, which one literal cannot express.

A binding object carries `qualifiedName, itemNumber, label, source, format, onValue, required,
confidence, note, when`. Approved artifacts may additionally carry `supersedes` and `changeLog`
(what changed from the version they replace) — both are informational.

**Fill sidecars** at `out/fills/<estateId>-<formId>.json`, or
`out/fills/<estateId>-<formId>-anvil.json` for a `--via anvil` run — the suffix is on the
sidecar as well as the PDF, so the two paths can be compared rather than overwriting each other.
Each carries `llmCallsAtRuntime` (measured by the counter in `src/forge/llm.py`, asserted zero),
`bindingVersion`, timings, `groupViolations`, and the `empty[]` report naming what was left
blank and the data path that would have filled it.

**Calibration** at `artifacts/calibration/<formId>.json` is not part of the contract, but note
it now carries `pages[]` (measured `cropBox`, `mediaBox`, `widthPt`, `heightPt`, `rotate`) and
per-field `widgets[]`. The review UI needs these to place its highlight overlay. Regenerate them
without spending model calls with `forge calibrate <form> --geometry-only`.

**Anvil cast registry** at `artifacts/anvil/<formId>.json` — the published cast eid tied to the
sha256 of the binding it was registered from. It lives outside the approved artifact because
approved artifacts are immutable.

## Deleting the mock

`warrant_mock.py` stand-in is referenced from exactly these places:

- `src/forge/warrant_mock.py` — the module itself; delete.
- `src/forge/cli.py` — `cmd_mock_workorder` and the `mock-workorder` subparser; delete both.
- `tests/test_warrant_mock.py` — replace with tests against real Warrant output, keeping
  `test_work_order_shape` (it validates the interface shape, not the mock).
- `src/forge/registry.py::load_work_order` stays — it reads whatever wrote the file.

Estate-path resolution (`src/forge/estatepath.py`) returns `{path, value, present, reason}`;
add Warrant's `verdict` as a new field on `Resolution`, not a refactor.

## Paths that must not move

```
inputs/forms/          blank PDFs, byte-identical fixtures shared with Warrant
inputs/estates/        sample estates, ditto
artifacts/workorders/  Warrant → Forge
artifacts/approved/    Forge → Warrant
artifacts/anvil/       Anvil cast registry, keyed to a binding's sha256
out/fills/             filled PDFs + sidecars
out/renders/           rasterised verification images
```

## Commands the other side may call

```
forge inspect --all
forge fill <formId> --estate <estateId> [--binding-version N] [--via anvil]
forge bench
forge review [--port 8078]
```

`--binding-version N` pins an exact approved version instead of taking the highest. Use it
anywhere reproducibility matters: re-producing a filing months later means filling with the
artifact that was approved then, not whatever is newest.

All print machine-checkable output and exit non-zero on failure. Nothing else in the CLI is part
of the contract — `propose`, `bind`, `calibrate`, `reuse-proof`, `anvil-register` and `demo` are
build-time tools.

## Two behaviours worth knowing before you touch approval

- **The UI reads the approved artifact by default**, and shows a draft only on `?draft=1`.
  Immediately after approval the draft is a byte-copy of the approved file, so a UI that
  defaults to the draft invites an operator to press Approve and mint a version differing only
  by timestamp. That is exactly how `irs-f56` v1 and v2 both came to exist.
- **`approve()` refuses a no-op**, raising when the draft's `bindings`, `unbound` and
  `exclusiveGroups` all match the newest approved version.

## State at merge time

`irs-f56` is approved at **v3** (67/72 bound) and proven across all five estates.
`irs-f8821` (39/45) and `irs-ss4` (77/89) are **drafts awaiting human approval** — they will not
fill until someone approves them. `ca-dmv-dl142` is calibrated but has **no binding at all**.
`forge bench` distinguishes these three states explicitly. Full status is in
[README.md](README.md#current-status--honestly).
