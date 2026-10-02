# Testing

| Nivel | Herramienta | Dónde | Qué cubre |
|---|---|---|---|
| Unit backend | pytest | `backend/tests/unit/` | reglas puras: métricas, estados, scoring, normalización, configuración |
| Integración backend | pytest + PostgreSQL real | `backend/tests/integration/` | API + DB, migraciones, auth, imports (Fase 1) |
| Unit frontend | Vitest + Testing Library | `frontend/tests/` | componentes críticos, formularios, estados loading/empty/error |
| E2E | Playwright | `frontend/e2e/` | flujos críticos completos, desktop y móvil |

## Reglas
- Nunca SQLite: los tests de integración corren contra PostgreSQL (`TEST_DATABASE_URL`, base descartable que se migra desde cero en cada corrida).
- Reglas de negocio y métricas con TDD y fixtures verificables a mano (`METRICS.md §7`).
- Idempotencia de imports: test obligatorio desde Fase 1.
- Escenarios verificables a mano en `backend/tests/fixtures/` (por ejemplo `route_execution.py`: cumplimiento 66,7 % vs. cobertura 75 %). Son la especificación ejecutable de fases futuras; un test unitario verifica que sus valores esperados coinciden con las definiciones de `METRICS.md`, y la fase que implemente la métrica los usa tal cual.
- No se saltean ni desactivan tests para llegar a verde.

## Cómo correr
```bash
# Backend (requiere PostgreSQL; ver README)
cd backend && uv run pytest
# Frontend
cd frontend && pnpm test
# E2E (levanta backend y frontend; requiere un admin creado, ver README)
cd frontend && pnpm e2e
```

## Fase 0
- `test_migrations.py`: upgrade → downgrade → upgrade desde base vacía, una sola head, `source_systems` sembrado.
- `test_health.py`: DB ok (200) y caída (503).
- `test_auth.py`: login, cookie HttpOnly/SameSite, token guardado hasheado, credenciales inválidas sin revelar existencia, usuario inactivo, 401/403, logout revoca, Argon2id, rate limit, CSRF, contraseña ausente de logs.
- `test_errors.py`: formato `{code, message, details, request_id}`, sin eco de datos enviados.
- `test_settings.py`: variables obligatorias, secret corto, CORS, driver.
- Frontend: login (validación, header CSRF, error del backend, redirect seguro), EmptyState/ErrorState, cliente HTTP ante fallas de red.
- E2E: redirección sin sesión, error de credenciales, login → configuración → inicio → logout (desktop y móvil).
