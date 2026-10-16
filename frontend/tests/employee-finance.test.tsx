import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { visibleSections } from "@/components/layout/sidebar";
import { areaRole, type CurrentUser } from "@/lib/api/auth";
import { MODULES } from "@/modules/registry";
import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { EmployeeFinanceDetailView } from "@/modules/settlements/employees/employee-detail";
import { EmployeeFinanceList } from "@/modules/settlements/employees/employee-list";
import { parseSalary } from "@/modules/settlements/employees/labels";
import { SETTLEMENTS_NAV } from "@/modules/settlements/nav";

import { jsonResponse, renderWithQuery } from "./render";

const navigation = vi.hoisted(() => ({ pathname: "/rendiciones" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const AREA = "settlements.employee_finance";
type Role = "viewer" | "operator" | "admin";

const me = (platform: "admin" | "viewer", area: Role | null): CurrentUser => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: platform,
  modules: [{ key: "settlements", name: "Rendiciones", role: "viewer" }],
  areas: area ? [{ key: AREA, name: "Empleados", role: area }] : [],
});

type Handler = (init: RequestInit | undefined) => Response;

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    const handler = routes[key];
    if (!handler)
      return jsonResponse(404, { code: "not_found", message: "No encontrado.", details: null, request_id: null });
    return handler(init);
  });
}

const TERM = {
  id: 5,
  employee_id: 7,
  valid_from: "2026-01-01",
  valid_to: null,
  base_salary: "800000.00",
  notes: "Acordado al ingreso",
  status: "CURRENT",
  created_at: "2026-01-02T12:00:00Z",
  created_by_name: "Enzo",
  ended_at: null,
  ended_by_name: null,
  end_reason: null,
};

const ROW = {
  employee_id: 7,
  full_name: "Pedro Gómez",
  active: true,
  balance: "20000.00",
  balance_reading: "OWES_COMPANY",
  current_term: TERM,
  next_term: null,
};

const MOVEMENTS = {
  items: [
    {
      entry_id: 31,
      operation_id: 12,
      business_date: "2026-10-10",
      amount: "20000.00",
      reading: "CHARGE",
      origin: "SETTLEMENT_SHORTAGE",
      document_kind: "DELIVERY_SETTLEMENT",
      document_id: 4,
      document_date: "2026-10-10",
      document_number: 18,
      created_at: "2026-10-11T12:00:00Z",
    },
    {
      entry_id: 32,
      operation_id: 13,
      business_date: "2026-10-09",
      amount: "-5000.00",
      reading: "CREDIT",
      origin: "PENDING_WRITE_OFF_REVERSAL",
      document_kind: "PENDING_COLLECTION",
      document_id: 9,
      document_date: null,
      document_number: null,
      created_at: "2026-10-11T12:00:00Z",
    },
  ],
  total: 2,
};

function detailRoutes(area: Role, terms: unknown[] = [TERM]) {
  return {
    "GET /auth/me": () => jsonResponse(200, me("viewer", area)),
    "GET /settlements/employee-finance/employees/7": () =>
      jsonResponse(200, { ...ROW, hire_date: "2025-03-01", has_account: true }),
    "GET /settlements/employee-finance/employees/7/movements": () => jsonResponse(200, MOVEMENTS),
    "GET /settlements/employee-finance/employees/7/compensation-terms": () => jsonResponse(200, terms),
  };
}

afterEach(() => vi.restoreAllMocks());

describe("protected area in the navigation", () => {
  it("lists Empleados only with an explicit grant, not for the platform admin", () => {
    const hrefs = (areas: string[]) =>
      visibleSections(SETTLEMENTS_NAV, areas).flatMap((s) => [s.href, ...(s.items ?? []).map((i) => i.href)]);
    expect(hrefs([])).not.toContain("/rendiciones/empleados");
    expect(hrefs([AREA])).toContain("/rendiciones/empleados");
    expect(areaRole(me("admin", null), AREA)).toBeNull();
    expect(areaRole(me("viewer", "operator"), AREA)).toBe("operator");
  });

  it("the shell shows the item to a granted user and hides it from a platform admin without the area", async () => {
    navigation.pathname = "/rendiciones";
    mockApi({ "GET /auth/me": () => jsonResponse(200, me("admin", null)) });
    const { unmount } = renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    await screen.findByText("contenido");
    const main = screen.getByRole("navigation", { name: "Navegación principal" });
    // Only the platform page Empleados (platform admin), never the area.
    expect(within(main).getByRole("link", { name: "Empleados" })).toHaveAttribute("href", "/empleados");
    expect(main.querySelector('a[href="/rendiciones/empleados"]')).toBeNull();
    unmount();
    vi.restoreAllMocks();

    mockApi({ "GET /auth/me": () => jsonResponse(200, me("viewer", "viewer")) });
    renderWithQuery(
      <AppShell modules={MODULES}>
        <p>contenido</p>
      </AppShell>,
    );
    await screen.findByText("contenido");
    expect(screen.getByRole("link", { name: "Empleados" })).toHaveAttribute("href", "/rendiciones/empleados");
  });
});

describe("EmployeeFinanceGate", () => {
  it("reads as a page that does not exist without the area, and asks nothing of the API", async () => {
    const spy = mockApi({ "GET /auth/me": () => jsonResponse(200, me("admin", null)) });
    renderWithQuery(
      <EmployeeFinanceGate>
        <p>secreto</p>
      </EmployeeFinanceGate>,
    );
    expect(await screen.findByText("Página no encontrada")).toBeInTheDocument();
    expect(screen.queryByText("secreto")).not.toBeInTheDocument();
    expect(spy.mock.calls.every(([input]) => !String(input).includes("employee-finance"))).toBe(true);
  });
});

describe("EmployeeFinanceList", () => {
  it("shows balance, its reading and the salary term, with the notice", async () => {
    mockApi({
      "GET /settlements/employee-finance/employees": () =>
        jsonResponse(200, {
          items: [
            ROW,
            {
              ...ROW,
              employee_id: 8,
              full_name: "Juan Pérez",
              balance: "-1500.00",
              balance_reading: "COMPANY_OWES",
              current_term: null,
            },
            { ...ROW, employee_id: 9, full_name: "Ana Ruiz", active: false, balance: "0.00", balance_reading: "NONE" },
          ],
          total: 3,
        }),
    });
    renderWithQuery(<EmployeeFinanceList />);
    expect(await screen.findByRole("link", { name: "Pedro Gómez" })).toHaveAttribute(
      "href",
      "/rendiciones/empleados/7",
    );
    expect(screen.getByText("Sueldo base interno (mensual). No es el recibo de sueldo legal.")).toBeInTheDocument();
    const pedro = screen.getByTestId("employee-finance-7");
    expect(within(pedro).getByText("$ 20.000,00")).toBeInTheDocument();
    expect(within(pedro).getByText("Debe a la empresa")).toBeInTheDocument();
    expect(within(pedro).getByText("$ 800.000,00")).toBeInTheDocument();
    const juan = screen.getByTestId("employee-finance-8");
    expect(within(juan).getByText("La empresa le debe")).toBeInTheDocument();
    expect(within(juan).getByText("Sin condición")).toBeInTheDocument();
    const ana = screen.getByTestId("employee-finance-9");
    expect(within(ana).getByText("Sin saldo")).toBeInTheDocument();
    expect(within(ana).getByText("(inactivo)")).toBeInTheDocument();
    // Stacked on a phone: each cell carries its label.
    expect(within(pedro).getByText("Debe a la empresa").closest("td")).toHaveAttribute("data-label", "Lectura");
  });
});

describe("EmployeeFinanceDetailView", () => {
  it("shows the internal account read-only, with each movement's document", async () => {
    mockApi(detailRoutes("viewer"));
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    expect(await screen.findByTestId("internal-balance")).toHaveTextContent("$ 20.000,00");
    expect(screen.getByText("Movimientos de la cuenta interna")).toBeInTheDocument();
    const shortage = await screen.findByTestId("account-movement-31");
    expect(within(shortage).getByText("Faltante de rendición")).toBeInTheDocument();
    expect(within(shortage).getByRole("link", { name: "Rendición n.º 18 (reparto)" })).toHaveAttribute(
      "href",
      "/rendiciones/repartos/4",
    );
    expect(within(shortage).getByText("+$ 20.000,00")).toBeInTheDocument();
    const reversal = screen.getByTestId("account-movement-32");
    expect(within(reversal).getByRole("link", { name: "Pendiente de cobro #9" })).toHaveAttribute(
      "href",
      "/rendiciones/pendientes/9",
    );
    expect(within(reversal).getByText("-$ 5.000,00")).toBeInTheDocument();
    // No application vocabulary in R8-1.
    expect(screen.queryByText(/aplicad/i)).not.toBeInTheDocument();
    // A viewer of the area does not edit salary terms.
    expect(await screen.findByTestId("compensation-term-5")).toHaveTextContent("Vigente");
    expect(screen.queryByRole("button", { name: "Terminar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cargar condición" })).not.toBeInTheDocument();
  });

  it("lets the area admin end the term in effect, with a reason", async () => {
    const ended = { ...TERM, valid_to: "2026-10-31", status: "CURRENT", end_reason: "Aumento" };
    const spy = mockApi({
      ...detailRoutes("admin"),
      "POST /settlements/employee-finance/compensation-terms/5/end": () => jsonResponse(200, ended),
    });
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Terminar" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Terminar condición" }));
    expect(within(dialog).getByText("Indicá el motivo.")).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Último día de vigencia"));
    await userEvent.type(within(dialog).getByLabelText("Último día de vigencia"), "2026-10-31");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Aumento");
    await userEvent.click(within(dialog).getByRole("button", { name: "Terminar condición" }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/compensation-terms/5/end") &&
            init?.body === JSON.stringify({ valid_to: "2026-10-31", reason: "Aumento" }),
        ),
      ).toBe(true),
    );
  });

  it("lets the area admin load a term when none is open, $0 included", async () => {
    const spy = mockApi({
      ...detailRoutes("admin", []),
      "POST /settlements/employee-finance/employees/7/compensation-terms": () => jsonResponse(201, TERM),
    });
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    expect(await screen.findByText("Sin condición salarial cargada.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cargar condición" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Sueldo base interno (mensual). No es el recibo de sueldo legal."),
    ).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Sueldo base interno mensual"), "-5");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar condición" }));
    expect(within(dialog).getByText("Ingresá un sueldo válido.")).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Sueldo base interno mensual"));
    await userEvent.type(within(dialog).getByLabelText("Sueldo base interno mensual"), "0");
    await userEvent.clear(within(dialog).getByLabelText("Vigente desde"));
    await userEvent.type(within(dialog).getByLabelText("Vigente desde"), "2026-11-01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar condición" }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/employees/7/compensation-terms") &&
            init?.body === JSON.stringify({ valid_from: "2026-11-01", base_salary: "0.00", notes: null }),
        ),
      ).toBe(true),
    );
  });

  it("shows the API's message when the term overlaps", async () => {
    mockApi({
      ...detailRoutes("admin", []),
      "POST /settlements/employee-finance/employees/7/compensation-terms": () =>
        jsonResponse(409, {
          code: "compensation_term_overlap",
          message: "Ya hay una condición salarial desde el 01/01/2026 sin fecha de fin.",
          details: null,
          request_id: "r",
        }),
    });
    renderWithQuery(<EmployeeFinanceDetailView employeeId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Cargar condición" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Sueldo base interno mensual"), "900.000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar condición" }));
    expect(await within(dialog).findByText(/Ya hay una condición salarial/)).toBeInTheDocument();
  });
});

describe("parseSalary", () => {
  it("reads Argentine amounts and accepts zero", () => {
    expect(parseSalary("800.000")).toBe("800000.00");
    expect(parseSalary("1.234,5")).toBe("1234.50");
    expect(parseSalary("0")).toBe("0.00");
    expect(parseSalary("$ 0,00")).toBe("0.00");
    expect(parseSalary("-1")).toBeNull();
    expect(parseSalary("abc")).toBeNull();
  });
});
