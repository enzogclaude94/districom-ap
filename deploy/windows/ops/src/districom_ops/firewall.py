"""Windows Firewall rules of DistriCom (W2-C4, ``docs/WINDOWS_DEPLOYMENT.md §4`` and ``§8.5``).

Every rule DistriCom owns is in the group ``DistriCom`` and named ``DistriCom-*``; nothing else is ever created,
changed or removed (not the rules of Store Sys, of Remote Desktop or of any other program, nor the profiles):

| Rule | What |
|---|---|
| ``DistriCom-HTTP-In`` / ``-HTTPS-In`` | allow TCP HTTP_PORT / HTTPS_PORT to Caddy, from LAN_SUBNET and Tailscale |
| ``DistriCom-RDP-In`` | allow TCP 3389, only from LAN_SUBNET and Tailscale |
| ``DistriCom-Block-PostgreSQL`` / ``-Backend`` / ``-Frontend`` | block TCP 5432 / 8000 / 3000 to that program |

Remote Desktop can only gain access: DistriCom never blocks 3389 and never changes the built-in Remote Desktop
rules (whether they stay wider is the IT decision ``verify`` reports). The block rules name the program and its
port, so they cannot affect another program on the same port; loopback traffic is not filtered by Windows, so
the services keep talking to each other on ``127.0.0.1`` (where they listen: ``verify`` checks the sockets too).

``apply`` is idempotent (a rule that is already right is not touched) and ``remove`` takes every DistriCom rule
away and nothing else. Rules are read and written with the NetSecurity cmdlets through PowerShell, exchanging
JSON; values are normalized (``a.b.c.d/255.255.0.0`` is ``a.b.c.d/16``) before comparing.
"""

import ipaddress
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GROUP = "DistriCom"
PREFIX = "DistriCom-"
TAILSCALE = "100.64.0.0/10"
RDP_PORT = 3389
POWERSHELL = "powershell.exe"


class FirewallError(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    display_name: str
    action: str  # Allow | Block
    protocol: str
    local_ports: tuple[str, ...]
    remote_addresses: tuple[str, ...] = ("Any",)
    program: str = "Any"
    direction: str = "Inbound"
    profile: str = "Any"
    enabled: bool = True
    group: str = GROUP

    def key(self) -> tuple[object, ...]:
        """What must match exactly (the display name and description are not part of the scope)."""
        return (self.action, self.protocol, tuple(sorted(self.local_ports)), tuple(sorted(self.remote_addresses)),
                _program_key(self.program), self.direction, self.profile, self.enabled, self.group)  # fmt: skip


@dataclass(frozen=True)
class Listener:
    address: str
    port: int
    program: str


@dataclass(frozen=True)
class Problem:
    name: str
    code: str
    detail: str = ""

    def describe(self) -> str:
        return f"{self.name}: {self.code}" + (f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True)
class Internal:
    """A port that must only be reachable on ``127.0.0.1``, and the program that listens on it."""

    port: int
    program: Path
    role: str


@dataclass
class Plan:
    rules: list[Rule]
    internal: list[Internal] = field(default_factory=list)


def _program_key(program: str) -> str:
    return program if program == "Any" else os.path.normcase(os.path.normpath(program))


def normalize_address(value: str) -> str:
    """``LocalSubnet``-style keywords stay; networks become ``a.b.c.d/n``; single hosts ``a.b.c.d``."""
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return value
    return str(network.network_address) if network.num_addresses == 1 else str(network)


def plan(
    lan_subnet: str,
    http_port: int,
    https_port: int,
    caddy: Path,
    internal: list[Internal],
) -> Plan:
    remote = (normalize_address(lan_subnet), TAILSCALE)
    rules = [
        Rule(f"{PREFIX}HTTP-In", "DistriCom HTTP (redirección a HTTPS)", "Allow", "TCP", (str(http_port),), remote,
             str(caddy)),
        Rule(f"{PREFIX}HTTPS-In", "DistriCom HTTPS", "Allow", "TCP", (str(https_port),), remote, str(caddy)),
        Rule(f"{PREFIX}RDP-In", "DistriCom Escritorio remoto (LAN y Tailscale)", "Allow", "TCP", (str(RDP_PORT),),
             remote),
    ]  # fmt: skip
    for item in internal:
        rules.append(
            Rule(f"{PREFIX}Block-{item.role}", f"DistriCom {item.role}: solo 127.0.0.1", "Block", "TCP",
                 (str(item.port),), ("Any",), str(item.program))
        )  # fmt: skip
    return Plan(rules, internal)


# --- PowerShell bridge -----------------------------------------------------------------------------------------

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
function Read-Rules($rules) {
    $ports = @{}; Get-NetFirewallPortFilter -All | ForEach-Object { $ports[$_.InstanceID] = $_ }
    $addresses = @{}; Get-NetFirewallAddressFilter -All | ForEach-Object { $addresses[$_.InstanceID] = $_ }
    $programs = @{}; Get-NetFirewallApplicationFilter -All | ForEach-Object { $programs[$_.InstanceID] = $_ }
    foreach ($rule in $rules) {
        $p = $ports[$rule.InstanceID]; $a = $addresses[$rule.InstanceID]; $g = $programs[$rule.InstanceID]
        [pscustomobject]@{
            name = [string]$rule.Name; display_name = [string]$rule.DisplayName; group = [string]$rule.Group
            action = [string]$rule.Action; direction = [string]$rule.Direction; profile = [string]$rule.Profile
            enabled = ([string]$rule.Enabled -eq 'True'); protocol = [string]$p.Protocol
            local_ports = @($p.LocalPort | ForEach-Object { [string]$_ })
            remote_addresses = @($a.RemoteAddress | ForEach-Object { [string]$_ })
            program = [string]$g.Program
        }
    }
}
switch ($request.op) {
    'read' {
        $own = @(Get-NetFirewallRule -All |
            Where-Object { $_.Group -eq $request.group -or $_.Name -like $request.prefix })
        $result = @(Read-Rules $own)
    }
    'read_all' { $result = @(Read-Rules @(Get-NetFirewallRule -All)) }
    'create' {
        $r = $request.rule
        $params = @{
            Name = $r.name; DisplayName = $r.display_name; Group = $r.group; Action = $r.action
            Direction = $r.direction; Profile = $r.profile; Protocol = $r.protocol; LocalPort = @($r.local_ports)
            RemoteAddress = @($r.remote_addresses); Program = $r.program
            Enabled = $(if ($r.enabled) { 'True' } else { 'False' })
            Description = 'Managed by districom-ops (W2-C4). Do not edit: firewall apply restores it.'
        }
        [void](New-NetFirewallRule @params)
        $result = @()
    }
    'remove' {
        foreach ($name in @($request.names)) { Remove-NetFirewallRule -Name $name }
        $result = @()
    }
    'listeners' {
        $paths = @{}
        Get-CimInstance Win32_Process | ForEach-Object { $paths[[int]$_.ProcessId] = [string]$_.ExecutablePath }
        $ports = @($request.ports | ForEach-Object { [int]$_ })
        $result = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $ports -contains [int]$_.LocalPort } |
            ForEach-Object { [pscustomobject]@{ address = [string]$_.LocalAddress; port = [int]$_.LocalPort
                                                program = $paths[[int]$_.OwningProcess] } })
    }
    'profiles' {
        $result = @(Get-NetFirewallProfile | ForEach-Object {
            [pscustomobject]@{ name = [string]$_.Name; enabled = ([string]$_.Enabled -eq 'True')
                               inbound = [string]$_.DefaultInboundAction } })
    }
}
ConvertTo-Json -InputObject @($result) -Depth 5 -Compress
"""

Runner = Callable[[dict[str, Any]], list[dict[str, Any]]]


def powershell(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one operation; raises with the cmdlet's message (no values of ours are in it but rule names)."""
    process = subprocess.run(  # noqa: S603 - fixed program, the request goes through stdin as JSON
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    if process.returncode != 0:
        raise FirewallError(f"firewall {request['op']} failed: {process.stderr.strip()[-500:]}")
    text = process.stdout.strip()
    data = json.loads(text) if text else []
    return data if isinstance(data, list) else [data]


def _rule(item: dict[str, Any]) -> Rule:
    def many(key: str) -> tuple[str, ...]:
        value = item.get(key) or []
        return tuple(str(v) for v in (value if isinstance(value, (list, tuple)) else [value]))

    return Rule(
        name=str(item["name"]),
        display_name=str(item.get("display_name", "")),
        action=str(item["action"]),
        protocol=str(item.get("protocol", "")),
        local_ports=many("local_ports"),
        remote_addresses=tuple(normalize_address(a) for a in many("remote_addresses")),
        program=str(item.get("program") or "Any"),
        direction=str(item["direction"]),
        profile=str(item["profile"]),
        enabled=bool(item["enabled"]),
        group=str(item.get("group", "")),
    )


def read_own(run: Runner = powershell) -> list[Rule]:
    return [_rule(i) for i in run({"op": "read", "group": GROUP, "prefix": f"{PREFIX}*"})]


def read_all(run: Runner = powershell) -> list[Rule]:
    return [_rule(i) for i in run({"op": "read_all"})]


def listeners(ports: list[int], run: Runner = powershell) -> list[Listener]:
    return [
        Listener(str(i["address"]), int(i["port"]), str(i.get("program") or ""))
        for i in run({"op": "listeners", "ports": ports})
    ]


# --- apply, remove, verify ---------------------------------------------------------------------------------------


def apply(pol: Plan, run: Runner = powershell) -> list[str]:
    """Make the DistriCom rules exactly the plan. A second run changes nothing (C4-2)."""
    wanted = {r.name: r for r in pol.rules}
    current: dict[str, list[Rule]] = {}
    for rule in read_own(run):
        current.setdefault(rule.name, []).append(rule)
    changes: list[str] = []
    stale = sorted(n for n in current if n not in wanted)
    if stale:
        run({"op": "remove", "names": stale})
        changes += [f"{n}: removed" for n in stale]
    for name, rule in wanted.items():
        found = current.get(name, [])
        if len(found) == 1 and found[0].key() == rule.key():
            continue
        if found:
            run({"op": "remove", "names": [name]})
        run({"op": "create", "rule": asdict(rule)})
        changes.append(f"{name}: {'updated' if found else 'created'}")
    return changes


def remove(run: Runner = powershell) -> list[str]:
    """Take away every DistriCom rule (and only those): the firewall is as it was before DistriCom."""
    names = sorted({r.name for r in read_own(run)})
    if names:
        run({"op": "remove", "names": names})
    return names


def verify(pol: Plan, run: Runner = powershell) -> tuple[list[Problem], list[Problem]]:
    """(problems, warnings). Read only. Warnings are about what is not DistriCom's to change."""
    problems: list[Problem] = []
    warnings: list[Problem] = []
    current: dict[str, list[Rule]] = {}
    for rule in read_own(run):
        current.setdefault(rule.name, []).append(rule)
    wanted = {r.name: r for r in pol.rules}
    for name, rule in wanted.items():
        found = current.get(name, [])
        if not found:
            problems.append(Problem(name, "missing"))
        elif len(found) > 1:
            problems.append(Problem(name, "duplicated", str(len(found))))
        elif found[0].key() != rule.key():
            problems.append(Problem(name, "not_as_plan", _difference(rule, found[0])))
    problems += [Problem(n, "unexpected") for n in sorted(current) if n not in wanted]
    for profile in run({"op": "profiles"}):
        if not profile.get("enabled"):
            problems.append(Problem(f"profile {profile.get('name')}", "firewall_off", "the rules have no effect"))
        elif str(profile.get("inbound")) not in ("Block", "NotConfigured"):
            problems.append(Problem(f"profile {profile.get('name')}", "inbound_allowed_by_default"))
    if pol.internal:
        programs = {i.port: i.program for i in pol.internal}
        for listener in listeners(sorted(programs), run):
            if listener.address != "127.0.0.1":
                problems.append(Problem(f"port {listener.port}", "listens_outside_loopback", listener.address))
            if listener.program and _program_key(listener.program) != _program_key(str(programs[listener.port])):
                warnings.append(Problem(f"port {listener.port}", "other_program", "not the program of its rule"))
    for rule in read_all(run):
        if rule.name.startswith(PREFIX) or rule.group == GROUP:
            continue
        inbound_rdp = rule.enabled and rule.direction == "Inbound" and _opens(rule, RDP_PORT)
        if inbound_rdp and rule.action == "Allow" and "Any" in rule.remote_addresses:
            warnings.append(Problem(rule.name, "rdp_open_to_any", "not DistriCom's: an IT decision"))
        if inbound_rdp and rule.action == "Block":
            warnings.append(Problem(rule.name, "rdp_blocked_by_other_rule", "not DistriCom's: an IT decision"))
    return problems, warnings


def _opens(rule: Rule, port: int) -> bool:
    if rule.protocol not in ("TCP", "Any", "6"):
        return False
    return any(_port_matches(p, port) for p in rule.local_ports)


def _port_matches(spec: str, port: int) -> bool:
    if spec == str(port):
        return True
    if "-" in spec:
        low, _, high = spec.partition("-")
        return low.isdigit() and high.isdigit() and int(low) <= port <= int(high)
    return spec == "RDP"


def _difference(wanted: Rule, found: Rule) -> str:
    fields = ("action", "protocol", "local_ports", "remote_addresses", "program", "direction", "profile",
              "enabled", "group")  # fmt: skip
    different = [f for f in fields if getattr(wanted, f) != getattr(found, f)]
    return ", ".join(different) or "program"
