import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  ExpectedHistory,
  ExpectedPending,
  Settlement,
  SettlementItem,
  Summary,
} from "@/modules/settlements/delivery/api";
import { DeliveryList } from "@/modules/settlements/delivery/delivery-list";
import { ExpectedHistorySection, PENDING_ADMIN_NOTE } from "@/modules/settlements/delivery/expected-panel";
import { differenceText, expectedOriginText, LEGACY_ORIGIN } from "@/modules/settlements/delivery/labels";
import { DIFFERENCE_WITHOUT_EXPECTED, SettlementDetail } from "@/modules/settlements/delivery/settlement-detail";
import { EmployeeCodes } from "@/modules/settlements/store-sys/employee-codes";
import { StoreSysGate } from "@/modules/settlements/store-sys/area-gate";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

/** R9-1: a delivery opens without its expected amount, every change is an event, Store Sys updates wait for a
 * decision and the employees' Store Sys codes live in their own protected area. */

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

const me = (role: Role, storeSys: Role | null = null) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
  areas: storeSys ? [{ key: "settlements.store_sys_import", name: "Integración Store Sys", role: storeSys }] : [],
});

function summary(changes: Partial<Summary> = {}): Summary {
  return {
    expected: "100000.00",
    cash_counted: "0.00",
    cash_additions: "0.00",
    cash_total: "0.00",
    cash_by_box: [],
    transfers_verified: "0.00",
    transfers_declared: "0.00",
    transfers_rejected: "0.00",
    declared_transfers_count: 0,
    cheques: "0.00",
    pending: "0.00",
    expenses: "0.00",
    justified: "0.00",
    difference: "-100000.00",
    difference_kind: "SHORTAGE",
    ...changes,
  };
}

function settlement(changes: Partial<Settlement> = {}): Settlement {
  return {
    id: 7,
    type: "DELIVERY",
    business_date: "2026-10-04",
    employee_id: 4,
    employee_name: "Pedro Gómez",
    sequence_number: 1,
    expected_amount: "100000.00",
    expected_source: "MANUAL",
    expected_origin: {
      source: "MANUAL",
      legacy: false,
      external_reference: null,
      revision: null,
      set_at: "2026-10-04T10:00:00Z",
      set_by_name: "Oscar Operador",
    },
    expected_pending: null,
    external_reference: null,
    notes: null,
    status: "OPEN",
    version: 1,
    created_by: 9,
    created_by_name: "Oscar Operador",
    created_at: "2026-10-04T10:00:00Z",
    updated_at: "2026-10-04T10:00:00Z",
    same_day_settlement_ids: [],
    count_lines: [],
    additions: [],
    transfers: [],
    cheques: [],
    expenses: [],
    summary: summary(),
    financial_state: "NOT_CLOSED",
    financial_state_detail: "",
    close_in_effect: null,
    closed_by: null,
    closed_by_name: null,
    closed_at: null,
    reopened_by: null,
    reopened_by_name: null,
    reopened_at: null,
    reopen_reason: null,
    current_difference: null,
    pending_collections: [],
    ...changes,
  };
}

const STORE_SYS_ORIGIN = {
  source: "STORE_SYS" as const,
  legacy: false,
  external_reference: "LIQ-4512",
  revision: 2,
  set_at: "2026-10-04T10:00:00Z",
  set_by_name: "Integración",
};

const PENDING: ExpectedPending = {
  event_id: 31,
  expected_amount: "120000.00",
  current_amount: "100000.00",
  external_reference: "LIQ-4512",
  revision: 3,
  received_at: "2026-10-04T12:00:00Z",
  requires_admin: false,
};

const HISTORY: ExpectedHistory = { settlement_id: 7, events: [], origin: null, pending: null };

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const handler = routes[`${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`];
    if (!handler) return jsonResponse(404, { code: "not_found", message: "x", details: null, request_id: null });
    return handler(init);
  });
}

function detailRoutes(role: Role, current: Settlement, extra: Record<string, Handler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, me(role)),
    "GET /settlements/accounts": () => jsonResponse(200, []),
    "GET /settlements/deliveries/7": () => jsonResponse(200, current),
    "GET /settlements/cash-denominations": () => jsonResponse(200, []),
    "GET /settlements/expense-categories": () => jsonResponse(200, []),
    "GET /settlements/employees": () => jsonResponse(200, []),
    ...extra,
  };
}

const sent = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input) === `/api/v1${path}` && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);

describe("a delivery without its expected amount", () => {
  it("reads «Sin cargar» and invents no difference", async () => {
    mockApi(
      detailRoutes(
        "operator",
        settlement({
          expected_amount: null,
          expected_origin: null,
          summary: summary({ expected: "0.00", expected_missing: true, difference: null, difference_kind: null }),
        }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    expect(await screen.findByTestId("expected-missing")).toHaveTextContent("Sin cargar");
    expect(screen.getByTestId("board-difference")).toHaveTextContent("—");
    expect(screen.getByText(DIFFERENCE_WITHOUT_EXPECTED)).toBeInTheDocument();
    expect(screen.queryByTestId("expected-origin")).not.toBeInTheDocument();
    expect(differenceText(null)).toBe("—");
  });

  it("is listed as «Sin cargar», with the mark of a Store Sys update to decide", async () => {
    const item: SettlementItem = {
      id: 7,
      type: "DELIVERY",
      business_date: "2026-10-04",
      employee_id: 4,
      employee_name: "Pedro Gómez",
      sequence_number: 1,
      expected_amount: null,
      external_reference: null,
      status: "OPEN",
      version: 1,
      financial_state: "NOT_CLOSED",
      created_at: "2026-10-04T10:00:00Z",
      justified: "0.00",
      difference: null,
      expected_update_pending: true,
    };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /settlements/employees": () => jsonResponse(200, []),
      "GET /settlements/deliveries": () => jsonResponse(200, { items: [item], total: 1 }),
    });
    renderWithQuery(<DeliveryList />);
    const row = (await screen.findByText("Rendición n.º 1")).closest("tr")!;
    expect(within(row).getByText("Sin cargar")).toBeInTheDocument();
    expect(within(row).getByTestId("expected-update-pending")).toHaveTextContent("Store Sys pendiente");
  });

  it("loads the expected amount later from Corregir datos, as a manual change", async () => {
    const spy = mockApi(
      detailRoutes(
        "operator",
        settlement({ expected_amount: null, expected_origin: null, summary: summary({ expected_missing: true }) }),
        { "PATCH /settlements/deliveries/7": () => jsonResponse(200, settlement()) },
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Corregir datos" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Importe esperado"), "95000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(sent(spy, "PATCH", "/settlements/deliveries/7")).toHaveLength(1));
    expect(sent(spy, "PATCH", "/settlements/deliveries/7")[0]).toMatchObject({ expected_amount: "95000.00" });
  });
});

describe("where the expected amount comes from", () => {
  it("names Store Sys with its reference and revision, a manual load, or one loaded before the integration", async () => {
    expect(expectedOriginText(STORE_SYS_ORIGIN)).toBe("Store Sys · Ref. LIQ-4512 rev. 2");
    expect(expectedOriginText({ ...STORE_SYS_ORIGIN, source: "MANUAL" })).toBe("Manual");
    expect(expectedOriginText({ ...STORE_SYS_ORIGIN, source: "MANUAL", legacy: true })).toBe(LEGACY_ORIGIN);
    mockApi(detailRoutes("viewer", settlement({ expected_source: "STORE_SYS", expected_origin: STORE_SYS_ORIGIN })));
    renderWithQuery(<SettlementDetail settlementId={7} />);
    expect(await screen.findByTestId("expected-origin")).toHaveTextContent("Origen: Store Sys · Ref. LIQ-4512 rev. 2");
  });

  it("correcting an amount from Store Sys asks for a reason; editing only the notes sends no amount", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement({ expected_source: "STORE_SYS", expected_origin: STORE_SYS_ORIGIN }), {
        "PATCH /settlements/deliveries/7": () => jsonResponse(200, settlement()),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Corregir datos" }));
    let dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Notas (opcional)"), "Llegó tarde");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(sent(spy, "PATCH", "/settlements/deliveries/7")).toHaveLength(1));
    expect(sent(spy, "PATCH", "/settlements/deliveries/7")[0]).not.toHaveProperty("expected_amount");

    await userEvent.click(await screen.findByRole("button", { name: "Corregir datos" }));
    dialog = screen.getByRole("dialog");
    const amount = within(dialog).getByLabelText("Importe esperado");
    await userEvent.clear(amount);
    await userEvent.type(amount, "90000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    expect(within(dialog).getByText("Indicá el motivo para corregir el esperado de Store Sys.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo de la corrección del esperado"), "Devolución");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(sent(spy, "PATCH", "/settlements/deliveries/7")).toHaveLength(2));
    expect(sent(spy, "PATCH", "/settlements/deliveries/7")[1]).toMatchObject({
      expected_amount: "90000.00",
      expected_reason: "Devolución",
    });
  });
});

describe("a pending Store Sys update", () => {
  it("is accepted by an operator", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement({ expected_pending: PENDING }), {
        "POST /settlements/expected-updates/31/accept": () => jsonResponse(200, HISTORY),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const banner = await screen.findByTestId("expected-pending");
    expect(banner).toHaveTextContent("Store Sys: $ 120.000,00");
    expect(banner).toHaveTextContent("la rendición no se puede cerrar");
    await userEvent.click(within(banner).getByRole("button", { name: "Aceptar el de Store Sys" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Aceptar" }));
    await waitFor(() => expect(sent(spy, "POST", "/settlements/expected-updates/31/accept")).toHaveLength(1));
  });

  it("is kept only with a reason", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement({ expected_pending: PENDING }), {
        "POST /settlements/expected-updates/31/keep": () => jsonResponse(200, HISTORY),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Mantener el cargado" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Mantener" }));
    expect(within(dialog).getByText("Indicá el motivo.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Ya se rindió así");
    await userEvent.click(within(dialog).getByRole("button", { name: "Mantener" }));
    await waitFor(() =>
      expect(sent(spy, "POST", "/settlements/expected-updates/31/keep")).toEqual([{ reason: "Ya se rindió así" }]),
    );
  });

  it("on a settlement being corrected, only an admin decides", async () => {
    mockApi(
      detailRoutes(
        "operator",
        settlement({ financial_state: "BEING_CORRECTED", expected_pending: { ...PENDING, requires_admin: true } }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} />);
    const banner = await screen.findByTestId("expected-pending");
    expect(banner).toHaveTextContent(PENDING_ADMIN_NOTE);
    expect(within(banner).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("the history of the expected amount", () => {
  it("shows every event with its outcome and who decided it", async () => {
    const history: ExpectedHistory = {
      settlement_id: 7,
      origin: STORE_SYS_ORIGIN,
      pending: null,
      events: [
        {
          id: 1,
          source: "MANUAL",
          outcome: "MANUAL",
          expected_amount: "100000.00",
          previous_amount: null,
          previous_source: null,
          external_reference: null,
          revision: null,
          reported_at: null,
          settlement_version: 1,
          reason: null,
          received_by_name: "Oscar Operador",
          received_at: "2026-10-04T10:00:00Z",
          decided_by_name: null,
          decided_at: null,
          decision_reason: null,
          in_effect: false,
        },
        {
          id: 2,
          source: "STORE_SYS",
          outcome: "KEPT",
          expected_amount: "120000.00",
          previous_amount: "100000.00",
          previous_source: "MANUAL",
          external_reference: "LIQ-4512",
          revision: 1,
          reported_at: null,
          settlement_version: 1,
          reason: null,
          received_by_name: "Integración",
          received_at: "2026-10-04T11:00:00Z",
          decided_by_name: "Oscar Operador",
          decided_at: "2026-10-04T11:30:00Z",
          decision_reason: "Error de Store Sys",
          in_effect: false,
        },
      ],
    };
    mockApi({ "GET /settlements/settlements/7/expected-history": () => jsonResponse(200, history) });
    renderWithQuery(<ExpectedHistorySection settlement={settlement()} />);
    const list = await screen.findByTestId("expected-history");
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Carga manual");
    expect(items[1]).toHaveTextContent("Se mantuvo el cargado");
    expect(items[1]).toHaveTextContent("Store Sys · Ref. LIQ-4512 rev. 1");
    expect(items[1]).toHaveTextContent("Motivo: Error de Store Sys");
  });

  it("explains an amount loaded before the integration, which has no event", async () => {
    mockApi({
      "GET /settlements/settlements/7/expected-history": () =>
        jsonResponse(200, { ...HISTORY, origin: { ...STORE_SYS_ORIGIN, source: "MANUAL", legacy: true } }),
    });
    renderWithQuery(<ExpectedHistorySection settlement={settlement()} />);
    expect(await screen.findByTestId("expected-history-empty")).toHaveTextContent(LEGACY_ORIGIN);
  });
});

describe("Integración Store Sys", () => {
  const ANA = { employee_id: 4, full_name: "Ana Pérez", active: true, store_code: "R-01" };
  const codeRoutes = (area: Role | null, extra: Record<string, Handler> = {}) => ({
    "GET /auth/me": () => jsonResponse(200, me("admin", area)),
    "GET /settlements/store-sys/employee-codes": () => jsonResponse(200, { items: [ANA], total: 1 }),
    ...extra,
  });

  it("does not exist without the area, not even for an admin of Rendiciones", async () => {
    mockApi(codeRoutes(null));
    renderWithQuery(
      <StoreSysGate>
        <EmployeeCodes />
      </StoreSysGate>,
    );
    expect(await screen.findByText("Página no encontrada")).toBeInTheDocument();
  });

  it("a viewer of the area reads codes and their history but changes nothing", async () => {
    mockApi(
      codeRoutes("viewer", {
        "GET /settlements/store-sys/employee-codes/4/history": () =>
          jsonResponse(200, [
            {
              before: null,
              after: "R-01",
              reason: null,
              changed_by_name: "Integra",
              changed_at: "2026-10-04T10:00:00Z",
            },
          ]),
      }),
    );
    renderWithQuery(
      <StoreSysGate>
        <EmployeeCodes />
      </StoreSysGate>,
    );

    const row = await screen.findByTestId("store-code-4");
    expect(row).toHaveTextContent("R-01");
    expect(within(row).queryByRole("button", { name: "Cambiar código" })).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Historia" }));
    expect(await screen.findByTestId("store-code-history")).toHaveTextContent("sin código → R-01");
  });

  it("the area admin changes a loaded code only with a reason", async () => {
    const spy = mockApi(
      codeRoutes("admin", {
        "PUT /settlements/store-sys/employee-codes/4": () => jsonResponse(200, { ...ANA, store_code: "R-02" }),
      }),
    );
    renderWithQuery(
      <StoreSysGate>
        <EmployeeCodes />
      </StoreSysGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Cambiar código" }));
    const dialog = screen.getByRole("dialog");
    const code = within(dialog).getByLabelText("Código");
    await userEvent.clear(code);
    await userEvent.type(code, "R-02");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    expect(within(dialog).getByText("Indicá el motivo del cambio.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Store Sys lo renumeró");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(sent(spy, "PUT", "/settlements/store-sys/employee-codes/4")).toEqual([
        { store_code: "R-02", reason: "Store Sys lo renumeró" },
      ]),
    );
  });
});
