from __future__ import annotations

from pathlib import Path

from house_ear.config import Config
from house_ear.config.recovery import (
    last_good_path,
    load_with_recovery,
    read_marker,
    remember_last_good,
    restore,
)
from house_ear.config.store import save_config


def _last_good(tmp_path: Path) -> tuple[Path, Config]:
    cfg = Config(system={"data_dir": str(tmp_path / "data")})
    path = tmp_path / "config.yaml"
    save_config(last_good_path(path), cfg)
    return path, cfg


def test_last_good_is_used_when_config_is_invalid(tmp_path: Path) -> None:
    path, cfg = _last_good(tmp_path)
    path.write_text("audio: [this is not valid", encoding="utf-8")
    loaded, info = load_with_recovery(path)
    assert info is not None and info.mode == "last-good"
    assert info.bad_path and Path(info.bad_path).is_file()
    assert loaded.system.data_dir == cfg.system.data_dir
    marker = read_marker(loaded)
    assert marker is not None and marker.mode == "last-good"
    # The broken file is never overwritten.
    assert "not valid" in path.read_text()


def test_remember_last_good_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    save_config(path, Config(system={"data_dir": str(tmp_path / "data")}))
    target = remember_last_good(path)
    assert target is not None and target.is_file()
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_restore_last_good_replaces_the_broken_file(tmp_path: Path) -> None:
    path, cfg = _last_good(tmp_path)
    path.write_text("audio: [bad", encoding="utf-8")
    loaded, info = load_with_recovery(path)
    assert info is not None
    restore(path, loaded, "last-good")
    assert "audio" in path.read_text()
    assert read_marker(loaded) is None


def test_recovery_api_and_banner_data(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from house_ear.web.api.app import create_app

    path, cfg = _last_good(tmp_path)
    path.write_text("audio: [bad", encoding="utf-8")
    loaded, info = load_with_recovery(path)
    assert info is not None
    with TestClient(create_app(loaded, config_path=path, recovery=info)) as c:
        headers = {"X-Auth-Token": "tok"}
        app = c.app
        app.state.cfg.web.auth_token = "tok"
        body = c.get("/api/v1/config/recovery", headers=headers).json()
        assert body["active"] is True and body["mode"] == "last-good"
        restore = c.post("/api/v1/config/recovery/restore", headers=headers,
                         json={"mode": "last-good"})
        assert restore.status_code == 200
        assert restore.json()["restart_required"] is True
    assert "audio" in path.read_text()


def test_successful_startup_snapshots_last_good(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from house_ear.web.api.app import create_app

    path = tmp_path / "config.yaml"
    cfg = Config(system={"data_dir": str(tmp_path / "data")})
    save_config(path, cfg)
    with TestClient(create_app(cfg, config_path=path)):
        assert last_good_path(path).is_file()
