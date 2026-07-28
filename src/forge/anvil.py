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

from .registry import ROOT, get_form

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
        elif src["kind"] in ("condition", "absent"):
            alias = _slug(b.get("label") or b["qualifiedName"])[:40] or _slug(b["qualifiedName"])
        else:  # constant
            alias = _slug(b.get("label") or b["qualifiedName"])[:40]
        base, n = alias, 2
        while alias in used:
            alias, n = f"{base}_{n}", n + 1
        used.add(alias)
        aliases[b["qualifiedName"]] = alias
    return aliases


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


CREATE_CAST = """mutation CreateCast($file: Upload!, $aliasIds: JSON) {
  createCast(file: $file, aliasIds: $aliasIds, allowAliasIds: true, isTemplate: true) {
    eid name fieldInfo
  }
}"""

CAST_QUERY = """query Cast($eid: String!) {
  cast(eid: $eid) { eid name fieldInfo }
}"""


def register_cast(
    form_id: str, binding_artifact: dict[str, Any], transport: Transport
) -> dict[str, Any]:
    form = get_form(form_id)
    aliases = alias_map(binding_artifact)
    file_b64 = base64.b64encode(form.path.read_bytes()).decode()
    data = transport.graphql(
        CREATE_CAST,
        {
            "file": {"data": file_b64, "filename": form.path.name, "mimetype": "application/pdf"},
            "aliasIds": [
                {"fieldId": q, "aliasId": a} for q, a in aliases.items()
            ],
        },
    )
    return data["createCast"]


def cast_field_ids(cast: dict[str, Any]) -> set[str]:
    info = cast.get("fieldInfo") or {}
    fields = info.get("fields") if isinstance(info, dict) else info
    ids = set()
    for f in fields or []:
        fid = f.get("aliasId") or f.get("id")
        if fid:
            ids.add(fid)
    return ids


def reconcile(
    binding_artifact: dict[str, Any], cast: dict[str, Any]
) -> dict[str, list[str]]:
    """Both directions. Any entry in either list must refuse the fill."""
    ours = set(alias_map(binding_artifact).values())
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
            "refusing to fill: cast is missing aliases we would write "
            f"(their values would be SILENTLY dropped): {drift['boundButMissingFromCast']}"
        )
    aliases = alias_map(binding_artifact)
    payload = {
        "data": {aliases[q]: v for q, v in fill_values.items() if q in aliases}
    }
    pdf_bytes = transport.fill(cast["eid"], payload)
    if not pdf_bytes.startswith(b"%PDF"):
        raise RuntimeError("Anvil response is not a PDF; refusing to write it")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(pdf_bytes)
    return {"castEid": cast["eid"], "bytes": len(pdf_bytes), "drift": drift}
