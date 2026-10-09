import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ThirdPartyHistory } from "@/features/third-parties/third-party-history";
import { ThirdPartyList } from "@/features/third-parties/third-party-list";
import { isValidTaxId, normalizeTaxId, type ThirdParty } from "@/lib/api/third-parties";

import { jsonResponse, renderWithQuery } from "./render";

type Handler = (init: RequestInit | undefined, url: URL) => Response;

const me = (role: "admin" | "viewer") => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role,
  modules: [{ key: "settlements", name: "Rendiciones", role: "admin" }],
});

const thirdParty = (overrides: Partial<ThirdParty>): ThirdParty => ({
  id: 1,
  name: "Distribuidora Pérez",
  tax_id: "30712345671",
  tax_id_display: "30-71234567-1",
  notes: null,
  active: true,
  created_at: "2026-10-09T10:00:00Z",
  created_by_name: "Admin",
  updated_at: "2026-10-09T10:00:00Z",
  updated_by_name: "Admin",
  ...overrides,
});

const ACTIVE = [thirdParty({})];
const ALL = [...ACTIVE, thirdParty({ id: 2, name: "Fletes Sur", tax_id: null, tax_id_display: null, active: false })];

function mockApi(user: ReturnType<typeof me>, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, user);
    const handler = routes[key];
    if (handler) return handler(init, url);
    if (key === "GET /third-parties") return jsonResponse(200, url.searchParams.get("include_inactive") ? ALL : ACTIVE);
    return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const sent = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls
    .filter(([input, init]) => String(input) === `/api/v1${path}` && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);

describe("CUIT", () => {
  it("ignores separators and validates the check digit like the backend", () => {
    expect(normalizeTaxId("30-71234567-1")).toBe("30712345671");
    expect(normalizeTaxId("30.71234567/1 ")).toBe("30712345671");
    for (const valid of ["30712345671", "20123456786", "27301112225", "30500000003"])
      expect(isValidTaxId(valid)).toBe(true);
    expect(isValidTaxId("30712345672")).toBe(false);
    expect(isValidTaxId("3071234567")).toBe(false);
  });
});

describe("Terceros of the platform", () => {
  it("lets any user consult them, without administering", async () => {
    mockApi(me("viewer"));
    renderWithQuery(<ThirdPartyList />);
    expect((await screen.findAllByText("Distribuidora Pérez")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("30-71234567-1").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Ficha de Distribuidora Pérez" })[0]).toHaveAttribute(
      "href",
      "/terceros/1",
    );
    expect(screen.queryByRole("button", { name: "Nuevo tercero" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Desactivar/ })).not.toBeInTheDocument();
    expect(screen.getByText("Solo un administrador de DistriCom los modifica.")).toBeInTheDocument();
  });

  it("shows the deactivated ones with «Todos» and searches by name or CUIT", async () => {
    const spy = mockApi(me("viewer"));
    renderWithQuery(<ThirdPartyList />);
    await screen.findAllByText("Distribuidora Pérez");
    await userEvent.click(screen.getByRole("button", { name: "Todos" }));
    expect((await screen.findAllByText("Fletes Sur")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Desactivado").length).toBeGreaterThan(0);
    await userEvent.type(screen.getByLabelText("Buscar"), "3071");
    await waitFor(() => expect(spy.mock.calls.some(([input]) => String(input).includes("q=3071"))).toBe(true));
  });

  it("a platform admin registers one, confirming a homonym with a reason", async () => {
    let attempt = 0;
    const spy = mockApi(me("admin"), {
      "POST /third-parties": () =>
        ++attempt === 1
          ? jsonResponse(409, {
              code: "third_party_possible_duplicate",
              message: "x",
              details: {
                candidates: [{ third_party_id: 2, name: "Fletes Sur", tax_id_display: null, active: false }],
              },
              request_id: null,
            })
          : jsonResponse(201, thirdParty({ id: 3, name: "Fletes Sur", tax_id: null, tax_id_display: null })),
    });
    renderWithQuery(<ThirdPartyList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nuevo tercero" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre o razón social"), "  Fletes   Sur ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar tercero" }));
    expect(await within(dialog).findByText(/Ya hay un tercero con el nombre «Fletes Sur»/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Sin CUIT · desactivado/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Usar este" })).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Si es otro tercero, indicá por qué"), "Otro dueño");
    await userEvent.click(within(dialog).getByRole("button", { name: "Es otro tercero: registrarlo" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Tercero Fletes Sur registrado.");
    expect(sent(spy, "POST", "/third-parties")).toEqual([
      { name: "Fletes Sur", tax_id: null, notes: null },
      { name: "Fletes Sur", tax_id: null, notes: null, confirm_not_duplicate: true, duplicate_reason: "Otro dueño" },
    ]);
  });

  it("a repeated CUIT is a hard stop, naming the deactivated one", async () => {
    mockApi(me("admin"), {
      "POST /third-parties": () =>
        jsonResponse(409, {
          code: "third_party_tax_id_taken",
          message: "Ya existe un tercero con ese CUIT.",
          details: { third_party_id: 2, active: false },
          request_id: null,
        }),
    });
    renderWithQuery(<ThirdPartyList />);
    await userEvent.click(await screen.findByRole("button", { name: "Nuevo tercero" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre o razón social"), "Otro");
    await userEvent.type(within(dialog).getByLabelText("CUIT (opcional)"), "30712345671");
    await userEvent.click(within(dialog).getByRole("button", { name: "Registrar tercero" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Ya existe un tercero con ese CUIT. Está desactivado",
    );
    expect(within(dialog).queryByRole("button", { name: /Es otro tercero/ })).not.toBeInTheDocument();
  });

  it("a correction sends only what changed; an empty CUIT removes it", async () => {
    const spy = mockApi(me("admin"), {
      "PATCH /third-parties/1": () => jsonResponse(200, thirdParty({ tax_id: null, tax_id_display: null })),
    });
    renderWithQuery(<ThirdPartyList />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Corregir Distribuidora Pérez" }))[0]);
    const dialog = screen.getByRole("dialog");
    await userEvent.clear(within(dialog).getByLabelText("CUIT (opcional)"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(sent(spy, "PATCH", "/third-parties/1")).toEqual([{ tax_id: "" }]));
  });

  it("deactivates without deleting", async () => {
    const spy = mockApi(me("admin"), {
      "PATCH /third-parties/1": () => jsonResponse(200, thirdParty({ active: false })),
    });
    renderWithQuery(<ThirdPartyList />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Desactivar Distribuidora Pérez" }))[0]);
    expect(await screen.findByRole("status")).toHaveTextContent("quedó desactivado: no se ofrece para pagos nuevos.");
    expect(sent(spy, "PATCH", "/third-parties/1")).toEqual([{ active: false }]);
  });
});

describe("third party history", () => {
  it("names the origin of a quick create and a confirmed homonym", () => {
    renderWithQuery(
      <ThirdPartyHistory
        history={[
          {
            id: 1,
            event_type: "created",
            occurred_at: "2026-10-09T10:00:00Z",
            user_name: "Oscar",
            before: null,
            after: {
              name: "Fletes Sur",
              tax_id: "30712345671",
              origin: "settlements.payment",
              duplicate_confirmation: { candidates: [2], reason: "Otro dueño" },
            },
          },
          {
            id: 2,
            event_type: "tax_id_changed",
            occurred_at: "2026-10-09T11:00:00Z",
            user_name: "Admin",
            before: { tax_id: null },
            after: { tax_id: "30712345671" },
          },
        ]}
      />,
    );
    expect(screen.getByText("Fletes Sur · CUIT 30-71234567-1")).toBeInTheDocument();
    expect(screen.getByText("Registrado desde un pago de Rendiciones")).toBeInTheDocument();
    expect(screen.getByText("Confirmado como distinto de un homónimo: Otro dueño")).toBeInTheDocument();
    expect(screen.getByText("— → 30-71234567-1")).toBeInTheDocument();
  });
});
