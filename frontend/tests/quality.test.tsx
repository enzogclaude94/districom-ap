import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Issue, IssueDetail, QualityCode } from "@/lib/api/quality";
import { COMMERCIAL_NAV } from "@/modules/commercial/nav";
import { QualityDetailView } from "@/modules/commercial/quality/quality-detail";
import { QualityList } from "@/modules/commercial/quality/quality-list";

import { jsonResponse, renderWithQuery } from "./render";

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => search.params,
}));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "commercial", name: "Inteligencia Comercial", role }],
});

const customers = { key: "customers", label: "Clientes" };
const sellers = { key: "sellers", label: "Vendedores y preventistas" };

const codes: QualityCode[] = [
  {
    code: "commercial.customer.without_zone",
    label: "Cliente sin zona",
    severity: "warning",
    group: customers,
    description: "El maestro trajo el cliente sin zona.",
    action: "Cargá la zona del cliente en STORE SYS: se corrige con la próxima foto del maestro.",
    closes_when: "Cuando un maestro posterior trae el cliente con zona.",
    fix: null,
  },
  {
    code: "commercial.seller.unmapped",
    label: "Vendedor sin vincular",
    severity: "warning",
    group: sellers,
    description: "Un vendedor de STORE SYS no está vinculado con un empleado.",
    action: "Vinculalo con su empleado en Mapeos.",
    closes_when: "Cuando el código se confirma o se excluye en Mapeos.",
    fix: "mapping",
  },
];

const issue = (overrides: Partial<Issue> = {}): Issue => ({
  id: 31,
  code: "commercial.seller.unmapped",
  label: "Vendedor sin vincular",
  severity: "warning",
  status: "open",
  group: sellers,
  subject: "Vendedor STORE SYS VENDEDOR UNO",
  occurrences: 619,
  first_seen_at: "2026-11-06T12:00:00Z",
  last_seen_at: "2026-11-11T12:00:00Z",
  source: {
    job_id: 12,
    import_type: "commercial.store_sys.customers",
    import_label: "STORE SYS · Clientes",
    row_id: null,
    row_number: null,
    sheet: null,
  },
  mapping_id: 44,
  fix: "mapping",
  action: "Vinculalo con su empleado en Mapeos.",
  resolved_at: null,
  resolved_by_name: null,
  ...overrides,
});

const detail = (overrides: Partial<IssueDetail> = {}): IssueDetail => ({
  ...issue(),
  description: "Un vendedor de STORE SYS no está vinculado con un empleado.",
  closes_when: "Cuando el código se confirma o se excluye en Mapeos.",
  facts: { lines: 12 },
  resolution_note: null,
  resolved_automatically: false,
  dedupe_key: null,
  entity_type: null,
  entity_id: null,
  ...overrides,
});

const summary = {
  open: { error: { issues: 1, occurrences: 1 }, warning: { issues: 790, occurrences: 1925 } },
  open_total: { issues: 794, occurrences: 1930 },
  resolved: { issues: 4, occurrences: 9 },
  ignored: { issues: 2, occurrences: 2 },
};

function mockApi(role: Role, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, me(role));
    const handler = routes[key];
    if (handler) return handler(init, url);
    if (key === "GET /quality/codes") return jsonResponse(200, codes);
    if (key === "GET /quality/issues/summary") return jsonResponse(200, summary);
    return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const calls = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) => String(input).startsWith(`/api/v1${path}`) && (init?.method ?? "GET") === method,
  );

const lastUrl = (spy: ReturnType<typeof mockApi>, path: string) => String(calls(spy, "GET", path).at(-1)?.[0]);

describe("Datos navigation", () => {
  it("has Calidad after Mapeos, then Maestros", () => {
    const datos = COMMERCIAL_NAV.find((s) => s.label === "Datos");
    expect(datos?.items?.map((i) => i.label)).toEqual(["Importaciones", "Mapeos", "Calidad", "Maestros"]);
    expect(datos?.items?.find((i) => i.label === "Maestros")?.href).toBe("/comercial/datos/maestros");
    expect(datos?.items?.find((i) => i.label === "Calidad")?.href).toBe("/comercial/datos/calidad");
  });
});

describe("Calidad queue", () => {
  it("separates problems from occurrences in the cards", async () => {
    mockApi("viewer", { "GET /quality/issues": () => jsonResponse(200, { items: [issue()], total: 1 }) });
    renderWithQuery(<QualityList />);
    const cards = screen.getByLabelText("Resumen de problemas");
    expect(
      await within(cards).findByRole("button", { name: /Advertencias abiertos\s*790\s*1\.925 ocurrencias/ }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(within(cards).getByRole("button", { name: /Errores abiertos\s*1\s*1 ocurrencia$/ })).toBeInTheDocument();
    expect(
      within(cards).getByRole("button", { name: /Informativos abiertos\s*0\s*0 ocurrencias/ }),
    ).toBeInTheDocument();
    expect(within(cards).getByRole("button", { name: /Resueltos\s*4\s*9 ocurrencias/ })).toBeInTheDocument();
    expect(within(cards).getByRole("button", { name: /Ignorados\s*2/ })).toBeInTheDocument();
    expect(within(cards).getByText("794 problemas abiertos · 1.930 ocurrencias")).toBeInTheDocument();
  });

  it("shows the business columns, never technical keys", async () => {
    mockApi("viewer", { "GET /quality/issues": () => jsonResponse(200, { items: [issue()], total: 1 }) });
    renderWithQuery(<QualityList />);
    expect(await screen.findByRole("link", { name: "Vendedor sin vincular" })).toHaveAttribute(
      "href",
      "/comercial/datos/calidad/31",
    );
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Problema",
      "Severidad",
      "Estado",
      "Afecta a",
      "Ocurrencias",
      "Primera vez",
      "Última vez",
      "Origen",
      "Acción recomendada",
    ]);
    expect(screen.getByRole("cell", { name: "619" })).toBeInTheDocument();
    expect(screen.getByText("STORE SYS · Clientes · importación #12")).toBeInTheDocument();
    expect(screen.queryByText(/commercial\.seller/)).not.toBeInTheDocument();
    expect(screen.queryByText(/mapping:/)).not.toBeInTheDocument();
  });

  it("asks the backend for the open problems sorted by severity and filters there", async () => {
    const api = mockApi("viewer", { "GET /quality/issues": () => jsonResponse(200, { items: [issue()], total: 1 }) });
    const user = userEvent.setup();
    renderWithQuery(<QualityList />);
    await screen.findByRole("link", { name: "Vendedor sin vincular" });
    const first = lastUrl(api, "/quality/issues?");
    expect(first).toContain("module=commercial");
    expect(first).toContain("status=open");
    expect(first).not.toContain("sort="); // the backend's default: error → warning → info, oldest first

    await user.selectOptions(screen.getByLabelText("Afecta a"), "sellers");
    await user.selectOptions(screen.getByLabelText("Tipo de problema"), "commercial.seller.unmapped");
    await user.selectOptions(screen.getByLabelText("Severidad"), "warning");
    await user.type(screen.getByLabelText("Visto desde"), "2026-11-01");
    await user.type(screen.getByLabelText("Buscar por tipo de problema"), "vendedor{Enter}");
    await waitFor(() => {
      const last = lastUrl(api, "/quality/issues?");
      for (const part of [
        "group=sellers",
        "code=commercial.seller.unmapped",
        "severity=warning",
        "seen_from=2026-11-01",
        "q=vendedor",
      ])
        expect(last).toContain(part);
    });
    const counted = lastUrl(api, "/quality/issues/summary");
    expect(counted).toContain("group=sellers");
    expect(counted).not.toContain("status=");
    expect(counted).not.toContain("severity=");

    await user.click(screen.getByRole("button", { name: /Ignorados/ }));
    await waitFor(() => expect(lastUrl(api, "/quality/issues?")).toContain("status=ignored"));
    expect(lastUrl(api, "/quality/issues?")).not.toContain("severity=");
  });

  it("starts filtered by the import it comes from", async () => {
    search.params = new URLSearchParams("job_id=12");
    const api = mockApi("viewer", { "GET /quality/issues": () => jsonResponse(200, { items: [], total: 0 }) });
    const user = userEvent.setup();
    renderWithQuery(<QualityList />);
    expect(await screen.findByText("No hay problemas abiertos")).toBeInTheDocument();
    expect(lastUrl(api, "/quality/issues?")).toContain("job_id=12");
    expect(lastUrl(api, "/quality/issues/summary")).toContain("job_id=12");
    await user.click(screen.getByRole("button", { name: "Ver todas las importaciones" }));
    await waitFor(() => expect(lastUrl(api, "/quality/issues?")).not.toContain("job_id"));
    search.params = new URLSearchParams();
  });
});

describe("Calidad detail", () => {
  it("explains the problem, its origin and links to the exact mapping", async () => {
    mockApi("viewer", { "GET /quality/issues/31": () => jsonResponse(200, detail()) });
    renderWithQuery(<QualityDetailView issueId={31} />);
    expect(await screen.findByRole("heading", { name: "Vendedor sin vincular" })).toBeInTheDocument();
    expect(screen.getByText("Vendedor STORE SYS VENDEDOR UNO")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "STORE SYS · Clientes · importación #12" })).toHaveAttribute(
      "href",
      "/comercial/datos/importaciones/12",
    );
    expect(screen.getByRole("link", { name: "Corregir en Mapeos" })).toHaveAttribute(
      "href",
      "/comercial/datos/mapeos/44",
    );
    expect(screen.getByText(/Se cierra solo: cuando el código se confirma/)).toBeInTheDocument();
    expect(screen.getByText("Líneas afectadas")).toBeInTheDocument();
    // A viewer reads only.
    expect(screen.queryByRole("button", { name: "Ignorar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Marcar resuelto" })).not.toBeInTheDocument();
    expect(screen.queryByText("Referencia técnica")).not.toBeInTheDocument();
  });

  it("links to the import when the fix is there", async () => {
    mockApi("viewer", {
      "GET /quality/issues/32": () =>
        jsonResponse(200, detail({ id: 32, fix: "import", mapping_id: null, code: "commercial.document.changed" })),
    });
    renderWithQuery(<QualityDetailView issueId={32} />);
    expect(await screen.findByRole("link", { name: "Ver la importación" })).toHaveAttribute(
      "href",
      "/comercial/datos/importaciones/12",
    );
  });

  it("links a product without category to that exact product in Maestros, with no editor here", async () => {
    mockApi("operator", {
      "GET /quality/issues/33": () =>
        jsonResponse(
          200,
          detail({
            id: 33,
            code: "commercial.product.without_category",
            label: "Producto sin categoría",
            fix: "masters",
            fix_href: "/comercial/datos/maestros/productos/123",
            mapping_id: null,
            subject: "Artículo 123 · AGUA 500",
            action: "Asignale una categoría en Maestros.",
          }),
        ),
    });
    renderWithQuery(<QualityDetailView issueId={33} />);
    const link = await screen.findByRole("link", { name: "Corregir en Maestros" });
    expect(link).toHaveAttribute("href", "/comercial/datos/maestros/productos/123");
    expect(screen.queryByRole("link", { name: /Mapeos|importación$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("never links a masters fix outside Maestros", async () => {
    mockApi("viewer", {
      "GET /quality/issues/34": () =>
        jsonResponse(200, detail({ id: 34, fix: "masters", fix_href: "https://example.com/x", mapping_id: null })),
    });
    renderWithQuery(<QualityDetailView issueId={34} />);
    await screen.findByText("Vinculalo con su empleado en Mapeos.");
    expect(screen.queryByRole("link", { name: "Corregir en Maestros" })).not.toBeInTheDocument();
  });

  it("lets an operator ignore with a required note, but not resolve", async () => {
    let state = detail({ dedupe_key: "mapping:44" });
    const api = mockApi("operator", {
      "GET /quality/issues/31": () => jsonResponse(200, state),
      "POST /quality/issues/31/ignore": () => {
        state = detail({
          status: "ignored",
          dedupe_key: "mapping:44",
          resolution_note: "Es la boca de expendio",
          resolved_by_name: "Usuario",
          resolved_at: "2026-11-20T15:00:00Z",
        });
        return jsonResponse(200, state);
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<QualityDetailView issueId={31} />);
    await user.click(await screen.findByRole("button", { name: "Ignorar" }));
    expect(screen.queryByRole("button", { name: "Marcar resuelto" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirmar ignorar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escribí una nota");
    expect(calls(api, "POST", "/quality/issues/31/ignore")).toHaveLength(0);

    await user.type(screen.getByLabelText("Nota (obligatoria)"), "Es la boca de expendio");
    await user.click(screen.getByRole("button", { name: "Confirmar ignorar" }));
    expect(await screen.findByText(/Ignorado a mano por Usuario/)).toBeInTheDocument();
    expect(screen.getByText("Es la boca de expendio")).toBeInTheDocument();
    expect(JSON.parse(String(calls(api, "POST", "/quality/issues/31/ignore")[0][1]?.body))).toEqual({
      note: "Es la boca de expendio",
    });
    expect(screen.queryByRole("button", { name: "Ignorar" })).not.toBeInTheDocument();
    expect(screen.getByText("Referencia técnica")).toBeInTheDocument();
  });

  it("lets an admin resolve by hand", async () => {
    let state = detail();
    const api = mockApi("admin", {
      "GET /quality/issues/31": () => jsonResponse(200, state),
      "POST /quality/issues/31/resolve": () => {
        state = detail({ status: "resolved", resolution_note: "Corregido en STORE SYS", resolved_by_name: "Usuario" });
        return jsonResponse(200, state);
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<QualityDetailView issueId={31} />);
    expect(await screen.findByRole("button", { name: "Ignorar" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Marcar resuelto" }));
    await user.type(screen.getByLabelText("Nota (obligatoria)"), "Corregido en STORE SYS");
    await user.click(screen.getByRole("button", { name: "Confirmar resuelto" }));
    expect(await screen.findByText(/Resuelto a mano por Usuario/)).toBeInTheDocument();
    expect(calls(api, "POST", "/quality/issues/31/resolve")).toHaveLength(1);
  });

  it("shows a problem that closed by itself as resolved automatically", async () => {
    mockApi("admin", {
      "GET /quality/issues/31": () =>
        jsonResponse(
          200,
          detail({ status: "resolved", resolved_automatically: true, resolved_at: "2026-11-20T15:00:00Z" }),
        ),
    });
    renderWithQuery(<QualityDetailView issueId={31} />);
    expect(await screen.findByText(/Se resolvió solo .*: la causa se corrigió/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ignorar" })).not.toBeInTheDocument();
  });

  it("refuses a stale decision and shows the current status", async () => {
    let state: IssueDetail = detail();
    mockApi("admin", {
      "GET /quality/issues/31": () => jsonResponse(200, state),
      "POST /quality/issues/31/ignore": () => {
        state = detail({ status: "resolved", resolved_automatically: true });
        return jsonResponse(409, {
          code: "issue_not_open",
          message: "Ese problema ya no está abierto.",
          details: { issue_id: 31, status: "resolved" },
          request_id: null,
        });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<QualityDetailView issueId={31} />);
    await user.click(await screen.findByRole("button", { name: "Ignorar" }));
    await user.type(screen.getByLabelText("Nota (obligatoria)"), "Revisado");
    await user.click(screen.getByRole("button", { name: "Confirmar ignorar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ahora está resuelto");
    expect(await screen.findByText(/Se resolvió solo/)).toBeInTheDocument();
  });
});
