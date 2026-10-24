"""P1.2: a fresh install reusing an old secrets file must still answer.

The cold-install template asks for `RUI_LLM_KEY`, while deployments from before
the rename stored `DEEPSEEK_API_KEY`. The old name is a fallback in `secrets.get()` so
the providers keep working, and `doctor` says out loud when that fallback is carrying
the install. Missing both names is never a silent success.
"""
from __future__ import annotations

import pytest

from rui import secrets
from rui.config import Config
from rui.doctor import run_doctor
from rui.providers.openai_compat import MissingKey, _key

LLM_ENV = "RUI_LLM_KEY"
LEGACY_ENV = "DEEPSEEK_API_KEY"
OLD_PREFIX_ENV = "HOUSE_EAR_LLM_KEY"


@pytest.fixture(autouse=True)
def _isolated_keys(tmp_path, monkeypatch):
    """No ambient key may decide these tests, and the store must not leak between them."""
    for name in (LLM_ENV, OLD_PREFIX_ENV, LEGACY_ENV):
        monkeypatch.delenv(name, raising=False)
    secrets.configure(tmp_path)
    yield
    secrets.configure(None)


def test_legacy_env_name_satisfies_the_new_llm_key(monkeypatch) -> None:
    monkeypatch.setenv(LEGACY_ENV, "sk-legacy-env")
    provider = Config().providers.catalog["llm"]
    assert provider.api_key_env == LLM_ENV
    assert secrets.get(LLM_ENV) == "sk-legacy-env"
    assert secrets.has(LLM_ENV) is True
    resolution = secrets.resolve(LLM_ENV)
    assert resolution is not None
    assert resolution.from_alias is True
    assert resolution.found_as == LEGACY_ENV
    assert _key(provider) == "sk-legacy-env"


def test_legacy_secrets_file_satisfies_the_new_llm_key(tmp_path) -> None:
    data_dir = tmp_path / "data"
    secrets.configure(data_dir)
    secrets.put(LEGACY_ENV, "sk-legacy-file")
    provider = Config().providers.catalog["llm"]
    assert secrets.get(LLM_ENV) == "sk-legacy-file"
    assert secrets.has(LLM_ENV) is True
    resolution = secrets.resolve(LLM_ENV)
    assert resolution is not None
    assert resolution.source == "secrets.yaml"
    assert resolution.from_alias is True
    assert _key(provider) == "sk-legacy-file"


def test_explicit_new_name_wins_over_the_legacy_alias(tmp_path, monkeypatch) -> None:
    secrets.configure(tmp_path)
    secrets.put(LEGACY_ENV, "sk-legacy-file")
    monkeypatch.setenv(LLM_ENV, "sk-new-env")
    assert secrets.get(LLM_ENV) == "sk-new-env"
    resolution = secrets.resolve(LLM_ENV)
    assert resolution is not None
    assert resolution.found_as == LLM_ENV
    assert resolution.from_alias is False


def test_doctor_warns_when_the_legacy_name_is_what_makes_the_llm_work(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    secrets.configure(cfg.system.data_dir)
    secrets.put(LEGACY_ENV, "sk-legacy-file")

    report = run_doctor(cfg)
    check = next(c for c in report.checks if c.name == "llm_key")
    assert check.status == "warn"
    assert LLM_ENV in check.value
    assert LEGACY_ENV in check.value
    assert "copy it" in check.hint
    # The overall verdict also depends on optional extras: CI's dev-only env has no
    # sherpa-onnx, so the wake_runtime check fails and the verdict is "unsupported".
    assert report.verdict in {"degraded", "unsupported"}
    # The doctor output is safe to paste into a bug report: no key values.
    assert "sk-legacy-file" not in report.to_text()


def test_doctor_reports_a_missing_key_when_neither_name_is_set(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    provider = cfg.providers.catalog["llm"]
    with pytest.raises(MissingKey):
        _key(provider)

    report = run_doctor(cfg)
    check = next(c for c in report.checks if c.name == "llm_key")
    assert check.status == "warn"
    assert f"missing {LLM_ENV}" in check.value
    assert LEGACY_ENV in check.value
    assert "will not answer" in check.hint
