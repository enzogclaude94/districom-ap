# ADR 013 — Servidor propio en la distribuidora, acceso LAN y remoto por Tailscale, backups continuos

- Estado: Propuesto · 2026-10-29 (S1 del hilo «Districom Servidor»; arquitectura aprobada por Enzo en S0 v2, decisiones D1–D9)
- Amplía: ADR 001 (PostgreSQL única base), ADR 002 (monolito modular), ADR 004 (sesiones server‑side) y ADR 006 (plataforma modular). No los reemplaza.
- Alcance: cómo se instala, expone, respalda, actualiza y opera DistriCom en un servidor físico de la empresa. No cambia reglas de negocio, endpoints, pantallas ni migraciones de ningún módulo.

## Contexto

DistriCom (Comercial + Rendiciones) es una sola aplicación: un backend FastAPI, un frontend Next.js y una base PostgreSQL con un schema por módulo. Tiene que correr en un servidor dentro de la distribuidora, accesible desde las PCs de la red local y, por Internet, desde notebooks autorizadas. Los datos productivos viven solo en ese servidor. No se expone nada a Internet.

## Decisiones

1. **Una sola instalación** con Docker Compose: `docker-compose.yml` (desarrollo y CI, sin cambios) + `deploy/docker-compose.prod.yml` (override de producción). LAN y remoto usan la misma base y las mismas funcionalidades.
2. **Caddy es la única puerta.** Es el único servicio con puertos publicados (80 redirige, 443 sirve). Backend, frontend y PostgreSQL no publican nada. Caddy envía `/api/*` directo al backend y el resto al frontend; el rewrite de Next.js queda para desarrollo y CI. No sirve `/api/docs` ni el OpenAPI. Responde solo al nombre configurado: otro `Host`, una IP o un intento de *DNS rebinding* no reciben respuesta.
3. **Un nombre, un origen, un certificado.** `DISTRICOM_HOST` es un subdominio de un dominio propio, nunca `.local` (reservado para mDNS). El certificado es público, de Let's Encrypt por desafío DNS‑01 (módulo `caddy-dns/cloudflare`), sin abrir puertos. Modo `internal` (CA propia de Caddy) para pruebas. Como el origen es el mismo desde la LAN y desde Tailscale, la cookie `Secure; HttpOnly; SameSite=Lax`, el CSRF (`SameSite` + `X-Requested-With`) y la sesión funcionan igual en los dos caminos.
4. **IP real.** Uvicorn confía en `X-Forwarded-For` solo si viene de la IP fija de Caddy en la red interna (`FORWARDED_ALLOW_IPS`). Caddy descarta el `X-Forwarded-For` que manda un cliente. Las sesiones y el límite de intentos de login ven la IP del cliente; una del rango `100.64.0.0/10` es un acceso remoto.
5. **Acceso remoto por Tailscale** (plan Standard, cuentas de la empresa con MFA, aprobación de usuarios y dispositivos). La política está versionada en `deploy/tailscale/policy.hujson`:
   - los usuarios remotos llegan solo al servidor, puertos 443 y 53;
   - IT además al 22;
   - sin subnet router, sin exit node y sin Funnel.

   Tailscale abre el camino de red y nada más: el login, los roles y las áreas de DistriCom deciden todo, igual que en la LAN.
6. **Firewall.** Docker publica puertos salteando `ufw`, así que el filtro de 80/443 vive en la cadena `DOCKER-USER` (`deploy/host/firewall.sh`): 80 y 443 desde la subred LAN (privada; opcionalmente también por interfaz), 443 desde `tailscale0`, solo hacia puertos publicados (nada directo a la IP de un contenedor), el resto se descarta. Se aplica antes de que arranque Docker, que conserva la cadena. Caddy se publica solo en IPv4; en IPv6 la misma cadena descarta toda conexión nueva hacia contenedores y `ufw` (IPv4 e IPv6) cubre el proxy de Docker, SSH y DNS en el host. El router no reenvía ningún puerto. Caddy se publica en `0.0.0.0` y no en la IP de Tailscale porque esa IP puede no existir todavía cuando arranca Docker; si el bind fallara, dejaría sin servicio también a la LAN.
7. **DNS.** En la LAN, un registro local en el router (o `hosts`) apunta a la IP LAN. Para el remoto se prefiere el *split DNS* de Tailscale hacia el CoreDNS opcional del servidor (perfil `dns`), que responde la IP de Tailscale solo por esa interfaz. Se evita publicar una IP 100.x en DNS público porque los resolvers con protección contra *DNS rebinding* pueden descartarla y porque expone el nombre.
8. **Backups con pgBackRest** (imagen `deploy/postgres`, PostgreSQL 17 + pgBackRest):
   - `repo1`, disco de backup del servidor: completo semanal, diferencial diario, PITR de unas 2 semanas.
   - `repo2`, S3 compatible fuera del local: retención por tiempo de 90 días.
   - Ambos cifrados (AES‑256).
   - WAL continuo y asíncrono con `archive_timeout = 60 s`. RPO de los datos transaccionales: 1 a 5 minutos aun con la pérdida total del local, si hay Internet.
   - `archive-push-queue-max` evita llenar el disco si un repositorio queda inalcanzable mucho tiempo; el WAL descartado se detecta en el registro de pgBackRest y el monitor lo reporta como hueco hasta un backup completo nuevo en cada repositorio.
   - Los backups se escriben solo en el disco de backup montado y preparado (`deploy/bin/backup-disk-init`, marca `.districom-backup-disk`); sin él, los scripts se niegan y el monitor falla. PostgreSQL no arranca sin el disco en ningún arranque (punto de entrada de la imagen, también con la política de reinicio de Docker). Si el disco se pierde con la base funcionando, la base sigue y el monitor da una alerta crítica con el estado de cada repositorio y del RPO (decisión de Enzo, 2026-10-30).
   - Los simulacros comparan producción y copia en el mismo punto de restauración, fila por fila de cada tabla (`deploy/sql/verify.sql`), con `pg_amcheck`; si la copia no llega al punto, el resultado es INCOMPLETE.
   - Las credenciales y passphrases viven en `deploy/pgbackrest.env`, fuera de Git y solo en el contenedor de la base.
9. **Archivos fuera de la base** (importaciones de Comercial y dumps lógicos nocturnos): `files-sync` (rclone) los copia **cada 5 minutos** al disco de backup y, cifrados con rclone crypt, fuera del local. Usa `copy --immutable`, nunca `sync`. RPO de esos archivos ≤ 10 minutos.
10. **Migraciones explícitas en producción.** El backend de producción no corre `alembic upgrade head` al arrancar. `deploy/bin/update` detiene la app, hace un backup completo etiquetado, migra, levanta y prueba. `deploy/bin/rollback [--restore-db]` vuelve a las imágenes y al código anteriores y, si hace falta, a la base del momento del backup previo; con `--restore-db` primero informa qué tablas cambiaron desde ese backup, exige la etiqueta del backup como confirmación y guarda lo que descarta en un backup *pre-rollback*.
11. **Imágenes construidas en el servidor** desde una versión de Git (tag), con tags locales `current` y `rollback`. Sin registro de imágenes ni secretos de Docker Hub.
12. **Operación por timers de systemd** (backups, dump, files-sync, monitor). El monitor alerta por un comando configurable (`ALERT_COMMAND`).

## Alternativas descartadas

- **Exponer 443 a Internet** (port‑forward): superficie de ataque pública sobre todo el stack.
- **Cloudflare Tunnel + Access** como primera opción: Cloudflare termina TLS y ve el tráfico en claro, y un error de política deja la app pública. Queda como opción futura para equipos sin cliente VPN.
- **IP de Tailscale en DNS público:** riesgo de bloqueo por protección contra rebinding y exposición del nombre.
- **Bases separadas por módulo:** dos backups no consistentes entre sí, identidades duplicadas (ADR 001/006).
- **Migración automática al arrancar en producción:** cambia la base sin backup previo ni ventana.

## Consecuencias

- Desarrollo y CI no cambian (`docker-compose.yml`, Dockerfiles y workflows intactos).
- El servidor necesita un dominio con DNS en Cloudflare, una cuenta de Tailscale Standard y un bucket S3 compatible antes de S3. Ninguno se contrata en S1.
- Comercial guarda archivos importados solo bajo `UPLOAD_DIR` (volumen `uploads`).
- El límite de intentos de login sigue en memoria (un proceso); con más de un proceso habría que moverlo a la base.
- Operación documentada en `docs/OPERATIONS.md`; requisitos de hardware en `docs/SERVER_REQUIREMENTS.md`.
