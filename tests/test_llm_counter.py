"""The counter that the whole thesis rests on. CLAUDE.md hard rule 1."""

from __future__ import annotations

import pytest

from forge.llm import CountedModelClient, ModelCallForbidden, client, forbid_model_calls


def test_calls_are_counted():
    c = CountedModelClient()
    assert c.count == 0
    with pytest.raises(NotImplementedError):
        c.call(purpose="calibrate", model="test")
    assert c.count == 1
    assert c.calls[0].purpose == "calibrate"


def test_forbidden_block_raises_before_any_transport():
    client.reset()
    with pytest.raises(ModelCallForbidden):
        with forbid_model_calls():
            client.call(purpose="sneaky", model="test")
    assert client.count == 0, "a forbidden call is refused, not recorded"


def test_forbid_restores_previous_state():
    client.reset()
    with forbid_model_calls() as counted:
        assert counted.count == 0
    assert client.forbidden is False
