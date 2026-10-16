"""Shared helpers of the R8-1 tests (Rendiciones > Empleados): users with and without the protected area."""

from typing import Any

AREA = "settlements.employee_finance"
BASE = "/api/v1/settlements"
EF = f"{BASE}/employee-finance"


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")
