# Decisions — irs-f8821 lane

Logged per docs/05 §2. This lane writes here, never to out/reports/decisions.md.

## 2026-07-27 — calibration

- `forge calibrate irs-f8821` passed first try: 45/45 fields labelled (100.0%),
  0 unresolved, 1 model call. Evidence: `artifacts/calibration/irs-f8821.json`,
  `out/reports/calibrate-irs-f8821.log`.
- Crop escalation did NOT fire ("escalating 0/45 field(s)") — the whole-page pass
  resolved every field with confidence above low. The escalation path therefore
  remains UNTESTED end to end; the STATUS NOTE in `src/forge/calibrate.py` still
  stands. Not exercised artificially — that would have burned model calls proving
  nothing about real escalation triggers.
- Sentinel renders verified by eye (both PNGs read and inspected): tokens visibly
  drawn in text boxes, all checkboxes visibly ticked.
  `out/renders/irs-f8821/sentinel-text-page-0.png`, `sentinel-btn-page-0.png`.

## 2026-07-27 — bind attempt 1 (failed round 2, fixed, rerun)

- Attempt 1 (`out/reports/irs-f8821-loop.attempt1.json`, renders under
  `out/renders/irs-f8821/attempt1/`) stopped at round 2: the critique call
  succeeded at the transport level but replied with a prose summary instead of
  the JSON array (`out/reports/calls/004-bind-irs-f8821-critique-r2.reply.txt`),
  and parse failures were not retried — only transport failures were.
- Decision 1: moved JSON parsing inside the retried callable in `loop.py`
  `_critique` and `_repair`, so an unparseable reply raises ModelCallFailed and
  gets the standard single retry (docs/05 §2: "a model call fails → retry once").
  One bad reply no longer kills a round.
- Decision 2: round-1/round-2 critiques flagged the signature date 08/03/2026 as
  "fabricated", but it is a verbatim estate fact (`form8821.signature.date =
  2026-08-03`). Root cause: `_estate_summary` had a hard-coded, Form-56-centric
  key list with no `form8821.*` facts, so the critique could not see them. Added
  `FORM_SUMMARY_KEYS` keyed by formId with the 8821 facts (taxpayer block, both
  designee blocks, checkbox flags, signature, all three authorizationRows).
  Form 56 summary output is unchanged (no entry in the dict for it).
- Test suite after both edits: 58 passed (`.venv/bin/python -m pytest -q`).
- Rerunning `forge bind irs-f8821 --estate estate-05-in-formal-probate` from a
  fresh propose rather than resuming the half-repaired draft: the gate command is
  the full bind, and the attempt-1 history is preserved on disk either way.
