"""W2-C4 firewall plan, apply, remove and verify against a fake firewall (any platform).

The real rules are written and read on the Windows runner (``firewall_tests/``); here the logic: the exact scope
of each rule, idempotence, that nothing outside DistriCom is touched and what ``verify`` reports.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from districom_ops import firewall
from districom_ops.firewall import Internal, Rule

CADDY = Path("C:/DistriCom/runtime/caddy/caddy.exe")
INTERNAL = [
    Internal(5432, Path("C:/DistriCom/runtime/postgresql/pgsql/bin/postgres.exe"), "PostgreSQL"),
    Internal(8000, Path("C:/DistriCom/runtime/python/cpython-3.12.16/python.exe"), "Backend"),
    Internal(3000, Path("C:/DistriCom/runtime/node/node.exe"), "Frontend"),
]
STORE_SYS = Rule("StoreSys-SQL", "Store Sys SQL", "Allow", "TCP", ("1433",), ("192.168.1.0/24",), group="Store Sys")
BUILTIN_RDP = Rule("RemoteDesktop-UserMode-In-TCP", "Escritorio remoto", "Allow", "TCP", ("3389",), group="@rdp")


@dataclass
class FakeFirewall:
    """Rules as Windows keeps them (several may share a name), profiles and listening sockets."""

    rules: list[Rule] = field(default_factory=lambda: [STORE_SYS, BUILTIN_RDP])
    profiles: list[dict[str, Any]] = field(
        default_factory=lambda: [{"name": n, "enabled": True, "inbound": "NotConfigured"} for n in
                                 ("Domain", "Private", "Public")]
    )  # fmt: skip
    sockets: list[dict[str, Any]] = field(default_factory=list)
    writes: int = 0

    def __call__(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        op = request["op"]
        if op == "read":
            return [self._out(r) for r in self.rules if r.group == request["group"] or r.name.startswith("DistriCom-")]
        if op == "read_all":
            return [self._out(r) for r in self.rules]
        if op == "create":
            self.writes += 1
            item = request["rule"]
            self.rules.append(
                Rule(**{**item, "local_ports": tuple(item["local_ports"]),
                        "remote_addresses": tuple(item["remote_addresses"])})
            )  # fmt: skip
            return []
        if op == "remove":
            self.writes += 1
            self.rules = [r for r in self.rules if r.name not in request["names"]]
            return []
        if op == "listeners":
            return [s for s in self.sockets if s["port"] in request["ports"]]
        if op == "profiles":
            return self.profiles
        raise AssertionError(op)

    @staticmethod
    def _out(rule: Rule) -> dict[str, Any]:
        data = asdict(rule)
        # Windows answers networks with masks and lists as lists
        data["remote_addresses"] = [
            "100.64.0.0/255.192.0.0" if a == "100.64.0.0/10" else a for a in rule.remote_addresses
        ]
        return data


def plan() -> firewall.Plan:
    return firewall.plan("192.168.1.0/24", 80, 443, CADDY, INTERNAL)


def own(fake: FakeFirewall) -> dict[str, Rule]:
    return {r.name: r for r in fake.rules if r.name.startswith("DistriCom-")}


def test_each_rule_has_its_exact_scope() -> None:
    rules = {r.name: r for r in plan().rules}
    lan_and_tailscale = ("192.168.1.0/24", "100.64.0.0/10")
    assert rules["DistriCom-HTTPS-In"].remote_addresses == lan_and_tailscale
    assert rules["DistriCom-HTTPS-In"].local_ports == ("443",)
    assert rules["DistriCom-HTTPS-In"].program == str(CADDY)
    assert rules["DistriCom-HTTP-In"].local_ports == ("80",)
    rdp = rules["DistriCom-RDP-In"]
    assert (rdp.action, rdp.local_ports, rdp.remote_addresses) == ("Allow", ("3389",), lan_and_tailscale)
    for item in INTERNAL:
        block = rules[f"DistriCom-Block-{item.role}"]
        assert (block.action, block.local_ports, block.program) == ("Block", (str(item.port),), str(item.program))
    assert all(r.group == "DistriCom" and r.direction == "Inbound" for r in rules.values())


def test_remote_desktop_is_never_blocked() -> None:
    for rule in plan().rules:
        if rule.action == "Block":
            assert "3389" not in rule.local_ports
            assert rule.program != "Any"


def test_apply_creates_the_rules_and_a_second_run_changes_nothing() -> None:
    fake = FakeFirewall()
    changes = firewall.apply(plan(), fake)
    assert len(changes) == len(plan().rules)
    assert firewall.verify(plan(), fake)[0] == []
    writes = fake.writes
    assert firewall.apply(plan(), fake) == []
    assert fake.writes == writes, "the second run wrote something (C4-2)"
    assert len(own(fake)) == len([r for r in fake.rules if r.name.startswith("DistriCom-")]), "duplicated"


def test_a_widened_or_duplicated_rule_is_reported_and_restored() -> None:
    fake = FakeFirewall()
    firewall.apply(plan(), fake)
    https = own(fake)["DistriCom-HTTPS-In"]
    fake.rules = [r for r in fake.rules if r.name != https.name]
    widened = Rule(**{**asdict(https), "remote_addresses": ("Any",)})
    fake.rules += [widened, widened]
    fake.rules.append(Rule("DistriCom-Extra", "x", "Allow", "TCP", ("5432",), group="DistriCom"))
    problems, _ = firewall.verify(plan(), fake)
    assert {(p.name, p.code) for p in problems} == {("DistriCom-HTTPS-In", "duplicated"),
                                                    ("DistriCom-Extra", "unexpected")}  # fmt: skip
    changes = firewall.apply(plan(), fake)
    assert "DistriCom-Extra: removed" in changes
    assert "DistriCom-HTTPS-In: updated" in changes
    assert firewall.verify(plan(), fake)[0] == []
    assert own(fake)["DistriCom-HTTPS-In"].remote_addresses == ("192.168.1.0/24", "100.64.0.0/10")


def test_apply_and_remove_never_touch_other_rules() -> None:
    fake = FakeFirewall()
    foreign = list(fake.rules)
    firewall.apply(plan(), fake)
    assert [r for r in fake.rules if not r.name.startswith("DistriCom-")] == foreign
    removed = firewall.remove(fake)
    assert sorted(removed) == sorted(r.name for r in plan().rules)
    assert fake.rules == foreign, "remove must leave the firewall as it was (C4-4)"
    assert firewall.remove(fake) == []


def test_a_rule_named_like_ours_in_another_group_is_still_ours_to_report() -> None:
    fake = FakeFirewall()
    firewall.apply(plan(), fake)
    fake.rules.append(Rule("DistriCom-Old", "x", "Allow", "TCP", ("8000",), group="Otro"))
    assert ("DistriCom-Old", "unexpected") in {(p.name, p.code) for p in firewall.verify(plan(), fake)[0]}


def test_verify_reports_a_listener_outside_loopback_and_a_firewall_off() -> None:
    fake = FakeFirewall()
    firewall.apply(plan(), fake)
    fake.sockets = [
        {"address": "127.0.0.1", "port": 5432, "program": str(INTERNAL[0].program)},
        {"address": "0.0.0.0", "port": 8000, "program": str(INTERNAL[1].program)},  # noqa: S104
        {"address": "127.0.0.1", "port": 3000, "program": "C:/Otro/node.exe"},
    ]
    fake.profiles[2]["enabled"] = False
    problems, warnings = firewall.verify(plan(), fake)
    assert {(p.name, p.code) for p in problems} == {("port 8000", "listens_outside_loopback"),
                                                    ("profile Public", "firewall_off")}  # fmt: skip
    assert ("port 3000", "other_program") in {(w.name, w.code) for w in warnings}


def test_verify_warns_but_never_changes_a_wider_remote_desktop_rule() -> None:
    fake = FakeFirewall()
    firewall.apply(plan(), fake)
    _, warnings = firewall.verify(plan(), fake)
    assert ("RemoteDesktop-UserMode-In-TCP", "rdp_open_to_any") in {(w.name, w.code) for w in warnings}
    assert BUILTIN_RDP in fake.rules


def test_addresses_are_compared_normalized() -> None:
    assert firewall.normalize_address("100.64.0.0/255.192.0.0") == "100.64.0.0/10"
    assert firewall.normalize_address("10.0.0.5/255.255.255.255") == "10.0.0.5"
    assert firewall.normalize_address("Any") == "Any"
    assert firewall.normalize_address("LocalSubnet") == "LocalSubnet"


def test_the_backend_rule_names_the_interpreter_the_venv_launcher_starts(tmp_path: Path) -> None:
    from districom_ops.services import base_python

    home = tmp_path / "runtime" / "python" / "cpython-3.12.16"
    home.mkdir(parents=True)
    (home / "python.exe").write_text("")
    launcher = tmp_path / "release" / ".venv" / "Scripts" / "python.exe"
    launcher.parent.mkdir(parents=True)
    (launcher.parent.parent / "pyvenv.cfg").write_text(f"home = {home}\nimplementation = CPython\n", "utf-8")
    assert base_python(launcher) == home / "python.exe"  # as written: the image path Windows reports


def test_verify_logs_problems_and_warnings_without_reserved_log_keys(monkeypatch: Any) -> None:
    """``name`` is a LogRecord attribute: using it as an extra key broke ``firewall verify`` on Windows."""
    import logging

    from districom_ops import cli, services

    problem = firewall.Problem("DistriCom-HTTPS-In", "not_as_plan", "remote_addresses")
    monkeypatch.setattr(services, "firewall_plan", lambda install: plan())
    monkeypatch.setattr(firewall, "verify", lambda pol: ([problem], [problem]))
    logging.getLogger("districom_ops").setLevel(logging.INFO)
    assert cli._firewall("verify", None) == 1  # type: ignore[arg-type]
