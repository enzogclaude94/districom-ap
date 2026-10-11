import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OutletList } from "@/features/outlets/outlet-list";
import type { Outlet, OutletDetail } from "@/lib/api/outlets";
import type { Responsibility } from "@/modules/settlements/outlets/api";
import { OutletResponsibilities } from "@/modules/settlements/outlets/outlet-responsibilities";

import { jsonResponse, renderWithQuery } from "./render";

type Handler = (init: RequestInit | undefined, url: URL) => Response;

const me = (role: "admin" | "viewer", settlements: "viewer" | "operator" | "admin" | null = "operator") => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role,
  modules: settlements ? [{ key: "settlements", name: "Rendiciones", role: settlements }] : [],
});

const outlet = (overrides: Partial<Outlet>): Outlet => ({
  id: 1,
  code: "BOCA-01",
  name: "Boca Centro",
  description: "Depósito de la avenida",
  active: true,
  created_at: "2026-10-11T10:00:00Z",
  created_by_name: "Admin",
  updated_at: "2026-10-11T10:00:00Z",
  updated_by_name: "Admin",
  ...overrides,
});

const ACTIVE = [outlet({})];
const ALL = [...ACTIVE, outlet({ id: 2, code: "BOCA-02", name: "Boca Norte", description: null, active: false })];

function mockApi(user: ReturnType<typeof me>, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, user);
    if (key === "GET /outlets") return jsonResponse(200, url.searchParams.get("include_inactive") ? ALL : ACTIVE);
    const handler = routes[key];
    return handler
      ? handler(init, url)
      : jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const sent = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input).split("?")[0] === `/api/v1${path}` && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

const table = async () => within(await screen.findByTestId("outlet-table"));

describe("Bocas de expendio of the platform (R7-1)", () => {
  it("lets a Rendiciones admin consult but not administer them", async () => {
    mockApi(me("viewer", "admin"));
    renderWithQuery(<OutletList />);
    const row = (await (await table()).findByText("BOCA-01")).closest("tr")!;
    expect(row).toHaveTextContent("Boca Centro");
    expect(row).toHaveTextContent("Depósito de la avenida");
    expect(within(row).getByText("Activa")).toBeInTheDocument();
    expect(await screen.findByText(/Solo un administrador de DistriCom las modifica/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva boca" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Corregir|Desactivar|Reactivar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Eliminar|Borrar/ })).not.toBeInTheDocument();
  });

  it("shows deactivated bocas under «Todas»", async () => {
    mockApi(me("viewer"));
    renderWithQuery(<OutletList />);
    await (await table()).findByText("BOCA-01");
    expect(screen.queryByText("BOCA-02")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Todas" }));
    const row = (await (await table()).findByText("BOCA-02")).closest("tr")!;
    expect(within(row).getByText("Desactivada")).toBeInTheDocument();
  });

  it("creates a boca with a normalized code and explains a duplicate name", async () => {
    const spy = mockApi(me("admin"), {
      "POST /outlets": (init) => {
        const body = JSON.parse(String(init?.body)) as { name: string };
        return body.name === "Boca Centro"
          ? jsonResponse(409, {
              code: "outlet_name_taken",
              message: "Ya hay una boca con ese nombre: BOCA-01 — Boca Centro.",
              details: { outlet_id: 1, active: false },
              request_id: null,
            })
          : jsonResponse(201, outlet({ id: 3, code: "B3", name: "Boca Sur" }));
      },
    });
    renderWithQuery(<OutletList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva boca" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Código"), " boca 3");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Boca Sur");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear boca" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("entre 2 y 20 caracteres");
    expect(sent(spy, "POST", "/outlets")).toEqual([]);

    await userEvent.clear(within(dialog).getByLabelText("Código"));
    await userEvent.type(within(dialog).getByLabelText("Código"), " b3 ");
    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Boca   Centro");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear boca" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Ya hay una boca con ese nombre: BOCA-01 — Boca Centro. Está desactivada",
    );
    expect(sent(spy, "POST", "/outlets")).toEqual([{ code: "B3", name: "Boca Centro", description: "" }]);

    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Boca Sur");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear boca" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Boca B3 registrada.");
  });

  it("corrects the name without ever offering to change the code", async () => {
    const spy = mockApi(me("admin"), {
      "PATCH /outlets/1": () => jsonResponse(200, outlet({ name: "Boca Centro Sur" })),
    });
    renderWithQuery(<OutletList />);
    await userEvent.click(await (await table()).findByRole("button", { name: "Corregir BOCA-01" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Código")).toBeDisabled();
    expect(within(dialog).getByText("El código no se modifica.")).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Boca Centro Sur");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("BOCA-01 actualizada.");
    expect(sent(spy, "PATCH", "/outlets/1")).toEqual([{ name: "Boca Centro Sur" }]);
  });

  it("warns before deactivating and says what an inactive boca can still do", async () => {
    const spy = mockApi(me("admin"), {
      "PATCH /outlets/1": () => jsonResponse(200, outlet({ active: false })),
    });
    renderWithQuery(<OutletList />);
    await userEvent.click(await (await table()).findByRole("button", { name: "Desactivar BOCA-01" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("no puede abrir rendiciones nuevas ni sumar responsables");
    expect(dialog).toHaveTextContent("Lo que ya esté abierto se termina normalmente");
    expect(sent(spy, "PATCH", "/outlets/1")).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Desactivar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("BOCA-01 quedó desactivada");
    expect(sent(spy, "PATCH", "/outlets/1")).toEqual([{ active: false }]);
  });

  it("shows the history of changes with who and when", async () => {
    const detail: OutletDetail = {
      ...outlet({}),
      history: [
        {
          id: 1,
          event_type: "created",
          occurred_at: "2026-10-11T10:00:00Z",
          user_name: "Admin",
          before: null,
          after: { code: "BOCA-01", name: "Boca Centro" },
        },
        {
          id: 2,
          event_type: "name_changed",
          occurred_at: "2026-10-11T11:00:00Z",
          user_name: "Admin",
          before: { name: "Boca Centro" },
          after: { name: "Boca Centro Sur" },
        },
        {
          id: 3,
          event_type: "deactivated",
          occurred_at: "2026-10-11T12:00:00Z",
          user_name: "Admin",
          before: { active: true },
          after: { active: false },
        },
      ],
    };
    mockApi(me("viewer"), { "GET /outlets/1": () => jsonResponse(200, detail) });
    renderWithQuery(<OutletList />);
    await userEvent.click(await (await table()).findByRole("button", { name: "Historia de BOCA-01" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Nombre corregido")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("«Boca Centro» → «Boca Centro Sur»");
    expect(dialog).toHaveTextContent("Desactivada");
  });

  it("shows cards for mobile with the same bocas and actions", async () => {
    mockApi(me("admin"));
    renderWithQuery(<OutletList />);
    const cards = within(await screen.findByTestId("outlet-cards"));
    expect(cards.getByText("BOCA-01")).toBeInTheDocument();
    expect(cards.getByText("Boca Centro")).toBeInTheDocument();
    expect(cards.getByRole("button", { name: "Corregir BOCA-01" })).toBeInTheDocument();
    expect(screen.getByTestId("outlet-cards")).toHaveClass("md:hidden");
    expect(screen.getByTestId("outlet-table")).toHaveClass("hidden", "md:block");
  });
});

const responsibility = (overrides: Partial<Responsibility>): Responsibility => ({
  id: 1,
  outlet_id: 1,
  outlet_code: "BOCA-01",
  outlet_name: "Boca Centro",
  outlet_active: true,
  employee_id: 7,
  employee_name: "Ana Gómez",
  employee_active: true,
  valid_from: "2026-10-01",
  valid_to: null,
  created_at: "2026-10-01T10:00:00Z",
  created_by_name: "Admin",
  ended_at: null,
  ended_by_name: null,
  end_reason: null,
  ...overrides,
});

const ANA = responsibility({});
const BETO = responsibility({
  id: 2,
  employee_id: 8,
  employee_name: "Beto Ruiz",
  employee_active: false,
  valid_from: "2026-09-01",
  valid_to: "2026-09-30",
  ended_at: "2026-09-30T20:00:00Z",
  ended_by_name: "Admin",
  end_reason: "Pasó a reparto",
});

function responsibilityRoutes(active = true): Record<string, Handler> {
  return {
    "GET /outlets/1": () => jsonResponse(200, { ...outlet({ active }), history: [] }),
    "GET /settlements/outlets/1/responsibilities": (_, url) =>
      jsonResponse(200, url.searchParams.get("on") ? { items: [ANA], total: 1 } : { items: [ANA, BETO], total: 2 }),
    "GET /settlements/outlet-responsibilities/employees": () => jsonResponse(200, [{ id: 7, full_name: "Ana Gómez" }]),
    "POST /settlements/outlets/1/responsibilities": () =>
      jsonResponse(409, {
        code: "outlet_responsibility_overlap",
        message: "Ese empleado ya es responsable de esta boca desde el 01/10/2026 sin fecha de fin.",
        details: { responsibility_id: 1 },
        request_id: null,
      }),
    "POST /settlements/outlet-responsibilities/1/end": () =>
      jsonResponse(
        200,
        responsibility({ valid_to: "2026-10-11", end_reason: "Cambio", ended_at: "2026-10-11T12:00:00Z" }),
      ),
  };
}

describe("Responsables of a boca (R7-1)", () => {
  it("shows who answers for the boca on a day and its whole history, inclusive at both ends", async () => {
    const spy = mockApi(me("viewer", "viewer"), responsibilityRoutes());
    renderWithQuery(<OutletResponsibilities outletId={1} />);
    const day = within(await screen.findByRole("region", { name: "Responsables del día" }));
    expect(await day.findAllByText("Ana Gómez")).not.toHaveLength(0);
    const history = within(screen.getByRole("region", { name: "Historia de responsables" }));
    expect(await history.findAllByText(/Beto Ruiz/)).not.toHaveLength(0);
    expect(history.getAllByText("(empleado inactivo)")).not.toHaveLength(0);
    expect(history.getAllByText(/Terminada: Pasó a reparto/)).not.toHaveLength(0);
    expect(screen.getByText(/los dos incluidos/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sumar responsable|Terminar/ })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([input]) =>
          /\/settlements\/outlets\/1\/responsibilities\?on=\d{4}-\d{2}-\d{2}/.test(String(input)),
        ),
      ).toBe(true),
    );
  });

  it("lets a Rendiciones admin add a responsable and shows the backend's overlap message", async () => {
    const spy = mockApi(me("viewer", "admin"), responsibilityRoutes());
    renderWithQuery(<OutletResponsibilities outletId={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Sumar responsable" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Sumar responsable" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Elegí el empleado responsable.");
    await within(dialog).findByRole("option", { name: "Ana Gómez" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Empleado"), "7");
    await userEvent.click(within(dialog).getByRole("button", { name: "Sumar responsable" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Ese empleado ya es responsable de esta boca");
    const [body] = sent(spy, "POST", "/settlements/outlets/1/responsibilities");
    expect(body?.employee_id).toBe(7);
    expect(body?.valid_from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("ends an open responsibility once, with a reason", async () => {
    const spy = mockApi(me("admin"), responsibilityRoutes());
    renderWithQuery(<OutletResponsibilities outletId={1} />);
    const day = within(await screen.findByRole("region", { name: "Responsables del día" }));
    const [button] = await day.findAllByRole("button", { name: "Terminar Ana Gómez" });
    expect(screen.queryAllByRole("button", { name: "Terminar Beto Ruiz" })).toHaveLength(0); // already ended
    await userEvent.click(button!);
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Terminar" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Indicá el motivo");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Cambio");
    await userEvent.click(within(dialog).getByRole("button", { name: "Terminar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("La responsabilidad de Ana Gómez quedó terminada.");
    const [body] = sent(spy, "POST", "/settlements/outlet-responsibilities/1/end");
    expect(body?.reason).toBe("Cambio");
  });

  it("offers no new responsable on an inactive boca, but still ends the open ones", async () => {
    mockApi(me("admin"), responsibilityRoutes(false));
    renderWithQuery(<OutletResponsibilities outletId={1} />);
    expect(await screen.findByText(/La boca está desactivada: no suma responsables nuevos/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sumar responsable" })).not.toBeInTheDocument();
    expect(await screen.findAllByRole("button", { name: "Terminar Ana Gómez" })).not.toHaveLength(0);
  });

  it("shows nothing to a user without Rendiciones", async () => {
    const spy = mockApi(me("viewer", null), responsibilityRoutes());
    const { container } = renderWithQuery(<OutletResponsibilities outletId={1} />);
    await waitFor(() => expect(spy.mock.calls.some(([input]) => String(input).includes("/auth/me"))).toBe(true));
    expect(container).toBeEmptyDOMElement();
    expect(spy.mock.calls.some(([input]) => String(input).includes("/settlements/"))).toBe(false);
  });
});
