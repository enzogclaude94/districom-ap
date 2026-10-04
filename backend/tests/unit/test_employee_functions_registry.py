import pytest
from fastapi import APIRouter

from app.platform.modules import ModuleSpec, install, installed_modules
from app.platform.parties.functions import EmployeeFunctionRegistry, EmployeeFunctionSpec, registry


def test_registry_is_idempotent_per_code() -> None:
    reg = EmployeeFunctionRegistry()
    reg.register(EmployeeFunctionSpec("demo.driver", "Chofer"))
    reg.register(EmployeeFunctionSpec("demo.driver", "Chofer"))
    assert [spec.code for spec in reg.all()] == ["demo.driver"]
    assert reg.get("demo.unknown") is None


def test_installed_modules_register_their_functions() -> None:
    import app.api.v1.router  # noqa: F401  (installs the modules)

    spec = registry.get("commercial.salesperson")
    assert spec is not None
    assert spec.label == "Preventista"


def test_function_codes_must_carry_the_module_prefix() -> None:
    module = ModuleSpec(
        key="demo",
        name="Demo",
        router=APIRouter(),
        employee_functions=(EmployeeFunctionSpec("other.driver", "Chofer"),),
    )
    with pytest.raises(ValueError, match=r"demo\. prefix"):
        install(module)
    assert "demo" not in {installed.key for installed in installed_modules()}
