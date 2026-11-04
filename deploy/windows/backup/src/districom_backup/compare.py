"""Exact comparison of two databases with ``deploy/sql/verify.sql``.

The same fingerprint the Linux restore drills use: schema version, structure and, table by table,
a hash of every row. Two databases are ``IDENTICAL`` only if the outputs match line by line.
"""

import difflib
from pathlib import Path

from districom_backup import pgtools

DEFAULT_VERIFY_SQL = Path(__file__).resolve().parents[4] / "sql" / "verify.sql"


def fingerprint(instance: pgtools.Instance, dbname: str, *, sql_file: Path = DEFAULT_VERIFY_SQL) -> str:
    if not sql_file.is_file():
        raise FileNotFoundError(f"verify.sql not found: {sql_file}")
    return pgtools.run(
        [
            pgtools.tool("psql", instance.bin_dir),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(instance.port),
            "-U",
            instance.user,
            "-d",
            dbname,
            "-f",
            str(sql_file),
        ],
        timeout=3600,
    )


def compare(expected: str, actual: str) -> tuple[str, list[str]]:
    """``("IDENTICAL", [])`` or ``("DIFFERENT", diff)``."""
    if expected == actual:
        return "IDENTICAL", []
    diff = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(), "expected", "actual", lineterm=""))
    return "DIFFERENT", diff
