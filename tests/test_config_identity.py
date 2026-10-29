"""The assistant's identity: who it says it is, and where that text comes from.

0.8.11 A: `persona` / `system_prompt` gained `{version}` (this build's `__version__`)
and `{config_version}` (the config file's format version). Version is deliberately
NOT a config field: a writable version could make the assistant lie about itself.
"""
from __future__ import annotations

import rui
from rui.config.catalog import field_specs
from rui.config.schema import CONFIG_VERSION, AssistantConfig, Config
from rui.session import prompts
from rui.session.turn import TurnRunner

DEFAULT_PERSONA = (
    "你是家里的小助理，叫{name}，跑在 rui {version} 上（家里自建的语音服务，"
    "不是 Home Assistant）。不知道就说不知道，别装懂。"
)


def test_default_persona_names_the_build_verbatim() -> None:
    """The shipped persona is the identity sentence, not a template-looking one."""
    assert AssistantConfig().persona == DEFAULT_PERSONA
    text = prompts.build(AssistantConfig())
    assert f"跑在 rui {rui.__version__} 上" in text
    assert "跑在 rui {version}" not in text          # never reaches the model as a template
    assert "叫小瑞" in text


def test_default_system_prompt_disambiguates_identity_and_ha_version() -> None:
    text = prompts.build(AssistantConfig())
    # asking about "you" or "the version" means rui, not the home automation system
    assert "用户问你是谁、你是什么系统、版本是多少时" in text
    assert f"rui {rui.__version__}" in text
    assert "不是 Home Assistant" in text
    # ... and HA's version must be attributed to HA, not to this assistant
    assert "Home Assistant 的版本时必须明确说成是它的版本" in text


def test_both_placeholders_fill_persona_and_system_prompt() -> None:
    a = AssistantConfig(
        name="小瑞",
        persona="P v={version} c={config_version} n={name} m={max_chars} l={long_chars}",
        system_prompt=(
            "S v={version} c={config_version} n={name} m={max_chars} l={long_chars}"
        ),
    )
    text = prompts.build(a, config_version=2)
    assert f"S v={rui.__version__} c=2 n=小瑞 m=120 l=900" in text
    assert f"P v={rui.__version__} c=2 n=小瑞 m=120 l=900" in text
    # no placeholder survives the format pass
    assert "{version}" not in text and "{config_version}" not in text


def test_config_version_falls_back_to_this_build_for_direct_callers() -> None:
    """No Config at hand (tests, tools): `{config_version}` is this build's format."""
    text = prompts.build(
        AssistantConfig(persona="c={config_version}", system_prompt="")
    )
    assert f"c={CONFIG_VERSION}" in text


def test_an_old_persona_style_is_unchanged() -> None:
    """A config written before 0.8.11 has no placeholders: same output as before."""
    a = AssistantConfig(
        persona="说话像个老兵，简短。",
        system_prompt="只回答用户的问题。",
    )
    text = prompts.build(a)
    assert text.startswith("只回答用户的问题。\n说话像个老兵，简短。")
    assert rui.__version__ not in text
    assert "{version}" not in text


def test_version_is_not_a_config_field() -> None:
    assert "version" not in AssistantConfig.model_fields
    # the only `version` is the config-format version on the root model
    assert Config().version == CONFIG_VERSION
    assert "version" in Config.model_fields
    paths = {field["path"] for field in field_specs(Config())}
    assert "assistant.version" not in paths


def test_put_config_rebuilds_the_prompt_with_the_build_version(tmp_path, monkeypatch) -> None:
    """Saving a persona from the console is live on the next turn (no TTS in this test)."""
    from fastapi.testclient import TestClient

    from rui.config.store import save_config
    from rui.web.api import app as app_module
    from rui.web.api.app import create_app

    async def _no_ack(cfg):  # noqa: ANN001, ARG001 - replaces a TTS render
        return b""

    # The write path renders the acknowledgement cue in the background; it must not
    # synthesise audio here (nightly quiet rule, and no provider keys in a test).
    monkeypatch.setattr(app_module, "render_ack_wav", _no_ack)

    path = tmp_path / "config.yaml"
    cfg = Config(version=2)
    save_config(path, cfg)
    app = create_app(cfg, config_path=path)
    runner = TurnRunner(
        cfg.providers, assistant=cfg.assistant, config_version=cfg.version
    )
    app.state.runner = runner

    client = TestClient(app)
    response = client.put(
        "/api/v1/config",
        json={"assistant": {"persona": "版本{version}，配置{config_version}"}},
    )
    assert response.status_code == 200, response.text
    assert runner.assistant.persona == "版本{version}，配置{config_version}"
    prompt = runner.build_prompt()
    assert f"版本{rui.__version__}，配置{cfg.version}" in prompt
    # the placeholder is gone from the model-facing text
    assert "{config_version}" not in prompt
