# MERGE.md — combining Forge with Warrant

Written for the merge on 2026-07-28. Everything the other repository needs is plain JSON
on disk; no Forge Python module is imported across the seam.

## What Warrant must write

One work order per estate at `artifacts/workorders/<estateId>.json`:

```json
{
  "estateId": "estate-05-in-formal-probate",
  "estatePath": "inputs/estates/estate-05-in-formal-probate.json",
  "jurisdiction": { "state": "IN", "county": "Marion" },
  "route": "FORMAL_PROBATE",
  "generatedAt": "2026-07-27T22:10:00Z",
  "forms": [
    { "formId": "irs-f56", "applicable": true, "reason": null,
      "priority": 1, "blastRadius": "high", "reversibility": "irreversible" }
  ]
}
```

- `formId` registry is fixed: `irs-f56`, `irs-ss4`, `irs-f8821`, `ca-dmv-dl142`
  (`src/forge/registry.py::FORMS`).
- `reason` required when `applicable` is false; it is rendered in the review UI.
- `blastRadius`/`reversibility` are consumed by the review UI only.

## What Warrant may read

Approved bindings at `artifacts/approved/<formId>.v<N>.json` (highest N wins; files are
immutable, mode 0444). Schema: docs/02-SPEC.md §2.1 — including the `when` guard and
`exclusiveGroups`, both added 2026-07-27. A form with no approved binding has not been
compiled; `forge fill` exits non-zero rather than falling back to a draft.

Fill sidecars at `out/fills/<estateId>-<formId>.json` carry
`llmCallsAtRuntime` (measured by the counter in `src/forge/llm.py`, asserted zero) and
the `empty[]` report of what was left blank and why.

## Deleting the mock

`warrant_mock.py` stand-in is referenced from exactly these places:

- `src/forge/warrant_mock.py` — the module itself; delete.
- `src/forge/cli.py` — `cmd_mock_workorder` and the `mock-workorder` subparser; delete both.
- `tests/test_warrant_mock.py` — replace with tests against real Warrant output, keeping
  `test_work_order_shape` (it validates the interface shape, not the mock).
- `src/forge/registry.py::load_work_order` stays — it reads whatever wrote the file.

Estate-path resolution (`src/forge/estatepath.py`) returns `{value, path, present, reason}`;
add Warrant's `verdict` as a new field on `Resolution`, not a refactor.

## Paths that must not move

```
inputs/forms/          blank PDFs, byte-identical fixtures shared with Warrant
inputs/estates/        sample estates, ditto
artifacts/workorders/  Warrant → Forge
artifacts/approved/    Forge → Warrant
out/fills/             filled PDFs + sidecars
out/renders/           rasterised verification images
```

## Commands the other side may call

```
forge inspect --all
forge fill <formId> --estate <estateId> [--via anvil]
forge bench
forge review
```

All print machine-checkable output and exit non-zero on failure. Nothing else in the
CLI is part of the contract.
