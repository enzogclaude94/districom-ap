"""Administrative commands: `uv run python -m app.cli create-admin --email ... --name ...`.

Module access (ARCHITECTURE.md §8): `grant-module-role --email ... --module settlements --role operator`,
`revoke-module-role --email ... --module settlements`. The same commands grant a protected area (ADR 010):
`--module settlements.employee_finance`; a platform admin needs that grant too to read the area.

`reset-password --email ...` (S2) gives a user a temporary password and closes all their sessions: the way back in
when nobody who can administer users can log in. The password is printed once on stdout, never logged.
"""

import argparse
import getpass
import logging
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_db
from app.platform.auth.models import MODULE_ROLES, User
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role
from app.platform.auth.service import create_user, set_temporary_password

logger = logging.getLogger("app.cli")


@contextmanager
def _session() -> Iterator[Session]:
    """A session closed when the command ends, also on an error: a session left to the garbage collector
    keeps its transaction (and its locks) open."""
    sessions = cast(Generator[Session], get_db())  # get_db is a generator function
    try:
        yield next(sessions)
    finally:
        sessions.close()


def _create_user(args: argparse.Namespace) -> int:
    password = getpass.getpass("Contraseña: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    with _session() as db:
        try:
            user = create_user(db, args.email, args.name, password, args.role)
        except ValueError as exc:
            logger.error("create_user_failed", extra={"reason": str(exc)})
            return 1
        logger.info("user_created", extra={"user_id": user.id, "role": user.role})
    return 0


def _installed_module_keys() -> list[str]:
    import app.api.v1.router  # noqa: F401  (composition root: installs the modules)
    from app.platform.modules import installed_areas, installed_modules

    return [module.key for module in installed_modules()] + [area.key for _, area in installed_areas()]


def _grant(args: argparse.Namespace) -> int:
    with _session() as db:
        try:
            grant = grant_module_role(db, args.email, args.module, args.role)
        except ModuleRoleError as exc:
            logger.error("grant_module_role_failed", extra={"reason": str(exc)})
            return 1
        logger.info(
            "module_role_granted", extra={"user_id": grant.user_id, "module_key": args.module, "role": grant.role}
        )
    return 0


def _revoke(args: argparse.Namespace) -> int:
    with _session() as db:
        try:
            revoked = revoke_module_role(db, args.email, args.module)
        except ModuleRoleError as exc:
            logger.error("revoke_module_role_failed", extra={"reason": str(exc)})
            return 1
    logger.info("module_role_revoked" if revoked else "module_role_absent", extra={"module_key": args.module})
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    with _session() as db:
        user = db.scalar(select(User).where(User.email == args.email))
        if user is None:
            logger.error("reset_password_failed", extra={"reason": "unknown user"})
            return 1
        password = set_temporary_password(db, user, get_settings(), by=None)
        logger.info("password_reset", extra={"user_id": user.id})
    # The one place the temporary password appears: the terminal of whoever ran the command.
    sys.stdout.write(f"Contraseña temporal (vence en {get_settings().temporary_password_hours} h): {password}\n")
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
    modules = _installed_module_keys()
    grant = sub.add_parser(
        "grant-module-role", help="Dar o cambiar el rol de un usuario en un módulo o un área protegida"
    )
    grant.add_argument("--email", required=True)
    grant.add_argument("--module", required=True, choices=modules)
    grant.add_argument("--role", required=True, choices=MODULE_ROLES)
    grant.set_defaults(func=_grant)
    revoke = sub.add_parser("revoke-module-role", help="Quitar el acceso de un usuario a un módulo o un área protegida")
    revoke.add_argument("--email", required=True)
    revoke.add_argument("--module", required=True, choices=modules)
    revoke.set_defaults(func=_revoke)
    reset = sub.add_parser(
        "reset-password", help="Dar una contraseña temporal a un usuario y cerrar sus sesiones (se muestra una vez)"
    )
    reset.add_argument("--email", required=True)
    reset.set_defaults(func=_reset_password)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
