import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ThirdParty } from "@/lib/api/third-parties";
import { todayIso } from "@/lib/format";
import type { Account, Operation } from "@/modules/settlements/cash/api";
import type { PaymentDetail } from "@/modules/settlements/payments/api";
import { paymentErrorView } from "@/modules/settlements/payments/errors";
import { PaymentDetailView } from "@/modules/settlements/payments/payment-detail";
import { paymentBalance } from "@/modules/settlements/payments/payment-dialog";
import { PaymentList } from "@/modules/settlements/payments/payment-list";
import { ApiError } from "@/lib/api/client";

import { cheque } from "./cheque-fixture";
import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const account = (id: number, name: string, kind: Account["kind"], changes: Partial<Account> = {}): Account => ({
  id,
  code: null,
  name,
  kind,
  allow_negative: false,
  active: true,
  balance: "0.00",
  overdrawn: false,
  overdrawn_label: null,
  ...changes,
});

const ACCOUNTS: Account[] = [
  account(1, "Caja principal", "CASH_BOX", { code: "main_cash", balance: "500000.00" }),
  account(2, "Caja vieja", "CASH_BOX", { active: false }),
  account(3, "Banco", "BANK", { code: "bank", balance: "900000.00" }),
  account(10, "Gastos operativos", "EXPENSE", { code: "expense", allow_negative: true }),
  account(12, "Pagos por mercadería", "MERCHANDISE_PAYMENTS", { code: "merchandise_payments", allow_negative: true }),
  account(13, "Devoluciones a terceros", "THIRD_PARTY_REFUNDS", { code: "third_party_refunds", allow_negative: true }),
  account(14, "Otros pagos", "OTHER_PAYMENTS", { code: "other_payments", allow_negative: true }),
  account(20, "Cartera de cheques", "CHEQUE_PORTFOLIO", { code: "cheque_portfolio" }),
];

/** The portfolio: two cheques the operator can deliver. */
const PORTFOLIO = [
  cheque(),
  cheque({
    id: 22,
    cheque_number: "00099999",
    amount: "50000.00",
    bank_name: "Banco Nación",
    payment_type: "DEFERRED",
  }),
];

const CATEGORIES = [
  { id: 1, name: "Peajes", active: true, code: null },
  { id: 3, name: "Combustible", active: true, code: "FUEL" },
];

const thirdParty = (id: number, name: string, changes: Partial<ThirdParty> = {}): ThirdParty => ({
  id,
  name,
  tax_id: null,
  tax_id_display: null,
  notes: null,
  active: true,
  created_at: "2026-10-09T10:00:00Z",
  created_by_name: "Admin",
  updated_at: "2026-10-09T10:00:00Z",
  updated_by_name: "Admin",
  ...changes,
});

const THIRD_PARTIES = [thirdParty(5, "Distribuidora X", { tax_id: "30712345671", tax_id_display: "30-71234567-1" })];

const OPERATION: Operation = {
  id: 60,
  type: "PAYMENT",
  business_date: "2026-10-09",
  description: "Pago n.º 8 · Pago por mercadería · Distribuidora X",
  created_by: 9,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-09T15:00:00Z",
  source_type: "payment",
  source_id: 8,
  reverses_operation_id: null,
  reversed_by_operation_id: null,
  entries: [
    { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-100000.00" },
    { account_id: 3, account_name: "Banco", account_kind: "BANK", amount: "-200000.00" },
    { account_id: 12, account_name: "Pagos por mercadería", account_kind: "MERCHANDISE_PAYMENTS", amount: "300000.00" },
  ],
};

const payment = (changes: Partial<PaymentDetail> = {}): PaymentDetail => ({
  id: 8,
  state: "IN_EFFECT",
  business_date: "2026-10-09",
  third_party_id: 5,
  third_party_name: "Distribuidora X",
  third_party_tax_id_display: "30-71234567-1",
  third_party_active: true,
  concept: "MERCHANDISE",
  expense_category_id: null,
  expense_category_name: null,
  counter_account_name: "Pagos por mercadería",
  amount: "300000.00",
  reference: "FC A 0001-00001234",
  description: null,
  items: [
    {
      id: 81,
      method: "CASH",
      account_id: 1,
      account_name: "Caja principal",
      account_kind: "CASH_BOX",
      cheque_id: null,
      cheque: null,
      amount: "100000.00",
      reference: null,
    },
    {
      id: 82,
      method: "TRANSFER",
      account_id: 3,
      account_name: "Banco",
      account_kind: "BANK",
      cheque_id: null,
      cheque: null,
      amount: "200000.00",
      reference: "TRF 99",
    },
  ],
  operation_id: 60,
  reversal_operation_id: null,
  created_by: 9,
  created_by_name: "Oscar Operador",
  created_at: "2026-10-09T15:00:00Z",
  reversed_by_name: null,
  reversed_at: null,
  reversal_reason: null,
  operation: OPERATION,
  reversal: null,
  history: [
    {
      id: 1,
      event_type: "created",
      occurred_at: "2026-10-09T15:00:00Z",
      user_name: "Oscar Operador",
      before: null,
      after: {},
    },
  ],
  ...changes,
});

type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;
type Role = "viewer" | "operator" | "admin";

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const handler = routes[`${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`];
    if (!handler) return jsonResponse(404, { code: "not_found", message: "x", details: null, request_id: null });
    return handler(init, url);
  });
}

const EMPTY_PAGE = { items: [], total: 0, amount_in_effect: "0.00" };

const routes = (role: Role, extra: Record<string, Handler> = {}): Record<string, Handler> => ({
  "GET /auth/me": () => jsonResponse(200, me(role)),
  "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
  "GET /settlements/expense-categories": () => jsonResponse(200, CATEGORIES),
  "GET /third-parties": () => jsonResponse(200, THIRD_PARTIES),
  "GET /settlements/payments": () => jsonResponse(200, EMPTY_PAGE),
  "GET /settlements/cheques": (_, url) =>
    url.searchParams.get("in_portfolio") === "true"
      ? jsonResponse(200, { items: PORTFOLIO, total: PORTFOLIO.length })
      : jsonResponse(400, { code: "bad_filter", message: "x", details: null, request_id: null }),
  ...extra,
});

const posts = (spy: ReturnType<typeof mockApi>, path: string) =>
  spy.mock.calls
    .filter(
      ([input, init]) => new URL(String(input), "http://l").pathname === `/api/v1${path}` && init?.method === "POST",
    )
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const apiError = (status: number, code: string, details: unknown = null) =>
  jsonResponse(status, { code, message: "Texto del backend.", details, request_id: "r" });

async function openPayment() {
  await userEvent.click(await screen.findByRole("button", { name: "Registrar pago" }));
  const dialog = screen.getByRole("dialog");
  await within(dialog).findByRole("option", { name: /Distribuidora X/ });
  return dialog;
}

async function fillMerchandise(dialog: HTMLElement) {
  await userEvent.selectOptions(within(dialog).getByLabelText("Tercero"), "5");
  await userEvent.selectOptions(within(dialog).getByLabelText("Concepto"), "MERCHANDISE");
  await userEvent.type(within(dialog).getByLabelText("Total del pago"), "300.000");
  await userEvent.selectOptions(within(dialog).getByLabelText("Caja"), "1");
  await userEvent.type(within(dialog).getByLabelText("Importe del medio 1"), "100.000");
}

describe("payment balance", () => {
  it("shows what is applied, missing and over, exactly in cents", () => {
    expect(paymentBalance("300.000", [{ amount: "100.000" }])).toMatchObject({
      total: "300000.00",
      applied: "100000.00",
      missing: "200000.00",
      over: null,
      matches: false,
    });
    expect(paymentBalance("0,30", [{ amount: "0,10" }, { amount: "0,20" }])).toMatchObject({ matches: true });
    expect(paymentBalance("100", [{ amount: "100,01" }])).toMatchObject({ over: "0.01", matches: false });
    expect(paymentBalance("", [{ amount: "5" }])).toMatchObject({ total: null, matches: false });
  });
});

describe("Pagos (R6-1)", () => {
  it("a viewer reads the list but cannot register a payment", async () => {
    mockApi(
      routes("viewer", {
        "GET /settlements/payments": () =>
          jsonResponse(200, { items: [payment()], total: 1, amount_in_effect: "300000.00" }),
      }),
    );
    renderWithQuery(<PaymentList />);
    expect(await screen.findByTestId("payments-summary")).toHaveTextContent("1 pago · vigentes por $ 300.000,00");
    expect(screen.getAllByText("Caja principal $ 100.000,00 · Banco $ 200.000,00")[0]).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar pago" })).not.toBeInTheDocument();
  });

  it("offers cash, transfers and cheques, OTHER only to an admin, and never fuel as a category", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    const methods = within(within(dialog).getByLabelText("Medio de pago")).getAllByRole("option");
    expect(methods.map((o) => o.textContent)).toEqual(["Efectivo", "Transferencia", "Cheque"]);
    const concepts = within(within(dialog).getByLabelText("Concepto")).getAllByRole("option");
    expect(concepts.map((o) => o.textContent)).toEqual([
      "Elegí el concepto…",
      "Pago por mercadería",
      "Pago de gasto",
      "Devolución",
    ]);
    expect(
      within(within(dialog).getByLabelText("Caja"))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Elegí la caja…", "Caja principal · saldo $ 500.000,00"]);
    await userEvent.selectOptions(within(dialog).getByLabelText("Concepto"), "EXPENSE");
    await waitFor(() =>
      expect(
        within(within(dialog).getByLabelText("Categoría del gasto"))
          .getAllByRole("option")
          .map((o) => o.textContent),
      ).toEqual(["Elegí una categoría…", "Peajes"]),
    );
  });

  it("an admin also has «Otro pago», which requires a description", async () => {
    const spy = mockApi(routes("admin"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.selectOptions(within(dialog).getByLabelText("Tercero"), "5");
    await userEvent.selectOptions(within(dialog).getByLabelText("Concepto"), "OTHER");
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "1000");
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja"), "1");
    await userEvent.type(within(dialog).getByLabelText("Importe del medio 1"), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("En «Otro pago» la descripción es obligatoria.");
    expect(posts(spy, "/settlements/payments")).toHaveLength(0);
  });

  it("does not reach the confirmation until the means add up exactly to the total", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await fillMerchandise(dialog);
    const balance = within(dialog).getByTestId("payment-balance");
    expect(balance).toHaveTextContent("Total$ 300.000,00");
    expect(balance).toHaveTextContent("Aplicado$ 100.000,00");
    expect(balance).toHaveTextContent("Falta$ 200.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Los medios tienen que sumar exactamente el total.");

    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio de pago" }));
    await userEvent.selectOptions(within(dialog).getByLabelText("Banco"), "3");
    await userEvent.type(within(dialog).getByLabelText("Importe del medio 2"), "250.000");
    expect(balance).toHaveTextContent("Sobra$ 50.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(screen.queryByTestId("payment-confirmation")).not.toBeInTheDocument();
  });

  it("refuses the same cash box twice in one payment", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await fillMerchandise(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio de pago" }));
    await userEvent.selectOptions(within(dialog).getAllByLabelText("Medio de pago")[1], "CASH");
    await userEvent.selectOptions(within(dialog).getAllByLabelText("Caja")[1], "1");
    await userEvent.type(within(dialog).getByLabelText("Importe del medio 2"), "200.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Cada caja o banco va una sola vez");
  });

  it("lists every leg before sending and sends only after «Confirmar pago»", async () => {
    const spy = mockApi(routes("operator", { "POST /settlements/payments": () => jsonResponse(201, payment()) }));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await fillMerchandise(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio de pago" }));
    expect(within(dialog).getAllByLabelText("Medio de pago")[1]).toHaveValue("TRANSFER");
    await userEvent.selectOptions(within(dialog).getByLabelText("Banco"), "3");
    await userEvent.type(within(dialog).getByLabelText("Importe del medio 2"), "200.000");
    await userEvent.type(within(dialog).getByLabelText("Referencia del medio 2 (opcional)"), "TRF 99");
    await userEvent.type(within(dialog).getByLabelText("Referencia (opcional)"), "FC A 0001-00001234");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));

    const confirmation = await screen.findByTestId("payment-confirmation");
    expect(confirmation).toHaveTextContent("Pago por mercadería a Distribuidora X");
    expect(confirmation).toHaveTextContent("Caja principal-$ 100.000,00");
    expect(confirmation).toHaveTextContent("Banco-$ 200.000,00");
    expect(confirmation).toHaveTextContent("Pagos por mercadería+$ 300.000,00");
    expect(confirmation).toHaveTextContent(`Fecha${todayIso().split("-").reverse().join("/")}`);
    expect(posts(spy, "/settlements/payments")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Confirmar pago" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments")).toHaveLength(1));
    const [body] = posts(spy, "/settlements/payments");
    expect(body).toMatchObject({
      business_date: todayIso(),
      third_party_id: 5,
      concept: "MERCHANDISE",
      expense_category_id: null,
      amount: "300000.00",
      reference: "FC A 0001-00001234",
      items: [
        { method: "CASH", account_id: 1, amount: "100000.00", reference: null },
        { method: "TRANSFER", account_id: 3, amount: "200000.00", reference: "TRF 99" },
      ],
    });
    expect(typeof body.client_request_id).toBe("string");
    expect(await screen.findByRole("status")).toHaveTextContent("Pago n.º 8 registrado por $ 300.000,00.");
  });

  it("insufficient funds names the cash box and keeps the confirmation open", async () => {
    mockApi(
      routes("operator", {
        "POST /settlements/payments": () =>
          apiError(422, "insufficient_funds", { account_id: 1, business_date: todayIso(), balance: "50000.00" }),
      }),
    );
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await fillMerchandise(dialog);
    await userEvent.clear(within(dialog).getByLabelText("Total del pago"));
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "100.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirmar pago" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No hay saldo suficiente en Caja principal para realizar este movimiento.",
    );
  });

  it("an unknown outcome keeps the attempt and retries it with the same request id", async () => {
    let calls = 0;
    const spy = mockApi(
      routes("operator", {
        "POST /settlements/payments": () => (++calls === 1 ? jsonResponse(502, {}) : jsonResponse(200, payment())),
      }),
    );
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await fillMerchandise(dialog);
    await userEvent.clear(within(dialog).getByLabelText("Total del pago"));
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "100.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirmar pago" }));
    await userEvent.click(await screen.findByRole("button", { name: "Reintentar la misma operación" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments")).toHaveLength(2));
    const [first, second] = posts(spy, "/settlements/payments");
    expect(second.client_request_id).toBe(first.client_request_id);
  });

  it("registers a new third party on the spot and offers the homonym instead of duplicating it", async () => {
    let attempt = 0;
    const spy = mockApi(
      routes("operator", {
        "POST /settlements/third-parties": () =>
          ++attempt === 1
            ? apiError(409, "third_party_possible_duplicate", {
                candidates: [{ third_party_id: 5, name: "Distribuidora X", tax_id_display: null, active: true }],
              })
            : jsonResponse(201, thirdParty(6, "Distribuidora X")),
      }),
    );
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.click(within(dialog).getByRole("button", { name: "Nuevo tercero" }));
    const form = screen.getByRole("dialog");
    await userEvent.type(within(form).getByLabelText("Nombre o razón social"), "distribuidora x");
    await userEvent.click(within(form).getByRole("button", { name: "Registrar y usar" }));
    expect(await within(form).findByText(/Ya hay un tercero con el nombre/)).toBeInTheDocument();

    await userEvent.click(within(form).getByRole("button", { name: "Es otro tercero: registrarlo" }));
    expect(within(form).getByText("Indicá por qué es otro tercero.")).toBeInTheDocument();
    expect(posts(spy, "/settlements/third-parties")).toHaveLength(1);

    await userEvent.click(within(form).getByRole("button", { name: "Usar este" }));
    expect(within(screen.getByRole("dialog")).getByLabelText("Tercero")).toHaveValue("5");
    expect(posts(spy, "/settlements/third-parties")).toHaveLength(1);
  });

  it("a confirmed homonym is sent with its reason and selected in the payment", async () => {
    let attempt = 0;
    const spy = mockApi(
      routes("operator", {
        "POST /settlements/third-parties": () =>
          ++attempt === 1
            ? apiError(409, "third_party_possible_duplicate", {
                candidates: [{ third_party_id: 5, name: "Distribuidora X", tax_id_display: null, active: true }],
              })
            : jsonResponse(201, thirdParty(6, "Distribuidora X", { notes: "Sucursal sur" })),
      }),
    );
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.click(within(dialog).getByRole("button", { name: "Nuevo tercero" }));
    const form = screen.getByRole("dialog");
    await userEvent.type(within(form).getByLabelText("Nombre o razón social"), "Distribuidora X");
    await userEvent.click(within(form).getByRole("button", { name: "Registrar y usar" }));
    await userEvent.type(await within(form).findByLabelText("Si es otro tercero, indicá por qué"), "Otra sucursal");
    await userEvent.click(within(form).getByRole("button", { name: "Es otro tercero: registrarlo" }));
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByLabelText("Tercero")).toHaveValue("6"));
    expect(posts(spy, "/settlements/third-parties")[1]).toMatchObject({
      name: "Distribuidora X",
      confirm_not_duplicate: true,
      duplicate_reason: "Otra sucursal",
    });
  });

  it("the CUIT check digit is validated before sending", async () => {
    const spy = mockApi(routes("operator"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.click(within(dialog).getByRole("button", { name: "Nuevo tercero" }));
    const form = screen.getByRole("dialog");
    await userEvent.type(within(form).getByLabelText("Nombre o razón social"), "Proveedor nuevo");
    await userEvent.type(within(form).getByLabelText("CUIT (opcional)"), "30-71234567-2");
    await userEvent.click(within(form).getByRole("button", { name: "Registrar y usar" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("revisá el dígito verificador");
    expect(posts(spy, "/settlements/third-parties")).toHaveLength(0);
  });
});

describe("payment detail", () => {
  it("shows the means, the ledger legs and, to an admin, reverse and correct", async () => {
    mockApi(routes("admin", { "GET /settlements/payments/8": () => jsonResponse(200, payment()) }));
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    expect(await screen.findByRole("heading", { name: "Pago n.º 8" })).toBeInTheDocument();
    expect(screen.getByTestId("payment-items")).toHaveTextContent("Transferencia · BancoRef. TRF 99$ 200.000,00");
    expect(screen.getByText("Pagos por mercadería", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Operación n.º 60" })).toHaveAttribute(
      "href",
      "/rendiciones/cajas/operaciones/60",
    );
    expect(screen.getByRole("link", { name: "Distribuidora X" })).toHaveAttribute("href", "/terceros/5");
    expect(screen.getByRole("button", { name: "Revertir pago" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Corregir referencias" })).toBeInTheDocument();
  });

  it("an operator cannot reverse or correct", async () => {
    mockApi(routes("operator", { "GET /settlements/payments/8": () => jsonResponse(200, payment()) }));
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await screen.findByRole("heading", { name: "Pago n.º 8" });
    expect(screen.queryByRole("button", { name: "Revertir pago" })).not.toBeInTheDocument();
  });

  it("reverses the whole payment with a reason", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/payments/8": () => jsonResponse(200, payment()),
        "POST /settlements/payments/8/reverse": () => jsonResponse(200, payment({ state: "REVERSED" })),
      }),
    );
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir pago" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("vuelven $ 100.000,00 a Caja principal, $ 200.000,00 a Banco");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Pago duplicado");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir pago" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments/8/reverse")).toHaveLength(1));
    expect(posts(spy, "/settlements/payments/8/reverse")[0]).toMatchObject({ reason: "Pago duplicado" });
  });

  it("corrects only what changed: an item reference, with a reason", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/payments/8": () => jsonResponse(200, payment()),
        "POST /settlements/payments/8/descriptive": () => jsonResponse(200, payment()),
      }),
    );
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await userEvent.click(await screen.findByRole("button", { name: "Corregir referencias" }));
    const dialog = screen.getByRole("dialog");
    const field = within(dialog).getByLabelText("Referencia de transferencia · Banco");
    await userEvent.clear(field);
    await userEvent.type(field, "TRF 100");
    await userEvent.type(within(dialog).getByLabelText("Motivo de la corrección"), "Número mal copiado");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments/8/descriptive")).toHaveLength(1));
    expect(posts(spy, "/settlements/payments/8/descriptive")[0]).toEqual({
      reason: "Número mal copiado",
      items: [{ id: 82, reference: "TRF 100" }],
    });
  });
});

/** A payment of $150.000: $50.000 in cash and the cheque n.º 00012345 of $100.000 from the portfolio. */
const chequePayment = (changes: Partial<PaymentDetail> = {}, recovered = false): PaymentDetail =>
  payment({
    amount: "150000.00",
    items: [
      {
        id: 81,
        method: "CASH",
        account_id: 1,
        account_name: "Caja principal",
        account_kind: "CASH_BOX",
        cheque_id: null,
        cheque: null,
        amount: "50000.00",
        reference: null,
      },
      {
        id: 83,
        method: "CHEQUE",
        account_id: null,
        account_name: null,
        account_kind: null,
        cheque_id: 21,
        cheque: {
          cheque_id: 21,
          kind: "PHYSICAL",
          payment_type: "COMMON",
          cheque_number: "00012345",
          bank_name: "Banco de Galicia",
          drawer_name: "Don José SRL",
          delivered_by_name: "Almacén Don José",
          payable_from: "2026-10-01",
          state: recovered ? "IN_PORTFOLIO" : "DELIVERED",
          state_label: recovered ? "En cartera" : "Entregado",
          recovered_at: recovered ? "2026-10-10T12:00:00Z" : null,
          recovered_by_name: recovered ? "Ada Admin" : null,
        },
        amount: "100000.00",
        reference: "Recibo 7",
      },
    ],
    operation: {
      ...OPERATION,
      entries: [
        { account_id: 1, account_name: "Caja principal", account_kind: "CASH_BOX", amount: "-50000.00" },
        {
          account_id: 20,
          account_name: "Cartera de cheques",
          account_kind: "CHEQUE_PORTFOLIO",
          amount: "-100000.00",
          cheque_id: 21,
        },
        {
          account_id: 12,
          account_name: "Pagos por mercadería",
          account_kind: "MERCHANDISE_PAYMENTS",
          amount: "150000.00",
        },
      ],
    },
    ...changes,
  });

describe("Cheques en pagos (R6-2)", () => {
  it("offers only cheques of the portfolio, each once, with its amount locked", async () => {
    const spy = mockApi(routes("operator"));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.selectOptions(within(dialog).getByLabelText("Medio de pago"), "CHEQUE");
    expect(within(dialog).queryByLabelText("Caja")).not.toBeInTheDocument();
    const select = within(dialog).getByLabelText("Cheque de la cartera");
    await within(select).findByRole("option", { name: /00012345/ });
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      "Elegí el cheque…",
      "N.º 00012345 · Banco de Galicia · Físico común · $ 100.000,00",
      "N.º 00099999 · Banco Nación · Físico diferido · $ 50.000,00",
    ]);
    const requested = spy.mock.calls
      .map(([input]) => new URL(String(input), "http://l"))
      .filter((url) => url.pathname === "/api/v1/settlements/cheques");
    expect(requested.every((url) => url.searchParams.get("in_portfolio") === "true")).toBe(true);

    await userEvent.selectOptions(select, "21");
    const amount = within(dialog).getByLabelText("Importe del medio 1");
    expect(amount).toHaveValue("$ 100.000,00");
    expect(amount).toHaveAttribute("readonly");
    expect(within(dialog).getByTestId("payment-cheque-0")).toHaveTextContent("Lo entregóAlmacén Don José");
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "150.000");
    expect(within(dialog).getByTestId("payment-balance")).toHaveTextContent("Aplicado$ 100.000,00");
    expect(within(dialog).getByTestId("payment-balance")).toHaveTextContent("Falta$ 50.000,00");

    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio de pago" }));
    await userEvent.selectOptions(within(dialog).getAllByLabelText("Medio de pago")[1], "CHEQUE");
    const second = within(dialog).getAllByLabelText("Cheque de la cartera")[1];
    expect(within(second).queryByRole("option", { name: /00012345/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(second, "22");
    expect(within(dialog).getByTestId("payment-balance")).toHaveTextContent("Aplicado$ 150.000,00");
  });

  it("mixes cash and a cheque: the confirmation names the cheque leaving the portfolio and sends its whole amount", async () => {
    const spy = mockApi(routes("operator", { "POST /settlements/payments": () => jsonResponse(201, chequePayment()) }));
    renderWithQuery(<PaymentList />);
    const dialog = await openPayment();
    await userEvent.selectOptions(within(dialog).getByLabelText("Tercero"), "5");
    await userEvent.selectOptions(within(dialog).getByLabelText("Concepto"), "MERCHANDISE");
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "150.000");
    await userEvent.selectOptions(within(dialog).getByLabelText("Caja"), "1");
    await userEvent.type(within(dialog).getByLabelText("Importe del medio 1"), "50.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar medio de pago" }));
    await userEvent.selectOptions(within(dialog).getAllByLabelText("Medio de pago")[1], "CHEQUE");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Elegí el cheque del medio 2.");
    await userEvent.selectOptions(within(dialog).getByLabelText("Cheque de la cartera"), "21");
    await userEvent.type(within(dialog).getByLabelText("Referencia del medio 2 (opcional)"), "Recibo 7");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));

    const confirmation = await screen.findByTestId("payment-confirmation");
    expect(confirmation).toHaveTextContent("Caja principal-$ 50.000,00");
    expect(confirmation).toHaveTextContent("Cheque n.º 00012345 desde Cartera de cheques-$ 100.000,00");
    expect(confirmation).toHaveTextContent("Pagos por mercadería+$ 150.000,00");
    await userEvent.click(screen.getByRole("button", { name: "Confirmar pago" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments")).toHaveLength(1));
    const [body] = posts(spy, "/settlements/payments");
    expect(body.amount).toBe("150000.00");
    expect(body.items).toEqual([
      { method: "CASH", account_id: 1, amount: "50000.00", reference: null },
      { method: "CHEQUE", cheque_id: 21, amount: "100000.00", reference: "Recibo 7" },
    ]);
  });

  it("«Entregar» opens «Registrar pago» with the cheque preloaded; the user still completes the rest", async () => {
    mockApi(routes("operator"));
    renderWithQuery(<PaymentList initialCheque={22} />);
    const dialog = await screen.findByRole("dialog", { name: "Registrar pago" });
    expect(within(dialog).getByLabelText("Medio de pago")).toHaveValue("CHEQUE");
    await waitFor(() => expect(within(dialog).getByLabelText("Cheque de la cartera")).toHaveValue("22"));
    expect(within(dialog).getByLabelText("Importe del medio 1")).toHaveValue("$ 50.000,00");
    expect(within(dialog).getByLabelText("Tercero")).toHaveValue("");
    expect(within(dialog).getByLabelText("Concepto")).toHaveValue("");
  });

  it("a cheque that left the portfolio meanwhile is not offered and cannot be sent", async () => {
    const spy = mockApi(routes("operator"));
    renderWithQuery(<PaymentList initialCheque={99} />);
    const dialog = await screen.findByRole("dialog", { name: "Registrar pago" });
    await within(within(dialog).getByLabelText("Cheque de la cartera")).findByRole("option", { name: /00012345/ });
    await userEvent.selectOptions(within(dialog).getByLabelText("Tercero"), "5");
    await userEvent.selectOptions(within(dialog).getByLabelText("Concepto"), "MERCHANDISE");
    await userEvent.type(within(dialog).getByLabelText("Total del pago"), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("ya no está en cartera");
    expect(posts(spy, "/settlements/payments")).toHaveLength(0);
  });

  it("a viewer gets no «Registrar pago» even from «Entregar»", async () => {
    mockApi(routes("viewer"));
    renderWithQuery(<PaymentList initialCheque={21} />);
    await screen.findByRole("heading", { name: "Pagos" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("the detail shows each cheque with its link, state and its leg on the portfolio", async () => {
    mockApi(routes("admin", { "GET /settlements/payments/8": () => jsonResponse(200, chequePayment()) }));
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await screen.findByRole("heading", { name: "Pago n.º 8" });
    expect(screen.getByRole("link", { name: "n.º 00012345" })).toHaveAttribute("href", "/rendiciones/cheques/21");
    expect(screen.getByTestId("payment-cheque-state-21")).toHaveTextContent("Entregado");
    expect(screen.getByText("Cartera de cheques · Cheque n.º 00012345")).toBeInTheDocument();
  });

  it("reverses only with every cheque confirmed as recovered, one by one", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/payments/8": () => jsonResponse(200, chequePayment()),
        "POST /settlements/payments/8/reverse": () => jsonResponse(200, chequePayment({ state: "REVERSED" }, true)),
      }),
    );
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir pago" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("el cheque n.º 00012345 ($ 100.000,00) a Cartera de cheques");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Pago equivocado");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir pago" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Confirmá que recuperaste cada cheque");
    expect(posts(spy, "/settlements/payments/8/reverse")).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Recuperé el cheque n.º 00012345/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir pago" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments/8/reverse")).toHaveLength(1));
    expect(posts(spy, "/settlements/payments/8/reverse")[0]).toMatchObject({
      reason: "Pago equivocado",
      recovered_cheque_ids: [21],
    });
  });

  it("a payment without cheques asks for no recovery", async () => {
    const spy = mockApi(
      routes("admin", {
        "GET /settlements/payments/8": () => jsonResponse(200, payment()),
        "POST /settlements/payments/8/reverse": () => jsonResponse(200, payment({ state: "REVERSED" })),
      }),
    );
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    await userEvent.click(await screen.findByRole("button", { name: "Revertir pago" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByTestId("recovered-cheques")).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Duplicado");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir pago" }));
    await waitFor(() => expect(posts(spy, "/settlements/payments/8/reverse")).toHaveLength(1));
    expect(posts(spy, "/settlements/payments/8/reverse")[0]).toMatchObject({ recovered_cheque_ids: [] });
  });

  it("after the reversal the cheque reads «recuperado y devuelto a Cartera»", async () => {
    mockApi(
      routes("admin", {
        "GET /settlements/payments/8": () => jsonResponse(200, chequePayment({ state: "REVERSED" }, true)),
      }),
    );
    renderWithQuery(<PaymentDetailView paymentId={8} />);
    expect(await screen.findByTestId("payment-cheque-recovered-21")).toHaveTextContent(
      "Cheque recuperado y devuelto a Cartera · Ada Admin",
    );
    expect(screen.getByTestId("payment-cheque-state-21")).toHaveTextContent("En cartera");
    expect(screen.getByRole("link", { name: "Ver cheque" })).toHaveAttribute("href", "/rendiciones/cheques/21");
  });
});

describe("payment errors", () => {
  it("reads idempotency conflicts and unknown outcomes for the user", () => {
    const conflict = new ApiError(409, { code: "idempotency_conflict", message: "x", details: null, request_id: null });
    expect(paymentErrorView(conflict)).toMatchObject({ conflict: true });
    expect(
      paymentErrorView(new ApiError(502, { code: "x", message: "", details: null, request_id: null })),
    ).toMatchObject({ uncertain: true });
    const missing = new ApiError(409, {
      code: "cheque_recovery_required",
      message: "Para revertir el pago confirmá que recuperaste cada cheque: falta el n.º 00012345.",
      details: { missing_cheque_ids: [21] },
      request_id: null,
    });
    expect(paymentErrorView(missing).message).toBe(
      "Para revertir el pago confirmá que recuperaste cada cheque: falta el n.º 00012345.",
    );
  });
});
