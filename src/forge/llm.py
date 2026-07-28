"""The single, counted path to a model.

Everything that talks to a model goes through `client.call()`. Nothing else may.
That is what makes `llmCallsAtRuntime` in the fill report a measurement rather than a
literal zero — see CLAUDE.md hard rule 1 and docs/02-SPEC.md §4.

Calibration and binding synthesis are allowed to call. `forge fill` runs inside
`forbid_model_calls()`, which raises if anything tries.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


class ModelCallForbidden(RuntimeError):
    """Raised when a model call is attempted inside the fill path."""


@dataclass
class CallRecord:
    purpose: str
    model: str
    images: int = 0


@dataclass
class CountedModelClient:
    calls: list[CallRecord] = field(default_factory=list)
    forbidden: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def _record(self, purpose: str, model: str, images: int) -> None:
        with self._lock:
            if self.forbidden:
                raise ModelCallForbidden(
                    f"model call ({purpose!r}) attempted inside a no-model path; "
                    "the fill path must be deterministic"
                )
            self.calls.append(CallRecord(purpose=purpose, model=model, images=images))

    def call(self, *, purpose: str, model: str, images: int = 0, **_: Any) -> Any:
        """Placeholder for the real transport, wired in phase 1.

        It counts first and raises second, so the zero-call guarantee is enforced
        before any provider code exists.
        """
        self._record(purpose, model, images)
        raise NotImplementedError(
            "no model transport is wired yet; phase 1 (calibration) adds it"
        )


client = CountedModelClient()


@contextmanager
def forbid_model_calls() -> Iterator[CountedModelClient]:
    """Assert at runtime that no model is consulted inside this block."""
    previous = client.forbidden
    before = client.count
    client.forbidden = True
    try:
        yield client
    finally:
        client.forbidden = previous
    if client.count != before:  # pragma: no cover - defensive
        raise ModelCallForbidden("model calls were recorded inside a no-model path")
