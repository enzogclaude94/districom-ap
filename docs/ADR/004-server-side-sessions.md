# ADR 004 — Sesiones server-side en lugar de JWT

- Estado: Aceptado · 2026-10-02

## Contexto
Pocos usuarios internos, necesidad de revocar accesos de inmediato, sin clientes de terceros.

## Decisión
Token aleatorio de 256 bits en cookie `HttpOnly; SameSite=Lax` (`Secure` en producción). En la base se guarda solo su SHA-256. Expiración por inactividad (8 h) y absoluta (7 días). Next.js reenvía `/api/*` al backend, así el navegador ve un solo origen. CSRF: header `X-Requested-With` obligatorio en métodos mutantes.

## Consecuencias
- Logout y desactivación de usuarios efectivos al instante.
- Una consulta a la base por request autenticado (irrelevante a esta escala).
