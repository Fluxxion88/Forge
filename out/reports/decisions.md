# Decisions — autonomous run per docs/05-AUTONOMOUS-RUN.md

Format: timestamp UTC · fork · decision · reason.

- 2026-07-28T03:20Z · **Demo estate** · Demo assets (6.6) built around
  `estate-03-oh-trust-administration`, not estate-05 · Operator directive: estate-05 is the
  calibration estate, so demoing it proves nothing about reuse; estate-03 takes the opposite
  2a/2b branch, ticks 1e not 1a, trustee not executor.
- 2026-07-28T03:20Z · **Concurrency** · Total model-call concurrency ≈4 across ALL agents,
  not per-agent · Operator directive; parallel image uploads already cost 15 minutes tonight.
- 2026-07-28T03:21Z · **Anvil credential** · `ANVIL_API_KEY` is not in `.env` (only
  `OPEN_AI_KEY` is present). Per §5 item 2 this is an ask; per §6.3 the ask is surfaced in
  the final report while the Anvil client is built and tested against a recorded-shape stub
  so a key drop makes it live without code changes.
- 2026-07-28T03:21Z · **Vision fallback** · OpenAI fallback (operator-authorised, $10 hard
  cap in code) is NOT activated — `claude -p` vision transport is working; the only repeated
  timeouts so far are the text-only propose call, which halving image batches would not
  affect. Fallback stays dormant unless vision calls start failing repeatedly.
- 2026-07-28T03:55Z · **Propose timeout** · Whole-form propose (72 fields + full estate
  JSON) timed out twice at 420s. Per §2 "halve the batch": added per-page proposal
  fallback (merge of page-scoped proposals), firing after ONE whole-form attempt since
  the failure is reproducible. Evidence: out/reports/calls/00{1,2}-bind-irs-f56-propose.error.txt.
- 2026-07-28T04:20Z · **Loop crash round 1 (f56)** · Critique returned an array with a
  malformed member; `f["problem"]` on a non-dict crashed the run after round 1. Fix:
  findings are sanitised (only dicts with a `problem` key survive, target/mustFix
  coerced), malformed entries are dropped loudly. Per-page propose fallback worked:
  pages 0+1 proposed in 360+206s after the whole-form 420s timeout. Round 1 had
  produced 1 image finding before the crash; rerunning the loop from scratch.
- 2026-07-28T04:45Z · **Lane discipline** · Operator rule folded in: subagents may not
  edit shared code while other lanes run; they report diffs, MAIN applies between runs.
  f8821 agent's two earlier loop.py edits (parse-retry, FORM_SUMMARY_KEYS) accepted.
  MAIN pre-added FORM_SUMMARY_KEYS["irs-ss4"] before that lane's bind starts.
- 2026-07-28T04:45Z · **f8821 lane cost diagnosis** · The 30-minute lane was NOT crop
  escalation (0/45 escalated; calibration passed in 1 call). It was bind attempt 1
  dying at round 2 on an unparseable critique reply + the agent's root-cause work +
  full rerun. Both underlying bugs were real and are fixed in shared code.
- 2026-07-27T20:0Z (local session, operator scope cut) · **One-pass propose** · Operator cut
  the convergence loop, Anvil, the other two forms and the benchmark. Added
  `forge propose <form> --estate <id>`: propose → validate → write draft → fill → rasterise,
  once. `forge fill` is left strictly approved-only (docs/02 §4 "never fall back to a draft") —
  the build-time fill lives in `propose` and goes through the same `fill_pdf()` the runtime
  uses, so the draft is verifiable by eye without weakening the runtime guarantee.
- 2026-07-27T20:0Z · **Per-page propose is the default, not a fallback** · The whole-form
  f56 propose call is a reproduced 420s timeout (out/reports/calls/00{1,2}-bind-irs-f56-propose.error.txt);
  attempting it first would burn 7 of the 30 minutes to learn nothing. `--whole-form` opts back in.
- 2026-07-27T20:0Z · **Draft render naming** · One-pass renders are
  `out/renders/irs-f56/draft-page-<p>.png`, NOT `round-1-*`: no round ran, and the stale
  `round-1-*.png` from the crashed 04:20Z loop must not be passed off as this draft's output.
  The review UI prefers `draft-page-*` and falls back to the highest round.
- 2026-07-27T20:0Z · **Per-row approval is data** · The review UI's per-row approve writes
  `"reviewed": true` onto the binding object — one more JSON field, no new store, consistent
  with CLAUDE.md rule 6 (artifacts are data). It is advisory: the footer gate is still
  required-fields-bound, per docs/02 §3.
- 2026-07-27T21:30Z · **17 dead checkbox bindings on f56 items 3 and 4** · The proposal bound
  every "check all that apply" box as `condition` against an ARRAY path
  (`taxMatters.taxTypes` = `["Income","Estate"]`, `taxMatters.federalFormNumbers` =
  `["1040_or_1040SR","1041"]`). Scalar equality against a list is never true, so 15 checkboxes
  plus their 2 "other, describe" text fields could never be marked ON ANY ESTATE — the raster
  showed items 3 and 4 entirely blank while the estate plainly supports 3a, 3c, 4e and 4f.
  Decision: do NOT add a sixth `contains` source kind (docs/02 §2.1 fixes the language at five,
  and the operator scoped this run to those five). Instead added a deterministic pass,
  `bind.unbind_dead_bindings()`, that detects a `condition` source or a `when` guard whose path
  resolves to a list and moves the binding to `unbound`, with `whatWouldFillIt` naming the
  missing capability. Rationale: a provably unmarkable checkbox is exactly the
  confident-and-wrong failure CLAUDE.md rule 5 forbids; unbound-and-visible beats
  silently-blank. The reviewer now sees 22 red rows, 17 of them explaining that the estate has
  the data and the binding language cannot express the test.
- 2026-07-27T21:30Z · **`forge propose --from-draft`** · Added a no-model re-validate /
  re-fill / re-render path so the dead-binding pass could be applied without spending the
  400s proposal again, and so a reviewer who edits a row can refresh the picture the review
  UI told them was stale. 0 model calls, 0.4s.
- 2026-07-27T21:30Z · **Third empty-reason state** · The UI distinguished only guard-false from
  data-absent, which mislabelled 9 correctly-clear f8821 checkboxes ("the fact says no") as
  missing data. Added `condition-false` as a third status. On f8821 that moved 9 of 11
  "absent" rows out of the column a reviewer must act on.
- 2026-07-27T22:05Z · **Sixth source kind `contains`** · Operator directive: spec fixed the
  binding language at five kinds, reality needs six, reality wins. `{"kind": "contains",
  "path", "includes"}` marks a checkbox when the ARRAY at `path` holds `includes`.
  docs/02-SPEC.md §2.1 amended (plus a dated note at the top of the file) with the reason it
  exists, so the next reader does not "simplify" it away. `unbind_dead_bindings()` now checks
  BOTH shape mismatches: scalar equality against an array, and `contains` against a scalar.
- 2026-07-27T22:05Z · **f56 items 3/4 rebound; one literal was wrong** · The 17 rows moved to
  `unbound` last round are rebound with `contains`. While rebinding, found the proposal's
  `706_series` does not match the corpus, which spells it `706Series` (estate-01, estate-04) —
  so item 4a would have stayed dead even with the new kind. Fixed from evidence. Literals for
  the 10 options no estate exercises (Gift, Employment, 940, 1120, …) follow the observed
  convention but carry `confidence: low` and a note saying the enum spelling is unverified:
  the referenced schema `./estate-form-data.schema.json` is NOT in inputs/, and 706_series
  proves the guesses are unreliable. Those rows sort to the top of the review UI for a human.
- 2026-07-27T22:05Z · **Two "other, describe" text boxes** · Their original `when` guard
  compared an array path with equality and so was permanently false. Rebound as plain `path`
  with no guard rather than extending `when` to do membership: a description is only recorded
  when the option applies, so the value's own presence is the condition. Keeps the guard
  grammar at one shape (path == literal).
- 2026-07-27T22:05Z · **Page geometry backfilled, not recalibrated** · The hover overlay needs
  page width/height in points, which the calibration artifact did not carry. Added `pages: [
  {cropBox, mediaBox, widthPt, heightPt, rotate}]` and per-field `widgets: [{page, rect}]` to
  the calibrate output, plus `forge calibrate <form> --geometry-only`: a no-model pass that
  measures the PDF and backfills an existing artifact, asserting `sourceSha256` first and
  leaving every label untouched. Chose this over a full recalibrate because the f56 72/72 and
  dl142 28/28 labels are human-verified and re-running the semantic pass would spend model
  calls to re-derive them, with a chance of regression. Geometry is measured either way.
- 2026-07-27T22:05Z · **The overlay flips Y against the CROP box, not the page height** ·
  `pdftoppm` rasterises the CropBox, and a CropBox need not start at the origin: DL 142 is a
  1224x792 MediaBox cropped to [0, 3.55556, 612, 792] (788.44pt tall, not 792), and SS-4's
  page 2 is 828pt tall where page 1 is 792. So the mapping subtracts the crop origin as well
  as flipping — `left = (x0-cx0)/w`, `top = (cy1-y1)/h` — and every page is measured
  separately. The operator-supplied formula (bare page height) is the special case where the
  crop starts at 0,0; it would have been off by 3.5pt on DL 142 and badly wrong on SS-4 page 2.
  Rotated pages are refused with an `unsupported` marker rather than drawn wrong (none of the
  four forms is rotated). Widgets falling outside the rendered crop are flagged `offCrop` and
  not drawn.
- 2026-07-27T22:05Z · **Percentages computed server-side** · The flip lives in one Python
  function with unit tests rather than in JavaScript, and the browser only positions divs with
  the percentages it is handed. Verified with playwright: the boxes the browser actually draws
  are within 0.002 percentage points of the computed values.

## Session 3 — reuse proof, loop history, Anvil live

- 2026-07-27T22:20Z · **Duplicate approval left alone** · The UI produced BOTH
  `irs-f56.v1.json` and `v2.json` — identical bindings, timestamps 36 minutes apart (the
  approve button was pressed twice). Approved artifacts are immutable, so neither was deleted
  or edited. Instead added `forge fill --binding-version N` so the demo pins v1 explicitly
  rather than silently taking the highest. Recommend the operator delete v2 by hand.
- 2026-07-27T22:25Z · **Reuse over v1 exposed three defects; approved artifact NOT edited** ·
  Filling all five estates from the frozen v1 found: (a) line 1e literal `TrustInstrument`
  where the corpus says `ValidTrustInstrument`, so estates 03/04 ticked NO authority box and
  the `exactlyOne` group check failed them; (b) line 2a guarded on one basis value where
  docs/02 §2.1 specifies the 1a/1b/1d branch, blanking estate-02's date of death; (c) line 2b
  likewise for the 1c/1e/1f/1g branch, blanking 03/04's date of appointment. All three are the
  same species as the `706_series` bug: a literal or guard too narrow. Fixed in the DRAFT only
  and left at `status: draft, version: 3` for a human to approve — self-approving would violate
  CLAUDE.md rule 7. Both reuse runs are published side by side (`reuse-v1.md` showing the
  violations, `reuse-draft.md` clean) because the checker catching it is itself the thesis.
- 2026-07-27T22:25Z · **`equalsAny` added to the `when` guard** · Same reasoning the operator
  applied to `contains`: the spec's single-literal guard cannot express a three- or four-value
  branch, and reality wins. `{path, equalsAny: [...]}`, membership against a fixed list, no
  predicates. docs/02-SPEC.md §2.1 amended with the failure that motivated it.
- 2026-07-27T22:30Z · **Strip stacked vertically, not horizontally** · Section A is ~550pt wide
  and ~110pt tall; five side by side is 2700px+ and illegible on a projector. Stacked, line 1
  and the 2a/2b rows sit directly above one another, which is what makes the difference
  readable at a glance. Each row carries its estate, jurisdiction, basis, box, date and title.
- 2026-07-27T22:35Z · **Loop run isolated by label** · `forge bind --label naive-estate03`
  writes renders to `out/renders/irs-f56/naive-estate03/` and its draft to `out/reports/`,
  never `artifacts/bindings/`. Verified by sha256 before and after: the draft under review was
  untouched. Also added `--naive`, which proposes with the PRE-lessons binding language, so the
  loop has genuine mistakes to find; feeding it the corrected prompt and showing it converge in
  one round would be theatre. Round 1 produced 4 findings read off the image — including,
  independently, the same 2a-guard defect the reuse run found.
- 2026-07-27T22:40Z · **Anvil: `aliasIds` is positional AND inert** · docs/03-ANVIL.md's alias
  strategy does not survive contact with the API. The live schema takes `allowedAliasIds:
  [String]` (not `allowAliasIds`) and `aliasIds: JSON` as a POSITIONAL list of strings aligned
  to Anvil's own detection order — which differs from AcroForm order. Objects fail validation.
  And once accepted the aliases do nothing: a fill keyed by them returned HTTP 200 with 136 KB
  of valid PDF and all 32 values silently dropped. The payload is therefore keyed by Anvil's
  internal field id, resolved at fill time; our alias vocabulary stays in the artifact where a
  human reads it.
- 2026-07-27T22:40Z · **Reconciliation had the bug it exists to catch** · It compared our
  aliases against `allowedAliasIds`, which Anvil echoes back in full — so it reported ZERO
  drift on the fill that lost every value. Now compares against `fieldInfo.fields[].name`, the
  short PDF field name, which is what actually decides whether a value lands. Pinned by
  `test_reconcile_ignores_allowedAliasIds`. Related hazard guarded but unexercised: Anvil
  reports only the short name, and DL 142 has several fields whose last segment is `0`, so
  `bound_short_names()` refuses rather than guessing.
- 2026-07-27T22:45Z · **Cast eid stored beside the approved artifact, not in it** ·
  `artifacts/approved/*.json` is chmod 0444 on approval. The registration lives in
  `artifacts/anvil/<formId>.json` with the sha256 of the binding it was registered from.
- 2026-07-27T22:45Z · **Anvil demonstrated on Form 56, not DL 142** · docs/03 §"Order of work"
  says DL 142 first as the lowest-risk. Form 56 worked on the first properly-keyed attempt —
  Anvil detected all 72 fields on the XFA hybrid, 39 text / 33 checkbox, matching calibration
  exactly — so the fallback was never needed and DL 142 was left unbound.
- 2026-07-27T22:50Z · **Sidecar filename collision fixed** · `forge fill --via anvil` wrote its
  report to the same path as the local run, so the Anvil run silently overwrote the local one
  and the two could not be compared. The `-anvil` suffix now applies to the sidecar as well.
- 2026-07-27T23:05Z · **Per-run reuse directories** · Both reuse runs wrote sidecars, PDFs and
  renders to `out/demo/reuse/`, so the second run overwrote the first and `reuse-draft.md`
  ended up citing v1's evidence. Outputs are now per-run: `out/demo/reuse-draft/` and
  `out/demo/reuse-v1/`. Same class of bug as the `--via anvil` sidecar collision.
- 2026-07-28T00:05Z · **v3 approved; canonical asset naming** · The highest approved version now
  owns the unsuffixed asset names (`out/demo/reuse.md`, `reuse-section-a.png`), so the
  authoritative report is never a stale sibling. Older approved versions keep a suffix and stay
  as history — `reuse-v1.md` is retained deliberately, because a system reporting the defect it
  shipped with is the thesis. `reuse-draft.md` was deleted: the draft IS v3 now, so it was a
  third near-identical copy.
- 2026-07-28T00:05Z · **Pillow was missing from the venv** · `forge reuse-proof` had only ever
  been run with system python, which has PIL; from `.venv` (what the RUNBOOK tells the operator
  to activate) it died with ModuleNotFoundError. Added `pillow>=10.0` to pyproject dependencies
  and installed it. Caught only because the RUNBOOK was verified from a cold shell with
  PYTHONPATH unset — the exact failure that would have happened on stage.
- 2026-07-28T00:05Z · **Demo markdown used repo-relative image links** · `reuse.md`,
  `headline.md` and `loop-history.md` live inside `out/demo/` but linked images as
  `out/demo/...` and `out/renders/...`, which render broken when the file is opened in place.
  All links are now relative to `out/demo/`; a link audit over every asset passes.
- 2026-07-28T00:05Z · **RUNBOOK pinned to v3** · Every `forge fill` carries
  `--binding-version 3` so approving a v4 cannot change what the demo does. All nine steps plus
  the regeneration section were executed in order from a cold shell (`env -u PYTHONPATH`,
  `source .venv/bin/activate`): all pass.
