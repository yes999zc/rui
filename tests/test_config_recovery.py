from __future__ import annotations

from pathlib import Path

import pytest

from house_ear.config import Config, ConfigError, load_config
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


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_truncated_config_falls_back_to_last_good(tmp_path: Path, content: str) -> None:
    """A 0-byte/whitespace config must go through recovery, not schema defaults."""
    path, cfg = _last_good(tmp_path)
    path.write_text(content, encoding="utf-8")

    loaded, info = load_with_recovery(path)

    assert info is not None and info.mode == "last-good"
    assert info.last_good_path == str(last_good_path(path))
    failed = list(tmp_path.glob("config.yaml.failed-*"))
    assert len(failed) == 1
    assert info.bad_path == str(failed[0])
    assert failed[0].read_text(encoding="utf-8") == content
    assert loaded.system.data_dir == cfg.system.data_dir
    marker = read_marker(loaded)
    assert marker is not None and marker.mode == "last-good"
    # Recovery never rewrites the truncated file.
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_truncated_config_without_last_good_uses_defaults(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")

    loaded, info = load_with_recovery(path)

    assert info is not None and info.mode == "defaults"
    assert info.last_good_path == ""
    assert info.bad_path and Path(info.bad_path).is_file()
    assert loaded == Config()


def test_broken_yaml_without_last_good_still_uses_defaults(tmp_path: Path) -> None:
    """Broken YAML keeps the old recovery path (no last-good -> schema defaults)."""
    path = tmp_path / "config.yaml"
    path.write_text("audio: [this is not valid", encoding="utf-8")

    loaded, info = load_with_recovery(path)

    assert info is not None and info.mode == "defaults"
    assert info.bad_path and Path(info.bad_path).is_file()
    assert loaded == Config()
    assert path.read_text(encoding="utf-8") == "audio: [this is not valid"


def test_non_empty_valid_config_is_unaffected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    cfg = Config(system={"data_dir": str(tmp_path / "data")}, web={"port": 9091})
    save_config(path, cfg)

    loaded, info = load_with_recovery(path)

    assert info is None
    assert loaded.web.port == 9091
    assert not list(tmp_path.glob("config.yaml.failed-*"))


def test_missing_config_still_uses_schema_defaults(tmp_path: Path) -> None:
    """`load_with_recovery` keeps returning Config() when the file does not exist."""
    loaded, info = load_with_recovery(tmp_path / "config.yaml")

    assert info is None
    assert loaded == Config()


@pytest.mark.parametrize("content", ["", "  \n\t\n", "null\n", "~\n", "---\n",
                                     "# only a comment\n"])
def test_empty_or_yaml_null_config_raises_config_error(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="empty|no YAML document"):
        load_config(path)
