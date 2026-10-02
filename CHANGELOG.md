# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Cada entrada explica qué cambió realmente.

## [Unreleased]

### Added
- **Backend (FastAPI):** configuración por variables de entorno que falla al arrancar si falta `DATABASE_URL` o `SECRET_KEY`; logging JSON con `request_id` por request; errores normalizados `{code, message, details, request_id}` que nunca devuelven los datos enviados; `GET /api/v1/health` que verifica la base (503 si está caída).
- **Base de datos:** SQLAlchemy 2 + Alembic. Migración `0001` con `users`, `user_sessions`, `app_settings`, `audit_events` y `source_systems` (AXUM, STORE_SYS, MANUAL, DEMO), más extensiones `citext` y `btree_gist`.
- **Autenticación:** usuarios locales con Argon2id; sesiones server-side (solo se guarda el hash del token) en cookie HttpOnly/SameSite=Lax; expiración por inactividad (8 h) y absoluta (7 días); límite de intentos de login; roles `admin` y `viewer`; protección CSRF por header; auditoría de logins; CLI `create-admin`.
- **Parámetros comerciales:** los 17 defaults de `METRICS.md §6` en código, con overrides en `app_settings`; `GET /api/v1/settings` (solo admin).
- **Frontend (Next.js 16):** tokens de diseño con colores funcionales, componentes base, layout con sidebar jerárquica (módulos futuros deshabilitados con su fase), login con redirección segura, logout, página Inicio con estado vacío y página Configuración con los parámetros.
- **Calidad:** 26 tests de backend contra PostgreSQL real, 13 tests de frontend, 6 E2E (desktop y móvil); ruff, mypy strict, eslint, prettier, tsc.
- **Infraestructura:** Docker Compose (db, backend, frontend), Dockerfiles sin root, CI en GitHub Actions (lint, tipos, tests, build, E2E y smoke test de Compose), scripts de backup y restore de prueba.
- **Documentación:** README completo, ADRs 001–005, `docs/TESTING.md`, `docs/SECURITY.md`, `docs/UX_GUIDELINES.md`.
- Documentos de planificación de Fase 0: guía operativa (`CLAUDE.md`), plan con riesgos y decisiones pendientes (`PLAN.md`), arquitectura (`ARCHITECTURE.md`), modelo de datos (`DATA_MODEL.md`), catálogo formal de métricas con IDs estables (`METRICS.md`) y roadmap de 12 fases (`ROADMAP.md`).
