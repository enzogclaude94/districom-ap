# W2-C: operación de DistriCom en Windows (C1 base operativa, C2 servicios, C3 permisos, C4 firewall, C9‑A coordinación, C9‑B bloqueo de escrituras, C6 backups)

Primera subetapa de W2‑C (ADR 016, en estado **Propuesto**; plan en `docs/WINDOWS_DEPLOYMENT.md` y propuesta W2‑C revisión 1). Son las piezas que usan las subetapas siguientes. Esta carpeta **no** instala servicios, no toca el firewall ni bloquea escrituras: eso es C2 a C13.

Python con solo la biblioteca estándar (decisión D1). Reutiliza `districom_backup` (W2‑A) para las escrituras durables.

## Piezas (`src/districom_ops/`)

| Módulo | Qué hace |
|---|---|
| `layout` | Carpetas `releases\`, `config\`, `logs\` y `state\` bajo la raíz. Las crea si faltan; correrlo de nuevo no cambia nada. El enlace `current` es de C12 |
| `config` | Lee `config\districom.env` en forma literal. Rechaza variables faltantes o inseguras (bases fuera de `127.0.0.1`, contraseñas débiles o de ejemplo, el mismo rol para la app y la operación, una LAN pública) y nombra cada problema **sin mostrar el valor**. Los secretos nunca aparecen en un `repr` ni en un log |
| `logs` | Un JSON por línea en `logs\ops.log`. Enmascara los secretos registrados, las credenciales en URL, los pares `password=…`, los campos con nombre de secreto y los CUIT. De una excepción guarda el tipo y el mensaje enmascarado, nunca el traceback |
| `oplog` | Registro de operaciones `state\operations.jsonl`, solo agregado, con `fsync` en cada evento. Cada operación (cierre, actualización, rollback, manual) tiene un id, sus pasos y un único final: `completed`, `failed` o `reverted`. Sin final queda **incompleta**. La lectura es estricta y nunca modifica el archivo: una línea JSON válida con campos faltantes, tipos incorrectos, un evento o tipo desconocido, eventos de una operación que no empezó o posteriores a su final es **inválida** y deja la operación **incierta** (o todo el registro, si no nombra ninguna). La única línea tolerada es la última cortada por un corte, que el evento siguiente cierra con un `torn_line_sealed` que la nombra |
| `maintenance` | Ventana de mantenimiento `state\maintenance.json` con la **operación dueña**. Solo su dueña la cierra y no se abre una segunda. Al pasar `MAINTENANCE_MAX_MINUTES` da **FAIL** (alerta), pero **nunca se cierra sola ni habilita escrituras**. Un archivo ilegible cuenta como ventana abierta con dueña desconocida |
| `safety` | `writes_resume_check`: verificación conjunta y de solo lectura. Dice que sería seguro reactivar escrituras **solo si** la ventana está cerrada, el registro se lee y no tiene líneas inválidas, y no hay operaciones incompletas ni inciertas. Que falte `maintenance.json` no alcanza. C1 no desbloquea nada: **C9 debe consultar esta verificación antes de reactivar la aplicación** |
| `oplock` | (C9‑A) Bloqueo exclusivo entre operaciones, `state\operations.lock`. Es el bloqueo de archivos del sistema operativo: si el proceso muere, Windows lo libera, y nunca vence. Quien no lo consigue falla enseguida o tras una espera acotada, y nombra al dueño. `operations.lock.json` dice quién lo tiene (operación, proceso, hora), solo para los mensajes. Soltarlo nunca cambia la ventana, el registro ni el resto del estado persistente |
| `cli` | `districom-ops init`, `check-config`, `maintenance open\|close\|status`, `operations` y `resume-check` |

## C2: servicios nativos de Windows

| Servicio | Programa | Cuenta | Inicio | Lo arranca |
|---|---|---|---|---|
| `DistriComPostgreSQL` | PostgreSQL 17 (`pg_ctl register`) | `NT SERVICE\DistriComPostgreSQL` | Manual | Solo el Stack, después de verificar el disco |
| `DistriComBackend` | uvicorn (WinSW) | `NT SERVICE\DistriComBackend` | Manual | El Stack, cuando PostgreSQL acepta conexiones |
| `DistriComFrontend` | `node server.js` (WinSW); depende del backend | `NT SERVICE\DistriComFrontend` | Manual | El Stack, cuando el backend está vivo |
| `DistriComCaddy` | Caddy (WinSW); depende del frontend | `NT SERVICE\DistriComCaddy` | Manual | El Stack, cuando el frontend responde |
| `DistriComStack` | `districom-ops services supervise` (WinSW) | `NT SERVICE\DistriComStack` | Automático, diferido | Windows |

- **PostgreSQL no es dependencia de nada en el SCM, no tiene inicio automático ni reinicio por el SCM.** Así Windows nunca lo arranca sin la verificación del disco.
- El Stack verifica el disco y arranca la cadena en orden por disponibilidad.
- Vigila PostgreSQL: después de una caída vuelve a verificar el disco y lo reinicia, como máximo 3 veces por hora.
- Al detenerse, para todo en orden inverso. PostgreSQL se detiene cuando no quedan sesiones de la app.
- WinSW reinicia el backend, el frontend y Caddy 3 veces; después deja de intentar.
- Caddy escucha en 80 y 443. Si otro programa del servidor ya los usa, `HTTP_PORT` y `HTTPS_PORT` los cambian; hay que revisarlo en el equipo real (por ejemplo IIS o un servicio de Store Sys).
- El Stack tiene permiso solo para consultar, arrancar y detener esos cuatro servicios.
- **`services start` termina con la señal del supervisor de ese arranque.** Escribe `state\start-request.json` con un valor nuevo y el Stack lo copia en `state\supervisor.json` junto con su fase: `starting`, `running`, `failed`, `stopping` o `stopped`. `start` vuelve solo cuando ese registro dice `running`, que se escribe después del `service_ready` de Caddy, y Caddy responde con el certificado verificado.
  - Un registro de un arranque anterior nunca cuenta. Si el Stack ya estaba en marcha, cuenta solo el del servicio vivo (su proceso WinSW).
  - Falla si el Stack se detiene o informa `failed`, o si se cumple el tiempo máximo. No hay esperas fijas.

| Módulo | Qué hace |
|---|---|
| `artifacts` | Runtimes fijados por versión y checksum (`templates/versions.json`): WinSW 2.12.0, PostgreSQL 17.6, Caddy 2.10.2 y Node 22.23.4. Verifica el hash **antes** de extraer. Si no coincide, borra la descarga |
| `backup_disk` | Prepara y verifica `.districom-backup-disk`. El id, el GUID del volumen y que sea un volumen distinto del de sistema y del de datos tienen que coincidir. Una marca copiada a otro disco con la misma letra falla |
| `database` | Crea el clúster una sola vez: ICU `es-AR`, checksums, SCRAM, `127.0.0.1`, logs O7 en `logs\postgresql`, superusuario con contraseña aleatoria en `config\postgres.secret`. También crea el rol y la base de la app |
| `services` | Instala de forma idempotente, desinstala, arranca, detiene, informa el estado y supervisa |
| `winservice`, `acl` | SCM por `advapi32`, independiente del idioma; `acl` solo restringe el archivo temporal de `database init` |

```powershell
districom-ops runtime fetch
districom-ops backup-disk prepare --root D:\   # una vez; el id va a BACKUP_DISK_ID
districom-ops database init
districom-ops services install --release <versión>
districom-ops services start        # por DistriComStack; --direct (administrador) para mantenimiento
districom-ops services stop | status | uninstall
```

Las pruebas C2‑1 a C2‑7 (`services_tests/`) instalan servicios reales y corren solo en el runner descartable del job «Windows services (W2-C2)», como administrador.

**PENDIENTE, nunca PASS:**
- el arranque después de un reinicio real sin sesión;
- Windows 11 Pro;
- el disco desconectado físicamente (en CI se desconecta un VHDX);
- el número de serie físico del disco;
- la parada durante el apagado de Windows (C10 y C11).

**Logs de cada servicio:** desde C3 cada servicio WinSW escribe en `logs\services\<servicio>\`.

## C3: cuentas de servicio y permisos NTFS

`services install` termina aplicando la política; `permissions apply|verify` y `install\acl.ps1` la aplican o la verifican aparte.

| Dónde | Quién (además de Administradores y SYSTEM) |
|---|---|
| La raíz (protegida: no hereda de `C:\`) | nadie |
| `runtime\python` · `runtime\postgresql-*` · `runtime\caddy-*` · `runtime\node-*` | lectura y ejecución: backend y Stack · PostgreSQL y Stack · Caddy · frontend |
| `services\<servicio>.exe` y `.xml` | lectura y ejecución: ese servicio |
| `releases\<versión>\backend` · `frontend` · `windows` | lectura y ejecución: backend · frontend · Stack |
| `config\` (protegida) | nadie; `districom.env` → Stack (lectura), `backend\` → backend (lectura), `Caddyfile` → Caddy (lectura) |
| `config\postgres.secret`, `config\integrity.json` | nadie |
| Datos de PostgreSQL (protegida) | PostgreSQL (control total) |
| `data\uploads` | backend (modificar) |
| `logs\`, `state\` | Stack (modificar) |
| `logs\services\<servicio>`, `logs\postgresql` (protegidas) | ese servicio (modificar) |
| `state\caddy` (protegida, claves de Caddy) · `state\frontend-cache` (protegida) | Caddy · frontend (modificar) |
| Marca del disco de backup (protegida) | Stack (lectura) |
| Cada carpeta por encima de un permiso (hasta la raíz); datos de PostgreSQL para el Stack | atravesar: leer atributos, sin listar, sin herencia |

| Módulo | Qué hace |
|---|---|
| `ntfs` | Lee y escribe listas de acceso por SID con `advapi32`; nunca interpreta texto localizado |
| `permissions` | La política. `apply` es idempotente: primero revisa los enlaces (una junction que sale de su release frena todo, sin cambios), después aplica cada regla y quita los permisos sueltos de lo que está debajo. `verify` lee las listas efectivas de cada objeto e informa ruta y código, nunca valores |
| `integrity` | `config\integrity.json` con el SHA‑256 de runtimes, envoltorios y release; `verify` informa cambiados, faltantes y agregados |

- «Modificar» nunca incluye cambiar permisos, tomar posesión ni borrar lo de otros.
- `.next\cache` es una junction a `state\frontend-cache`: la release sigue de solo lectura.
- El Stack no puede leer `state\caddy` (están las claves de Caddy). Para saber si Caddy responde usa TLS sin verificar el certificado: es una prueba de disponibilidad en `127.0.0.1`, no una decisión de confianza. Los administradores y las pruebas sí verifican el certificado.
- Solo se tocan la raíz de DistriCom, los datos de PostgreSQL y la marca del disco de backup; nunca ACL del sistema ni de otros programas.

Las pruebas C3‑1 a C3‑5 (`permissions_tests/`) corren en el job «Windows permissions (W2-C3)». Una sonda de PowerShell intenta cada operación de verdad bajo siete identidades: los cinco servicios (apuntando el servicio a la sonda por un momento y restaurándolo), otro servicio de la máquina y un usuario local estándar.

**PENDIENTE, nunca PASS:** las cuentas y el grupo de IT reales del cliente, Store Sys y su proveedor con sus cuentas reales, la cuenta de backups (C6), Windows 11 Pro y Defender.

## C4: Firewall de Windows

`firewall apply|verify|remove` e `install\firewall.ps1` (administrador). Todas las reglas están en el grupo `DistriCom` y se llaman `DistriCom-*`; nada más se toca.

| Regla | Qué |
|---|---|
| `DistriCom-HTTP-In` · `DistriCom-HTTPS-In` | Permite TCP `HTTP_PORT` · `HTTPS_PORT` hacia `caddy.exe`, solo desde `LAN_SUBNET` y `100.64.0.0/10` |
| `DistriCom-RDP-In` | Permite TCP 3389, solo desde `LAN_SUBNET` y `100.64.0.0/10` |
| `DistriCom-Block-PostgreSQL` · `-Backend` · `-Frontend` | Bloquea TCP 5432 · 8000 · 3000 hacia el programa que escucha en cada uno |

- `apply` es idempotente y corrige una regla alterada o sobrante del grupo. `remove` quita todas las de DistriCom, aunque no haya release.
- `verify` (solo lectura, estática) compara las reglas y los perfiles. Avisa, sin cambiarlas, si las reglas propias de Windows para el Escritorio remoto están abiertas a cualquier origen.
- `verify --services-running` revisa además la instalación en marcha: 5432, 8000 y 3000 tienen que escuchar solo en `127.0.0.1`, cada uno abierto por el programa de su regla. Un listener ausente o de otro programa es un problema, no una advertencia.
- Nunca bloquea 3389 ni cambia reglas de Store Sys, del Escritorio remoto ni de otros programas, ni los perfiles.

Las pruebas C4‑1 a C4‑4 (`firewall_tests/`) corren en el job «Windows firewall (W2-C4)», con dos reglas ficticias de Store Sys y todas las reglas ajenas comparadas antes y después de cada paso.

**PENDIENTE, nunca PASS:** el acceso real desde otra PC de la LAN, Tailscale, el Escritorio remoto del grupo IT, los puertos reales de Store Sys, el perfil de red del equipo y Windows 11 Pro.

## C9‑A: coordinación de operaciones

Plan: `docs/WINDOWS_W2C_PLAN.md`, C9‑A. No toca `ALLOW_CONNECTIONS`, la propiedad ni los roles de PostgreSQL, y no tiene `resume` (eso es C9‑B).

- **Quién toma el bloqueo:** `maintenance open` y `close`, `services start`, `start --direct`, `stop` y `uninstall`, el supervisor para decidir el arranque y para reiniciar PostgreSQL y, desde C6, cada tarea de backup durante su verificación de inicio (nunca durante el trabajo).
- **`maintenance open` sin carrera:** comprueba y escribe con el bloqueo tomado, y crea `maintenance.json` en exclusiva, así que una segunda ventana nunca reemplaza a la primera.
- **Arranque:** `services start` y `start --direct` se niegan con una ventana abierta, vencida o ilegible, con un registro inválido o con una operación incompleta o incierta (`safety.start_blockers`, los mismos motivos que `writes_resume_check`). `services stop` siempre se permite, con el bloqueo: detener solo acerca a un estado seguro.
- **Protocolo sin interbloqueo:**
  1. `services start` toma el bloqueo, hace la validación previa, escribe `start-request.json` con un valor nuevo y pide al SCM que arranque el Stack, que no espera al supervisor.
  2. Suelta el bloqueo y recién entonces espera la respuesta del supervisor.
  3. El supervisor toma el bloqueo (espera acotada), hace la validación que cuenta y lo mantiene hasta registrar su fase con ese valor.
  4. `services start` acepta solo ese registro, o el del servicio vivo si el Stack ya estaba en marcha. `running` es PASS; `blocked`, `failed` o el tiempo máximo son FAIL.
  5. Con el Stack en marcha y una solicitud todavía sin respuesta, un segundo `services start` se niega.
- **Estado `blocked`:** con un estado inseguro, o sin conseguir el bloqueo, el supervisor arranca solo PostgreSQL (o nada, si no consiguió el bloqueo) y registra `blocked` con el motivo en `state\supervisor.json`. `services status` lo muestra. Reiniciar el Stack, reiniciar PostgreSQL o que venza la ventana no lo levanta.
- **Reinicio de PostgreSQL (política conservadora, Enzo 2026-11-17):** el supervisor lo reinicia por sí mismo solo si el bloqueo está libre y no hay ningún motivo de `start_blockers`. Una ventana abierta, vencida o ilegible, un registro inválido o una operación incompleta o incierta lo retienen, aunque no exista `maintenance.json`. PostgreSQL queda detenido y se deja una entrada `postgresql_not_restarted` por motivo, hasta que se resuelva el registro o un operador vuelva a arrancar el Stack, que queda en `blocked`. Arrancar PostgreSQL en `blocked` al iniciar el Stack es otra cosa: lo pide alguien y no levanta la app.
- **Trazabilidad del arranque:** `start-request.json`, `supervisor.json` y `logs\ops.log` (`services_start_requested`, `services_start_done`, `services_start_blocked`). El arranque no es una operación del registro de operaciones.

Las pruebas C9A‑1 a C9A‑13 están en dos lugares:
- `tests/test_c9a_coordination_units.py`, con procesos reales para el bloqueo y el supervisor real contra un SCM simulado. Corre también en Windows, en el job «Windows ops (W2-C)».
- `services_tests/test_c9a_coordination.py`, con los servicios reales, en el job «Windows services (W2-C2)».

**PENDIENTE, nunca PASS:** el reinicio real de Windows con un estado `blocked`, y Windows 11 Pro.

## C9‑B: bloqueo reversible de escrituras

Plan: `docs/WINDOWS_W2C_PLAN.md`, C9‑B, con sus precisiones de implementación.

- **Roles** (`database init`): `districom_ops` (el de `OPS_DATABASE_URL`, sin superusuario) es dueño de la base. La app conserva `CONNECT`, `CREATE` y `TEMPORARY`, es dueña de `public` y de lo que crea, no es miembro de ningún rol y `pg_hba.conf` la rechaza fuera de su base. No puede revertir `ALLOW_CONNECTIONS false`.
- **Bloqueo** (`write_block.block`, con el bloqueo de C9‑A y la operación dueña): enganche de Caddy (C5) registrado como «no aplica todavía»; tareas de C6 verificadas antes de cambiar nada (ninguna tarea pesada tiene `backup.lock`; si alguna lo tiene, se niega sin tocar el diario); `ALLOW_CONNECTIONS false` desde `postgres`, espera acotada de las transacciones (si vence, se revierte), `pg_terminate_backend` y comprobación de la marca y de las sesiones. Cualquier falla revierte.
- **Reversión que no se verifica** (Enzo 2026-11-19): la marca vuelve a `false` si se puede, el diario queda en `reverting` con `revert_failed` y solo `resume` lo resuelve.
- **Diario** `state\write-block.json`: `none`, `blocking`, `blocked`, `closed_complete`, `reverting`, `resuming`, con transiciones cerradas. Se escribe antes de cada paso; uno ilegible o desconocido bloquea.
- **`districom-ops resume --operation <id> --reason <texto>`:** solo un administrador; antes se termina la operación dueña. Después, `services stop` y `services start`.
- **Recuperación automática:** el supervisor, solo desde `closed_complete`, verifica la evidencia con PostgreSQL detenido (`pg_controldata` y las copias de `repo1` y `repo2`), arranca PostgreSQL y reanuda. Si algo no coincide, queda `blocked` con el motivo. Un diario `resuming` con las conexiones habilitadas: la marca vuelve a `false` y queda `blocked` hasta un `resume`.
- **Alcance de la evidencia de cierre** (Enzo 2026-11-20): C9‑B solo implementa la **verificación técnica de la evidencia que recibe** (checkpoint, timeline, estado del clúster y SHA‑256 de las copias). **C10 y C11** deberán producir evidencia auténtica del cierre. **C6 y C7** deberán garantizar el origen, la integridad y la recuperabilidad de los backups. **Los hashes de archivos no equivalen por sí solos a una restauración validada.** En CI la evidencia es fabricada.

Pruebas:
- `tests/test_c9b_units.py`: máquina de estados, contexto (C9‑X1 a X6), supervisor con SCM simulado.
- `db_tests/`: PostgreSQL real (`DISTRICOM_PG_BIN`, PostgreSQL 17.6 en CI) en clústeres descartables hechos por `database.init`. C9‑1 a C9‑9, H1, M (migraciones como la app y, con `DISTRICOM_C9M_SUITE=1`, la suite completa del backend), R y X7, con procesos matados en cada paso del diario. Resultados en `DISTRICOM_C9B_RESULTS`.
- `services_tests/test_c9b_write_block.py`: con los servicios reales, en el job «Windows services (W2-C2)».

**Riesgo residual (Enzo 2026-11-24):** si el almacenamiento impide registrar la reversión y también volver a `ALLOW_CONNECTIONS false`, PostgreSQL puede seguir aceptando conexiones con la app detenida: no es un bloqueo total. Antes de producción: red y credenciales verificadas y un procedimiento seguro de recuperación.

**PENDIENTE, nunca PASS:** la LAN con usuarios reales, el reinicio real de Windows a mitad de un cierre, Windows 11 Pro y el cierre real de jornada (C10 y C11; en CI la evidencia es fabricada).

## C6: integración de los backups (diseño AB)

Plan: `docs/WINDOWS_W2C_PLAN.md`, C6, y el diseño AB que aprobó Enzo el 2026-11-25. Usa `districom_backup` (W2‑A) sin cambiar su lógica.

- **Instalación** (`districom-ops tasks install`, administrador, idempotente): crea `repo1` en el disco de backup verificado; escribe `districom-archive.conf` en los datos de PostgreSQL (`archive_mode`, `archive_timeout = 300` y los `archive_command` y `restore_command` de W2‑A con el Python de la release; hace falta reiniciar PostgreSQL); escribe `config\tasks.json`, sin secretos; aplica los permisos de C3 con las cuentas de las tareas y registra las tareas. `database init` crea el rol `districom_backup` (`REPLICATION` y `pg_read_all_data`, sin superusuario ni propiedad) y su contraseña en `config\backup-db.secret`.
- **Claves:** el administrador escribe `config\dump.secret` y, si hay `OFFSITE_REMOTE`, `config\offsite.secret` (32 o más caracteres al azar), y **guarda una copia fuera del servidor**. Sin ellas no se pueden leer los dumps ni `repo2`.
- **Tareas** (Programador de tareas, carpeta raíz, `DistriComBackup<Nombre>`), cada una como `LocalService` con su **SID de tarea** propio (`NT TASK\…`, `S-1-5-87-…`), sin contraseña:

  | Tarea | Pesada | Cuándo | Límite |
  |---|---|---|---|
  | `base_backup` | sí | 15:00 todos los días (ventana de 15 a 17; si se pierde, no corre tarde) | 120 min |
  | `dump` (cifrado y prueba de restauración) | sí | 13:00 | 90 min |
  | `offsite` (subida asíncrona a `repo2`) | sí | cada hora, a los :40 | 120 min |
  | `retention` | sí | 03:00 | 30 min |
  | `wal_gaps` (solo lectura) | no | cada hora, a los :50 | 15 min |

- **Coordinación AB** (`tasklock`):
  - Una tarea toma `operations.lock` **solo** para verificar y registrarse. Con el bloqueo tomado comprueba que no haya ventana, operación incompleta o incierta, diario de C9‑B distinto de `none`, pedido de cancelación ni, si es pesada, otra tarea pesada (`backup.lock`). Si algo no cumple, no empieza y registra `refused` con el motivo (C6‑7). Después suelta `operations.lock` y trabaja con `backup.lock`.
  - El mantenimiento (y con él el cierre y la actualización) llama a `open_stopping_tasks`. Escribe un **pedido de cancelación persistente** con `operations.lock` tomado, espera **sin el bloqueo**, con un plazo acotado (`BACKUP_CANCEL_WAIT_SECONDS`, 120 s por defecto), a que el sistema operativo confirme que `backup.lock` está libre, y recién entonces abre la ventana, con `operations.lock` tomado de nuevo. Si la tarea no confirma, **no se abre nada** y se niega con el motivo. `MaintenanceWindow.open` también se niega, por cualquier camino, mientras haya una tarea pesada.
  - **Cada pedido tiene dueño** (revisión de Enzo, 2026-11-26). Vive en `state\tasks\cancel-requests\` como dos archivos: `<id>.owner`, un bloqueo del sistema operativo que el proceso solicitante retiene mientras el pedido siga en pie, y `<id>.json`, qué pide. Por eso:
    - dos pedidos nunca se pisan, y la tarea ve todos;
    - solo el dueño puede borrar el suyo: cerrar una ventana y `tasks clear-cancel` quitan únicamente **huérfanos**;
    - si el solicitante muere, el sistema operativo libera su bloqueo y el pedido queda huérfano. El próximo inicio de tarea lo reconcilia con `operations.lock` tomado y lo registra;
    - un pedido ilegible o una carpeta que no se puede leer cuentan como pedido válido (fallo seguro);
    - `services stop` detiene igual aunque no pueda escribir su pedido.
  - `services stop` pide lo mismo y espera igual, pero se detiene de todos modos: se conserva la garantía de C9‑A de que detener siempre se permite. Es seguro para las copias porque una tarea cortada no publica nada.
  - El supervisor no cambia.
- **Cancelación e interrupciones:** los pasos largos corren como procesos hijos que la tarea termina junto con sus propios hijos (`taskkill /T`) ante un pedido o al vencer su límite (`cancelled` o `timeout`). Después borra lo temporal y recién entonces suelta `backup.lock`. Nada se publica antes de verificarse, y una copia anterior nunca se toca. Una tarea matada queda `running` en su registro, y la siguiente corrida la marca `interrupted` y limpia lo temporal. La retención deja antes un registro de intención y termina una remoción cortada (primero los metadatos, después los datos).
- **Registro para C8** (`state\tasks\`): `<tarea>.json` (el último estado), `history.jsonl` (todos los resultados), `offsite-copy.json` (qué llegó a `repo2` y qué espera) y `districom-ops tasks status`. Cada copia se registra como **creada** o **verificada** por separado. Verificada significa que `pg_verifybackup` pasó, o que el dump se restauró en una instancia descartable. La copia externa queda «copiada, no verificada» hasta C7. **Ningún registro afirma que el sistema sea recuperable** (C7, C13).
- **Comandos:** `tasks install`, `tasks uninstall`, `tasks status`, `tasks start <tarea>` (por el Programador de tareas), `tasks clear-cancel` (quita los pedidos huérfanos, nunca uno cuyo dueño siga vivo) y `tasks run` (lo que ejecuta el Programador).

Pruebas:
- `tests/test_c6_units.py` (C6‑AB, C6‑R y C6‑U): procesos reales que tienen `backup.lock`, el ejecutor, las retenciones, las definiciones y los permisos. Corren en cualquier plataforma y en el job «Windows ops (W2-C)».
- `backups_tests/` (C6‑1 a C6‑7, C6‑AB y C6‑I): con los servicios reales, PostgreSQL 17, el Programador de tareas y rclone, en el job «Windows backups (W2-C6)».

**PENDIENTE, nunca PASS:**
- el horario real durante días;
- reinicios, apagados y cortes de energía con una tarea en curso;
- Windows 11 Pro;
- el disco real, desconectado o lleno;
- el bucket real con bloqueo de objetos;
- Defender;
- los tiempos en el i3‑9100;
- el resguardo de las claves fuera del servidor;
- la recuperabilidad completa (C7 y C13).

## Regla de logs para C2 a C13

Los logs operativos **no deben incorporar** información personal de clientes, teléfonos, direcciones ni valores completos de consultas SQL (texto o parámetros). El enmascarado de `logs` cubre secretos, credenciales y CUIT, y no es una red de seguridad para lo demás: cada etapa registra solo identificadores técnicos, conteos, estados y clases de error. Regla obligatoria (Enzo, 2026-11-07).

## Cómo correrlo

```powershell
cd deploy\windows\ops
uv sync --frozen
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest -v
uv run districom-ops init --root C:\DistriCom
uv run districom-ops --config C:\DistriCom\config\districom.env check-config
```

`config.example.env` lista las variables, sin valores.

## Criterios

- **C1‑1:** carpetas y configuración.
- **C1‑2:** ventana de mantenimiento y registro de operaciones.
- **C1‑3:** logs sin secretos ni datos personales.
- **C1‑4:** calidad (ruff, formato y mypy estricto), en los pasos del job «Windows ops (W2-C)».

Ese mismo job deja la tabla PASS/FAIL en el log.

**PENDIENTE, nunca PASS:**
- Windows 11 Pro;
- un corte de energía real durante una escritura del registro.
