"""Operations configuration: ``<root>\\config\\districom.env`` (``KEY=value`` lines, ``#`` comments).

Rules:

* Variables are read literally: no shell expansion, no quotes interpreted beyond one surrounding pair.
* A missing or insecure variable is an error that names the variable and the reason, **never its value**.
* Secret values are wrapped in :class:`Secret`, whose ``repr`` and ``str`` never show them, and they are
  registered with the log redaction (``districom_ops.logs``) as soon as they are read.
* The databases are only reachable on 127.0.0.1 (``docs/WINDOWS_DEPLOYMENT.md §4``).

The backend keeps validating its own production configuration (W2-B, B-6); this file only covers what the
operations tools need. Later stages add their variables here, each with its validation and tests.
"""

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MIN_PASSWORD_LENGTH = 16
DEFAULT_MAINTENANCE_MAX_MINUTES = 60
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
# Values that example files and tutorials use: refused even when long enough.
WEAK_PASSWORDS = {"districom", "postgres", "password", "changeme", "change-me", "secret", "admin", "123456"}
PLACEHOLDER = re.compile(r"^(<.*>|\.\.\.|x+|changeme.*|replace.*|cambiar.*)$", re.IGNORECASE)


class Secret:
    """A value that never appears in a repr, a str, an error or a log."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__


@dataclass(frozen=True)
class Problem:
    variable: str
    reason: str

    def __str__(self) -> str:
        return f"{self.variable}: {self.reason}"


class ConfigError(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


@dataclass(frozen=True)
class OpsConfig:
    root: Path
    host: str
    database_url: Secret
    ops_database_url: Secret
    lan_subnet: ipaddress.IPv4Network
    maintenance_max_minutes: int
    alert_command: str | None

    def secrets(self) -> list[str]:
        """Every secret value, for the log redaction; includes the passwords alone, not only the URLs."""
        values = []
        for url in (self.database_url, self.ops_database_url):
            values.append(url.reveal())
            password = urlsplit(url.reveal()).password
            if password:
                values.append(password)
        return values


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigError([Problem(f"line {number}", "expected KEY=value with an uppercase name")])
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in values:
            raise ConfigError([Problem(key, f"defined twice (line {number})")])
        values[key] = value
    return values


def _database_url(values: Mapping[str, str], key: str, problems: list[Problem]) -> Secret:
    raw = values.get(key, "")
    if not raw:
        problems.append(Problem(key, "missing"))
        return Secret("")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        problems.append(Problem(key, "not a valid URL"))
        return Secret(raw)
    if not parts.scheme.startswith("postgresql"):
        problems.append(Problem(key, "must be a postgresql URL"))
    if parts.hostname not in LOCAL_HOSTS:
        problems.append(Problem(key, "the database must be on 127.0.0.1"))
    if port is not None and not 1 <= port <= 65535:
        problems.append(Problem(key, "invalid port"))
    if not parts.username:
        problems.append(Problem(key, "missing user"))
    password = parts.password or ""
    if not password:
        problems.append(Problem(key, "missing password"))
    elif (
        len(password) < MIN_PASSWORD_LENGTH
        or password.lower() in WEAK_PASSWORDS
        or PLACEHOLDER.match(password)
        or password.lower() == (parts.username or "").lower()
    ):
        problems.append(Problem(key, f"weak password (at least {MIN_PASSWORD_LENGTH} characters, not a known value)"))
    return Secret(raw)


def load_config(values: Mapping[str, str]) -> OpsConfig:
    """Validate every variable and report all the problems at once, without any value."""
    problems: list[Problem] = []

    root_raw = values.get("DISTRICOM_ROOT", "")
    root = Path(root_raw)
    if not root_raw:
        problems.append(Problem("DISTRICOM_ROOT", "missing"))
    elif not root.is_absolute():
        problems.append(Problem("DISTRICOM_ROOT", "must be an absolute path"))

    host = values.get("DISTRICOM_HOST", "")
    if not host:
        problems.append(Problem("DISTRICOM_HOST", "missing"))
    elif not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        problems.append(Problem("DISTRICOM_HOST", "not a valid host name"))

    database_url = _database_url(values, "DATABASE_URL", problems)
    ops_database_url = _database_url(values, "OPS_DATABASE_URL", problems)
    if database_url.reveal() and database_url.reveal() == ops_database_url.reveal():
        problems.append(Problem("OPS_DATABASE_URL", "must use its own role, not the application's"))
    else:
        app_user = urlsplit(database_url.reveal()).username if database_url.reveal() else None
        ops_user = urlsplit(ops_database_url.reveal()).username if ops_database_url.reveal() else None
        if app_user and app_user == ops_user:
            problems.append(Problem("OPS_DATABASE_URL", "must use its own role, not the application's"))

    subnet = ipaddress.IPv4Network("0.0.0.0/32")
    subnet_raw = values.get("LAN_SUBNET", "")
    if not subnet_raw:
        problems.append(Problem("LAN_SUBNET", "missing"))
    else:
        try:
            subnet = ipaddress.IPv4Network(subnet_raw, strict=True)
            if not subnet.is_private or subnet.prefixlen < 16:
                problems.append(Problem("LAN_SUBNET", "must be a private network of /16 or smaller"))
        except ValueError:
            problems.append(Problem("LAN_SUBNET", "not a valid IPv4 network (for example 192.168.1.0/24)"))

    minutes = DEFAULT_MAINTENANCE_MAX_MINUTES
    minutes_raw = values.get("MAINTENANCE_MAX_MINUTES", "")
    if minutes_raw:
        if not minutes_raw.isdigit() or not 1 <= int(minutes_raw) <= 1440:
            problems.append(Problem("MAINTENANCE_MAX_MINUTES", "must be a whole number of minutes between 1 and 1440"))
        else:
            minutes = int(minutes_raw)

    alert = values.get("ALERT_COMMAND") or None

    if problems:
        raise ConfigError(problems)
    return OpsConfig(
        root=root,
        host=host,
        database_url=database_url,
        ops_database_url=ops_database_url,
        lan_subnet=subnet,
        maintenance_max_minutes=minutes,
        alert_command=alert,
    )


def read_config(path: Path) -> OpsConfig:
    if not path.is_file():
        raise ConfigError([Problem(str(path), "configuration file not found")])
    return load_config(parse_env(path.read_text(encoding="utf-8-sig")))
