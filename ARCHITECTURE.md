# ARCHITECTURE — DistriCom

> v0.3 · 2026-10-04 · Fase 0 implementada. v0.2: DistriCom como **plataforma modular** (ADR 006). v0.3: módulo **Rendiciones** aprobado (ADR 007), empleados y roles por módulo en plataforma.

## 1. Contexto

**DistriCom es una plataforma modular** para una distribuidora de bebidas: una sola aplicación (un login, una sesión, un backend, un frontend, una base de datos) compuesta por un **núcleo compartido** (plataforma) y **módulos** de negocio.

| Pieza | Estado | Alcance |
|---|---|---|
| **Plataforma (DistriCom Core)** | En construcción | Usuarios, sesiones, roles por módulo, configuración, auditoría, sistemas fuente, identidades compartidas (empleados y funciones; clientes y localidades en Fase 1), framework de importación. |
| **Inteligencia Comercial** (`commercial`, `/comercial`) | En desarrollo | Capa de **inteligencia comercial** sobre AXUM y STORE SYS. Alcance en `ROADMAP.md`. |
| **Rendiciones** (`settlements`, `/rendiciones`) | En desarrollo (R2) | Rendiciones de reparto y boca de expendio, cajas, cheques, pendientes, gastos, combustible, pagos y cuenta interna y liquidación interna de empleados por período (área protegida, ADR 010), con ledger propio (ADR 007, §4.6). |
| Otros módulos | Sin desarrollar | Se agregan con el patrón de §4.5. Nada de su modelo se anticipa. |

Sistemas fuente:

- **AXUM**: preventa (preventistas, rutas, clientes, programación y resultado de visitas, pedidos).
- **STORE SYS**: administración y facturación (facturas, ítems, notas de crédito, productos, canal).

La aplicación **no** reemplaza ni escribe en esos sistemas. La ingesta inicial es por archivos (XLSX/CSV); las APIs vendrán en el futuro.
Usuarios iniciales: propietario, administración y dirección (pocos usuarios).
Escala: 3 preventistas, 7 rutas, ~600 clientes, 500+ SKU, historia desde 2026-01-01.

## 2. Vista general

```
                ┌──────────────────────────────────────┐
  Navegador ──▶ │  Next.js (frontend)                  │  shell de plataforma + navegación por módulo
                │  /api/* → rewrite                    │  No calcula KPIs.
                └──────────────────┬───────────────────┘
                                   │ REST/JSON  /api/v1  (mismo origen, cookie de sesión)
                ┌──────────────────▼───────────────────┐
                │  FastAPI (backend, un proceso)       │
                │  ├─ core/ db/      técnico           │
                │  ├─ platform/      DistriCom Core    │  auth, settings, audit, sources,
                │  │                                   │  (Fase 1) imports, identity, parties
                │  ├─ modules/                         │
                │  │   ├─ commercial/  Intel. Comercial │  /api/v1/commercial/*
                │  │   └─ settlements/ Rendiciones      │  /api/v1/settlements/*
                │  └─ api/v1/router  composición       │
                └──────────────────┬───────────────────┘
                                   │ SQLAlchemy 2 (Core para analítica, ORM para CRUD)
                ┌──────────────────▼───────────────────┐
                │  PostgreSQL 17 — una base            │  public (plataforma) · commercial · settlements
                └──────────────────────────────────────┘
```

**Monolito modular**: un proceso backend, un proceso frontend, una base. Sin microservicios, Redis, Celery, Kafka ni aplicaciones separadas por módulo. Los módulos se comunican por interfaces Python públicas y nunca leen ni escriben tablas internas de otro módulo. La única excepción son las consultas analíticas de solo lectura del propio módulo sobre sus tablas y las de plataforma (ver §4.3).

## 3. Stack

| Capa | Elección | Notas |
|---|---|---|
| Frontend | Next.js (App Router, versión estable vigente) + React + TypeScript `strict` | Rutas por módulo; páginas mayormente client components con TanStack Query. Sin lógica de negocio en route handlers de Next. |
| UI | Tailwind CSS + shadcn/ui + lucide-react | Tokens CSS para light/dark. |
| Datos UI | TanStack Query + TanStack Table | Paginación/sort/filtro **server‑side**. |
| Formularios | React Hook Form + Zod | Zod valida forma; las reglas de negocio se validan en backend. |
| Gráficos | **ECharts** (vía `echarts-for-react`) | Heatmaps, sparklines, funnels y matrices grandes rinden mejor que Recharts. Una sola librería. |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic | |
| Imports | `openpyxl` (read‑only, sin macros) + `csv` estándar | Ver PLAN M1. |
| Auth | Argon2id (`argon2-cffi`), sesiones en DB | Ver §8. |
| DB | PostgreSQL 17 | |
| Tooling | `uv`, `ruff`, `mypy`, `pytest`; `pnpm`, `eslint`, `prettier`, `vitest`, Playwright | |
| Infra | Docker Compose (db + backend + frontend), GitHub Actions | |

## 4. Plataforma y módulos (backend)

### 4.1 Estructura

```
backend/app/
├── core/                  técnico: settings de entorno, logging, errores, middleware (no importa negocio)
├── db/                    técnico: Base declarativa, convenciones, sesión, registro de modelos
├── platform/              DistriCom Core — negocio compartido
│   ├── modules.py         contrato ModuleSpec + módulos instalados
│   ├── auth/              users, user_sessions, user_module_roles (access.py: require_module_access)
│   ├── audit/             audit_events
│   ├── settings/          app_settings + registro de parámetros por scope (platform.*, <módulo>.*)
│   ├── sources/           source_systems
│   ├── imports/           [Fase 1] framework: batches, files, staging, errors, plantillas, TabularReader, registro de tipos
│   ├── identity/          [Fase 1] external_entity_mappings
│   └── parties/           [R0] employees, employee_functions, registro de funciones · [Fase 1] customers (identidad), localities
├── modules/
│   ├── __init__.py        MODULES: lista literal de módulos instalados
│   └── commercial/        Inteligencia Comercial
│       ├── __init__.py    MODULE: ModuleSpec (key, nombre visible, router, settings)
│       ├── api.py         router montado en /api/v1/commercial
│       ├── settings.py    parámetros commercial.* con defaults y etiquetas
│       ├── public.py      [cuando otro módulo lo necesite] interfaz pública
│       ├── models/        [Fase 1] routes, assignments, products, sales, visits, orders…
│       ├── imports/       [Fase 1] adaptadores AXUM / STORE SYS + tipos commercial.*
│       ├── analytics/     [Fase 2] REGISTRO ÚNICO de métricas (IDs = METRICS.md), filtros, períodos
│       └── customer_status/  opportunities/  goals/  incentives/  reports/   [Fases 4–9]
│   └── settlements/       Rendiciones (ADR 007)
│       ├── __init__.py    MODULE: ModuleSpec (key `settlements`, "Rendiciones", router, settings, funciones de empleado)
│       ├── api.py         router montado en /api/v1/settlements
│       ├── settings.py    parámetros settlements.*
│       ├── ledger/        [R1] accounts, financial_operations, ledger_entries y el servicio de operaciones
│       ├── cash/          [R1] API de cajas: schemas, casos de uso y endpoints sobre el servicio del ledger
│       ├── delivery/      [R2] rendiciones de reparto: alta, conteo, agregados, transferencias, gastos, tablero, denominaciones, cierre y reapertura
│       └── pending/ cheques/ payments/ outlets/ employee_finance/   [R3–R8]
│           expected/ store_sys/                                    [R9‑1]
└── api/v1/router.py       raíz de composición: monta routers de plataforma y de cada módulo
```

Lo marcado [Fase N] nace con su primer código real; no se crean carpetas vacías.

### 4.2 Regla de dependencias

```
modules.<m>  ──▶  platform  ──▶  core, db
modules.<m>  ──▶  modules.<otro>.public     (solo si está declarado aquí; grafo acíclico)
api          ──▶  platform, modules         (raíz de composición)
```

1. `core` y `db` no importan nada de `platform`, `modules` ni `api`. (Excepción técnica: `db/models.py` importa los modelos solo para registrarlos en Alembic.)
2. `platform` nunca importa de `modules`.
3. Un módulo importa de `platform` libremente.
4. Un módulo **no** importa nada de otro módulo salvo su `public.py`, y esa dependencia se declara en esta sección. **Dependencias declaradas hoy: ninguna.**
5. `api/v1/router.py` es el único lugar que conoce a todos los módulos.

La regla la verifica el CI con **import-linter** (contratos en `backend/pyproject.toml`) y, en el frontend, con `no-restricted-imports` de ESLint. Una violación hace fallar el pipeline.

Ownership:
- **Tablas:** cada tabla tiene un único dueño (`DATA_MODEL.md §0`), que la define, la migra y es el único que escribe en ella. Un módulo escribe datos de plataforma solo a través de servicios de plataforma.
- **API:** `/api/v1/{health,auth,settings}` = plataforma; `/api/v1/<módulo>/…` = módulo. Todo endpoint de módulo exige acceso al módulo (§8).
- **Interfaces entre módulos:** funciones Python síncronas con tipos propios (dataclasses o Pydantic), sin exponer modelos ORM. Sin bus de eventos; si algún día hace falta desacoplar tiempos, primero se evalúa una tabla outbox en proceso.

Reglas que siguen vigentes:
- **Las reglas de negocio viven en el backend.** El frontend nunca deriva un KPI; recibe valores ya calculados + metadatos (numerador, denominador, definición).
- **La analítica solo lee el modelo canónico**, nunca columnas de archivos.
- Un `utils/` genérico está prohibido; los helpers viven en el módulo que los usa.

### 4.3 Excepción analítica: lectura directa del modelo canónico

Dentro de un módulo, los paquetes **operacionales** (en Comercial: `imports/`, `customer_status/`, `opportunities/`, `goals/`, `incentives/`…) mantienen encapsulación: son dueños de sus tablas, escriben solo a través de sus servicios y exponen interfaces Python.

`modules/commercial/analytics/` es la excepción explícita, para no sobrearquitecturar consultas BI:

- **Puede** escribir consultas SQL (SQLAlchemy Core) que crucen directamente las tablas **del módulo comercial y de plataforma** que necesite: `commercial.sales`, `sale_items`, `products`, `product_categories`, `brands`, `routes`, asignaciones, `scheduled_visits`, `visits`, `orders`, `customer_status_*`, `opportunities`, etc., y `public.customers`, `employees`, `employee_functions`, `localities`. No tiene que encadenar servicios para armar un join.
- **Nunca** lee tablas de otro módulo. Un reporte que cruce módulos se resuelve cuando exista, con la interfaz pública o una vista de solo lectura publicada por el módulo dueño.
- **Solo lectura.** `analytics/` nunca inserta, actualiza ni borra filas. Los procesos que escriben resultados derivados (estados de cliente, oportunidades) viven en su propio paquete, aunque lean de `analytics/`.
- **Centralizado.** Esas consultas están únicamente en `analytics/` (registro de métricas, filtros, períodos y servicios por pantalla). Los routers y los demás paquetes no escriben SQL analítico propio: piden la métrica a `analytics/`.
- **Contrato:** los nombres de tablas y columnas del modelo canónico (DATA_MODEL.md) son la interfaz pública para `analytics/`. Un cambio de schema que rompa una consulta analítica se detecta por los tests de métricas y se corrige en el mismo PR.
- Los paquetes operacionales pueden consumir métricas de `analytics/` (por ejemplo, `opportunities/` usa penetración por pares), nunca al revés.

### 4.4 Base de datos y migraciones

- Una sola base PostgreSQL. Plataforma en `public`; cada módulo en su schema (`commercial`, `settlements`). Detalle y reglas de FKs en `DATA_MODEL.md §0`.
- **Una sola cadena Alembic** para toda la aplicación (`env.py` con `include_schemas=True`). La primera migración de un módulo crea su schema.
- Cada revisión pertenece a un solo dueño y su mensaje empieza con él: `platform: …`, `commercial: …`, `settlements: …`.

### 4.5 Patrón para agregar un módulo

| Aspecto | Qué se agrega |
|---|---|
| Backend | `app/modules/<key>/` con `MODULE` (ModuleSpec), `api.py`, `settings.py` y, si usa personas, sus códigos de función de empleado; una entrada en `MODULES` (`app/modules/__init__.py`) |
| API | `/api/v1/<key>/…` |
| Acceso | `require_module_access("<key>", …)`; roles por módulo cuando existan (§8) |
| Configuración | Parámetros `<key>.*` en `settings.py`; aparecen solos en Configuración |
| Base de datos | Migración `<key>: create schema` con su schema y tablas |
| Frontend | `src/modules/<key>/` (nav, api, features), `src/app/(app)/<ruta>/`, una entrada en `src/modules/registry.ts`, override de ESLint para la carpeta del módulo |
| Tests | `backend/tests/modules/<key>/`, tests de frontend y un E2E de humo; los contratos de dependencias lo cubren sin configuración extra |
| Docs | Sección propia en ROADMAP y docs del módulo |

No hay carga dinámica, descubrimiento automático, plugins ni feature flags por módulo: el registro es una lista literal en código.

### 4.6 Rendiciones: ledger de valores

Fuente de verdad de dónde está cada valor del módulo (cajas, banco, cartera de cheques, pendientes de cobro, cuentas internas de empleados, aportes de socios). Detalle en ADR 007, ADR 008 y `DATA_MODEL.md §13`.

- `accounts` (pocas filas: lugares y conceptos, no un plan de cuentas) · `financial_operations` (un hecho económico, con su documento origen) · `ledger_entries` (patas inmutables con signo).
- **Un solo escritor:** `ledger/service.py` (`post_operation`, `reverse_operation` y, desde R2‑2, `replace_operation`). Los documentos (rendición, pago, pendiente…) lo invocan en su misma transacción, que confirma quien llama; ningún otro código importa los modelos del ledger ni escribe sus tablas (`tests/modules/settlements/test_ledger_single_writer.py`).
- **Reglas por tipo de operación** (`ledger/rules.py`): rol mínimo y forma de las patas. Ajustes y reversos exigen `admin` y motivo.
- **Idempotencia:** `client_request_id` + huella SHA‑256 del comando; el mismo id con otro contenido es `409 idempotency_conflict` (ADR 008).
- **Concurrencia:** las cuentas que no admiten negativo y pierden valor se bloquean (`FOR NO KEY UPDATE`, en orden de id) antes de validar el saldo, que debe quedar ≥ 0 hoy y al cierre de cada día desde la fecha de la operación (operaciones retroactivas); la base vuelve a verificarlo al commit.
- **Invariantes en la base:** suma cero y al menos dos patas por operación (constraint trigger diferido), tablas append-only (trigger), sin saldo negativo donde no se permite (constraint trigger diferido con bloqueo), `client_request_id` único, un reverso por operación. Desde su fase: unicidad del documento origen para operaciones que existen una sola vez (R2), un cheque sale de cartera una sola vez (R4).
- **Correcciones por reverso:** nunca UPDATE ni DELETE de entradas. Reabrir una rendición no toca el ledger: su cierre sigue vigente en Cajas hasta el nuevo cierre, que publica en una sola transacción el reverso del anterior y el cierre nuevo, en la fecha de negocio de la rendición y validando el efecto neto (decisión R2‑P1 de Enzo). Lo hace `replace_operation(original, reemplazo)`: solo para operaciones de un documento (`DOCUMENT_TYPES`, hoy `SETTLEMENT_CLOSE`), del mismo tipo y documento; valida saldo actual e histórico sobre el efecto neto por cuenta y publica ambas en la transacción de quien llama. `reverse_operation` rechaza una operación de documento (`409 operation_owned_by_document`): un cierre se corrige reabriendo la rendición.
- **API de cajas** (`cash/`, R1‑2): un caso de uso por operación (traspaso, depósito, extracción, saldo inicial, aporte, devolución, ajuste, reverso, alta de cuenta de socio) que arma las patas y llama al servicio; el router exige el rol del módulo (`viewer` lee, `operator` mueve entre cajas y banco, `admin` el resto) y responde `201`/`200` según la operación sea nueva o un reintento. Un rechazo de la base al confirmar se traduce a `409 invalid_ledger_operation` sin detalles internos.
- **Rendiciones de reparto** (`delivery/`, R2‑1): la rendición y su carga (conteo por denominación con destino por línea, agregados, transferencias con verificación, gastos) viven en tablas propias y todavía no tocan el ledger; el cierre las vuelca en una operación (R2‑2). El tablero (esperado, efectivo, transferencias verificadas, cheques, pendientes, gastos, justificado y diferencia) lo calcula `delivery/summary.py`. Cada escritura bloquea la rendición y exige `OPEN`; la base rechaza cambios sobre una rendición `CLOSED`, borrados y ediciones de agregados (migración `0007`). Altas idempotentes con `client_request_id` + huella, como el ledger.
- **Cierre y reapertura** (`delivery/closing.py`, R2‑2): `status` es el estado de edición y `current_close_operation_id` el cierre vigente en el ledger; la API deriva `financial_state` (`NOT_CLOSED`, `CLOSED`, `BEING_CORRECTED`). El cierre bloquea la rendición, recalcula todo desde la base (nunca toma totales del cliente), valida la asignación de la diferencia (empresa solo `admin` con motivo), publica el `SETTLEMENT_CLOSE` (o, tras una reapertura, el reverso y el cierre nuevo con `replace_operation`), materializa diferencia y asignaciones, congela y audita, todo en una transacción. Sus ids de solicitud derivan de (rendición, versión) y la huella cubre totales por cuenta, diferencia, asignaciones y patas. El cierre lleva el `preview_token` de la vista previa que el usuario revisó (huella de todo lo que puede alterar el cierre: esperado, conteo, agregados, transferencias, gastos, diferencia y patas); si cambió algo desde entonces, se rechaza con `409 settlement_preview_stale` antes de escribir. Reabrir (`admin`, con motivo) agrega una fila a `settlement_reopenings`, descongela y sube la versión; el historial sale de las entidades de dominio, no de la auditoría. Orden de bloqueo: la rendición y después, dentro del ledger, las cuentas que pierden valor por id.
- **Saldos derivados:** suma de entradas por cuenta. Sin tablas de saldo mantenidas a mano; si algún día hace falta performance, un saldo materializado se agrega sin cambiar el modelo.
- Sin event sourcing, bus de eventos ni contabilidad formal.

## 5. Frontend

```
frontend/src/
├── app/(auth)/login              login de DistriCom
├── app/(app)/                    shell de plataforma (sesión, topbar, sidebar del módulo activo)
│   ├── configuracion/            configuración: General + una sección por módulo
│   ├── page.tsx (selector)       home de plataforma: una tarjeta por módulo; con uno solo, redirige
│   ├── empleados/                empleados y funciones (administración de plataforma)
│   ├── rendiciones/              Rendiciones (inicio en R0; pantallas por fase R1–R8)
│   │   └── cajas/                Cajas (R1): tablero y operaciones/[id]
│   └── comercial/                Inteligencia Comercial
│       ├── page.tsx              Inicio comercial (dashboard)
│       ├── preventistas/ rutas/ clientes/ oportunidades/
│       ├── portfolio/{productos,categorias,marcas,penetracion}
│       ├── gestion/{objetivos,incentivos,reuniones}
│       ├── boca
│       └── datos/{importaciones,calidad}
├── modules/registry.ts           registro de módulos (key, nombre, ruta base, navegación); lo usan solo las rutas de app/
├── modules/commercial/           navegación, API y features del módulo
├── modules/settlements/          ídem para Rendiciones (incluye su logo oficial); cash/ = API y pantallas de Cajas
├── features/                     features de plataforma (auth, settings)
├── components/ui/                design system (shadcn/ui)
├── components/common/            KpiCard, DataTable, EmptyState, ErrorState, PeriodPicker...
├── components/layout/            AppShell, Sidebar
├── lib/api/                      cliente HTTP y endpoints de plataforma
└── lib/filters/                  estado de filtros globales sincronizado con la URL
```

- **Una sola aplicación:** mismo login, sesión, shell, branding y design system para todos los módulos. Cada módulo tiene su prefijo de URL y su navegación; el AppShell muestra la del módulo activo y bloquea (pantalla "sin acceso") las rutas de un módulo que el usuario no tiene. El selector de módulos (home `/` y menú bajo el wordmark) aparece solo cuando un usuario tiene acceso a más de uno.
- **Dependencias:** `modules/<m>` puede importar `components/`, `lib/` y `features/` de plataforma; nunca otro `modules/<otro>` ni el registro. `components/`, `lib/` y `features/` nunca importan de `modules/`. Las únicas raíces de composición son las rutas de `app/` y `modules/registry.ts`: el layout pasa el módulo activo al AppShell por props (ESLint `no-restricted-imports`).
- **Filtros globales en la URL** (`?from=…&to=…&salesperson=…`) → enlaces compartibles y coherencia entre vistas.
- **Tipos de API generados desde OpenAPI** (`openapi-typescript`), para que frontend y backend no diverjan.
- Estados obligatorios en cada vista de datos: loading (skeleton), empty (con explicación), error (mensaje comprensible + `request_id`).
- Prioridad: desktop → tablet → móvil de consulta. Light mode excelente; dark mode preparado por tokens.

## 6. Pipeline de importación

```
UPLOAD → IDENTIFICACIÓN → MAPPING → PARSING → VALIDACIÓN → NORMALIZACIÓN
       → RESOLUCIÓN DE ENTIDADES → PREVIEW → IMPORT (transacción) → CHECKS → AUDITORÍA
```

El framework (lote, archivo, staging, errores, plantillas, lectores por formato) es de **plataforma**; los tipos de import y los adaptadores son de cada **módulo** (`commercial.sales`, `commercial.visits`…), registrados en código.

1. **Upload**: límite de tamaño (`UPLOAD_MAX_SIZE`), extensión + MIME + firma mágica validadas contra el **registro de formatos** (V1: `.xlsx` = ZIP, `.csv` = texto). Nombre de archivo sanitizado; se guarda con nombre UUID fuera del webroot. Hash SHA‑256.
2. **Identificación**: el usuario elige sistema + tipo (o se sugiere por encabezados conocidos).
3. **Mapping**: detección automática por sinónimos (`Cliente`, `Razón Social`, `NomCliente` → `customer_name`); corrección manual; guardado como plantilla (`import_column_mappings`).
4. **Parsing**: el `TabularReader` del formato convierte el archivo en filas crudas. V1: `openpyxl` read‑only con `data_only=True` (nunca ejecuta fórmulas ni macros) para `.xlsx`; CSV con detección de encoding (UTF‑8 / Latin‑1) y separador.
5. **Validación** por fila → `import_errors` con fila, columna, código, severidad (`ERROR`/`WARNING`).
6. **Normalización**: espacios, mayúsculas, decimales `1.234,56`, fechas `dd/mm/yyyy`, CUIT a 11 dígitos, strings vacíos → NULL.
7. **Resolución**: por ID externo exacto → `external_entity_mappings`; lo no resuelto queda `PENDING_REVIEW`, nunca se fusiona en silencio.
8. **Preview**: filas, válidas, warnings, errores, nuevas, actualizaciones, duplicados; descarga de errores CSV.
9. **Import**: una transacción por batch; upsert por business key; rollback total ante error crítico.
10. **Checks post‑import** y evento de auditoría.

**Idempotencia** (tres defensas): hash de archivo (rechazo de duplicado exacto salvo reproceso deliberado), business key de documento (`source_system + external_document_id`, o `doc_type + punto_venta + número`), clave de línea (`sale_id + line_number`).

**Dos capas separadas: formato y sistema fuente.** El adaptador de sistema no sabe de qué formato vienen las filas.

```
archivo .xlsx / .csv / .xls (futuro)      API AXUM / STORE SYS (futuro)
        │                                          │
  TabularReader (por formato)              ApiClient (por sistema)
        │  Iterable[RawRecord]                     │  Iterable[RawRecord]
        └──────────────────┬───────────────────────┘
                     SourceAdapter (por sistema + tipo: STORE SYS ventas, AXUM visitas…)
                           │  Iterable[CanonicalSale | CanonicalVisit | …]
                  Normalización → Resolución → Modelo canónico
```

- `RawRecord`: número de fila/origen + valores por columna (texto, número, fecha o vacío), ya despegados del formato. Es lo único que recibe el `SourceAdapter`, junto con el mapping de columnas.
- `TabularReader`: un lector por formato, registrado por extensión + firma (`.xlsx`, `.csv` en V1). Agregar `.xls` = registrar un lector nuevo (candidatos: `xlrd` para BIFF o conversión con LibreOffice headless), sin tocar adaptadores, validación ni analítica. Se decide el parser cuando lleguen los exports reales de STORE SYS (PLAN D2).
- `SourceAdapter`: semántica del sistema fuente (qué columna es el comprobante, cómo se lee el signo de una NC, cómo se deduce el canal). No importa `openpyxl` ni `csv`.
- Reemplazar archivos por API = escribir un `ApiClient` que emita `RawRecord` (o un adaptador que emita registros canónicos directamente); normalización, resolución y analítica no cambian.

Imports síncronos en V1 (volumen chico). Si superan ~30 s se pasan a una tarea en background **dentro del mismo proceso** (tabla `import_batches` como cola), sin Celery.

## 7. Procesos derivados (cálculos)

| Proceso | Disparador | Resultado |
|---|---|---|
| Estados de cliente | después de cada import de ventas + diario | `customer_status_current`, `customer_status_history`, `customer_recovery_events` |
| Motor de oportunidades | diario + manual | `opportunities` (dedup), `opportunity_status_history` |
| Atribución de conversiones | después de import de ventas | `opportunities.converted_at`, `generated_sale_id` |

Son procesos del módulo comercial. Se ejecutan como comandos CLI (`python -m app.cli …`) y desde un endpoint admin. Programación diaria vía cron del host o del contenedor; sin scheduler externo.

## 8. Seguridad

- Usuarios locales, password Argon2id, política de longitud mínima 12.
- Sesión: token aleatorio de 256 bits; en DB se guarda **solo su hash**; cookie `HttpOnly; Secure; SameSite=Lax`; expiración deslizante (8 h) y absoluta (7 días).
- **Acceso por módulo, en etapas** (ADR 006):
  - **Etapa 0 (hasta R0):** `users.role` (`admin`, `viewer`) era el único rol; `require_module_access` derivaba de él el rol en cada módulo.
  - **Etapa 1 (R0, ADR 007):** tabla `user_module_roles(user_id, module_key, role)` con roles `viewer < operator < admin`. `users.role = admin` es el **administrador de plataforma** (usuarios, empleados, configuración general) y tiene `admin` en todos los módulos; los demás usuarios acceden solo a los módulos asignados. Todo endpoint de módulo usa `require_module_access("<módulo>", min_role)`; `GET /auth/me` devuelve `modules: [{key, name, role}]`. Asignación por CLI (`grant-module-role`, `revoke-module-role`).
  - **Áreas protegidas (R8‑1, ADR 010):** un módulo declara áreas con autorización propia en `ModuleSpec.protected_areas` (`ProtectedAreaSpec(key="<módulo>.<área>", name, explicit_grant_required)`). La capa central resuelve `area_role` / `require_area_access`: el área exige un rol en su módulo **y** un permiso propio (fila de `user_module_roles` con la clave del área); con `explicit_grant_required` el admin de plataforma no la hereda. Sin el área, 404 sin detalles; por debajo del rol, 403. `GET /auth/me` devuelve además `areas: [{key, name, role}]`. El admin de plataforma otorga y revoca (CLI o `PUT /auth/access-grants`, `POST /auth/access-grants/revoke`) sin poder leer el área antes. Primera área: `settlements.employee_finance` (Rendiciones › Empleados); segunda, `settlements.store_sys_import` (Rendiciones › Integración Store Sys, R9‑1, ADR 011).
  - **Etapa 2 (solo si hace falta):** permisos por acción como mapa en código rol → permisos.
  - `admin` de plataforma: usuarios, empleados y configuración general. `admin` de módulo: imports, configuración, correcciones y aprobaciones del módulo. `operator`: operación diaria (en Rendiciones: cargar y cerrar rendiciones, acreditar pendientes, registrar pagos). `viewer`: lectura.
- CSRF: `SameSite=Lax` + header custom obligatorio en métodos mutantes.
- Rate limit simple en login (en memoria, por IP+usuario).
- Validación estricta Pydantic en todos los endpoints; queries parametrizadas.
- Secretos solo por variables de entorno; `.env.example` sin valores reales.
- PII (CUIT, teléfono, dirección) nunca en logs ni mensajes de error.
- Archivos subidos: ver §6.1. Sin ejecución de macros ni contenido embebido.

## 9. Observabilidad

- Logging JSON estructurado (stdlib `logging` + formatter JSON) con `request_id` (header `X-Request-ID`) e `import_batch_id`.
- `/api/v1/health` (liveness + DB).
- Métricas de import en `import_batches` (duración, filas, errores).
- Hook de error tracking preparado (Sentry opcional por variable de entorno, desactivado por defecto).

## 10. Despliegue y backups

- Producción V1: un host con Docker Compose (db, backend, frontend) detrás de un reverse proxy con TLS (Caddy).
- Backups: `pg_dump` diario comprimido, retención 14 diarios + 8 semanales, copia fuera del host; **prueba de restore mensual** documentada en README (script `scripts/backup.sh` / `scripts/restore.sh`).

## 11. Performance

- Objetivo: KPIs principales < 1–2 s con datos de varios años.
- Índices diseñados desde queries reales (ver `DATA_MODEL.md §11`); `EXPLAIN ANALYZE` ante consultas lentas.
- Sin precomputación al inicio. Si hace falta: tabla agregada diaria `daily_customer_sales` o vistas materializadas, en ese orden.

## 12. Futuras integraciones y extensiones

- APIs AXUM / STORE SYS → nuevos `SourceAdapter`.
- Asistente IA: consumirá el **registro de métricas** y las reglas estructuradas (no SQL libre ni UI).
- ML (churn, propensión, next best product): puntos de extensión en `modules/commercial/customer_status/` y `opportunities/rules/` (una regla puede ser un modelo con el mismo contrato: inputs → score + motivos).
- Market basket (support/confidence/lift) a partir de `sale_items`.

## 13. ADRs

- 001 PostgreSQL como única base.
- 002 FastAPI + monolito modular.
- 003 Modelo canónico de importación con adaptadores.
- 004 Sesiones server‑side en lugar de JWT.
- 005 Registro centralizado de métricas en backend.
- 006 Plataforma modular (núcleo compartido + módulos).
- 007 Módulo Rendiciones (`settlements`): identidad, empleados en plataforma, roles por módulo, ledger propio, logo.
