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
