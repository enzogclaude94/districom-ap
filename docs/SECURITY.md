# Seguridad

Línea base de Fase 0 (detalle en `ARCHITECTURE.md §8` y ADR 004).

| Control | Implementación |
|---|---|
| Autenticación | Usuarios locales, Argon2id, mínimo 12 caracteres |
| Sesiones | Token 256 bits en cookie `HttpOnly; SameSite=Lax; Secure` (prod); solo el hash en DB; expiración por inactividad y absoluta; logout revoca |
| Autorización | Roles `admin` / `viewer` con `require_role`; RBAC ampliable |
| Enumeración de cuentas | Mismo mensaje y verificación de hash simulada para email inexistente |
| Fuerza bruta | Límite de intentos por IP+email (en memoria, instancia única) |
| CSRF | `SameSite=Lax` + header `X-Requested-With` obligatorio en métodos mutantes de `/api` |
| CORS | Producción mismo origen (rewrite de Next.js); CORS solo para orígenes de desarrollo explícitos |
| Validación | Pydantic estricto en todos los endpoints; errores de validación no devuelven los valores enviados |
| Logs | JSON estructurado con `request_id`; sin cuerpos, query strings, credenciales ni PII |
| Secretos | Solo por variables de entorno; `.env` ignorado por Git; la app no arranca sin `SECRET_KEY` ≥ 32 caracteres |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` |
| Contenedores | Procesos sin root; puertos publicados solo en `127.0.0.1` |
| Redirecciones | Login solo redirige a rutas relativas del mismo sitio |

## Pendiente (fases siguientes)
- Archivos subidos (Fase 1): límite de tamaño, extensión + MIME + firma, nombre sanitizado, sin macros ni fórmulas.
- Rate limit compartido si el backend se escala a más de una instancia.
- TLS terminado en el reverse proxy (Caddy) al desplegar.
- Auditoría de dependencias en CI (`pip-audit`, `pnpm audit`).

## Reporte
Problemas de seguridad: avisar a Enzo directamente, no en issues públicos.
