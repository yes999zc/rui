"""Cold install (P14): `setup` must write a config that can answer, not a schema shell.

A schema-default config was never a working install: empty provider catalog, no
VAD path, no wake model or keywords file, and a playback layout the device adapter
rejects. The first sentence could not happen, so these tests pin the quartet:
setup writes the production template, the keywords file matches the measured gate,
the wake words follow the config, and fetch-models unpacks the wake model.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shlex
import stat
import subprocess
import tarfile
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from rui.__main__ import (
    ASR_URL_ENV,
    KWS_REPO,
    KWS_URL_ENV,
    KWS_URLS,
    VAD_URL_ENV,
    VAD_URLS,
    _asr_urls,
    _download_client,
    _download_first,
    _download_to,
    _fetch_kws_model,
    _url_list,
    cmd_fetch_models,
    main,
)
from rui.audio.kws import keyword_lines, write_keywords_file
from rui.config import Config, load_config, save_config
from rui.config.schema import DeviceConfig, MusicConfig

EXPECTED = "n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞"


def test_setup_writes_the_production_template(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    assert main(["setup", "--config", str(path)]) == 0
    cfg = load_config(path)
    assert cfg.devices == []
    assert cfg.providers.asr.chain == ["local_asr", "siliconflow_asr"]
    # SiliconFlow TTS left the template chain 2026-10-23; Volcano is the fallback.
    assert cfg.providers.tts.chain == ["xfyun_tts", "volc_tts"]
    assert cfg.providers.llm.chain == ["deepseek_llm"]
    assert cfg.audio.voice.kws_enabled is True
    assert cfg.audio.voice.vad_model_path == "/data/models/silero_vad.onnx"
    assert cfg.audio.playback.channels == 1
    assert cfg.music.enabled is False


def test_setup_output_distinguishes_the_installer_path(monkeypatch, capsys, tmp_path) -> None:
    """install.sh already writes .env and fetches models; do not ask for them again."""
    assert main(["setup", "--config", str(tmp_path / "manual.yaml")]) == 0
    manual = capsys.readouterr().out
    assert "fill .env" in manual
    assert "deploy/install.sh" in manual

    monkeypatch.setenv("RUI_INSTALLER", "1")
    assert main(["setup", "--config", str(tmp_path / "installer.yaml")]) == 0
    installer = capsys.readouterr().out
    assert "fill .env" not in installer
    assert "install.sh writes .env" in installer


def test_setup_generates_the_keywords_file_with_the_measured_gate(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    main(["setup", "--config", str(path)])
    keywords = Path(load_config(path).audio.voice.kws_keywords_file)
    assert keywords == tmp_path / "keywords.txt"
    assert keywords.read_text(encoding="utf-8").strip() == EXPECTED


def test_keyword_lines_split_syllables_like_the_wake_model_expects() -> None:
    assert keyword_lines(["你好小瑞"], threshold=0.15, boost=2.0) == [EXPECTED]
    # No initial: the whole syllable is one token; latin words are one token too.
    assert keyword_lines(["小爱"])[0].startswith("x iǎo ài :")
    assert keyword_lines(["小瑞", "小瑞"]) == keyword_lines(["小瑞"])  # de-duplicated
    with pytest.raises(ValueError):
        write_keywords_file("/tmp/should-not-exist.txt", [])


def test_keywords_command_follows_the_config_wake_words(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    main(["setup", "--config", str(path)])
    cfg = load_config(path)
    cfg = cfg.model_copy(update={"devices": [
        DeviceConfig(id="living", adapter="xiaoai", host="speaker.local",
                     room="客厅", wake_words=["嗨小瑞"]),
    ]})
    save_config(path, cfg)
    assert main(["keywords", "--config", str(path)]) == 0
    content = (tmp_path / "keywords.txt").read_text(encoding="utf-8")
    assert content.strip().startswith("h āi x iǎo r uì")
    assert "@嗨小瑞" in content


def test_fetch_models_unpacks_the_wake_model(monkeypatch, tmp_path) -> None:
    """The archive layout is the dependency: fetch it once, then the plane finds it."""
    tarball = tmp_path / "kws.tar.bz2"
    with tarfile.open(tarball, "w:bz2") as tar:
        for name in ("tokens.txt", "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
                     "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
                     "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx"):
            data = f"fake {name}".encode()
            info = tarfile.TarInfo(f"{KWS_REPO}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(tarball.read_bytes())
        return target.stat().st_size

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    args = argparse.Namespace(kws_dest=str(tmp_path / "kws"), kws_url="", force=False)
    assert _fetch_kws_model(args) == 0
    model = tmp_path / "kws" / KWS_REPO
    assert (model / "tokens.txt").exists()
    assert not (tmp_path / "kws" / f"{KWS_REPO}.tar.bz2").exists()  # unpacked, not kept


def test_fetch_models_seeds_vad_and_kws_before_downloading(monkeypatch, tmp_path) -> None:
    """The image seed is the cold-install path: copy first, then the network for ASR only."""
    seed = tmp_path / "seed"
    kws_seed = seed / "kws" / KWS_REPO
    kws_seed.mkdir(parents=True)
    (seed / "silero_vad.onnx").write_bytes(b"seeded vad")
    (kws_seed / "tokens.txt").write_text("tokens", encoding="utf-8")
    (kws_seed / "encoder-chunk-8.int8.onnx").write_bytes(b"encoder")

    calls: list[str] = []

    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"from network")
        return target.stat().st_size

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    args = argparse.Namespace(
        dest=str(tmp_path / "asr"),
        vad_dest=str(tmp_path / "models" / "silero_vad.onnx"),
        vad_base="",
        base="",
        kws_dest=str(tmp_path / "models" / "kws"),
        kws_url="",
        seed_dir=str(seed),
        no_kws=False,
        force=False,
    )
    assert cmd_fetch_models(args) == 0
    assert (tmp_path / "models" / "silero_vad.onnx").read_bytes() == b"seeded vad"
    assert (tmp_path / "models" / "kws" / KWS_REPO / "tokens.txt").exists()
    assert not (tmp_path / "models" / "kws" / f"{KWS_REPO}.tar.bz2").exists()
    # Only the two ASR files came from the network; VAD/KWS never hit a mirror.
    base = ("https://hf-mirror.com/csukuangfj/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main")
    assert calls == [f"{base}/model.int8.onnx", f"{base}/tokens.txt"]


def test_fetch_models_without_seed_keeps_the_network_fallbacks(monkeypatch, tmp_path) -> None:
    """An older image has no /opt/seed-models: behave exactly like today."""
    tarball = tmp_path / "kws.tar.bz2"
    with tarfile.open(tarball, "w:bz2") as tar:
        data = b"tokens"
        info = tarfile.TarInfo(f"{KWS_REPO}/tokens.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    calls: list[str] = []

    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        calls.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(tarball.read_bytes() if url.endswith(".tar.bz2") else b"network")
        return target.stat().st_size

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    args = argparse.Namespace(
        dest=str(tmp_path / "asr"),
        vad_dest=str(tmp_path / "models" / "silero_vad.onnx"),
        vad_base="",
        base="",
        kws_dest=str(tmp_path / "models" / "kws"),
        kws_url="",
        seed_dir=str(tmp_path / "missing-seed"),
        no_kws=False,
        force=False,
    )
    assert cmd_fetch_models(args) == 0
    assert calls[0].startswith("https://hf-mirror.com/") and calls[0].endswith("/model.int8.onnx")
    assert calls[1].startswith("https://hf-mirror.com/") and calls[1].endswith("/tokens.txt")
    assert calls[2] == VAD_URLS[0]
    assert calls[3] == KWS_URLS[0]
    assert (tmp_path / "models" / "silero_vad.onnx").read_bytes() == b"network"
    assert (tmp_path / "models" / "kws" / KWS_REPO / "tokens.txt").exists()


def test_url_env_overrides_take_precedence_with_mirror_fallback(monkeypatch) -> None:
    base = "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-test/resolve/main"
    monkeypatch.setenv(VAD_URL_ENV, "https://models.example/silero_vad.onnx")
    monkeypatch.setenv(KWS_URL_ENV, "https://models.example/kws.tar.bz2")
    monkeypatch.setenv(ASR_URL_ENV, "https://models.example/asr/model.int8.onnx")

    vad_urls = _url_list(VAD_URL_ENV, VAD_URLS)
    kws_urls = _url_list(KWS_URL_ENV, KWS_URLS)
    model_urls, token_urls = _asr_urls(base)
    assert vad_urls == ("https://models.example/silero_vad.onnx", *VAD_URLS)
    assert kws_urls == ("https://models.example/kws.tar.bz2", *KWS_URLS)
    assert model_urls == ("https://models.example/asr/model.int8.onnx",
                          f"{base}/model.int8.onnx")
    assert token_urls == ("https://models.example/asr/tokens.txt", f"{base}/tokens.txt")


def test_url_env_override_falls_through_to_the_builtin_mirror(monkeypatch, tmp_path) -> None:
    override = "https://models.example/silero_vad.onnx"
    monkeypatch.setenv(VAD_URL_ENV, override)
    calls: list[str] = []

    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        calls.append(url)
        if "models.example" in url:
            raise RuntimeError("mirror offline")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"vad")
        return 3

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    target = tmp_path / "silero_vad.onnx"
    assert _download_first(_url_list(VAD_URL_ENV, VAD_URLS), target) == 3
    assert calls == [override, VAD_URLS[0]]
    assert target.read_bytes() == b"vad"


def test_url_env_absent_keeps_the_builtin_mirrors(monkeypatch) -> None:
    base = "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-test/resolve/main"
    for name in (VAD_URL_ENV, KWS_URL_ENV, ASR_URL_ENV):
        monkeypatch.delenv(name, raising=False)
    assert _url_list(VAD_URL_ENV, VAD_URLS) == VAD_URLS
    assert _url_list(KWS_URL_ENV, KWS_URLS) == KWS_URLS
    assert _asr_urls(base) == ((f"{base}/model.int8.onnx",), (f"{base}/tokens.txt",))


def test_vad_download_falls_back_when_the_first_mirror_breaks(monkeypatch, tmp_path) -> None:
    """Regression 2026-10-24: the HF repo behind hf-mirror vanished (401), so the mirror
    list now starts at ghproxy; a dead first mirror must still fall through to GitHub."""
    calls: list[str] = []

    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        calls.append(url)
        if url == VAD_URLS[0]:
            raise RuntimeError("mirror unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"vad")
        return 3

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    target = tmp_path / "silero_vad.onnx"
    assert _download_first(VAD_URLS, target) == 3
    assert calls == [VAD_URLS[0], VAD_URLS[1]]
    assert "github.com" in calls[-1]
    assert target.read_bytes() == b"vad"


def test_vad_download_raises_when_every_mirror_fails(monkeypatch, tmp_path) -> None:
    def fake_download(url: str, target: Path, *, force: bool = False) -> int:
        raise RuntimeError("down")

    monkeypatch.setattr("rui.__main__._download_to", fake_download)
    with pytest.raises(RuntimeError):
        _download_first(("https://a.invalid/x", "https://b.invalid/x"), tmp_path / "x.onnx")


def test_download_uses_bounded_http_timeouts(monkeypatch, tmp_path) -> None:
    """`timeout=None` is what let a stalled direct GitHub attempt hang forever."""
    seen: dict[str, object] = {}

    class Response:
        headers = {"content-length": "4"}

        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self, chunk_size: int):
            yield b"data"

    class Stream:
        def __enter__(self) -> Response:
            return Response()

        def __exit__(self, *exc: object) -> bool:
            return False

    class Client:
        timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def stream(self, method: str, url: str, **kwargs: object) -> Stream:
            seen.update(kwargs)
            return Stream()

    def fake_client(timeout: object) -> Client:
        client = Client()
        client.timeout = timeout
        return client

    monkeypatch.setattr("rui.__main__._download_client", fake_client)
    target = tmp_path / "model.int8.onnx"
    assert _download_to("https://example.invalid/model.int8.onnx", target) == 4
    timeout = seen.get("timeout")
    assert timeout is None  # the client carries the timeout, not each stream call
    assert target.read_bytes() == b"data"


def test_download_client_forces_ipv4() -> None:
    """The NAS has no global IPv6 route; the client must bind to IPv4."""
    client = _download_client(httpx.Timeout(1.0))
    try:
        assert client._transport._pool._local_address == "0.0.0.0"
    finally:
        client.close()


def test_read_timeout_on_the_first_mirror_falls_through_to_the_next(
    monkeypatch, tmp_path
) -> None:
    """A dead first mirror must fail fast enough for `_download_first` to try the next."""
    calls: list[str] = []
    first = "https://direct.invalid/model.int8.onnx"
    second = "https://mirror.invalid/model.int8.onnx"

    class Response:
        headers = {"content-length": "4"}

        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self, chunk_size: int):
            yield b"data"

    class TimeoutResponse(Response):
        def iter_bytes(self, chunk_size: int):
            raise httpx.ReadTimeout("read timed out", request=httpx.Request("GET", first))

    class Stream:
        def __init__(self, response: Response) -> None:
            self._response = response

        def __enter__(self) -> Response:
            return self._response

        def __exit__(self, *exc: object) -> bool:
            return False

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def stream(self, method: str, url: str, **kwargs: object) -> Stream:
            calls.append(url)
            return Stream(TimeoutResponse() if url == first else Response())

    monkeypatch.setattr("rui.__main__._download_client", lambda timeout: Client())
    target = tmp_path / "model.int8.onnx"
    assert _download_first((first, second), target) == 4
    assert calls == [first, second]
    assert target.read_bytes() == b"data"
    assert not list(tmp_path.glob("*.part"))


def test_playback_defaults_are_playable_by_the_device_adapter() -> None:
    cfg = Config()
    assert (cfg.audio.playback.rate, cfg.audio.playback.channels) == (16000, 1)


def test_music_cannot_be_enabled_without_credentials() -> None:
    with pytest.raises(ValidationError):
        MusicConfig(enabled=True, secrets_path="")


# ------------------------------------------------- console token persistence (issue #4)


def _run_install_token_block(
    tmp_path: Path, docker_stdout: str, docker_exit: int = 0
) -> subprocess.CompletedProcess[str]:
    """Run the shipped token-persistence shell block against a fake `docker`.

    The block is extracted from deploy/install.sh by its marker comments, so this
    exercises the real shell rather than a copy: a fresh install must leave a 0600
    file, a rerun must keep it, and a failed read must only warn.
    """
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
    ).read_text(encoding="utf-8")
    start = script.index("# Persist the console token")
    end = script.index('IP="$(hostname -I', start)
    block = script[start:end]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(docker_stdout) + f"\nexit {docker_exit}\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    harness = tmp_path / "token-block.sh"
    harness.write_text('set -eu\nDIR="$1"\n' + block, encoding="utf-8")
    env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    return subprocess.run(
        ["sh", str(harness), str(home)], capture_output=True, text=True, env=env
    )


def test_install_sh_saves_the_console_token_with_mode_600(tmp_path: Path) -> None:
    result = _run_install_token_block(tmp_path, "tok-abc123")
    assert result.returncode == 0, result.stderr
    token_file = tmp_path / "home" / "console-token.txt"
    assert token_file.read_text(encoding="utf-8").strip() == "tok-abc123"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert "console token saved" in result.stdout
    # Pasting install logs must not leak the token (dot2 #13): only the 0600 path is
    # ever printed, never the value itself.
    assert "tok-abc123" not in result.stdout + result.stderr


def test_install_sh_does_not_overwrite_an_existing_console_token(tmp_path: Path) -> None:
    """A rerun/upgrade must not replace a secret the operator already stored."""
    home = tmp_path / "home"
    home.mkdir()
    token_file = home / "console-token.txt"
    token_file.write_text("saved-before\n", encoding="utf-8")
    token_file.chmod(0o600)
    result = _run_install_token_block(tmp_path, "rotated-token")
    assert result.returncode == 0, result.stderr
    assert token_file.read_text(encoding="utf-8") == "saved-before\n"
    assert "does not match the running console token" in result.stdout
    assert "rotated-token" not in result.stdout + result.stderr


def test_install_sh_warns_but_succeeds_when_the_token_cannot_be_read(tmp_path: Path) -> None:
    """An empty/failed token CLI must not fail an otherwise healthy install."""
    result = _run_install_token_block(
        tmp_path, "web.auth_token is empty", docker_exit=1
    )
    assert result.returncode == 0, result.stderr
    assert "could not read the console token" in result.stdout
    assert not (tmp_path / "home" / "console-token.txt").exists()

# ------------------------------------- upgrade hints (dot2 #12 C2 / K2)

def _run_install_upgrade_hint(tmp_path: Path, env_text: str) -> subprocess.CompletedProcess[str]:
    """Run the shipped C2 block against an existing .env (extracted by its marker)."""
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
    ).read_text(encoding="utf-8")
    start = script.index("# Upgrade hint (dot2 #12 C2)")
    end = script.index("# Existing installs upgrading", start)
    block = script[start:end]
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    harness = tmp_path / "hint.sh"
    harness.write_text('set -eu\nDIR="$1"\n' + block, encoding="utf-8")
    return subprocess.run(["sh", str(harness), str(tmp_path)],
                          capture_output=True, text=True)


def test_install_sh_prints_the_pinned_image_on_an_existing_install(tmp_path: Path) -> None:
    """A rerun must say which image is pinned and that upgrading is an explicit edit."""
    result = _run_install_upgrade_hint(
        tmp_path, "RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2\nRUI_UID=1000\n")
    assert result.returncode == 0, result.stderr
    assert "current RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2" in result.stdout
    assert "never rewrites an existing image tag" in result.stdout
    assert "docker compose pull && docker compose up -d" in result.stdout


def test_install_sh_still_works_when_the_pin_is_missing(tmp_path: Path) -> None:
    result = _run_install_upgrade_hint(tmp_path, "RUI_UID=1000\n")
    assert result.returncode == 0, result.stderr
    assert "current RUI_IMAGE=<unset>" in result.stdout


def test_install_sh_mentions_image_pruning_after_rollback() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
    ).read_text(encoding="utf-8")
    assert "docker image prune -f" in script
    # K1: the release note placeholder comment is gone.
    note = (
        Path(__file__).resolve().parents[1] / "docs" / "releases" / "v0.4.2.md"
    ).read_text(encoding="utf-8")
    assert "Verification filled from the 0.4.2 CI run" not in note


# ------------------------------------- .env template (dot2 #13)

NL = chr(10)


def _install_script() -> str:
    return (
        Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
    ).read_text(encoding="utf-8")


def _env_heredoc_template(script: str) -> str:
    """The literal .env template, extracted from the quoted heredoc.

    Reading it out of the script means a comment edit is covered automatically: the
    assertion is "the file on disk is exactly the template", not a copy of it.
    """
    marker = "cat <<'ENV'"
    start = script.index(marker) + len(marker)
    # Skip the rest of the command line (the newline that opens the heredoc body).
    body_start = script.index(NL, start) + 1
    end = script.index(NL + "ENV" + NL, body_start)
    return script[body_start:end] + NL


def _run_install_env_block(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the shipped [3/5] env block in a temp home with a recording fake docker.

    A fake docker is the canary: the unquoted heredoc this replaced executed the
    backticked "docker compose up -d" in the comment while writing .env, so any
    docker invocation here means command substitution happened again.
    """
    script = _install_script()
    start = script.index('echo "[3/5] env"')
    end = script.index('echo "[4/5] local models', start)
    block = script[start:end]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh" + NL + 'echo "$@" >> "$DOCKER_CALLS"' + NL, encoding="utf-8"
    )
    docker.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    harness = tmp_path / "env-block.sh"
    harness.write_text(
        "set -eu" + NL
        + 'DIR="$1"' + NL
        + 'IMAGE="ghcr.io/example/rui:test"' + NL
        + 'RUI_UID="1000"' + NL
        + 'RUI_GID="1000"' + NL
        + block,
        encoding="utf-8",
    )
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "DOCKER_CALLS": str(tmp_path / "docker-calls"),
    }
    result = subprocess.run(
        ["sh", str(harness), str(home)], capture_output=True, text=True, env=env
    )
    return result, home


def test_install_sh_writes_the_env_template_without_expanding_comments(
    tmp_path: Path,
) -> None:
    """The .env must be the template byte for byte; backticks must not run commands."""
    script = _install_script()
    result, home = _run_install_env_block(tmp_path)
    assert result.returncode == 0, result.stderr

    env_text = (home / ".env").read_text(encoding="utf-8")
    match = re.search(r"^RUI_DEVICE_TOKEN=([0-9a-f]{48})$", env_text, re.MULTILINE)
    assert match is not None, env_text
    expected = "".join([
        _env_heredoc_template(script),
        f"RUI_DEVICE_TOKEN={match.group(1)}" + NL,
        "RUI_IMAGE=ghcr.io/example/rui:test" + NL,
        "RUI_UID=1000" + NL,
        "RUI_GID=1000" + NL,
    ])
    assert env_text == expected
    # The two comments that used to be executed are present literally.
    backtick = chr(96)
    assert backtick + "docker compose up -d" + backtick in env_text
    assert backtick + "user:" + backtick in env_text
    # No command substitution ran: the fake docker was never invoked.
    calls = tmp_path / "docker-calls"
    assert not calls.exists(), calls.read_text(encoding="utf-8")
    assert stat.S_IMODE((home / ".env").stat().st_mode) == 0o600
    # The generated device token is never echoed, only its length.
    assert match.group(1) not in result.stdout + result.stderr
    assert "48 hex chars" in result.stdout


def test_install_sh_env_heredoc_delimiter_is_quoted() -> None:
    """Static guard: reverting to an unquoted delimiter re-opens the bug."""
    script = _install_script()
    assert "cat <<'ENV'" in script
    assert re.search(r"<<-?ENV", script) is None
