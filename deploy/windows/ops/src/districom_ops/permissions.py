"""Definitive NTFS permissions of an installation (W2-C3, ``docs/WINDOWS_DEPLOYMENT.md §8.5``).

The installation root does not inherit from ``C:\\`` (whose default lets every signed-in user write): it is
protected, with Administrators and SYSTEM only. Each service account then gets exactly what it reads or writes:

| Where | Who |
|---|---|
| ``runtime\\<runtime>`` | read and execute: only the services that run it |
| ``services\\<service>.exe/.xml`` | read and execute: that service |
| ``releases\\<release>\\backend`` / ``frontend`` / ``windows`` | read and execute: backend / frontend / stack |
| ``config\\`` | nobody but administrators; ``districom.env`` → stack, ``backend\\`` → backend, ``Caddyfile`` → Caddy |
| PostgreSQL data | PostgreSQL (full) |
| ``data\\uploads`` | backend (modify) |
| ``logs\\`` and ``state\\`` | stack (modify); each service's own folder below them, protected, only for it |
| backup disk marker | stack (read) |

No service can write a release, a runtime or a service definition, nor change permissions (modify never includes
``WRITE_DAC``, ``WRITE_OWNER`` or delete-child). Links are never followed: a link below a managed folder that
points elsewhere stops ``apply`` before anything changes. Problems name the path and a code, never a value.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from districom_ops import ntfs
from districom_ops.ntfs import FULL, MODIFY, READ, READ_EXECUTE, WRITE_BITS, Ace, Dacl
from districom_ops.winservice import service_sid

ADMINISTRATORS = "S-1-5-32-544"
SYSTEM = "S-1-5-18"
# Principals that must never appear anywhere in an installation (C3-5).
BROAD = {
    "S-1-1-0": "Everyone",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-32-546": "Guests",
    "S-1-5-4": "Interactive",
    "S-1-5-7": "Anonymous",
    "S-1-5-2": "Network",
    "S-1-3-0": "Creator Owner",
    "S-1-5-80-0": "All Services",
}
BASE = (Ace(ADMINISTRATORS, FULL), Ace(SYSTEM, FULL))
CONTAINER = ntfs.OBJECT_INHERIT | ntfs.CONTAINER_INHERIT


class Backend(Protocol):
    def read(self, path: Path) -> Dacl: ...
    def write(self, path: Path, protected: bool, aces: tuple[Ace, ...]) -> None: ...
    def is_link(self, path: Path) -> bool: ...
    def link_target(self, path: Path) -> Path: ...


class _Ntfs:
    read = staticmethod(ntfs.read)
    write = staticmethod(ntfs.write)
    is_link = staticmethod(ntfs.is_link)
    link_target = staticmethod(ntfs.link_target)


NTFS: Backend = _Ntfs()


@dataclass(frozen=True)
class Rule:
    """``grants`` are explicit entries; a protected rule also drops what the parent would give (BASE is added)."""

    path: Path
    grants: tuple[tuple[str, int], ...] = ()
    protected: bool = False
    required: bool = True  # a missing optional path (a runtime not installed yet) is skipped, not a problem

    def aces(self, container: bool) -> tuple[Ace, ...]:
        flags = CONTAINER if container else 0
        explicit = [Ace(sid, mask, flags) for sid, mask in self.grants]
        if self.protected:
            explicit = [Ace(a.sid, a.mask, flags) for a in BASE] + explicit
        return tuple(sorted(explicit))


@dataclass(frozen=True)
class Problem:
    path: str
    code: str
    detail: str = ""

    def describe(self) -> str:
        return f"{self.path}: {self.code}" + (f" ({self.detail})" if self.detail else "")


@dataclass
class Policy:
    root: Path
    rules: list[Rule]
    trees: list[Path]  # what is walked: the root and the PostgreSQL data when it lives elsewhere
    links: dict[Path, Path] = field(default_factory=dict)  # allowed links outside their release: link → target
    release_dirs: list[Path] = field(default_factory=list)

    def rule_for(self, path: Path) -> Rule | None:
        for rule in self.rules:
            if rule.path == path:
                return rule
        return None


def _sids(*names: str) -> list[str]:
    return [service_sid(n) for n in names]


def policy(
    root: Path,
    release: Path | None,
    pg_data: Path,
    runtimes: dict[str, tuple[Path, list[str]]],
    winsw: list[str],
    names: dict[str, str],
    backup_marker: Path | None = None,
) -> Policy:
    """``names``: role → service name (backend, frontend, caddy, postgresql, stack). ``runtimes``: home → readers."""
    sid = {role: service_sid(name) for role, name in names.items()}
    rules: list[Rule] = [Rule(root, protected=True)]
    for home, readers in runtimes.values():
        rules.append(Rule(home, tuple((service_sid(n), READ_EXECUTE) for n in readers), required=False))
    for name in winsw:
        for suffix in (".exe", ".xml"):
            rules.append(Rule(root / "services" / f"{name}{suffix}", ((service_sid(name), READ_EXECUTE),)))
    release_dirs: list[Path] = []
    links: dict[Path, Path] = {}
    if release is not None:
        release_dirs.append(release)
        rules += [
            Rule(release / "backend", ((sid["backend"], READ_EXECUTE),)),
            Rule(release / "frontend", ((sid["frontend"], READ_EXECUTE),)),
            Rule(release / "windows", ((sid["stack"], READ_EXECUTE),)),
        ]
        links[release / "frontend" / ".next" / "cache"] = root / "state" / "frontend-cache"
    rules += [
        Rule(root / "config", protected=True),
        Rule(root / "config" / "districom.env", ((sid["stack"], READ),), protected=True),
        Rule(root / "config" / "backend", ((sid["backend"], READ_EXECUTE),), protected=True),
        Rule(root / "config" / "Caddyfile", ((sid["caddy"], READ),), protected=True),
        Rule(root / "config" / "postgres.secret", protected=True, required=False),
        Rule(root / "config" / "integrity.json", protected=True, required=False),
        Rule(pg_data, ((sid["postgresql"], FULL),), protected=True),
        Rule(root / "data" / "uploads", ((sid["backend"], MODIFY),)),
        Rule(root / "logs", ((sid["stack"], MODIFY),)),
        Rule(root / "logs" / "postgresql", ((sid["postgresql"], MODIFY),), protected=True),
        *(Rule(root / "logs" / "services" / n, ((service_sid(n), MODIFY),), protected=True) for n in winsw),
        Rule(root / "state", ((sid["stack"], MODIFY),)),
        Rule(root / "state" / "caddy", ((sid["caddy"], MODIFY),), protected=True),
        Rule(root / "state" / "frontend-cache", ((sid["frontend"], MODIFY),), protected=True),
    ]
    if backup_marker is not None:
        rules.append(Rule(backup_marker, ((sid["stack"], READ),), protected=True))
    trees = [root] if pg_data.is_relative_to(root) else [root, pg_data]
    return Policy(root, rules, trees, links, release_dirs)


# --- walking ----------------------------------------------------------------------------------------------------


def walk(tree: Path, backend: Backend) -> Iterator[tuple[Path, bool]]:
    """(path, is_container) below ``tree`` without following links; links are yielded once, not entered."""
    stack = [tree]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            if backend.is_link(entry):
                yield entry, True
                continue
            is_dir = entry.is_dir()
            yield entry, is_dir
            if is_dir:
                stack.append(entry)


def foreign_links(pol: Policy, backend: Backend) -> list[Problem]:
    """Links whose target is outside the release that holds them (or, for the allowed ones, not their target).
    Windows propagates permissions through what it can open, so ``apply`` refuses to run while one exists."""
    problems = []
    for tree in pol.trees:
        for path, _ in walk(tree, backend):
            if not backend.is_link(path):
                continue
            target = backend.link_target(path)
            if path in pol.links:
                ok = target == pol.links[path]
            else:
                ok = any(path.is_relative_to(r) and target.is_relative_to(r) for r in pol.release_dirs)
            if not ok:
                problems.append(Problem(_show(pol, path), "foreign_link", "points outside its release"))
    return problems


def _show(pol: Policy, path: Path) -> str:
    return str(path.relative_to(pol.root)) if path.is_relative_to(pol.root) else str(path)


# --- apply ------------------------------------------------------------------------------------------------------


def _wanted(rule: Rule, container: bool) -> tuple[bool, tuple[Ace, ...]]:
    return rule.protected, rule.aces(container)


def _current(dacl: Dacl) -> tuple[bool, tuple[Ace, ...]]:
    return dacl.protected, tuple(sorted(dacl.explicit))


class PermissionsError(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.describe() for p in problems))


def apply(pol: Policy, backend: Backend = NTFS) -> list[str]:
    """Make the permissions exactly the policy. Idempotent: a second run returns no change and writes nothing.

    Order: links are checked first (nothing changes if one is foreign); then the rules, parents before children;
    then everything below them loses explicit entries and protection that the policy does not define (for
    example the per-file grants of W2-C2, or a hand-made change), so it inherits from its rule."""
    problems = foreign_links(pol, backend)
    if problems:
        raise PermissionsError(problems)
    changes: list[str] = []
    rule_paths = {r.path for r in pol.rules}
    for rule in sorted(pol.rules, key=lambda r: len(r.path.parts)):
        if not rule.path.exists():
            if rule.required:
                raise PermissionsError([Problem(_show(pol, rule.path), "missing")])
            continue
        wanted = _wanted(rule, rule.path.is_dir())
        if _current(backend.read(rule.path)) != wanted:
            backend.write(rule.path, *wanted)
            changes.append(f"{_show(pol, rule.path)}: set")
    reset = 0
    for tree in pol.trees:
        for path, _ in walk(tree, backend):
            if path in rule_paths or backend.is_link(path):
                continue
            dacl = backend.read(path)
            if dacl.protected or dacl.explicit:
                backend.write(path, False, ())
                reset += 1
    if reset:
        changes.append(f"{reset} objects now inherit from their folder")
    return changes


# --- verify -----------------------------------------------------------------------------------------------------


def _allowed_mask(rule_chain: list[Rule], sid: str) -> int:
    """What the closest rules give ``sid`` (protected rules cut what is above them)."""
    mask = 0
    for rule in rule_chain:  # closest first
        for granted_sid, granted in rule.grants:
            if granted_sid == sid:
                mask |= granted
        if rule.protected:
            if sid in (ADMINISTRATORS, SYSTEM):
                mask |= FULL
            break
    return mask


def verify(pol: Policy, backend: Backend = NTFS) -> list[Problem]:
    """Read the effective lists (explicit and inherited) of every object and compare them with the policy."""
    problems = foreign_links(pol, backend)
    by_path = {r.path: r for r in pol.rules}

    def chain(path: Path) -> list[Rule]:
        rules = [by_path[p] for p in (path, *path.parents) if p in by_path]
        return rules

    for rule in pol.rules:
        if not rule.path.exists():
            if rule.required:
                problems.append(Problem(_show(pol, rule.path), "missing"))
            continue
        if _current(backend.read(rule.path)) != _wanted(rule, rule.path.is_dir()):
            problems.append(Problem(_show(pol, rule.path), "not_as_policy"))

    objects: list[Path] = [r.path for r in pol.rules if r.path.exists()]
    for tree in pol.trees:
        objects += [p for p, _ in walk(tree, backend) if not backend.is_link(p)]
    for path in dict.fromkeys(objects):
        dacl = backend.read(path)
        shown = _show(pol, path)
        if dacl.null:
            problems.append(Problem(shown, "no_dacl", "everyone has full access"))
            continue
        if path not in by_path and dacl.protected:
            problems.append(Problem(shown, "own_entries", "does not inherit from its folder"))
        # Explicit entries below a rule (a file a service created, a hand-made grant) are judged like inherited
        # ones: they may not give more than the rule. ``apply`` removes them all.
        rules = chain(path)
        for ace in dacl.aces:
            if ace.type != ntfs.ACCESS_ALLOWED:
                continue  # a deny entry never gives access
            if ace.flags & ntfs.INHERIT_ONLY:
                continue  # applies to children, which are checked themselves
            if ace.sid in BROAD:
                problems.append(Problem(shown, "broad_access", BROAD[ace.sid]))
                continue
            allowed = _allowed_mask(rules, ace.sid)
            if ace.mask & ~allowed:
                excess = ace.mask & ~allowed
                kind = "write" if excess & WRITE_BITS else "read"
                problems.append(Problem(shown, f"excess_{kind}", ace.sid))
    return problems


def snapshot(pol: Policy, backend: Backend = NTFS) -> dict[str, tuple[bool, tuple[Ace, ...]]]:
    """Every list below the managed trees (C3-4: applying twice must leave this identical)."""
    found: dict[str, tuple[bool, tuple[Ace, ...]]] = {}
    paths = [r.path for r in pol.rules if r.path.exists()]
    for tree in pol.trees:
        paths += [p for p, _ in walk(tree, backend) if not backend.is_link(p)]
    for path in paths:
        dacl = backend.read(path)
        found[str(path)] = (dacl.protected, tuple(sorted(dacl.aces)))
    return found
