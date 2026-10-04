import { expect, test, type Page } from "@playwright/test";

import { formatMoney } from "../src/lib/format";
import { adminEmail, adminPassword, login } from "./helpers";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";
const cashViewerEmail = process.env.E2E_CASH_VIEWER_EMAIL ?? "e2e-cash-viewer@districom.local";
const cashViewerPassword = process.env.E2E_CASH_VIEWER_PASSWORD ?? "cash-viewer-password-123";

const HEADERS = { "X-Requested-With": "e2e" };

interface Account {
  id: number;
  code: string | null;
  name: string;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);
const money = (value: number) => formatMoney((value / 100).toFixed(2));

async function accounts(page: Page): Promise<Account[]> {
  const response = await page.request.get("/api/v1/settlements/accounts", { headers: HEADERS });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as Account[];
}

async function switchUser(page: Page, email: string, password: string) {
  await page.context().clearCookies();
  await login(page, "/rendiciones/cajas", email, password);
  await expect(page.getByText("Cajas y banco")).toBeVisible();
}

async function submitOperation(
  page: Page,
  button: string,
  fill: (dialog: ReturnType<Page["getByRole"]>) => Promise<void>,
  submit: string,
) {
  await page.getByRole("button", { name: button, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await fill(dialog);
  await dialog.getByRole("button", { name: submit }).click();
  await expect(dialog).toBeHidden();
  const notice = page.getByRole("status");
  await expect(notice).toContainText("Operación registrada");
  const href = await notice.getByRole("link", { name: "Ver detalle" }).getAttribute("href");
  return Number(href?.split("/").pop());
}

test("cash boxes end to end: opening, transfer, deposit, contribution, adjustment and reversal", async ({
  page,
  isMobile,
}) => {
  // The ledger is shared state: the full flow runs once (desktop); mobile checks the read-only layout below.
  test.skip(isMobile, "full flow runs on desktop");

  await login(page, "/rendiciones/cajas", adminEmail, adminPassword);
  const before = Object.fromEntries((await accounts(page)).filter((a) => a.code).map((a) => [a.code, a]));
  const main = before.main_cash;
  const change = before.change_cash;
  const bank = before.bank;
  const run = Date.now();
  const partnerName = `Socio E2E ${run}`;
  const transferText = `Cambio para repartos E2E ${run}`;

  // 1. Admin: partner account and opening balance.
  await page.getByRole("button", { name: "Crear cuenta de aporte" }).click();
  await page.getByRole("dialog").getByLabel("Nombre del socio").fill(partnerName);
  await page.getByRole("dialog").getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByRole("status")).toContainText(`Cuenta de aporte «${partnerName}» creada.`);
  const partner = (await accounts(page)).find((a) => a.name === partnerName)!;

  await submitOperation(
    page,
    "Cargar saldo inicial",
    async (dialog) => {
      await dialog.getByLabel("Cuenta").selectOption(String(main.id));
      await dialog.getByLabel("Saldo existente").fill("100.000");
    },
    "Registrar saldo inicial",
  );
  await expect(page.getByTestId(`balance-${main.id}`)).toHaveText(money(cents(main.balance) + 10_000_000));

  // 2–3. Operator: transfer and deposit.
  await switchUser(page, operatorEmail, operatorPassword);
  await expect(page.getByLabel("Tu rol en Rendiciones: Operador")).toBeVisible();
  await expect(page.getByText("Operaciones administrativas")).toHaveCount(0);
  const transferId = await submitOperation(
    page,
    "Mover entre cajas",
    async (dialog) => {
      await dialog.getByLabel("Desde").selectOption(String(main.id));
      await dialog.getByLabel("Hacia").selectOption(String(change.id));
      await dialog.getByLabel("Importe").fill("30.000");
      await dialog.getByLabel("Descripción (opcional)").fill(transferText);
    },
    "Registrar traspaso",
  );
  await submitOperation(
    page,
    "Depositar en banco",
    async (dialog) => {
      await dialog.getByLabel("Desde la caja").selectOption(String(main.id));
      await dialog.getByLabel("Hacia el banco").selectOption(String(bank.id));
      await dialog.getByLabel("Importe").fill("50000");
    },
    "Registrar depósito",
  );

  // 4–6. Admin: contribution, adjustment and reversal of the transfer.
  await switchUser(page, adminEmail, adminPassword);
  await submitOperation(
    page,
    "Registrar aporte de socio",
    async (dialog) => {
      await dialog.getByLabel("Socio").selectOption(String(partner.id));
      await dialog.getByLabel("Ingresa en").selectOption(String(bank.id));
      await dialog.getByLabel("Importe").fill("20.000");
    },
    "Registrar aporte",
  );
  await submitOperation(
    page,
    "Ajuste administrativo",
    async (dialog) => {
      await dialog.getByLabel("Cuenta").selectOption(String(main.id));
      await dialog.getByLabel("Faltante (resta del saldo)").check();
      await dialog.getByLabel("Importe").fill("1.000");
      await dialog.getByLabel("Motivo").fill("Faltante en arqueo E2E");
    },
    "Registrar ajuste",
  );

  await page.goto(`/rendiciones/cajas/operaciones/${transferId}`);
  await page.getByRole("button", { name: "Revertir operación" }).click();
  const dialog = page.getByRole("dialog", { name: "Revertir operación" });
  await expect(dialog).toContainText("El movimiento original no se borra.");
  await dialog.getByLabel("Motivo").fill("Traspaso cargado por error");
  await dialog.getByRole("button", { name: "Revertir operación" }).click();
  const done = page.getByRole("dialog", { name: "Operación revertida" });
  await expect(done).toContainText("Se registró el reverso");
  await done.getByRole("button", { name: "Cerrar" }).first().click();
  await expect(page.getByText("Esta operación fue revertida y ya no afecta los saldos.")).toBeVisible();

  // 7. Final balances, as the API returns them.
  await page.goto("/rendiciones/cajas");
  await expect(page.getByTestId(`balance-${main.id}`)).toHaveText(
    money(cents(main.balance) + 10_000_000 - 3_000_000 - 5_000_000 - 100_000 + 3_000_000),
  );
  await expect(page.getByTestId(`balance-${change.id}`)).toHaveText(money(cents(change.balance)));
  await expect(page.getByTestId(`balance-${bank.id}`)).toHaveText(money(cents(bank.balance) + 5_000_000 + 2_000_000));
  await expect(page.getByTestId(`balance-${partner.id}`)).toHaveText(money(2_000_000));
  await expect(page.getByRole("button", { name: "Ver movimientos de Saldo inicial" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ver movimientos de Ajustes" })).toHaveCount(0);

  // 8. History keeps the original and its reversal.
  await page.getByRole("button", { name: "Ver movimientos de Caja de cambio" }).click();
  const movements = page.getByRole("table");
  await expect(movements.getByRole("row").filter({ hasText: transferText })).toContainText("Revertida");
  await expect(movements.getByRole("row").filter({ hasText: `Revierte la n.º ${transferId}` })).toContainText(
    "Traspaso cargado por error",
  );

  // 9. Viewer cannot operate, neither in the screen nor through the API.
  await switchUser(page, cashViewerEmail, cashViewerPassword);
  await expect(page.getByTestId(`balance-${main.id}`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Mover entre cajas" })).toHaveCount(0);
  await expect(page.getByText("Operaciones administrativas")).toHaveCount(0);
  const forbidden = await page.request.post("/api/v1/settlements/operations/cash-transfer", {
    headers: HEADERS,
    data: {
      client_request_id: crypto.randomUUID(),
      business_date: new Date().toISOString().slice(0, 10),
      from_account_id: main.id,
      to_account_id: change.id,
      amount: "1.00",
    },
  });
  expect(forbidden.status()).toBe(403);
});

test("cash boxes are readable on a phone and forms fit the screen", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile layout check");
  await login(page, "/rendiciones/cajas", operatorEmail, operatorPassword);
  await expect(page.getByRole("button", { name: "Ver movimientos de Caja principal" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Ver movimientos de Saldo inicial" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ver movimientos de Ajustes" })).toHaveCount(0);
  await page.getByRole("button", { name: "Mover entre cajas" }).click();
  const dialog = page.getByRole("dialog", { name: "Mover entre cajas" });
  await expect(dialog.getByRole("button", { name: "Registrar traspaso" })).toBeInViewport();
  const width = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(width).toBeLessThanOrEqual(0);
  await dialog.getByRole("button", { name: "Cancelar" }).click();
  await expect(dialog).toBeHidden();
});
