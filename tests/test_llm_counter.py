"""The counter that the whole thesis rests on. CLAUDE.md hard rule 1."""

from __future__ import annotations

import pytest

from forge.llm import (
    CountedModelClient,
    ModelCallFailed,
    ModelCallForbidden,
    client,
    extract_json_array,
    forbid_model_calls,
)


def _stub_transport(self, purpose, prompt, model, timeout):
    return '[{"id": "Z001"}]'


def test_calls_are_counted(monkeypatch, tmp_path):
    import forge.llm as llm_mod

    monkeypatch.setattr(llm_mod, "CALL_LOG_DIR", tmp_path / "calls")
    c = CountedModelClient()
    monkeypatch.setattr(CountedModelClient, "_transport", _stub_transport)
    assert c.count == 0
    out = c.call(purpose="calibrate", prompt="x", model="test")
    assert out == '[{"id": "Z001"}]'
    assert c.count == 1
    assert c.calls[0].purpose == "calibrate"


def test_forbidden_block_raises_before_any_transport(monkeypatch):
    def exploding_transport(self, purpose, prompt, model, timeout):  # pragma: no cover
        raise AssertionError("transport must never run inside a forbidden block")

    monkeypatch.setattr(CountedModelClient, "_transport", exploding_transport)
    client.reset()
    with pytest.raises(ModelCallForbidden):
        with forbid_model_calls():
            client.call(purpose="sneaky", prompt="x", model="test")
    assert client.count == 0, "a forbidden call is refused, not recorded"


def test_forbid_restores_previous_state():
    client.reset()
    with forbid_model_calls() as counted:
        assert counted.count == 0
    assert client.forbidden is False


def test_extract_json_array_tolerates_fences():
    assert extract_json_array('noise ```json\n[{"a": 1}]\n``` trailing') == [{"a": 1}]
    with pytest.raises(ModelCallFailed):
        extract_json_array("no array here")
