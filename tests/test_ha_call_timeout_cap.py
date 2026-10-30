"""`tools.ha.call_timeout_s` cannot exceed the turn's tool timeout (0.8.13).

The field used to accept up to 60 s while `TurnRunner` cancels every tool handler at
`tool_timeout_s` (20 s). Anything above 20 could never be spent: the turn kills the
call first, so the state read-back and the "only part of it worked" reply - the whole
reason the batch path has a budget - never run. The console would also happily store a
number the runtime truncates, which is exactly the kind of config that reads as working
and is not.

Production runs 10.0, which stays the default and the recommendation: with
`SERVICE_BUDGET_FACTOR = 1.5`, even 14 s of per-call budget is a 21 s batch run, i.e.
already past the cancel.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from rui.config import load_config
from rui.config.schema import HA_CALL_TIMEOUT_MAX_S, HaConfig

ROOT = Path(__file__).resolve().parents[1]


def test_the_ceiling_is_the_turn_tool_timeout() -> None:
    """The two numbers are one fact written twice; this is what keeps them equal."""
    from rui.session.turn import TurnRunner

    turn_timeout = inspect.signature(TurnRunner.__init__).parameters["tool_timeout_s"]
    assert HA_CALL_TIMEOUT_MAX_S == turn_timeout.default, (
        "the schema ceiling and TurnRunner.tool_timeout_s drifted apart: a budget above "
        "the turn's cancel is a value that can never be spent"
    )


def test_twenty_is_accepted() -> None:
    assert HaConfig(call_timeout_s=20).call_timeout_s == 20.0
    assert HaConfig(call_timeout_s=20.0).call_timeout_s == 20.0


def test_twenty_one_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        HaConfig(call_timeout_s=21)
    message = str(caught.value)
    assert "call_timeout_s" in message, message
    assert "20" in message, message


def test_the_old_upper_bound_is_gone() -> None:
    """60 was the old cap. Loading it must fail, not load and get truncated later."""
    with pytest.raises(ValidationError):
        HaConfig(call_timeout_s=60)


def test_the_default_is_unchanged() -> None:
    assert HaConfig().call_timeout_s == 10.0


def test_a_config_file_with_the_old_value_is_refused(tmp_path: Path) -> None:
    """The gate is the file loader, not just the submodel: `load_config` must say no."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "tools:\n  ha:\n    call_timeout_s: 30\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:  # ConfigError or pydantic's ValidationError
        load_config(path)
    assert "call_timeout_s" in str(caught.value), str(caught.value)


@pytest.mark.parametrize("name", ["config.example.yaml", "src/rui/config/template.yaml"])
def test_the_shipped_examples_stay_inside_the_cap(name: str) -> None:
    """An example is the value an operator copies: it must be one the schema accepts."""
    import re

    text = (ROOT / name).read_text(encoding="utf-8")
    found = re.search(r"call_timeout_s:\s*([0-9.]+)", text)
    assert found is not None, f"{name} lost call_timeout_s"
    value = float(found.group(1))
    assert 2.0 <= value <= HA_CALL_TIMEOUT_MAX_S, (name, value)
    assert "2-60" not in text and "2–60" not in text, (
        f"{name} still advertises the old 2-60 range")
