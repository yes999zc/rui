"""The one-rename bridge: RUI_* is canonical, HOUSE_EAR_* still answers."""
from __future__ import annotations

import pytest

from rui import env, secrets


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RUI_LLM_KEY", raising=False)
    monkeypatch.delenv("HOUSE_EAR_LLM_KEY", raising=False)
    monkeypatch.delenv("RUI_CONFIG", raising=False)
    monkeypatch.delenv("HOUSE_EAR_CONFIG", raising=False)
    secrets.configure(tmp_path)
    yield
    secrets.configure(None)


def test_canonical_name_and_legacy_name_are_symmetric() -> None:
    assert env.canonical_name("HOUSE_EAR_DEVICE_TOKEN") == "RUI_DEVICE_TOKEN"
    assert env.canonical_name("RUI_DEVICE_TOKEN") == "RUI_DEVICE_TOKEN"
    assert env.canonical_name("DEEPSEEK_API_KEY") == "DEEPSEEK_API_KEY"
    assert env.legacy_name("RUI_DEVICE_TOKEN") == "HOUSE_EAR_DEVICE_TOKEN"
    assert env.legacy_name("DEEPSEEK_API_KEY") == ""
    assert env.names("HOUSE_EAR_DEVICE_TOKEN") == ("RUI_DEVICE_TOKEN", "HOUSE_EAR_DEVICE_TOKEN")


def test_rui_wins_and_house_ear_legacy_is_the_fallback(monkeypatch) -> None:
    monkeypatch.setenv("HOUSE_EAR_LLM_KEY", "legacy")
    assert env.get("RUI_LLM_KEY") == "legacy"
    assert env.is_set("RUI_LLM_KEY") is True

    monkeypatch.setenv("RUI_LLM_KEY", "canonical")
    assert env.get("RUI_LLM_KEY") == "canonical"
    # A legacy spelling in the config asks for the same variable, canonical first.
    assert env.get("HOUSE_EAR_LLM_KEY") == "canonical"


def test_unset_variables_fall_back_to_the_default(monkeypatch) -> None:
    assert env.get("RUI_LLM_KEY") == ""
    assert env.get("RUI_LLM_KEY", "missing") == "missing"
    assert env.is_set("RUI_LLM_KEY") is False


def test_secrets_resolves_the_canonical_name_then_the_old_prefix(monkeypatch) -> None:
    monkeypatch.setenv("HOUSE_EAR_LLM_KEY", "from-old-prefix")
    resolution = secrets.resolve("RUI_LLM_KEY")
    assert resolution is not None
    assert resolution.value == "from-old-prefix"
    assert resolution.found_as == "HOUSE_EAR_LLM_KEY"
    assert resolution.from_alias is True

    monkeypatch.setenv("RUI_LLM_KEY", "from-canonical")
    resolution = secrets.resolve("RUI_LLM_KEY")
    assert resolution is not None
    assert resolution.value == "from-canonical"
    assert resolution.found_as == "RUI_LLM_KEY"
    assert resolution.from_alias is False
