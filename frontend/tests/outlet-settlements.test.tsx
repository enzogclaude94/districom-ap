import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Account } from "@/modules/settlements/cash/api";
import type { ClosingPreview, History, Settlement, Summary } from "@/modules/settlements/delivery/api";
import { timeline } from "@/modules/settlements/delivery/history-tab";
import { SettlementDetail, ZERO_CLOSE_TEXT } from "@/modules/settlements/delivery/settlement-detail";
import { parseExpected } from "@/modules/settlements/outlet-settlements/amount";
import type { OutletSettlementItem } from "@/modules/settlements/outlet-settlements/api";
import { OutletOpenSettlements } from "@/modules/settlements/outlet-settlements/open-count";
import { OutletSettlementList } from "@/modules/settlements/outlet-settlements/outlet-list";

import { jsonResponse, renderWithQuery } from "./render";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }) }));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "settlements", name: "Rendiciones", role }],
});

const ACCOUNTS: Account[] = [
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
];

const OUTLETS = [
  { id: 3, code: "CENTRO", name: "Boca Centro", description: null, active: true },
  { id: 4, code: "NORTE", name: "Boca Norte", description: null, active: false },
];

function summary(changes: Partial<Summary> = {}): Summary {
  return {
    expected: "0.00",
    expected_missing: false,
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
    difference: "0.00",
    difference_kind: "BALANCED",
    ...changes,
  };
}

function settlement(changes: Partial<Settlement> = {}): Settlement {
  return {
    id: 7,
    type: "OUTLET",
    business_date: "2026-10-04",
    outlet_id: 3,
    outlet_code: "CENTRO",
    outlet_name: "Boca Centro",
    outlet_active: true,
    responsible_suggestions: [
      { employee_id: 5, full_name: "Ana Cajera" },
      { employee_id: 6, full_name: "Luis Encargado" },
    ],
    sequence_number: 1,
    expected_amount: "0.00",
    expected_source: "MANUAL",
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

function preview(changes: Partial<ClosingPreview> = {}): ClosingPreview {
  return {
    settlement_id: 7,
    version: 1,
    status: "OPEN",
    financial_state: "NOT_CLOSED",
    summary: summary(),
    difference: "0.00",
    difference_kind: "BALANCED",
    amount_to_allocate: "0.00",
    blockers: [],
    legs: [],
    preview_token: "b".repeat(64),
    close_in_effect: null,
    administrative_zero: true,
    responsible_suggestions: [],
    pending_collections: [],
    ...changes,
  };
}

const ZERO_CLOSED = settlement({
  status: "CLOSED",
  financial_state: "CLOSED",
  close_in_effect: {
    operation_id: null,
    kind: "ADMINISTRATIVE_ZERO",
    settlement_version: 1,
    closed_at: "2026-10-04T15:00:00Z",
  },
  closed_by: 9,
  closed_by_name: "Oscar Operador",
  closed_at: "2026-10-04T15:00:00Z",
});

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler) return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
    return handler(init);
  });
}

function detailRoutes(role: Role, current: Settlement, extra: Record<string, Handler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, me(role)),
    "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
    "GET /settlements/outlet-settlements/7": () => jsonResponse(200, current),
    "GET /settlements/cash-denominations": () => jsonResponse(200, []),
    "GET /settlements/expense-categories": () =>
      jsonResponse(200, [
        { id: 1, name: "Limpieza", active: true, code: null },
        { id: 2, name: "Combustible", active: true, code: "FUEL" },
      ]),
    "GET /settlements/employees": () => jsonResponse(200, [{ id: 5, full_name: "Ana Cajera" }]),
    ...extra,
  };
}

const bodies = (spy: ReturnType<typeof mockApi>, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input).startsWith(`/api/v1${path}`) && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

describe("expected amount of a boca", () => {
  it("accepts $0 and empty, unlike a delivery's", () => {
    expect(parseExpected("0")).toBe("0.00");
    expect(parseExpected("0,00")).toBe("0.00");
    expect(parseExpected("$ 1.500,50")).toBe("1500.50");
    expect(parseExpected("")).toBeNull();
    expect(parseExpected("-5")).toBeNull();
    expect(parseExpected("abc")).toBeNull();
  });
});

describe("boca settlement list", () => {
  const ITEM: OutletSettlementItem = {
    id: 7,
    business_date: "2026-10-04",
    outlet_id: 3,
    outlet_code: "CENTRO",
    outlet_name: "Boca Centro",
    sequence_number: 1,
    expected_amount: null,
    external_reference: null,
    status: "OPEN",
    version: 1,
    financial_state: "NOT_CLOSED",
    administrative_zero: false,
    created_at: "2026-10-04T10:00:00Z",
    justified: "5000.00",
    difference: null,
  };

  it("shows a missing expected amount and no difference, and links to the boca screen", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /outlets": () => jsonResponse(200, OUTLETS),
      "GET /settlements/outlet-settlements": () =>
        jsonResponse(200, {
          items: [
            ITEM,
            {
              ...ITEM,
              id: 8,
              sequence_number: 2,
              expected_amount: "0.00",
              difference: "0.00",
              status: "CLOSED",
              financial_state: "CLOSED",
              administrative_zero: true,
            },
          ],
          total: 2,
        }),
    });
    renderWithQuery(<OutletSettlementList />);
    const first = (await screen.findByText("Rendición n.º 1")).closest("tr")!;
    expect(within(first).getByText("Sin cargar")).toBeInTheDocument();
    expect(within(first).getByText("—")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: /Abrir Rendición n.º 1 de Boca Centro/ })).toHaveAttribute(
      "href",
      "/rendiciones/boca/7",
    );
    const second = screen.getByText("Rendición n.º 2").closest("tr")!;
    expect(within(second).getByText("Cierre en cero")).toBeInTheDocument();
    // Deactivated bocas still filter their past settlements.
    expect(screen.getByRole("option", { name: "Boca Norte (desactivada)" })).toBeInTheDocument();
  });

  it("opens a boca settlement without an expected amount and confirms a second one of the day", async () => {
    const spy = mockApi({
      "GET /auth/me": () => jsonResponse(200, me("operator")),
      "GET /outlets": () => jsonResponse(200, OUTLETS),
      "GET /settlements/outlet-settlements": () => jsonResponse(200, { items: [ITEM], total: 1 }),
      "POST /settlements/outlet-settlements": () => jsonResponse(201, settlement({ id: 9, sequence_number: 2 })),
    });
    renderWithQuery(<OutletSettlementList initialOutletId={3} />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva rendición" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("same-day-warning")).toHaveTextContent("esperado sin cargar");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear rendición" }));
    expect(await within(dialog).findByText("Confirmá que es otra rendición del mismo día.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByLabelText("Sí, es otra rendición del mismo día"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear rendición" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/rendiciones/boca/9"));
    const [body] = bodies(spy, "/settlements/outlet-settlements");
    expect(body).toMatchObject({ outlet_id: 3, expected_amount: null, confirm_additional: true });
    expect(body).not.toHaveProperty("employee_id");
  });
});

describe("boca settlement screen", () => {
  it("has no pendings and no fuel, shows the responsables and a missing expected amount", async () => {
    mockApi(
      detailRoutes(
        "operator",
        settlement({ expected_amount: null, summary: summary({ expected_missing: true, difference: "0.00" }) }),
      ),
    );
    renderWithQuery(<SettlementDetail settlementId={7} type="OUTLET" />);
    expect(await screen.findByRole("heading", { name: "Rendición n.º 1 · Boca Centro" })).toBeInTheDocument();
    expect(screen.getByTestId("expected-missing")).toHaveTextContent("Sin cargar");
    expect(screen.getByTestId("responsible-suggestions")).toHaveTextContent("Ana Cajera, Luis Encargado");
    expect(screen.queryByText("Pendientes")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Rendiciones de boca" })).toHaveAttribute("href", "/rendiciones/boca");
    await userEvent.click(screen.getByRole("button", { name: "Registrar gasto" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("option", { name: "Limpieza" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: "Combustible" })).not.toBeInTheDocument();
  });

  it("closes a $0 boca without movements administratively: no operation, no link to Cajas", async () => {
    const spy = mockApi(
      detailRoutes("operator", settlement(), {
        "GET /settlements/outlet-settlements/7/closing-preview": () => jsonResponse(200, preview()),
        "POST /settlements/outlet-settlements/7/close": () => jsonResponse(201, ZERO_CLOSED),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} type="OUTLET" />);
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar rendición" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("administrative-zero")).toHaveTextContent("cierre administrativo cero");
    expect(within(dialog).queryByText("Pendiente")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar cierre" }));
    await waitFor(() =>
      expect(bodies(spy, "/settlements/outlet-settlements/7/close")).toEqual([
        { version: 1, preview_token: "b".repeat(64), allocations: [], confirm: true },
      ]),
    );
    expect(await screen.findByText(`Rendición cerrada. ${ZERO_CLOSE_TEXT}`)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Ver el cierre en Cajas" })).not.toBeInTheDocument();
  });

  it("shows a zero close in effect as closed, and the admin can reopen it", async () => {
    mockApi(detailRoutes("admin", ZERO_CLOSED));
    renderWithQuery(<SettlementDetail settlementId={7} type="OUTLET" />);
    expect(await screen.findByTestId("financial-state")).toHaveTextContent("Cerrada");
    expect(screen.getByTestId("zero-close")).toHaveTextContent(ZERO_CLOSE_TEXT);
    await userEvent.click(screen.getByRole("button", { name: "Reabrir para corregir" }));
    expect(await screen.findByText(/El cierre en cero sigue vigente/)).toBeInTheDocument();
  });

  it("suggests the responsables when a difference must be allocated, never preloading them", async () => {
    mockApi(
      detailRoutes("operator", settlement({ expected_amount: "1000.00" }), {
        "GET /settlements/outlet-settlements/7/closing-preview": () =>
          jsonResponse(
            200,
            preview({
              administrative_zero: false,
              difference: "-100.00",
              difference_kind: "SHORTAGE",
              amount_to_allocate: "100.00",
              responsible_suggestions: [{ employee_id: 5, full_name: "Ana Cajera" }],
            }),
          ),
      }),
    );
    renderWithQuery(<SettlementDetail settlementId={7} type="OUTLET" />);
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar rendición" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("close-responsible-suggestions")).toHaveTextContent(
      "Responsables de la boca ese día (sugerencia): Ana Cajera",
    );
    expect(within(dialog).getByLabelText("Parte 1: quién")).toHaveValue("");
    expect(within(dialog).getByLabelText("Importe de la parte 1")).toHaveValue("");
  });

  it("a delivery link to a boca settlement opens the boca screen", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/accounts": () => jsonResponse(200, ACCOUNTS),
      "GET /settlements/deliveries/7": () =>
        jsonResponse(404, { code: "settlement_not_found", message: "x", details: null, request_id: null }),
      "GET /settlements/outlet-settlements/7": () => jsonResponse(200, settlement()),
    });
    renderWithQuery(<SettlementDetail settlementId={7} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/rendiciones/boca/7"));
  });

  it("tells a zero close apart in the history", () => {
    const history: History = {
      settlement_id: 7,
      version: 1,
      status: "CLOSED",
      financial_state: "CLOSED",
      closings: [
        {
          settlement_version: 1,
          kind: "ADMINISTRATIVE_ZERO",
          operation_id: null,
          reversal_operation_id: null,
          reversed_by_operation_id: null,
          in_effect: true,
          closed_by: 9,
          closed_by_name: "Oscar",
          closed_at: "2026-10-04T15:00:00Z",
          business_date: "2026-10-04",
          legs: [],
          snapshot: { expected_amount: "0.00" },
        },
      ],
      reopenings: [],
      differences: [],
    };
    const titles = timeline(ZERO_CLOSED, history).map((event) => event.title);
    expect(titles).toEqual(["Rendición creada", "Cierre administrativo cero"]);
  });
});

describe("a boca's page", () => {
  it("shows its open settlements to Rendiciones users only", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, me("viewer")),
      "GET /settlements/outlets/3/settlements-summary": () => jsonResponse(200, { outlet_id: 3, open_count: 2 }),
    });
    renderWithQuery(<OutletOpenSettlements outletId={3} />);
    expect(await screen.findByTestId("outlet-open-settlements")).toHaveTextContent("2 rendiciones abiertas.");
    expect(screen.getByRole("link", { name: "Ver rendiciones de la boca" })).toHaveAttribute(
      "href",
      "/rendiciones/boca?boca=3",
    );
  });
});
