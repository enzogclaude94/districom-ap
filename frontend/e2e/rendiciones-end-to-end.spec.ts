import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { login } from "./helpers";
import { api, day, galicia } from "./settlement-setup";

/**
 * R9-3, Rendiciones end to end: one business scenario over seven business days that goes through everything from R1
 * to R9-2 as one system, and checks each day's «Cierre del día» against the ledger.
 *
 *   Día 1  opening balances of Caja principal and Banco.
 *   Día 2  a delivery opened without its expected amount, filled by Store Sys, with count (two boxes), a verified
 *          transfer, a cheque, a fuel expense, a pending and a shortage allocated to the driver; closed.
 *   Día 3  a boca: expected by Store Sys, two remittances, a transfer and the final count; closed.
 *   Día 4  the Día 2 pending collected (partial in cash on screen, the rest by transfer); an advance and a loan.
 *   Día 5  the Día 2 cheque deposited and credited (the same cheque, never a new one).
 *   Día 6  a payment to a supplier from Caja and Banco.
 *   Día 7  the REGULAR payroll (salary, bonus, the shortage, the advance, one installment), closed and paid from
 *          Caja and Banco.
 *   Then   the Día 3 boca is reopened, Store Sys sends a new revision while it is being corrected, it is accepted, the
 *          count is corrected and it closes again: the previous close is reversed on Día 3, and every later day
 *          still chains.
 *
 * Each project (desktop, mobile) runs the whole scenario on its own seven past days, which no other spec writes, so
 * every figure is exact on a clean base. Setup goes by API; the critical steps go through the screen on both.
 * A fresh platform admin, created with the CLI, holds the Store Sys and Empleados areas (ADR 010, ADR 011); the E2E
 * operator, without the Empleados area, reads the same days with the employee movements reserved.
 */

const PASSWORD = "root-password-123";
// Rendiciones operator without the Empleados area (the E2E admin holds it).
const operatorEmail = process.env.E2E_OPERATOR_EMAIL ?? "e2e-operator@districom.local";
const operatorPassword = process.env.E2E_OPERATOR_PASSWORD ?? "operator-password-123";
const EF = "/settlements/employee-finance";
const DAILY = "/settlements/daily-close";
const DRIVER_FUNCTION = "settlements.delivery_driver";

type Money = string;
interface Category {
  key: string;
  label: string;
  operations: number;
  amount: Money;
}
interface AccountDay {
  account_id: number;
  name: string;
  active: boolean;
  opening_balance: Money;
  inflows: Money;
  outflows: Money;
  net: Money;
  closing_balance: Money;
  reconciled: boolean;
  inflow_categories: Category[];
  outflow_categories: Category[];
}
interface SettlementDay {
  id: number;
  type: "DELIVERY" | "OUTLET";
  name: string;
  financial_state: string;
  being_corrected: boolean;
  expected_missing: boolean;
  expected_update_pending: boolean;
  expected_amount: Money | null;
  expected_origin: { source: string; external_reference: string | null; revision: number | null } | null;
  justified: Money | null;
  difference: Money | null;
}
interface DailyClose {
  business_date: string;
  reconciled: boolean;
  accounts: AccountDay[];
  settlements: { total: number; open: number; closed: number; being_corrected: number; items: SettlementDay[] };
  cheques: Record<string, number | Money>;
}
interface Entry {
  account_id: number;
  account_name: string;
  account_kind: string;
  amount: Money;
  cheque_id: number | null;
}
interface Operation {
  id: number;
  type: string;
  business_date: string;
  source_type: string | null;
  source_id: number | null;
  reverses_operation_id: number | null;
  reversed_by_operation_id: number | null;
  entries: Entry[];
}
interface Leg {
  account_id: number;
  account_name: string;
  account_kind: string;
  employee_id: number | null;
  cheque_id: number | null;
  amount: Money;
}
interface Closing {
  settlement_version: number;
  operation_id: number | null;
  reversal_operation_id: number | null;
  in_effect: boolean;
}
interface Settlement {
  id: number;
  version: number;
  expected_amount: Money | null;
  expected_source: string | null;
  expected_event_id: number | null;
  financial_state: string;
  close_in_effect: { operation_id: number | null } | null;
  transfers: { id: number }[];
  cheques: { id: number }[];
  pending_collections: { id: number }[];
  current_difference: { kind: string; amount: Money; close_operation_id: number } | null;
}
interface ExpectedEvent {
  id: number;
  source: string;
  outcome: string;
  expected_amount: Money;
  external_reference: string | null;
  revision: number | null;
  in_effect: boolean;
}
interface Account {
  id: number;
  code: string | null;
  name: string;
  kind: string;
  balance: Money;
}

const cents = (amount: Money | number) => Math.round(Number(amount) * 100);
const id = () => crypto.randomUUID();

function createPlatformAdmin(email: string) {
  execFileSync(
    "uv",
    ["run", "--directory", "../backend", "python", "-m", "app.cli", "create-admin", "--email", email, "--name", "Root"],
    { input: `${PASSWORD}\n`, stdio: ["pipe", "ignore", "inherit"] },
  );
}

async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function closeView(request: APIRequestContext, on: string) {
  return api<DailyClose>(request, "GET", `${DAILY}?on=${on}`);
}

/** The account's day, with every account's opening + net = closing checked first. */
function accountOf(view: DailyClose, accountId: number) {
  expect(view.reconciled, `invariant on ${view.business_date}`).toBe(true);
  for (const a of view.accounts) {
    expect(cents(a.opening_balance) + cents(a.net), `${a.name} on ${view.business_date}`).toBe(
      cents(a.closing_balance),
    );
    expect(a.reconciled).toBe(true);
  }
  return view.accounts.find((a) => a.account_id === accountId);
}

/** {category key: [operations, amount]} of one side of an account's day. */
function flows(account: AccountDay | undefined, side: "inflow" | "outflow") {
  if (!account) return {};
  return Object.fromEntries(
    account[`${side}_categories`].map((c) => [c.key, [c.operations, Number(c.amount)] as const]),
  );
}

/** The day as a whole: opening, net and closing per account, in pesos. */
function figures(account: AccountDay | undefined) {
  if (!account) return null;
  return [Number(account.opening_balance), Number(account.net), Number(account.closing_balance)];
}

test("Rendiciones de punta a punta: seven business days, one coherent system", async ({ page }, testInfo) => {
  test.setTimeout(600_000);
  const mobile = testInfo.project.name === "mobile";
  // Desktop: 22 to 16 days ago; mobile: 13 to 7 days ago. Seven past days that no other spec writes.
  const base = mobile ? -14 : -23;
  const D = (n: number) => day(base + n);
  const run = `${Date.now()}${Math.floor(Math.random() * 1000)}${mobile ? "M" : "D"}`;
  const { request } = page;
  const trace: Record<string, unknown> = { run, project: testInfo.project.name, days: {} as Record<string, string> };
  for (let n = 0; n <= 8; n++) (trace.days as Record<string, string>)[`D${n}`] = D(n);

  // --- Cast ------------------------------------------------------------------------------------------------------
  const email = `e2e-r9-3-${run}@districom.local`;
  createPlatformAdmin(email);
  await login(page, "/rendiciones", email, PASSWORD);
  await api(request, "PUT", "/auth/access-grants", { email, key: "settlements.store_sys_import", role: "admin" });
  await api(request, "PUT", "/auth/access-grants", { email, key: "settlements.employee_finance", role: "admin" });

  const accounts = await api<Account[]>(request, "GET", "/settlements/accounts");
  const byCode = (code: string) => accounts.find((a) => a.code === code)!.id;
  const main = byCode("main_cash");
  const change = byCode("change_cash");
  const bank = byCode("bank");

  const driverName = `Repartidor punta a punta ${run}`;
  const driver = await api<{ id: number }>(request, "POST", "/employees", {
    full_name: driverName,
    hire_date: null,
    phone: null,
  });
  await api(request, "POST", `/employees/${driver.id}/functions`, {
    function_code: DRIVER_FUNCTION,
    valid_from: "2026-01-01",
  });
  const plate = `PP${run.slice(-6)}`;
  const vehicle = await api<{ id: number }>(request, "POST", "/vehicles", {
    plate,
    description: "Camión punta a punta",
  });
  const outletCode = `PP${run.slice(-8)}`;
  const outlet = await api<{ id: number }>(request, "POST", "/outlets", {
    code: outletCode,
    name: `Boca punta a punta ${run}`,
    description: null,
  });
  const customer = `Almacén Don Tito ${run}`;
  const supplierName = `Distribuidora Norte ${run}`;
  const fuel = (await api<{ id: number; name: string }[]>(request, "GET", "/settlements/expense-categories")).find(
    (c) => c.name === "Combustible",
  )!.id;
  await api(request, "POST", `${EF}/employees/${driver.id}/compensation-terms`, {
    valid_from: D(1),
    base_salary: "900000",
    notes: null,
  });
  Object.assign(trace, { email, driver: driver.id, vehicle: vehicle.id, outlet: outlet.id, main, change, bank });

  // What each account held at the end of the day before Día 1: the scenario's figures are exact on top of it. On a
  // clean base it is zero (desktop); mobile's days come after desktop's, so it opens with desktop's closing.
  const dayZero = await closeView(request, D(0));
  const start: Record<number, number> = {};
  for (const account of [main, change, bank]) {
    const a = accountOf(dayZero, account);
    start[account] = a ? Number(a.closing_balance) : 0;
  }
  if (!mobile) expect(Object.values(start)).toEqual([0, 0, 0]);
  trace.startBalances = start;
  /** opening, net and closing of the scenario, moved by what the account held before it. */
  const shifted = (account: number, [opening, net, closing]: number[]) => [
    opening + start[account]!,
    net,
    closing + start[account]!,
  ];
  const expectDay = (view: DailyClose, account: number, expected: number[]) =>
    expect(figures(accountOf(view, account)), `${view.business_date} ${account}`).toEqual(shifted(account, expected));

  // --- Día 1: opening balances ---------------------------------------------------------------------------------
  await test.step("Día 1: opening balances", async () => {
    for (const [account, amount] of [
      [main, "500000"],
      [bank, "1000000"],
    ] as const) {
      await api(request, "POST", "/settlements/operations/opening-balance", {
        client_request_id: id(),
        business_date: D(1),
        account_id: account,
        amount,
        description: "Saldo inicial punta a punta",
      });
    }
    const view = await closeView(request, D(1));
    expectDay(view, main, [0, 500000, 500000]);
    expectDay(view, bank, [0, 1000000, 1000000]);
    expect(flows(accountOf(view, main), "inflow")).toEqual({ OPENING_BALANCE: [1, 500000] });
    // Caja de cambio: created with the database (today) and without balance or movement yet: not on Día 1.
    if (start[change] === 0) expect(accountOf(view, change)).toBeUndefined();
    else expectDay(view, change, [0, 0, 0]);
    const cajas = await api<Account[]>(request, "GET", `/settlements/accounts?on=${D(1)}`);
    expect(Number(cajas.find((a) => a.id === main)!.balance)).toBe(500000 + start[main]!);
  });

  // --- Día 2: the delivery -------------------------------------------------------------------------------------
  const storeCode = `PP-${run}`;
  const deliveryRef = `LIQ-PP-${run}`;
  let deliveryId = 0;
  let chequeId = 0;
  let pendingId = 0;
  let deliveryClose = 0;
  await test.step("Día 2: delivery opened without expected; Store Sys code on screen", async () => {
    await page.goto("/rendiciones/integracion-store-sys");
    await expect(page.getByRole("heading", { name: "Integración Store Sys" })).toBeVisible();
    await page.getByLabel("Empleado").fill(driverName);
    const row = page.getByRole("row").filter({ hasText: driverName });
    await expect(row).toContainText("Sin código");
    await row.getByRole("button", { name: "Cargar código" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Código").fill(storeCode);
    await dialog.getByRole("button", { name: "Guardar" }).click();
    await expect(dialog).toBeHidden();
    await expect(row).toContainText(storeCode);
    await noSideScroll(page);

    await page.goto("/rendiciones/repartos");
    await page.getByRole("button", { name: "Nueva rendición" }).click();
    const create = page.getByRole("dialog");
    await create.getByLabel("Fecha").fill(D(2));
    await create.getByLabel("Repartidor").selectOption({ label: driverName });
    await create.getByRole("button", { name: "Crear rendición" }).click();
    await expect(page).toHaveURL(/\/rendiciones\/repartos\/\d+$/);
    deliveryId = Number(page.url().split("/").pop());
    await expect(page.getByTestId("expected-missing")).toHaveText("Sin cargar");
    await expect(page.getByTestId("board-difference")).toHaveText("—");
    await noSideScroll(page);
  });

  await test.step("Día 2: count, transfer, cheque, fuel and pending in the same settlement", async () => {
    const base = `/settlements/deliveries/${deliveryId}`;
    await api(request, "PUT", `${base}/count`, {
      lines: [
        { denomination_code: "ARS_10000", quantity: 50, destination_account_id: main },
        { denomination_code: "ARS_500", quantity: 20, destination_account_id: change },
      ],
    });
    const transfer = await api<Settlement>(request, "POST", `${base}/transfers`, {
      client_request_id: id(),
      amount: "200000",
      bank_account_id: bank,
      reference: `OP-${run}`,
      payer_name: customer,
    });
    await api(request, "POST", `${base}/transfers/${transfer.transfers[0]!.id}/status`, {
      status: "VERIFIED",
      reason: null,
    });
    const withCheque = await api<Settlement>(request, "POST", `${base}/cheques`, {
      client_request_id: id(),
      kind: "PHYSICAL",
      payment_type: "COMMON",
      amount: "150000",
      issuer_bank_id: await galicia(request),
      cheque_number: run.replace(/\D/g, "").slice(-8),
      issue_date: D(2),
      drawer_name: "Librador punta a punta",
      delivered_by_name: customer,
      physical_receipt_confirmed: true,
      received_by_employee_id: driver.id,
    });
    chequeId = withCheque.cheques[0]!.id;
    await api(request, "POST", `${base}/expenses`, {
      client_request_id: id(),
      category_id: fuel,
      description: "Carga del día",
      fuel: {
        vehicle_id: vehicle.id,
        employee_id: driver.id,
        liters: "50",
        price_per_liter: "1000",
        odometer_km: 12000,
      },
    });
    const withPending = await api<Settlement>(request, "POST", `${base}/pending-collections`, {
      client_request_id: id(),
      customer_name: customer,
      amount: "80000",
      reason: "Paga la semana que viene",
      responsible_employee_id: driver.id,
      expected_date: D(4),
    });
    pendingId = withPending.pending_collections[0]!.id;

    // Without the expected amount it cannot close, on screen too.
    await page.reload();
    await page.getByRole("button", { name: "Cerrar rendición" }).click();
    const blocked = page.getByRole("dialog");
    await expect(blocked.getByText("Cargá el importe esperado para cerrar la rendición.")).toBeVisible();
    await expect(blocked.getByRole("button", { name: "Confirmar cierre" })).toBeDisabled();
    await blocked.getByRole("button", { name: "Cancelar" }).click();
    // Día 2 while it is open: listed without expected, nothing in Cajas yet.
    const open = await closeView(request, D(2));
    const listed = open.settlements.items.find((s) => s.id === deliveryId)!;
    expect(listed).toMatchObject({ financial_state: "NOT_CLOSED", expected_missing: true, justified: null });
  });

  await test.step("Día 2: Store Sys fills the expected amount", async () => {
    const result = await api<{ results: { outcome: string; event_id?: number; settlement_id?: number }[] }>(
      request,
      "POST",
      "/settlements/imports/store-sys/expected-amounts",
      {
        source: "STORE_SYS",
        client_request_id: id(),
        records: [
          {
            settlement_type: "DELIVERY",
            business_date: D(2),
            revision: 1,
            external_reference: deliveryRef,
            employee_store_code: storeCode,
            expected_amount: "1000000.00",
          },
        ],
      },
    );
    expect(result.results[0]).toMatchObject({ outcome: "APPLIED" });
    const settlement = await api<Settlement>(request, "GET", `/settlements/deliveries/${deliveryId}`);
    expect(settlement).toMatchObject({ expected_amount: "1000000.00", expected_source: "STORE_SYS" });
    const history = await api<{ events: ExpectedEvent[] }>(
      request,
      "GET",
      `/settlements/settlements/${deliveryId}/expected-history`,
    );
    const applied = history.events.find((e) => e.id === settlement.expected_event_id)!;
    expect(applied).toMatchObject({
      source: "STORE_SYS",
      outcome: "APPLIED",
      external_reference: deliveryRef,
      revision: 1,
      expected_amount: "1000000.00",
      in_effect: true,
    });
    trace.storeSysEvent = applied.id;
    await page.reload();
    await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${deliveryRef} rev. 1`);
    // 500.000 + 10.000 + 200.000 + 150.000 + 50.000 + 80.000 = 990.000 against 1.000.000.
    await expect(page.getByTestId("board-difference")).toHaveText("Faltante $ 10.000,00");
  });

  await test.step("Día 2: preview with its exact legs, then close on screen with the shortage to the driver", async () => {
    const preview = await api<{ difference: Money; difference_kind: string; legs: Leg[] }>(
      request,
      "GET",
      `/settlements/deliveries/${deliveryId}/closing-preview`,
    );
    expect(preview).toMatchObject({ difference: "-10000.00", difference_kind: "SHORTAGE" });
    // Every leg but the allocation, which the close adds with the parts chosen: they add up to the shortage.
    expect(preview.legs.map((l) => [l.account_name, l.cheque_id, l.amount])).toEqual([
      ["Caja principal", null, "500000.00"],
      ["Caja de cambio", null, "10000.00"],
      ["Banco", null, "200000.00"],
      ["Cobranzas de reparto", null, "-1000000.00"],
      ["Gastos operativos", null, "50000.00"],
      ["Pendientes de cobro", null, "80000.00"],
      ["Cartera de cheques", chequeId, "150000.00"],
    ]);
    expect(preview.legs.reduce((sum, l) => sum + cents(l.amount), 0)).toBe(cents("-10000"));

    await page.getByRole("button", { name: "Cerrar rendición" }).click();
    const close = page.getByRole("dialog");
    await expect(close.getByLabel("Importes del cierre")).toContainText("$ 990.000,00");
    await close.getByLabel("Importe de la parte 1").fill("10.000");
    await expect(close.getByTestId("remaining")).toHaveText("$ 0,00");
    await noSideScroll(page);
    await close.getByRole("button", { name: "Confirmar cierre" }).click();
    await expect(close).toBeHidden();
    await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

    const closed = await api<Settlement>(request, "GET", `/settlements/deliveries/${deliveryId}`);
    deliveryClose = closed.close_in_effect!.operation_id!;
    expect(closed.current_difference).toMatchObject({
      kind: "SHORTAGE",
      amount: "10000.00",
      close_operation_id: deliveryClose,
    });
    trace.deliveryClose = deliveryClose;
  });

  await test.step("Día 2: ledger, Cajas, cheques, pending, internal account and Cierre del día", async () => {
    const op = await api<Operation>(request, "GET", `/settlements/operations/${deliveryClose}`);
    expect(op).toMatchObject({ type: "SETTLEMENT_CLOSE", business_date: D(2), source_id: deliveryId });
    expect(op.entries.reduce((sum, e) => sum + cents(e.amount), 0)).toBe(0); // balanced
    const at = (accountId: number) => op.entries.filter((e) => e.account_id === accountId).map((e) => e.amount);
    expect([at(main), at(change), at(bank)]).toEqual([["500000.00"], ["10000.00"], ["200000.00"]]);
    expect(op.entries.find((e) => e.cheque_id === chequeId)!.amount).toBe("150000.00");
    const employeeLeg = op.entries.find((e) => e.account_kind === "EMPLOYEE")!;
    expect(employeeLeg.amount).toBe("10000.00");

    const cheque = await api<{ state: string; settlement_id: number; amount: Money }>(
      request,
      "GET",
      `/settlements/cheques/${chequeId}`,
    );
    expect(cheque).toMatchObject({ state: "IN_PORTFOLIO", settlement_id: deliveryId, amount: "150000.00" });
    const pending = await api<{ balance: Money; responsible_employee_id: number; customer_name: string }>(
      request,
      "GET",
      `/settlements/pending-collections/${pendingId}`,
    );
    expect(pending).toMatchObject({ balance: "80000.00", responsible_employee_id: driver.id, customer_name: customer });
    const internal = await api<{ balance: Money }>(request, "GET", `${EF}/employees/${driver.id}`);
    expect(internal.balance).toBe("10000.00");

    const view = await closeView(request, D(2));
    expectDay(view, main, [500000, 500000, 1000000]);
    expectDay(view, change, [0, 10000, 10000]);
    expectDay(view, bank, [1000000, 200000, 1200000]);
    expect(flows(accountOf(view, main), "inflow")).toEqual({ SETTLEMENT_CLOSE_DELIVERY: [1, 500000] });
    const item = view.settlements.items.find((s) => s.id === deliveryId)!;
    expect(item).toMatchObject({
      financial_state: "CLOSED",
      expected_amount: "1000000.00",
      justified: "990000.00",
      difference: "-10000.00",
      expected_origin: { source: "STORE_SYS", external_reference: deliveryRef, revision: 1 },
    });
    expect(view.cheques).toMatchObject({ portfolio_count: 1, portfolio_amount: "150000.00" });

    await page.goto(`/rendiciones/cierre-del-dia?fecha=${D(2)}`);
    await expect(page.getByTestId(`account-${main}`)).toContainText("Cierres de rendición de reparto");
    await expect(page.getByTestId(`settlement-${deliveryId}`)).toContainText("Cerrada");
    await expect(page.getByTestId("cheques-day")).toContainText("$ 150.000,00");
    await noSideScroll(page);
  });

  // --- Día 3: the boca -----------------------------------------------------------------------------------------
  const outletRef = `BOCA-PP-${run}`;
  let outletId = 0;
  let outletClose = 0;
  await test.step("Día 3: boca with Store Sys expected, remittances, transfer and final count; closed", async () => {
    const created = await api<{ id: number }>(request, "POST", "/settlements/outlet-settlements", {
      client_request_id: id(),
      business_date: D(3),
      outlet_id: outlet.id,
      expected_amount: null,
      external_reference: null,
      notes: null,
      confirm_additional: false,
    });
    outletId = created.id;
    const imported = await api<{ results: { outcome: string }[] }>(
      request,
      "POST",
      "/settlements/imports/store-sys/expected-amounts",
      {
        source: "STORE_SYS",
        client_request_id: id(),
        records: [
          {
            settlement_type: "OUTLET",
            business_date: D(3),
            revision: 1,
            external_reference: outletRef,
            outlet_code: outletCode,
            expected_amount: "600000.00",
          },
        ],
      },
    );
    expect(imported.results[0]).toMatchObject({ outcome: "APPLIED" });
    const base = `/settlements/outlet-settlements/${outletId}`;
    for (const [amount, time] of [
      ["300000", "13:00"],
      ["150000", "17:00"],
    ] as const) {
      await api(request, "POST", `${base}/remittances`, {
        client_request_id: id(),
        amount,
        destination_account_id: main,
        delivered_at: `${D(3)}T${time}:00-03:00`,
        delivered_by_employee_id: null,
        reference: null,
        confirm_above_expected: false,
      });
    }
    const transfer = await api<Settlement>(request, "POST", `${base}/transfers`, {
      client_request_id: id(),
      amount: "50000",
      bank_account_id: bank,
      reference: null,
      payer_name: "Cliente de la boca",
    });
    await api(request, "POST", `${base}/transfers/${transfer.transfers[0]!.id}/status`, {
      status: "VERIFIED",
      reason: null,
    });
    await api(request, "PUT", `${base}/count`, {
      lines: [{ denomination_code: "ARS_10000", quantity: 10, destination_account_id: main }],
    });

    await page.goto(`/rendiciones/boca/${outletId}`);
    await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${outletRef} rev. 1`);
    await page.getByRole("button", { name: "Cerrar rendición" }).click();
    const close = page.getByRole("dialog");
    await close.getByRole("button", { name: "Confirmar cierre" }).click();
    await expect(close).toBeHidden();
    await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");
    await noSideScroll(page);

    const closed = await api<Settlement>(request, "GET", base);
    outletClose = closed.close_in_effect!.operation_id!;
    const op = await api<Operation>(request, "GET", `/settlements/operations/${outletClose}`);
    expect(op.entries.reduce((sum, e) => sum + cents(e.amount), 0)).toBe(0);
    // The close consolidates the remittances already in Caja: «Entregas a cuenta de boca» +450.000 back to zero.
    const advances = op.entries.filter((e) => e.account_kind === "OUTLET_ADVANCES");
    expect(advances.map((e) => e.amount)).toEqual(["450000.00"]);
    trace.outletClose = outletClose;

    const view = await closeView(request, D(3));
    expectDay(view, main, [1000000, 550000, 1550000]);
    expect(flows(accountOf(view, main), "inflow")).toEqual({
      OUTLET_REMITTANCE: [2, 450000],
      SETTLEMENT_CLOSE_OUTLET: [1, 100000],
    });
    expectDay(view, bank, [1200000, 50000, 1250000]);
    expect(view.settlements.items.find((s) => s.id === outletId)).toMatchObject({
      type: "OUTLET",
      financial_state: "CLOSED",
      expected_amount: "600000.00",
      justified: "600000.00",
      difference: "0.00",
    });
  });

  // --- Día 4: the pending, an advance and a loan ---------------------------------------------------------------
  let advanceId = 0;
  let loanId = 0;
  await test.step("Día 4: the Día 2 pending collected (partial on screen, then the rest)", async () => {
    await page.goto(`/rendiciones/pendientes/${pendingId}`);
    await expect(page.getByTestId("pending-balance")).toHaveText("$ 80.000,00");
    await page.getByRole("button", { name: "Registrar cobro" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Registrar cobro" });
    await dialog.getByLabel("Importe").fill("30.000");
    await dialog.getByRole("radio", { name: "Efectivo" }).check();
    await dialog.getByLabel("Caja de destino").selectOption({ label: "Caja principal" });
    await dialog.getByLabel("Fecha de negocio").fill(D(4));
    await dialog.getByRole("button", { name: "Revisar cobro" }).click();
    await expect(dialog.getByRole("group", { name: "Revisá el cobro" })).toContainText(customer);
    await noSideScroll(page);
    await dialog.getByRole("button", { name: "Confirmar cobro" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId("pending-balance")).toHaveText("$ 50.000,00");
    await expect(page.getByTestId("pending-state")).toHaveText("Parcial");

    await api(request, "POST", `/settlements/pending-collections/${pendingId}/receipts`, {
      client_request_id: id(),
      amount: "50000",
      method: "TRANSFER",
      destination_account_id: bank,
      business_date: D(4),
    });
    await page.reload();
    await expect(page.getByTestId("pending-balance")).toHaveText("$ 0,00");
    const pending = await api<{ balance: Money; collected: Money; receipts: { business_date: string }[] }>(
      request,
      "GET",
      `/settlements/pending-collections/${pendingId}`,
    );
    expect(pending).toMatchObject({ balance: "0.00", collected: "80000.00" });
    expect(pending.receipts.map((r) => r.business_date)).toEqual([D(4), D(4)]);
  });

  await test.step("Día 4: an advance from Caja and a loan from Banco", async () => {
    const advance = await api<{ id: number }>(request, "POST", `${EF}/employees/${driver.id}/advances`, {
      client_request_id: id(),
      business_date: D(4),
      amount: "100000",
      description: "Adelanto de sueldo",
      reference: null,
      funding_account_id: main,
    });
    advanceId = advance.id;
    const loan = await api<{ id: number }>(request, "POST", `${EF}/employees/${driver.id}/loans`, {
      client_request_id: id(),
      business_date: D(4),
      principal: "120000",
      funding_account_id: bank,
      installment_count: 3,
      first_due_on: D(7),
      description: "Préstamo punta a punta",
      reference: null,
    });
    loanId = loan.id;
    const view = await closeView(request, D(4));
    expectDay(view, main, [1550000, -70000, 1480000]);
    expect(flows(accountOf(view, main), "inflow")).toEqual({ PENDING_COLLECTION: [1, 30000] });
    expect(flows(accountOf(view, main), "outflow")).toEqual({ EMPLOYEE_ADVANCE: [1, 100000] });
    expectDay(view, bank, [1250000, -70000, 1180000]);
    expect(flows(accountOf(view, bank), "inflow")).toEqual({ PENDING_COLLECTION: [1, 50000] });
    expect(flows(accountOf(view, bank), "outflow")).toEqual({ EMPLOYEE_LOAN: [1, 120000] });
  });

  // --- Día 5: the Día 2 cheque, deposited and credited -----------------------------------------------------------
  await test.step("Día 5: the same cheque deposited and credited", async () => {
    await api(request, "POST", "/settlements/cheque-deposits", {
      client_request_id: id(),
      bank_account_id: bank,
      cheque_ids: [chequeId],
      business_date: D(5),
      slip_number: `BOL-${run.slice(-6)}`,
      notes: null,
    });
    const deposited = await api<{ state: string }>(request, "GET", `/settlements/cheques/${chequeId}`);
    expect(deposited.state).toBe("DEPOSITED");
    const inTransit = await closeView(request, D(5));
    expect(inTransit.cheques).toMatchObject({
      portfolio_count: 0,
      in_transit_count: 1,
      in_transit_amount: "150000.00",
    });

    await api(request, "POST", "/settlements/cheque-credits", {
      client_request_id: id(),
      bank_account_id: bank,
      cheque_ids: [chequeId],
      business_date: D(5),
      notes: null,
    });
    const cheque = await api<{
      state: string;
      deposit_business_date: string;
      credit_business_date: string;
      documents: { type: string; business_date: string; reversed: boolean; operation_id: number }[];
    }>(request, "GET", `/settlements/cheques/${chequeId}`);
    expect(cheque).toMatchObject({ state: "CREDITED", deposit_business_date: D(5), credit_business_date: D(5) });
    // Its history: the deposit and the credit of Día 5, neither reversed.
    expect(cheque.documents.map((d) => [d.type, d.business_date, d.reversed])).toEqual([
      ["DEPOSIT", D(5), false],
      ["CREDIT", D(5), false],
    ]);
    trace.chequeDocuments = cheque.documents;
    const view = await closeView(request, D(5));
    expectDay(view, bank, [1180000, 150000, 1330000]);
    expect(flows(accountOf(view, bank), "inflow")).toEqual({ CHEQUE_CREDIT: [1, 150000] });
    expect(view.cheques).toMatchObject({
      portfolio_count: 0,
      in_transit_count: 0,
      deposited_count: 1,
      deposited_amount: "150000.00",
      credited_count: 1,
      credited_amount: "150000.00",
    });
    // Día 4 still ends with the cheque in the portfolio.
    expect((await closeView(request, D(4))).cheques).toMatchObject({
      portfolio_count: 1,
      portfolio_amount: "150000.00",
    });
  });

  // --- Día 6: a payment to a supplier ----------------------------------------------------------------------------
  await test.step("Día 6: a supplier paid from Caja and Banco", async () => {
    const supplier = await api<{ id: number }>(request, "POST", "/third-parties", {
      name: supplierName,
      tax_id: null,
      notes: null,
    });
    const payment = await api<{ id: number; operation_id: number; amount: Money }>(
      request,
      "POST",
      "/settlements/payments",
      {
        client_request_id: id(),
        business_date: D(6),
        third_party_id: supplier.id,
        concept: "MERCHANDISE",
        reference: `FC-${run.slice(-6)}`,
        description: "Mercadería de la semana",
        items: [
          { method: "CASH", account_id: main, amount: "70000" },
          { method: "TRANSFER", account_id: bank, amount: "50000", reference: `TR-${run.slice(-6)}` },
        ],
      },
    );
    expect(payment.amount).toBe("120000.00");
    const op = await api<Operation>(request, "GET", `/settlements/operations/${payment.operation_id}`);
    expect(op.entries.reduce((sum, e) => sum + cents(e.amount), 0)).toBe(0);
    const at = (accountId: number) => op.entries.find((e) => e.account_id === accountId)!.amount;
    expect([at(main), at(bank)]).toEqual(["-70000.00", "-50000.00"]);
    trace.payment = payment.id;
    const view = await closeView(request, D(6));
    expectDay(view, main, [1480000, -70000, 1410000]);
    expectDay(view, bank, [1330000, -50000, 1280000]);
    expect(flows(accountOf(view, main), "outflow")).toEqual({ PAYMENT: [1, 70000] });
  });

  // --- Día 7: payroll and payout ---------------------------------------------------------------------------------
  let payrollId = 0;
  let payoutId = 0;
  await test.step("Día 7: REGULAR payroll with the shortage, the advance and one installment; closed", async () => {
    const payroll = await api<{ id: number }>(request, "POST", `${EF}/payrolls`, {
      client_request_id: id(),
      employee_id: driver.id,
      kind: "REGULAR",
      period_start: D(1),
      period_end: D(7),
      payment_date: D(7),
      notes: null,
    });
    payrollId = payroll.id;
    for (const [kind, amount] of [
      ["SALARY", "900000"],
      ["BONUS", "50000"],
    ] as const) {
      // Not a whole calendar month: no automatic proration, the salary goes by hand with its note.
      const description = kind === "SALARY" ? "Período de siete días del escenario" : null;
      await api(request, "POST", `${EF}/payrolls/${payrollId}/lines`, { kind, amount, description });
    }
    await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
    const legs = page.locator('[data-testid^="payroll-leg-"]');
    await expect(legs).toHaveCount(2);
    await expect(legs.filter({ hasText: "Faltante de rendición" })).toContainText("$ 10.000,00");
    await expect(legs.filter({ hasText: "Adelanto" })).toContainText("$ 100.000,00");
    await expect(page.getByTestId("payroll-installments")).toContainText("$ 40.000,00");
    const preview = page.getByTestId("payroll-preview");
    await expect(preview).toContainText("Haberes$ 950.000,00");
    await expect(preview).toContainText("Descuentos$ 150.000,00");
    await expect(preview).toContainText("Neto$ 800.000,00");
    await noSideScroll(page);
    await page.getByRole("button", { name: "Cerrar liquidación" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Confirmar cierre" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId("payroll-status")).toHaveText("Cerrada");

    const closed = await api<{
      closing: {
        id: number;
        kind: string;
        operation_id: number;
        net: Money;
        applications: { entry_id: number; origin: string; amount: Money; installment_number: number | null }[];
      };
      payment: { net: Money; pending: Money };
    }>(request, "GET", `${EF}/payrolls/${payrollId}`);
    expect(closed.closing).toMatchObject({ net: "800000.00" });
    expect(closed.payment).toMatchObject({ net: "800000.00", pending: "800000.00" });
    // The shortage the payroll applied is the very entry the Día 2 close posted on the internal account.
    const movements = await api<{ items: { entry_id: number; operation_id: number; amount: Money }[] }>(
      request,
      "GET",
      `${EF}/employees/${driver.id}/movements?limit=50`,
    );
    const shortage = movements.items.find((m) => m.operation_id === deliveryClose)!;
    const applied = closed.closing.applications;
    expect(applied.find((a) => a.entry_id === shortage.entry_id)).toMatchObject({ amount: "10000.00" });
    expect(applied.filter((a) => a.installment_number === 1).map((a) => a.amount)).toEqual(["40000.00"]);
    expect(applied).toHaveLength(3);
    const close = await api<Operation>(request, "GET", `/settlements/operations/${closed.closing.operation_id}`);
    expect(close.type).toBe("PAYROLL_CLOSE");
    expect(close.entries.reduce((sum, e) => sum + cents(e.amount), 0)).toBe(0);
    // A payroll close never moves a cash box nor the bank.
    expect(close.entries.some((e) => e.account_kind === "CASH_BOX" || e.account_kind === "BANK")).toBe(false);
    const loan = await api<{ balance: Money; installments: { number: number; status: string }[] }>(
      request,
      "GET",
      `${EF}/loans/${loanId}`,
    );
    expect(loan.balance).toBe("80000.00");
    // 10.000 + 100.000 + 120.000 − 950.000: the company owes the driver.
    expect((await api<{ balance: Money }>(request, "GET", `${EF}/employees/${driver.id}`)).balance).toBe("-720000.00");
    Object.assign(trace, {
      payroll: payrollId,
      payrollClosing: closed.closing.id,
      payrollClose: closed.closing.operation_id,
    });
    trace.payrollApplications = closed.closing.applications;
  });

  await test.step("Día 7: payout from Caja and Banco", async () => {
    const payroll = await api<{ closing: { id: number } }>(request, "GET", `${EF}/payrolls/${payrollId}`);
    const payout = await api<{ id: number; operation_id: number; amount: Money }>(
      request,
      "POST",
      `${EF}/payrolls/${payrollId}/payouts`,
      {
        client_request_id: id(),
        payroll_closing_id: payroll.closing.id,
        business_date: D(7),
        reference: null,
        items: [
          { method: "CASH", account_id: main, amount: "300000" },
          { method: "TRANSFER", account_id: bank, amount: "500000" },
        ],
      },
    );
    payoutId = payout.id;
    expect(payout.amount).toBe("800000.00");
    const op = await api<Operation>(request, "GET", `/settlements/operations/${payout.operation_id}`);
    expect(op.entries.reduce((sum, e) => sum + cents(e.amount), 0)).toBe(0);
    expect(op.entries.find((e) => e.account_id === main)!.amount).toBe("-300000.00");
    expect(op.entries.find((e) => e.account_id === bank)!.amount).toBe("-500000.00");
    trace.payout = payoutId;
    trace.payoutOperation = payout.operation_id;

    await page.goto(`/rendiciones/empleados/liquidaciones/${payrollId}`);
    await expect(page.getByTestId("payment-status")).toHaveText("Pagada");
    const paid = await api<{ payment: { paid: Money; pending: Money } }>(request, "GET", `${EF}/payrolls/${payrollId}`);
    expect(paid.payment).toMatchObject({ paid: "800000.00", pending: "0.00" });
    await page.goto(`/rendiciones/empleados/${driver.id}`);
    // −720.000 + 800.000: what is left is the loan.
    await expect(page.getByTestId("internal-balance")).toHaveText("$ 80.000,00");
    await noSideScroll(page);

    const view = await closeView(request, D(7));
    expectDay(view, main, [1410000, -300000, 1110000]);
    expectDay(view, bank, [1280000, -500000, 780000]);
    expect(flows(accountOf(view, main), "outflow")).toEqual({ EMPLOYEE_PAYOUT: [1, 300000] });
  });

  // --- Privacy: the same days without the Empleados area ---------------------------------------------------------
  await test.step("Privacy: without the area the amounts reconcile and the employee is reserved", async () => {
    const withArea = { d4: await closeView(request, D(4)), d7: await closeView(request, D(7)) };
    await page.context().clearCookies();
    await login(page, "/rendiciones", operatorEmail, operatorPassword);
    for (const [on, full] of [
      [D(4), withArea.d4],
      [D(7), withArea.d7],
    ] as const) {
      const reserved = await closeView(request, on);
      for (const a of full.accounts) {
        expect(figures(accountOf(reserved, a.account_id))).toEqual(figures(a));
      }
      const keys = reserved.accounts.flatMap((a) =>
        [...a.inflow_categories, ...a.outflow_categories].map((c) => c.key),
      );
      expect(keys).toContain("EMPLOYEE_RESERVED");
      expect(keys).not.toContain("EMPLOYEE_ADVANCE");
      expect(keys).not.toContain("EMPLOYEE_PAYOUT");
      const csv = await request.get(`/api/v1${DAILY}.csv?on=${on}`, { headers: { "X-Requested-With": "e2e" } });
      const text = await csv.text();
      expect(text).toContain("Movimiento de empleado (reservado)");
      expect(text).not.toContain(driverName);
      expect(text).not.toContain("Adelanto");
    }
    // The internal account does not exist for this user (404, ADR 010).
    const hidden = await request.get(`/api/v1${EF}/employees/${driver.id}`, { headers: { "X-Requested-With": "e2e" } });
    expect(hidden.status()).toBe(404);
    await page.goto(`/rendiciones/cierre-del-dia?fecha=${D(4)}`);
    await expect(page.getByTestId(`account-${main}`)).toContainText("Movimiento de empleado (reservado)");
    await expect(page.getByTestId(`account-${main}`)).not.toContainText("Adelantos a empleados");
    await noSideScroll(page);

    await page.context().clearCookies();
    await login(page, "/rendiciones", email, PASSWORD);
    await page.goto(`/rendiciones/cierre-del-dia?fecha=${D(4)}`);
    await expect(page.getByTestId(`account-${main}`)).toContainText("Adelantos a empleados");
  });

  // --- Correction of the Día 3 boca --------------------------------------------------------------------------------
  let newOutletClose = 0;
  await test.step("Correction: the boca reopens; Store Sys revision 2 waits; the old close stays in effect", async () => {
    const before = await api<Settlement>(request, "GET", `/settlements/outlet-settlements/${outletId}`);
    await api(request, "POST", `/settlements/outlet-settlements/${outletId}/reopen`, {
      version: before.version,
      reason: "Se recontó la caja de la boca",
    });
    const imported = await api<{ results: { outcome: string }[] }>(
      request,
      "POST",
      "/settlements/imports/store-sys/expected-amounts",
      {
        source: "STORE_SYS",
        client_request_id: id(),
        records: [
          {
            settlement_type: "OUTLET",
            business_date: D(3),
            revision: 2,
            external_reference: outletRef,
            outlet_code: outletCode,
            expected_amount: "610000.00",
          },
        ],
      },
    );
    expect(imported.results[0]).toMatchObject({ outcome: "PENDING_CONFIRMATION" });
    await api(request, "PUT", `/settlements/outlet-settlements/${outletId}/count`, {
      lines: [{ denomination_code: "ARS_10000", quantity: 11, destination_account_id: main }],
    });

    // Día 3 while it is open: the close in Cajas, never the draft.
    const view = await closeView(request, D(3));
    expectDay(view, main, [1000000, 550000, 1550000]);
    expect(view.settlements.items.find((s) => s.id === outletId)).toMatchObject({
      being_corrected: true,
      expected_update_pending: true,
      expected_amount: "600000.00",
      justified: "600000.00",
      difference: "0.00",
    });
    await page.goto(`/rendiciones/cierre-del-dia?fecha=${D(3)}`);
    await expect(page.getByTestId(`settlement-${outletId}`)).toContainText("En corrección");
    await noSideScroll(page);

    // Accepted by an admin on the boca's screen.
    await page.goto(`/rendiciones/boca/${outletId}`);
    const banner = page.getByTestId("expected-pending");
    await expect(banner).toContainText("Store Sys: $ 610.000,00");
    await banner.getByRole("button", { name: "Aceptar el de Store Sys" }).click();
    const accept = page.getByRole("dialog");
    await accept.getByRole("button", { name: "Aceptar", exact: true }).click();
    await expect(accept).toBeHidden();
    await expect(page.getByTestId("expected-origin")).toHaveText(`Origen: Store Sys · Ref. ${outletRef} rev. 2`);
  });

  await test.step("Correction: the new close reverses the previous one on Día 3", async () => {
    await page.getByRole("button", { name: "Cerrar rendición" }).click();
    const close = page.getByRole("dialog");
    await close.getByRole("button", { name: "Confirmar cierre" }).click();
    await expect(close).toBeHidden();
    await expect(page.getByTestId("financial-state")).toHaveText("Cerrada");

    const history = await api<{ closings: Closing[]; reopenings: unknown[] }>(
      request,
      "GET",
      `/settlements/outlet-settlements/${outletId}/history`,
    );
    expect(history.closings.filter((c) => c.in_effect)).toHaveLength(1);
    expect(history.closings).toHaveLength(2);
    const previous = history.closings.find((c) => c.operation_id === outletClose)!;
    expect(previous.in_effect).toBe(false);
    const current = history.closings.find((c) => c.in_effect)!;
    newOutletClose = current.operation_id!;
    expect(history.reopenings).toHaveLength(1);
    // The ledger links them: the previous close is reversed by an operation the new closing carries.
    const original = await api<Operation>(request, "GET", `/settlements/operations/${outletClose}`);
    expect(original.reversed_by_operation_id).toBe(current.reversal_operation_id);
    const reversal = await api<Operation>(
      request,
      "GET",
      `/settlements/operations/${original.reversed_by_operation_id}`,
    );
    expect(reversal).toMatchObject({ reverses_operation_id: outletClose, business_date: D(3) });
    const replacement = await api<Operation>(request, "GET", `/settlements/operations/${newOutletClose}`);
    expect(replacement.business_date).toBe(D(3));
    Object.assign(trace, { outletReversal: reversal.id, newOutletClose });

    const view = await closeView(request, D(3));
    expectDay(view, main, [1000000, 560000, 1560000]);
    expect(flows(accountOf(view, main), "inflow")).toEqual({
      OUTLET_REMITTANCE: [2, 450000],
      SETTLEMENT_CLOSE_OUTLET: [2, 210000],
    });
    expect(flows(accountOf(view, main), "outflow")).toEqual({ "REVERSAL:SETTLEMENT_CLOSE_OUTLET": [1, 100000] });
    expect(flows(accountOf(view, bank), "outflow")).toEqual({ "REVERSAL:SETTLEMENT_CLOSE_OUTLET": [1, 50000] });
    expect(view.settlements.items.find((s) => s.id === outletId)).toMatchObject({
      being_corrected: false,
      financial_state: "CLOSED",
      expected_amount: "610000.00",
      justified: "610000.00",
      difference: "0.00",
      expected_origin: { source: "STORE_SYS", revision: 2 },
    });
    await page.goto(`/rendiciones/cierre-del-dia?fecha=${D(3)}`);
    await expect(page.getByTestId(`account-${main}`)).toContainText("Reverso de cierres de rendición de boca");
  });

  // --- Every day: opening + net = closing, and each closing is the next opening --------------------------------
  await test.step("Cierre del día chains over the eight days, after the correction", async () => {
    const expected: Record<number, [number[] | null, number[] | null, number[] | null]> = {
      // [Caja principal, Caja de cambio, Banco]: opening, net, closing
      1: [[0, 500000, 500000], null, [0, 1000000, 1000000]],
      2: [
        [500000, 500000, 1000000],
        [0, 10000, 10000],
        [1000000, 200000, 1200000],
      ],
      3: [
        [1000000, 560000, 1560000],
        [10000, 0, 10000],
        [1200000, 50000, 1250000],
      ],
      4: [
        [1560000, -70000, 1490000],
        [10000, 0, 10000],
        [1250000, -70000, 1180000],
      ],
      5: [
        [1490000, 0, 1490000],
        [10000, 0, 10000],
        [1180000, 150000, 1330000],
      ],
      6: [
        [1490000, -70000, 1420000],
        [10000, 0, 10000],
        [1330000, -50000, 1280000],
      ],
      7: [
        [1420000, -300000, 1120000],
        [10000, 0, 10000],
        [1280000, -500000, 780000],
      ],
    };
    let previous: DailyClose | null = null;
    for (let n = 1; n <= 8; n++) {
      const view = await closeView(request, D(n));
      if (expected[n]) {
        const [m, c, b] = expected[n]!;
        expectDay(view, main, m!);
        expectDay(view, bank, b!);
        if (c) expectDay(view, change, c);
        else if (start[change] === 0) expect(accountOf(view, change)).toBeUndefined();
      }
      if (previous) {
        for (const a of view.accounts) {
          const yesterday = previous.accounts.find((p) => p.account_id === a.account_id);
          expect(cents(a.opening_balance), `${a.name} ${D(n)}`).toBe(yesterday ? cents(yesterday.closing_balance) : 0);
        }
      }
      previous = view;
    }
    // The balances of Cajas at the end of Día 7 are those of the day.
    const cajas = await api<Account[]>(request, "GET", `/settlements/accounts?on=${D(7)}`);
    expect(Number(cajas.find((a) => a.id === main)!.balance)).toBe(1120000 + start[main]!);
    expect(Number(cajas.find((a) => a.id === bank)!.balance)).toBe(780000 + start[bank]!);
  });

  // --- Traceability -------------------------------------------------------------------------------------------------
  await test.step("Traceability: Store Sys → … → payout → Caja/Banco", async () => {
    const settlement = await api<Settlement>(request, "GET", `/settlements/deliveries/${deliveryId}`);
    expect(settlement.expected_event_id).toBe(trace.storeSysEvent);
    expect(settlement.close_in_effect!.operation_id).toBe(deliveryClose);
    const close = await api<Operation>(request, "GET", `/settlements/operations/${deliveryClose}`);
    const employeeLeg = close.entries.find((e) => e.account_kind === "EMPLOYEE")!;
    const movements = await api<{ items: { operation_id: number; amount: Money; applied: string }[] }>(
      request,
      "GET",
      `${EF}/employees/${driver.id}/movements?limit=50`,
    );
    const shortage = movements.items.find((m) => m.operation_id === deliveryClose)!;
    expect(shortage.amount).toBe(employeeLeg.amount);
    const payroll = await api<{ closing: { operation_id: number; applications: { entry_id?: number }[] } }>(
      request,
      "GET",
      `${EF}/payrolls/${payrollId}`,
    );
    const payout = await api<{ operation_id: number; payroll_id: number }>(request, "GET", `${EF}/payouts/${payoutId}`);
    expect(payout.payroll_id).toBe(payrollId);
    const payoutOp = await api<Operation>(request, "GET", `/settlements/operations/${payout.operation_id}`);
    expect(payoutOp.entries.filter((e) => e.account_id === main || e.account_id === bank)).toHaveLength(2);
    Object.assign(trace, {
      delivery: deliveryId,
      deliveryEmployeeLeg: employeeLeg,
      shortageMovement: shortage,
      payrollClose: payroll.closing.operation_id,
      payrollApplications: payroll.closing.applications,
      cheque: chequeId,
      pending: pendingId,
      advance: advanceId,
      loan: loanId,
      outletSettlement: outletId,
    });
    const body = JSON.stringify(trace, null, 2);
    await testInfo.attach("trazabilidad.json", { body, contentType: "application/json" });
    // For the phase report: the real ids of this run, when asked for.
    if (process.env.R9_3_TRACE_DIR)
      writeFileSync(`${process.env.R9_3_TRACE_DIR}/trace-${testInfo.project.name}.json`, body);
  });
});
