"""W2-C3 permissions policy and integrity record, against a fake NTFS that models inheritance (any platform).

The real lists are read and written on the Windows runner (``permissions_tests/``); here the rules themselves:
what each account gets, idempotence, the reset of stray entries, foreign links and what ``verify`` reports.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from districom_ops import integrity, ntfs, permissions
from districom_ops.ntfs import CONTAINER_INHERIT, FULL, INHERITED, MODIFY, OBJECT_INHERIT, READ, Ace, Dacl
from districom_ops.permissions import ADMINISTRATORS, SYSTEM, PermissionsError, Policy
from districom_ops.winservice import service_sid
from tests.test_services_units import make_install

NAMES = {
    "backend": "DistriComBackend",
    "frontend": "DistriComFrontend",
    "caddy": "DistriComCaddy",
    "postgresql": "DistriComPostgreSQL",
    "stack": "DistriComStack",
}
WINSW = ["DistriComBackend", "DistriComFrontend", "DistriComCaddy", "DistriComStack"]
SID = {role: service_sid(name) for role, name in NAMES.items()}
USERS = "S-1-5-32-545"


@dataclass
class FakeNtfs:
    """Explicit entries per path; inherited ones are computed from the parents, as Windows propagates them."""

    explicit: dict[Path, tuple[bool, tuple[Ace, ...]]] = field(default_factory=dict)
    writes: int = 0
    stop_at: Path | None = None  # above this, nothing is managed (stands for C:\\)

    def read(self, path: Path) -> Dacl:
        protected, own = self.explicit.get(path, (False, ()))
        inherited: tuple[Ace, ...] = ()
        if not protected and path.parent != path and path != self.stop_at:
            parent = self.read(path.parent)
            is_dir = path.is_dir()
            inherited = tuple(
                Ace(a.sid, a.mask, ((a.flags & (OBJECT_INHERIT | CONTAINER_INHERIT)) if is_dir else 0) | INHERITED)
                for a in parent.aces
                if (a.flags & CONTAINER_INHERIT if is_dir else a.flags & OBJECT_INHERIT)
            )
        return Dacl(protected, tuple(own) + inherited)

    def write(self, path: Path, protected: bool, aces: tuple[Ace, ...]) -> None:
        self.writes += 1
        self.explicit[path] = (protected, aces)

    is_link = staticmethod(ntfs.is_link)
    link_target = staticmethod(ntfs.link_target)


def build(tmp_path: Path) -> tuple[Policy, FakeNtfs, Path]:
    root = tmp_path / "DistriCom"
    release = root / "releases" / "r1"
    for folder in (
        "runtime/python/Lib",
        "runtime/postgresql-17.6/pgsql/bin",
        "runtime/caddy-2.10.2",
        "runtime/node-22/bin",
        "services",
        "releases/r1/backend/app",
        "releases/r1/frontend/.next",
        "releases/r1/frontend/node_modules/.pnpm/next@1/node_modules/next",
        "releases/r1/windows/ops",
        "config/backend",
        "pgdata/base",
        "data/uploads",
        "logs/postgresql",
        "state/caddy",
        "state/frontend-cache",
        "downloads",
    ):
        (root / folder).mkdir(parents=True)
    for name in WINSW:
        (root / "logs" / "services" / name).mkdir(parents=True)
        (root / "services" / f"{name}.exe").write_bytes(b"MZ")
        (root / "services" / f"{name}.xml").write_text("<service/>")
    for file in ("config/districom.env", "config/backend/.env", "config/Caddyfile", "config/postgres.secret",
                 "releases/r1/backend/app/main.py", "releases/r1/frontend/server.js", "pgdata/PG_VERSION",
                 "runtime/caddy-2.10.2/caddy.exe", "logs/ops.log"):  # fmt: skip
        (root / file).write_text("x")
    os.symlink(
        release / "frontend" / "node_modules" / ".pnpm" / "next@1" / "node_modules" / "next",
        release / "frontend" / "node_modules" / "next",
        target_is_directory=True,
    )
    os.symlink(root / "state" / "frontend-cache", release / "frontend" / ".next" / "cache", target_is_directory=True)
    runtimes = {
        "python": (root / "runtime" / "python", [NAMES["backend"], NAMES["stack"]]),
        "postgresql": (root / "runtime" / "postgresql-17.6", [NAMES["postgresql"], NAMES["stack"]]),
        "caddy": (root / "runtime" / "caddy-2.10.2", [NAMES["caddy"]]),
        "node": (root / "runtime" / "node-22", [NAMES["frontend"]]),
    }
    marker = tmp_path / "R" / ".districom-backup-disk"
    marker.parent.mkdir()
    marker.write_text("{}")
    pol = permissions.policy(root, release, root / "pgdata", runtimes, WINSW, NAMES, backup_marker=marker)
    fake = FakeNtfs(stop_at=root)
    # What C:\\ gives on a standard Windows: every signed-in user may modify (inherited by a new folder).
    fake.explicit[root] = (False, (Ace("S-1-5-11", MODIFY, OBJECT_INHERIT | CONTAINER_INHERIT),))
    return pol, fake, root


def who(fake: FakeNtfs, path: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for ace in fake.read(path).aces:
        found[ace.sid] = found.get(ace.sid, 0) | ace.mask
    return found


def test_apply_makes_the_policy_and_a_second_run_changes_nothing(tmp_path: Path) -> None:
    pol, fake, _ = build(tmp_path)
    assert permissions.verify(pol, fake), "the standard inheritance from C:\\ must be reported"
    changes = permissions.apply(pol, fake)
    assert changes
    assert permissions.verify(pol, fake) == []
    before = permissions.snapshot(pol, fake)
    writes = fake.writes
    assert permissions.apply(pol, fake) == []
    assert fake.writes == writes, "the second run wrote something"
    assert permissions.snapshot(pol, fake) == before


def test_each_account_gets_only_what_it_needs(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    permissions.apply(pol, fake)
    base = {ADMINISTRATORS: FULL, SYSTEM: FULL}
    assert who(fake, root / "config" / "districom.env") == {**base, SID["stack"]: READ}
    assert who(fake, root / "config" / "backend" / ".env") == {**base, SID["backend"]: ntfs.READ_EXECUTE}
    assert who(fake, root / "config" / "Caddyfile") == {**base, SID["caddy"]: READ}
    assert who(fake, root / "config" / "postgres.secret") == base
    assert who(fake, root / "pgdata" / "PG_VERSION") == {**base, SID["postgresql"]: FULL}
    main = who(fake, root / "releases" / "r1" / "backend" / "app" / "main.py")
    assert main == {**base, SID["backend"]: ntfs.READ_EXECUTE}  # read only: C3-3
    server = who(fake, root / "releases" / "r1" / "frontend" / "server.js")
    assert server == {**base, SID["frontend"]: ntfs.READ_EXECUTE}
    assert who(fake, root / "runtime" / "caddy-2.10.2" / "caddy.exe") == {**base, SID["caddy"]: ntfs.READ_EXECUTE}
    assert who(fake, root / "services" / "DistriComBackend.exe") == {**base, SID["backend"]: ntfs.READ_EXECUTE}
    assert who(fake, root / "data" / "uploads") == {**base, SID["backend"]: MODIFY}
    assert who(fake, root / "logs" / "ops.log") == {**base, SID["stack"]: MODIFY}
    assert who(fake, root / "logs" / "services" / "DistriComCaddy") == {**base, SID["caddy"]: MODIFY}
    assert who(fake, root / "state" / "caddy") == {**base, SID["caddy"]: MODIFY}  # not the stack: Caddy's keys
    assert who(fake, root / "state" / "frontend-cache") == {**base, SID["frontend"]: MODIFY}
    assert who(fake, root / "downloads") == base
    assert who(fake, tmp_path / "R" / ".districom-backup-disk") == {**base, SID["stack"]: READ}
    for _, granted in pol.rules[0].grants:
        assert granted & ntfs.WRITE_BITS == 0


def test_services_may_traverse_the_folders_above_their_grants_and_nothing_more(tmp_path: Path) -> None:
    """Node.js reads the attributes of every folder of a path and the stack checks the volume of the PostgreSQL
    data: without traverse they stop (EPERM on ``releases``). Traverse is the folder's own, never inherited."""
    pol, fake, root = build(tmp_path)
    permissions.apply(pol, fake)
    base = {ADMINISTRATORS: FULL, SYSTEM: FULL}
    releases = who(fake, root / "releases")
    assert releases == {**base, **{SID[r]: ntfs.TRAVERSE for r in ("backend", "frontend", "stack")}}
    assert who(fake, root)[SID["frontend"]] == ntfs.TRAVERSE
    assert who(fake, root / "pgdata") == {**base, SID["postgresql"]: FULL, SID["stack"]: ntfs.TRAVERSE}
    assert who(fake, root / "pgdata" / "PG_VERSION") == {**base, SID["postgresql"]: FULL}
    assert who(fake, root / "config")[SID["caddy"]] == ntfs.TRAVERSE
    assert SID["caddy"] not in who(fake, root / "config" / "districom.env")
    assert who(fake, root / "downloads") == base
    assert ntfs.TRAVERSE & 0x1 == 0, "traverse never lists a folder"
    assert ntfs.TRAVERSE & ntfs.WRITE_BITS == 0
    fake.explicit[root / "downloads"] = (False, (Ace(SID["frontend"], ntfs.TRAVERSE),))
    assert [p.code for p in permissions.verify(pol, fake)] == ["excess_read"], "traverse only where the policy says"


def test_modify_never_lets_an_account_change_permissions_or_delete_children() -> None:
    assert MODIFY & 0x40000 == 0  # WRITE_DAC
    assert MODIFY & 0x80000 == 0  # WRITE_OWNER
    assert MODIFY & 0x40 == 0  # FILE_DELETE_CHILD


def test_stray_entries_are_reported_and_removed(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    permissions.apply(pol, fake)
    exe = root / "runtime" / "caddy-2.10.2" / "caddy.exe"
    main = root / "releases" / "r1" / "backend" / "app" / "main.py"
    fake.explicit[exe] = (False, (Ace(USERS, MODIFY),))
    fake.explicit[main] = (False, (Ace(SID["backend"], MODIFY),))
    fake.explicit[root / "logs" / "ops.log"] = (True, (Ace(SID["stack"], MODIFY),))
    codes = {(p.path, p.code) for p in permissions.verify(pol, fake)}
    assert (str(exe.relative_to(root)), "broad_access") in codes
    assert (str(exe.relative_to(root)), "own_entries") not in codes  # not protected: judged by what it gives
    assert (str(main.relative_to(root)), "excess_write") in codes
    assert ("logs/ops.log".replace("/", os.sep), "own_entries") in codes
    changes = permissions.apply(pol, fake)
    assert changes == ["3 objects now inherit from their folder"]
    assert permissions.verify(pol, fake) == []


def test_a_link_outside_its_release_stops_apply_before_anything_changes(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, root / "releases" / "r1" / "frontend" / "node_modules" / "evil", target_is_directory=True)
    writes = fake.writes
    with pytest.raises(PermissionsError) as caught:
        permissions.apply(pol, fake)
    assert [p.code for p in caught.value.problems] == ["foreign_link"]
    assert fake.writes == writes
    assert "foreign_link" in {p.code for p in permissions.verify(pol, fake)}


def test_a_link_inside_its_runtime_is_allowed(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    exact = root / "runtime" / "python" / "cpython-3.12.16"
    exact.mkdir()
    os.symlink(exact, root / "runtime" / "python" / "cpython-3.12", target_is_directory=True)  # what uv does
    permissions.apply(pol, fake)
    assert permissions.verify(pol, fake) == []
    os.symlink(root / "config", root / "runtime" / "python" / "escape", target_is_directory=True)
    with pytest.raises(PermissionsError):
        permissions.apply(pol, fake)


def test_the_cache_link_must_point_to_its_own_folder(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    cache = root / "releases" / "r1" / "frontend" / ".next" / "cache"
    cache.unlink()
    os.symlink(root / "config", cache, target_is_directory=True)
    with pytest.raises(PermissionsError):
        permissions.apply(pol, fake)


def test_a_missing_required_folder_is_reported(tmp_path: Path) -> None:
    pol, fake, root = build(tmp_path)
    (root / "data" / "uploads").rmdir()
    with pytest.raises(PermissionsError) as caught:
        permissions.apply(pol, fake)
    assert caught.value.problems[0].code == "missing"


def test_a_null_dacl_is_reported(tmp_path: Path) -> None:
    pol, fake, _ = build(tmp_path)
    permissions.apply(pol, fake)
    real_read = fake.read

    def read(path: Path) -> Dacl:
        return Dacl(False, (), null=True) if path.name == "server.js" else real_read(path)

    fake.read = read  # type: ignore[method-assign]
    assert "no_dacl" in {p.code for p in permissions.verify(pol, fake)}


# --- integrity --------------------------------------------------------------------------------------------------


def test_integrity_sees_a_changed_a_missing_and_an_added_file(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom", release="r1")
    release = install.release_dir()
    (release / "backend" / "app" / "__pycache__").mkdir(parents=True)
    (release / "backend" / "app" / "main.py").write_text("print(1)")
    (release / "backend" / "app" / "util.py").write_text("x = 1")
    install.runtime.mkdir(parents=True, exist_ok=True)
    (install.runtime / "tool.exe").write_bytes(b"MZ1")
    install.service_dir.mkdir(parents=True, exist_ok=True)
    install.integrity_record.parent.mkdir(parents=True, exist_ok=True)
    assert integrity.record(install) is True
    assert integrity.record(install) is False
    assert integrity.verify(install).ok
    (release / "backend" / "app" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"cache")  # skipped
    assert integrity.verify(install).ok
    (install.runtime / "tool.exe").write_bytes(b"MZ2")
    (release / "backend" / "app" / "util.py").unlink()
    (release / "backend" / "app" / "new.py").write_text("y = 2")
    report = integrity.verify(install)
    assert not report.ok
    assert report.changed == [str(Path("runtime") / "tool.exe")]
    assert report.missing == [str(Path("releases") / "r1" / "backend" / "app" / "util.py")]
    assert report.added == [str(Path("releases") / "r1" / "backend" / "app" / "new.py")]
