# Forge benchmark

Generated 2026-07-28T06:30:36Z. 14 applicable pairs of 20; SS-4 correctly refused where an EIN already exists.

A pair that is not filled is in one of two states, and they are not the same thing: **compiled, awaiting human approval** means the binding exists and a person has to sign it off before anything is produced (`forge fill` never falls back to a draft); **not compiled** means there is no binding at all.

| Estate | Form | Applicable | Filled | Empty (reported) | Fill ms | LLM calls at fill |
|---|---|---|---|---|---|---|
| estate-01-nj-ancillary-probate | irs-f56 | yes | 32 | 40 | 42 | **0** |
| estate-01-nj-ancillary-probate | irs-ss4 | no — The estate already holds EIN 22-6104839; SS-4 applies for a  | | | | |
| estate-01-nj-ancillary-probate | irs-f8821 | yes | — | — | — | 39 bound / 6 unbound — compiled, awaiting human approval |
| estate-01-nj-ancillary-probate | ca-dmv-dl142 | no — The decedent's only recorded government identification is a  | | | | |
| estate-02-ca-intestate-independent-admin | irs-f56 | yes | 28 | 44 | 40 | **0** |
| estate-02-ca-intestate-independent-admin | irs-ss4 | yes | — | — | — | 77 bound / 12 unbound — compiled, awaiting human approval |
| estate-02-ca-intestate-independent-admin | irs-f8821 | yes | — | — | — | 39 bound / 6 unbound — compiled, awaiting human approval |
| estate-02-ca-intestate-independent-admin | ca-dmv-dl142 | yes | — | — | — | not compiled (no binding at all) |
| estate-03-oh-trust-administration | irs-f56 | yes | 27 | 45 | 40 | **0** |
| estate-03-oh-trust-administration | irs-ss4 | yes | — | — | — | 77 bound / 12 unbound — compiled, awaiting human approval |
| estate-03-oh-trust-administration | irs-f8821 | yes | — | — | — | 39 bound / 6 unbound — compiled, awaiting human approval |
| estate-03-oh-trust-administration | ca-dmv-dl142 | no — The decedent held an Ohio driver licence. DL 142 is a Califo | | | | |
| estate-04-ca-trust-and-estate | irs-f56 | yes | 25 | 47 | 40 | **0** |
| estate-04-ca-trust-and-estate | irs-ss4 | no — The estate already holds EIN 95-4718203; SS-4 applies for a  | | | | |
| estate-04-ca-trust-and-estate | irs-f8821 | yes | — | — | — | 39 bound / 6 unbound — compiled, awaiting human approval |
| estate-04-ca-trust-and-estate | ca-dmv-dl142 | yes | — | — | — | not compiled (no binding at all) |
| estate-05-in-formal-probate | irs-f56 | yes | 32 | 40 | 40 | **0** |
| estate-05-in-formal-probate | irs-ss4 | no — The estate already holds EIN 35-6082714; SS-4 applies for a  | | | | |
| estate-05-in-formal-probate | irs-f8821 | yes | — | — | — | 39 bound / 6 unbound — compiled, awaiting human approval |
| estate-05-in-formal-probate | ca-dmv-dl142 | no — The decedent held an Indiana driver licence. DL 142 is a Cal | | | | |

## Build cost per form (once, ever)

| Form | Calibration calls | Loop rounds | Converged | Loop calls | Loop seconds |
|---|---|---|---|---|---|
| ca-dmv-dl142 | 1 | — | — | — | — |
| irs-f56 | 2 | — | — | — | — |
| irs-f8821 | 1 | 2 | False | 4 | 532.8 |
| irs-ss4 | 1 | — | — | — | — |

Accuracy: not measured — no recorded human check exists yet; measuring it means a human comparing each filled render against the blank form field by field and recording the result per pair
