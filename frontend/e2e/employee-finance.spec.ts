import { execFileSync } from "node:child_process";

import { expect, test, type APIRequestContext } from "@playwright/test";

import { login } from "./helpers";
import { HEADERS, api, driver, openSettlement } from "./settlement-setup";

/**
 * Rendiciones > Empleados (R8-1, ADR 010): a protected area the platform admin does not inherit. A fresh
 * platform admin (created with the CLI, so no other spec's grants interfere) does not see it, grants it to
 * itself without reading anything first, then reads the internal account and loads a salary term.
 */

const AREA = "settlements.employee_finance";
const PASSWORD = "root-password-123";

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    [
      "run",
      "--directory",
      "../backend",
      "python",
      "-m",
      "app.cli",
      "create-admin",
      "--email",
      email,
      "--name",
      "Root E2E",
    ],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

/** A delivery settlement closed $2.000 short, charged to the driver's internal account. */
async function shortage(request: APIRequestContext, driverId: number) {
  const id = await openSettlement(request, driverId, "10000");
  const accounts = await api<{ id: number; code: string | null }[]>(request, "GET", "/settlements/accounts");
  const box = accounts.find((a) => a.code === "main_cash")!.id;
  await api(request, "PUT", `/settlements/deliveries/${id}/count`, {
    lines: [{ denomination_code: "ARS_1000", quantity: 8, destination_account_id: box }],
  });
  const preview = await api<{ preview_token: string }>(request, "GET", `/settlements/deliveries/${id}/closing-preview`);
  const closed = await api<{ sequence_number: number }>(request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [{ target: "EMPLOYEE", employee_id: driverId, amount: "2000" }],
    confirm: true,
  });
  return { id, number: closed.sequence_number };
}

test("the area is granted explicitly, then shows the internal account and the salary term", async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const email = `e2e-root-${suffix}@districom.local`;
  const name = `Empleado R8 ${suffix}`;
  createPlatformAdmin(email);

  // Platform admin without the area: no menu item, no page, the API answers 404.
  await login(page, "/rendiciones", email, PASSWORD);
  await expect(page.getByRole("link", { name: "Cajas" })).toBeAttached();
  await expect(page.locator('a[href="/rendiciones/empleados"]')).toHaveCount(0);
  await page.goto("/rendiciones/empleados");
  await expect(page.getByText("Página no encontrada")).toBeVisible();
  const hidden = await page.request.get("/api/v1/settlements/employee-finance/employees", { headers: HEADERS });
  expect(hidden.status()).toBe(404);

  // It grants itself the area (as for anyone else) without having read it.
  await api(page.request, "PUT", "/auth/access-grants", { email, key: AREA, role: "admin" });
  const driverId = await driver(page.request, name);
  const settlement = await shortage(page.request, driverId);

  await page.goto("/rendiciones");
  await expect(page.locator('a[href="/rendiciones/empleados"]')).toHaveCount(1);
  await page.goto("/rendiciones/empleados");
  await expect(page.getByText("Sueldo base interno (mensual). No es el recibo de sueldo legal.")).toBeVisible();
  await page.getByRole("searchbox", { name: "Empleado" }).fill(name);
  const row = page.getByTestId(`employee-finance-${driverId}`);
  await expect(row).toContainText("$ 2.000,00");
  await expect(row).toContainText("Debe a la empresa");
  await expect(row).toContainText("Sin condición");

  await row.getByRole("link", { name }).click();
  await expect(page).toHaveURL(new RegExp(`/rendiciones/empleados/${driverId}$`));
  await expect(page.getByTestId("internal-balance")).toHaveText("$ 2.000,00");
  const movement = page.locator('[data-testid^="account-movement-"]');
  await expect(movement).toHaveCount(1);
  await expect(movement).toContainText("Faltante de rendición");
  await expect(movement.getByRole("link")).toHaveAttribute("href", `/rendiciones/repartos/${settlement.id}`);
  await expect(movement).toContainText(`Rendición n.º ${settlement.number}`);

  // Salary term: monthly internal base salary, born open.
  await page.getByRole("button", { name: "Cargar condición" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Sueldo base interno mensual").fill("850.000");
  await dialog.getByRole("button", { name: "Guardar condición" }).click();
  await expect(dialog).toBeHidden();
  const term = page.locator('[data-testid^="compensation-term-"]');
  await expect(term).toHaveCount(1);
  await expect(term).toContainText("$ 850.000,00");
  await expect(term).toContainText("Vigente");
  await expect(page.getByRole("button", { name: "Cargar condición" })).toBeHidden();

  // Phones never scroll sideways.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  // Revoked: gone again.
  await api(page.request, "POST", "/auth/access-grants/revoke", { email, key: AREA });
  await page.reload();
  await expect(page.getByText("Página no encontrada")).toBeVisible();
});
