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

## R1‑3 (Rendiciones: pantalla Cajas)
- `frontend/tests/cash.test.tsx`: formato de dinero y lectura de importes argentinos, id de solicitud sin `crypto.randomUUID`, mensajes de cada error de negocio sin texto técnico; tablero con saldos del backend y sin cuentas técnicas, loading/error/reintento, movimientos (autor, signo, origen, revertida, detalle, paginación), acciones visibles por rol, traspaso con id y fecha y recarga de saldos, reintento tras error de red con el mismo id y un id nuevo para la siguiente operación, doble envío bloqueado, conflicto que empieza una operación nueva, validación local, ajuste con signo y motivo, cuenta de socio duplicada; detalle con patas y cuentas técnicas, revertida con vínculos, reverso con confirmación y motivo, ya revertida, inexistente.
- Operaciones sin confirmar (`cash.test.tsx`): cerrar y reabrir el formulario reintenta con el mismo id y el mismo cuerpo, el tablero las lista, descartar exige confirmación y después usa un id nuevo, no se mezclan operaciones ni usuarios, lo guardado se limita al pedido, una respuesta definitiva lo borra, el reverso pendiente queda atado a su operación.
- `dialog.test.tsx`: título accesible, foco inicial, Tab y Shift+Tab atrapados, foco que escapa vuelve, Escape y botón cerrar devuelven el foco al disparador. `settlements.test.tsx`: rol efectivo del módulo activo en la barra superior y rol de plataforma fuera de los módulos.
- `frontend/e2e/cash.spec.ts` (desktop): admin crea una cuenta de socio y carga saldo inicial → operador traspasa y deposita → admin registra aporte y ajuste y revierte el traspaso → saldos finales correctos → historial con original y reverso → el lector no ve acciones y la API le responde 403. Usa saldos relativos, así que se puede repetir sobre la misma base. Móvil: tablero legible y formulario dentro de la pantalla, sin scroll horizontal.
- `test_cash_api.py`: los movimientos incluyen el nombre del autor.

## R1‑2 (Rendiciones: API de cajas)
- `tests/modules/settlements/test_cash_api.py`: lectura de cuentas, saldos a una fecha, movimientos (paginación, fechas, rango inválido) y detalle; 401 sin sesión y 403 sin rol; matriz de permisos por operación (`viewer`, `operator`, `admin`); saldos tras traspasos, depósitos y extracciones; aportes y devoluciones de socios; ajustes con motivo y de ambos signos; reverso por HTTP (reintento, ya revertido, reverso de reverso, inexistente); idempotencia por HTTP (`201`, `200` con la misma respuesta, `409` con otro contenido o en otro endpoint); saldo insuficiente hoy y retroactivo; cuenta inactiva; validaciones (decimales, signo, campos extra, fecha futura, `client_request_id` obligatorio); cuentas de socio duplicadas por mayúsculas, acentos o espacios, contra cuentas del sistema, auditadas, con nombre validado.
- `test_migrations.py`: `0006` calcula `name_key` de las cuentas existentes.

## R1‑1 (Rendiciones: núcleo del ledger)
- `tests/modules/settlements/test_ledger_service.py`: saldos derivados de las entradas, patas y auditoría, validaciones (suma cero, decimales, cuentas repetidas, fecha futura, cuenta inactiva), forma de patas por tipo, rol mínimo por tipo, ajustes con motivo y reversibles, cajas y Banco sin negativo, también históricamente (el ejemplo de Enzo: −50.000 retroactivo rechazado; débito anterior al primer ingreso; día posterior protegido; saldo de cierre por día; reverso retroactivo), cuentas de socios con negativo, idempotencia (reintento, mismo id con otro contenido u otro usuario, reintento con saldos cambiados), reversos (saldo y rastro, una sola vez, solo admin, no se revierte un reverso, no deja cajas negativas), saldos a una fecha, movimientos paginados, sin commit no se escribe nada.
- `test_ledger_db_invariants.py`: con SQL directo, la base rechaza operaciones desbalanceadas o sin patas, patas agregadas después, UPDATE/DELETE, saldos negativos (también retroactivos y dos retiros en transacciones paralelas), caja con negativo habilitado, cuentas técnicas duplicadas, ajustes sin motivo, reversos sin vínculo o duplicados, borrado de cuentas con movimientos.
- `test_ledger_concurrency.py`: dos retiros simultáneos de la misma caja (el segundo espera el bloqueo y recibe `insufficient_funds`, o ambos pasan si alcanza), el mismo `client_request_id` en vuelo dos veces (devuelve la operación o `idempotency_conflict`), traspasos opuestos simultáneos sin deadlock.
- `test_ledger_single_writer.py`: ningún archivo de `app/` fuera del servicio importa los modelos del ledger, construye o escribe sus filas, ni tiene SQL sobre sus tablas; y la guardia detecta esos patrones.
- `tests/unit/test_ledger_rules.py`: huella estable (independiente del orden de patas y del formato del importe, sensible a cada campo) y forma de patas por tipo.

## R0 (Rendiciones: habilitación)
- `test_employees_api.py`: alta, edición auditada con antes/después sin PII, búsqueda y filtros, asignación y cierre de funciones con historia, solapamiento rechazado (servicio y base), baja lógica que cierra funciones, reactivación, permisos de plataforma.
- `test_employee_functions_registry.py`: registro idempotente y prefijo obligatorio del módulo.
- Frontend: formulario, lista y funciones de empleados. E2E: alta → función → baja (desktop y móvil).
- `test_module_access.py` / `test_module_roles.py`: admin de plataforma en todos los módulos, roles ordenados, permisos sin rol (403), grant/cambio/revocación auditados, CLI, restricciones de base, configuración visible por módulo.
- `test_migrations.py`: schema `settlements` creado y eliminado; la migración de roles conserva el acceso de los `viewer` a Comercial.
- Frontend: navegación de Rendiciones, cambio de módulo, bloqueo de rutas sin rol, selector. E2E: selector → Rendiciones (logo cargado) → Comercial; usuario solo-Comercial bloqueado en `/rendiciones` y 403 en la API.

## Fase 0
- `test_migrations.py`: upgrade → downgrade → upgrade desde base vacía, una sola head, `source_systems` sembrado.
- `test_health.py`: DB ok (200) y caída (503).
- `test_auth.py`: login, cookie HttpOnly/SameSite, token guardado hasheado, credenciales inválidas sin revelar existencia, usuario inactivo, 401/403, logout revoca, Argon2id, rate limit, CSRF, contraseña ausente de logs.
- `test_errors.py`: formato `{code, message, details, request_id}`, sin eco de datos enviados.
- `test_settings.py`: variables obligatorias, secret corto, CORS, driver.
- Frontend: login (validación, header CSRF, error del backend, redirect seguro), EmptyState/ErrorState, cliente HTTP ante fallas de red.
- E2E: redirección sin sesión, error de credenciales, login → configuración → inicio → logout (desktop y móvil).
