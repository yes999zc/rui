"""TTS voice audition: play on the real speaker, browser WAV as fallback.

The user's requested default is to hear the preview in the room, not on the debug
machine. `?output=speaker` synthesises the same PCM, writes one bounded WAV under
`<data_dir>/cache/auditions/`, and queues it on the running audio plane; `output=browser`
(and the old request without an output) still returns `audio/wav`.

The audio plane is faked here: a real speaker is not required to pin the contract
(JSON vs WAV, the path the plane receives, the cache bounds, the no-plane 503).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.audio.protocol import PlayFile
from rui.config import Config
from rui.web.api import app as app_module
from rui.web.api.app import (
    AUDITION_MAX_FILES,
    AUDITION_PREFIX,
    audition_cache_dir,
    create_app,
    sweep_audition_files,
)

PCM = b"\x01\x00" * 1600  # 1600 samples = 100 ms at 16 kHz


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.calls.append((text, voice))
        yield PCM


class FakeMetrics:
    def __init__(self, *, connected: bool = True, owner: str = "") -> None:
        self.speaker_connected = connected
        self.output_owner = owner


class FakeClient:
    def __init__(self, *, fail: bool = False, metrics=None) -> None:
        self.sent: list[object] = []
        self.fail = fail
        self.metrics = metrics

    async def send(self, message: object) -> None:
        if self.fail:
            raise ConnectionError("plane socket closed")
        self.sent.append(message)


class FakeSupervisor:
    def __init__(self, *, running: bool = True, client: FakeClient | None = None) -> None:
        self.running = running
        self.client = client if client is not None else FakeClient()


def _app(tmp_path, monkeypatch, *, plane: FakeSupervisor | None = None,
         data_dir: str | None = None):
    cfg = Config()
    cfg.system.data_dir = data_dir if data_dir is not None else str(tmp_path / "data")
    provider = FakeProvider()
    monkeypatch.setattr(
        app_module, "build_stage",
        lambda providers, stage, sample_rate: [("tts", provider)])
    client = TestClient(create_app(cfg))
    if plane is not None:
        client.app.state.audio = plane
    return client, provider, cfg


def _audition(client, *, output: str | None = None, body: dict | None = None):
    params = {"output": output} if output is not None else None
    return client.post("/api/v1/providers/tts/tts/audition",
                       params=params, json=body or {})


# --------------------------------------------------------------- speaker path

def test_speaker_audition_returns_json_and_queues_a_play_file(tmp_path, monkeypatch):
    plane = FakeSupervisor()
    client, provider, cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = client.post("/api/v1/providers/tts/tts/audition",
                           params={"output": "speaker"},
                           json={"voice": "zh_female_vv_uranus_bigtts",
                                 "text": "你好呀"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    # Exact contract console-voice is building against.
    assert body == {"played": "speaker", "duration_ms": 100,
                    "provider": "tts", "voice": "zh_female_vv_uranus_bigtts"}
    assert provider.calls == [("你好呀", "zh_female_vv_uranus_bigtts")]

    assert len(plane.client.sent) == 1
    message = plane.client.sent[0]
    assert isinstance(message, PlayFile)
    played = Path(message.path)
    cache = (Path(cfg.system.data_dir) / "cache" / "auditions").resolve()
    assert played.is_file()
    assert played.parent == cache
    assert played.name.startswith(AUDITION_PREFIX) and played.suffix == ".wav"
    assert cache in played.parents
    assert played.read_bytes()[:4] == b"RIFF"
    # The response carries no path (containment: the caller never names a file).
    assert "path" not in body and "file" not in body
    assert not list(cache.glob("*.tmp"))


def test_body_output_speaker_wins_over_the_query_value(tmp_path, monkeypatch):
    plane = FakeSupervisor()
    client, _provider, _cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = client.post("/api/v1/providers/tts/tts/audition?output=browser",
                           json={"output": "speaker", "voice": "v1"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["played"] == "speaker"
    assert len(plane.client.sent) == 1


def test_a_busy_speaker_is_queued_not_rejected(tmp_path, monkeypatch):
    """The plane keeps one pending PlayFile behind the active reply: no 409."""
    plane = FakeSupervisor(client=FakeClient(
        metrics=FakeMetrics(connected=True, owner="dialogue")))
    client, _provider, _cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = _audition(client, output="speaker")
    assert response.status_code == 200, response.text
    assert response.json()["played"] == "speaker"
    assert len(plane.client.sent) == 1


# --------------------------------------------------------------- browser path

def test_browser_is_still_the_default_wav(tmp_path, monkeypatch):
    client, provider, cfg = _app(tmp_path, monkeypatch)  # no plane needed
    for kwargs in ({}, {"output": "browser"}):
        response = _audition(client, **kwargs)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content[:4] == b"RIFF"
        assert PCM in response.content
    assert len(provider.calls) == 2
    # No cache directory or plane traffic for browser playback.
    assert not (Path(cfg.system.data_dir) / "cache" / "auditions").exists()


def test_unknown_output_is_422(tmp_path, monkeypatch):
    client, _provider, _cfg = _app(tmp_path, monkeypatch)
    response = _audition(client, output="television")
    assert response.status_code == 422
    assert "browser" in response.json()["detail"] and "speaker" in response.json()["detail"]


# ------------------------------------------------------------- unavailable

def test_no_plane_is_503_with_a_fallback_sentence(tmp_path, monkeypatch):
    client, provider, _cfg = _app(tmp_path, monkeypatch)  # app.state.audio unset
    response = _audition(client, output="speaker")
    assert response.status_code == 503
    assert "output=browser" in response.json()["detail"]
    # Checked before synthesis: the fallback must not cost a TTS call.
    assert provider.calls == []


def test_a_plane_that_is_not_running_is_503(tmp_path, monkeypatch):
    plane = FakeSupervisor(running=False)
    client, provider, _cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = _audition(client, output="speaker")
    assert response.status_code == 503
    assert "audio plane is not running" in response.json()["detail"]
    assert provider.calls == []


def test_a_disconnected_speaker_is_503(tmp_path, monkeypatch):
    plane = FakeSupervisor(client=FakeClient(metrics=FakeMetrics(connected=False)))
    client, _provider, _cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = _audition(client, output="speaker")
    assert response.status_code == 503
    assert "speaker is not connected" in response.json()["detail"]
    assert plane.client.sent == []


def test_a_send_failure_is_503_and_does_not_leak_the_path(tmp_path, monkeypatch):
    plane = FakeSupervisor(client=FakeClient(fail=True))
    client, _provider, cfg = _app(tmp_path, monkeypatch, plane=plane)
    response = _audition(client, output="speaker")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "output=browser" in detail
    assert str(cfg.system.data_dir) not in detail


# ------------------------------------------------------ bounded cache/cleanup

def test_sweep_removes_expired_and_excess_files(tmp_path):
    cache = tmp_path / "cache" / "auditions"
    cache.mkdir(parents=True)
    old = cache / f"{AUDITION_PREFIX}old.wav"
    old.write_bytes(b"old")
    os.utime(old, (1, 1))                       # far in the past
    fresh = []
    for index in range(AUDITION_MAX_FILES + 5):
        path = cache / f"{AUDITION_PREFIX}{index:02d}.wav"
        path.write_bytes(b"x")
        fresh.append(path)
    stale_tmp = cache / f"{AUDITION_PREFIX}half.wav.tmp"
    stale_tmp.write_bytes(b"half")
    os.utime(stale_tmp, (1, 1))
    removed = sweep_audition_files(cache)
    assert removed == 7, (removed, len(list(cache.iterdir())))
    assert not stale_tmp.exists()
    assert not old.exists()
    assert len(list(cache.glob(f"{AUDITION_PREFIX}*.wav"))) == AUDITION_MAX_FILES
    # The newest files survive (the ones that might still be queued).
    assert sorted(fresh)[-1].exists()


def test_a_speaker_audition_sweeps_the_cache_first(tmp_path, monkeypatch):
    plane = FakeSupervisor()
    client, _provider, cfg = _app(tmp_path, monkeypatch, plane=plane)
    cache = Path(cfg.system.data_dir) / "cache" / "auditions"
    cache.mkdir(parents=True)
    stale = cache / f"{AUDITION_PREFIX}stale.wav"
    stale.write_bytes(b"stale")
    os.utime(stale, (1, 1))
    response = _audition(client, output="speaker")
    assert response.status_code == 200, response.text
    assert not stale.exists()
    assert len(list(cache.glob(f"{AUDITION_PREFIX}*.wav"))) == 1


# ------------------------------------------------------------ containment

def test_audition_cache_is_contained_by_data_dir(tmp_path):
    data = tmp_path / "data"
    cache = audition_cache_dir(str(data))
    assert cache == (data / "cache" / "auditions").resolve()
    assert data.resolve() in cache.parents

    with pytest.raises(ValueError, match="data_dir"):
        audition_cache_dir("")

    # A symlinked cache pointing outside the data volume must be refused.
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "cache").mkdir(parents=True, exist_ok=True)
    (data / "cache" / "auditions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escape"):
        audition_cache_dir(str(data))


def test_a_symlinked_cache_never_writes_outside_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "cache").mkdir(parents=True, exist_ok=True)
    (data / "cache" / "auditions").symlink_to(outside, target_is_directory=True)
    plane = FakeSupervisor()
    client, _provider, _cfg = _app(tmp_path, monkeypatch, plane=plane,
                                   data_dir=str(data))
    response = _audition(client, output="speaker")
    assert response.status_code == 500
    assert "escape" in response.json()["detail"]
    assert list(outside.iterdir()) == []
    assert plane.client.sent == []


def test_an_unset_data_dir_is_refused_by_the_helper(tmp_path):
    # create_app itself refuses an empty data_dir (ReminderStore), but the audition
    # helper is the last line of defence: it must never resolve to the cwd.
    with pytest.raises(ValueError, match="data_dir"):
        audition_cache_dir("")
