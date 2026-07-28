# 03 — Anvil

Anvil is a hackathon sponsor. Their CEO is one of the three judges, and there is a $1,000 prize
for the best use of their product, separate from and stackable with the main prize. Getting one
real fill working matters commercially, not just technically.

## What Anvil is, in one paragraph

A PDF form is, to a computer, a picture with boxes on it — nothing in the file says which box
means what. Anvil turns a PDF into something a program can fill: you upload it once, it becomes a
template with an identifier, every box gets a name, and from then on you POST structured data and
receive the filled PDF back. Think of it as a printer that knows exactly where on the page each
value belongs. It never guesses and never improvises.

**What Anvil does not do** is decide that the value at `fiduciary.name.full` belongs in the box it
called `f1_04`. Someone has to build that mapping. Today a human does, by hand, per form, per
jurisdiction. That mapping is exactly what Forge compiles — which makes Anvil the runtime that
executes our artifact, not a bolt-on.

## Access

Do this before anything else; it is not instant.

1. Sign up at `https://www.useanvil.com/signup`
2. Email `support@useanvil.com`, subject **Alix Hackathon Free Trial**, from or naming the signup
   address.

The API key lives in the environment as `ANVIL_API_KEY` and is never committed, never logged, and
never printed in an error message.

## Authentication

HTTP Basic, with the API key as the **username** and an **empty password**. This applies to both
the REST fill endpoint and the GraphQL endpoint.

GraphQL reports application errors with HTTP **200**. A naive status check treats a failed
mutation as success. Always inspect the response body for an `errors` array.

## Operations we use

| Operation | Mechanism |
|---|---|
| Register | `createCast` mutation, uploading the blank PDF, with `aliasIds` set to our own binding aliases so Anvil's field detection maps onto names we chose. Publish it — an unpublished cast cannot be filled. |
| Reconcile | Query the cast for `fieldInfo`, and compare against our binding **in both directions**: fields we bind that the cast lacks, and cast fields we never bind. |
| Fill | `POST https://app.useanvil.com/api/v1/fill/{castEid}.pdf` with JSON. Response body is **binary PDF bytes** — write with no encoding, or you get a corrupt file. |
| Sign | `createEtchPacket` referencing the cast, same field-keyed payload as fill; embedded signing via `generateEtchSignURL`. Out of scope unless time remains. |
| Retrieve | Document-group zip download. Out of scope. |

## Why reconciliation is not optional

**The fill endpoint fails silently.** A value written to an alias the template does not have is
dropped. No error, no warning — you receive a PDF that looks entirely correct with one empty box
in the middle of it. On a filing that is a rejection and another month of a grieving family's
life.

So `forge fill --via anvil` runs reconciliation first and refuses to fill on a mismatch. Making
drift visible before anyone files is the whole point; discovering it afterwards is worthless.

This is also a genuinely good thirty seconds of demo: show a deliberate alias mismatch, show
Anvil returning a clean-looking PDF with a hole in it, then show reconciliation catching it.

## Alias strategy

Our binding artifact already names every field. Register the cast with **our** aliases rather
than accepting Anvil's generated ones. That way:

- The same alias vocabulary spans every form, so `decedent_full_name` means one thing everywhere.
- Adding the hundredth form costs the same as adding the second — which is precisely what Ian
  meant by "thousands of forms, not five or six."
- Reconciliation compares two lists we control.

Store the returned cast identifier on the binding artifact as `anvilCastEid`. It is part of the
compiled output.

## Order of work

1. **DL 142 first.** It is single-page, has no XFA layer, has human-readable field names and only
   51 fields. It will register and fill with the fewest unknowns. Get one real filled PDF back
   from Anvil before touching the IRS forms.
2. **Form 56 second.** Two pages, XFA hybrid, 76 fields. This is the real test.
3. SS-4 and 8821 after that, if time allows.

## Local fallback is mandatory

The `pypdf` fill path is not a stepping stone to be discarded. It is the guarantee that the demo
runs on venue wifi. Keep both paths behind one command:

```
forge fill irs-f56 --estate estate-05-…              # local, always works
forge fill irs-f56 --estate estate-05-… --via anvil  # sponsor path
```

Both must produce a PDF from the same approved binding. If the two outputs disagree on which
fields ended up populated, that is a bug worth finding before the demo, not after.

## Do not

- Do not put the API key in a URL, a log line, or an exception message.
- Do not retry a failed fill in a loop without backoff; there are rate limits.
- Do not use Anvil's field auto-detection as the source of truth for meaning. It finds boxes; it
  does not know that box 1b means the decedent died intestate. That is what calibration is for.
