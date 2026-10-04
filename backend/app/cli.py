"""Administrative commands: `uv run python -m app.cli create-admin --email ... --name ...`.

Module access (ARCHITECTURE.md §8): `grant-module-role --email ... --module settlements --role operator`,
`revoke-module-role --email ... --module settlements`.
"""

import argparse
import getpass
import logging
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_db
from app.platform.auth.models import MODULE_ROLES
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role
from app.platform.auth.service import create_user

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


def _installed_module_keys() -> list[str]:
    import app.api.v1.router  # noqa: F401  (composition root: installs the modules)
    from app.platform.modules import installed_modules

    return [module.key for module in installed_modules()]


def _grant(args: argparse.Namespace) -> int:
    db = next(get_db())
    try:
        grant = grant_module_role(db, args.email, args.module, args.role)
    except ModuleRoleError as exc:
        logger.error("grant_module_role_failed", extra={"reason": str(exc)})
        return 1
    logger.info("module_role_granted", extra={"user_id": grant.user_id, "module_key": args.module, "role": grant.role})
    return 0


def _revoke(args: argparse.Namespace) -> int:
    db = next(get_db())
    try:
        revoked = revoke_module_role(db, args.email, args.module)
    except ModuleRoleError as exc:
        logger.error("revoke_module_role_failed", extra={"reason": str(exc)})
        return 1
    logger.info("module_role_revoked" if revoked else "module_role_absent", extra={"module_key": args.module})
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
    grant = sub.add_parser("grant-module-role", help="Dar o cambiar el rol de un usuario en un módulo")
    grant.add_argument("--email", required=True)
    grant.add_argument("--module", required=True, choices=modules)
    grant.add_argument("--role", required=True, choices=MODULE_ROLES)
    grant.set_defaults(func=_grant)
    revoke = sub.add_parser("revoke-module-role", help="Quitar el acceso de un usuario a un módulo")
    revoke.add_argument("--email", required=True)
    revoke.add_argument("--module", required=True, choices=modules)
    revoke.set_defaults(func=_revoke)
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
