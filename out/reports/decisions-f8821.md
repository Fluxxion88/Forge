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

## 2026-07-27 — bind attempt 2 (crashed round 1, fixed, rerun)

- Attempt 2 (`out/reports/bind-irs-f8821.attempt2.log`) survived a prose reply on
  propose (fell back to per-page proposals as designed) but crashed in critique
  round 1: the retried reply was prose containing incidental brackets
  ("dece[dent]"), so `extract_json_array` sliced non-JSON and `json.loads` raised
  a raw JSONDecodeError that nothing caught
  (`out/reports/calls/004-bind-irs-f8821-critique-r1.reply.txt`).
- Decision 3: `llm.extract_json_array`/`extract_json_object` now wrap
  `json.loads` and raise ModelCallFailed on decode errors, so unparseable replies
  are retryable failures everywhere instead of crashes.
- Decision 4: the model replied in prose in 3 of 6 calls despite "Answer with
  ONLY a JSON..." — hardened the format instruction at the END of the critique,
  repair and propose prompts (entire reply must parse as JSON; non-JSON replies
  are discarded unread; do not read/edit repo files). One propose reply showed
  the sub-model attempting to EDIT `artifacts/bindings/irs-f8821.json` (blocked
  by allowedTools=Read); the added instruction forbids that explicitly.
- Verified by eye from `out/renders/irs-f8821/round-1-page-0.png` (attempt 2):
  the critique's substance is real — column 3(d) clips all three rows. From the
  blank PDF: those fields are single-line (Ff=8388608 DoNotScroll, no multiline),
  fixed 8pt font, 128.6pt wide (~35 chars); the estate's specificTaxMatters
  strings are longer. This is a form-capacity fact, not a rendering bug. Not
  patching the fill path (shared with irs-f56); the loop must surface it —
  an open finding or an unbound-with-reason is the honest result.
- Test suite after edits: 58 passed.
