import { execFileSync } from "node:child_process";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, day, driver, openSettlement } from "./settlement-setup";

/**
 * R9-1, the Store Sys → DistriCom contract (ADR 011): a delivery opens without its expected amount, Store Sys fills
 * it through the import endpoint (here called by API, as the adapter would) and the settlement closes; an update that
 * differs from a manual amount waits for someone to accept it on screen. A fresh platform admin, created with the
 * CLI, holds the protected area `settlements.store_sys_import` only for these tests.
 */

const AREA = "settlements.store_sys_import";
const PASSWORD = "root-password-123";

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "-m", "app.cli", "create-admin", "--email", email, "--name", "Root"],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

async function setUp(page: Page, tag: string) {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const email = `e2e-r9-${tag}-${suffix}@districom.local`;
  createPlatformAdmin(email);
  await login(page, "/rendiciones", email, PASSWORD);
  await api(page.request, "PUT", "/auth/access-grants", { email, key: AREA, role: "admin" });
  const name = `Repartidor R9 ${tag} ${suffix}`;
  return { name, code: `R9-${suffix}`, driverId: await driver(page.request, name) };
}

async function importRecord(request: APIRequestContext, record: Record<string, unknown>) {
  const result = await api<{ results: { outcome: string | null; code: string | null }[] }>(
    request,
    "POST",
    "/settlements/imports/store-sys/expected-amounts",
    {
      source: "STORE_SYS",
      client_request_id: crypto.randomUUID(),
      records: [{ settlement_type: "DELIVERY", business_date: day(-1), revision: 1, ...record }],
    },
  );
  return result.results[0];
}

async function count(request: APIRequestContext, settlementId: number, bills: number) {
  const accounts = await api<{ id: number; code: string | null }[]>(request, "GET", "/settlements/accounts");
  const box = accounts.find((a) => a.code === "main_cash")!.id;
  await api(request, "PUT", `/settlements/deliveries/${settlementId}/count`, {
    lines: [{ denomination_code: "ARS_10000", quantity: bills, destination_account_id: box }],
  });
}

async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test("a delivery opens without its expected amount, Store Sys fills it and it closes", async ({ page }) => {
  const { name, code } = await setUp(page, "open");

  // 1. The employee's Store Sys code, loaded on screen by the area admin.
  await page.goto("/rendiciones/integracion-store-sys");
  await expect(page.getByRole("heading", { name: "Integración Store Sys" })).toBeVisible();
  await page.getByLabel("Empleado").fill(name);
  const row = page.getByRole("row").filter({ hasText: name });
  await expect(row).toContainText("Sin código");
  await row.getByRole("button", { name: "Cargar código" }).click();
  const codeDialog = page.getByRole("dialog");
  await codeDialog.getByLabel("Código").fill(code);
  await codeDialog.getByRole("button", { name: "Guardar" }).click();
  await expect(codeDialog).toBeHidden();
  await expect(row).toContainText(code);
  await noSideScroll(page);

  // 2. The settlement opens without an expected amount: «Sin cargar», no difference, and it cannot close.
  await page.goto("/rendiciones/repartos");
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Fecha").fill(day(-1));
  await create.getByLabel("Repartidor").selectOption({ label: name });
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);
  const settlementId = Number(page.url().split("/").pop());
  await expect(page.getByTestId("expected-missing")).toHaveText("Sin cargar");
  await expect(page.getByTestId("board-difference")).toHaveText("—");
  await count(page.request, settlementId, 10);
  await page.reload();
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const blocked = page.getByRole("dialog");
  await expect(blocked.getByText("Cargá el importe esperado de Store Sys para cerrar la rendición.")).toBeVisible();
  await expect(blocked.getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
  await blocked.getByRole("button", { name: "Cancelar" }).click();

  // 3. Store Sys reports it: applied, with its origin on the board and in the history.
  const reference = `LIQ-${settlementId}`;
  expect(
    await importRecord(page.request, {
      external_reference: reference,
      employee_store_code: code,
      expected_amount: "100000.00",
    }),
  ).toMatchObject({ outcome: "APPLIED" });
  // The same record again is the same event.
  expect(
    await importRecord(page.request, {
      external_reference: reference,
      employee_store_code: code,
      expected_amount: "100000.00",
    }),
  ).toMatchObject({ outcome: "APPLIED" });
  await page.reload();
  await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${reference} rev. 1`);
  await expect(page.getByTestId("board-difference")).toHaveText("Balanceada");
  await noSideScroll(page);

  // 4. It closes, and the close keeps the expected amount it was closed with.
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const close = page.getByRole("dialog");
  await close.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(close).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

  // 5. A later revision of a closed settlement is stored but changes nothing.
  expect(
    await importRecord(page.request, {
      external_reference: reference,
      revision: 2,
      employee_store_code: code,
      expected_amount: "90000.00",
    }),
  ).toMatchObject({ outcome: "REJECTED_CLOSED" });
  await page.reload();
  await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${reference} rev. 1`);
});

test("a Store Sys amount different from the manual one waits until it is accepted on screen", async ({ page }) => {
  const { code, driverId } = await setUp(page, "pending");
  await api(page.request, "PUT", `/settlements/store-sys/employee-codes/${driverId}`, { store_code: code });
  const settlementId = await openSettlement(page.request, driverId, "80000");
  const reference = `LIQ-P-${settlementId}`;
  expect(
    await importRecord(page.request, {
      external_reference: reference,
      employee_store_code: code,
      expected_amount: "100000.00",
    }),
  ).toMatchObject({ outcome: "PENDING_CONFIRMATION" });

  await page.goto(`/rendiciones/repartos/${settlementId}`);
  const banner = page.getByTestId("expected-pending");
  await expect(banner).toContainText("Store Sys: $ 100.000,00");
  await expect(page.getByTestId("expected-origin")).toHaveText("Origen: Manual");
  await noSideScroll(page);
  await banner.getByRole("button", { name: "Aceptar el de Store Sys" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Aceptar", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(banner).toBeHidden();
  await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${reference} rev. 1`);

  await page.getByRole("tab", { name: "Historial" }).click();
  const history = page.getByTestId("expected-history");
  await expect(history).toContainText("Carga manual");
  await expect(history).toContainText("Aceptado");
});
