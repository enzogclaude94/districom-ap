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
| Firewall | `deploy/host/firewall.sh`, `ufw` | 80/443 solo desde la LAN (subred privada, opcionalmente también por interfaz) y 443 desde Tailscale; nada directo a un contenedor; IPv6 cerrado (Caddy se publica solo en IPv4). Se aplica **antes** de que arranque Docker. SSH y DNS solo desde la LAN y Tailscale. |
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

1. Instalar **Ubuntu Server 24.04 LTS** con los dos SSD en **RAID 1** (instalador de Ubuntu, software RAID). Formatear el disco de backup y montarlo en `/srv/districom-backup` (`/etc/fstab`). `install.sh` se detiene si no está montado.
2. `git clone https://github.com/enzogclaude94/districom-ap /opt/districom && cd /opt/districom && git checkout <tag>`
3. `deploy/bin/init-env`: crea `.env` y los archivos de secretos, con secretos generados. Completar los valores **PENDIENTE** de `.env`: `DISTRICOM_HOST`, `LAN_SUBNET`, `LAN_SERVER_IP` y `CADDY_TLS_MODE` (`cloudflare` en producción). **[S3]** Completar `deploy/caddy.env` y las líneas de `repo2` en `deploy/pgbackrest.env` y `deploy/offsite.env`.
4. `deploy/host/install.sh` (antes, `--dry-run` para ver qué hará): instala paquetes, Docker, Tailscale, `ufw`, el firewall de Docker y los timers.
5. **[S3]** Tailscale: `tailscale up --advertise-tags=tag:districom-server --auth-key=<clave de un solo uso>`. Anotar la IP 100.x en `.env` (`TAILSCALE_SERVER_IP`) si se usa el split DNS.
6. `deploy/bin/backup-disk-init`: comprueba que el disco de backup esté montado, le escribe la marca `.districom-backup-disk` y crea sus carpetas. Sin esa marca, `up`, `backup`, `dump`, `files-sync`, `update`, `rollback --restore-db` y el simulacro desde `repo1` **se niegan a correr**, y el monitor da FAIL: si el disco no se montó (o se desmontó), nada escribe backups en el disco del sistema creyendo que es el de backup.
7. `deploy/bin/up`: construye las imágenes, levanta la base, crea el *stanza* de pgBackRest en los dos repositorios, migra, levanta la app, hace el primer backup completo y corre el smoke test.
8. Crear el primer administrador: `deploy/bin/dc exec backend python -m app.cli create-admin --email ... --name ...` (la contraseña se lee de stdin). Hasta S2, los roles por módulo se asignan con `grant-module-role` (ver `CLAUDE.md §2`).
9. **[S3]** DNS: registro local en el router (`DISTRICOM_HOST` → `LAN_SERVER_IP`) y split DNS en la consola de Tailscale (dominio → `TAILSCALE_SERVER_IP`, con `COMPOSE_PROFILES=dns` en `.env` y `deploy/bin/dc up -d dns`).
10. Verificar con `deploy/bin/smoke` y `deploy/bin/monitor`, y hacer un simulacro de restauración (§5).

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
  - si el corte supera la cola (`archive-push-queue-max`, 8 GiB), pgBackRest **descarta** ese WAL para no llenar el disco y PostgreSQL sigue funcionando. Medido en S1: el descarte vale para **los dos repositorios**, no solo el inalcanzable. Desde ese momento no se puede restaurar a un instante posterior hasta tener un backup completo nuevo, aunque `pgbackrest check` vuelva a pasar. El monitor y el smoke test leen el registro de pgBackRest y marcan **FAIL «WAL GAP»** hasta que exista un backup completo posterior en cada repositorio; el remedio es `deploy/bin/backup full "after WAL gap"` apenas vuelve la conexión. `deploy/bin/wal-gap` muestra el estado.
- **Inmutabilidad [S3]:** el bucket externo debe impedir borrar o sobrescribir durante un período menor que la retención (30 días si la retención es de 90). Con Cloudflare R2:
  - R2 aplica sus reglas de bloqueo por prefijo y no versiona objetos, así que las reglas van solo sobre los prefijos que pgBackRest escribe una vez, `archive/districom/` y `backup/districom/20`, nunca sobre los `*.info`, que pgBackRest reescribe;
  - las credenciales del servidor solo con los permisos que pgBackRest necesita;
  - se verifica en S3 con un ensayo real (escritura, intento de borrado y restauración).

  Con AWS S3 o Backblaze B2, versionado + Object Lock en modo *governance* es el esquema nativo, y pgBackRest puede restaurar desde una versión anterior (`--repo-target-time`).

## 5. Simulacros de restauración

Nunca se restaura sobre producción para probar. `deploy/bin/restore-drill` restaura en un contenedor y una base descartables y la compara con producción **en el mismo instante**:

1. bloquea las escrituras un momento (las lecturas siguen; con el dataset de prueba, menos de un segundo; `DRILL_LOCK_TIMEOUT`, 60 s, limita la espera detrás de una transacción larga);
2. saca la huella de producción y crea un punto de restauración con nombre;
3. restaura la copia exactamente hasta ese punto. Si falta WAL en el medio, PostgreSQL no llega y el simulacro dice **INCOMPLETE**: nunca informa una recuperación completa que no ocurrió;
4. saca la huella de la copia y la verifica con `pg_amcheck` (páginas de las tablas y cada índice contra su tabla).

La huella (`deploy/sql/verify.sql`) cubre **cada fila y cada columna de cada tabla** de todos los schemas de la aplicación (plataforma, Comercial, Rendiciones y cualquier módulo futuro): por tabla, cantidad de filas y un hash de todas sus filas, independiente del orden físico; además la versión de migración, la estructura (columnas, restricciones, índices, triggers, funciones y vistas) y el saldo de cada cuenta del ledger. Si una tabla difiere, `diff.txt` la nombra. Las secuencias quedan afuera a propósito: PostgreSQL las registra adelantadas y una copia restaurada puede estar unos valores más allá sin perder nada.

| Simulacro | Comando | Frecuencia |
|---|---|---|
| Desde el disco de backup | `deploy/bin/restore-drill --repo 1` | Mensual |
| **Solo desde fuera del local** (no monta el disco de backup) | `deploy/bin/restore-drill --repo 2` | Trimestral, idealmente **en otra máquina** con solo el repo clonado y los archivos de secretos del resguardo |
| A un minuto dado (PITR) | `deploy/bin/restore-drill --repo 2 --target-time '2026-11-02 10:15:00-03' --keep` | Cuando se ensaye un error humano |
| Al final de un backup dado (p. ej. el *pre-rollback*) | `deploy/bin/restore-drill --repo 1 --set <etiqueta> --keep` | Para mirar datos que descartó una reversión |

El resultado queda en `deploy/state/drills/` (`summary.txt`: duración, `amcheck` y `IDENTICAL` (0), `RESTORED` sin comparación (0), `DIFFERENT` (2) o `INCOMPLETE` (3)). Anotar fecha, duración y resultado en el registro de simulacros.

**Recuperación real** (el servidor se perdió):

1. Equipo nuevo, o una VM temporal en la nube.
2. §2 pasos 1–4 y 6 (disco de backup) con los archivos de secretos del resguardo.
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
   - `deploy/bin/rollback --restore-db`: además, la base vuelve al backup previo a la actualización. **Quita de la base todo lo cargado después de la actualización**, así que tiene tres protecciones:
     1. sin confirmación **solo informa**: compara la base actual con la huella tomada al hacer el backup y lista, tabla por tabla, qué cambió desde entonces (filas de antes y de ahora). No toca nada y termina con código 3;
     2. para ejecutarlo hay que escribir la **etiqueta del backup** que muestra el informe (en una terminal, cuando la pide; en un script, `--confirm <etiqueta>`). Otra cosa cancela;
     3. antes de restaurar hace un backup **pre-rollback** del estado que se descarta, y anota su etiqueta en `deploy/state/rollback-safety-label`: lo descartado se puede mirar o recuperar con `restore-drill --set`. Ese backup sigue la retención de cada repositorio: en `repo1` hasta que los backups completos siguientes lo vencen (una a dos semanas), en `repo2` 90 días. Después de una reversión de base, otra sobre la misma actualización se rechaza.
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
- **T1a:** sin el disco de backup preparado y montado, nada corre.
- **T7b:** la huella detecta un carácter, un microsegundo, una fila borrada o un centavo del ledger en cualquier tabla, y no cambia por reescribir tablas.
- **T14:** firewall con dos redes simuladas: la LAN entra, otra red no (y sin las reglas sí entraría), nadie llega directo a un contenedor.
- **T15:** corte largo que llena la cola de WAL: descarte detectado, simulacro INCOMPLETE, remedio con un backup completo.

Se niega a correr donde haya un `.env` real. `deploy/tests/run-s1.sh clean` lo desarma.
