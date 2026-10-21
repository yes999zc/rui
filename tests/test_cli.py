from __future__ import annotations

import json

from house_ear.__main__ import main
from house_ear.config import load_config


def test_config_can_follow_the_subcommand(tmp_path, capsys) -> None:
    """This is how the container entrypoint calls it: `house-ear setup --config /c/x.yaml`."""
    path = tmp_path / "config.yaml"
    assert main(["setup", "--config", str(path)]) == 0
    assert path.exists()
    assert load_config(path).version == 2
    assert "wrote" in capsys.readouterr().out


def test_config_can_precede_the_subcommand(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    assert main(["--config", str(path), "setup"]) == 0
    assert path.exists()


def test_setup_refuses_to_overwrite_without_force(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    assert main(["setup", "--config", str(path)]) == 0
    assert main(["setup", "--config", str(path)]) == 1


def test_doctor_json_is_machine_readable(tmp_path, capsys) -> None:
    path = tmp_path / "config.yaml"
    main(["setup", "--config", str(path)])
    capsys.readouterr()
    assert main(["doctor", "--config", str(path), "--json"]) in {0, 2}
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] in {"ok", "degraded", "unsupported"}
    assert "capacity_basis" in body
