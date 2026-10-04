# DistriCom

Plataforma web modular para una distribuidora de bebidas: un núcleo compartido (usuarios, configuración, identidades) y módulos de negocio en una sola aplicación. El módulo en desarrollo es **Inteligencia Comercial**, que analiza datos de **AXUM** (preventa) y **STORE SYS** (facturación) para responder qué está pasando, dónde se pierden oportunidades y qué acción concreta tomar. No reemplaza a esos sistemas: es una capa de análisis.

**Estado:** Fase 0 DONE; preparación de la plataforma modular (ADR 006) y de la Fase 1. Ver [`PLAN.md`](PLAN.md) y [`ROADMAP.md`](ROADMAP.md).

## Arquitectura

```
Navegador → Next.js (frontend, :3000) → /api/* → FastAPI (backend, :8000) → PostgreSQL
```

Monorepo con monolito modular: una sola aplicación con un núcleo de **plataforma** (`backend/app/platform/`) y **módulos** (`backend/app/modules/<módulo>/`, `frontend/src/modules/<módulo>/`), una base PostgreSQL con un schema por módulo. Detalle en [`ARCHITECTURE.md`](ARCHITECTURE.md); decisiones en [`docs/ADR/`](docs/ADR) (ver ADR 006).

| Carpeta | Contenido |
|---|---|
| `backend/` | FastAPI, SQLAlchemy 2, Alembic, pytest (Python 3.12, `uv`) |
| `frontend/` | Next.js, TypeScript, Tailwind, TanStack Query, Vitest, Playwright (`pnpm`) |
| `docs/` | Seguridad, testing, UX, ADRs |
| `scripts/` | Backup/restore e inicialización de la base |

## Requisitos

- Docker + Docker Compose (o PostgreSQL 16+ local)
- Python 3.12 y [`uv`](https://docs.astral.sh/uv/)
- Node.js 22 y `pnpm` (`corepack enable`)

## Puesta en marcha (desarrollo)

```bash
# 1. Variables de entorno
cp .env.example .env
# Completá SECRET_KEY y POSTGRES_PASSWORD (y la misma contraseña en DATABASE_URL / TEST_DATABASE_URL).
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # genera un SECRET_KEY

# 2. Base de datos (crea también districom_test)
docker compose up -d db

# 3. Backend
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-admin --email vos@empresa.com --name "Tu Nombre"   # pide la contraseña
uv run fastapi dev app/main.py          # http://localhost:8000/api/docs

# 4. Frontend (otra terminal)
cd frontend
pnpm install
pnpm dev                                # http://localhost:3000
```

### Stack completo con Docker

```bash
docker compose up -d --build            # http://localhost:3000
docker compose exec backend python -m app.cli create-admin --email vos@empresa.com --name "Tu Nombre"
```

## Variables de entorno

Todas están en [`.env.example`](.env.example). Obligatorias: `DATABASE_URL`, `SECRET_KEY` (≥ 32 caracteres); el backend no arranca sin ellas y lo dice. En producción: `APP_ENV=production`, `COOKIE_SECURE=true`, `CORS_ORIGINS` vacío. `API_INTERNAL_URL` (adónde Next.js reenvía `/api/*`) se lee **al compilar** el frontend.

## Base de datos y migraciones

```bash
cd backend
uv run alembic upgrade head                        # aplicar
uv run alembic revision --autogenerate -m "..."    # nueva migración (revisarla a mano)
uv run alembic downgrade -1                        # revertir la última
uv run alembic check                               # verifica que modelos y migraciones coinciden
```
Nunca se modifica el schema fuera de Alembic. Política completa en [`CLAUDE.md §6`](CLAUDE.md).

## Tests y calidad

```bash
# Backend: usa TEST_DATABASE_URL (base descartable; se migra desde cero en cada corrida)
cd backend
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest

# Frontend
cd frontend
pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build
```

### Tests E2E

Levantan backend y frontend solos; necesitan la base migrada y un admin:

```bash
cd backend && echo "e2e-password-123" | uv run python -m app.cli create-admin --email e2e-admin@districom.local --name "Admin E2E"
cd ../frontend && pnpm exec playwright install chromium && pnpm e2e
```
(Credenciales configurables con `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD`.)

CI (GitHub Actions) corre todo lo anterior más un smoke test de Docker Compose en cada push y PR. Detalle en [`docs/TESTING.md`](docs/TESTING.md).

## Importaciones

Llegan en la Fase 1 (XLSX/CSV de AXUM y STORE SYS, con mapping de columnas, preview, errores e idempotencia). Ver `ROADMAP.md`.

## Backups

```bash
scripts/backup.sh                         # pg_dump comprimido en ./backups
scripts/restore.sh backups/<archivo>.dump # restaura en una base aparte para validarlo
```
Recomendado: backup diario por cron, retención 14 diarios + 8 semanales, copia fuera del servidor y **restore de prueba mensual**. Un backup nunca restaurado no es una estrategia.

## Troubleshooting

| Síntoma | Causa probable |
|---|---|
| El backend no arranca: `database_url Field required` | Falta `.env` o la variable |
| `GET /api/v1/health` → 503 `db: error` | PostgreSQL apagado o `DATABASE_URL` incorrecta |
| Login responde 403 `csrf_header_missing` | Cliente HTTP sin header `X-Requested-With` (el frontend lo agrega) |
| Login responde 429 | Demasiados intentos fallidos; esperar 5 minutos |
| El frontend devuelve 500 en `/api/*` | Backend caído o `API_INTERNAL_URL` distinta a la usada al compilar |
| La cookie no se guarda en local | `COOKIE_SECURE=true` sin HTTPS; usar `false` en desarrollo |

## Documentación

[`CLAUDE.md`](CLAUDE.md) (guía operativa) · [`PLAN.md`](PLAN.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`DATA_MODEL.md`](DATA_MODEL.md) · [`METRICS.md`](METRICS.md) · [`ROADMAP.md`](ROADMAP.md) · [`CHANGELOG.md`](CHANGELOG.md) · [`docs/`](docs)
