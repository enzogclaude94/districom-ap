import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LinkTarget, Mapping } from "@/lib/api/mappings";
import { MappingDetailView } from "@/modules/commercial/mappings/mapping-detail";
import { MappingList } from "@/modules/commercial/mappings/mapping-list";
import { COMMERCIAL_NAV } from "@/modules/commercial/nav";

import { jsonResponse, renderWithQuery } from "./render";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "commercial", name: "Inteligencia Comercial", role }],
});

const ana: LinkTarget = {
  id: 7,
  name: "Ana Preventista",
  active: true,
  legal_name: null,
  store_sys_code: null,
  zone: null,
  functions: ["Preventista"],
  is_salesperson: true,
};

const mapping = (overrides: Partial<Mapping> = {}): Mapping => ({
  id: 11,
  entity_type: "employee",
  source_system: "AXUM",
  namespace: "salesperson",
  external_id: "01",
  external_label: "Vendedor 01",
  entity_id: null,
  status: "pending",
  method: null,
  candidates: [],
  decided_by: null,
  decided_at: null,
  decision_reason: null,
  created_at: "2026-11-11T21:00:00Z",
  updated_at: "2026-11-11T21:00:00Z",
  kind_label: "Preventista AXUM",
  first_seen_job_id: 5,
  last_seen_job_id: 5,
  first_seen_at: "2026-11-11T21:00:00Z",
  last_seen_at: "2026-11-11T21:00:00Z",
  decided_by_name: null,
  entity: null,
  candidate_entities: [],
  facts: [{ kind: "commercial.no_purchase_events", label: "eventos de no compra", total: 655, linked: 0 }],
  ...overrides,
});

const counts = { pending: 3, ambiguous: 0, confirmed: 313, rejected: 0, excluded: 1 };

function mockApi(role: Role, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, me(role));
    const handler = routes[key];
    if (handler) return handler(init, url);
    if (key === "GET /commercial/mappings/summary") return jsonResponse(200, { counts });
    return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const calls = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) => String(input).startsWith(`/api/v1${path}`) && (init?.method ?? "GET") === method,
  );

const bodyOf = (init: RequestInit | undefined) => JSON.parse(String(init?.body)) as Record<string, unknown>;

describe("Datos navigation", () => {
  it("shows only Importaciones and Mapeos", () => {
    const datos = COMMERCIAL_NAV.find((s) => s.label === "Datos");
    expect(datos?.items?.map((i) => [i.label, i.href])).toEqual([
      ["Importaciones", "/comercial/datos/importaciones"],
      ["Mapeos", "/comercial/datos/mapeos"],
    ]);
  });
});

describe("Mapeos queue", () => {
  it("shows the cards, the codes in business words and filters through the backend", async () => {
    const api = mockApi("viewer", {
      "GET /commercial/mappings": () => jsonResponse(200, { items: [mapping()], total: 1 }),
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingList />);
    expect(await screen.findByRole("link", { name: "01" })).toHaveAttribute("href", "/comercial/datos/mapeos/11");
    expect(screen.getByRole("cell", { name: "Preventista AXUM" })).toBeInTheDocument();
    expect(screen.getByText("655 eventos de no compra")).toBeInTheDocument();
    expect(screen.queryByText(/employee/)).not.toBeInTheDocument();
    const cards = screen.getByLabelText("Resumen por estado");
    expect(within(cards).getByRole("button", { name: /Pendientes\s*3/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(cards).getByRole("button", { name: /Confirmados\s*313/ })).toBeInTheDocument();
    // A viewer reads: no selection, no batch.
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(String(calls(api, "GET", "/commercial/mappings?")[0][0])).toContain("status=pending");

    await user.selectOptions(screen.getByLabelText("Tipo de código"), "employee/STORE_SYS/seller");
    await user.type(screen.getByLabelText("Buscar código o nombre"), "boca{Enter}");
    await waitFor(() => {
      const last = String(calls(api, "GET", "/commercial/mappings?").at(-1)?.[0]);
      expect(last).toContain("source_system=STORE_SYS");
      expect(last).toContain("entity_type=employee");
      expect(last).toContain("namespace=seller");
      expect(last).toContain("q=boca");
    });
    const summary = String(calls(api, "GET", "/commercial/mappings/summary").at(-1)?.[0]);
    expect(summary).toContain("namespace=seller");
    expect(summary).not.toContain("status=");
  });

  it("confirms selected proposals only after a review, all or nothing", async () => {
    const proposed = mapping({ id: 21, candidates: [7], candidate_entities: [ana] });
    const without = mapping({ id: 22, external_id: "02", facts: [] });
    let sent: Record<string, unknown> | undefined;
    const api = mockApi("operator", {
      "GET /commercial/mappings": () => jsonResponse(200, { items: [proposed, without], total: 2 }),
      "POST /commercial/mappings/batch-confirm": (init) => {
        sent = bodyOf(init);
        return jsonResponse(200, {
          confirmed: 1,
          failed: 0,
          items: [{ mapping_id: 21, entity_id: 7, facts_updated: { "commercial.no_purchase_events": 655 } }],
          facts_updated: { "commercial.no_purchase_events": 655 },
          issues_closed: 1,
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingList />);
    const box = await screen.findByRole("checkbox", { name: "Elegir Preventista AXUM 01" });
    expect(screen.getByRole("checkbox", { name: "Elegir Preventista AXUM 02" })).toBeDisabled();
    expect(box).not.toBeChecked(); // never preselected
    expect(screen.getByText("Propuesto: Ana Preventista")).toBeInTheDocument();
    await user.click(box);
    await user.click(screen.getByRole("button", { name: "Revisar y confirmar los candidatos propuestos" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("cell", { name: "Ana Preventista" })).toBeInTheDocument();
    expect(within(dialog).getByText("655 eventos de no compra históricos quedarán asociados.")).toBeInTheDocument();
    expect(calls(api, "POST", "/commercial/mappings/batch-confirm")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Confirmar 1 vínculo" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Se confirmaron 1 vínculos. 655 datos históricos");
    expect(sent).toEqual({ items: [{ mapping_id: 21, entity_id: 7 }] });
  });

  it("says nothing was confirmed when the batch fails", async () => {
    mockApi("operator", {
      "GET /commercial/mappings": () =>
        jsonResponse(200, { items: [mapping({ id: 21, candidates: [7], candidate_entities: [ana] })], total: 1 }),
      "POST /commercial/mappings/batch-confirm": () =>
        jsonResponse(409, {
          code: "mapping_not_open",
          message: "No se confirmó ningún vínculo: Ese vínculo ya no está abierto: alguien lo decidió.",
          details: { mapping_id: 21 },
          request_id: null,
        }),
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingList />);
    await user.click(await screen.findByRole("checkbox", { name: "Elegir Preventista AXUM 01" }));
    await user.click(screen.getByRole("button", { name: "Revisar y confirmar los candidatos propuestos" }));
    await user.click(screen.getByRole("button", { name: "Confirmar 1 vínculo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se confirmó ningún vínculo");
  });
});

describe("Mapeo detail", () => {
  it("links a salesperson with the impact the backend counts", async () => {
    let sent: Record<string, unknown> | undefined;
    let detail = mapping();
    const api = mockApi("operator", {
      "GET /commercial/mappings/11": () => jsonResponse(200, detail),
      "GET /commercial/mappings/11/targets": () => jsonResponse(200, { items: [ana] }),
      "GET /commercial/mappings/11/impact": () =>
        jsonResponse(200, {
          mapping_id: 11,
          status: "pending",
          entity_id: 7,
          facts: [
            {
              kind: "commercial.no_purchase_events",
              label: "eventos de no compra",
              total: 655,
              linked: 0,
              on_target: 0,
              changes_on_link: 655,
              changes_on_unlink: 0,
            },
          ],
          on_link: 655,
          on_unlink: 0,
        }),
      "POST /commercial/mappings/11/confirm": (init) => {
        sent = bodyOf(init);
        detail = mapping({ status: "confirmed", entity_id: 7, entity: ana });
        return jsonResponse(200, {
          ...detail,
          resolution: { facts_updated: { "commercial.no_purchase_events": 655 }, issues_closed: 1 },
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingDetailView mappingId={11} />);
    expect(await screen.findByRole("heading", { name: "Preventista AXUM 01" })).toBeInTheDocument();
    expect(screen.getByText("655 eventos de no compra")).toBeInTheDocument();
    expect(screen.getByText("Pendiente")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Vincular" }));
    await user.click(screen.getByRole("button", { name: "Buscar" }));
    await user.click(await screen.findByRole("button", { name: "Elegir" }));
    expect(
      await screen.findByText("655 eventos de no compra históricos quedarán asociados a este preventista."),
    ).toBeInTheDocument();
    expect(String(calls(api, "GET", "/commercial/mappings/11/impact")[0][0])).toContain("entity_id=7");
    await user.click(screen.getByRole("button", { name: "Confirmar vínculo" }));
    expect(await screen.findByText(/655 datos históricos se actualizaron/)).toBeInTheDocument();
    expect(sent).toEqual({ entity_id: 7, expected_status: "pending" });
    expect(screen.getByText("Confirmado")).toBeInTheDocument();
  });

  it("refreshes the real state when another tab decided first", async () => {
    let detail = mapping();
    mockApi("operator", {
      "GET /commercial/mappings/11": () => jsonResponse(200, detail),
      "POST /commercial/mappings/11/exclude": () => {
        detail = mapping({ status: "confirmed", entity_id: 7, entity: ana });
        return jsonResponse(409, {
          code: "mapping_changed",
          message: "Alguien decidió este vínculo mientras lo revisabas. Revisá el estado actual.",
          details: { status: "confirmed" },
          request_id: null,
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingDetailView mappingId={11} />);
    await user.click(await screen.findByRole("button", { name: "Excluir código" }));
    await user.click(screen.getByRole("button", { name: "Excluir" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Indicá el motivo");
    await user.type(screen.getByLabelText("Motivo (obligatorio)"), "No es una persona");
    await user.click(screen.getByRole("button", { name: "Excluir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Alguien decidió este vínculo");
    expect(await screen.findByText("Confirmado")).toBeInTheDocument();
    expect(screen.getByText("Ana Preventista")).toBeInTheDocument();
  });

  it("gives a viewer no decision and an operator no reassign", async () => {
    mockApi("viewer", { "GET /commercial/mappings/11": () => jsonResponse(200, mapping()) });
    const { unmount } = renderWithQuery(<MappingDetailView mappingId={11} />);
    await screen.findByRole("heading", { name: "Preventista AXUM 01" });
    expect(screen.queryByRole("button", { name: "Vincular" })).not.toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();

    const confirmed = mapping({ status: "confirmed", entity_id: 7, entity: ana, method: "manual" });
    mockApi("operator", { "GET /commercial/mappings/11": () => jsonResponse(200, confirmed) });
    renderWithQuery(<MappingDetailView mappingId={11} />);
    expect(await screen.findByText(/Solo un admin de Comercial/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reasignar" })).not.toBeInTheDocument();
  });

  it("asks an admin for a reason to reassign", async () => {
    let sent: Record<string, unknown> | undefined;
    const bruno = { ...ana, id: 8, name: "Bruno", functions: [], is_salesperson: false };
    mockApi("admin", {
      "GET /commercial/mappings/11": () =>
        jsonResponse(200, mapping({ status: "confirmed", entity_id: 7, entity: ana, method: "manual" })),
      "GET /commercial/mappings/11/targets": () => jsonResponse(200, { items: [ana, bruno] }),
      "GET /commercial/mappings/11/impact": () =>
        jsonResponse(200, { mapping_id: 11, status: "confirmed", entity_id: 8, facts: [], on_link: 0, on_unlink: 0 }),
      "POST /commercial/mappings/11/reassign": (init) => {
        sent = bodyOf(init);
        return jsonResponse(200, mapping({ status: "confirmed", entity_id: 8, entity: bruno }));
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<MappingDetailView mappingId={11} />);
    await user.click(await screen.findByRole("button", { name: "Reasignar" }));
    await user.click(screen.getByRole("button", { name: "Buscar" }));
    const results = await screen.findByRole("list", { name: "Resultados" });
    await user.click(within(results).getAllByRole("button", { name: "Elegir" })[1]);
    await user.click(screen.getByRole("button", { name: "Confirmar reasignación" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Indicá el motivo");
    await user.type(screen.getByLabelText("Motivo (obligatorio)"), "Era Bruno");
    await user.click(screen.getByRole("button", { name: "Confirmar reasignación" }));
    await waitFor(() => expect(sent).toEqual({ entity_id: 8, reason: "Era Bruno", expected_status: "confirmed" }));
  });
});
