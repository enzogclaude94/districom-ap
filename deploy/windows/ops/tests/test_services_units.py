"""W2-C2 units that run anywhere: pinned runtimes, backup disk identity, service definitions and settings.

The real services (C2-1 to C2-7) are exercised on Windows by ``services_tests/`` in the job «Windows services».
"""

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from districom_ops import artifacts, backup_disk, services
from districom_ops.artifacts import Artifact, ArtifactError
from districom_ops.config import ConfigError, load_config, load_services_config
from districom_ops.install import Install
from districom_ops.winservice import service_sid
from tests.test_layout_and_config import APP_PASSWORD, valid

SECRET_KEY = "k9Qv2Lr8Zt5Wx1Np7Hd3Bf6Mj4Sc0GyEuTa"
DISK_ID = "6f1c2a7e-3b4d-4e5f-9a8b-1c2d3e4f5a6b"


def services_values(root: Path) -> dict[str, str]:
    return {
        **valid(root),
        "SECRET_KEY": SECRET_KEY,
        "BACKUP_DISK_ROOT": str(root.parent / "backupdisk"),
        "BACKUP_DISK_ID": DISK_ID,
    }


def make_install(root: Path, release: str | None = "r1") -> Install:
    values = services_values(root)
    ops = load_config(values)
    return Install(ops, load_services_config(values, ops), release)


# --- pinned runtimes ---


def _artifact(tmp_path: Path, content: bytes, kind: str = "file", name: str = "tool") -> tuple[Artifact, Path]:
    archive = tmp_path / "downloads" / f"{name}.bin"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(content)
    target = "tool.exe" if kind == "file" else "folder"
    digest = hashlib.sha256(content).hexdigest()
    return Artifact(name, "1.0", f"https://example.invalid/{name}", "sha256", digest, kind, target), archive  # type: ignore[arg-type]


def test_the_manifest_pins_every_runtime_with_a_full_checksum() -> None:
    manifest = artifacts.manifest()
    assert set(manifest) == {"winsw", "postgresql", "caddy", "node"}
    for artifact in manifest.values():
        assert artifact.url.startswith("https://")
        assert len(artifact.digest) == {"sha256": 64, "sha512": 128}[artifact.algorithm]
        assert artifact.digest == artifact.digest.lower()


def test_a_verified_runtime_is_installed_once(tmp_path: Path) -> None:
    artifact, archive = _artifact(tmp_path, b"wrapper")
    runtime = tmp_path / "runtime"
    assert artifacts.install(artifact, archive, runtime)
    assert artifact.path(runtime).read_bytes() == b"wrapper"
    assert artifacts.verified(artifact, runtime)
    assert not artifacts.install(artifact, archive, runtime)  # nothing to do the second time


def test_a_checksum_mismatch_deletes_the_download_and_extracts_nothing(tmp_path: Path) -> None:
    artifact, archive = _artifact(tmp_path, b"tampered")
    pinned = Artifact(**{**artifact.__dict__, "digest": hashlib.sha256(b"original").hexdigest()})
    runtime = tmp_path / "runtime"
    with pytest.raises(ArtifactError, match="mismatch"):
        artifacts.install(pinned, archive, runtime)
    assert not archive.exists()
    assert not pinned.home(runtime).exists()
    assert not artifacts.verified(pinned, runtime)


def test_a_stamp_for_another_version_or_hash_is_not_trusted(tmp_path: Path) -> None:
    artifact, archive = _artifact(tmp_path, b"wrapper")
    runtime = tmp_path / "runtime"
    artifacts.install(artifact, archive, runtime)
    stamp = artifact.home(runtime) / artifacts.STAMP
    stamp.write_text(json.dumps({**json.loads(stamp.read_text()), "digest": "0" * 64}))
    assert not artifacts.verified(artifact, runtime)


def test_a_zip_with_an_escaping_path_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as handle:
        handle.writestr("folder/ok.txt", "ok")
        handle.writestr("../outside.txt", "no")
    artifact, archive = _artifact(tmp_path, bundle.read_bytes(), kind="zip")
    with pytest.raises(ArtifactError, match="unsafe"):
        artifacts.install(artifact, archive, tmp_path / "runtime")
    assert not (tmp_path / "outside.txt").exists()


def test_only_https_downloads(tmp_path: Path) -> None:
    artifact, _ = _artifact(tmp_path, b"x")
    plain = Artifact(**{**artifact.__dict__, "url": "http://example.invalid/tool"})
    with pytest.raises(ArtifactError, match="HTTPS"):
        artifacts.fetch(plain, tmp_path / "downloads-empty", tmp_path / "runtime")


# --- backup disk identity ---


@pytest.fixture
def volumes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Fake volumes: each top folder under tmp_path is its own volume; the system volume is 'system'."""
    table = {"disk": "vol-disk", "other": "vol-other", "system": "vol-system", "data": "vol-data"}

    def volume_id(path: Path) -> str:
        relative = Path(path).resolve().relative_to(tmp_path.resolve())
        return table[relative.parts[0]]

    monkeypatch.setattr(backup_disk, "volume_id", volume_id)
    monkeypatch.setattr(backup_disk, "system_root", lambda: tmp_path / "system")
    for name in table:
        (tmp_path / name).mkdir()
    return table


def test_a_prepared_disk_verifies(tmp_path: Path, volumes: dict[str, str]) -> None:
    disk_id = backup_disk.prepare(tmp_path / "disk", tmp_path / "data" / "pgdata")
    assert backup_disk.DISK_ID.match(disk_id)
    check = backup_disk.verify(tmp_path / "disk", disk_id, tmp_path / "data" / "pgdata")
    assert check.ok, check.describe()
    with pytest.raises(backup_disk.BackupDiskError, match="already"):
        backup_disk.prepare(tmp_path / "disk", tmp_path / "data")


@pytest.mark.parametrize(("root", "message"), [("system", "system volume"), ("data", "same volume")])
def test_prepare_refuses_the_system_or_data_volume(
    tmp_path: Path, volumes: dict[str, str], root: str, message: str
) -> None:
    with pytest.raises(backup_disk.BackupDiskError, match=message):
        backup_disk.prepare(tmp_path / root, tmp_path / "data")


def test_without_the_marker_postgresql_may_not_start(tmp_path: Path, volumes: dict[str, str]) -> None:
    check = backup_disk.verify(tmp_path / "disk", DISK_ID, tmp_path / "data")
    assert (check.ok, check.code) == (False, "missing")
    assert check.describe().startswith("FAIL")


def test_the_marker_of_another_disk_is_refused(tmp_path: Path, volumes: dict[str, str]) -> None:
    backup_disk.prepare(tmp_path / "disk", tmp_path / "data")
    check = backup_disk.verify(tmp_path / "disk", DISK_ID, tmp_path / "data")
    assert check.code == "identity_mismatch"


def test_a_copied_marker_on_another_volume_is_refused(tmp_path: Path, volumes: dict[str, str]) -> None:
    disk_id = backup_disk.prepare(tmp_path / "disk", tmp_path / "data")
    (tmp_path / "other" / backup_disk.MARKER).write_bytes((tmp_path / "disk" / backup_disk.MARKER).read_bytes())
    check = backup_disk.verify(tmp_path / "other", disk_id, tmp_path / "data")
    assert check.code == "volume_mismatch"


@pytest.mark.parametrize("content", [b"{ torn", b"[]", b'{"disk_id": 1}', b"\xff\xfe"])
def test_an_unreadable_marker_is_refused(tmp_path: Path, volumes: dict[str, str], content: bytes) -> None:
    (tmp_path / "disk" / backup_disk.MARKER).write_bytes(content)
    assert backup_disk.verify(tmp_path / "disk", DISK_ID, tmp_path / "data").code == "unreadable"


# --- service definitions ---


def test_service_sid_matches_windows() -> None:
    # The SID Windows itself gives to the TrustedInstaller service account.
    assert service_sid("TrustedInstaller") == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    assert service_sid("trustedinstaller") == service_sid("TrustedInstaller")


def test_definitions_keep_the_order_restart_limits_and_no_secrets(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    definitions = services.specs(install)
    assert set(definitions) == set(services.WINSW)
    documents = {name: services.render(spec, install.service_logs) for name, spec in definitions.items()}
    for name, document in documents.items():
        text = document.decode("utf-8")
        assert SECRET_KEY not in text
        assert APP_PASSWORD not in text
        root = ET.fromstring(document)  # noqa: S314 (our own output)
        assert root.findtext("id") == name
        actions = [e.get("action") for e in root.findall("onfailure")]
        assert actions[-1] == "none"  # restarts are limited
        assert actions.count("restart") == 3
        assert root.findtext("stopparentprocessfirst") == "true"
    depends = {name: [e.text for e in ET.fromstring(d).findall("depend")] for name, d in documents.items()}  # noqa: S314
    assert depends == {
        services.BACKEND: [],  # PostgreSQL is deliberately not an SCM dependency: only the stack starts it
        services.FRONTEND: [services.BACKEND],
        services.CADDY: [services.FRONTEND],
        services.STACK: [],
    }
    modes = {name: ET.fromstring(d).findtext("startmode") for name, d in documents.items()}  # noqa: S314
    assert modes == {
        services.BACKEND: "Manual",
        services.FRONTEND: "Manual",
        services.CADDY: "Manual",
        services.STACK: "Automatic",
    }
    assert ET.fromstring(documents[services.STACK]).findtext("delayedAutoStart") == "true"  # noqa: S314


def test_rendering_is_deterministic(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    first = {n: services.render(s, install.service_logs) for n, s in services.specs(install).items()}
    second = {n: services.render(s, install.service_logs) for n, s in services.specs(install).items()}
    assert first == second


def test_the_backend_env_carries_production_settings(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    lines = dict(line.split("=", 1) for line in services.backend_env(install).splitlines()[1:])
    assert lines["APP_ENV"] == "'production'"
    assert lines["COOKIE_SECURE"] == "'true'"
    assert lines["ARGON2_PROFILE"] == "'standard'"
    assert lines["SECRET_KEY"] == f"'{SECRET_KEY}'"
    assert lines["DATABASE_URL"].startswith("'postgresql+psycopg://districom_app:")


def test_the_postgresql_service_runs_pg_ctl_on_the_configured_data(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    path = services.pg_binary_path(install)
    assert "runservice" in path
    assert f'-N "{services.PG}"' in path
    assert str(install.pg_data) in path


# --- settings ---


def test_services_settings_are_validated_without_values(tmp_path: Path) -> None:
    root = tmp_path / "DistriCom"
    base = services_values(root)
    ops = load_config(base)
    bad = {
        **base,
        "SECRET_KEY": "short",
        "BACKUP_DISK_ROOT": "relative\\disk",
        "BACKUP_DISK_ID": "not-an-id",
        "BACKUP_DISK_WAIT_SECONDS": "9999",
    }
    with pytest.raises(ConfigError) as caught:
        load_services_config(bad, ops)
    found = {p.variable for p in caught.value.problems}
    assert found == {"SECRET_KEY", "BACKUP_DISK_ROOT", "BACKUP_DISK_ID", "BACKUP_DISK_WAIT_SECONDS"}
    assert "short" not in str(caught.value)
    with pytest.raises(ConfigError) as caught:
        load_services_config({**base, "PG_DATA_DIR": str(Path(base["BACKUP_DISK_ROOT"]) / "pgdata")}, ops)
    assert {p.variable for p in caught.value.problems} == {"PG_DATA_DIR"}
    with pytest.raises(ConfigError) as caught:
        load_services_config({**base, "SECRET_KEY": SECRET_KEY + "'"}, ops)
    assert {p.variable for p in caught.value.problems} == {"SECRET_KEY"}
    settings = load_services_config(base, ops)
    assert settings.pg_data_dir == root / "pgdata"
    assert settings.backup_disk_wait_seconds == 300
    assert SECRET_KEY not in repr(settings)
    assert (settings.http_port, settings.https_port) == (80, 443)


@pytest.mark.parametrize(
    ("values", "variable"),
    [
        ({"HTTP_PORT": "0"}, "HTTP_PORT"),
        ({"HTTPS_PORT": "70000"}, "HTTPS_PORT"),
        ({"HTTP_PORT": "eighty"}, "HTTP_PORT"),
        ({"HTTP_PORT": "8443", "HTTPS_PORT": "8443"}, "HTTPS_PORT"),
    ],
)
def test_caddy_ports_are_validated(tmp_path: Path, values: dict[str, str], variable: str) -> None:
    base = services_values(tmp_path / "DistriCom")
    with pytest.raises(ConfigError) as caught:
        load_services_config({**base, **values}, load_config(base))
    assert {p.variable for p in caught.value.problems} == {variable}


def test_other_caddy_ports_reach_the_service_environment(tmp_path: Path) -> None:
    values = {**services_values(tmp_path / "DistriCom"), "HTTP_PORT": "8080", "HTTPS_PORT": "8443"}
    ops = load_config(values)
    install = Install(ops, load_services_config(values, ops), "r1")
    env = services.specs(install)[services.CADDY].env
    assert (env["CADDY_HTTP_PORT"], env["CADDY_HTTPS_PORT"]) == ("8080", "8443")
