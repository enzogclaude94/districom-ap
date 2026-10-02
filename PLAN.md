# PLAN — Districom · Plataforma de Inteligencia Comercial

> Estado del documento: **v0.2 — Fase 0 implementada, en validación**
> Última actualización: 2026-10-02
> Fuente de requisitos: prompt maestro adjunto por Enzo (`message.md`, 165 secciones).

---

## 1. Estado actual

### 1.1 Resultado de la exploración

| Ítem revisado | Hallazgo |
|---|---|
| Repositorio del proyecto | `enzogclaude94/districom-ap`, creado vacío por Enzo el 2026-10-02 (sin commits). |
| Código existente | Ninguno. |
| `CLAUDE.md` | No existía. Se crea en este paso (borrador). |
| README / Docker / `.env` / CI | No existen. |
| Tests | No existen. |
| Datos reales (exports AXUM / STORE SYS) | **No se recibieron todavía.** Ningún archivo de datos en el proyecto. |
| Deuda técnica / decisiones previas | Ninguna: proyecto **greenfield**. |

**Conclusión:** se trata como proyecto greenfield. Las recomendaciones del prompt maestro (Next.js + FastAPI + PostgreSQL, monolito modular, monorepo) se adoptan, con los ajustes justificados en §4.

### 1.2 Fase activa

**Fase 0 — Fundación y arquitectura**, sub‑etapa *validación*. El código está en la rama `phase-0-foundation` (PR abierto contra `main`). Resultado por criterio en §10. **Fase 1 no empieza hasta que Enzo valide la Fase 0 y todos los criterios estén en PASS.**

---

## 2. Objetivos de esta etapa

1. Dejar documentada la arquitectura, el modelo de datos y las métricas **antes** de programar.
2. Detectar riesgos y contradicciones del prompt que cambien diseño.
3. Definir criterios de aceptación verificables para Fase 0.
4. Identificar qué información real hace falta para que Fase 1 (importación) no se construya sobre supuestos.

---

## 3. Riesgos y contradicciones detectadas

Ordenados por impacto. Cada uno tiene un **supuesto por defecto** para no bloquear el avance.

### R1 — Conversión visita → compra "mismo día" vs. modelo de preventa (ALTO)
En preventa, el pedido se toma el día de la visita y la **factura suele emitirse el día de entrega (D+1 o D+2)**. Si la conversión se mide con ventas de STORE SYS del mismo día, la conversión real aparecería cercana a 0 %.
**Supuesto:** se definen dos métricas (ver `METRICS.md`):
- `visit.conversion_order` — visita con pedido registrado en AXUM (métrica principal si AXUM exporta pedidos/resultado).
- `visit.conversion_invoiced` — visita con factura válida en ventana `[visit_date, visit_date + conversion_window_days]`, default **2 días**, configurable.
**A confirmar:** desfase real pedido → factura.

### R2 — Identidad de clientes entre AXUM y STORE SYS (ALTO)
Es el mayor riesgo de integración. No sabemos si ambos sistemas comparten un código de cliente.
**Supuesto:** cliente canónico propio con IDs externos separados; matching automático solo por clave exacta (código compartido o CUIT normalizado); todo lo demás queda **pendiente de revisión manual**. Nunca fuzzy matching irreversible.

### R3 — Semántica de notas de crédito y devoluciones (ALTO, requiere confirmación)
El prompt pide confirmación antes de decisiones contables. **Supuesto provisional:** documentos separados con `document_type` (INVOICE, CREDIT_NOTE, DEBIT_NOTE) e importes **con signo** (NC negativa); `net_sales` = suma de importes con signo, sin impuestos. Se valida con el primer export real antes de cerrar Fase 1.

### R4 — "Cliente nuevo" con historia que empieza en 2026 (MEDIO)
Con datos desde enero 2026, todo cliente parecería "nuevo" en sus primeras semanas.
**Supuesto:** parámetro `history_start_date` (2026-01-01) + `new_customer_warmup_days` (default 60). Un comprador solo cuenta como nuevo si su primera compra es posterior a `history_start_date + warmup`. Lo mismo aplica a estados de cliente: sin suficientes observaciones → `INSUFFICIENT_DATA`, nunca `LOST` por falta de historia.

### R5 — Listas de estados inconsistentes en el prompt (BAJO)
§37 define 5 niveles (NORMAL, ATTENTION, AT_RISK, DORMANT, LOST); el ejemplo de §39 omite ATTENTION.
**Decisión:** `health_status ∈ {NORMAL, ATTENTION, AT_RISK, DORMANT, LOST, NO_PURCHASES}` + `frequency_segment` separado.

### R6 — Cliente recuperado requiere estados históricos (MEDIO)
Para saber que un cliente "estaba DORMANT" hay que tener el estado calculado en fechas pasadas.
**Decisión:** el motor de estados se puede **re-ejecutar día a día desde `history_start_date`** (backfill determinístico) y guarda solo cambios (SCD‑2) en `customer_status_history`.

### R7 — "Oportunidad trabajada" sin app de preventistas (MEDIO)
Los preventistas no usan esta app, así que nadie marca "trabajada" en campo.
**Supuesto:** WORKED se registra (a) manualmente por admin, o (b) inferido si hay una visita AXUM al cliente después de `first_communicated_at`. Se guarda el origen (`MANUAL` / `INFERRED_VISIT`).

### R8 — Canal preventa vs. boca de expendio (MEDIO)
No sabemos si STORE SYS trae un campo de canal. Puede deducirse por punto de venta, serie de comprobante o vendedor.
**Supuesto:** el mapping de importación permite derivar el canal por regla configurable; si no se puede determinar → `OTRO` y aparece en Calidad de datos.

### R9 — Cliente genérico "Consumidor Final" en boca de expendio (MEDIO)
Las ventas de mostrador suelen ir a un cliente genérico. Contarlo como "comprador" distorsiona penetración, ticket y estados.
**Decisión:** flag `customers.is_generic`; se excluye de universos de clientes, estados y oportunidades, pero sus ventas cuentan en ventas totales y en el módulo Boca.

### R10 — Atribución de ventas a preventista / ruta (MEDIO)
La factura puede traer un vendedor distinto del preventista asignado (rotaciones, reemplazos).
**Decisión:** `sales.salesperson_id` y `sales.route_id` guardan la atribución **resuelta**, con `attribution_source ∈ {DOCUMENT, ASSIGNMENT_AT_DATE, UNKNOWN}`. Regla: si el documento trae vendedor mapeable se usa; si no, la asignación vigente en la fecha de negocio.

### R11 — Período de comparación para presets de calendario (BAJO)
"N días inmediatamente anteriores" es claro para rangos; para "mes actual" (incompleto) compararlo con 30 días previos mezcla calendarios.
**Decisión:** rangos personalizados / "últimos N días" → N días previos. Presets de calendario (semana/mes actual) → mismo tramo transcurrido del período calendario anterior (MTD vs. MTD). Documentado en `METRICS.md §2`.

### R12 — Disponibilidad de horario en ventas de boca (BAJO)
El heatmap día × hora solo existe si STORE SYS exporta hora. Se detecta en el import; si no hay hora, el módulo lo dice explícitamente.

---

## 4. Modificaciones justificadas respecto del prompt

| # | Recomendación del prompt | Propuesta | Por qué |
|---|---|---|---|
| M1 | Pandas o Polars para imports | **Sin librería de dataframes en V1**: `openpyxl` (read‑only) + `csv` estándar + validación por fila con Pydantic. | Volumen chico (decenas de miles de filas/mes). La validación es por fila y necesita errores con número de fila; un dataframe no aporta y agrega una dependencia pesada. Se reevalúa si un import supera ~1 M filas. |
| M2 | JWT implícito | **Sesiones server‑side** en PostgreSQL con cookie `HttpOnly; Secure; SameSite=Lax`. | Pocos usuarios, revocación inmediata, sin manejo de refresh tokens. |
| M3 | CORS restrictivo | Next.js hace **rewrite** de `/api/*` al backend → mismo origen en producción; CORS solo para desarrollo. | Elimina toda una clase de problemas de cookies cross‑site. |
| M4 | `customer.salesperson_id` evitado | Además de las tablas de asignación con historia, se mantiene una **vista** `customer_current_assignment`. | Simplifica queries frecuentes sin duplicar datos. |
| M5 | Métricas "centralizadas" | **Registro de métricas en backend** (`app/analytics/metrics/`), cada métrica con ID estable igual al de `METRICS.md`. El frontend nunca calcula KPIs, solo formatea. | Garantiza una sola definición; testeable con fixtures. |
| M6 | `health_status` | Agrega `NO_PURCHASES` (registrado sin compras) y separa `INSUFFICIENT_DATA` como segmento de frecuencia. | Evita clasificar como perdido a quien nunca compró o tiene poca historia. |
| M7 | Conversión mismo día | Dos métricas (pedido AXUM / factura en ventana). | R1. |
| M8 | Herramientas | `uv` para Python, `pnpm` para frontend, `ruff` + `mypy`, `eslint` + `prettier` + `tsc`, Playwright para E2E. | Rápidas, estándar, sin duplicados. |

Ninguna modificación cambia el alcance funcional.

---

## 5. Decisiones pendientes (necesitan a Enzo)

| ID | Pregunta | Default mientras tanto | Bloquea |
|---|---|---|---|
| D1 | ~~¿Repo nuevo o existente?~~ **Resuelto:** `districom-ap`. | — | — |
| D2 | Exports de ejemplo de AXUM y STORE SYS (aunque sea 1 semana, anonimizados). | Demo data sintética. | Cierre de Fase 1. |
| D3 | ¿AXUM y STORE SYS comparten código de cliente / producto? | Asumir que no. | Diseño de matching (Fase 1). |
| D4 | ¿Cómo se registran NC y devoluciones en STORE SYS? ¿Importes con o sin IVA? | NC separadas, signo negativo, neto sin IVA. | Definición final de `sales.net_total`. |
| D5 | Desfase típico pedido → factura en preventa. | 2 días. | Conversión facturada. |
| D6 | ¿Cómo se distingue boca de expendio en STORE SYS? | Regla configurable de mapping. | Fase 1/10. |
| D7 | Hosting objetivo (VPS propio, cloud, on‑premise). | Docker Compose en un VPS. | Fase 0 (deploy), no bloquea desarrollo local. |

---

## 6. Fase 0 — Tareas

Orden de ejecución, cada una con commit propio.

1. `chore: initialize monorepo` — estructura de carpetas, `.gitignore`, `.editorconfig`, `README` mínimo, documentos de este plan.
2. `chore(infra): add docker compose with postgres` — PostgreSQL 17, volumen persistente, healthcheck.
3. `chore(backend): scaffold fastapi app` — `uv`, FastAPI, configuración con `pydantic-settings`, logging estructurado JSON con `request_id`, manejador de errores normalizado, `/api/v1/health` (app + DB).
4. `chore(db): add sqlalchemy + alembic` — base declarativa, convención de nombres de constraints, migración inicial: `users`, `user_sessions`, `app_settings`, `audit_events`, `source_systems`.
5. `feat(auth): local auth with server-side sessions` — hash Argon2id, login/logout/me, roles `admin`/`viewer`, dependencia `require_role`, comando CLI para crear el primer admin.
6. `chore(frontend): scaffold next.js app` — Next.js (App Router) + TS estricto, Tailwind, shadcn/ui, TanStack Query, tokens de diseño (light; dark preparado), layout con sidebar jerárquica, página de login, página "Inicio" vacía con empty state, rewrite `/api → backend`.
7. `test: baseline tests` — pytest (unit + integración contra PostgreSQL real en Docker), Vitest + Testing Library, un E2E Playwright de login.
8. `ci: github actions` — lint, format check, typecheck, tests backend (servicio postgres), tests frontend, build.
9. `docs: demo data strategy + ADRs` — `docs/ADR/001..005`, `docs/TESTING.md`, `docs/SECURITY.md`, estrategia del generador de demo (implementación en Fase 1).
10. `docs: finalize phase 0` — `CHANGELOG.md`, actualización de `PLAN.md` y `ROADMAP.md` con PASS/FAIL.

### Dependencias
- 1 → todo. 3 → 4 → 5. 6 depende de 5 para el login real. 8 depende de 7.

---

## 7. Criterios de aceptación — Fase 0

Cada criterio es verificable con un comando o prueba concreta.

| # | Criterio | Verificación |
|---|---|---|
| F0‑1 | PostgreSQL disponible localmente | `docker compose up -d db` → healthcheck `healthy`. |
| F0‑2 | Migraciones funcionan desde cero y son reversibles | `alembic upgrade head` sobre DB vacía; `alembic downgrade base` y `upgrade head` de nuevo sin error (test automatizado). |
| F0‑3 | Backend arranca localmente | `uv run fastapi dev` responde. |
| F0‑4 | Health check | `GET /api/v1/health` → `200 {"status":"ok","db":"ok"}`; con DB caída → `503` con `db: "error"`. |
| F0‑5 | Autenticación base | Tests: login correcto crea sesión; password incorrecto → 401 sin revelar si el usuario existe; endpoint protegido sin sesión → 401; `viewer` en endpoint `admin` → 403; logout invalida sesión. |
| F0‑6 | Passwords hasheadas | Test: el valor guardado es Argon2id, nunca texto plano. |
| F0‑7 | Errores normalizados | Test: validación inválida → `422` con `{code, message, details, request_id}`. |
| F0‑8 | Logs estructurados | Cada request loguea JSON con `request_id`; test verifica que el password no aparece en logs. |
| F0‑9 | Frontend arranca localmente | `pnpm dev`; login → Inicio con sidebar. |
| F0‑10 | Sistema de diseño inicial | Tokens de color/tipografía/espaciado definidos; componentes base (Button, Card, Badge, Table, Skeleton, EmptyState, ErrorState) en uso. |
| F0‑11 | Calidad | `ruff check`, `ruff format --check`, `mypy --strict app`, `eslint`, `prettier --check`, `tsc --noEmit`, `next build`: todos en verde. |
| F0‑12 | Tests básicos pasan | `pytest` y `vitest` en verde; E2E de login en verde. |
| F0‑13 | CI | GitHub Actions ejecuta F0‑11 y F0‑12 en cada push/PR y queda verde. |
| F0‑14 | Configuración | `.env.example` lista todas las variables sin secretos; la app falla al arrancar con mensaje claro si falta una obligatoria. |
| F0‑15 | Documentación | `README` explica setup completo desde clon limpio; un tercero puede levantar todo siguiendo solo el README. |
| F0‑16 | Arquitectura y modelo revisados | `ARCHITECTURE.md`, `DATA_MODEL.md`, `METRICS.md` revisados contra el prompt maestro (checklist en §9). |

**No se avanza a Fase 1 con algún criterio en FAIL.**

---

## 8. Pruebas necesarias en Fase 0

- `tests/integration/test_migrations.py` — upgrade/downgrade/upgrade desde base vacía.
- `tests/integration/test_health.py` — DB ok / DB caída.
- `tests/integration/test_auth.py` — casos de F0‑5 y F0‑6.
- `tests/unit/test_errors.py` — formato de error.
- `tests/unit/test_settings.py` — variables faltantes.
- `frontend/tests/login.test.tsx` — formulario y estado de error.
- `e2e/login.spec.ts` — login → Inicio.

---

## 9. Checklist de cobertura del prompt maestro

| Sección del prompt | Dónde queda cubierta |
|---|---|
| §1 documentos | Este paquete + Fase 0 tarea 9 |
| §2–8 negocio y supuestos | `ARCHITECTURE.md §1`, `METRICS.md`, `docs/BUSINESS_RULES.md` (Fase 1) |
| §9–12 arquitectura, API, integraciones | `ARCHITECTURE.md` |
| §13–33 modelo de datos | `DATA_MODEL.md` |
| §34–41 estados de cliente | `DATA_MODEL.md §8`, `METRICS.md §6` |
| §42–45 métricas | `METRICS.md` |
| §46–89 módulos | `ROADMAP.md` (fases 2–10) |
| §90–103 imports y demo | `ARCHITECTURE.md §6`, `ROADMAP.md` Fase 1 |
| §104–115 UX y performance | `ARCHITECTURE.md §5`, `docs/UX_GUIDELINES.md` (Fase 0) |
| §121–126 seguridad, logs, backups | `ARCHITECTURE.md §8–10` |
| §127–134 testing | `CLAUDE.md §5`, `docs/TESTING.md` |
| §141 roadmap | `ROADMAP.md` |
| §159–162 definición de terminado | `CLAUDE.md §7` |

---

## 10. Verificación de Fase 0 (2026-10-02)

Verificado en el entorno de desarrollo (PostgreSQL 16 local; Docker no disponible en ese entorno). CI de GitHub Actions repite todo con PostgreSQL 17 y agrega el smoke test de Docker Compose.

| # | Criterio | Resultado | Evidencia |
|---|---|---|---|
| F0‑1 | PostgreSQL disponible | PASS | Local con PostgreSQL 16; en CI, `docker compose up --wait` levanta db + backend + frontend y `/api/v1/health` responde `db: ok` a través del frontend. |
| F0‑2 | Migraciones desde cero y reversibles | PASS | `test_migrations.py`: upgrade → downgrade → upgrade; `alembic check` sin diferencias. |
| F0‑3 | Backend arranca | PASS | `uvicorn app.main:app` (también lo levanta Playwright). |
| F0‑4 | Health check | PASS | `test_health.py`: 200 `{status: ok, db: ok}` / 503 con DB caída. |
| F0‑5 | Autenticación base | PASS | `test_auth.py` (12 casos): login, 401, 403, logout revoca, inactivo, rate limit, CSRF. |
| F0‑6 | Passwords hasheadas | PASS | Test verifica prefijo `$argon2id$`. |
| F0‑7 | Errores normalizados | PASS | `test_errors.py`. |
| F0‑8 | Logs estructurados sin secretos | PASS | JSON con `request_id`; test verifica que la contraseña no aparece. |
| F0‑9 | Frontend arranca | PASS | `pnpm build && pnpm start`; login → Inicio verificado en E2E. |
| F0‑10 | Sistema de diseño inicial | PASS | Tokens en `globals.css`; Button, Card, Badge, Input, Table, Skeleton, EmptyState, ErrorState en uso. Revisión visual desktop y móvil. |
| F0‑11 | Calidad | PASS | ruff, ruff format, mypy strict, eslint (0 warnings), prettier, tsc, next build. |
| F0‑12 | Tests | PASS | Backend 26, frontend 13, E2E 6 (3 flujos × desktop/móvil). |
| F0‑13 | CI | PASS | `ci.yml` en verde en el PR #1: backend, frontend, E2E y Docker Compose. |
| F0‑14 | Configuración | PASS | `.env.example` completo; `test_settings.py` verifica fallo claro sin variables obligatorias. |
| F0‑15 | Documentación de setup | PASS | `README.md` (desarrollo, Docker, migraciones, tests, E2E, backups, troubleshooting). |
| F0‑16 | Arquitectura y modelo revisados | PENDIENTE | Requiere validación de Enzo de `ARCHITECTURE.md`, `DATA_MODEL.md` y `METRICS.md`. |

### Desvíos respecto del plan
- **TanStack Table y ECharts** no se instalaron todavía: entran en Fase 2, cuando haya tablas y gráficos reales (evita dependencias sin uso).
- El email del login se valida como texto, no como formato de email: solo busca una cuenta existente.
- `API_INTERNAL_URL` del frontend se resuelve **al compilar** (rewrites de Next.js). Documentado en README y Dockerfile.

### Deuda técnica conocida
- Rate limit de login en memoria: válido con una sola instancia del backend (ADR 002).
- Detrás de proxy, la IP del rate limit es la del frontend; la clave incluye el email, así que sigue limitando por cuenta.
- No hay alta/edición de usuarios desde la UI (solo CLI). Se agrega cuando haya más de un par de usuarios.
- Auditoría de dependencias (`pip-audit`, `pnpm audit`) pendiente de sumar a CI.
