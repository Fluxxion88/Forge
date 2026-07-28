# 05 — Autonomous run

Phases 0 and 1 are done. The one checkpoint that genuinely required human eyes — confirming
sentinels render on an XFA hybrid — has passed. Everything downstream is machine-checkable.

So this document replaces the stop-at-every-gate posture with standing authority. Work the queue
in section 6 from top to bottom in one continuous run. Do not stop to report progress. Do not ask
permission to proceed between phases. Report once, at the end, or when something in section 5
happens.

---

## 1. Standing authority

You may, without asking:

- Move between phases when a gate passes.
- Install packages, create virtualenvs, run migrations, change build tooling.
- Refactor anything under `src/` and `tests/`.
- Amend `docs/` when reality contradicts them — the docs are normative for tomorrow's merge, so
  keep them true rather than keeping them frozen. Record the change and the reason.
- Spawn subagents for parallel work (section 4).
- Retry, back off, change approach, and try a different technique when something fails.
- Delete and rewrite your own earlier work if it is wrong.

You may not, without asking:

- Edit anything under `inputs/`. Those files are shared with the other repository.
- Weaken a hard rule in `CLAUDE.md` to make a gate pass.
- Commit an API key, or print one in a log or error.
- Fabricate a measurement. An unverified number is worse than a missing one.

## 2. When you are blocked, do not stop — decide

The failure mode we are eliminating is stopping to ask. When you hit a fork, resolve it with
these rules and write down what you chose in `out/reports/decisions.md`.

| Fork | Rule |
|---|---|
| A model call fails | Retry once. Then retry with a smaller batch. Then skip the item, record it as unresolved, and continue. Never let one call kill a run. |
| A call times out | Halve the batch and retry. If a batch of one still times out, the transport is the problem — switch to the Anthropic API with base64 image blocks and carry on. |
| The loop will not converge in 6 rounds | Stop the loop, keep the history, mark the remaining findings as open, and move to the next form. A form with three open findings is a result. Spinning is not. |
| A field cannot be bound | It goes to `unbound` with what would fill it. Never guess. Never invent a plausible path. |
| Spec and reality disagree | Reality wins. Fix the code to match reality, fix the spec to match the code, note it in `decisions.md`. |
| Two designs both look defensible | Take the one that is easier to verify. Verifiability beats elegance every time on this project. |
| Something is ambiguous but low blast radius | Choose, note it, move on. |
| Something is ambiguous and high blast radius | Section 5. |
| You are running out of time | Section 6 is ordered by priority. Cut from the bottom, never from the middle. |

## 3. Verification protocol

Every claim you make at the end must be backed by an artifact on disk.

- A number in your report points to the file it came from.
- "It works" means a command was run and its output is saved.
- A filled PDF is verified by rasterising and looking at the image, never by reading back the
  value you just wrote, never by `pdftotext`.
- The critique in the loop compares the calibration's `printedLabel` — obtained from the blank
  form, independent of the binding — against what actually landed in the box. It must never
  compare the binding against itself.
- Preserve every model prompt and raw reply under `out/reports/calls/`.

If you cannot verify something, say so plainly in the final report. An honest gap is worth more
than a confident claim, and the whole project is built on that principle.

## 4. Parallelism

Use subagents. The work below is largely independent per form.

Sensible split:

- One agent per form for calibration and binding: `irs-f8821`, `irs-ss4`. They do not touch each
  other's artifacts.
- One agent for the review UI, which depends only on the artifact schema, not on any particular
  form being finished.
- One agent for the Anvil integration, working against `ca-dmv-dl142` first.
- Keep model-call concurrency at 2 per agent. Parallel image uploads thrash the link, and we have
  already lost fifteen minutes to that once.

Serialise anything that writes to a shared file. `artifacts/` is per-form, so it is safe;
`out/reports/decisions.md` is not, so append with care.

## 5. The only reasons to stop and ask

Four. Nothing else.

1. **A hard rule in `CLAUDE.md` would have to be broken** to make progress.
2. **A real credential is needed** and is not in `.env` — say exactly which one.
3. **The Anvil account is out of quota** or the API rejects us in a way retrying will not fix.
4. **You discover that a passed gate was wrong** — for example, calibration labels turn out to be
   systematically off by one row. That invalidates everything downstream and is worth the
   interruption.

Anything else: decide, record, continue.

## 6. The work queue

Ordered by value. Work top to bottom. If time runs out, everything below the line you reached is
cut, and that is an acceptable outcome — say where you stopped.

### 6.1 — Phase 2 on `irs-f56` (in flight)

Converge the loop against `estate-05-in-formal-probate`. Keep every round's render and the
findings history in `out/reports/irs-f56-loop.json`.

If it converges in one round, say so explicitly and note that it demonstrated nothing about
self-repair. Then deliberately run it again against `estate-03-oh-trust-administration`, which
takes the opposite branch on lines 2a/2b and ticks box 1e instead of 1a — if the binding has a
weakness, that estate finds it.

### 6.2 — Phase 3: review and freeze

The FastAPI review page per `docs/02-SPEC.md` §3. Approval, versioning, promotion to
`artifacts/approved/`, immutability.

Must display: source path and produced value per field, low-confidence and unbound at the top,
`guardedOff` distinguished from `absent`, and any `exclusiveGroups` violation prominently.

Gate: approve `irs-f56`, then fill `estate-05`, then fill `estate-01` with the same approved
binding and no recompilation. Both sidecars must read `llmCallsAtRuntime: 0`.

### 6.3 — Phase 4: one live Anvil fill

`ca-dmv-dl142` first — single page, no XFA, readable field names, and we already have proof it
renders. Register with our own aliases, reconcile in both directions, fill, write the bytes as
binary, rasterise and compare against the local path.

Then demonstrate the failure deliberately: introduce an alias the cast does not have, and confirm
reconciliation refuses rather than producing a clean-looking PDF with a hole in it. Save that
before/after pair — it is thirty seconds of demo and it is worth $1,000.

If `ANVIL_API_KEY` is absent, stop per section 5 item 2 and continue with 6.4 meanwhile.

### 6.4 — The remaining two forms

`irs-f8821` then `irs-ss4`, calibration through binding, in parallel subagents. These are the
first forms that may actually exercise crop escalation, which has never completed end to end —
expect it to break, and fix it when it does.

### 6.5 — Phase 5: the benchmark

`forge bench` across every applicable pair. Honest denominators from `docs/00-DOMAIN.md` §5, with
the applicable-pair count corrected to 14 — SS-4 is not produced for the three estates that
already hold an EIN, and refusing to produce it is a result worth reporting, not a gap.

Report no accuracy figure unless a human check is recorded. Where none exists, write "not
measured" and say what would measure it.

### 6.6 — Demo assets

Assemble under `out/demo/`:

- The headline run: the same approved `irs-f56` binding across all five estates, showing that the
  executor / administrator / trustee and testate / intestate ticks differ per estate while the
  binding does not change. One page of side-by-side crops of Section A.
- The loop history rendered readably — round 1 findings next to the final render.
- The reconciliation catch, before and after.
- `benchmark.md`.
- A `RUNBOOK.md`: exact commands for the live demo, in order, each under two seconds, with a note
  saying the loop is never run live because a critique round costs a minute and the slot is three.

### 6.7 — Merge preparation

A `MERGE.md` for tomorrow: the artifact JSON schema the other repository will read, the work-order
shape it must write, the exact file paths, and every place `warrant_mock.py` is referenced so it
can be deleted cleanly.

---

## 7. The final report

One report, at the end. Structure it as:

1. **What works** — with the command that proves each item.
2. **What does not** — plainly, no softening.
3. **What was never tested** — crop escalation belongs here unless 6.4 exercised it.
4. **Decisions taken under section 2** — the list from `decisions.md`, with reasons.
5. **Numbers** — every one pointing at its artifact.
6. **What I would do with two more hours.**

Section 3 is not a confession, it is the most valuable part of the report. The project's entire
thesis is that a system which reports its own gaps beats one that quietly guesses. Hold yourself
to the same standard.

## 8. Commits

Commit at every gate with the gate output in the message. If a gate fails and you route around it
per section 2, commit that too, with the decision in the message. Tomorrow's merge is far easier
against a history of honest checkpoints than one large commit at the end.
