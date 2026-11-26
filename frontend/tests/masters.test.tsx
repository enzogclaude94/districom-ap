import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Customer, Product, ProductDetail, Zone, ZoneDetail } from "@/lib/api/masters";
import { CategoriesView } from "@/modules/commercial/masters/categories";
import { CustomerDetailView, CustomerList } from "@/modules/commercial/masters/customers";
import { DocumentTypesView } from "@/modules/commercial/masters/document-types";
import { PriceListsView } from "@/modules/commercial/masters/price-lists";
import { ProductDetailView, ProductList } from "@/modules/commercial/masters/products";
import { ReasonsView } from "@/modules/commercial/masters/reasons";
import { SalespeopleView } from "@/modules/commercial/masters/salespeople";
import { MastersTabs } from "@/modules/commercial/masters/shared";
import { RULES_REMINDER, ZoneDetailView, ZoneList } from "@/modules/commercial/masters/zones";

import { jsonResponse, renderWithQuery } from "./render";

const nav = vi.hoisted(() => ({ pathname: "/comercial/datos/maestros/clientes", params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.params,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

type Role = "viewer" | "operator" | "admin";
type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

const me = (role: Role, platform: "viewer" | "admin" = "viewer") => ({
  id: 9,
  email: "u@d.c",
  full_name: "Usuario",
  role: platform,
  modules: [{ key: "commercial", name: "Inteligencia Comercial", role }],
});

const VERSION = "2026-11-20T10:00:00.123456Z";
const error = (status: number, code: string, message: string) =>
  jsonResponse(status, { code, message, details: null, request_id: null });

const customer = (overrides: Partial<Customer> = {}): Customer => ({
  id: 1,
  display_name: "ALMACEN UNO",
  legal_name: "Uno SRL",
  is_generic: false,
  active: true,
  last_seen_on: "2026-11-20",
  created_at: VERSION,
  updated_at: VERSION,
  store_sys_code: "101",
  zone: { id: 7, name: "RUTA 01 - LUVI" },
  salesperson: {
    salesperson_id: 3,
    salesperson_name: "Ana Preventista",
    source_label: "VENDEDOR UNO",
    mapping_id: 44,
    valid_from: "2026-11-01",
  },
  ...overrides,
});

const product = (overrides: Partial<Product> = {}): ProductDetail => ({
  id: 5,
  source_code: "1001",
  source_description: "AGUA 500",
  source_category: "AGUAS",
  source_line: null,
  source_capacity: null,
  source_pack: null,
  canonical_category_id: null,
  category_origin: "NONE",
  product_kind: "UNCLASSIFIED",
  analytics_eligible: false,
  eligibility_origin: "NONE",
  family_id: null,
  active: true,
  created_at: VERSION,
  updated_at: VERSION,
  master_price: "1500.00",
  prices: [
    {
      id: 1,
      list_number: 1,
      price_list_name: "Lista 1",
      price: "1500.00",
      valid_from: "2026-11-01",
      valid_to: null,
      voided: false,
    },
  ],
  ...overrides,
});

const zone = (overrides: Partial<Zone> = {}): ZoneDetail => ({
  id: 7,
  source_name: "RUTA 03 - MISA",
  display_name: "RUTA 03 - MISA",
  zone_type: "UNCLASSIFIED",
  requires_visit: false,
  weekdays: [],
  configuration_origin: "NONE",
  rule_id: null,
  active: true,
  configured_by: null,
  configured_at: null,
  created_at: VERSION,
  updated_at: VERSION,
  visit_days: [],
  ...overrides,
});

const categories = [
  {
    id: 2,
    code: "AGUAS",
    name: "Aguas",
    default_product_kind: "PRODUCT",
    default_analytics_eligible: true,
    parent_id: null,
    sort_order: 0,
    active: true,
    updated_at: VERSION,
  },
];

function mockApi(role: Role, routes: Record<string, Handler> = {}, platform: "viewer" | "admin" = "viewer") {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${init?.method ?? "GET"} ${url.pathname.replace("/api/v1", "")}`;
    if (key === "GET /auth/me") return jsonResponse(200, me(role, platform));
    const handler = routes[key];
    if (handler) return handler(init, url);
    if (key === "GET /commercial/product-categories") return jsonResponse(200, categories);
    if (key === "GET /commercial/product-families") return jsonResponse(200, []);
    if (key === "GET /commercial/zones") return jsonResponse(200, [zone()]);
    if (key === "GET /commercial/zone-rules") return jsonResponse(200, []);
    if (key === "GET /commercial/salespeople") return jsonResponse(200, { items: [], unlinked: [] });
    return error(404, "not_found", key);
  });
}

const calls = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  spy.mock.calls.filter(
    ([input, init]) => String(input).startsWith(`/api/v1${path}`) && (init?.method ?? "GET") === method,
  );
const body = (spy: ReturnType<typeof mockApi>, method: string, path: string) =>
  JSON.parse(String(calls(spy, method, path).at(-1)?.[1]?.body)) as Record<string, unknown>;

describe("Maestros navigation", () => {
  it("shows the eight sections and marks the current one", () => {
    nav.pathname = "/comercial/datos/maestros/zonas/7";
    renderWithQuery(<MastersTabs />);
    const tabs = within(screen.getByRole("navigation", { name: "Secciones de Maestros" })).getAllByRole("link");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Clientes",
      "Productos",
      "Categorías y familias",
      "Zonas",
      "Vendedores",
      "Listas de precios",
      "Tipos de comprobante",
      "Motivos de no compra",
    ]);
    expect(screen.getByRole("link", { name: "Zonas" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Clientes" })).not.toHaveAttribute("aria-current");
  });
});

describe("Clientes", () => {
  it("searches and filters in the backend and paginates", async () => {
    const api = mockApi("viewer", {
      "GET /commercial/customers": () => jsonResponse(200, { items: [customer()], total: 60 }),
    });
    renderWithQuery(<CustomerList />);
    expect(await screen.findByRole("link", { name: "ALMACEN UNO" })).toHaveAttribute(
      "href",
      "/comercial/datos/maestros/clientes/1",
    );
    expect(screen.getByText("Ana Preventista")).toBeInTheDocument();
    expect(screen.getByText("Página 1 de 3 · 60 en total")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Buscar por nombre o código STORE SYS"), "101{Enter}");
    await userEvent.selectOptions(screen.getByLabelText("Genéricos"), "yes");
    await waitFor(() => {
      const url = new URL(String(calls(api, "GET", "/commercial/customers").at(-1)?.[0]), "http://x");
      expect(Object.fromEntries(url.searchParams)).toMatchObject({ q: "101", is_generic: "true", active: "true" });
    });
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(String(calls(api, "GET", "/commercial/customers").at(-1)?.[0])).toContain("offset=25"));
  });

  it("shows an empty state", async () => {
    mockApi("viewer", { "GET /commercial/customers": () => jsonResponse(200, { items: [], total: 0 }) });
    renderWithQuery(<CustomerList />);
    expect(await screen.findByText("No hay clientes con estos filtros")).toBeInTheDocument();
  });

  it("shows an unlinked seller code as such, never guessed", async () => {
    mockApi("viewer", {
      "GET /commercial/customers": () =>
        jsonResponse(200, {
          items: [
            customer({ salesperson: { ...customer().salesperson!, salesperson_id: null, salesperson_name: null } }),
          ],
          total: 1,
        }),
    });
    renderWithQuery(<CustomerList />);
    expect(await screen.findByText("Código sin vincular")).toBeInTheDocument();
    expect(screen.getByText("VENDEDOR UNO")).toBeInTheDocument();
  });

  it("is read-only for a viewer, without contact data, with history and links to Mapeos", async () => {
    mockApi("viewer", {
      "GET /commercial/customers/1": () => jsonResponse(200, customer()),
      "GET /commercial/customers/1/history": () =>
        jsonResponse(200, {
          customer_id: 1,
          mappings: [
            {
              id: 61,
              source_system: "STORE_SYS",
              namespace: "customer",
              kind_label: "Cliente STORE SYS",
              external_id: "101",
              status: "confirmed",
              method: "created_from_master",
            },
          ],
          zones: [
            {
              id: 2,
              zone: { id: 8, name: "RUTA 02 - MAJUSA" },
              valid_from: "2026-11-10",
              valid_to: null,
              voided: false,
              source: "STORE_SYS",
              import_job_id: 3,
            },
            {
              id: 1,
              zone: { id: 7, name: "RUTA 01 - LUVI" },
              valid_from: "2026-11-01",
              valid_to: "2026-11-09",
              voided: false,
              source: "STORE_SYS",
              import_job_id: 2,
            },
          ],
          salespeople: [],
        }),
    });
    renderWithQuery(<CustomerDetailView customerId={1} />);
    expect(await screen.findByRole("heading", { name: "ALMACEN UNO" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cambiar marcas" })).not.toBeInTheDocument();
    expect(screen.queryByText("CUIT")).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "101" })).toHaveAttribute("href", "/comercial/datos/mapeos/61");
    expect(screen.getByText("RUTA 02 - MAJUSA")).toBeInTheDocument(); // the history, newest first
    expect(screen.getByText("El maestro nunca le asignó vendedor.")).toBeInTheDocument();
  });

  it("lets an admin change the flags with a reason and the version it read, and shows a 409", async () => {
    const api = mockApi("admin", {
      "GET /commercial/customers/1": () =>
        jsonResponse(200, customer({ tax_id: "20123456786", address: "X", phone: "1" })),
      "GET /commercial/customers/1/history": () =>
        jsonResponse(200, { customer_id: 1, mappings: [], zones: [], salespeople: [] }),
      "PATCH /commercial/customers/1": () =>
        error(409, "changed_meanwhile", "Alguien modificó este dato mientras lo editabas."),
    });
    renderWithQuery(<CustomerDetailView customerId={1} />);
    expect(await screen.findByText("20123456786")).toBeInTheDocument(); // admin receives contact data
    await userEvent.click(screen.getByRole("button", { name: "Cambiar marcas" }));
    const save = screen.getByRole("button", { name: "Guardar" });
    expect(save).toBeDisabled(); // a reason is required
    await userEvent.click(screen.getByLabelText("Cliente genérico"));
    await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Consumidor final");
    await userEvent.click(save);
    expect(await screen.findByText(/Alguien modificó este dato mientras lo editabas/)).toBeInTheDocument();
    expect(body(api, "PATCH", "/commercial/customers/1")).toEqual({
      is_generic: true,
      active: true,
      reason: "Consumidor final",
      expected_updated_at: VERSION,
    });
  });
});

describe("Productos", () => {
  it("filters without category and shows the articles-master price", async () => {
    const api = mockApi("viewer", {
      "GET /commercial/products": () => jsonResponse(200, { items: [product()], total: 1 }),
    });
    renderWithQuery(<ProductList />);
    expect(await screen.findByRole("link", { name: "1001" })).toHaveAttribute(
      "href",
      "/comercial/datos/maestros/productos/5",
    );
    expect(within(screen.getByRole("table")).getByText("Sin categoría")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Con categoría"), "no");
    await waitFor(() =>
      expect(String(calls(api, "GET", "/commercial/products").at(-1)?.[0])).toContain("has_category=false"),
    );
  });

  it("is read-only for an operator", async () => {
    mockApi("operator", { "GET /commercial/products/5": () => jsonResponse(200, product()) });
    renderWithQuery(<ProductDetailView productId={5} />);
    expect(await screen.findByText("Historia de precios")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clasificar" })).not.toBeInTheDocument();
  });

  it("lets an admin assign a category, sending only what changed", async () => {
    let current = product();
    const api = mockApi("admin", {
      "GET /commercial/products/5": () => jsonResponse(200, current),
      "PATCH /commercial/products/5": () => {
        current = product({ canonical_category_id: 2, category_origin: "MANUAL", updated_at: "2026-11-21T00:00:00Z" });
        return jsonResponse(200, current);
      },
    });
    renderWithQuery(<ProductDetailView productId={5} />);
    await userEvent.click(await screen.findByRole("button", { name: "Clasificar" }));
    await userEvent.selectOptions(screen.getByLabelText("Categoría"), "2");
    await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Es agua");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(body(api, "PATCH", "/commercial/products/5")).toEqual({
      category_id: 2,
      reason: "Es agua",
      expected_updated_at: VERSION,
    });
    expect(await screen.findByText("Aguas")).toBeInTheDocument();
  });
});

describe("Categorías", () => {
  it("lets an admin create a category; a viewer sees no actions", async () => {
    const api = mockApi("admin", {
      "POST /commercial/product-categories": () => jsonResponse(201, { ...categories[0], id: 3, name: "Gaseosas" }),
    });
    renderWithQuery(<CategoriesView />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva categoría" }));
    expect(screen.getByText(/no reescribe los productos ya clasificados/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Código"), "GASEOSAS");
    await userEvent.type(screen.getByLabelText("Nombre"), "Gaseosas");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(calls(api, "POST", "/commercial/product-categories")).toHaveLength(1));
    expect(body(api, "POST", "/commercial/product-categories")).toMatchObject({ code: "GASEOSAS", name: "Gaseosas" });
  });

  it("has no edit actions for a viewer and an empty state for families", async () => {
    mockApi("viewer");
    renderWithQuery(<CategoriesView />);
    expect(await screen.findByText("Aguas")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Nueva|Modificar/ })).not.toBeInTheDocument();
    expect(await screen.findByText("Sin familias")).toBeInTheDocument();
  });
});

describe("Zonas", () => {
  it("lists zones with their configuration origin and the rules reminder", async () => {
    mockApi("viewer", {
      "GET /commercial/zones": () =>
        jsonResponse(200, [
          zone(),
          zone({
            id: 8,
            source_name: "RUTA 01 - LUVI",
            display_name: "RUTA 01 - LUVI",
            zone_type: "PRESALE",
            requires_visit: true,
            weekdays: [1, 3, 5],
            configuration_origin: "RULE",
            rule_id: 30,
          }),
        ]),
    });
    renderWithQuery(<ZoneList />);
    expect(await screen.findByRole("link", { name: "RUTA 03 - MISA" })).toHaveAttribute(
      "href",
      "/comercial/datos/maestros/zonas/7",
    );
    expect(screen.getByText("Sin configurar")).toBeInTheDocument();
    expect(screen.getByText("Lu · Mi · Vi")).toBeInTheDocument();
    expect(screen.getByText(RULES_REMINDER)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /aplicar/i })).not.toBeInTheDocument();
  });

  it("configures a zone and shows a 422 from the backend", async () => {
    const api = mockApi("admin", {
      "GET /commercial/zones/7": () => jsonResponse(200, zone()),
      "PUT /commercial/zones/7/configuration": () =>
        error(422, "zone_change_out_of_order", "La fecha de vigencia es anterior al último cambio de días de la zona."),
    });
    renderWithQuery(<ZoneDetailView zoneId={7} />);
    expect(await screen.findByText(/Ninguna regla clasifica esta zona/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Configurar zona" }));
    await userEvent.selectOptions(screen.getByLabelText("Tipo de zona"), "PRESALE");
    await userEvent.click(screen.getByLabelText(/Se visita/));
    await userEvent.click(screen.getByLabelText("martes"));
    await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Confirmado");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText(/anterior al último cambio/)).toBeInTheDocument();
    expect(body(api, "PUT", "/commercial/zones/7/configuration")).toMatchObject({
      zone_type: "PRESALE",
      requires_visit: true,
      weekdays: [2],
      reason: "Confirmado",
      expected_updated_at: VERSION,
    });
  });

  it("does not let an outlet be visited", async () => {
    mockApi("admin", { "GET /commercial/zones/7": () => jsonResponse(200, zone()) });
    renderWithQuery(<ZoneDetailView zoneId={7} />);
    await userEvent.click(await screen.findByRole("button", { name: "Configurar zona" }));
    await userEvent.selectOptions(screen.getByLabelText("Tipo de zona"), "OUTLET");
    expect(screen.getByLabelText(/Se visita/)).toBeDisabled();
  });
});

describe("Vendedores", () => {
  it("shows employees, their codes, the unlinked codes and links to Mapeos", async () => {
    mockApi("viewer", {
      "GET /commercial/salespeople": () =>
        jsonResponse(200, {
          items: [{ id: 3, name: "Ana Preventista", active: true, is_salesperson: true, mappings: [], customers: 12 }],
          unlinked: [
            {
              id: 45,
              source_system: "AXUM",
              namespace: "salesperson",
              kind_label: "Preventista AXUM",
              external_id: "40",
              external_label: null,
              status: "pending",
              customers: 0,
            },
          ],
        }),
    });
    renderWithQuery(<SalespeopleView />);
    expect(await screen.findByText("Ana Preventista")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sin códigos vinculados/ })).toHaveAttribute(
      "href",
      "/comercial/datos/mapeos",
    );
    expect(screen.getByRole("link", { name: "40" })).toHaveAttribute("href", "/comercial/datos/mapeos/45");
    expect(screen.getByText(/El alta y la edición de empleados las hace un administrador/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Empleados/ })).not.toBeInTheDocument();
  });

  it("links a platform admin to Empleados", async () => {
    mockApi("admin", {}, "admin");
    renderWithQuery(<SalespeopleView />);
    expect(await screen.findByRole("link", { name: "Dar de alta o editar en Empleados" })).toHaveAttribute(
      "href",
      "/empleados",
    );
  });
});

describe("Listas de precios", () => {
  it("shows what the articles master feeds and lets only an admin change it", async () => {
    const lists = [1, 2, 3, 4, 5].map((n) => ({
      id: n,
      list_number: n,
      code: `LISTA_${n}`,
      name: `Lista ${n}`,
      description: null,
      kind: "UNASSIGNED",
      commercially_relevant: true,
      active: true,
      updated_at: VERSION,
    }));
    const api = mockApi("admin", {
      "GET /commercial/price-lists": () => jsonResponse(200, lists),
      "GET /commercial/price-lists/articles-master": () => jsonResponse(200, { list_number: 1 }),
      "PUT /commercial/price-lists/articles-master": () => jsonResponse(200, { list_number: 2 }),
    });
    renderWithQuery(<PriceListsView />);
    expect(await screen.findByText("Lista 1 · Lista 1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cambiar" }));
    expect(screen.getByText(/nada se reescribe/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("El precio del maestro de artículos alimenta"), "2");
    await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Cambio de lista");
    await userEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    await waitFor(() =>
      expect(body(api, "PUT", "/commercial/price-lists/articles-master")).toEqual({
        list_number: 2,
        reason: "Cambio de lista",
      }),
    );
  });
});

describe("Tipos de comprobante y motivos", () => {
  it("highlights the type Calidad linked to", async () => {
    nav.params = new URLSearchParams("tipo=4");
    mockApi("viewer", {
      "GET /commercial/document-types": () =>
        jsonResponse(200, [
          {
            id: 4,
            source_name: "COMPROBANTE NUEVO",
            document_class: "UNCLASSIFIED",
            sign: 0,
            commercial_universe: false,
            active: true,
            decided_by: null,
            decided_at: null,
            notes: null,
            updated_at: VERSION,
          },
        ]),
    });
    renderWithQuery(<DocumentTypesView />);
    const row = (await screen.findByText("COMPROBANTE NUEVO")).closest("tr");
    expect(row).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("button", { name: "Clasificar" })).not.toBeInTheDocument();
    nav.params = new URLSearchParams();
  });

  it("lets an admin assign a reason's category with the version it read", async () => {
    const api = mockApi("admin", {
      "GET /commercial/no-purchase-reasons": () =>
        jsonResponse(200, [
          {
            id: 6,
            source_system_id: 1,
            reason_source: "TIENE STOCK",
            category_id: null,
            decided_by: null,
            decided_at: null,
            updated_at: VERSION,
          },
        ]),
      "GET /commercial/no-purchase-reason-categories": () =>
        jsonResponse(200, [{ id: 1, code: "STOCK", name: "Tiene stock", active: true, updated_at: VERSION }]),
      "PUT /commercial/no-purchase-reasons/6/category": () => jsonResponse(200, {}),
    });
    renderWithQuery(<ReasonsView />);
    await userEvent.click(await screen.findByRole("button", { name: "Asignar categoría" }));
    await userEvent.selectOptions(screen.getByLabelText("Categoría"), "1");
    await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Confirmado");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(body(api, "PUT", "/commercial/no-purchase-reasons/6/category")).toEqual({
        category_id: 1,
        reason: "Confirmado",
        expected_updated_at: VERSION,
      }),
    );
  });
});
