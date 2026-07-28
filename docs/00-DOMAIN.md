# 00 — Domain

Everything in this file was verified by opening the actual files. Where a number appears, it was
counted, not estimated. Treat these as fixtures: if your code disagrees with a number here, your
code is wrong, not this document.

## 1. What an estate settlement is

When someone dies, an executor must prove their authority to a court, then present that proof to
every institution the deceased dealt with. Each institution wants its own form. The forms differ
by state, by county and by institution, and getting one word wrong costs months.

Alix does this work for families. Their thesis: replace a twelve-person private-bank back office
with agents, drop the cost by 80%, and sell the service to ordinary people instead of only the
very rich. The service stays human-led — a specialist talks to the family; the machines work
behind them.

Our job is the paperwork layer.

## 2. The four forms

Supplied by the organisers in `TRACK 3 RESOURCES/TRACK 3 FORMS/`. Place them unmodified in
`inputs/forms/`.

| File | What it is, in plain language |
|---|---|
| `Form 56 June 2026.pdf` | Tells the IRS that a named person now acts on behalf of the estate |
| `Form SS-4 Dec 2025.pdf` | Applies for an EIN — the estate's own tax number, like a small company |
| `Form 8821 Jan 2021.pdf` | Authorises a named designee to receive the taxpayer's tax information |
| `DL 142 R7 93.pdf` | California DMV — surrender and cancel a deceased person's driver licence |

### Verified field inventory

| Form | Total fields | Text `/Tx` | Buttons `/Btn` | Fields carrying a human-readable tooltip |
|---|---|---|---|---|
| Form SS-4 | 93 | 45 | 44 | **0** |
| Form 56 | 76 | 39 | 33 | **0** |
| Form 8821 | 52 | 33 | 12 | 5 (all in the IRS-use-only header block) |
| DL 142 | 51 | 14 | 14 | 28 |

`forge inspect` must reproduce these numbers exactly. They are the first gate.

Total: 272 fields, of which roughly 220 carry no semantic hint of any kind. Field names on the
IRS forms look like this:

```
topmostSubform[0].Page1[0].f1_04[0]
topmostSubform[0].Page1[0].c1_5[0]
```

Nothing in that name tells you it is the fiduciary's address, or the checkbox for "decedent died
intestate". **This gap — between an opaque field name and its meaning — is the entire problem
Forge exists to close.** Today a human closes it by opening the PDF, clicking each box, watching
where the cursor lands, and writing it down. That is the hours we are taking out.

## 3. Three PDF traps, verified by experiment

These are not hypothetical. Each was reproduced on the actual files. Handle all three in
`calibrate.py` and `fill.py` or nothing will render.

### 3.1 All three IRS forms are XFA hybrids, with `NeedAppearances` unset

```
Form 56    XFA present: True   NeedAppearances: None
Form SS-4  XFA present: True   NeedAppearances: None
Form 8821  XFA present: True   NeedAppearances: None
DL 142     XFA present: False  NeedAppearances: None
```

Writing AcroForm values into an XFA hybrid can leave the page visually blank in viewers that
prefer the XFA layer. Mitigation, applied together:

1. Set `/NeedAppearances` to `True` on the AcroForm dictionary after writing values.
2. Call `update_page_form_field_values(..., auto_regenerate=True)` so pypdf generates appearance
   streams rather than relying on the viewer.
3. **Verify by rasterising**, never by reading the value back.

### 3.2 Checkbox "on" values are `/1`, `/2`, `/3`, `/4` — not `/Yes`, not `True`

Observed states on Form 56:

```
c1_1[0]  states = ['/1',  '/Off']
c1_1[1]  states = ['/2',  '/Off']
c1_1[2]  states = ['/3',  '/Off']
c1_1[3]  states = ['/4',  '/Off']
```

Each checkbox is a **separate field** with its **own** on-value. The index in the name suggests a
group, but they are not a radio group with shared states. Writing `True` or `/Yes` is a silent
no-op: no error, no mark, and a PDF that looks perfectly fine with an empty box in the middle of
it. On a filing that is a rejection and another month of the family's life.

Enumerate the real on-value per field from the appearance dictionary (`/AP` → `/N`, the key that
is not `/Off`) and store it in the calibration artifact as `onValue`. Never hardcode.

### 3.3 DL 142 has duplicate short field names

DL 142 contains multiple fields whose short name is `0`, plus parent/child pairs such as
`Address person DL/ID to be cancel` (a container, no `/FT`) and
`Address person DL/ID to be cancel.0` (the actual `/Tx`).

Key every field by its **fully qualified name**, never the short name. A dict keyed on short
names silently loses fields.

## 4. The five sample estates

Supplied in `TRACK 3 RESOURCES/TRACK 3 SAMPLES/`. Place them in `inputs/estates/`. All five share
one schema, `estate-form-data.schema.json`. The data is synthetic — the `provenance` block in
each file documents the anonymisation, and SSNs come from the 900 block, which the SSA has never
issued. It is safe to display on a projector.

| File | Jurisdiction | Route | Notable |
|---|---|---|---|
| `estate-01-nj-ancillary-probate.json` | NJ, Mercer | Ancillary probate | EIN already issued; decedent held a passport, not a licence |
| `estate-02-ca-intestate-independent-admin.json` | CA, Los Angeles | Independent administration, no will | No EIN — SS-4 is a live application; CA licence, so DL 142 applies in full |
| `estate-03-oh-trust-administration.json` | OH, Cuyahoga | Trust administration | Successor trustee out of state; Form 56 authority is the trust instrument |
| `estate-04-ca-trust-and-estate.json` | CA, Los Angeles | Trust and estate combined | Surviving spouse is sole trustee and sole beneficiary |
| `estate-05-in-formal-probate.json` | IN, Marion | Formal probate | Most complete court record: docket, letters date, bond, EIN; two attorneys, so both 8821 designee slots used |

### Schema shape

Top-level keys, identical across all five:

```
$schema  meta  decedent  estateEntity  fiduciary  authority
taxMatters  form56  form8821  formSS4  formDL142
relatedParties  assets  provenance
```

Two families of keys, and the distinction matters:

- **Generic estate data** — `decedent.name.full`, `fiduciary.address.line1`,
  `authority.proceeding.docketNumber`, `assets[]`, `relatedParties[]`. Shared across every form.
- **Per-form blocks** — `form56`, `form8821`, `formSS4`, `formDL142`. Already shaped to one
  specific document. These carry the answers a form asks that nothing else does.

A binding will draw from both. Prefer the generic path where a value exists in both places, so
the same binding fragment is reusable across forms.

### Applicability is data, and it is not ours to decide

Each per-form block carries an applicability flag:

```json
"formDL142": {
  "applicable": false,
  "notApplicableReason": "The decedent held an Ohio driver licence.
                          DL 142 is a California Department of Motor Vehicles form..."
}
```

DL 142 applies in only 2 of the 5 estates. SS-4 is a live application in only 2 — the other three
already have an EIN on `estateEntity.ein`.

**Forge never decides applicability.** That decision belongs to the other half of the system. Forge
reads the flag and skips. See `docs/01-CONTRACT.md`.

## 5. Benchmark denominators

Use these as the honest denominators in the report. They are what makes the impact claim
measurable rather than asserted.

- 272 fields across four forms
- ~220 with no semantic hint of any kind
- 103 button fields with per-field on-values that must be discovered
- 5 estates × 4 forms = 20 pairs, of which roughly 17 are applicable
- 3 jurisdictions represented (CA, plus NJ / OH / IN)

The strongest single measurement: run **the same Form 56 binding across all five estates**. The
data differs, the jurisdiction differs, the checkboxes differ — executor versus administrator
versus trustee, testate versus intestate — and the binding does not change. That is reusability
demonstrated on the organisers' own data, without a word about scaling.
