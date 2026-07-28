"""Strict JSON path resolution against an estate record.

docs/01-CONTRACT.md: a path that does not resolve returns a sentinel meaning *absent*,
never None conflated with an empty string, and never a guess. Every resolution is
recorded so the review UI can show a field's source path next to its value.

The return type is a small object on purpose: when Warrant lands, a verification
`verdict` becomes one more field here rather than a refactor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


@dataclass(frozen=True)
class Resolution:
    path: str
    value: Any
    present: bool
    reason: str | None = None
    # verdict: reserved for Warrant. Do not populate in this repository.

    @property
    def is_empty(self) -> bool:
        """Present but carrying nothing a form can print."""
        return not self.present or self.value is None or self.value == ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value": self.value,
            "present": self.present,
            "reason": self.reason,
        }


def _parse(path: str) -> list[str | int]:
    segments: list[str | int] = []
    pos = 0
    for m in _SEGMENT.finditer(path):
        if m.start() > pos and path[pos : m.start()] not in (".", ""):
            raise ValueError(f"malformed path {path!r} near offset {pos}")
        key, index = m.group(1), m.group(2)
        segments.append(key if key is not None else int(index))
        pos = m.end()
    if not segments:
        raise ValueError(f"malformed path {path!r}")
    return segments


class EstateData:
    """An estate record plus a log of every path ever asked of it."""

    def __init__(self, data: dict[str, Any], estate_id: str, source: str | None = None):
        self.data = data
        self.estate_id = estate_id
        self.source = source
        self.log: list[Resolution] = []

    @classmethod
    def load(cls, path: str | Path) -> "EstateData":
        p = Path(path)
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        estate_id = (data.get("meta") or {}).get("recordId") or p.stem
        return cls(data, estate_id, source=str(p))

    def resolve(self, path: str) -> Resolution:
        try:
            segments = _parse(path)
        except ValueError as exc:
            r = Resolution(path, None, False, reason=str(exc))
            self.log.append(r)
            return r

        node: Any = self.data
        walked: list[str] = []
        for seg in segments:
            if isinstance(seg, int):
                if not isinstance(node, list) or seg >= len(node):
                    return self._absent(path, walked, seg, node)
                node = node[seg]
            else:
                if not isinstance(node, dict) or seg not in node:
                    return self._absent(path, walked, seg, node)
                node = node[seg]
            walked.append(str(seg))

        r = Resolution(path, node, True)
        self.log.append(r)
        return r

    def _absent(self, path: str, walked: list[str], seg: Any, node: Any) -> Resolution:
        where = ".".join(walked) or "<root>"
        reason = f"no {seg!r} under {where} (found {type(node).__name__})"
        r = Resolution(path, None, False, reason=reason)
        self.log.append(r)
        return r
