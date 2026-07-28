# Demo runbook — Forge

Three minutes. Every live command below runs in under two seconds. Nothing that calls a
model is ever run live: calibration is ~83 s and a single critique round is ~2-4 min.
Their outputs are pre-rendered and on disk.

The approved binding is **v3**. Every `forge fill` below pins it with
`--binding-version 3` rather than taking whatever is highest — so the demo cannot change
under you if someone approves a v4 between now and the slot.

## Before the slot (once)

    cd <repo root>
    source .venv/bin/activate
    forge inspect --all                # 4 forms, exact field counts, PASS
    forge review --port 8078           # leave running; open the URL it prints

## Live sequence

**1. The problem.** Open `inputs/forms/Form 56 June 2026.pdf`, click any box, show the
field name: `topmostSubform[0].Page1[0].f1_04[0]`. 76 fields, zero tooltips. Nothing in
the file says what any of them mean. Today a human closes that gap by hand, per form.

**2. The compiled artifact.**

    less artifacts/approved/irs-f56.v3.json

Point at one binding (a data path onto a box), a `when` guard with `equalsAny`, an
`exclusiveGroups` entry, and `approvedBy` / `approvedAt`. Say: *a human approved this
once; it is data, not code; no model runs from here on.* Then point at `changeLog` — the
three defects v1 shipped with, and what fixed them.

**3. The review UI** (already open). Hover any row — the box it fills lights up on the
rendered form. Click to pin. Every value on the paper traces to a named path, and you
can see which box.

**4. The fill.**

    forge fill irs-f56 --estate estate-03-oh-trust-administration --binding-version 3
    cat out/fills/estate-03-oh-trust-administration-irs-f56.json

Point at `llmCallsAtRuntime: 0` — a counter wired into the model client, with a test that
fails if it is ever non-zero. Then `elapsedMs`: about 40 ms.

**5. Reuse — the headline.**

    open out/demo/reuse.md
    open out/demo/reuse-section-a.png

One binding, five estates, five jurisdictions: line 1 ticks 1a / 1b / 1e differently,
line 2a and 2b swap on the guard, the fiduciary title changes across four values. **The
binding file is byte-identical** — its sha256 is in the report. Zero `exactlyOne`
violations. Cold cost 485 s once per form; warm cost ~40 ms per estate, forever.

**6. Honesty.** In the step-4 sidecar, the `empty` array: every blank field names the
data path that would fill it. Unknown is not false. Then:

    open out/demo/reuse-v1.md

The same five-estate run over the **first** approved binding: two estates with no
authority box ticked, from one wrong enum literal. The deterministic `exclusiveGroups`
check caught it before anything was filed. That is the system reporting its own defect —
kept on disk on purpose.

**7. The loop's history.**

    open out/demo/loop-history.md

Round 1's four findings, read off the rendered image rather than the JSON that produced
it; round 3 clean. Started from a deliberately naive proposal. Never run live.

**8. Anvil, the sponsor path.**

    forge fill irs-f56 --estate estate-05-in-formal-probate --via anvil --binding-version 3
    open out/demo/anvil.md

Same artifact, Anvil executes it — on the XFA hybrid, not the easy form. All 72 fields
detected. Then the catch:

    open out/demo/anvil-drift/before-the-hole.png

A renamed field: 31 of 32 values delivered, HTTP 200, 156 KB of valid PDF with the
date of death missing. Then reconciliation on — refuses, zero fill requests sent, no
file written.

**9. Close.**

    open out/reports/benchmark.md

14 applicable pairs, build cost paid once per form, model calls at fill time **0**
everywhere, measured. And the accuracy line: we do not claim a number a human has not
checked.

## Regenerating the assets (not live)

    forge reuse-proof --binding-version 3     # out/demo/reuse.md + strip, ~1 s
    forge bench                               # out/reports/benchmark.{json,md}
    forge demo                                # assembles out/demo/
    pytest -q                                 # 72 tests

Model-calling steps, for reference only — do not run these on stage:

    forge calibrate irs-f56                                    # ~83 s, 2 calls
    forge bind irs-f56 --estate estate-03-oh-trust-administration \
        --naive --max-rounds 4 --label naive-estate03           # ~11 min, 6 calls

---

# Appendix — the original phase-ordered runbook

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
