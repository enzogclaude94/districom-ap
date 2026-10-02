# ARCHITECTURE — Districom · Inteligencia Comercial

> v0.1 · 2026-10-02 · Estado: propuesta para validar en Fase 0.

## 1. Contexto

Tercera capa de **inteligencia comercial** sobre dos sistemas fuente:

- **AXUM** — preventa: preventistas, rutas, clientes, programación y resultado de visitas, pedidos.
- **STORE SYS** — administración y facturación: facturas, ítems, notas de crédito, productos, canal.

La aplicación **no** reemplaza ni escribe en esos sistemas. Ingesta inicial por archivos (XLSX/CSV); APIs en el futuro.
Usuarios iniciales: propietario, administración, dirección (pocos usuarios, roles `admin` y `viewer`).
Escala: 3 preventistas, 7 rutas, ~600 clientes, 500+ SKU, historia desde 2026-01-01.

## 2. Vista general

```
                ┌────────────────────────────┐
  Navegador ──▶ │  Next.js (frontend)        │   UI, filtros, tablas, gráficos.
                │  /api/* → rewrite          │   No calcula KPIs.
                └─────────────┬──────────────┘
                              │ REST/JSON  /api/v1  (mismo origen, cookie de sesión)
                ┌─────────────▼──────────────┐
                │  FastAPI (backend)         │
                │  ├─ api/        (routers)  │
                │  ├─ auth/                  │
                │  ├─ masters/   (maestros)  │
                │  ├─ imports/   (pipeline)  │
                │  ├─ analytics/ (métricas)  │
                │  ├─ customer_status/       │
                │  ├─ opportunities/ (motor) │
                │  ├─ goals/ incentives/     │
                │  ├─ reports/               │
                │  └─ core/ db/ settings/    │
                └─────────────┬──────────────┘
                              │ SQLAlchemy 2 (Core para analítica, ORM para CRUD)
                ┌─────────────▼──────────────┐
                │  PostgreSQL 17             │
                └────────────────────────────┘
```

**Monolito modular**: un proceso backend, un proceso frontend, una base. Sin microservicios, Redis, Celery ni Kafka en V1. Los módulos operacionales se comunican por interfaces de servicio Python, nunca leyendo tablas de otro módulo directamente salvo a través de su repositorio/servicio. **Única excepción:** las consultas analíticas de solo lectura de `analytics/` (ver §4.1).

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

## 4. Límites entre módulos (backend)

```
backend/app/
├── api/v1/            # routers delgados: validan input, llaman servicios, serializan
├── core/              # settings, logging, errores, seguridad, request_id
├── db/                # engine, sesión, base declarativa, convenciones
├── auth/              # users, sessions, RBAC
├── settings_store/    # parámetros comerciales (app_settings) con defaults seguros
├── masters/           # salespeople, routes, customers, products, catálogos, asignaciones
├── imports/
│   ├── adapters/      # axum_xlsx, storesys_xlsx, ... (Source Adapter)
│   ├── mapping/       # detección y plantillas de columnas
│   ├── normalize/     # fechas, números, CUIT, textos
│   ├── validate/
│   ├── resolve/       # entity resolution contra IDs externos y mappings
│   └── pipeline.py    # orquesta batch → staging → commit
├── analytics/
│   ├── metrics/       # REGISTRO ÚNICO de métricas (IDs = METRICS.md)
│   ├── filters.py     # filtros globales → condiciones SQL
│   ├── periods.py     # períodos y período de comparación
│   └── dashboard.py routes.py salespeople.py customers.py penetration.py ...
├── customer_status/   # frecuencia esperada, overdue ratio, estados, recuperaciones
├── opportunities/
│   ├── rules/         # una regla por archivo, con id + versión
│   ├── scoring.py
│   ├── lifecycle.py
│   └── attribution.py # conversión
├── goals/  incentives/  reports/
└── audit/
```

Reglas:
- **Las reglas de negocio viven en el backend.** El frontend nunca deriva un KPI; recibe valores ya calculados + metadatos (numerador, denominador, definición).
- **La analítica solo lee el modelo canónico**, nunca columnas de archivos.
- Un `utils/` genérico está prohibido; los helpers viven en el módulo que los usa.

### 4.1 Excepción analítica: lectura directa del modelo canónico

Los módulos **operacionales** (`masters/`, `imports/`, `customer_status/`, `opportunities/`, `goals/`, `incentives/`, `auth/`…) mantienen encapsulación: dueños de sus tablas, escriben solo a través de sus servicios y exponen interfaces Python.

`analytics/` es la excepción explícita, para no sobrearquitecturar consultas BI:

- **Puede** escribir consultas SQL (SQLAlchemy Core) que crucen directamente las tablas del modelo canónico que necesite: `sales`, `sale_items`, `customers`, `products`, `product_categories`, `brands`, `routes`, `salespeople`, asignaciones, `scheduled_visits`, `visits`, `orders`, `customer_status_*`, `opportunities`, etc. No tiene que encadenar servicios o repositorios de otros módulos para armar un join.
- **Solo lectura.** `analytics/` nunca inserta, actualiza ni borra filas de otro módulo. Los procesos que escriben resultados derivados (estados de cliente, oportunidades) viven en su propio módulo, aunque lean de `analytics/`.
- **Centralizado.** Esas consultas están únicamente en `analytics/` (registro de métricas, `filters.py`, `periods.py` y servicios por pantalla). Los routers y los demás módulos no escriben SQL analítico propio: piden la métrica a `analytics/`.
- **Contrato:** los nombres de tablas y columnas del modelo canónico (DATA_MODEL.md) son la interfaz pública para `analytics/`. Un cambio de schema que rompa una consulta analítica se detecta por los tests de métricas y se corrige en el mismo PR.
- Los módulos operacionales pueden consumir métricas de `analytics/` (por ejemplo, `opportunities/` usa penetración por pares), nunca al revés.

## 5. Frontend

```
frontend/src/
├── app/(auth)/login
├── app/(app)/            # layout con sidebar + topbar de filtros
│   ├── page.tsx          # Inicio (dashboard)
│   ├── comercial/{preventistas,rutas,clientes,oportunidades}
│   ├── portfolio/{productos,categorias,marcas,penetracion}
│   ├── gestion/{objetivos,incentivos,reuniones}
│   ├── boca
│   ├── datos/{importaciones,calidad}
│   └── configuracion
├── features/<modulo>/    # componentes + hooks + queries del módulo
├── components/ui/        # shadcn/ui
├── components/common/    # KpiCard, DataTable, EmptyState, ErrorState, PeriodPicker...
├── lib/api/              # cliente HTTP tipado (tipos generados desde OpenAPI)
├── lib/filters/          # estado de filtros globales sincronizado con la URL
└── styles/tokens.css
```

- **Filtros globales en la URL** (`?from=…&to=…&salesperson=…`) → enlaces compartibles y coherencia entre módulos.
- **Tipos de API generados desde OpenAPI** (`openapi-typescript`), para que frontend y backend no diverjan.
- Estados obligatorios en cada vista de datos: loading (skeleton), empty (con explicación), error (mensaje comprensible + `request_id`).
- Prioridad: desktop → tablet → móvil de consulta. Light mode excelente; dark mode preparado por tokens.

## 6. Pipeline de importación

```
UPLOAD → IDENTIFICACIÓN → MAPPING → PARSING → VALIDACIÓN → NORMALIZACIÓN
       → RESOLUCIÓN DE ENTIDADES → PREVIEW → IMPORT (transacción) → CHECKS → AUDITORÍA
```

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

Se ejecutan como comandos CLI (`python -m app.jobs …`) y desde un endpoint admin. Programación diaria vía cron del host o del contenedor; sin scheduler externo.

## 8. Seguridad

- Usuarios locales, password Argon2id, política de longitud mínima 12.
- Sesión: token aleatorio de 256 bits; en DB se guarda **solo su hash**; cookie `HttpOnly; Secure; SameSite=Lax`; expiración deslizante (8 h) y absoluta (7 días).
- RBAC: `admin` (todo, incluidos imports/configuración/aprobaciones), `viewer` (lectura). Tabla de permisos preparada para crecer.
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
- ML (churn, propensión, next best product): puntos de extensión en `customer_status/` y `opportunities/rules/` (una regla puede ser un modelo con el mismo contrato: inputs → score + motivos).
- Market basket (support/confidence/lift) a partir de `sale_items`.

## 13. ADRs a registrar en Fase 0

- 001 PostgreSQL como única base.
- 002 FastAPI + monolito modular.
- 003 Modelo canónico de importación con adaptadores.
- 004 Sesiones server‑side en lugar de JWT.
- 005 Registro centralizado de métricas en backend.
