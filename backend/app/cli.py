"""Administrative commands: `uv run python -m app.cli create-admin --email ... --name ...`."""

import argparse
import getpass
import logging
import sys

from app.auth.service import create_user
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_db

logger = logging.getLogger("app.cli")


def _create_user(args: argparse.Namespace) -> int:
    password = getpass.getpass("Contraseña: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    db = next(get_db())
    try:
        user = create_user(db, args.email, args.name, password, args.role)
    except ValueError as exc:
        logger.error("create_user_failed", extra={"reason": str(exc)})
        return 1
    logger.info("user_created", extra={"user_id": user.id, "role": user.role})
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, role in (("create-admin", "admin"), ("create-viewer", "viewer")):
        cmd = sub.add_parser(name, help=f"Crear usuario {role} (la contraseña se lee de stdin)")
        cmd.add_argument("--email", required=True)
        cmd.add_argument("--name", required=True)
        cmd.set_defaults(func=_create_user, role=role)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
