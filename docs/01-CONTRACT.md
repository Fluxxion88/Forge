# 01 — Contract with Warrant

This repository is one half of a system. The other half is called **Warrant** and is being built
in parallel in a separate repository. We merge tomorrow. This file is the interface between them.

Build the mock in `src/forge/warrant_mock.py`. Build everything downstream against the mock.
Tomorrow the mock is deleted and the real thing writes the same file. Nothing else changes.

## Division of responsibility

| Question | Owner |
|---|---|
| What did the documents actually say, and can we prove it? | Warrant |
| Which procedure applies to this estate, and on what legal authority? | Warrant |
| Which forms must be produced, and in what order? | Warrant |
| Is a given form applicable to this estate at all? | Warrant |
| What are the verified values available to fill with? | Warrant |
| How does an unfamiliar form get filled at all? | **Forge** |
| Which data path feeds which box on the paper? | **Forge** |
| Which checkbox value marks which option? | **Forge** |
| What does the human approve, and how is it frozen? | **Forge** |
| Producing the final PDF | **Forge** |

The line in one sentence: **Warrant decides what and why. Forge decides how.**

Do not implement legal reasoning in this repository. Do not add a rules engine, a probate route
selector, a statute table, or a threshold check. If you find yourself wanting one, you have
crossed the seam — read the flag from the work order instead.

## The interface: a work order

Warrant writes one JSON file per estate. Forge reads it and does what it says.

Path: `artifacts/workorders/<estateId>.json`

```json
{
  "estateId": "estate-05-in-formal-probate",
  "estatePath": "inputs/estates/estate-05-in-formal-probate.json",
  "jurisdiction": { "state": "IN", "county": "Marion" },
  "route": "FORMAL_PROBATE",
  "generatedAt": "2026-07-27T22:10:00Z",
  "forms": [
    {
      "formId": "irs-f56",
      "applicable": true,
      "reason": null,
      "priority": 1,
      "blastRadius": "high",
      "reversibility": "irreversible"
    },
    {
      "formId": "ca-dmv-dl142",
      "applicable": false,
      "reason": "The decedent held an Indiana driver licence. DL 142 is a California DMV form.",
      "priority": null,
      "blastRadius": null,
      "reversibility": null
    }
  ]
}
```

### Field meanings

- `formId` — stable slug. The registry is fixed: `irs-f56`, `irs-ss4`, `irs-f8821`, `ca-dmv-dl142`.
- `applicable` — Forge produces the form if and only if this is `true`.
- `reason` — required when `applicable` is `false`. Render it in the review UI so a human sees
  why a form was skipped rather than wondering whether it was forgotten.
- `priority` — filing order. Lower goes first. Display only; Forge does not enforce sequencing.
- `blastRadius` (`low` | `medium` | `high`) and `reversibility` (`reversible` | `irreversible`) —
  set by Warrant, consumed by the review UI to decide how loudly to demand human attention.
  Forge never computes these.

## The mock

`warrant_mock.py` derives a work order from a sample estate file using only the applicability
flags that are already in the data. It performs no legal reasoning whatsoever — it reads
`formDL142.applicable`, checks whether `estateEntity.ein` is null to decide whether SS-4 is a
live application, and copies `notApplicableReason` straight through.

```
forge mock-workorder --estate estate-05-in-formal-probate
```

Writes `artifacts/workorders/estate-05-in-formal-probate.json`.

Rules for the mock:

- It must be under 100 lines. If it grows past that, legal reasoning is leaking in — stop.
- It must set `blastRadius` and `reversibility` from a small hardcoded table per form, with a
  comment saying these are placeholders that Warrant will replace.
- Its output must validate against the same shape as the real thing, so the swap is a deletion.

## Data access

Forge reads estate values by JSON path against the estate file named in the work order:

```
fiduciary.name.full
decedent.residenceAddress.city
form56.signature.title
taxMatters.authorizationRows[0].taxFormNumber
```

Implement a small, strict path resolver. Two requirements:

- A path that does not resolve returns a sentinel meaning **absent**, never `None` conflated with
  an empty string, and never a guess.
- Every resolution is recorded, so the review UI can show a field's source path next to its value.

When Warrant is merged, values will additionally carry a verification verdict. Design the
resolver's return type as a small object now — `{ value, path, present }` — so adding
`verdict` tomorrow is a field, not a refactor.

## What crosses the seam in the other direction

Forge writes approved bindings to `artifacts/approved/<formId>.v<N>.json`. Warrant reads that
directory to know which forms it can produce without human involvement. Keep the format stable
and documented in `docs/02-SPEC.md`; it is the thing the other repository will import.

## Merge posture

Tomorrow the two repositories are combined. To make that cheap:

- Keep every Forge artifact as plain JSON on disk. No database, no pickles, no in-memory state
  that matters across runs.
- Keep the Python surface behind the `forge` CLI. The other side calls a command or reads a file;
  it never imports a Python module.
- Never edit files under `inputs/`. They are shared fixtures and must stay byte-identical.
