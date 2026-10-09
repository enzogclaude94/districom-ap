import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { todayIso } from "@/lib/format";
import type { Cheque, ChequeDetail } from "@/modules/settlements/cheques/api";
import { ChequeDetailView } from "@/modules/settlements/cheques/cheque-detail";
import { ChequeList } from "@/modules/settlements/cheques/cheque-list";
import { chequeErrorView } from "@/modules/settlements/cheques/errors";
import { depositBlocker, expiringText } from "@/modules/settlements/cheques/labels";
import { SettlementCheques } from "@/modules/settlements/cheques/settlement-cheques";
import type { Settlement } from "@/modules/settlements/delivery/api";

import { jsonResponse, renderWithQuery } from "./render";

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => search.params,
}));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, call: number, url: URL) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

function shift(days: number) {
  const date = new Date(`${todayIso()}T12:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function cheque(changes: Partial<Cheque> = {}): Cheque {
  return {
    id: 21,
    kind: "PHYSICAL",
    payment_type: "COMMON",
    amount: "100000.00",
    issuer_bank_id: 7,
    bank_bcra_code: 7,
    bank_name: "Banco de Galicia",
    branch_code: "001",
    account_ref: null,
    cheque_number: "00012345",
    echeq_external_id: null,
    identity_complete: true,
    issue_date: shift(-5),
    payment_date: null,
    payable_from: shift(-5),
    presentation_deadline: shift(25),
    presentation_expired: false,
    expires_in_days: 25,
    expiring_soon: false,
    location_label: "En Cartera de cheques",
    deposit_id: null,
    deposit_business_date: null,
    deposit_bank_account_id: null,
    deposit_bank_account_name: null,
    credit_id: null,
    credit_business_date: null,
    drawer_name: "Don José SRL",
    drawer_tax_id: null,
    delivered_by_name: "Almacén Don José",
    origin_type: "SETTLEMENT",
    settlement_id: 7,
    origin_pending_id: null,
    received_business_date: shift(-5),
    received_by_employee_id: 4,
    received_by_name: "Pedro Gómez",
    physical_receipt_confirmed_by_name: "Oscar Operador",
    physical_receipt_confirmed_at: "2026-10-04T12:00:00Z",
    verification_status: null,
    verification_changed_by_name: null,
    verification_changed_at: null,
    verification_reason: null,
    notes: null,
    state: "IN_PORTFOLIO",
    state_label: "En cartera",
    published: true,
    in_effect: true,
    load_change: null,
    load_change_detail: null,
    created_by_name: "Oscar Operador",
    created_at: "2026-10-04T12:00:00Z",
    voided: false,
    voided_by_name: null,
    voided_at: null,
    void_reason: null,
    ...changes,
  };
}

function detail(changes: Partial<ChequeDetail> = {}): ChequeDetail {
  return {
    ...cheque(),
    events: [
      {
        id: 1,
        event_type: "RECEIVED",
        before: null,
        after: null,
        reason: null,
        settlement_id: 7,
        settlement_version: 1,
        close_operation_id: null,
        operation_id: null,
        created_by_name: "Oscar Operador",
        created_at: "2026-10-04T12:00:00Z",
      },
      {
        id: 2,
        event_type: "PUBLISHED",
        before: null,
        after: null,
        reason: null,
        settlement_id: 7,
        settlement_version: 1,
        close_operation_id: 400,
        operation_id: 400,
        created_by_name: "Oscar Operador",
        created_at: "2026-10-04T13:00:00Z",
      },
    ],
    closings: [],
    entry_operation_id: 400,
    documents: [],
    ...changes,
  };
}

const ACCOUNTS = [
  {
    id: 1,
    code: "main_cash",
    name: "Caja principal",
    kind: "CASH_BOX",
    allow_negative: false,
    active: true,
    balance: "0.00",
    overdrawn: false,
    overdrawn_label: null,
  },
  {
    id: 3,
    code: "bank",
    name: "Banco Nación cuenta corriente",
    kind: "BANK",
    allow_negative: true,
    active: true,
    balance: "0.00",
    overdrawn: false,
    overdrawn_label: null,
  },
];

function mockApi(routes: Record<string, Handler>) {
  const counts = new Map<string, number>();
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
    const call = (counts.get(key) ?? 0) + 1;
    counts.set(key, call);
    return handler(init, call, url);
  });
}

type Spy = ReturnType<typeof mockApi>;
const calls = (spy: Spy, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) =>
      new URL(String(input), "http://localhost").pathname === `/api/v1${path}` && (init?.method ?? "GET") === method,
  );
const bodies = (spy: Spy, method: string, path: string) =>
  calls(spy, method, path).map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const base = (role: Role, extra: Record<string, Handler> = {}): Record<string, Handler> => ({
  "GET /auth/me": () => jsonResponse(200, me(role)),
  "GET /financial-institutions": () =>
    jsonResponse(200, [{ id: 7, bcra_code: 7, name: "Banco de Galicia", source: "BCRA", active: true }]),
  "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
  "GET /settlements/employees": () =>
    jsonResponse(200, [{ id: 4, full_name: "Pedro Gómez", active: true, functions: [] }]),
  ...extra,
});

describe("expiry rules shown to the user", () => {
  it("does not deposit before it is payable nor after its last presentation day", () => {
    const today = todayIso();
    expect(depositBlocker(cheque(), today)).toBeNull();
    expect(depositBlocker(cheque({ payable_from: shift(10) }), today)).toMatch(/^Disponible para depósito desde el /);
    expect(depositBlocker(cheque({ presentation_expired: true }), today)).toMatch(/^Vencido: se podía presentar hasta/);
    expect(depositBlocker(cheque({ state: "DEPOSITED" }), today)).toBe("No está en cartera.");
  });

  it("says «Vence en N días» only when it is close", () => {
    expect(expiringText(cheque())).toBeNull();
    expect(expiringText(cheque({ expiring_soon: true, expires_in_days: 3 }))).toBe("Vence en 3 días");
    expect(expiringText(cheque({ expiring_soon: true, expires_in_days: 1 }))).toBe("Vence mañana");
    expect(expiringText(cheque({ expiring_soon: true, expires_in_days: 0 }))).toBe("Vence hoy");
  });

  it("explains cheque_has_activity and pending_has_activity without offering an adjustment", () => {
    const err = (code: string) => new ApiError(409, { code, message: "x", details: null, request_id: null });
    expect(chequeErrorView(err("cheque_has_activity")).message).toContain("primero hay que revertir los posteriores");
    expect(chequeErrorView(err("pending_has_activity")).message).toContain("No se registró ningún ajuste");
  });
});

describe("cheque list", () => {
  it("lists the cheques, sends the filters to the backend and paginates there", async () => {
    search.params = new URLSearchParams("state=IN_PORTFOLIO");
    const spy = mockApi(
      base("viewer", {
        "GET /settlements/cheques": (_i, _c, url) =>
          jsonResponse(200, {
            items: [
              cheque({ expiring_soon: true, expires_in_days: 3 }),
              cheque({ id: 22, presentation_expired: true }),
            ],
            total: 45,
            _offset: url.searchParams.get("offset"),
          }),
      }),
    );
    renderWithQuery(<ChequeList />);
    const row = await screen.findByTestId("cheque-21");
    expect(row).toHaveTextContent("N.º 00012345");
    expect(row).toHaveTextContent("Físico · Común · Banco de Galicia");
    expect(row).toHaveTextContent("Don José SRL");
    expect(row).toHaveTextContent("Almacén Don José");
    expect(row).toHaveTextContent("Rendición");
    expect(row).toHaveTextContent("En Cartera de cheques");
    expect(within(row).getByTestId("expiring-21")).toHaveTextContent("Vence en 3 días");
    expect(within(screen.getByTestId("cheque-22")).getByTestId("expired-22")).toHaveTextContent("Vencido");
    const urls = () => calls(spy, "GET", "/settlements/cheques").map(([u]) => new URL(String(u), "http://localhost"));
    expect(urls()[0].searchParams.get("state")).toBe("IN_PORTFOLIO");

    await userEvent.click(screen.getByRole("checkbox", { name: "Vencen pronto" }));
    await waitFor(() => expect(urls().at(-1)!.searchParams.get("expiring_soon")).toBe("true"));
    await userEvent.selectOptions(screen.getByLabelText("Pagadero"), "no");
    await waitFor(() => expect(urls().at(-1)!.searchParams.get("payable")).toBe("false"));
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(urls().at(-1)!.searchParams.get("offset")).toBe("20"));
    // A viewer reads only: nothing to select.
    expect(screen.queryByRole("checkbox", { name: /Seleccionar el cheque/ })).not.toBeInTheDocument();
    search.params = new URLSearchParams();
  });

  it("deposits several cheques after a summary; expired or not-yet-payable ones cannot be chosen", async () => {
    const items = [
      cheque(),
      cheque({ id: 23, cheque_number: "00099999", amount: "50000.00" }),
      cheque({ id: 22, cheque_number: "00055555", presentation_expired: true, presentation_deadline: shift(-1) }),
      cheque({ id: 24, cheque_number: "00077777", payable_from: shift(10), payment_type: "DEFERRED" }),
    ];
    const spy = mockApi(
      base("operator", {
        "GET /settlements/cheques": () => jsonResponse(200, { items, total: 4 }),
        "POST /settlements/cheque-deposits": () =>
          jsonResponse(201, {
            id: 5,
            bank_account_name: "Banco Nación cuenta corriente",
            total: "150000.00",
            items: [{}, {}],
          }),
      }),
    );
    renderWithQuery(<ChequeList />);
    await screen.findByTestId("cheque-21");
    const box = (n: string) =>
      within(screen.getByTestId(`cheque-${n}`)).getByRole("checkbox", { name: /Seleccionar el cheque/ });
    expect(box("22")).toBeDisabled();
    expect(screen.getByTestId("cheque-22")).toHaveTextContent("Vencido: se podía presentar hasta");
    expect(box("24")).toBeDisabled();
    expect(screen.getByTestId("cheque-24")).toHaveTextContent("Disponible para depósito desde el");
    await userEvent.click(box("21"));
    await userEvent.click(box("23"));
    await userEvent.click(screen.getByRole("button", { name: "Crear boleta de depósito" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear boleta de depósito" });
    expect(within(dialog).getByTestId("cycle-total")).toHaveTextContent("$ 150.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Elegí el banco destino.");
    await userEvent.selectOptions(within(dialog).getByLabelText("Banco destino"), "3");
    await userEvent.type(within(dialog).getByLabelText("Número de boleta (opcional)"), "B-77");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    const review = within(dialog).getByRole("group", { name: "Revisá antes de confirmar" });
    expect(review).toHaveTextContent("Cartera de cheques−$ 150.000,00");
    expect(review).toHaveTextContent("Saldo del bancoSin cambio hasta la acreditación");
    expect(calls(spy, "POST", "/settlements/cheque-deposits")).toHaveLength(0);
    await userEvent.dblClick(within(dialog).getByRole("button", { name: "Confirmar depósito" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Boleta de depósito registrada");
    const sent = bodies(spy, "POST", "/settlements/cheque-deposits");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ bank_account_id: 3, cheque_ids: [21, 23], slip_number: "B-77" });
    expect(sent[0].client_request_id).toEqual(expect.any(String));
  });
});

describe("cheque detail", () => {
  it("shows instrument, origin with links, state, history and the expiry dates; a viewer has no actions", async () => {
    mockApi(base("viewer", { "GET /settlements/cheques/21": () => jsonResponse(200, detail()) }));
    renderWithQuery(<ChequeDetailView chequeId={21} />);
    expect(await screen.findByTestId("cheque-state")).toHaveTextContent("En cartera");
    expect(screen.getByText("Pagadero desde")).toBeInTheDocument();
    expect(screen.getAllByText("Presentar hasta").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Rendición de reparto n.º 7" })).toHaveAttribute(
      "href",
      "/rendiciones/repartos/7",
    );
    expect(screen.getByText("Entró a cartera")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Depositar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Devolver cheque" })).not.toBeInTheDocument();
  });

  it("an operator deposits but cannot return; an expired cheque cannot be deposited", async () => {
    mockApi(
      base("operator", {
        "GET /settlements/cheques/21": () =>
          jsonResponse(200, detail({ presentation_expired: true, presentation_deadline: shift(-1) })),
      }),
    );
    renderWithQuery(<ChequeDetailView chequeId={21} />);
    expect(await screen.findByRole("button", { name: "Depositar" })).toBeDisabled();
    expect(screen.getByText(/Vencido: se podía presentar hasta/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Devolver cheque" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Entregar cheque/)).not.toBeInTheDocument();
  });

  it("warns that a rejection after crediting debits the bank and may overdraw it, then links the new pending", async () => {
    const credited = detail({
      state: "CREDITED",
      state_label: "Acreditado",
      deposit_bank_account_id: 3,
      deposit_bank_account_name: "Banco Nación cuenta corriente",
      deposit_business_date: shift(-3),
      credit_business_date: shift(-2),
    });
    const spy = mockApi(
      base("operator", {
        "GET /settlements/cheques/21": () => jsonResponse(200, credited),
        "POST /settlements/cheques/21/rejection": () => jsonResponse(201, { id: 4, pending_id: 33 }),
      }),
    );
    renderWithQuery(<ChequeDetailView chequeId={21} />);
    await userEvent.click(await screen.findByRole("button", { name: "Registrar rechazo" }));
    const dialog = await screen.findByRole("dialog", { name: "Registrar rechazo" });
    expect(dialog).toHaveTextContent("Después de acreditado");
    expect(dialog).toHaveTextContent(
      "El rechazo volverá a reconocer una deuda por $ 100.000,00 a nombre de Almacén Don José.",
    );
    expect(within(dialog).getByTestId("rejection-overdraft-warning")).toHaveTextContent(
      "El banco será debitado por $ 100.000,00. Esto puede dejar la cuenta en descubierto.",
    );
    await userEvent.type(within(dialog).getByLabelText("Motivo bancario"), "Sin fondos");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar rechazo" }));
    expect(await screen.findByTestId("new-pending-link")).toHaveAttribute("href", "/rendiciones/pendientes/33");
    expect(bodies(spy, "POST", "/settlements/cheques/21/rejection")[0]).toMatchObject({
      reason: "Sin fondos",
      responsible_employee_id: null,
    });
  });

  it("lets only an admin return a cheque of the portfolio, with a reason and the new debt explained", async () => {
    mockApi(
      base("admin", {
        "GET /settlements/cheques/21": () => jsonResponse(200, detail()),
        "POST /settlements/cheques/21/return": () => jsonResponse(201, { id: 2, pending_id: 34 }),
      }),
    );
    renderWithQuery(<ChequeDetailView chequeId={21} />);
    await userEvent.click(await screen.findByRole("button", { name: "Devolver cheque" }));
    const dialog = await screen.findByRole("dialog", { name: "Devolver cheque" });
    expect(dialog).toHaveTextContent("El cheque saldrá de Cartera y volverá a reconocerse una deuda por $ 100.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Cliente pidió cambiarlo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revisar" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar devolución" }));
    expect(await screen.findByTestId("new-pending-link")).toHaveAttribute("href", "/rendiciones/pendientes/34");
  });

  it("an admin reverses a document in its original date and reads why it cannot when there is later activity", async () => {
    const deposited = detail({
      state: "DEPOSITED",
      state_label: "Depositado",
      documents: [
        {
          type: "DEPOSIT",
          id: 5,
          business_date: shift(-2),
          operation_id: 600,
          reversed: false,
          reversal_operation_id: null,
          pending_id: null,
        },
      ],
    });
    mockApi(
      base("admin", {
        "GET /settlements/cheques/21": () => jsonResponse(200, deposited),
        "POST /settlements/cheque-deposits/5/reverse": () =>
          jsonResponse(409, { code: "cheque_has_activity", message: "x", details: null, request_id: null }),
      }),
    );
    renderWithQuery(<ChequeDetailView chequeId={21} />);
    const doc = await screen.findByTestId("document-DEPOSIT-5");
    await userEvent.click(within(doc).getByRole("button", { name: "Revertir" }));
    const dialog = await screen.findByRole("dialog", { name: "Revertir depósito" });
    expect(dialog).toHaveTextContent("La corrección se registrará en la fecha financiera original");
    expect(dialog).toHaveTextContent("El movimiento no se eliminará.");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Boleta equivocada");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revertir" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("primero hay que revertir los posteriores");
  });
});

describe("cheques in a settlement", () => {
  const settlement = (cheques: Cheque[]) => ({ id: 7, cheques }) as unknown as Settlement;

  it("marks a declared ECHEQ as blocking the close and a physical one as justifying", () => {
    renderWithQuery(
      <SettlementCheques
        settlement={settlement([
          cheque({ published: false, state: "RECEIVED", state_label: "Recibido" }),
          cheque({
            id: 30,
            kind: "ECHEQ",
            verification_status: "DECLARED",
            state: "DECLARED",
            state_label: "Declarado",
            published: false,
          }),
        ])}
        editable
        isAdmin={false}
      />,
    );
    expect(screen.getByTestId("cheque-row-21")).toHaveTextContent("Justifica la rendición");
    expect(screen.getByTestId("echeq-blocks-30")).toHaveTextContent("Pendiente de verificación: bloquea el cierre");
    const row = screen.getByTestId("cheque-row-30");
    expect(within(row).getByRole("button", { name: "Verificar" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Rechazar verificación" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Corregir decisión" })).not.toBeInTheDocument();
  });

  it("asks for «Recibí el cheque físicamente» before loading a physical cheque", async () => {
    const spy = mockApi(base("operator"));
    renderWithQuery(<SettlementCheques settlement={settlement([])} editable isAdmin={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Cargar cheque" }));
    const dialog = await screen.findByRole("dialog", { name: "Cargar cheque" });
    expect(within(dialog).getByRole("checkbox", { name: /Recibí el cheque físicamente/ })).not.toBeChecked();
    await userEvent.click(within(dialog).getByRole("radio", { name: "ECHEQ" }));
    expect(dialog).toHaveTextContent("El ECHEQ queda declarado, pendiente de verificación.");
    expect(spy).not.toHaveBeenCalledWith(
      expect.stringContaining("/cheques"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
