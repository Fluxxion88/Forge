"""Anvil integration — the sponsor runtime that executes our binding. docs/03-ANVIL.md.

Everything here is deterministic plumbing: register a cast with OUR aliases,
reconcile both directions, fill via REST, write binary bytes. No model calls.

The API key comes from ANVIL_API_KEY (or .env). It is never logged, never put in a
URL, and never included in an exception message. GraphQL reports application errors
with HTTP 200, so every response body is checked for an `errors` array.

Reconciliation is not optional: the fill endpoint silently drops values written to
aliases the template does not have, which produces a clean-looking PDF with a hole
in it. `fill_via_anvil` refuses to fill on any mismatch.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from .registry import ARTIFACTS, ROOT, get_form

GRAPHQL_URL = "https://graphql.useanvil.com/"
FILL_URL = "https://app.useanvil.com/api/v1/fill/{cast_eid}.pdf"


class MissingCredential(RuntimeError):
    pass


def _load_env_key() -> str | None:
    key = os.environ.get("ANVIL_API_KEY")
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANVIL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def require_key() -> str:
    key = _load_env_key()
    if not key:
        raise MissingCredential(
            "ANVIL_API_KEY is not set (env or .env). Sign up at useanvil.com and email "
            "support@useanvil.com with subject 'Alix Hackathon Free Trial'."
        )
    return key


# ------------------------------------------------------------------ aliases

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG.sub("_", text.lower()).strip("_")


def alias_map(binding_artifact: dict[str, Any]) -> dict[str, str]:
    """qualifiedName -> our alias. Path-derived where possible so the vocabulary
    spans forms (`decedent_name_full` means one thing everywhere); unique per cast."""
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for b in binding_artifact["bindings"]:
        src = b["source"]
        if src["kind"] == "path":
            alias = _slug(src["path"])
        elif src["kind"] == "template":
            alias = _slug(src["paths"][0]) + "_joined"
        elif src["kind"] in ("condition", "contains", "absent"):
            item = _slug(b.get("itemNumber") or "")
            body = _slug(b.get("label") or b["qualifiedName"])[:36]
            alias = f"line{item}_{body}" if item else body
        else:  # constant
            alias = _slug(b.get("label") or b["qualifiedName"])[:40]
        # an alias must start with a letter: several Form 56 line-4 captions are bare
        # form numbers ("940", "1041"), which would otherwise produce "940"
        if not alias[:1].isalpha():
            alias = f"box_{alias}"
        base, n = alias, 2
        while alias in used:
            alias, n = f"{base}_{n}", n + 1
        used.add(alias)
        aliases[b["qualifiedName"]] = alias
    return aliases


def detected_names(cast: dict[str, Any]) -> list[str]:
    """Anvil's detected field short-names, in ITS order. `aliasIds` is a positional
    list of strings (verified against the live schema — passing objects fails
    validation), so our aliases have to be reordered onto this."""
    info = cast.get("fieldInfo") or {}
    fields = info.get("fields") if isinstance(info, dict) else info
    return [f.get("name") for f in fields or []]


def aligned_alias_ids(
    binding_artifact: dict[str, Any], order: list[str], calibration: dict[str, Any] | None = None
) -> tuple[list[str], dict[str, str]]:
    """Our aliases arranged in Anvil's detection order.

    Anvil reports the SHORT field name (`f1_01[0]`); our artifacts are keyed by the
    fully qualified name. On Form 56 the 72 short names are unique, which is checked
    here rather than assumed — DL 142 has duplicate short names (docs/00-DOMAIN.md
    §3.3) and a positional mapping built on a collision would silently mis-file values.

    Fields we deliberately left unbound still get an alias, so the cast covers the
    whole form and reconciliation's `inCastButNeverBound` names them honestly.
    """
    aliases = alias_map(binding_artifact)
    by_short: dict[str, str] = {}
    for qname, alias in aliases.items():
        short = qname.split(".")[-1]
        if short in by_short:
            raise RuntimeError(
                f"short name {short!r} is not unique in this binding; a positional "
                "alias mapping would mis-file values. Map by qualified name instead."
            )
        by_short[short] = alias
    for u in binding_artifact.get("unbound") or []:
        q = u.get("qualifiedName") or ""
        short = q.split(".")[-1]
        if short and short not in by_short:
            by_short[short] = f"unbound_{_slug(short)}"

    out: list[str] = []
    for name in order:
        alias = by_short.get(name)
        if alias is None:
            alias = f"undetected_{_slug(name or 'unknown')}"
        out.append(alias)
    return out, aliases


# ------------------------------------------------------------------ transport


class Transport(Protocol):
    """Swappable so reconciliation logic is testable without an account."""

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...
    def fill(self, cast_eid: str, payload: dict[str, Any]) -> bytes: ...


class HttpTransport:
    def __init__(self) -> None:
        self._auth = (require_key(), "")  # key as username, empty password

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import httpx

        r = httpx.post(
            GRAPHQL_URL, json={"query": query, "variables": variables},
            auth=self._auth, timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):  # HTTP 200 does not mean success on GraphQL
            raise RuntimeError(f"Anvil GraphQL error: {json.dumps(body['errors'])[:400]}")
        return body["data"]

    def fill(self, cast_eid: str, payload: dict[str, Any]) -> bytes:
        import httpx

        r = httpx.post(
            FILL_URL.format(cast_eid=cast_eid), json=payload, auth=self._auth, timeout=120
        )
        r.raise_for_status()
        return r.content  # binary PDF bytes — write with no encoding


# Arg names verified by GraphQL introspection against the live schema, not from
# memory: createCast takes `allowedAliasIds: [String]` (not `allowAliasIds`), plus
# `aliasIds: JSON`, `organizationEid`, `title`, `isTemplate` and `detectFields`.
CREATE_CAST = """mutation CreateCast(
  $organizationEid: String, $title: String, $file: Upload!,
  $allowedAliasIds: [String], $aliasIds: JSON
) {
  createCast(
    organizationEid: $organizationEid, title: $title, file: $file,
    isTemplate: true, detectFields: true,
    allowedAliasIds: $allowedAliasIds, aliasIds: $aliasIds
  ) { eid name title isTemplate allowedAliasIds fieldInfo }
}"""

PROBE_CAST = """mutation ProbeCast($organizationEid: String, $title: String, $file: Upload!) {
  createCast(organizationEid: $organizationEid, title: $title, file: $file,
             isTemplate: true, detectFields: true) { eid fieldInfo }
}"""

# An unpublished cast cannot be filled (docs/03-ANVIL.md).
PUBLISH_CAST = """mutation PublishCast($eid: String!, $title: String!) {
  publishCast(eid: $eid, title: $title) {
    eid publishedAt hasBeenPublished publishedVersionNumber
  }
}"""

CAST_QUERY = """query Cast($eid: String!) {
  cast(eid: $eid) {
    eid name title allowedAliasIds fieldInfo hasBeenPublished publishedAt
  }
}"""


def _org_eid() -> str | None:
    eid = os.environ.get("ANVIL_ORG_EID")
    if eid:
        return eid
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANVIL_ORG_EID="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def register_cast(
    form_id: str,
    binding_artifact: dict[str, Any],
    transport: Transport,
    publish: bool = True,
    alias_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Upload the blank PDF as a cast keyed by OUR aliases, then publish it.

    `alias_override` exists for the deliberate-drift demonstration: it lets a caller
    register a cast that is missing an alias the binding will later write, which is
    exactly the silent-failure condition reconciliation has to catch.
    """
    form = get_form(form_id)
    file_b64 = base64.b64encode(form.path.read_bytes()).decode()
    upload = {
        "data": file_b64,
        "filename": form.path.name,
        "mimetype": "application/pdf",
    }

    # Pass 1 — upload with detection only, to learn the order Anvil assigns. It is
    # not guessable: on Form 56 its order differs from the AcroForm order, and
    # `aliasIds` is positional. `updateCast` cannot set aliasIds, so the aliases have
    # to be right at creation; hence two uploads, the first one throwaway.
    probe = transport.graphql(
        PROBE_CAST,
        {"organizationEid": _org_eid(), "title": f"forge {form_id} (probe)", "file": upload},
    )["createCast"]
    order = detected_names(probe)

    alias_ids, aliases = aligned_alias_ids(binding_artifact, order)
    if alias_override is not None:
        # deliberate-drift demonstration: swap specific aliases out of the cast
        alias_ids = [alias_override.get(a, a) for a in alias_ids]
        aliases = {q: alias_override.get(a, a) for q, a in aliases.items()}

    data = transport.graphql(
        CREATE_CAST,
        {
            "organizationEid": _org_eid(),
            "title": f"forge {form_id}",
            "file": upload,
            "allowedAliasIds": sorted(set(alias_ids)),
            "aliasIds": alias_ids,
        },
    )
    cast = data["createCast"]
    if publish:
        transport.graphql(PUBLISH_CAST, {"eid": cast["eid"], "title": f"forge {form_id}"})
        cast = transport.graphql(CAST_QUERY, {"eid": cast["eid"]})["cast"]
    cast["_probeEid"] = probe["eid"]
    cast["_detectedOrder"] = order
    return cast


def cast_field_ids(cast: dict[str, Any]) -> set[str]:
    """The PDF field short-names this cast actually knows about.

    Learned the hard way against a live cast. `allowedAliasIds` comes back populated
    with all 72 of our aliases, which makes it look like the authoritative list — it
    is not. A fill keyed by those aliases returned HTTP 200 and 136 KB of perfectly
    valid PDF with **every one of 32 values silently dropped**; the same fill keyed by
    `fieldInfo.fields[].id` rendered correctly. So `allowedAliasIds` is accepted and
    inert, and reconciling against it reported zero drift on a fill that lost
    everything — precisely the failure docs/03-ANVIL.md says reconciliation exists to
    prevent, hiding inside reconciliation itself.

    What determines whether a value lands is the detected field, so that is what gets
    compared: `name` is the short PDF field name, `id` is Anvil's internal handle.
    """
    info = cast.get("fieldInfo") or {}
    fields = info.get("fields") if isinstance(info, dict) else info
    return {f.get("name") for f in fields or [] if f.get("name")}


def field_id_map(
    binding_artifact: dict[str, Any], cast: dict[str, Any]
) -> dict[str, str]:
    """qualifiedName -> Anvil internal field id, matched on the short PDF name.

    The fill payload must be keyed by Anvil's id. Our alias vocabulary still lives in
    the artifact and still spans forms; this is the last-mile translation, and because
    we compute it we can also refuse when it is incomplete.
    """
    info = cast.get("fieldInfo") or {}
    fields = info.get("fields") if isinstance(info, dict) else info
    by_short: dict[str, str] = {}
    for f in fields or []:
        name, fid = f.get("name"), f.get("id")
        if not name or not fid:
            continue
        if name in by_short:
            raise RuntimeError(
                f"cast reports duplicate short name {name!r}; keying a fill by short "
                "name would mis-file values (docs/00-DOMAIN.md §3.3)"
            )
        by_short[name] = fid
    out: dict[str, str] = {}
    for b in binding_artifact["bindings"]:
        q = b["qualifiedName"]
        short = q.split(".")[-1]
        if short in by_short:
            out[q] = by_short[short]
    return out


def bound_short_names(binding_artifact: dict[str, Any]) -> set[str]:
    """The last dotted segment of each bound field, which is what Anvil reports as
    `name`. Refuses on a collision: DL 142 has fields whose last segment is `0` for
    several different boxes (docs/00-DOMAIN.md §3.3), and matching on a colliding key
    would file a value into the wrong box — worse than not filling at all."""
    seen: dict[str, str] = {}
    for b in binding_artifact["bindings"]:
        q = b["qualifiedName"]
        short = q.split(".")[-1]
        if short in seen:
            raise RuntimeError(
                f"cannot match this binding to an Anvil cast by field name: {short!r} is "
                f"the last name segment of both {seen[short]!r} and {q!r}. Anvil reports "
                "only the short name, so the mapping would be ambiguous."
            )
        seen[short] = q
    return set(seen)


def reconcile(
    binding_artifact: dict[str, Any], cast: dict[str, Any]
) -> dict[str, list[str]]:
    """Both directions, compared on the short PDF field name — the thing that actually
    decides whether a posted value reaches a box. `boundButMissingFromCast` must refuse
    the fill; `inCastButNeverBound` is reported, not fatal (those are the fields we
    deliberately left unbound)."""
    ours = bound_short_names(binding_artifact)
    theirs = cast_field_ids(cast)
    return {
        "boundButMissingFromCast": sorted(ours - theirs),
        "inCastButNeverBound": sorted(theirs - ours),
    }


def fill_via_anvil(
    binding_artifact: dict[str, Any],
    fill_values: dict[str, Any],  # qualifiedName -> value (str for text, bool for checkbox)
    cast: dict[str, Any],
    transport: Transport,
    out_pdf: Path,
) -> dict[str, Any]:
    """Reconcile, refuse on drift, fill, write binary."""
    drift = reconcile(binding_artifact, cast)
    if drift["boundButMissingFromCast"]:
        raise RuntimeError(
            "refusing to fill: the cast does not have these fields we would write "
            "(their values would be SILENTLY dropped and the returned PDF would look "
            f"complete): {drift['boundButMissingFromCast']}"
        )
    ids = field_id_map(binding_artifact, cast)
    unroutable = sorted(q for q in fill_values if q not in ids)
    if unroutable:
        raise RuntimeError(
            "refusing to fill: no Anvil field id for "
            f"{len(unroutable)} value(s) we intend to write: {unroutable[:5]}"
        )
    payload = {"data": {ids[q]: v for q, v in fill_values.items()}}
    pdf_bytes = transport.fill(cast["eid"], payload)
    if not pdf_bytes.startswith(b"%PDF"):
        raise RuntimeError("Anvil response is not a PDF; refusing to write it")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(pdf_bytes)
    return {"castEid": cast["eid"], "bytes": len(pdf_bytes), "drift": drift}


# ------------------------------------------------------------------ cast registry
# The cast eid belongs with the compiled output, but artifacts/approved/<form>.vN.json
# is immutable and chmod 0444 the moment a human approves it (docs/02-SPEC.md §3). So
# the registration lives beside it in its own file, keyed to the binding it was
# registered from — a cast registered against a different binding is not usable, and
# the sha256 recorded here is what proves which one it was.

ANVIL_DIR = ARTIFACTS / "anvil"


def save_cast_registry(
    form_id: str, cast: dict[str, Any], binding_sha256: str, binding_ref: str
) -> Path:
    ANVIL_DIR.mkdir(parents=True, exist_ok=True)
    out = ANVIL_DIR / f"{form_id}.json"
    out.write_text(
        json.dumps(
            {
                "formId": form_id,
                "castEid": cast["eid"],
                "castTitle": cast.get("title"),
                "publishedAt": cast.get("publishedAt"),
                "detectedFieldCount": len(cast_field_ids(cast)),
                "bindingRef": binding_ref,
                "bindingSha256": binding_sha256,
                "probeCastEid": cast.get("_probeEid"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def load_cast_registry(form_id: str) -> dict[str, Any] | None:
    p = ANVIL_DIR / f"{form_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
