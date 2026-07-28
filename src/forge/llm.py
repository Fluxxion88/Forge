"""The single, counted path to a model.

Everything that talks to a model goes through `client.call()`. Nothing else may.
That is what makes `llmCallsAtRuntime` in the fill report a measurement rather than a
literal zero — see CLAUDE.md hard rule 1 and docs/02-SPEC.md §4.

Calibration and binding synthesis are allowed to call. `forge fill` runs inside
`forbid_model_calls()`, which raises if anything tries.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from .registry import REPORTS_DIR

DEFAULT_MODEL = os.environ.get("FORGE_MODEL", "sonnet")
CALL_LOG_DIR = REPORTS_DIR / "calls"


class ModelCallForbidden(RuntimeError):
    """Raised when a model call is attempted inside the fill path."""


class ModelCallFailed(RuntimeError):
    """The transport ran but did not return usable output."""


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

    def _record(self, purpose: str, model: str, images: int) -> int:
        with self._lock:
            if self.forbidden:
                raise ModelCallForbidden(
                    f"model call ({purpose!r}) attempted inside a no-model path; "
                    "the fill path must be deterministic"
                )
            self.calls.append(CallRecord(purpose=purpose, model=model, images=images))
            return len(self.calls)

    def call(
        self,
        *,
        purpose: str,
        prompt: str,
        model: str = DEFAULT_MODEL,
        images: int = 0,
        timeout: int = 180,
    ) -> str:
        """One counted vision/text call, transported through the `claude` CLI.

        The prompt names any image paths and instructs the model to Read them; the
        only tool allowed to the subprocess is Read. Counting happens before the
        transport runs, so a forbidden call is refused before it can do anything.
        """
        seq = self._record(purpose, model, images)
        self._log(seq, purpose, "prompt", prompt)
        try:
            reply = self._transport(purpose, prompt, model, timeout)
        except Exception as exc:
            self._log(seq, purpose, "error", f"{type(exc).__name__}: {exc}")
            raise
        self._log(seq, purpose, "reply", reply)
        return reply

    @staticmethod
    def _log(seq: int, purpose: str, kind: str, text: str) -> str:
        """Every prompt and every raw reply lands on disk before anything parses it."""
        slug = re.sub(r"[^A-Za-z0-9]+", "-", purpose).strip("-")
        CALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = CALL_LOG_DIR / f"{seq:03d}-{slug}.{kind}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _transport(self, purpose: str, prompt: str, model: str, timeout: int) -> str:
        try:
            proc = self._spawn(prompt, model, timeout)
        except subprocess.TimeoutExpired as exc:
            raise ModelCallFailed(f"{purpose!r} timed out after {timeout}s") from exc
        if proc.returncode != 0:
            raise ModelCallFailed(
                f"claude CLI exited {proc.returncode} for {purpose!r}: {proc.stderr[:500]}"
            )
        try:
            envelope = json.loads(proc.stdout)
            result = envelope["result"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise ModelCallFailed(f"unparseable CLI envelope for {purpose!r}: {exc}") from exc
        if not isinstance(result, str) or not result.strip():
            raise ModelCallFailed(f"empty model result for {purpose!r}")
        return result

    def _spawn(self, prompt: str, model: str, timeout: int):
        return subprocess.run(
            [
                "claude", "-p",
                "--output-format", "json",
                "--model", model,
                "--allowedTools", "Read",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def extract_json_array(text: str) -> list[Any]:
    """Pull the first JSON array out of a model reply, tolerating code fences."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ModelCallFailed(f"no JSON array in model reply: {text[:200]!r}")
    return json.loads(text[start : end + 1])


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
