# 02 — Engine specification

Four stages. The first two may call a model. The last two must not.

> Amended 2026-07-27: the binding language has **six** source kinds, not five. `contains` was
> added because Form 56 items 3 and 4 store "check all that apply" answers as arrays and no
> scalar-equality kind can read them. See §2.1.

```
calibrate  →  bind  →  review  →  fill
(model)      (model)    (human)   (no model, ever)
```

---

## Stage 1 — Calibration

**Goal:** turn a PDF full of meaningless field names into a map of what each field means and
where it sits on the page. Runs once per form. The output is checkable by eye.

### 1.1 Enumerate

Read the AcroForm with `pypdf`. For every field record:

| Key | Source |
|---|---|
| `qualifiedName` | full dotted name — never the short name, see `docs/00-DOMAIN.md` §3.3 |
| `type` | `/FT`, one of `/Tx` or `/Btn`; skip container nodes with no `/FT` |
| `page` | page index the widget appears on |
| `rect` | widget rectangle in PDF points |
| `onValue` | for `/Btn` only: the key in `/AP` → `/N` that is not `/Off` |
| `maxLen` | `/MaxLen` when present |

Fields whose `onValue` cannot be determined are recorded with `onValue: null` and flagged. Do not
default them to `/Yes`.

### 1.2 Sentinel pass

The trick that makes this cheap. Instead of reasoning about field names, discover meaning
empirically by writing values and looking at where they land.

1. Assign every `/Tx` field a unique token: `Z001`, `Z002`, … Uniqueness matters more than
   brevity, but keep tokens short enough not to overflow narrow boxes.
2. Write all of them in a single pass. Set `/NeedAppearances` true and use
   `auto_regenerate=True` (see `docs/00-DOMAIN.md` §3.1).
3. Render every page to PNG at 150 dpi with `pdftoppm`.
4. Repeat for `/Btn` fields in a **second** pass, marking every button at its discovered
   `onValue`. Keep this separate from the text pass so the marks are unambiguous — a page with
   every text box and every checkbox filled at once is hard to read.

Cost: two renders per page per form. For four forms that is a handful of images and a couple of
model calls, total, for the entire project.

### 1.3 Semantic pass

One vision call per rendered page. Input: the page image plus the list of sentinels placed on it
with their rectangles. Ask for, per sentinel:

- `itemNumber` — the number printed on the paper next to it, e.g. `1b`, `7a`, `9`. Null if the
  form does not number that area.
- `printedLabel` — the caption as printed, verbatim.
- `meaning` — one short sentence in plain English.
- `confidence` — `high` | `medium` | `low`.

Instruct the model explicitly that a sentinel it cannot locate must be returned with
`confidence: low` and nulls, never with a guess. A wrong `itemNumber` is worse than a missing one,
because a reviewer trusts it.

**Crop escalation.** After the whole-page pass, any field that returned `confidence: low`
**or no `printedLabel`** is re-queried from a crop: re-render around each of the field's
widget rectangles, extended ~120pt to the left and ~40pt vertically so the printed caption
is in frame. Batch at most 4 crops per call, with at most 2 calls in flight.

A null `itemNumber` alone must **never** trigger escalation — many forms number nothing at
all (DL 142 is one), and on those the trigger fires for every field on the page. A field
that is still unlabelled after escalation goes to `unresolved`; it is not retried further.

### 1.4 Output

`artifacts/calibration/<formId>.json`

```json
{
  "formId": "irs-f56",
  "sourceFile": "inputs/forms/Form 56 June 2026.pdf",
  "sourceSha256": "…",
  "calibratedAt": "2026-07-27T22:40:00Z",
  "model": "…",
  "pageCount": 2,
  "fields": [
    {
      "qualifiedName": "topmostSubform[0].Page1[0].f1_04[0]",
      "type": "text",
      "page": 0,
      "rect": [72.0, 648.5, 306.0, 664.5],
      "onValue": null,
      "sentinel": "Z004",
      "itemNumber": "1",
      "printedLabel": "Name of person for whom you are acting",
      "meaning": "Full legal name of the deceased person the fiduciary acts for",
      "confidence": "high"
    }
  ],
  "unresolved": ["topmostSubform[0].Page2[0].c2_9[0]"]
}
```

`unresolved` is a first-class output, not an error. It is what the reviewer looks at first.

### 1.5 Gate

Calibration for a form passes when at least 90% of its `/Tx` and `/Btn` fields carry a non-null
`itemNumber` or `printedLabel`, and the rendered sentinel pages exist on disk for a human to
check. Print the percentage. Do not proceed on a form that fails.

---

## Stage 2 — Binding synthesis and the convergence loop

**Goal:** produce a draft mapping from estate data paths to form fields, then improve it by
looking at the filled result until nothing is wrong. Runs once per form.

### 2.1 The artifact

This is the thing that gets reviewed, frozen and reused. It is data. It is never code.

`artifacts/bindings/<formId>.json`, promoted on approval to
`artifacts/approved/<formId>.v<N>.json`.

```json
{
  "formId": "irs-f56",
  "version": 1,
  "status": "draft",
  "sourceFormSha256": "…",
  "calibrationRef": "artifacts/calibration/irs-f56.json",
  "createdAt": "…",
  "approvedBy": null,
  "approvedAt": null,
  "anvilCastEid": null,
  "bindings": [
    {
      "qualifiedName": "topmostSubform[0].Page1[0].f1_04[0]",
      "itemNumber": "1",
      "label": "Name of person for whom you are acting",
      "source": { "kind": "path", "path": "decedent.name.full" },
      "format": "text",
      "required": true,
      "confidence": "high",
      "note": null
    },
    {
      "qualifiedName": "topmostSubform[0].Page1[0].c1_1[1]",
      "itemNumber": "1b",
      "label": "Court appointment of intestate estate",
      "source": {
        "kind": "condition",
        "path": "authority.basis",
        "equals": "CourtAppointmentIntestate"
      },
      "format": "checkbox",
      "onValue": "/2",
      "required": false,
      "confidence": "high",
      "note": null
    }
  ],
  "unbound": [
    {
      "qualifiedName": "…",
      "label": "…",
      "reason": "No estate data path corresponds to this field",
      "whatWouldFillIt": "A value for the court's telephone number"
    }
  ]
}
```

Supported `source.kind`:

| Kind | Shape | Use |
|---|---|---|
| `path` | `{ path }` | direct value from the estate JSON |
| `constant` | `{ value }` | a box whose content never varies |
| `template` | `{ pattern, paths }` | e.g. `"{0}, {1}"` from two paths |
| `condition` | `{ path, equals }` | checkbox: mark when the path equals the value |
| `contains` | `{ path, includes }` | checkbox: mark when the array at the path contains the value |
| `absent` | `{ path }` | checkbox: mark when the path is absent or null |

Nothing else. If a field needs logic beyond these six, it does not get bound — it goes into
`unbound` with a description. Arbitrary expressions are how a data artifact quietly becomes code.

**Why `contains` exists.** This list was five kinds, all of them scalar equality, until the
first real binding hit Form 56 items 3 and 4 — "check all that apply". The estate stores those
answers as arrays (`taxMatters.taxTypes` is `["Income", "Estate"]`,
`taxMatters.federalFormNumbers` is `["1040_or_1040SR", "1041"]`), and `condition` compares with
`==`, which is never true against a list. Fifteen checkboxes were bound, validated, and could
not have been marked on any estate ever. The failure was silent: the boxes simply rendered
empty, exactly as they would if the data were missing. A "check all that apply" group is not an
exotic form construct, so the sixth kind is the honest fix; the alternative was leaving a whole
section of the form permanently unfillable. `includes` is a single literal, membership only —
no predicates, no expressions. It stays data.

A checkbox whose `condition`/`contains` path is the wrong shape (scalar equality against an
array, or membership against a scalar) is **provably unmarkable**, and the loop must not ship
one. `bind.unbind_dead_bindings()` detects both directions against the calibration estate and
moves the binding to `unbound` with the reason, so the review UI shows it rather than the
reviewer discovering an empty box on a filed form.

**Conditional values: the `when` guard.** Any binding, whatever its `source.kind`, may carry an
optional guard, in one of two shapes:

```json
"when": { "path": "authority.basis", "equals": "CourtAppointmentTestate" }
"when": { "path": "authority.basis", "equalsAny": ["CourtAppointmentTestate",
                                                  "CourtAppointmentIntestate",
                                                  "FiduciaryIntestateEstate"] }
```

`equalsAny` was added 2026-07-27 for the same reason as the `contains` source kind: the branch
below is three authority values wide on the 2a side and four on the 2b side, and a single-literal
guard cannot express it. The first approved v1 binding guarded 2a on `CourtAppointmentTestate`
alone, which silently blanked the date of death for estate-02 (intestate, box 1b) and the date of
appointment for estates 03 and 04 (trust instrument, box 1e). Membership against a fixed list,
still one path, still no expressions.

If the guard path does not resolve to exactly that value, the binding is skipped and the field is
left empty, recorded as guarded-off rather than absent. This exists because which text field gets
a value can depend on another field's answer: Form 56 line 2a takes the date of death only when
box 1a, 1b or 1d is checked; line 2b takes the date of appointment on the opposite branch
(1c/1e/1f/1g). estate-05 (testate probate) and estate-03 (trust instrument) take opposite
branches of that pair. One path, one literal, equality only — the guard stays data, not code.

**Mutually exclusive checkbox groups.** The artifact carries a top-level `exclusiveGroups`:

```json
"exclusiveGroups": [
  {
    "label": "Line 1 — authority for fiduciary relationship",
    "rule": "exactlyOne",
    "members": ["topmostSubform[0].Page1[0].c1_1[0]", "…c1_1[1]", "…", "…c1_1[6]"],
    "when": null
  }
]
```

`rule` is `exactlyOne` or `atMostOne` (for groups inside sections that may legitimately not
apply, e.g. the Form 56 line 6b/6c/6d revocation-reason group). An optional `when` guard, same
shape as above, scopes enforcement. The PDF renders these as independent checkboxes — nothing in
the file stops three of them being ticked, and a filing with three authority boxes marked is
rejected on sight. Therefore: the fill records a group violation whenever a group breaks its
rule, **the convergence loop fails any round containing a violation**, and the review UI must
display violations prominently (a phase 3 obligation, recorded here so it is not lost).

### 2.2 The loop

For a chosen calibration estate (use `estate-05-in-formal-probate`, the most complete record):

```
1. Propose      model reads calibration + estate schema → draft bindings
2. Fill         deterministic, using the draft
3. Render       pdftoppm to PNG, per page
4. Critique     model reads the RENDERED IMAGE and reports findings
5. Repair       model revises the binding to address findings
6. Repeat       until zero findings, or 6 rounds, whichever comes first
```

Step 4 is the load-bearing one and it must look at the **image**, not at the JSON it just wrote.
A model asked "is your binding correct?" says yes. A model shown a page with an empty box in
item 7 says "item 7 is empty."

Findings to ask for explicitly:

- a required box left empty
- a value in the wrong box, or shifted by one row
- text overflowing its rectangle
- a date rendered in a format the form does not use
- a checkbox marked that contradicts the estate's facts, or an option group with none marked
- an exclusive group with no box, or more than one box, marked (also checked deterministically
  from `exclusiveGroups` — a violation fails the round even if the critique misses it)
- a name or address split across the wrong lines

Every round writes `out/renders/<formId>/round-<n>-page-<p>.png` and appends to
`out/reports/<formId>-loop.json` with the findings and the diff applied. **The loop's history is
part of the demo** — being able to show round 1 wrong and round 4 right is worth more than a
correct answer with no visible provenance.

Stop conditions: zero findings, or 6 rounds, or two consecutive rounds with an identical finding
set (the loop is stuck — report it rather than spinning).

### 2.3 Gate

`forge bind irs-f56 --estate estate-05-in-formal-probate` converges with zero findings, and the
final render exists on disk. Print the round count.

---

## Stage 3 — Review

**Goal:** a human who is not an engineer approves the artifact in minutes. They never see code.

Single page, served locally by FastAPI. No framework, no build step.

Layout:

- **Left:** the rendered filled page as an image. Page switcher if the form has more than one.
- **Right:** the binding as a list, one row per field, sorted so that `confidence: low` and
  everything in `unbound` sits at the top and is visually marked.
- Each row shows: item number as printed on the paper, the label, the source path, and the actual
  value that was produced for this estate.
- Row actions: approve, edit the source path, or mark unbound with a note.
- **Header:** the work order context — estate, jurisdiction, route, and the `blastRadius` and
  `reversibility` that Warrant supplied.
- **Footer:** one button, "Approve binding". It is disabled while any `required` field is
  unbound.

On approval:

1. `status` becomes `approved`, `version` increments, `approvedBy` and `approvedAt` are set.
2. The artifact is copied to `artifacts/approved/<formId>.v<N>.json` and never modified again.
3. Later edits create version N+1. Approved versions are immutable.

The reviewer is approving **the binding**, not the document. Make that explicit in the UI copy:
one approval covers every future estate that uses this form.

---

## Stage 4 — Fill

**Goal:** produce the document. Deterministic, fast, no model.

```
forge fill irs-f56 --estate estate-02-ca-intestate-independent-admin
```

1. Load the work order. If `applicable` is false, exit 0 and print the reason. Do not produce a
   file.
2. Load the highest approved version of the binding. If none exists, exit non-zero and say the
   form has not been compiled yet — never fall back to a draft.
3. Resolve every binding against the estate. Absent values leave the field empty and are recorded.
4. Write the PDF with `/NeedAppearances` and regenerated appearances.
5. Rasterise to `out/renders/` so the result is inspectable.
6. Emit `out/fills/<estateId>-<formId>.json` alongside the PDF:

```json
{
  "estateId": "…", "formId": "…", "bindingVersion": 1,
  "fieldsTotal": 76, "fieldsFilled": 61, "fieldsEmpty": 15,
  "empty": [ { "itemNumber": "7", "label": "…", "whatWouldFillIt": "…" } ],
  "llmCallsAtRuntime": 0,
  "elapsedMs": 412
}
```

`llmCallsAtRuntime` is produced by an actual counter wired into the model client, not a literal
zero. There is a test that fails if any model call occurs during `fill`. That counter is the
proof of the whole thesis; do not fake it.

### Anvil path

The same binding also drives Anvil. See `docs/03-ANVIL.md`. The local `pypdf` path is the
fallback that guarantees the demo runs without network; the Anvil path is what wins the sponsor
prize. Build local first, Anvil second, and keep both behind the same `forge fill` command with a
`--via anvil` flag.

---

## Stage 5 — Benchmark

```
forge bench
```

Runs every applicable (estate, form) pair from the work orders. Writes
`out/reports/benchmark.json` and a markdown table. Columns:

| Metric | Meaning |
|---|---|
| Fields total | across all applicable pairs |
| Fields bound automatically | count and percentage |
| Fields left empty and reported | count — this is honesty, not failure |
| Rounds to converge, per form | from the loop history |
| Wall time, first estate on a new form | includes calibration and loop |
| Wall time, subsequent estate, same form | should be milliseconds |
| Model calls at build time | per form |
| **Model calls at fill time** | **must read 0** |

Do not report an accuracy figure unless a human has checked the output against the form by hand
and that check is recorded. An unverified accuracy number is exactly the failure mode this
project exists to prevent, and a judge will ask how it was measured.
