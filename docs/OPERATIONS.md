# Operación del servidor DistriCom

Runbook del servidor propio de la distribuidora (ADR 013). Todo comando se corre en el servidor, como root (`sudo`), desde el clon del repositorio (recomendado: `/opt/districom`). Los scripts están en `deploy/bin/` y `deploy/host/`; cada uno explica su uso en su encabezado.

> **Estado (S1):** infraestructura lista y probada en un entorno de prueba con datos ficticios. Dominio, proveedor de identidad, bucket externo y hardware siguen **pendientes**; lo que depende de ellos está marcado **[S3]**.

## 1. Piezas

| Pieza | Dónde | Qué hace |
|---|---|---|
| Caddy | `deploy/caddy/` | Única puerta: HTTPS en 443 (80 redirige). `/api/*` → backend, resto → frontend. |
| Backend, frontend | `backend/`, `frontend/` | Las mismas imágenes de siempre; en producción el backend no migra al arrancar. |
| PostgreSQL + pgBackRest | `deploy/postgres/` | Base única; WAL continuo a `repo1` (disco de backup) y `repo2` (fuera del local). |
| files-sync | `deploy/rclone/` | Cada 5 min copia archivos importados y dumps al disco de backup y, cifrados, fuera del local. |
| CoreDNS (opcional) | `deploy/coredns/` | Split DNS: el mismo nombre responde la IP LAN o la de Tailscale según la interfaz. |
| Tailscale | host | Acceso remoto. Política en `deploy/tailscale/policy.hujson`. |
| Firewall | `deploy/host/firewall.sh`, `ufw` | 80/443 solo desde la LAN y Tailscale; SSH y DNS solo desde la LAN y Tailscale. |
| Timers | `deploy/systemd/` | Backups, dump, files-sync, monitor. |

Archivos de configuración del servidor (fuera de Git; `deploy/bin/init-env` los crea desde sus `*.example`):

| Archivo | Lo lee | Contiene |
|---|---|---|
| `.env` | Compose, backend, scripts | Nombre, red, rutas, contraseña de la base, `SECRET_KEY` |
| `deploy/pgbackrest.env` | Solo el contenedor `db` | Passphrases de cifrado de backups, credenciales del bucket |
| `deploy/offsite.env` | Solo `files-sync` | Bucket y claves de rclone crypt para archivos |
| `deploy/caddy.env` | Solo Caddy | Token de la API de DNS (Let's Encrypt DNS‑01) |

**Resguardo obligatorio fuera del servidor** (gestor de contraseñas de la empresa + sobre cerrado en otro lugar físico):

- `deploy/pgbackrest.env` y `deploy/offsite.env` completos;
- el `.env`;
- el acceso de administrador a Tailscale, al proveedor de identidad, al DNS y al bucket.

Sin las passphrases **no se puede restaurar ningún backup**.

## 2. Instalación (una vez)

Requisitos de hardware y de red en `docs/SERVER_REQUIREMENTS.md`.

1. Instalar **Ubuntu Server 24.04 LTS** con los dos SSD en **RAID 1** (instalador de Ubuntu, software RAID). Montar el disco de backup en `/srv/districom-backup` (`/etc/fstab`).
2. `git clone https://github.com/enzogclaude94/districom-ap /opt/districom && cd /opt/districom && git checkout <tag>`
3. `deploy/bin/init-env`: crea `.env` y los archivos de secretos, con secretos generados. Completar los valores **PENDIENTE** de `.env`: `DISTRICOM_HOST`, `LAN_SUBNET`, `LAN_SERVER_IP` y `CADDY_TLS_MODE` (`cloudflare` en producción). **[S3]** Completar `deploy/caddy.env` y las líneas de `repo2` en `deploy/pgbackrest.env` y `deploy/offsite.env`.
4. `deploy/host/install.sh` (antes, `--dry-run` para ver qué hará): instala paquetes, Docker, Tailscale, `ufw`, el firewall de Docker y los timers.
5. **[S3]** Tailscale: `tailscale up --advertise-tags=tag:districom-server --auth-key=<clave de un solo uso>`. Anotar la IP 100.x en `.env` (`TAILSCALE_SERVER_IP`) si se usa el split DNS.
6. `deploy/bin/up`: construye las imágenes, levanta la base, crea el *stanza* de pgBackRest en los dos repositorios, migra, levanta la app, hace el primer backup completo y corre el smoke test.
7. Crear el primer administrador: `deploy/bin/dc exec backend python -m app.cli create-admin --email ... --name ...` (la contraseña se lee de stdin). Hasta S2, los roles por módulo se asignan con `grant-module-role` (ver `CLAUDE.md §2`).
8. **[S3]** DNS: registro local en el router (`DISTRICOM_HOST` → `LAN_SERVER_IP`) y split DNS en la consola de Tailscale (dominio → `TAILSCALE_SERVER_IP`, con `COMPOSE_PROFILES=dns` en `.env` y `deploy/bin/dc up -d dns`).
9. Verificar con `deploy/bin/smoke` y `deploy/bin/monitor`, y hacer un simulacro de restauración (§5).

## 3. Día a día

| Qué | Cómo |
|---|---|
| Estado | `deploy/bin/dc ps` · `deploy/bin/monitor` (también corre cada 5 min y alerta) |
| Logs | `deploy/bin/dc logs -f --tail=100 backend` (Caddy, backend, frontend, db) |
| Reiniciar la app | `deploy/bin/dc restart backend frontend` |
| Apagar el servidor | `deploy/bin/dc stop` y después `shutdown`; al volver la luz Docker levanta todo solo |
| Backups manuales | `deploy/bin/backup full|diff [nota]` |
| Estado de backups | `deploy/bin/dc exec -u postgres db pgbackrest --stanza=districom info` |

**Timers** (`systemctl list-timers 'districom*'`):

| Timer | Cuándo |
|---|---|
| Backup completo | Domingos 03:00 |
| Backup diferencial | Lunes a sábado 03:00 |
| Dump lógico | Todos los días 02:00 |
| files-sync | Cada 5 min |
| Monitor | Cada 5 min |

**Alertas:** `ALERT_COMMAND` en `.env` recibe el reporte por stdin (por ejemplo, un `curl` a un webhook de chat o `mail`). **[S3]** Definir el canal.

## 4. Backups y objetivos de recuperación

| Dato | Dónde se copia | Frecuencia | RPO |
|---|---|---|---|
| Base (todo lo transaccional) | WAL a `repo1` y `repo2` | continuo, segmento cerrado cada ≤ 60 s | **1–5 min** aun perdiendo el local (con Internet) |
| Base, puntos de partida | completo semanal + diferencial diario, en ambos repos | diario | — |
| Archivos importados (Comercial) | disco de backup + fuera del local (cifrado) | cada 5 min | **≤ 10 min** |
| Dumps lógicos | disco de backup + fuera del local | diario | formato secundario |

- **Retención:** `repo1`, 2 completos semanales con sus WAL (PITR de unas 2 semanas). `repo2`, 90 días (PITR a cualquier minuto de los últimos 90 días). Dumps locales, `DUMP_RETENTION_DAYS` (30).
- **Corte de Internet:**
  - la app y la LAN siguen funcionando;
  - el WAL espera en el servidor y se envía al volver Internet;
  - el monitor marca FAIL si hay WAL sin archivar hace más de 15 minutos;
  - si el corte supera la cola (`archive-push-queue-max`, 8 GiB), pgBackRest deja de encolar y hace falta un backup completo nuevo para cerrar el hueco (el monitor lo avisa).
- **Inmutabilidad [S3]:** el bucket externo debe impedir borrar o sobrescribir durante un período menor que la retención (30 días si la retención es de 90). Con Cloudflare R2:
  - R2 aplica sus reglas de bloqueo por prefijo y no versiona objetos, así que las reglas van solo sobre los prefijos que pgBackRest escribe una vez, `archive/districom/` y `backup/districom/20`, nunca sobre los `*.info`, que pgBackRest reescribe;
  - las credenciales del servidor solo con los permisos que pgBackRest necesita;
  - se verifica en S3 con un ensayo real (escritura, intento de borrado y restauración).

  Con AWS S3 o Backblaze B2, versionado + Object Lock en modo *governance* es el esquema nativo, y pgBackRest puede restaurar desde una versión anterior (`--repo-target-time`).

## 5. Simulacros de restauración

Nunca se restaura sobre producción para probar. `deploy/bin/restore-drill` restaura en un contenedor y una base descartables, saca la huella de la base (`deploy/sql/verify.sql`: versión de schema, filas por tabla, ledger asiento por asiento y saldos por cuenta) y la compara con producción.

| Simulacro | Comando | Frecuencia |
|---|---|---|
| Desde el disco de backup | `deploy/bin/restore-drill --repo 1` | Mensual |
| **Solo desde fuera del local** (no monta el disco de backup) | `deploy/bin/restore-drill --repo 2` | Trimestral, idealmente **en otra máquina** con solo el repo clonado y los archivos de secretos del resguardo |
| A un minuto dado (PITR) | `deploy/bin/restore-drill --repo 2 --target-time '2026-11-02 10:15:00-03' --keep` | Cuando se ensaye un error humano |

El resultado queda en `deploy/state/drills/` (`summary.txt`: duración y `IDENTICAL`/`DIFFERENT`). Anotar fecha, duración y resultado en el registro de simulacros.

**Recuperación real** (el servidor se perdió):

1. Equipo nuevo, o una VM temporal en la nube.
2. §2 pasos 1–4 con los archivos de secretos del resguardo.
3. `deploy/bin/dc up -d db`, y después restaurar sobre su volumen: `deploy/bin/dc stop db && deploy/bin/dc run --rm --no-deps -u postgres --entrypoint pgbackrest db --stanza=districom --repo=2 --delta restore` (con `--type=time --target=...` si se busca un minuto anterior).
4. `deploy/bin/up --no-build`.
5. Tailscale con la misma etiqueta: los remotos vuelven a entrar al mismo nombre.
6. Los archivos de Comercial: `rclone copy offsitecrypt:uploads` al volumen `uploads`.

## 6. Actualizar y volver atrás

1. La versión nueva se etiqueta en Git (`vX.Y.Z`) después de que su PR se mergea con CI verde.
2. Si es posible, primero en staging: restaurar un backup en otra máquina y actualizar ahí.
3. Avisar a los usuarios. Fuera de horario: `deploy/bin/update vX.Y.Z`. El orden es:
   1. chequeos;
   2. imágenes nuevas construidas sin cortar el servicio;
   3. app detenida;
   4. **backup completo etiquetado**;
   5. migraciones;
   6. arranque;
   7. smoke test.
4. Si algo falla:
   - `deploy/bin/rollback`: código e imágenes anteriores. Sirve cuando no hubo migración o era compatible.
   - `deploy/bin/rollback --restore-db`: además, la base vuelve al backup previo a la actualización. **Descarta lo cargado después de la actualización**: decidirlo y avisarlo antes.
5. Registro: `deploy/state/updates.log`.

## 7. Acceso remoto (Tailscale) **[S3]**

- **Alta de un usuario remoto:**
  1. Cuenta de la empresa con MFA.
  2. Agregarla a `group:districom-remote` en `deploy/tailscale/policy.hujson` y aplicar la política en la consola.
  3. La persona instala Tailscale en su notebook y entra con su cuenta.
  4. **Aprobar el dispositivo** en la consola.
  5. Tener su usuario de DistriCom con los roles que correspondan.
- **Baja** (inmediata, en las dos capas):
  1. Quitarla del grupo o del proveedor de identidad, y eliminar sus dispositivos en la consola.
  2. Desactivar su usuario de DistriCom, lo que cierra sus sesiones (pantalla de usuarios en S2; hasta entonces, por base).
- **Notebook perdida:** eliminar el dispositivo en la consola de Tailscale y cerrar las sesiones del usuario en DistriCom.
- **Nunca** activar Funnel, exit node ni subnet router; nunca reenviar puertos en el router.

## 8. Pruebas de la infraestructura

`deploy/tests/run-s1.sh` levanta un stack descartable con datos ficticios (generador de `backend/perf`) y prueba:

- **T0:** validación estática.
- **T1:** primer arranque.
- **T2:** exposición de puertos y nombres.
- **T3/T4:** HTTPS, cookies, CSRF e IP real.
- **T6:** RPO hacia fuera del local.
- **T7:** simulacros desde el disco de backup y desde fuera del local.
- **T8:** PITR.
- **T9:** corte de Internet.
- **T10:** archivos y dumps.
- **T11:** monitor.
- **T12:** actualización y reversión con una migración.
- **T13:** split DNS.
- **T14:** firewall.

Se niega a correr donde haya un `.env` real. `deploy/tests/run-s1.sh clean` lo desarma.
