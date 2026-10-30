# Seguridad

Línea base de Fase 0 (detalle en `ARCHITECTURE.md §8` y ADR 004).

| Control | Implementación |
|---|---|
| Autenticación | Usuarios locales, Argon2id, mínimo 12 caracteres |
| Sesiones | Token 256 bits en cookie `HttpOnly; SameSite=Lax; Secure` (prod); solo el hash en DB; expiración por inactividad y absoluta; logout revoca. Cada request valida revocación, usuario activo, expiración absoluta e inactividad con el `last_seen_at` guardado y, solo si todo pasa, lo actualiza como mucho una vez cada `SESSION_TOUCH_SECONDS` (60) con un `UPDATE` condicional que repite todas esas condiciones, gana un único request concurrente y nunca revive una sesión; si no encuentra la fila, una relectura de solo lectura con el mismo instante decide: si otro request la tocó primero el request sigue, y si la sesión fue revocada, venció, quedó inactiva o el usuario se desactivó en el medio, se rechaza este mismo request (R9‑5‑1): la inactividad puede vencer hasta 60 s antes que con un registro exacto, nunca después |
| Autorización | Roles `admin` / `viewer` con `require_role`; RBAC ampliable |
| Enumeración de cuentas | Mismo mensaje y verificación de hash simulada para email inexistente |
| Fuerza bruta | Límite de intentos por IP+email (en memoria, instancia única); las claves cuyas fallas salieron de la ventana se descartan, así la memoria guarda solo las recientes (R9‑5‑1) |
| CSRF | `SameSite=Lax` + header `X-Requested-With` obligatorio en métodos mutantes de `/api` |
| CORS | Producción mismo origen (rewrite de Next.js); CORS solo para orígenes de desarrollo explícitos |
| Validación | Pydantic estricto en todos los endpoints; errores de validación no devuelven los valores enviados |
| Logs | JSON estructurado con `request_id`; sin cuerpos, query strings, credenciales ni PII |
| Secretos | Solo por variables de entorno; `.env` ignorado por Git; la app no arranca sin `SECRET_KEY` ≥ 32 caracteres. **`SECRET_KEY` hoy no es una protección activa de DistriCom:** no firma sesiones (son tokens opacos con su SHA‑256 en la base), no protege cookies ni firma los `preview_token` (son hashes del estado). Se mantiene por el contrato de entorno (decisión de Enzo, 2026-10-29); darle un uso real o sacarla es una decisión posterior al go‑live, con ADR |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` |
| Caché (R9‑5‑2) | Toda respuesta del backend lleva `Cache-Control: no-store` (JSON, CSV, errores 4xx/5xx, login/logout, health), puesto en el middleware de request y en el handler del 500 |
| Tamaño de request (R9‑5‑2) | Cuerpo máximo `REQUEST_MAX_BODY_BYTES` (1 MiB) en bytes reales; `multipart/form-data` usa `UPLOAD_MAX_SIZE_MB`. Con `Content-Length` mayor se responde 413 `request_too_large` sin leer el cuerpo; sin él (chunked) o si miente, se cuentan los bytes al recibirlos y se corta al pasar el límite |
| Timeouts de PostgreSQL (R9‑5‑2) | Solo en las conexiones del backend, como opciones de conexión (sin `ALTER ROLE` ni `ALTER DATABASE`): `statement_timeout` 30 s, `lock_timeout` 10 s, `idle_in_transaction_session_timeout` 60 s. Excederlos es 503 `database_timeout` / `database_busy` en el formato estándar, nunca un 500 opaco ni un error de regla. `migrate`, backups, restores, dumps y pgBackRest abren conexiones propias y conservan los valores del servidor |
| Contenedores | Procesos sin root; puertos publicados solo en `127.0.0.1` |
| Redirecciones | Login solo redirige a rutas relativas del mismo sitio |
| Datos de empleados | Administración solo para administradores de plataforma; el teléfono (PII) no se registra en auditoría ni en logs |
| Finanzas de empleados (R8, ADR 010) | Área protegida `settlements.employee_finance` con permiso explícito que el admin de plataforma no hereda; sin el área, 404 sin detalles y Cajas oculta cuentas `EMPLOYEE` y las técnicas de R8 y redacta operaciones R8; lo que mueve Caja o Banco (adelantos, préstamos, pagos de liquidación) exige además operator de Rendiciones, y revertir un pago, admin del área; logs sin identidad de empleado junto a importes |
| Integración Store Sys (R9‑1, ADR 011) | Área protegida `settlements.store_sys_import` con permiso explícito que el admin de plataforma no hereda; sin el área, 404. Importar el esperado exige operator del área y operator de Rendiciones; cambiar el código Store Sys de un empleado, solo admin del área, con motivo para corregirlo o quitarlo y auditado (antes, después, quién, cuándo); `EmployeeOut` de plataforma ya no expone el código. El contrato no recibe credenciales ni API keys (no hay conexión con Store Sys); los registros rechazados se loguean solo con su `sqlstate`, sin importes ni identidades |

| Cierre del día (R9‑2, ADR 012) | Solo lectura para cualquier rol de Rendiciones (viewer); sin acceso al módulo, 403. Sin `settlements.employee_finance`, los movimientos de empleado y sus reversos se agrupan como «Movimiento de empleado (reservado)» en la pantalla y en el CSV, sin empleado, tipo ni descripción, y suman igual en los totales |

## Pendiente (fases siguientes)
- Archivos subidos (Fase 1): límite de tamaño, extensión + MIME + firma, nombre sanitizado, sin macros ni fórmulas.
- Rate limit compartido si el backend se escala a más de una instancia.
- Despliegue (ADR 013, S1 hecho; se activa en S3): TLS en Caddy con certificado público por DNS‑01; acceso remoto solo por Tailscale (MFA, aprobación de dispositivos, solo 443 al servidor); nada publicado en Internet; filtro `DOCKER-USER` para 80/443 (LAN y Tailscale); IP real solo desde Caddy; backups cifrados dentro y fuera del local; secretos del servidor en archivos fuera de Git, cada uno leído solo por el servicio que lo usa.
- Administración de usuarios, contraseñas y sesiones desde la app (S2 del hilo Servidor); verificación de `Origin` en métodos mutantes.
- Auditoría de dependencias en CI (`pip-audit`, `pnpm audit`).

## Reporte
Problemas de seguridad: avisar a Enzo directamente, no en issues públicos.
