import pytest

from app.core.errors import AppError
from app.platform.auth.access import has_module_access, require_module_access
from app.platform.auth.models import User


def _user(role: str) -> User:
    return User(email=f"{role}@districom.test", full_name="Test", role=role)


def test_stage_zero_derives_module_role_from_platform_role() -> None:
    assert has_module_access(_user("admin"), "commercial", "admin")
    assert has_module_access(_user("viewer"), "commercial", "viewer")
    assert not has_module_access(_user("viewer"), "commercial", "admin")


def test_dependency_rejects_insufficient_role() -> None:
    check = require_module_access("commercial", "admin")
    assert check(_user("admin")).role == "admin"
    with pytest.raises(AppError) as exc_info:
        check(_user("viewer"))
    assert exc_info.value.status_code == 403


def test_unknown_min_role_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="Unknown module role"):
        require_module_access("commercial", "owner")
