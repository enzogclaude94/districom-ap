import { expect, test, type APIRequestContext } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

const HEADERS = { "X-Requested-With": "e2e" };

interface Account {
  id: number;
  code: string | null;
  balance: string;
}

async function api<T>(request: APIRequestContext, method: "GET" | "POST", path: string, data?: unknown) {
  const response = await request.fetch(`/api/v1${path}`, { method, headers: HEADERS, data });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function mainCash(request: APIRequestContext) {
  const all = await api<Account[]>(request, "GET", "/settlements/accounts?include_inactive=true");
  return all.find((a) => a.code === "main_cash")!;
}

const today = () => businessDay();

test("an operator pays fuel from the main cash box after a confirmation; an admin reverses it", async ({
  page,
}, testInfo) => {
  const run = `${Date.now()}${testInfo.project.name === "mobile" ? "M" : "D"}`;
  const plate = `GD${run.slice(-7)}`;
  const employeeName = `Empleado gasto directo ${run}`;

  // Admin: money in the box, an active vehicle and an employee.
  await login(page, "/rendiciones/cajas", adminEmail, adminPassword);
  const main = await mainCash(page.request);
  await api(page.request, "POST", "/settlements/operations/opening-balance", {
    client_request_id: crypto.randomUUID(),
    business_date: today(),
    account_id: main.id,
    amount: "50000.00",
  });
  await api(page.request, "POST", "/vehicles", { plate, description: "Camión gasto directo E2E" });
  const employee = await api<{ id: number }>(page.request, "POST", "/employees", {
    full_name: employeeName,
    hire_date: null,
    phone: null,
  });
  await api(page.request, "POST", `/employees/${employee.id}/functions`, {
    function_code: "settlements.delivery_driver",
    valid_from: "2026-01-01",
  });
  const before = cents((await mainCash(page.request)).balance);

  // Operator: Registrar gasto from Cajas, reviewed before anything is debited.
  await page.context().clearCookies();
  await login(page, "/rendiciones/cajas", operatorEmail, operatorPassword);
  await page.getByRole("button", { name: "Registrar gasto" }).click();
  const form = page.getByRole("dialog", { name: "Registrar gasto" });
  await form.getByLabel("Cuenta pagadora").selectOption(String(main.id));
  await form.getByLabel("Categoría").selectOption({ label: "Combustible" });
  await expect(form.getByLabel("Importe")).toHaveCount(0);
  await form.getByLabel("Vehículo").selectOption({ label: `${plate} · Camión gasto directo E2E` });
  await expect(form.getByLabel("Quién cargó")).toHaveValue("");
  await form.getByLabel("Quién cargó").selectOption({ label: employeeName });
  await form.getByLabel("Litros").fill("40");
  await form.getByLabel("Precio por litro").fill("1.250");
  await expect(form.getByText("Total estimado $ 50.000,00")).toBeVisible();
  await form.getByRole("button", { name: "Revisar gasto" }).click();

  const confirm = page.getByRole("dialog", { name: "Confirmá el gasto" });
  const summary = confirm.getByTestId("expense-confirmation");
  await expect(summary).toContainText("Caja principal-$ 50.000,00");
  await expect(summary).toContainText("Gastos operativos+$ 50.000,00");
  await expect(summary).toContainText(`Empleado${employeeName}`);
  expect(cents((await mainCash(page.request)).balance)).toBe(before); // nothing debited yet
  await confirm.getByRole("button", { name: "Confirmar y registrar" }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "Gasto registrado" })).toContainText("$ 50.000,00");
  expect(cents((await mainCash(page.request)).balance)).toBe(before - 5_000_000);

  // The list of direct expenses shows it.
  await page.getByRole("link", { name: "Ver gastos directos" }).click();
  const row = page.getByRole("row", { name: new RegExp(plate) });
  await expect(row).toContainText("$ 50.000,00");
  await expect(row).toContainText("Vigente");
  await row.getByRole("link", { name: /Ver detalle/ }).click();
  await expect(page).toHaveURL(/\/rendiciones\/cajas\/gastos\/\d+$/);
  await expect(page.getByRole("button", { name: "Revertir gasto" })).toHaveCount(0); // operator
  const detail = page.url();

  // Admin: reverse with a reason; the money is back.
  await page.context().clearCookies();
  await login(page, new URL(detail).pathname, adminEmail, adminPassword);
  await page.getByRole("button", { name: "Revertir gasto" }).click();
  const reverse = page.getByRole("dialog", { name: "Revertir gasto" });
  await reverse.getByLabel("Motivo").fill("Cargado por error (E2E)");
  await reverse.getByRole("button", { name: "Revertir gasto" }).click();
  await expect(reverse).toBeHidden();
  await expect(page.getByTestId("expense-state")).toHaveText("Revertido");
  expect(cents((await mainCash(page.request)).balance)).toBe(before);
});
