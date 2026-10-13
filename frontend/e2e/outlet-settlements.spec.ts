import { expect, test, type Locator, type Page } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";
import { api } from "./settlement-setup";

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

interface Account {
  code: string | null;
  name: string;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

async function balances(page: Page) {
  const accounts = await api<Account[]>(page.request, "GET", "/settlements/accounts?include_inactive=true");
  return Object.fromEntries(accounts.filter((a) => a.code).map((a) => [a.code!, cents(a.balance)]));
}

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

async function dialogAction(page: Page, button: Locator, fill: (dialog: Locator) => Promise<void>, submit: string) {
  await button.click();
  const dialog = page.getByRole("dialog");
  await fill(dialog);
  await dialog.getByRole("button", { name: submit, exact: true }).click();
  await expect(dialog).toBeHidden();
}

test("boca settlement: open without expected, zero close, reopen, cash and financial close", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "full flow runs on desktop");
  const stamp = String(Date.now()).slice(-8);
  const code = `B-${stamp}`;
  const name = `Boca E2E ${stamp}`;

  // 1. Admin: a boca of the platform.
  await login(page, "/bocas", adminEmail, adminPassword);
  const outlet = await api<{ id: number }>(page.request, "POST", "/outlets", { code, name });
  const before = await balances(page);

  // 2. Operator: a new boca settlement without the Store Sys amount.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/boca");
  await expect(page.getByRole("heading", { name: "Rendiciones de boca" })).toBeVisible();
  await page.getByRole("button", { name: "Nueva rendición" }).click();
  const create = page.getByRole("dialog");
  await create.getByLabel("Fecha").fill(businessDay(-1));
  await create.getByLabel("Boca").selectOption({ label: `${name} (${code})` });
  await create.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/boca\/\d+$/);
  const settlementId = Number(page.url().split("/").pop());
  await expect(page.getByRole("heading", { name: `Rendición n.º 1 · ${name}` })).toBeVisible();
  await expect(page.getByTestId("expected-missing")).toHaveText("Sin cargar");
  await expect(page.getByRole("tabpanel").getByText("Pendientes", { exact: true })).toHaveCount(0);

  // 3. Without the expected amount it does not close.
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const blocked = page.getByRole("dialog");
  await expect(blocked).toContainText("Cargá el importe esperado de Store Sys para cerrar la rendición.");
  await expect(blocked.getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
  await blocked.getByRole("button", { name: "Cancelar" }).click();

  // 4. Store Sys says $0 and nothing was loaded: administrative zero close, nothing moves in Cajas.
  await dialogAction(
    page,
    page.getByRole("button", { name: "Corregir datos" }),
    (dialog) => dialog.getByLabel(/Importe esperado de Store Sys/).fill("0"),
    "Guardar",
  );
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const zero = page.getByRole("dialog");
  await expect(zero.getByTestId("administrative-zero")).toBeVisible();
  await zero.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(zero).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
  await expect(page.getByRole("link", { name: "Ver el cierre en Cajas" })).toHaveCount(0);
  expect(await balances(page)).toEqual(before);

  // 5. The boca's page counts no open settlement now.
  await page.goto(`/bocas/${outlet.id}`);
  await expect(page.getByTestId("outlet-open-settlements")).toHaveText(/Sin rendiciones abiertas\./);

  // 6. Admin reopens it: the zero close stays in effect, nothing to reverse.
  await switchUser(page, adminEmail, adminPassword, `/rendiciones/boca/${settlementId}`);
  await dialogAction(
    page,
    page.getByRole("button", { name: "Reabrir para corregir" }),
    (dialog) => dialog.getByLabel("Motivo").fill("Faltaba la venta de la tarde"),
    "Reabrir",
  );
  await expect(page.getByTestId("financial-state")).toHaveText("En corrección");
  await expect(page.getByText(/Su cierre en cero sigue vigente/)).toBeVisible();

  // 7. Operator loads the real amount and the cash: a normal financial close.
  await switchUser(page, operatorEmail, operatorPassword, `/rendiciones/boca/${settlementId}`);
  await dialogAction(
    page,
    page.getByRole("button", { name: "Corregir datos" }),
    (dialog) => dialog.getByLabel(/Importe esperado de Store Sys/).fill("20.000"),
    "Guardar",
  );
  await dialogAction(
    page,
    page.getByRole("button", { name: "Cargar conteo" }),
    (dialog) => dialog.getByLabel("Cantidad de billetes de $ 20.000,00").fill("1"),
    "Guardar conteo",
  );
  await expect(page.getByTestId("board-difference")).toHaveText("Balanceada");
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const financial = page.getByRole("dialog");
  await expect(financial.getByTestId("balanced")).toBeVisible();
  await financial.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(financial).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
  await expect(page.getByRole("link", { name: "Ver el cierre en Cajas" }).first()).toBeVisible();
  const after = await balances(page);
  expect(after.main_cash - before.main_cash).toBe(2_000_000);
  expect(after.outlet_collections - before.outlet_collections).toBe(-2_000_000);

  // 8. History: the zero close, the reopening and the financial close, in order.
  await page.getByRole("tab", { name: "Historial" }).click();
  const history = page.getByRole("list", { name: "Historial de la rendición" });
  await expect(history).toContainText("Cierre administrativo cero");
  await expect(history).toContainText("Reapertura (versión 1 → 2)");
  await expect(history).toContainText("Nuevo cierre (versión 2)");
});
