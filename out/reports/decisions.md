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
