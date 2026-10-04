import pytest

from app.core.errors import AppError
from app.platform.auth.access import accessible_modules, has_module_access, module_role, require_module_access
from app.platform.auth.models import User, UserModuleRole


@pytest.fixture(autouse=True)
def _installed_modules() -> None:
    import app.api.v1.router  # noqa: F401  (installs commercial and settlements)


def _user(role: str, **modules: str) -> User:
    grants = [UserModuleRole(module_key=key, role=module_role) for key, module_role in modules.items()]
    return User(email=f"{role}@districom.test", full_name="Test", role=role, module_roles=grants)


def test_platform_admin_is_admin_in_every_installed_module() -> None:
    admin = _user("admin")
    assert module_role(admin, "commercial") == "admin"
    assert module_role(admin, "settlements") == "admin"
    assert module_role(admin, "not_installed") is None


def test_other_users_get_the_granted_role_or_nothing() -> None:
    user = _user("viewer", settlements="operator")
    assert module_role(user, "settlements") == "operator"
    assert module_role(user, "commercial") is None
    assert [(module.key, role) for module, role in accessible_modules(user)] == [("settlements", "operator")]


def test_grants_for_uninstalled_modules_are_ignored() -> None:
    assert module_role(_user("viewer", legacy="admin"), "legacy") is None


@pytest.mark.parametrize(
    ("granted", "required", "allowed"),
    [
        ("viewer", "viewer", True),
        ("viewer", "operator", False),
        ("operator", "operator", True),
        ("operator", "admin", False),
        ("admin", "operator", True),
    ],
)
def test_roles_are_ordered(granted: str, required: str, allowed: bool) -> None:
    assert has_module_access(_user("viewer", settlements=granted), "settlements", required) is allowed


def test_dependency_rejects_insufficient_role() -> None:
    check = require_module_access("settlements", "operator")
    assert check(_user("viewer", settlements="operator")).role == "viewer"
    with pytest.raises(AppError) as exc_info:
        check(_user("viewer", settlements="viewer"))
    assert exc_info.value.status_code == 403


def test_unknown_min_role_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="Unknown module role"):
        require_module_access("commercial", "owner")
