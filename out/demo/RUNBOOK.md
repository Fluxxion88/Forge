# Demo runbook

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
