import { execFileSync } from "node:child_process";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, driver, openSettlement } from "./settlement-setup";

/**
 * R8-2 end to end (Enzo's point 16): an employee with an earlier shortage → consumption → advance → adjustment →
 * balance → one movement reversed → balance → without the area, the advance reads only as a reserved movement
 * of the cash box. A fresh platform admin, created with the CLI, holds the area only for this test.
 */

const AREA = "settlements.employee_finance";
const PASSWORD = "root-password-123";

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "-m", "app.cli", "create-admin", "--email", email, "--name", "Root"],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

/** A delivery settlement closed $2.000 short, charged to the driver; its $8.000 enter the main cash box. */
async function shortage(request: APIRequestContext, driverId: number) {
  const id = await openSettlement(request, driverId, "10000");
  const accounts = await api<{ id: number; code: string | null }[]>(request, "GET", "/settlements/accounts");
  const box = accounts.find((a) => a.code === "main_cash")!.id;
  await api(request, "PUT", `/settlements/deliveries/${id}/count`, {
    lines: [{ denomination_code: "ARS_1000", quantity: 8, destination_account_id: box }],
  });
  const preview = await api<{ preview_token: string }>(request, "GET", `/settlements/deliveries/${id}/closing-preview`);
  await api(request, "POST", `/settlements/deliveries/${id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [{ target: "EMPLOYEE", employee_id: driverId, amount: "2000" }],
    confirm: true,
  });
}

async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test("consumption, advance and adjustment move the internal account; a reversal undoes one", async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const email = `e2e-r82-${suffix}@districom.local`;
  const name = `Empleado R8-2 ${suffix}`;
  createPlatformAdmin(email);
  await login(page, "/rendiciones", email, PASSWORD);
  await api(page.request, "PUT", "/auth/access-grants", { email, key: AREA, role: "admin" });
  const driverId = await driver(page.request, name);
  await shortage(page.request, driverId);

  await page.goto(`/rendiciones/empleados/${driverId}`);
  const balance = page.getByTestId("internal-balance");
  await expect(balance).toHaveText("$ 2.000,00");

  // Consumption: amount only, confirmed against the backend's preview.
  await page.getByRole("button", { name: "Registrar consumo" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Importe", { exact: true }).fill("3.000");
  await dialog.getByLabel("Descripción").fill("Gaseosas");
  await dialog.getByRole("button", { name: "Revisar" }).click();
  await expect(dialog.getByTestId("movement-confirmation")).toContainText(
    "El empleado deberá $ 3.000,00 más a la empresa.",
  );
  await expect(dialog.getByTestId("movement-legs")).toContainText("Consumos de empleados");
  await noSideScroll(page);
  await dialog.getByRole("button", { name: "Confirmar y registrar" }).click();
  await expect(dialog).toBeHidden();
  await expect(balance).toHaveText("$ 5.000,00");

  // Advance from the main cash box: the legs before confirming.
  await page.getByRole("button", { name: "Entregar adelanto" }).click();
  dialog = page.getByRole("dialog");
  const from = dialog.getByLabel("Se entrega desde");
  await expect(from.locator("option", { hasText: "Caja principal" })).toHaveCount(1);
  await from.selectOption({ label: (await from.locator("option", { hasText: "Caja principal" }).textContent())! });
  await dialog.getByLabel("Importe", { exact: true }).fill("5.000");
  await dialog.getByLabel("Descripción").fill("Adelanto quincena");
  await dialog.getByRole("button", { name: "Revisar" }).click();
  const legs = dialog.getByTestId("movement-legs");
  await expect(legs).toContainText("Caja principal");
  await expect(legs).toContainText("-$ 5.000,00");
  await expect(legs).toContainText(`Cuenta interna · ${name}`);
  await expect(legs).toContainText("+$ 5.000,00");
  await dialog.getByRole("button", { name: "Confirmar y registrar" }).click();
  await expect(dialog).toBeHidden();
  await expect(balance).toHaveText("$ 10.000,00");

  // Adjustment in the employee's favour: exceptional, its effect in words.
  await page.getByRole("button", { name: "Registrar ajuste" }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("excepcional y correctivo");
  await dialog.getByRole("radio", { name: /A favor del empleado/ }).check();
  await dialog.getByLabel("Importe", { exact: true }).fill("1.500");
  await dialog.getByLabel("Motivo").fill("Reconocimiento por error de precio");
  await expect(dialog.getByTestId("adjustment-effect")).toHaveText(
    "La empresa reconocerá $ 1.500,00 a favor del empleado.",
  );
  await dialog.getByRole("button", { name: "Revisar" }).click();
  await expect(dialog.getByTestId("balance-after")).toContainText("$ 8.500,00");
  await dialog.getByRole("button", { name: "Confirmar y registrar" }).click();
  await expect(dialog).toBeHidden();
  await expect(balance).toHaveText("$ 8.500,00");

  const rows = page.locator('[data-testid^="account-movement-"]');
  await expect(rows).toHaveCount(4);
  await expect(page.getByText("Ajuste a favor", { exact: true })).toBeVisible();
  await noSideScroll(page);

  // The consumption is reversed whole, on its date, with a reason.
  await rows.filter({ hasText: "Gaseosas" }).getByRole("link").click();
  await expect(page.getByRole("heading", { name: /^Consumo n\.º \d+$/ })).toBeVisible();
  await page.getByRole("button", { name: "Revertir" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Motivo del reverso").fill("Cargado por error");
  await dialog.getByRole("button", { name: "Confirmar reverso" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("movement-status")).toHaveText("Revertido");
  await noSideScroll(page);
  await page.getByRole("link", { name }).click();
  await expect(balance).toHaveText("$ 5.500,00");
  await expect(rows).toHaveCount(5);
  await expect(page.getByText("Reverso de consumo")).toBeVisible();

  // Without the area the advance is only a reserved movement of the cash box: date, amount, box, direction.
  const advanceHref = await rows.filter({ hasText: "Adelanto quincena" }).getByRole("link").getAttribute("href");
  const advance = await api<{ operation_id: number }>(
    page.request,
    "GET",
    `/settlements/employee-finance/employee-movements/${advanceHref!.split("/").pop()}`,
  );
  await api(page.request, "POST", "/auth/access-grants/revoke", { email, key: AREA });
  await page.goto(`/rendiciones/cajas/operaciones/${advance.operation_id}`);
  await expect(page.getByText("Movimiento de empleado (reservado)").first()).toBeVisible();
  await expect(page.getByText("-$ 5.000,00").first()).toBeVisible();
  await expect(page.getByText(name)).toHaveCount(0);
  await expect(page.getByText("Adelanto quincena")).toHaveCount(0);
  await noSideScroll(page);
});
