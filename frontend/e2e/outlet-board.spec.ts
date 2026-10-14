import { expect, test, type Locator, type Page } from "@playwright/test";

import { adminEmail, adminPassword, businessDay, login } from "./helpers";
import { api } from "./settlement-setup";

/**
 * Rendiciones › Bocas (R7-4), end to end on desktop and on the phone: the normal day of a boca, a difference
 * corrected after the close, and two settlements of the same day that are never mixed.
 */

const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";

interface Account {
  id: number;
  code: string | null;
  kind: string;
  name: string;
  balance: string;
}

const cents = (amount: string) => Math.round(Number(amount) * 100);

/** Every account's balance in cents, by code (or by name for accounts without one). */
async function balances(page: Page) {
  const accounts = await api<Account[]>(page.request, "GET", "/settlements/accounts?include_inactive=true");
  return Object.fromEntries(accounts.map((a) => [a.code ?? a.name, cents(a.balance)]));
}

async function switchUser(page: Page, email: string, password: string, next: string) {
  await page.context().clearCookies();
  await login(page, next, email, password);
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function dialogAction(page: Page, button: Locator, fill: (dialog: Locator) => Promise<void>, submit: string) {
  await button.click();
  const dialog = page.getByRole("dialog");
  await fill(dialog);
  await dialog.getByRole("button", { name: submit, exact: true }).click();
  await expect(dialog).toBeHidden();
}

/** A boca with two responsables from a month ago (admin, by API: R7-1 covers their screens). */
async function bocaWithResponsables(page: Page, stamp: string, prefix: string) {
  const name = `Boca ${prefix} ${stamp}`;
  const outlet = await api<{ id: number }>(page.request, "POST", "/outlets", { code: `${prefix}-${stamp}`, name });
  const people: { id: number; name: string }[] = [];
  for (const who of ["A", "B"]) {
    const full = `Responsable ${who} ${prefix} ${stamp}`;
    const employee = await api<{ id: number }>(page.request, "POST", "/employees", {
      full_name: full,
      hire_date: null,
      phone: null,
    });
    await api(page.request, "POST", `/settlements/outlets/${outlet.id}/responsibilities`, {
      employee_id: employee.id,
      valid_from: businessDay(-30),
    });
    people.push({ id: employee.id, name: full });
  }
  return { id: outlet.id, name, code: `${prefix}-${stamp}`, a: people[0]!, b: people[1]! };
}

const board = (page: Page) => page.goto(`/rendiciones/boca?fecha=${businessDay(-1)}`);

async function openFromBoard(page: Page, name: string, expected: string) {
  await board(page);
  const card = page.getByRole("article", { name });
  await expect(card.getByTestId("board-state")).toHaveText("Sin rendición");
  await card.getByRole("button", { name: `Abrir rendición de ${name}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Fecha")).toHaveValue(businessDay(-1));
  await dialog.getByLabel(/Importe esperado/).fill(expected);
  await dialog.getByRole("button", { name: "Crear rendición" }).click();
  await expect(page).toHaveURL(/\/rendiciones\/boca\/\d+$/);
  return Number(page.url().split("/").pop());
}

async function remitFromPage(page: Page, amount: string, time: string, mobile: boolean) {
  await page.getByRole("button", { name: "Entregar efectivo" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Importe").fill(amount);
  await dialog.getByLabel("Hora de la entrega").fill(time);
  await dialog.getByRole("button", { name: "Revisar entrega" }).click();
  await expect(dialog.getByTestId("remittance-explanation")).toHaveText(
    "Este efectivo entra ahora a Caja principal y quedará imputado a esta rendición cuando se cierre.",
  );
  if (mobile) await noHorizontalScroll(page);
  await dialog.getByRole("button", { name: "Confirmar entrega" }).click();
  await expect(dialog).toBeHidden();
}

async function countCash(page: Page, twentyThousands: string, tenThousands = "") {
  await dialogAction(
    page,
    page.getByRole("button", { name: /Cargar conteo|Editar conteo/ }),
    async (dialog) => {
      await expect(dialog.getByTestId("count-dialog-help")).toContainText(
        "Contá solamente el efectivo que quedó físicamente en la Boca después de las entregas parciales.",
      );
      await dialog.getByLabel("Cantidad de billetes de $ 20.000,00").fill(twentyThousands);
      if (tenThousands) await dialog.getByLabel("Cantidad de billetes de $ 10.000,00").fill(tenThousands);
    },
    "Guardar conteo",
  );
}

async function verifiedTransfer(page: Page, amount: string, payer: string) {
  await dialogAction(
    page,
    page.getByRole("button", { name: "Declarar transferencia" }),
    async (dialog) => {
      await dialog.getByLabel("Importe").fill(amount);
      await dialog.getByLabel("Quién pagó (opcional)").fill(payer);
    },
    "Declarar",
  );
  const row = page.getByRole("row", { name: new RegExp(payer) });
  await dialogAction(page, row.getByRole("button", { name: "Verificar" }), async () => {}, "Verificar");
  await expect(row).toContainText("Verificada");
}

test("normal day of a boca: two deliveries, a transfer, the final count and a balanced close", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const stamp = `${String(Date.now()).slice(-6)}${mobile ? "M" : "D"}`;

  // 1-2. A boca with responsables A and B.
  await login(page, "/bocas", adminEmail, adminPassword);
  const boca = await bocaWithResponsables(page, stamp, "N");
  const before = await balances(page);

  // 3-4. The operator opens its settlement from the board, with the Store Sys amount of $1.000.000.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/boca");
  await expect(page.getByRole("heading", { name: "Bocas", exact: true })).toBeVisible();
  if (mobile) await noHorizontalScroll(page);
  const id = await openFromBoard(page, boca.name, "1.000.000");
  await expect(page.getByTestId("responsible-suggestions")).toContainText(boca.a.name);
  if (mobile) await noHorizontalScroll(page);

  // 5-6. Two partial deliveries to Caja principal.
  await remitFromPage(page, "400.000", "12:00", mobile);
  await remitFromPage(page, "300.000", "16:00", mobile);
  await expect(page.getByTestId("board-remitted")).toHaveText("$ 700.000,00");
  await expect(page.getByTestId("remittance")).toHaveCount(2);
  await expect(page.getByTestId("remittance").first()).toContainText("Entregada");

  // 7. A verified transfer of $100.000.
  await verifiedTransfer(page, "100.000", `Cliente N ${stamp}`);

  // 8. The final count: only what stayed in the boca ($200.000), next to what was already delivered.
  await expect(page.getByTestId("count-remitted")).toHaveText("$ 700.000,00");
  await countCash(page, "10");
  await expect(page.getByTestId("board-remaining")).toHaveText("$ 0,00");
  if (mobile) await noHorizontalScroll(page);

  // 9. The board: nothing remains, and it is not called a difference while open.
  await board(page);
  const card = page.getByRole("article", { name: boca.name });
  await expect(card.getByTestId("board-state")).toHaveText("Abierta");
  await expect(card.getByText("Resta rendir o justificar")).toBeVisible();
  await expect(card.getByTestId("board-card-remaining")).toHaveText("$ 0,00");
  await expect(card).not.toContainText("Diferencia");
  await expect(card).not.toContainText("Faltante");
  if (mobile) await noHorizontalScroll(page);

  // 10-12. Preview line by line, difference 0, close.
  await card.getByRole("link", { name: `Ver Rendición n.º 1 de ${boca.name}` }).click();
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const closing = page.getByRole("dialog");
  const amounts = closing.getByLabel("Importes del cierre");
  await expect(amounts).toContainText("Entregas parciales$ 700.000,00");
  await expect(amounts).toContainText("Efectivo final$ 200.000,00");
  await expect(amounts).toContainText("Transferencias verificadas$ 100.000,00");
  await expect(amounts).toContainText("Total justificado$ 1.000.000,00");
  await expect(closing.getByTestId("preview-difference")).toHaveText("Balanceada");
  await expect(closing.getByTestId("balanced")).toBeVisible();
  if (mobile) await noHorizontalScroll(page);
  await closing.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(closing).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

  // 13-16. The ledger: Caja +900.000 (the deliveries and the final count), Banco +100.000, the boca's
  // «Entregas a cuenta» back to 0, «Cobranzas de boca» −1.000.000.
  const after = await balances(page);
  expect(after.main_cash! - before.main_cash!).toBe(90_000_000);
  expect(after.bank! - before.bank!).toBe(10_000_000);
  expect(after[`Entregas a cuenta — ${boca.name}`]).toBe(0);
  expect(after.outlet_collections! - before.outlet_collections!).toBe(-100_000_000);

  // 17. History and what the close consolidated.
  const history = page.getByRole("list", { name: "Historial de la rendición" });
  await expect(history).toContainText("Rendición abierta");
  await expect(history.getByText(/Entrega parcial n\.º \d+ registrada/)).toHaveCount(2);
  await history.getByText("Entregas consolidadas en este cierre (2)").click();
  await expect(history.getByTestId("consolidated-remittances")).toContainText("$ 400.000,00");
  await expect(history.getByTestId("consolidated-remittances")).toContainText("$ 300.000,00");
  if (mobile) await noHorizontalScroll(page);
  const recorded = await api<{ closings: { in_effect: boolean; remittances: { amount: string }[] }[] }>(
    page.request,
    "GET",
    `/settlements/outlet-settlements/${id}/history`,
  );
  expect(recorded.closings).toHaveLength(1);
  expect(recorded.closings[0]!.remittances.map((r) => r.amount).sort()).toEqual(["300000.00", "400000.00"]);

  await board(page);
  await expect(page.getByRole("article", { name: boca.name }).getByTestId("board-card-difference")).toHaveText(
    "Balanceada",
  );
});

test("a difference allocated to two responsables, then corrected after the close", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const stamp = `${String(Date.now()).slice(-6)}${mobile ? "M" : "D"}`;

  await login(page, "/bocas", adminEmail, adminPassword);
  const boca = await bocaWithResponsables(page, stamp, "D");
  const category = `Limpieza Boca ${stamp}`;
  await api(page.request, "POST", "/settlements/expense-categories", { name: category });
  const before = await balances(page);
  const internal = (person: { id: number; name: string }) => `Cuenta interna de ${person.name} (#${person.id})`;

  // Expected 1.000.000; delivery 600.000; final cash 250.000; transfer 100.000; expense 20.000.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/boca");
  const id = await openFromBoard(page, boca.name, "1.000.000");
  await remitFromPage(page, "600.000", "13:00", mobile);
  await countCash(page, "12", "1");
  await verifiedTransfer(page, "100.000", `Cliente D ${stamp}`);
  await dialogAction(
    page,
    page.getByRole("button", { name: "Registrar gasto" }),
    async (dialog) => {
      await dialog.getByLabel("Categoría").selectOption({ label: category });
      await dialog.getByLabel("Importe").fill("20.000");
    },
    "Registrar gasto",
  );
  await expect(page.getByTestId("board-remaining")).toHaveText("$ 30.000,00");

  // The preview shows a shortage of 30.000; A takes 20.000 and B 10.000 (suggested, never preloaded).
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const closing = page.getByRole("dialog");
  await expect(closing.getByTestId("preview-difference")).toHaveText("Faltante $ 30.000,00");
  await expect(closing.getByTestId("close-responsible-suggestions")).toContainText(
    `Responsables de la Boca en esta fecha (sugerencia): ${boca.a.name}, ${boca.b.name}`,
  );
  await expect(closing.getByLabel("Parte 1: quién")).toHaveValue("");
  await closing.getByLabel("Parte 1: quién").selectOption({ label: boca.a.name });
  await closing.getByLabel("Importe de la parte 1").fill("20.000");
  await closing.getByRole("button", { name: "Agregar otra parte" }).click();
  await closing.getByLabel("Parte 2: quién").selectOption({ label: boca.b.name });
  await closing.getByLabel("Importe de la parte 2").fill("10.000");
  await expect(closing.getByTestId("remaining")).toHaveText("$ 0,00");
  if (mobile) await noHorizontalScroll(page);
  await closing.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(closing).toBeHidden();
  await expect(page.getByTestId("close-section-difference")).toHaveText("Faltante $ 30.000,00");
  const closed = await balances(page);
  expect(closed[internal(boca.a)]).toBe(2_000_000);
  expect(closed[internal(boca.b)]).toBe(1_000_000);

  // 1-2. Admin reopens: the previous close stays in effect until a new one.
  await switchUser(page, adminEmail, adminPassword, `/rendiciones/boca/${id}`);
  await dialogAction(
    page,
    page.getByRole("button", { name: "Reabrir para corregir" }),
    (dialog) => dialog.getByLabel("Motivo").fill("La entrega fue de $630.000"),
    "Reabrir",
  );
  await expect(page.getByTestId("being-corrected-banner")).toHaveText(
    "Esta rendición está en corrección. El cierre anterior continúa siendo el vigente hasta que confirmes un nuevo cierre.",
  );

  // 3. The delivery was really $630.000: reverse the wrong one and record the right one.
  const wrong = page.getByTestId("remittance").first();
  await wrong.getByRole("button", { name: "Revertir" }).click();
  const reversal = page.getByRole("dialog");
  await reversal.getByLabel("Motivo").fill("Eran $630.000");
  await reversal.getByRole("button", { name: "Revertir", exact: true }).click();
  await expect(reversal).toBeHidden();
  await expect(wrong).toContainText("Revertida");
  await expect(wrong).toContainText("Sigue en el cierre vigente hasta el nuevo cierre");
  await remitFromPage(page, "630.000", "13:05", mobile);
  await expect(page.getByTestId("remittance").nth(1)).toContainText("Se incluye en el nuevo cierre");
  await expect(page.getByTestId("remitted-in-effect-close")).toHaveText("En el cierre vigente: $ 600.000,00");
  await expect(page.getByTestId("board-remitted")).toHaveText("$ 630.000,00");
  if (mobile) await noHorizontalScroll(page);

  // 4-5. New preview, balanced now; new close.
  await page.getByRole("button", { name: "Cerrar rendición" }).click();
  const again = page.getByRole("dialog");
  await expect(again.getByTestId("preview-difference")).toHaveText("Balanceada");
  await expect(again).toContainText("el cierre anterior se revierte");
  await again.getByRole("button", { name: "Confirmar cierre" }).click();
  await expect(again).toBeHidden();
  await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

  // 6-7. The previous close is reversed and the right deliveries are the consolidated ones.
  const history = page.getByRole("list", { name: "Historial de la rendición" });
  await expect(history).toContainText("Reverso del cierre de la versión 1");
  await expect(history).toContainText("Nuevo cierre (versión 2)");
  await expect(history.getByText(/registrada durante la corrección/)).toHaveCount(1);
  await expect(history.getByText(/Entrega parcial n\.º \d+ revertida/)).toHaveCount(1);
  if (mobile) await noHorizontalScroll(page);
  const recorded = await api<{
    closings: { settlement_version: number; in_effect: boolean; remittances: { amount: string }[] }[];
  }>(page.request, "GET", `/settlements/outlet-settlements/${id}/history`);
  expect(recorded.closings.map((c) => [c.settlement_version, c.in_effect, c.remittances.map((r) => r.amount)])).toEqual(
    [
      [1, false, ["600000.00"]],
      [2, true, ["630000.00"]],
    ],
  );

  // 8. The ledger: Caja +880.000, Banco +100.000, gasto 20.000, «Cobranzas de boca» −1.000.000, the boca's
  // «Entregas a cuenta» at 0 and no debt left for A and B.
  const after = await balances(page);
  expect(after.main_cash! - before.main_cash!).toBe(88_000_000);
  expect(after.bank! - before.bank!).toBe(10_000_000);
  expect(after.expense! - before.expense!).toBe(2_000_000);
  expect(after.outlet_collections! - before.outlet_collections!).toBe(-100_000_000);
  expect(after[`Entregas a cuenta — ${boca.name}`]).toBe(0);
  expect(after[internal(boca.a)]).toBe(0);
  expect(after[internal(boca.b)]).toBe(0);
});

test("two settlements of the same boca and day are shown apart", async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const stamp = `${String(Date.now()).slice(-6)}${mobile ? "M" : "D"}`;
  const day = businessDay(-1);

  await login(page, "/bocas", adminEmail, adminPassword);
  const boca = await bocaWithResponsables(page, stamp, "M");
  const accounts = await api<Account[]>(page.request, "GET", "/settlements/accounts");
  const mainCash = accounts.find((a) => a.code === "main_cash")!.id;
  const remit = (sid: number, amount: string, time: string) =>
    api(page.request, "POST", `/settlements/outlet-settlements/${sid}/remittances`, {
      client_request_id: crypto.randomUUID(),
      amount,
      destination_account_id: mainCash,
      delivered_at: `${day}T${time}:00-03:00`,
    });

  // R1: delivery of 700.000, closed. R2: delivery of 100.000, open (by API: the setup is not under test).
  const first = await api<{ id: number }>(page.request, "POST", "/settlements/outlet-settlements", {
    client_request_id: crypto.randomUUID(),
    business_date: day,
    outlet_id: boca.id,
    expected_amount: "700000",
  });
  await remit(first.id, "700000", "12:00");
  const preview = await api<{ preview_token: string }>(
    page.request,
    "GET",
    `/settlements/outlet-settlements/${first.id}/closing-preview`,
  );
  await api(page.request, "POST", `/settlements/outlet-settlements/${first.id}/close`, {
    version: 1,
    preview_token: preview.preview_token,
    allocations: [],
    confirm: true,
  });
  const second = await api<{ id: number }>(page.request, "POST", "/settlements/outlet-settlements", {
    client_request_id: crypto.randomUUID(),
    business_date: day,
    outlet_id: boca.id,
    expected_amount: "500000",
    confirm_additional: true,
  });
  await remit(second.id, "100000", "18:00");
  expect((await balances(page))[`Entregas a cuenta — ${boca.name}`]).toBe(-10_000_000);

  // The board shows both, the open one first, each with its own figures.
  await switchUser(page, operatorEmail, operatorPassword, "/rendiciones/boca");
  await page.goto(`/rendiciones/boca?fecha=${day}`);
  const card = page.getByRole("article", { name: boca.name });
  await expect(card.getByTestId("board-multiple")).toHaveText("· 2 rendiciones este día");
  await expect(card.getByTestId("board-state")).toHaveText("Abierta");
  await expect(card).toContainText("Rendición n.º 2");
  await expect(card).toContainText("Total justificado$ 100.000,00");
  await expect(card.getByTestId("board-card-remaining")).toHaveText("$ 400.000,00");
  if (mobile) await noHorizontalScroll(page);
  await card.getByRole("button", { name: "N.º 1 · Cerrada" }).click();
  await expect(card.getByTestId("board-state")).toHaveText("Cerrada");
  await expect(card).toContainText("Total justificado$ 700.000,00");
  await expect(card.getByTestId("board-card-difference")).toHaveText("Balanceada");
  await expect(card.getByRole("link", { name: `Ver Rendición n.º 1 de ${boca.name}` })).toHaveAttribute(
    "href",
    `/rendiciones/boca/${first.id}`,
  );

  // Every settlement of the boca, apart, in the list; and its ficha.
  await page.goto(`/rendiciones/boca?vista=todas&boca=${boca.id}`);
  await expect(page.getByRole("link", { name: /^Abrir Rendición n\.º 1 de / })).toHaveAttribute(
    "href",
    `/rendiciones/boca/${first.id}`,
  );
  await expect(page.getByRole("link", { name: /^Abrir Rendición n\.º 2 de / })).toHaveAttribute(
    "href",
    `/rendiciones/boca/${second.id}`,
  );
  if (mobile) await noHorizontalScroll(page);
  await page.goto(`/bocas/${boca.id}`);
  await expect(page.getByTestId("outlet-open-settlements")).toHaveText("1 rendición abierta.");
  await expect(page.getByTestId("outlet-not-consolidated")).toContainText("$ 100.000,00");
  if (mobile) await noHorizontalScroll(page);
});
