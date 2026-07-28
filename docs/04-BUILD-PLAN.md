# 04 — Build plan

Six phases. Each ends with a gate: a command whose output is checked. **Do not start a phase
until the previous gate prints PASS.** If a gate cannot pass, stop and report why. Three phases
built on a broken foundation cost more than an honest stop at phase one.

Build phases 0–2 in one continuous pass if you can. Stop and hand back at the phase 2 gate so a
human can look at a real rendered form before more is built on top of it.

---

## Phase 0 — Skeleton and enumeration

Repository layout per `CLAUDE.md`. The `forge` CLI with subcommand stubs. `pypdf` and poppler
available. Field enumeration handling qualified names, `/FT` filtering, and button `onValue`
discovery.

Also: `warrant_mock.py` and `forge mock-workorder`, per `docs/01-CONTRACT.md`.

**Gate**

```
forge inspect --all
```

Must print, exactly:

```
irs-ss4        93 fields   45 text   44 button    0 tooltips
irs-f56        76 fields   39 text   33 button    0 tooltips
irs-f8821      52 fields   33 text   12 button    5 tooltips
ca-dmv-dl142   51 fields   14 text   14 button   28 tooltips
PASS
```

Any deviation means the enumeration is wrong — most likely short-name collisions on DL 142 or
counting container nodes that have no `/FT`. Fix it here. Every later stage inherits this error.

Also assert: at least one button on Form 56 reports `onValue` of `/2`, and none report `/Yes`.

---

## Phase 1 — Calibration

Sentinel pass, rasterisation, semantic pass, calibration artifact. Per `docs/02-SPEC.md` §1.

Start with `ca-dmv-dl142` — one page, no XFA, readable field names. It is the cheapest way to
find out whether the sentinel technique works at all. Then `irs-f56`.

**Gate**

```
forge calibrate irs-f56
```

Must print the resolution percentage and `PASS` at ≥90%, and leave on disk:

- `artifacts/calibration/irs-f56.json`
- `out/renders/irs-f56/sentinel-text-page-0.png`
- `out/renders/irs-f56/sentinel-btn-page-0.png`

**A human looks at those two images before phase 2 begins.** If the sentinels are not visibly
rendered on the page, the XFA/appearance handling in `docs/00-DOMAIN.md` §3.1 is wrong and
everything downstream is built on sand. This is the single most important checkpoint in the
project.

---

## Phase 2 — Binding and the convergence loop

Draft binding synthesis, fill, render, image-based critique, repair, iterate. Per
`docs/02-SPEC.md` §2. Calibration estate: `estate-05-in-formal-probate`.

**Gate**

```
forge bind irs-f56 --estate estate-05-in-formal-probate
```

Must converge to zero findings within 6 rounds, print the round count and `PASS`, and leave the
per-round renders and `out/reports/irs-f56-loop.json` on disk.

Then stop and hand back. A human reads the final render against the blank form.

---

## Phase 3 — Review and freeze

The FastAPI review page per `docs/02-SPEC.md` §3. Approval, versioning, promotion to
`artifacts/approved/`, immutability of approved versions.

**Gate**

Approve `irs-f56` in the UI, then:

```
forge fill irs-f56 --estate estate-05-in-formal-probate
```

Must produce a PDF and a sidecar report whose `llmCallsAtRuntime` is `0`, and must complete in
under a second. Then run it again against a **different** estate — `estate-01` — using the same
approved binding, and confirm it produces a different, correctly populated document with no
recompilation.

That second run is the proof of reuse. It is also the moment in the demo that lands.

---

## Phase 4 — Anvil

Per `docs/03-ANVIL.md`. Register `ca-dmv-dl142` first, then `irs-f56`. Reconciliation before every
fill.

**Gate**

```
forge fill ca-dmv-dl142 --estate estate-02-ca-intestate-independent-admin --via anvil
```

Must return real PDF bytes from Anvil, written as binary, and the rasterised result must show the
same populated fields as the local path.

Also demonstrate the failure mode deliberately: introduce an alias the cast does not have and
confirm reconciliation refuses the fill rather than producing a PDF with a hole in it.

---

## Phase 5 — Benchmark

Per `docs/02-SPEC.md` §5. Every applicable pair from the work orders, honest denominators from
`docs/00-DOMAIN.md` §5.

**Gate**

```
forge bench
```

Writes `out/reports/benchmark.md` with every column populated and `llmCallsAtRuntime` reading 0
across every row.

Include the headline run: the same `irs-f56` binding across all five estates, showing that the
executor / administrator / trustee and testate / intestate checkboxes differ per estate while the
binding does not change.

---

## Priority if time runs short

In this order. Cut from the bottom.

1. Phase 0–2 on `irs-f56` alone. Without a converging loop there is no project.
2. Phase 3. Without the review step it is just another AI form filler.
3. One live Anvil fill on `ca-dmv-dl142`. This is worth $1,000 and a judge's attention.
4. Phase 5 benchmark. Impact is the largest judging weight at 30%; an unmeasured demo cannot
   score there.
5. Remaining forms. Breadth is the least valuable thing on this list.

## Standing instructions

- Write tests as you go, not at the end. Anything that touches PDF bytes gets a fixture test
  against the real files in `inputs/forms/`.
- Never edit anything under `inputs/`. Those files are shared with the other repository and must
  stay byte-identical.
- When a model call is added anywhere, wire it through the single counted client. The runtime
  zero-call assertion depends on there being exactly one path to a model.
- Commit at every gate, with the gate output in the commit message. Tomorrow's merge is much
  easier against a history of verified checkpoints than one large commit.
- If you find yourself implementing legal reasoning — thresholds, probate routes, statutes — stop.
  That belongs to the other half of the system. Read the work order flag instead.
