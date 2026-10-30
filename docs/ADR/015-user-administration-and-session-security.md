# ADR 015 — Administración de usuarios, contraseñas, sesiones y verificación de `Origin`

- Estado: Propuesto · 2026-10-30 (S2 del hilo «Districom Servidor», aprobada por Enzo el 2026-10-30)
- Amplía: ADR 004 (sesiones server‑side), ADR 006 (plataforma modular), ADR 007 (roles por módulo), ADR 010 (áreas protegidas) y ADR 013 (servidor, IP real). No los reemplaza.
- Alcance: plataforma (`app/platform/auth`, `app/core/middleware.py`) y sus pantallas. No cambia reglas de negocio, el ledger ni ningún módulo.

## Contexto

Hasta S1 los usuarios, sus contraseñas y sus permisos se administraban solo por CLI en el servidor, y nadie podía cambiar su propia contraseña ni ver o cerrar sus sesiones. Con el servidor en la distribuidora y acceso remoto por Tailscale, la administración tiene que hacerse desde la app, sin abrir caminos nuevos para escalar privilegios.

## Decisiones

1. **Contraseñas.** Argon2id, mínimo 12 y máximo 256 caracteres, no puede ser solo espacios ni el propio email. El usuario cambia la suya probando la actual; los fallos de la contraseña actual cuentan como intentos de login (mismo límite).
2. **Contraseña temporal.** La da un administrador (S2‑2) o la CLI (`reset-password`): la genera el servidor (16 caracteres al azar sin ambiguos, ~92 bits), se muestra **una sola vez** a quien la entrega y se guarda solo su hash; nunca va a la auditoría ni a un log. Vence a las `TEMPORARY_PASSWORD_HOURS` (72). Mientras `must_change_password`, el backend responde `403 password_change_required` a todo menos `/auth/me`, `/auth/password` y `/auth/logout`: el bloqueo está en la dependencia `current_user`, así que un endpoint nuevo queda cerrado por defecto. `/auth/me` devuelve módulos y áreas vacíos.
3. **Revocación de sesiones (política).**

   | Evento | Sesiones que se cierran |
   |---|---|
   | Cambio de la propia contraseña | todas las otras del usuario; la actual sigue |
   | Contraseña temporal (reseteo) | todas las del usuario |
   | Desactivación (S2‑2) | todas las del usuario (además, un usuario inactivo ya no resuelve sesión) |
   | Cambio de roles o áreas (S2‑2) | ninguna: los permisos se leen de la base en cada request |
   | El usuario cierra una sesión o «las otras» | esas |

   Cada sesión cerrada guarda `revoked_reason` y `revoked_by`. Las reglas de R9‑5‑1 (validación con `last_seen_at` guardado, `UPDATE` condicional) no cambian.
4. **IP real y origen, solo auditoría.** La IP es la que resuelve uvicorn (detrás de Caddy, desde `X-Forwarded-For` solo si viene de Caddy, ADR 013). Con ella se clasifica `access_origin`: `local` (el propio servidor), `tailscale` (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`), `lan` (cualquier dirección privada) u `other`. Se guarda en la sesión y en los eventos `LOGIN` y `platform.login.failed`, y se muestra al usuario. **Nunca autoriza ni deniega nada**: un acceso desde la LAN o desde el servidor necesita la misma sesión y los mismos roles que uno remoto (hay tests que lo verifican).
5. **Verificación de `Origin`** en todo método mutante de `/api`, sumada al header `X-Requested-With` (no lo reemplaza). Con `Origin`, tiene que ser el origen propio de la request (esquema, por `X-Forwarded-Proto` de Caddy, y `Host`) o uno de `TRUSTED_ORIGINS` / `CORS_ORIGINS`; `Origin: null` o cualquier otro, `403 origin_not_allowed`. Sin `Origin` (scripts, CLI) sigue, salvo que el navegador declare `Sec-Fetch-Site: cross-site` o `same-site`. En producción `TRUSTED_ORIGINS` queda vacío (todo pasa por Caddy con el mismo `Host`). El servidor de Next.js reenvía `/api/*` con el `Host` del backend, así que en desarrollo su origen (`http://localhost:3000`) es el valor por defecto si la variable no está definida, y el E2E la define para su puerto.
6. **Auditoría.** Eventos en `audit_events`, sin contraseñas, emails tipeados ni otra PII: `LOGIN` y `platform.login.failed` (con motivo, IP y origen; el id de la cuenta si existe, nunca el email tipeado), `platform.password.changed`, `platform.password.reset` (quién, a quién, cuántas sesiones, vencimiento), `platform.session.revoked` y `platform.sessions.revoked`. S2‑2 suma las acciones del panel de usuarios.
7. **Entrega en dos PRs.** S2‑1: lo anterior más «Mi cuenta» (contraseña y sesiones propias) y la migración `0032`. S2‑2: el panel de administración de usuarios (alta, edición, activación, reseteo, roles por módulo y áreas, sesiones de otros), con protección del último administrador activo y contra el escalamiento de privilegios.

## Consecuencias

- Un administrador ya no necesita la CLI para el día a día; la CLI queda para recuperar el acceso cuando nadie puede administrar (`reset-password`).
- Quien usa DistriCom detrás de un proxy propio que cambie el `Host` tiene que declarar su origen en `TRUSTED_ORIGINS`, o los cambios se rechazan.
- La IP y el origen pueden faltar o ser los de un proxy mal configurado; por eso son informativos.
