# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Cada entrada explica qué cambió realmente.

## [Unreleased]

### Changed — Plataforma modular (ADR 006)
- **Documentación:** DistriCom pasa a describirse como plataforma modular (núcleo compartido + módulos) con Inteligencia Comercial como único módulo en desarrollo. ADR 006; `ARCHITECTURE.md` v0.2 (estructura `platform/` + `modules/`, regla de dependencias, excepción analítica acotada al módulo, patrón para agregar módulos, acceso por módulo en etapas); `CLAUDE.md`, `ROADMAP.md` (carril Plataforma), README.
- **Backend reorganizado:** `auth`, `audit`, `settings_store` (ahora `settings`) y `sources` pasan a `app/platform/`; `app/modules/` aloja los módulos. Sin cambios de comportamiento, tablas ni endpoints.
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
