"""C1-1: folder layout and operations configuration."""

from pathlib import Path

import pytest

from districom_ops.cli import main
from districom_ops.config import ConfigError, OpsConfig, Secret, load_config, parse_env, read_config
from districom_ops.layout import FOLDERS, Layout, LayoutError

APP_PASSWORD = "Fict1cia-app-9f3k2LmQ"
OPS_PASSWORD = "Fict1cia-ops-7d1x0PzW"


def valid(root: Path) -> dict[str, str]:
    return {
        "DISTRICOM_ROOT": str(root),
        "DISTRICOM_HOST": "districom.example.internal",
        "DATABASE_URL": f"postgresql+psycopg://districom_app:{APP_PASSWORD}@127.0.0.1:5432/districom",
        "OPS_DATABASE_URL": f"postgresql://districom_ops:{OPS_PASSWORD}@127.0.0.1:5432/postgres",
        "LAN_SUBNET": "192.168.10.0/24",
    }


def problems(values: dict[str, str]) -> dict[str, str]:
    with pytest.raises(ConfigError) as caught:
        load_config(values)
    text = str(caught.value)
    assert APP_PASSWORD not in text
    assert OPS_PASSWORD not in text
    return {p.variable: p.reason for p in caught.value.problems}


@pytest.mark.criterion("C1-1")
def test_layout_creates_the_folders_and_running_it_again_changes_nothing(tmp_path: Path) -> None:
    layout = Layout(tmp_path / "DistriCom")
    assert sorted(p.name for p in layout.ensure()) == sorted(FOLDERS)
    assert layout.missing() == []
    marker = layout.state / "keep.txt"
    marker.write_text("kept", encoding="utf-8")
    assert layout.ensure() == []
    assert marker.read_text(encoding="utf-8") == "kept"
    assert not layout.current.exists()  # the version link belongs to C12


@pytest.mark.criterion("C1-1")
def test_layout_refuses_a_relative_root_and_a_file_in_place_of_a_folder(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        Layout(Path("DistriCom"))
    (tmp_path / "logs").write_text("not a folder", encoding="utf-8")
    with pytest.raises(LayoutError, match="logs"):
        Layout(tmp_path).ensure()


@pytest.mark.criterion("C1-1")
def test_a_valid_configuration_loads_with_secrets_hidden(tmp_path: Path) -> None:
    config = load_config(valid(tmp_path))
    assert config.maintenance_max_minutes == 60
    assert config.alert_command is None
    shown = repr(config) + str(config.database_url)
    assert APP_PASSWORD not in shown
    assert OPS_PASSWORD not in shown
    assert APP_PASSWORD in config.secrets()
    assert OPS_PASSWORD in config.secrets()


@pytest.mark.criterion("C1-1")
def test_every_missing_variable_is_reported_by_name() -> None:
    assert set(problems({})) == {"DISTRICOM_ROOT", "DISTRICOM_HOST", "DATABASE_URL", "OPS_DATABASE_URL", "LAN_SUBNET"}


@pytest.mark.criterion("C1-1")
@pytest.mark.parametrize(
    ("change", "variable"),
    [
        ({"DISTRICOM_ROOT": "DistriCom"}, "DISTRICOM_ROOT"),
        ({"DISTRICOM_HOST": "bad host!"}, "DISTRICOM_HOST"),
        ({"DATABASE_URL": f"postgresql://districom_app:{APP_PASSWORD}@10.0.0.5:5432/districom"}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgresql://districom_app:districom@127.0.0.1:5432/districom"}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgresql://districom_app:short@127.0.0.1:5432/districom"}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgresql://districom_app:<password-here-xxxxxxxx>@127.0.0.1/districom"}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgresql://districom_app@127.0.0.1:5432/districom"}, "DATABASE_URL"),
        ({"DATABASE_URL": f"mysql://districom_app:{APP_PASSWORD}@127.0.0.1/districom"}, "DATABASE_URL"),
        ({"OPS_DATABASE_URL": f"postgresql://districom_app:{OPS_PASSWORD}@127.0.0.1/postgres"}, "OPS_DATABASE_URL"),
        ({"LAN_SUBNET": "192.168.10.7/24"}, "LAN_SUBNET"),
        ({"LAN_SUBNET": "8.8.8.0/24"}, "LAN_SUBNET"),
        ({"LAN_SUBNET": "10.0.0.0/8"}, "LAN_SUBNET"),
        ({"MAINTENANCE_MAX_MINUTES": "0"}, "MAINTENANCE_MAX_MINUTES"),
        ({"MAINTENANCE_MAX_MINUTES": "sixty"}, "MAINTENANCE_MAX_MINUTES"),
    ],
)
def test_insecure_or_invalid_values_are_refused_without_showing_them(
    tmp_path: Path, change: dict[str, str], variable: str
) -> None:
    assert variable in problems(valid(tmp_path) | change)


@pytest.mark.criterion("C1-1")
def test_the_same_url_for_the_app_and_the_operations_is_refused(tmp_path: Path) -> None:
    values = valid(tmp_path)
    values["OPS_DATABASE_URL"] = values["DATABASE_URL"]
    assert "OPS_DATABASE_URL" in problems(values)


@pytest.mark.criterion("C1-1")
def test_env_file_parsing_is_literal_and_strict(tmp_path: Path) -> None:
    assert parse_env("# comment\n\nA_B='x y'\nC=\"$HOME\"\nD=%PATH%\n") == {"A_B": "x y", "C": "$HOME", "D": "%PATH%"}
    with pytest.raises(ConfigError, match="line 1"):
        parse_env("lowercase=1\n")
    with pytest.raises(ConfigError, match="defined twice"):
        parse_env("A=1\nA=2\n")
    path = tmp_path / "districom.env"
    path.write_text("﻿" + "\n".join(f"{k}={v}" for k, v in valid(tmp_path).items()), encoding="utf-8")
    assert isinstance(read_config(path), OpsConfig)  # a BOM written by Notepad is accepted


@pytest.mark.criterion("C1-1")
def test_check_config_reports_problems_without_values(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    values = valid(tmp_path) | {"DATABASE_URL": f"postgresql://districom_app:{APP_PASSWORD}@10.1.1.1/districom"}
    path = tmp_path / "districom.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    assert main(["--config", str(path), "check-config"]) == 1
    out = capfd.readouterr()
    assert "DATABASE_URL" in out.err
    assert APP_PASSWORD not in out.err + out.out
    assert OPS_PASSWORD not in out.err + out.out


@pytest.mark.criterion("C1-1")
def test_init_and_check_config_through_the_command_line(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "DistriCom"
    path = tmp_path / "districom.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in valid(root).items()), encoding="utf-8")
    assert main(["--config", str(path), "check-config"]) == 1  # folders missing
    assert main(["init", "--root", str(root)]) == 0
    assert main(["init", "--root", str(root)]) == 0
    assert main(["--config", str(path), "check-config"]) == 0
    out = capfd.readouterr()
    assert APP_PASSWORD not in out.err + out.out
    log = (root / "logs" / "ops.log").read_text(encoding="utf-8")
    assert "config_ok" in log
    assert APP_PASSWORD not in log
    assert OPS_PASSWORD not in log


def test_secret_never_shows_its_value() -> None:
    secret = Secret("visible-only-by-reveal")
    assert "visible" not in repr(secret) + str(secret) + f"{secret}"
    assert secret.reveal() == "visible-only-by-reveal"
