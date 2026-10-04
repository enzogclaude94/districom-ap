# CLAUDE.md — Guía operativa del repositorio

**DistriCom** — **plataforma modular** para una distribuidora de bebidas: un núcleo compartido (plataforma) + módulos de negocio, en una sola aplicación. Módulos aprobados:

- **Inteligencia Comercial** (`commercial`, `/comercial`): capa de análisis sobre AXUM (preventa) y STORE SYS (facturación). No es ERP ni facturador; nunca escribe en los sistemas fuente.
- **Rendiciones** (`settlements`, `/rendiciones`): rendiciones de reparto y boca de expendio, cajas, cheques, pendientes, gastos, combustible, pagos y liquidación semanal de empleados, con un ledger propio como fuente de verdad (ADR 007). Avanza por fases R0–R9.

No se desarrollan otros módulos ni se anticipan sus modelos sin aprobación explícita de Enzo (ADR 006). Dentro de Rendiciones, cada fase crea solo lo suyo (ADR 007).

Leer antes de cualquier tarea: este archivo → `PLAN.md` → `ROADMAP.md` → documentos del módulo afectado (Rendiciones: ADR 007, ADR 008 y `DATA_MODEL.md §13`).

## 1. Arquitectura (resumen)

Monorepo, monolito modular: plataforma (`app/platform/`) + módulos (`app/modules/<módulo>/`), una base con un schema por módulo. Detalle en `ARCHITECTURE.md` y ADR 006.

```
frontend/   Next.js + TS + Tailwind + shadcn/ui + TanStack Query/Table + ECharts
backend/    FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 (Python 3.12+, uv)
db          PostgreSQL 17 (Docker Compose)
docs/       reglas de negocio, imports, motor de oportunidades, ADRs
scripts/    seed demo, backup/restore, utilidades de desarrollo
```

Principios no negociables:
- **Reglas de negocio y métricas solo en el backend.** El frontend no calcula KPIs.
- **Regla de dependencias** (verificada en CI): módulo → plataforma → core/db. La plataforma nunca importa de un módulo; un módulo nunca importa internals de otro (`ARCHITECTURE.md §4.2`).
- **Única fuente de verdad:** personas (`employees` + `employee_functions`), clientes, localidades e identidades externas viven una sola vez en plataforma. Preventista, repartidor, etc. son **funciones** del empleado, no personas nuevas; cada módulo registra sus códigos de función en código.
- **Dinero en Rendiciones:** todo movimiento de valores pasa por `app/modules/settlements/ledger/service.py`, la **única puerta** de escritura de `financial_operations` y `ledger_entries` (un test lo verifica): entradas inmutables, operaciones balanceadas e idempotentes, correcciones por reverso, ningún saldo editable, cajas nunca negativas (ADR 007, ADR 008).
- **Toda métrica se define primero en `METRICS.md`** y se implementa una sola vez en `backend/app/modules/commercial/analytics/metrics/` con el mismo `id`.
- **La analítica opera sobre el modelo canónico**, nunca sobre columnas de archivos. `analytics/` de un módulo puede cruzar directamente sus tablas y las de plataforma con SQL de **solo lectura**, nunca las de otro módulo; los paquetes operacionales mantienen su encapsulación (`ARCHITECTURE.md §4.3`).
- **Nunca fusionar entidades por nombre** sin confirmación; usar `external_entity_mappings`.
- **Nunca destruir historia** de asignaciones ni estados (tablas con vigencia).
- **Parámetros en `app_settings`** con defaults en código junto a su dueño, claves con scope (`platform.*`, `commercial.*`, `settlements.*`); nada hardcodeado en componentes.
- **Marca:** colores y tipografías solo vía tokens; wordmark e ícono solo vía `<Wordmark />` / `<BrandIcon />`; logo de Rendiciones solo vía su componente y sin modificar su apariencia (`docs/BRAND.md`). Verde/ámbar/rojo únicamente para estados de negocio.

## 2. Comandos habituales

```bash
# Infra
docker compose up -d db                       # PostgreSQL (crea también districom_test)
docker compose up -d --build                  # stack completo en :3000
# Backend
cd backend
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py                # :8000, docs en /api/docs
uv run pytest                                 # unit + integración (usa TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run lint-imports                           # límites plataforma/módulos (ARCHITECTURE §4.2)
uv run alembic check                          # modelos == migraciones
uv run python -m app.cli create-admin --email ... --name ...
uv run python -m app.cli grant-module-role --email ... --module settlements --role operator
# Frontend
cd frontend
pnpm install
pnpm dev                                      # :3000, /api/* → backend
pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build
pnpm e2e                                      # Playwright; requiere admin E2E (README)
```

Este bloque debe reflejar siempre los comandos reales. Seed de demo: Fase 1.

## 3. Estructura

Ver `ARCHITECTURE.md §4–5`. Primero por **dueño** (plataforma o módulo) y dentro de cada uno por **dominio** (`imports/`, `analytics/`, `customer_status/`, `opportunities/`, `ledger/`…), no por capa técnica global. Endpoints de módulo bajo `/api/v1/<módulo>/` con `require_module_access`; pantallas bajo su prefijo (`/comercial/…`, `/rendiciones/…`). Prohibido un `utils.py` genérico o servicios sin responsabilidad definida.

## 4. Convenciones

- Python: tipado completo (`mypy` estricto en `app/`), `ruff` para lint y formato, funciones chicas, docstrings solo donde explican decisión/invariante/regla.
- TS: `strict`, sin `any` salvo justificado; tipos de API generados desde OpenAPI.
- API: `/api/v1/…`, orientada a casos de uso, paginada, con filtros y sort validados; errores `{code, message, details, request_id}`.
- DB: nombres en inglés `snake_case`, sin prefijo de módulo (lo da el schema); PK `bigint identity`; dinero `numeric(14,2)`; fechas de negocio `date`; instantes `timestamptz`.
- UI en español rioplatense; código, tablas y commits en inglés.
- Sin logs con PII (CUIT, teléfono, dirección) ni credenciales. Logging estructurado, nunca `print`.

## 5. Testing

- **No saltear tests.** No se commitea con tests fallando (excepción: commit TDD rojo explícito, marcado `test(…): failing test for …` y seguido inmediatamente por la implementación).
- TDD para reglas matemáticas/de negocio: cobertura, conversión, penetración, estados de cliente, idempotencia de import, scoring, recuperación, objetivos.
- Unit: `backend/tests/unit/` (métricas con fixtures verificables a mano). Integración: `backend/tests/integration/` contra PostgreSQL real (no SQLite). Frontend: Vitest + Testing Library. E2E: Playwright, solo flujos críticos.
- Test de idempotencia de import obligatorio: importar dos veces → conteos sin cambio.
- Antes de cerrar cualquier tarea: correr la suite completa, no solo los tests nuevos (regla de compatibilidad).

## 6. Migraciones

- Todo cambio de schema por **Alembic**; nunca DDL manual.
- Una migración por cambio coherente y por dueño (mensaje `platform: …` / `commercial: …` / `settlements: …`), con `downgrade` cuando sea razonable. Una sola cadena Alembic.
- Test de migraciones: `upgrade head` desde vacío, `downgrade base`, `upgrade head`.
- Migraciones ya mergeadas **no se editan**; se crea una nueva.
- Cambios destructivos (drop de columnas/tablas con datos) requieren confirmación explícita de Enzo.

## 7. Definition of Done

### 7.1 Funcionalidad
Cumple el caso de uso; maneja happy path, errores, empty state y permisos · usa el modelo correcto, no duplica datos, considera nulos, es trazable · métricas documentadas y testeadas con edge cases · endpoint validado, errores consistentes, query razonable · UI con loading/empty/error, responsive razonable, accesible, con drill‑down si corresponde · tests unit/integración y regresión en verde · docs y `CHANGELOG.md` actualizados · lint, format, typecheck, tests y build en verde.

### 7.2 Fase
Criterios críticos en PASS · migraciones desde base limpia · seed funciona · tests anteriores pasan · docs y changelog actualizados · sin errores críticos conocidos · flujo principal probado · commits ordenados · `PLAN.md` y `ROADMAP.md` reflejan el estado real.
Al cerrar una fase, reportar: qué se implementó, archivos/módulos, decisiones, tests ejecutados con resultado, criterios con PASS/FAIL/PARTIAL, problemas, deuda técnica, próximo paso. **No declarar "fase completada" con un criterio crítico en FAIL.**

### 7.3 V1
**Inteligencia Comercial:** flujo completo importar → validar → normalizar → dashboard → rutas → preventistas → clientes → riesgos → penetración → oportunidades → seguimiento → objetivos → incentivos → reunión, más los requisitos de datos, analítica, seguridad, UX y calidad del prompt maestro §162.
**Rendiciones:** fases R0–R9 de `ROADMAP.md` en DONE; el flujo de punta a punta (importe esperado → rendición → efectivo, transferencias, cheque, combustible, pendiente, diferencia → cierre → acreditación del pendiente) probado E2E y trazable en el ledger.

## 8. Archivos que no se modifican sin razón explícita

- `METRICS.md` (cambiar una definición exige actualizar tests y anotar en `CHANGELOG.md`).
- `backend/migrations/versions/*` ya mergeadas.
- `docs/ADR/*` aceptados (se reemplazan con un ADR nuevo que los supersede).
- `.env.example` (solo agregar variables, nunca valores reales).
- Fixtures de métricas en `backend/tests/fixtures/` (son la especificación ejecutable).

## 9. Documentación

- Si cambia una regla de negocio → actualizar `docs/BUSINESS_RULES.md` / `METRICS.md` / `docs/CUSTOMER_STATUS.md` / `docs/OPPORTUNITY_ENGINE.md` **en el mismo commit**.
- Decisiones arquitectónicas importantes → ADR en `docs/ADR/NNN-titulo.md`.
- `CHANGELOG.md` al cerrar cada unidad relevante, explicando qué cambió realmente.

## 10. Commits

Conventional Commits en inglés (`feat(imports): …`, `fix(metrics): …`, `test(…)`, `docs: …`, `chore: …`). Commits chicos y coherentes; nunca una fase entera en un solo commit.

## 11. Compatibilidad

Al agregar una fase: correr todos los tests anteriores, verificar migraciones, probar flujos principales, evitar breaking changes de API; si un contrato debe cambiar, versionarlo o actualizar el cliente en el mismo PR. Nunca arreglar algo nuevo rompiendo algo existente.

## 12. Cuándo preguntar

Ante ambigüedad: elegir el supuesto conservador, documentarlo y hacerlo configurable si impacta. **Pedir confirmación** antes de: interpretación contable de documentos, matching de identidad ambiguo, borrado de datos, cambios destructivos de schema productivo.
