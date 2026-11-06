import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ImportJobDetail, ImportJobItem, ImportMessage } from "@/lib/api/imports";
import { ImportDetailView } from "@/modules/commercial/imports/import-detail";
import { ImportList } from "@/modules/commercial/imports/import-list";

import { jsonResponse, renderWithQuery } from "./render";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

const me = (role: Role) => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: "viewer",
  modules: [{ key: "commercial", name: "Inteligencia Comercial", role }],
});

const counts = {
  detected: 7415,
  processed: 7414,
  valid: 6,
  rejected: 0,
  duplicate: 0,
  skipped: 1,
  pending_resolution: 7408,
  inserted: 0,
  updated: 0,
  unchanged: 0,
  superseded: 0,
  warnings: 7671,
  errors: 0,
};

const salesJob = (overrides: Partial<ImportJobDetail> = {}): ImportJobDetail => ({
  id: 3,
  import_type: "commercial.store_sys.sales_lines",
  import_type_name: "STORE SYS · Ventas artículos/clientes",
  source_system: "STORE_SYS",
  status: "pending",
  parser_version: "store-sys-sales-2",
  file: {
    id: 3,
    original_filename: "ventas.xls",
    size_bytes: 1678336,
    detected_format: "XLS",
    sha256: "abc",
    uploaded_at: "2026-11-06T18:42:00Z",
  },
  created_by_name: "Enzo",
  confirmed_by_name: null,
  created_at: "2026-11-06T18:42:00Z",
  analyzed_at: "2026-11-06T18:42:02Z",
  confirmed_at: null,
  finished_at: null,
  counts,
  snapshot_date: null,
  period_from: "2026-10-01",
  period_to: "2026-10-31",
  scope: { declared: "FULL", note: null, days: ["2026-10-01", "2026-10-02", "2026-10-31"] },
  metadata: {
    preview: {
      replacement: { action: "insert", overlap_days: [], uncovered_days: [], reasons: [], replaces: [], lines: 0 },
      unknown_products: 20,
      products: { unknown: 20, unknown_lines: 257 },
      customers: { unresolved: 0, unresolved_lines: 0 },
      sellers: { unmapped: ["JAVIER TORRES"], unmapped_lines: 7408, without_seller_lines: 6 },
      price_lists: { unknown: [], unknown_lines: 0 },
    },
  },
  failure_reason: null,
  failure_detail: null,
  reprocess_of_job_id: null,
  duplicate_of_job_id: null,
  can_confirm: true,
  can_decide: false,
  can_reprocess: false,
  ...overrides,
});

const MESSAGES: ImportMessage[] = [
  {
    code: "product_unknown",
    severity: "warning",
    field: "product_code",
    message: "El artículo no está en el maestro de artículos.",
    rows: 257,
  },
];

const BLOCKED_PLAN = {
  action: "blocked",
  overlap_days: ["2026-10-24"],
  uncovered_days: [],
  reasons: ["new_scope_not_full"],
  replaces: [{ job_id: 2, scope: "FULL", days: ["2026-10-24"], lines: 598 }],
  lines: 598,
};

function mockApi(role: Role, routes: Record<string, Handler> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, me(role));
    const handler = routes[key];
    if (handler) return handler(init, url);
    if (key.endsWith("/messages")) return jsonResponse(200, MESSAGES);
    if (key.endsWith("/issues")) return jsonResponse(200, []);
    if (key.endsWith("/rows")) return jsonResponse(200, { items: [], total: 0 });
    return jsonResponse(404, { code: "not_found", message: key, details: null, request_id: null });
  });
}

const calls = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) => String(input).startsWith(`/api/v1${path}`) && (init?.method ?? "GET") === method,
  );

const item = (overrides: Partial<ImportJobItem> = {}): ImportJobItem => ({
  id: 3,
  import_type: "commercial.store_sys.sales_lines",
  import_type_name: "STORE SYS · Ventas artículos/clientes",
  status: "completed_with_warnings",
  original_filename: "ventas.xls",
  created_by_name: "Enzo",
  created_at: "2026-11-06T18:42:00Z",
  finished_at: "2026-11-06T18:42:07Z",
  rows_detected: 7415,
  rows_processed: 7414,
  rows_rejected: 0,
  failure_reason: null,
  ...overrides,
});

describe("Imports history and upload", () => {
  it("shows a viewer the history, with each import linked to its detail, and no upload", async () => {
    const api = mockApi("viewer", {
      "GET /imports": () => jsonResponse(200, { items: [item()], total: 1 }),
    });
    renderWithQuery(<ImportList />);
    const link = await screen.findByRole("link", { name: "ventas.xls" });
    expect(link).toHaveAttribute("href", "/comercial/datos/importaciones/3");
    expect(screen.getByText("Cargada con avisos")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Ventas" })).toBeInTheDocument();
    // The data rows of the file, not the header STORE SYS writes above them.
    expect(screen.getByRole("columnheader", { name: "Filas con datos" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "7.414" })).toBeInTheDocument();
    expect(screen.queryByText("7.415")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Subir y revisar" })).not.toBeInTheDocument();
    expect(String(calls(api, "GET", "/imports?")[0][0])).toContain("module=commercial");
  });

  it("uploads the file with what the operator declared and opens its preview", async () => {
    push.mockClear();
    let sent: FormData | undefined;
    const api = mockApi("operator", {
      "GET /imports": () => jsonResponse(200, { items: [], total: 0 }),
      "POST /imports": (init) => {
        sent = init?.body as FormData;
        return jsonResponse(201, salesJob({ id: 41 }));
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportList />);
    await screen.findByText("Todavía no hay importaciones");
    await user.click(screen.getByRole("button", { name: "Subir y revisar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Elegí el archivo");

    await user.upload(screen.getByLabelText("Archivo"), new File(["x"], "ventas.xls"));
    await user.selectOptions(screen.getByLabelText("Qué reporte es"), "commercial.store_sys.sales_lines");
    expect(screen.queryByLabelText(/Día de la foto/)).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Filtrado (por vendedor, zona…)"));
    await user.click(screen.getByRole("button", { name: "Subir y revisar" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Contá qué filtros usaste");
    expect(calls(api, "POST", "/imports")).toHaveLength(0);

    await user.type(screen.getByLabelText("Qué filtros usaste"), "solo RUTA 03");
    await user.click(screen.getByRole("button", { name: "Subir y revisar" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/comercial/datos/importaciones/41"));
    expect(sent?.get("import_type")).toBe("commercial.store_sys.sales_lines");
    expect(sent?.get("scope")).toBe("FILTERED");
    expect(sent?.get("scope_note")).toBe("solo RUTA 03");
    expect(sent?.get("snapshot_date")).toBeNull();
    expect((sent?.get("file") as File).name).toBe("ventas.xls");
  });
});

describe("Import detail", () => {
  it("explains a sales preview in business words and lists what needs attention", async () => {
    const api = mockApi("operator", { "GET /imports/3": () => jsonResponse(200, salesJob()) });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    expect(await screen.findByText("Qué encontró DistriCom")).toBeInTheDocument();
    expect(screen.getByText(/01\/10\/2026 al 31\/10\/2026 · 3 días con ventas/)).toBeInTheDocument();
    expect(screen.getByText("Completo, sin filtros")).toBeInTheDocument();
    expect(screen.getByText("20 artículos (257 líneas)")).toBeInTheDocument();
    expect(screen.getByText("JAVIER TORRES (7.408 líneas)")).toBeInTheDocument();
    expect(screen.getByText(/Carga ventas de 3 días que todavía no estaban cargados/)).toBeInTheDocument();
    expect(await screen.findByText("El artículo no está en el maestro de artículos.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ver filas" }));
    await waitFor(() => expect(calls(api, "GET", "/imports/3/rows")).toHaveLength(1));
    expect(String(calls(api, "GET", "/imports/3/rows")[0][0])).toContain("code=product_unknown");
  });

  it("confirms once: the button is disabled and says it is loading until the result arrives", async () => {
    let release: (value: Response) => void = () => undefined;
    let job = salesJob();
    const api = mockApi("operator", {
      "GET /imports/3": () => jsonResponse(200, job),
      "POST /imports/3/confirm": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    await user.click(await screen.findByRole("button", { name: "Confirmar y cargar" }));
    const running = await screen.findByRole("button", { name: /Cargando/ });
    expect(running).toBeDisabled();
    await user.click(running);
    expect(screen.getByRole("status")).toHaveTextContent("No cierres esta página");
    expect(calls(api, "POST", "/imports/3/confirm")).toHaveLength(1);

    job = salesJob({ status: "completed_with_warnings", can_confirm: false, counts: { ...counts, inserted: 7414 } });
    release(jsonResponse(200, job));
    expect(await screen.findByRole("heading", { name: "Resultado" })).toBeInTheDocument();
    expect(screen.getByText("Nuevas").nextElementSibling).toHaveTextContent("7.414");
    expect(screen.queryByRole("button", { name: /Confirmar y cargar/ })).not.toBeInTheDocument();
  });

  it("tells an operator a blocked sales file waits for an admin", async () => {
    const api = mockApi("operator", {
      "GET /imports/3": () => jsonResponse(200, salesJob()),
      "POST /imports/3/confirm": () =>
        jsonResponse(409, {
          code: "import_decision_required",
          message: "No se puede probar qué ventas reemplaza este archivo: un administrador tiene que decidir.",
          details: BLOCKED_PLAN,
          request_id: "r1",
        }),
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    await user.click(await screen.findByRole("button", { name: "Confirmar y cargar" }));
    expect(await screen.findByText(/un administrador tiene que decidir/)).toBeInTheDocument();
    expect(screen.getByText(/necesita la decisión de un administrador de Comercial/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirmar y cargar" })).not.toBeInTheDocument();
    expect(calls(api, "POST", "/imports/3/confirm")).toHaveLength(1);
  });

  it("lets an admin replace or add a blocked file, always with a reason", async () => {
    const blocked = salesJob({
      can_decide: true,
      scope: { declared: "FILTERED", note: "solo RUTA 03", days: ["2026-10-24"] },
      metadata: { preview: { replacement: BLOCKED_PLAN } },
    });
    let body: unknown;
    let job = blocked;
    const api = mockApi("admin", {
      "GET /imports/3": () => jsonResponse(200, job),
      "POST /imports/3/confirm": (init) => {
        body = JSON.parse(String(init?.body));
        job = { ...blocked, status: "completed_with_warnings", can_confirm: false, can_decide: false };
        return jsonResponse(200, job);
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    expect(await screen.findByText(/no puede probar qué reemplaza/)).toBeInTheDocument();
    expect(screen.getByText(/no está declarado como export completo/)).toBeInTheDocument();
    expect(screen.getByText("Filtrado: solo RUTA 03")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Aplicar decisión y cargar" });
    await user.click(apply);
    expect(screen.getByRole("alert")).toHaveTextContent("Elegí qué hacer");
    await user.click(screen.getByLabelText(/Cargar como adicionales/));
    await user.click(apply);
    expect(screen.getByRole("alert")).toHaveTextContent("Escribí el motivo");
    expect(calls(api, "POST", "/imports/3/confirm")).toHaveLength(0);
    await user.type(screen.getByLabelText("Motivo (obligatorio)"), "Días distintos");
    await user.click(apply);
    expect(await screen.findByRole("heading", { name: "Resultado" })).toBeInTheDocument();
    expect(body).toEqual({ decision: "additional", reason: "Días distintos" });
  });

  it("says the confirmation may have finished when the server does not answer", async () => {
    mockApi("operator", {
      "GET /imports/3": () => jsonResponse(200, salesJob()),
      "POST /imports/3/confirm": () => new Response("Gateway Timeout", { status: 504 }),
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    await user.click(await screen.findByRole("button", { name: "Confirmar y cargar" }));
    expect(await screen.findByText(/No recibimos la respuesta del servidor/)).toBeInTheDocument();
  });

  it("explains a file that could not be read and a file already imported, without stack traces", async () => {
    mockApi("operator", {
      "GET /imports/5": () =>
        jsonResponse(
          200,
          salesJob({
            id: 5,
            import_type: null,
            status: "failed",
            failure_reason: "type_not_detected",
            failure_detail: "No se reconoce el reporte. Elegí el tipo de importación.",
            can_confirm: false,
          }),
        ),
      "GET /imports/6": () =>
        jsonResponse(200, salesJob({ id: 6, status: "duplicate", duplicate_of_job_id: 3, can_confirm: false })),
    });
    const first = renderWithQuery(<ImportDetailView jobId={5} />);
    expect(await screen.findByText("No se reconoce el reporte. Elegí el tipo de importación.")).toBeInTheDocument();
    expect(screen.getByText(/Volvé a subirlo eligiendo qué reporte es/)).toBeInTheDocument();
    expect(screen.queryByText("Qué encontró DistriCom")).not.toBeInTheDocument();
    first.unmount();

    renderWithQuery(<ImportDetailView jobId={6} />);
    expect(await screen.findByText(/ya se importó con los mismos datos declarados/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver la importación #3" })).toHaveAttribute(
      "href",
      "/comercial/datos/importaciones/3",
    );
  });

  it("shows a viewer the preview without any action", async () => {
    mockApi("viewer", { "GET /imports/3": () => jsonResponse(200, salesJob({ can_confirm: false })) });
    renderWithQuery(<ImportDetailView jobId={3} />);
    expect(await screen.findByText("Qué encontró DistriCom")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirmar y cargar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Descartar" })).not.toBeInTheDocument();
    expect(screen.getByText(/Solo un operador o administrador de Comercial/)).toBeInTheDocument();
  });

  it("discards an analysis only after a second confirmation", async () => {
    let job = salesJob();
    const api = mockApi("operator", {
      "GET /imports/3": () => jsonResponse(200, job),
      "POST /imports/3/cancel": () => {
        job = salesJob({ status: "cancelled", can_confirm: false });
        return jsonResponse(200, job);
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<ImportDetailView jobId={3} />);
    await user.click(await screen.findByRole("button", { name: "Descartar" }));
    expect(calls(api, "POST", "/imports/3/cancel")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Sí, descartar" }));
    expect(await screen.findByText("Descartada")).toBeInTheDocument();
    expect(calls(api, "POST", "/imports/3/cancel")).toHaveLength(1);
  });
});
