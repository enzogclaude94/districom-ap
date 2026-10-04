# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Cada entrada explica qué cambió realmente.

## [Unreleased]

### Added — Roles por módulo y esqueleto de Rendiciones (R0, PR 3)
- **Roles por módulo (Etapa 1):** migración `0003` con `user_module_roles` (`viewer < operator < admin`, único por usuario y módulo); los `viewer` existentes conservan `viewer` en Comercial. El administrador de plataforma es `admin` en todos los módulos; los demás usuarios ven solo los módulos asignados. `require_module_access` admite `operator`. CLI `grant-module-role` / `revoke-module-role`, auditados con antes/después.
- **Módulo Rendiciones (`settlements`):** registrado en backend y frontend; migración `0004` crea el schema `settlements` vacío; `GET /api/v1/settlements/status` (rol del usuario, 403 sin acceso); parámetro `settlements.pending_alert_hours` (24); función de empleado `settlements.delivery_driver` (Repartidor). Alembic compara todos los schemas (`include_schemas`).
- **Frontend:** `/rendiciones` con el logo oficial (versión web recortada, fondo transparente, `frontend/public/brand/districom-rendiciones.png`) y navegación por fases R1–R8 deshabilitada; home de plataforma con selector de módulos (con un solo módulo redirige); menú para cambiar de módulo bajo el wordmark; pantalla "sin acceso" para rutas de módulos sin rol.
- **Tests:** acceso por rol y módulo, migración de roles, CLI, configuración por módulo, selector y bloqueo de rutas; E2E de selector, cambio de módulo y usuario sin acceso a Rendiciones.

### Changed
- La home `/` deja de redirigir siempre a Comercial: muestra los módulos del usuario cuando tiene más de uno.

### Added — Empleados en plataforma (R0, PR 2)
- **Base de datos:** migración `0002` (`platform: employees and employee functions`): `employees` (identidad única, baja lógica, IDs externos con unicidad parcial) y `employee_functions` (períodos con vigencia; exclusion constraint que impide solapar la misma función y CHECK de rango).
- **Backend:** paquete `app/platform/parties/` con servicio, API `/api/v1/employees` (listar con búsqueda, estado, función y paginación; alta; edición; baja lógica que cierra funciones vigentes; reactivación; asignar y cerrar funciones; códigos de función) solo para administradores de plataforma. Registro de funciones por módulo vía `ModuleSpec.employee_functions` (Comercial registra `commercial.salesperson`). `has_function()` para que los módulos validen funciones por fecha.
- **Auditoría:** helper `record_change` con la convención `{action, before, after}` (ADR 007); el teléfono nunca se audita, solo `phone_changed`.
- **Frontend:** pantallas **Empleados** (`/empleados`, `/empleados/nuevo`, `/empleados/[id]`) con historia de funciones, visibles en la navegación solo para administradores de plataforma.
- **Tests:** API, reglas e invariantes de base de empleados; registro de funciones; componentes de frontend; E2E de alta → función → baja.

### Added — Módulo Rendiciones aprobado (ADR 007)
- **Documentación:** ADR 007 registra el segundo módulo, **Rendiciones** (`settlements`, `/rendiciones`): empleados y roles por módulo adelantados a plataforma, clientes como texto + `customer_id` opcional hasta que exista el maestro, ledger propio (entradas inmutables, operaciones balanceadas e idempotentes, reversos, sin saldos editables), alertas internas de pendientes y logo oficial del módulo. `ARCHITECTURE.md` v0.3 (§4.6 ledger, roles Etapa 1, selector de módulos), `DATA_MODEL.md` v0.3 (`user_module_roles`, `employees` y `employee_functions` en R0, §13 modelo conceptual de Rendiciones), `ROADMAP.md` v0.4 (carril Rendiciones R0–R9, PL‑1 en DONE, PL‑2 dividido), `CLAUDE.md`, `PLAN.md §13`, README.
- **Marca:** logo oficial de DistriCom Rendiciones en `docs/brand/districom-rendiciones-original.png` y sus reglas de uso en `docs/BRAND.md`.

### Changed — Plataforma modular (ADR 006)
- **Documentación:** DistriCom pasa a describirse como plataforma modular (núcleo compartido + módulos) con Inteligencia Comercial como único módulo en desarrollo. ADR 006; `ARCHITECTURE.md` v0.2 (estructura `platform/` + `modules/`, regla de dependencias, excepción analítica acotada al módulo, patrón para agregar módulos, acceso por módulo en etapas); `CLAUDE.md`, `ROADMAP.md` (carril Plataforma), README.
- **Backend reorganizado:** `auth`, `audit`, `settings_store` (ahora `settings`) y `sources` pasan a `app/platform/`; `app/modules/` aloja los módulos. Sin cambios de comportamiento, tablas ni endpoints.
- **Configuración por scope:** parámetros con claves `platform.*` (moneda, inicio de semana) y `commercial.*` (los 14 de `METRICS.md §6`), cada scope con defaults y etiquetas junto a su dueño; `GET /api/v1/settings` devuelve secciones según los permisos del usuario y la pantalla Configuración muestra "General" e "Inteligencia Comercial". La zona horaria de negocio queda solo como variable de entorno `BUSINESS_TIMEZONE` (ya no figura como parámetro).
- **Acceso por módulo (Etapa 0):** `require_module_access(módulo, rol mínimo)` y `GET /auth/me` con `modules: [{key, name, role}]`, derivados del rol de plataforma; el módulo comercial se monta en `/api/v1/commercial` (sin endpoints todavía).
- **Frontend modular:** Inteligencia Comercial vive bajo `/comercial` (Inicio y la navegación del módulo); `/` redirige al módulo; la sidebar muestra el nombre del módulo, su navegación y las páginas de plataforma (Configuración); la sección "Comercial" pasa a llamarse "Equipo y clientes"; título de la app y login dicen "DistriCom".
- **Límites verificados en CI:** `import-linter` (capas api → modules → platform → db → core e independencia entre módulos) y reglas `no-restricted-imports` de ESLint en el frontend.
- **Modelo de datos (`DATA_MODEL.md` v0.2, aún no implementado):** ownership por plataforma/módulo y schema `commercial`; `employees` + `employee_functions` (identidad única y funciones con historia) reemplazan a `salespeople`; identidad de `customers` en plataforma con `commercial.customer_profiles` para segmentación y derivados; rutas documentadas como rutas de preventa; catálogo `suppliers` diferido; tipos de import y de mapping extensibles por registro en lugar de CHECK.

### Added
- `docs/DATA_INTAKE.md`: exportaciones reales que hacen falta de AXUM y STORE SYS, período recomendado y qué se valida en cada archivo, como entrada a la Fase 1.

### Changed
- `PLAN.md` y `ROADMAP.md`: Fase 0 en DONE; Fase 1 en preparación (análisis de datos reales, sin implementación).

## [0.1.0] — 2026-10-02 · Fase 0

### Added
- **Marca DistriCom:** paleta oficial como design tokens, Manrope SemiBold para marca y títulos, Inter para la interfaz, wordmark vectorial (color, negativo, monocromo), ícono DC, favicon y app icons, guía `docs/BRAND.md`.
- **Métricas:** `route.schedule_compliance` (cumplimiento de visitas programadas, por visita cliente‑día) separada de `route.coverage` (cobertura de cartera, por cliente único), con drill‑downs distintos y escenarios verificables a mano para la Fase 3.
- **Arquitectura:** excepción documentada para que `analytics/` lea el modelo canónico directamente (solo lectura); importación dividida en lectores por formato y adaptadores por sistema, para sumar `.xls` o APIs sin tocar la lógica.
- **Backend (FastAPI):** configuración por variables de entorno que falla al arrancar si falta `DATABASE_URL` o `SECRET_KEY`; logging JSON con `request_id` por request; errores normalizados `{code, message, details, request_id}` que nunca devuelven los datos enviados; `GET /api/v1/health` que verifica la base (503 si está caída).
- **Base de datos:** SQLAlchemy 2 + Alembic. Migración `0001` con `users`, `user_sessions`, `app_settings`, `audit_events` y `source_systems` (AXUM, STORE_SYS, MANUAL, DEMO), más extensiones `citext` y `btree_gist`.
- **Autenticación:** usuarios locales con Argon2id; sesiones server-side (solo se guarda el hash del token) en cookie HttpOnly/SameSite=Lax; expiración por inactividad (8 h) y absoluta (7 días); límite de intentos de login; roles `admin` y `viewer`; protección CSRF por header; auditoría de logins; CLI `create-admin`.
- **Parámetros comerciales:** los 17 defaults de `METRICS.md §6` en código, con overrides en `app_settings`; `GET /api/v1/settings` (solo admin).
- **Frontend (Next.js 16):** tokens de diseño con colores funcionales, componentes base, layout con sidebar jerárquica (módulos futuros deshabilitados con su fase), login con redirección segura, logout, página Inicio con estado vacío y página Configuración con los parámetros.
- **Calidad:** 29 tests de backend contra PostgreSQL real, 13 tests de frontend, 8 E2E (desktop y móvil); ruff, mypy strict, eslint, prettier, tsc.
- **Infraestructura:** Docker Compose (db, backend, frontend), Dockerfiles sin root, CI en GitHub Actions (lint, tipos, tests, build, E2E y smoke test de Compose), scripts de backup y restore de prueba.
- **Documentación:** README completo, ADRs 001–005, `docs/TESTING.md`, `docs/SECURITY.md`, `docs/UX_GUIDELINES.md`.
- Documentos de planificación de Fase 0: guía operativa (`CLAUDE.md`), plan con riesgos y decisiones pendientes (`PLAN.md`), arquitectura (`ARCHITECTURE.md`), modelo de datos (`DATA_MODEL.md`), catálogo formal de métricas con IDs estables (`METRICS.md`) y roadmap de 12 fases (`ROADMAP.md`).
