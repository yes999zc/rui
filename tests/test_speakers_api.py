"""Card B: ``/api/v2/speakers`` enrollment (state machine, files, routes).

The plane is faked at its only seam (``AudioPlaneClient.send``): the fake writes
the wav + sidecar the real plane writes, so the tests exercise the same
completion rule the deployment uses (wav present **and** sidecar parsed). The
embedder is faked too - the real one needs a 28 MB ONNX model the CI does not
ship - and the store is a shared in-memory implementation of Card A's
``SpeakerLibrary`` contract. One ``importorskip`` test runs the same flow against
the real library once Card A lands, so the wire format stays honest.
"""
from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import numpy as np
import pytest
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from rui.web.api import speakers as speakers_module
from rui.web.api.speakers import install_speaker_routes, sample_path
from rui.web.api.v2_api import V2Error

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
RATE = 16000


# --------------------------------------------------------------------------- doubles


class FakeStore:
    """The subset of ``SpeakerLibrary`` this API uses, kept across requests."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.saves = 0

    def profiles(self) -> list[SimpleNamespace]:
        return list(self.rows)

    def add(self, name: str, device_id: str, embedding: Any, *, samples: int = 1):
        self.rows = [row for row in self.rows
                     if not (row.name == name and row.device_id == device_id)]
        row = SimpleNamespace(
            name=name,
            device_id=device_id,
            embedding=[float(value) for value in embedding],
            dim=len(embedding),
            enrolled_at=time.time(),
            samples=samples,
        )
        self.rows.append(row)
        self.save()
        return row

    def remove(self, name: str, device_id: str | None = None) -> int:
        keep = [row for row in self.rows
                if not (row.name == name and (device_id is None or row.device_id == device_id))]
        removed = len(self.rows) - len(keep)
        self.rows = keep
        if removed:
            self.save()
        return removed

    def save(self) -> None:
        self.saves += 1


class FakeEmbedder:
    available = True

    def __init__(self, vector: list[float] | None = None, *, silence: bool = False) -> None:
        self.vector = vector if vector is not None else [1.0, 0.0, 0.0, 0.0]
        self.silence = silence
        self.calls: list[tuple[Any, int]] = []

    @property
    def dim(self) -> int:
        return len(self.vector)

    def embed(self, pcm: Any, rate: int):
        self.calls.append((pcm, rate))
        if self.silence:
            return None
        return np.asarray(self.vector, dtype=np.float32)


class FakePlane:
    """Writes what the plane writes: the wav first, then the sidecar."""

    def __init__(self, *, write: bool = True, ms: float | None = None,
                 speech_ms: float | None = None, sidecar_device: str | None = None,
                 sidecar_id: str | None = None, omit_speech_ms: bool = False) -> None:
        self.write = write
        self.ms = ms
        self.speech_ms = speech_ms
        self.omit_speech_ms = omit_speech_ms
        self.sidecar_device = sidecar_device
        self.sidecar_id = sidecar_id
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)
        if not self.write:
            return
        path = Path(message.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = int(RATE * message.seconds)
        tmp = path.with_name(path.name + ".tmp")
        with wave.open(str(tmp), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(b"\x00\x00" * frames)
        tmp.replace(path)
        meta = {
            "enroll_id": self.sidecar_id if self.sidecar_id is not None else message.enroll_id,
            "device_id": (self.sidecar_device if self.sidecar_device is not None
                          else message.device_id),
            "path": str(path),
            "ms": self.ms if self.ms is not None else message.seconds * 1000.0,
        }
        if not self.omit_speech_ms:
            meta["speech_ms"] = (self.speech_ms if self.speech_ms is not None
                                 else message.seconds * 1000.0)
        path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")


class FakePlaneFactory:
    def __init__(self, **kwargs: Any) -> None:
        self.plane = FakePlane(**kwargs)

    def __call__(self, device_id: str) -> FakePlane | None:
        return self.plane


def make_config(tmp_path: Path, *, min_speech_ms: int = 600, threshold: float = 0.55,
                model_path: str = "model.onnx") -> Any:
    gate = SimpleNamespace(model_path=model_path, min_speech_ms=min_speech_ms,
                           threshold=threshold)
    return SimpleNamespace(
        system=SimpleNamespace(data_dir=str(tmp_path / "data")),
        devices=[SimpleNamespace(id="living", enabled=True),
                 SimpleNamespace(id="dot2", enabled=True),
                 SimpleNamespace(id="offline", enabled=False)],
        audio=SimpleNamespace(voice=SimpleNamespace(speaker_gate=gate)),
    )


def build_client(
    tmp_path: Path,
    *,
    plane: Any = None,
    embedder: FakeEmbedder | None = None,
    store: FakeStore | None = None,
    store_factory: Any = None,
    config: Any = None,
) -> tuple[TestClient, FakeStore, FakeEmbedder]:
    shared_store = store if store is not None else FakeStore()
    shared_embedder = embedder if embedder is not None else FakeEmbedder()

    def auth_dep(x_auth_token: str | None = Header(default=None)) -> None:
        if x_auth_token != TOKEN:
            raise V2Error(401, "unauthorized", "bad or missing X-Auth-Token")

    app = FastAPI()

    @app.exception_handler(V2Error)
    async def _handle(_request: Any, exc: V2Error) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message,
                               "details": exc.details}},
        )

    install_speaker_routes(
        app,
        auth_dep=auth_dep,
        config_now=lambda: config if config is not None else make_config(tmp_path),
        error_cls=V2Error,
        plane_client=plane if plane is not None else FakePlaneFactory(),
        store_factory=(store_factory
                       if store_factory is not None
                       else lambda _data_dir, _threshold: shared_store),
        embedder_factory=lambda _model_path: shared_embedder,
    )
    return TestClient(app), shared_store, shared_embedder


def enroll(client: TestClient, **body: Any):
    payload = {"name": "宝妈", "device_id": "living", "seconds": 3}
    payload.update(body)
    return client.post("/api/v2/speakers/enroll", json=payload, headers=AUTH)


def commit(client: TestClient, enroll_id: str):
    return client.post(f"/api/v2/speakers/enroll/{enroll_id}/commit", headers=AUTH)


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 409 test must not sit through the production 2 s wait."""
    monkeypatch.setattr(speakers_module, "COMMIT_WAIT_S", 0.05)


# --------------------------------------------------------------------------- happy path


def test_enroll_then_commit_stores_profile_and_sample(tmp_path: Path) -> None:
    factory = FakePlaneFactory()
    client, store, embedder = build_client(tmp_path, plane=factory)

    response = enroll(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["device_id"] == "living"
    assert body["seconds"] == 3
    assert body["started_at"]
    enroll_id = body["enroll_id"]

    started = factory.plane.sent[0]
    assert started.type == "enroll_start"
    assert started.enroll_id == enroll_id
    assert started.device_id == "living"
    assert started.seconds == 3
    assert started.path == str(tmp_path / "data" / "enroll" / f"{enroll_id}.wav")

    done = commit(client, enroll_id)
    assert done.status_code == 200, done.text
    payload = done.json()
    assert payload["name"] == "宝妈"
    assert payload["device_id"] == "living"
    assert payload["samples"] == 1
    assert payload["dim"] == 4
    assert payload["score"] is None
    assert payload["reference"] == "none"
    assert payload["speech_ms"] == 3000

    # The library got the embedding; the temp pair is gone and the sample stays.
    assert len(store.rows) == 1
    assert store.rows[0].embedding == [1.0, 0.0, 0.0, 0.0]
    assert store.saves == 1
    assert embedder.calls and embedder.calls[0][1] == RATE
    kept = sample_path(tmp_path / "data", "宝妈", "living")
    assert kept.is_file()
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.wav").exists()
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.json").exists()

    listing = client.get("/api/v2/speakers", headers=AUTH)
    assert listing.status_code == 200
    body = listing.json()
    assert len(body) == 1
    assert body[0]["name"] == "宝妈"
    assert body[0]["threshold"] == 0.55
    devices = body[0]["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == "living"
    assert devices[0]["samples"] == 1
    assert devices[0]["sample"] is True
    assert "T" in devices[0]["enrolled_at"]  # ISO 8601 for the console


def test_rerecord_overwrites_and_scores_against_the_previous_profile(tmp_path: Path) -> None:
    store = FakeStore()
    client, _, _ = build_client(tmp_path, store=store)

    first = enroll(client)
    assert commit(client, first.json()["enroll_id"]).status_code == 200

    # A different voice this time: the console shows how far it moved.
    other = FakeEmbedder(vector=[0.0, 1.0, 0.0, 0.0])
    client2, _, _ = build_client(tmp_path, store=store, embedder=other)
    second = enroll(client2)
    done = commit(client2, second.json()["enroll_id"])
    assert done.status_code == 200, done.text
    assert done.json()["score"] == 0.0
    assert done.json()["reference"] == "previous"
    assert len(store.rows) == 1  # overwritten, not appended


def test_commit_uses_real_speaker_library_when_available(tmp_path: Path) -> None:
    """Card A's ``SpeakerLibrary`` is the contract the fake above only imitates."""
    pytest.importorskip("rui.audio.speaker_gate")
    from rui.audio.speaker_gate import SpeakerLibrary

    library = SpeakerLibrary(str(tmp_path / "data"), threshold=0.55)
    library.load()
    client, _, _ = build_client(
        tmp_path, store_factory=lambda _data_dir, _threshold: library)

    response = enroll(client)
    assert response.status_code == 201, response.text
    done = commit(client, response.json()["enroll_id"])
    assert done.status_code == 200, done.text
    profiles = library.profiles()
    assert len(profiles) == 1
    assert profiles[0].name == "宝妈"
    assert profiles[0].device_id == "living"
    assert done.json()["dim"] == profiles[0].dim
    # speakers.json is on disk, which is what the gate hot-reloads.
    assert (tmp_path / "data" / "speakers.json").is_file()


def test_two_devices_can_record_at_the_same_time(tmp_path: Path) -> None:
    """The conflict is per device (one mic), not per person."""
    client, store, _ = build_client(tmp_path, plane=FakePlaneFactory(write=False))
    assert enroll(client, name="宝妈", device_id="living").status_code == 201
    assert enroll(client, name="宝妈", device_id="dot2").status_code == 201


def test_sample_is_owner_only(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    enroll_id = enroll(client).json()["enroll_id"]
    assert commit(client, enroll_id).status_code == 200
    kept = sample_path(tmp_path / "data", "宝妈", "living")
    assert kept.stat().st_mode & 0o777 == 0o600


# --------------------------------------------------------------------------- 404


def test_enroll_unknown_device_is_404(tmp_path: Path) -> None:
    client, store, _ = build_client(tmp_path)
    response = enroll(client, device_id="kitchen")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert store.rows == []


def test_enroll_disabled_device_is_404(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    assert enroll(client, device_id="offline").status_code == 404


def test_commit_unknown_enrollment_is_404(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    assert commit(client, "nope").status_code == 404


def test_sample_of_unknown_speaker_is_404(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    response = client.get("/api/v2/speakers/宝妈/sample.wav?device_id=living", headers=AUTH)
    assert response.status_code == 404


# --------------------------------------------------------------------------- 409


def test_second_enroll_on_the_same_device_is_409(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path, plane=FakePlaneFactory(write=False))
    assert enroll(client).status_code == 201
    response = enroll(client, name="爸爸", seconds=2)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_busy"


def test_enroll_without_a_plane_is_409(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path, plane=lambda _device_id: None)
    response = enroll(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plane_not_connected"


def test_commit_before_the_recording_finishes_is_409(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path, plane=FakePlaneFactory(write=False))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_incomplete"


def test_commit_rejects_a_sidecar_from_another_device(tmp_path: Path) -> None:
    client, store, _ = build_client(tmp_path, plane=FakePlaneFactory(sidecar_device="dot2"))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_mismatch"
    assert store.rows == []


# --------------------------------------------------------------------------- 400


@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "   "},
    {"name": "长" * 21},
    {"name": 42},
    {"device_id": ""},
    {"device_id": None},
    {"seconds": 0},
    {"seconds": 11},
    {"seconds": "3"},
    {"seconds": True},
])
def test_enroll_validation_is_400(tmp_path: Path, body: dict[str, Any]) -> None:
    client, _, _ = build_client(tmp_path)
    response = enroll(client, **body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "bad_request"


def test_enroll_without_body_is_400(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    response = client.post("/api/v2/speakers/enroll", headers=AUTH)
    assert response.status_code == 400


def test_short_speech_is_400_and_stores_nothing(tmp_path: Path) -> None:
    client, store, _ = build_client(tmp_path, plane=FakePlaneFactory(speech_ms=200))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "speech_too_short"
    assert store.rows == []
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.wav").exists()
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.json").exists()


def test_no_identifiable_speech_is_400(tmp_path: Path) -> None:
    client, store, _ = build_client(tmp_path, embedder=FakeEmbedder(silence=True))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_speech"
    assert store.rows == []


# --------------------------------------------------------------------------- expiry


def test_uncommitted_enroll_expires_and_is_swept(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speakers_module, "ENROLL_TTL_S", 0.01)
    client, _, _ = build_client(tmp_path, plane=FakePlaneFactory(write=False))
    enroll_id = enroll(client).json()["enroll_id"]
    wav = tmp_path / "data" / "enroll" / f"{enroll_id}.wav"
    wav.write_bytes(b"partial")
    time.sleep(0.05)

    assert commit(client, enroll_id).status_code == 404
    assert not wav.exists()
    # The device is free again: the stale session no longer blocks a new one.
    assert enroll(client).status_code == 201


# --------------------------------------------------------------------------- listing / delete / sample


def seed(store: FakeStore, rows: list[tuple[str, str, list[float]]]) -> None:
    for name, device_id, vector in rows:
        store.add(name, device_id, np.asarray(vector, dtype=np.float32))


def test_list_aggregates_by_name(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0]), ("宝妈", "dot2", [1.0, 0.0]),
                 ("爸爸", "living", [0.0, 1.0])])
    client, _, _ = build_client(tmp_path, store=store)
    response = client.get("/api/v2/speakers", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["宝妈", "爸爸"]
    by_name = {item["name"]: item for item in body}
    assert [row["device_id"] for row in by_name["宝妈"]["devices"]] == ["dot2", "living"]
    assert by_name["宝妈"]["threshold"] == 0.55
    assert all(row["samples"] == 1 for row in by_name["宝妈"]["devices"])


def test_delete_removes_one_device(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0]), ("宝妈", "dot2", [1.0, 0.0])])
    data_dir = tmp_path / "data"
    for device in ("living", "dot2"):
        path = sample_path(data_dir, "宝妈", device)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
    client, _, _ = build_client(tmp_path, store=store)

    response = client.delete("/api/v2/speakers/宝妈?device_id=living", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"removed": 1}
    assert not sample_path(data_dir, "宝妈", "living").exists()
    assert sample_path(data_dir, "宝妈", "dot2").is_file()
    assert [row.device_id for row in store.rows] == ["dot2"]


def test_delete_person_removes_every_device(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0]), ("宝妈", "dot2", [1.0, 0.0])])
    client, _, _ = build_client(tmp_path, store=store)
    response = client.delete("/api/v2/speakers/宝妈", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"removed": 2}
    assert store.rows == []
    # A second delete is a genuine 404, not a silent success.
    assert client.delete("/api/v2/speakers/宝妈", headers=AUTH).status_code == 404


def test_delete_unknown_speaker_is_404(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    assert client.delete("/api/v2/speakers/没人", headers=AUTH).status_code == 404


def test_sample_wav_is_served(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0])])
    path = sample_path(tmp_path / "data", "宝妈", "living")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF\x00\x00")
    client, _, _ = build_client(tmp_path, store=store)

    response = client.get("/api/v2/speakers/宝妈/sample.wav?device_id=living", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF\x00\x00"


def test_sample_without_device_id_is_409_when_ambiguous(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0]), ("宝妈", "dot2", [1.0, 0.0])])
    client, _, _ = build_client(tmp_path, store=store)
    response = client.get("/api/v2/speakers/宝妈/sample.wav", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_required"
    assert response.json()["error"]["details"]["devices"] == ["dot2", "living"]


# --------------------------------------------------------------------------- auth + wiring


def test_every_route_requires_the_token(tmp_path: Path) -> None:
    client, _, _ = build_client(tmp_path)
    assert client.get("/api/v2/speakers").status_code == 401
    assert client.post("/api/v2/speakers/enroll", json={"name": "x", "device_id": "living"}
                       ).status_code == 401
    assert client.post("/api/v2/speakers/enroll/x/commit").status_code == 401
    assert client.delete("/api/v2/speakers/x").status_code == 401
    assert client.get("/api/v2/speakers/x/sample.wav").status_code == 401


def test_routes_are_wired_into_the_real_v2_app(tmp_path: Path, monkeypatch) -> None:
    """The installer must actually run inside ``create_app`` (auth + error shape)."""
    from rui.config import Config, DeviceConfig, save_config
    from rui.web.api.app import create_app

    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    config = Config(
        devices=[DeviceConfig(id="living", adapter="lx06", host="speaker.local",
                              room="客厅", params={"plug_id": "xiaoai-lx06"})],
        web={"auth_token": TOKEN},
    )
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    client = TestClient(create_app(config, config_path=path))

    assert client.get("/api/v2/speakers").status_code == 401
    unknown = client.post("/api/v2/speakers/enroll",
                          json={"name": "宝妈", "device_id": "kitchen"}, headers=AUTH)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"
    # A configured device with no running plane is a conflict, not a crash.
    offline = client.post("/api/v2/speakers/enroll",
                          json={"name": "宝妈", "device_id": "living"}, headers=AUTH)
    assert offline.status_code == 409
    assert offline.json()["error"]["code"] == "plane_not_connected"


# --------------------------------------------------------------------------- review fixes


def test_explicit_zero_speech_is_rejected(tmp_path: Path) -> None:
    """A plane that heard nothing writes speech_ms=0; that is not "old sidecar"."""
    client, store, _ = build_client(tmp_path, plane=FakePlaneFactory(speech_ms=0.0))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "speech_too_short"
    assert store.rows == []
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.wav").exists()
    assert not (tmp_path / "data" / "enroll" / f"{enroll_id}.json").exists()


def test_sidecar_without_speech_ms_falls_back_to_full_length(tmp_path: Path) -> None:
    client, store, _ = build_client(tmp_path, plane=FakePlaneFactory(omit_speech_ms=True))
    enroll_id = enroll(client).json()["enroll_id"]
    response = commit(client, enroll_id)
    assert response.status_code == 200, response.text
    assert response.json()["speech_ms"] == 3000
    assert len(store.rows) == 1


def test_whitespace_in_a_name_is_one_key(tmp_path: Path) -> None:
    """Card A keys profiles by `" ".join(raw.split())`; the API must do the same."""
    store = FakeStore()
    client, _, _ = build_client(tmp_path, store=store)

    first = enroll(client, name="张  三")
    assert first.status_code == 201
    assert commit(client, first.json()["enroll_id"]).status_code == 200
    assert [row.name for row in store.rows] == ["张 三"]

    second = enroll(client, name="张 三")
    assert second.status_code == 201
    assert commit(client, second.json()["enroll_id"]).status_code == 200
    assert len(store.rows) == 1

    kept = sample_path(tmp_path / "data", "张 三", "living")
    assert kept.is_file()
    # The same person, typed with a double space, still finds the sample ...
    sample = client.get(f"/api/v2/speakers/{quote('张  三', safe='')}/sample.wav?device_id=living",
                        headers=AUTH)
    assert sample.status_code == 200
    # ... and DELETE removes exactly that row and its sample file.
    removed = client.delete(f"/api/v2/speakers/{quote('张  三', safe='')}", headers=AUTH)
    assert removed.status_code == 200
    assert removed.json() == {"removed": 1}
    assert store.rows == []
    assert not kept.exists()


def test_orphan_recordings_are_swept_after_the_ttl(tmp_path: Path) -> None:
    """A plane that finished after its session was dropped must not leak voice data."""
    client, _, _ = build_client(tmp_path)
    folder = tmp_path / "data" / "enroll"
    folder.mkdir(parents=True)
    stale = time.time() - 120
    old_wav = folder / "deadbeef.wav"
    old_json = folder / "deadbeef.json"
    old_wav.write_bytes(b"RIFF")
    old_json.write_text("{}", encoding="utf-8")
    os.utime(old_wav, (stale, stale))
    os.utime(old_json, (stale, stale))
    fresh = folder / "cafe.wav"
    fresh.write_bytes(b"RIFF")  # could still be a plane writing right now

    assert client.get("/api/v2/speakers", headers=AUTH).status_code == 200
    assert not old_wav.exists()
    assert not old_json.exists()
    assert fresh.exists()


def test_deleting_a_person_mid_recording_leaves_no_orphan(tmp_path: Path) -> None:
    store = FakeStore()
    seed(store, [("宝妈", "living", [1.0, 0.0])])
    client, _, _ = build_client(tmp_path, store=store, plane=FakePlaneFactory(write=False))
    enroll_id = enroll(client).json()["enroll_id"]

    assert client.delete("/api/v2/speakers/宝妈", headers=AUTH).json() == {"removed": 1}

    # The plane never learned about the delete and finishes its file afterwards.
    folder = tmp_path / "data" / "enroll"
    wav = folder / f"{enroll_id}.wav"
    meta = folder / f"{enroll_id}.json"
    wav.write_bytes(b"RIFF")
    meta.write_text("{}", encoding="utf-8")
    stale = time.time() - 120
    os.utime(wav, (stale, stale))
    os.utime(meta, (stale, stale))

    assert client.get("/api/v2/speakers", headers=AUTH).status_code == 200
    assert not wav.exists()
    assert not meta.exists()


# --------------------------------------------------------------------------- plane hook
#
# Card B also arms the recorder inside the audio plane. These cases drive
# `AudioPlane._begin_enroll/_feed_enroll/_finish_enroll` directly (no socket, no
# adapter): what matters is the file the control plane will read.

def _plane(tmp_path: Path):
    from rui.audio.process import AudioPlane
    from rui.config import Config

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.aec.enabled = False
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


def _block(amplitude: int = 8000) -> bytes:
    """One 20 ms post-AEC voice block at 16 kHz mono int16."""
    samples = RATE // 50
    if amplitude == 0:
        return b"\x00\x00" * samples
    t = np.arange(samples, dtype=np.float32) / RATE
    tone = (amplitude * np.sin(2 * np.pi * 440 * t)).astype("<i2")
    return tone.tobytes()


def test_plane_enroll_writes_wav_and_sidecar_when_the_window_is_full(tmp_path: Path) -> None:
    from rui.audio.protocol import CONTROL, EnrollStart

    plane = _plane(tmp_path)
    target = tmp_path / "data" / "enroll" / "e1.wav"
    # The dispatch path: the union must parse an EnrollStart into that class.
    parsed = CONTROL.validate_json(
        EnrollStart(enroll_id="e1", device_id="living", seconds=1, path=str(target))
        .model_dump_json())
    assert isinstance(parsed, EnrollStart)

    plane._begin_enroll(parsed)
    assert plane._enroll is not None
    block = _block()
    for _ in range(50):  # 50 x 20 ms = the whole 1 s window
        plane._feed_enroll(block)
    assert plane._enroll is None

    with wave.open(str(target)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == RATE
        assert handle.getnframes() == RATE

    sidecar = target.with_suffix(".json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["enroll_id"] == "e1"
    assert payload["device_id"] == "living"
    assert payload["path"] == str(target)
    assert payload["ms"] == 1000.0
    assert payload["speech_ms"] == 1000.0
    assert target.stat().st_mode & 0o777 == 0o600
    # No .tmp/.part survives the atomic replace.
    assert sorted(p.name for p in target.parent.iterdir()) == ["e1.json", "e1.wav"]


def test_plane_enroll_does_not_write_before_the_window(tmp_path: Path) -> None:
    from rui.audio.protocol import EnrollStart

    plane = _plane(tmp_path)
    target = tmp_path / "data" / "enroll" / "e2.wav"
    plane._begin_enroll(EnrollStart(enroll_id="e2", device_id="living",
                                    seconds=1, path=str(target)))
    for _ in range(49):  # 980 ms
        plane._feed_enroll(_block())
    assert plane._enroll is not None
    assert not target.exists()
    assert not target.with_suffix(".json").exists()
    plane._feed_enroll(_block())
    assert target.is_file()
    assert target.with_suffix(".json").is_file()


def test_plane_enroll_speech_ms_ignores_silence(tmp_path: Path) -> None:
    from rui.audio.protocol import EnrollStart

    plane = _plane(tmp_path)
    target = tmp_path / "data" / "enroll" / "e3.wav"
    plane._begin_enroll(EnrollStart(enroll_id="e3", device_id="living",
                                    seconds=1, path=str(target)))
    for _ in range(25):
        plane._feed_enroll(_block(0))
    for _ in range(25):
        plane._feed_enroll(_block())
    payload = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["ms"] == 1000.0
    assert payload["speech_ms"] == 500.0


def test_plane_enroll_drop_removes_partial_files(tmp_path: Path) -> None:
    from rui.audio.protocol import EnrollStart

    plane = _plane(tmp_path)
    target = tmp_path / "data" / "enroll" / "e4.wav"
    plane._begin_enroll(EnrollStart(enroll_id="e4", device_id="living",
                                    seconds=5, path=str(target)))
    plane._feed_enroll(_block())
    plane._feed_enroll(_block())
    plane._finish_enroll(reason="cancelled")
    assert plane._enroll is None
    assert not target.exists()
    assert not target.with_suffix(".json").exists()


def test_plane_enroll_write_failure_never_raises(tmp_path: Path) -> None:
    from rui.audio.protocol import EnrollStart

    plane = _plane(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    target = blocked / "e5.wav"  # mkdir(parents=True) raises NotADirectoryError
    plane._begin_enroll(EnrollStart(enroll_id="e5", device_id="living",
                                    seconds=1, path=str(target)))
    plane._feed_enroll(_block())
    for _ in range(50):
        plane._feed_enroll(_block())  # must not raise
    assert plane._enroll is None
