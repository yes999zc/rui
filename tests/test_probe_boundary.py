"""P1-4: a raising/hanging/malformed probe() degrades a device, never a 500."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from rui.audio.probe import (
    PROBE_FAILED,
    PROBE_INVALID,
    PROBE_TIMEOUT,
    safe_probe,
)
from rui.config import Config, DeviceConfig, save_config
from rui.device.contract import Capability, DeviceInfo, Transport
from rui.web.api.app import create_app


class HealthOnlyAdapter:
    """The minimum adapter surface the device endpoints touch."""

    def __init__(self, device_id: str) -> None:
        self.info = DeviceInfo(
            id=device_id,
            adapter="mock",
            model="mock",
            host="127.0.0.1",
            capabilities=frozenset({Capability.PLAYBACK}),
            transport=Transport.OUTBOUND,
        )

    async def probe(self) -> DeviceInfo:
        return self.info

    def health(self) -> dict:
        return {"connected": False}


class RaisingProbe(HealthOnlyAdapter):
    async def probe(self) -> DeviceInfo:
        raise RuntimeError("probe boom")


def make_config(tmp_path: Path) -> tuple[Config, Path]:
    cfg = Config(
        devices=[
            DeviceConfig(id="good", adapter="mock", host="127.0.0.1"),
            DeviceConfig(id="bad", adapter="mock", host="127.0.0.2"),
        ]
    )
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return cfg, path


def test_the_devices_api_survives_a_raising_probe(tmp_path: Path) -> None:
    cfg, path = make_config(tmp_path)
    client = TestClient(create_app(cfg, config_path=path))
    client.app.state.adapters = SimpleNamespace(
        adapters={
            "good": HealthOnlyAdapter("good"),
            "bad": RaisingProbe("bad"),
        },
        errors={},
    )

    listing = client.get("/api/v1/devices")
    assert listing.status_code == 200, listing.text
    rows = {row["id"]: row for row in listing.json()}
    assert rows["good"]["status"] != "error"
    assert rows["bad"]["status"] == "error"
    assert rows["bad"]["error"] == PROBE_FAILED

    detail = client.get("/api/v1/devices/bad")
    assert detail.status_code == 200, detail.text
    assert detail.json()["live"]["status"] == "error"
    assert detail.json()["live"]["error"] == PROBE_FAILED

    status = client.get("/api/v1/devices/bad/status")
    assert status.status_code == 200, status.text
    assert PROBE_FAILED in status.json()["errors"]

    assert client.get("/health").status_code == 200


def test_safe_probe_reports_raising_and_hanging_probes() -> None:
    class Hanging(HealthOnlyAdapter):
        async def probe(self) -> DeviceInfo:
            await asyncio.sleep(1.0)
            return self.info

    class NoneProbe(HealthOnlyAdapter):
        async def probe(self):  # type: ignore[override]
            return None

    async def run() -> tuple[object, object, object]:
        failing = await safe_probe(RaisingProbe("bad"), device_id="bad")
        hanging = await safe_probe(Hanging("slow"), timeout_s=0.01, device_id="slow")
        none = await safe_probe(NoneProbe("none"), device_id="none")
        return failing, hanging, none

    failing, hanging, none = asyncio.run(run())

    assert failing.error == PROBE_FAILED  # type: ignore[attr-defined]
    assert failing.info.id == "bad"  # type: ignore[attr-defined]
    assert failing.info.capabilities == frozenset({Capability.PLAYBACK})  # type: ignore[attr-defined]
    assert hanging.error == PROBE_TIMEOUT  # type: ignore[attr-defined]
    assert hanging.info.id == "slow"  # type: ignore[attr-defined]
    assert none.error == PROBE_INVALID  # type: ignore[attr-defined]
    assert none.info.id == "none"  # type: ignore[attr-defined]
