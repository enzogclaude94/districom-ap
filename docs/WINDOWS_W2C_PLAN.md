# Propuesta técnica W2‑C: infraestructura de DistriCom en Windows

> Copia versionada de la propuesta, desde la revisión 2. La revisión 1 se conserva en la carpeta de informes del proyecto.

**Estado:** propuesta, **revisión 2** (2026-11-11). La revisión 1 (2026-11-06) tenía los ajustes obligatorios de Enzo y las decisiones D1 a D5 confirmadas. La revisión 2 incorpora la revisión de dependencias de C9 y las decisiones de diseño de Enzo del 2026-11-11 (ver «Cambios de la revisión 2»).
- **Hechas:**
  - C1 DONE: PR #79.
  - C2, C3 y C4 DONE a nivel de implementación y CI: PR #80, #81 y #82.
  - La corrección de la carrera de arranque de C2: PR #83.
- **Autorizada a continuación:** solo **C9‑A**, en su propia rama y PR, después de mergear esta revisión.
- **C9‑B:** aprobada conceptualmente. Su implementación necesita una autorización nueva después de revisar C9‑A.
- C5, C6, C7 y las demás subetapas **no se empiezan** sin una autorización nueva.

**Cambios de la revisión 2** (revisión de dependencias del 2026-11-11, §7, aprobados por Enzo):
1. C9 se parte en **C9‑A, coordinación de operaciones**, y **C9‑B, bloqueo reversible de escrituras**, cada una con su PR. El orden general no cambia.
2. C9‑B incluye el nuevo modelo de propiedad de PostgreSQL (hallazgo H1): `districom_ops` pasa a ser el dueño de la base `districom` y la app no tiene acceso a la base `postgres`. Agrega sus regresiones a los criterios.
3. C9 incluye:
   - el estado `blocked` del supervisor (H4), en C9‑A;
   - la credencial operativa que lee solo el Stack (H2), con su regla de C3, en C9‑B.
4. C6 suma el criterio «respeta el bloqueo de operaciones y el diario de C9».
5. C11 y C12 dependen también del modo mantenimiento de Caddy (C5‑3).
6. Requisito transversal: toda operación nueva (C6, C11, C12 y las que vengan) toma el bloqueo exclusivo de C9‑A.

**Base:**
- ADR 016, que sigue «Propuesto»;
- `docs/WINDOWS_DEPLOYMENT.md` §2 a §8;
- W2‑A, DONE como prueba de concepto y CI (`deploy/windows/backup/`);
- W2‑B, DONE como compatibilidad y pruebas automatizadas (PR #75, `b42efa0`).

**Reglas de toda la etapa:**
- Cada subetapa tiene su rama, su PR y su CI. Ninguna se mergea sin la aprobación de Enzo.
- Los jobs existentes del CI (doce al cerrar C4) no se modifican ni se eliminan. Cada subetapa agrega como máximo un job de Windows propio.
- La infraestructura Linux (`deploy/` fuera de `deploy/windows/`, `docker-compose`, systemd, pgBackRest) no cambia.
- Sin cambios funcionales en Comercial, Rendiciones ni plataforma. Si alguna subetapa necesitara uno, se detiene y se informa como en W2‑B.
- Solo datos ficticios y bases descartables.
- **Coordinación de operaciones (revisión 2):**
  - Toda operación que arranca, detiene, actualiza, mantiene o escribe toma el bloqueo exclusivo de C9‑A.
  - Arranque, actualización y mantenimiento nunca corren a la vez con una operación incompatible.
- Las acciones siguientes no forman parte de W2‑C:
  - tocar el servidor ASUS;
  - tocar el servidor actual de Store Sys;
  - migrar Store Sys;
  - contratar dominio, bucket o Tailscale;
  - comprar hardware;
  - pasar a producción.
- Lo que el CI no reproduce queda **PENDIENTE**, nunca PASS. Se completa en un laboratorio Windows 11 Pro, nunca en el equipo donde funciona Store Sys.
- **Simular no es probar lo real.** Ningún acceso remoto, apagado real ni recuperación física se da por probado porque se haya simulado en GitHub Actions. Lo simulado da PASS solo como simulación, y la variante real queda PENDIENTE.
- Los runners de GitHub son **Windows Server 2025** y corren como administrador. Sirven para instalar servicios, crear cuentas virtuales, aplicar ACL NTFS y reglas de firewall. No sirven para:
  - reiniciar el equipo;
  - probar Windows 11 Pro;
  - probar Defender real;
  - probar discos o energía reales;
  - acceder a redes reales.

## 1. Resumen de subetapas

| # | Subetapa | Depende de | Tamaño estimado |
|---|---|---|---|
| C1 | Base operativa: estructura de carpetas, configuración, registro y ventana de mantenimiento | — | Chico |
| C2 | Servicios nativos: PostgreSQL, backend, frontend y Caddy, con arranque automático y orden | C1 | Mediano |
| C3 | Cuentas de servicio y permisos NTFS | C2 | Chico |
| C4 | Firewall de Windows | C2 | Chico |
| C5 | Caddy con HTTPS (Cloudflare DNS‑01) y Tailscale | C2 · C4 | Mediano |
| C6 | Integración de los backups de W2‑A: archivado, tareas programadas y copia externa | C2 · C3 · C9‑A | Mediano |
| C7 | Requisitos de backups: retención segura, consistencia de copias externas y criterios de recuperación | C6 | Mediano |
| C8 | Monitoreo y alertas | C2 · C6 | Mediano |
| C9‑A | Coordinación de operaciones: bloqueo exclusivo, supervisor consciente del mantenimiento, estado `blocked` | C1 · C2 | Chico |
| C9‑B | Bloqueo reversible de escrituras: propiedad de PostgreSQL, diario, `resume` | C9‑A · C3 | Mediano |
| C10 | Verificación del WAL al apagar | C6 · C9‑B | Chico |
| C11 | Cierre seguro de jornada | C5 (modo mantenimiento, C5‑3) · C7 validada · C8 · C9‑B · C10 | Mediano |
| C12 | Actualizaciones y rollback | C2 · C5 (C5‑3) · C6 · C7 validada · C8 · C9‑B | Mediano |
| C13 | Instalador, suite de aceptación y restauración completa | Todas, con C7 validada | Mediano |

**Orden (D5, secuencial; cada subetapa con PR, CI y aprobación expresa antes del merge):**
1. C1;
2. C2;
3. C3;
4. C4;
5. C9‑A, después C9‑B (con autorización propia);
6. C6;
7. C10;
8. C7;
9. C8;
10. C5;
11. C11;
12. C12;
13. C13.

Ajustes de la revisión 1:
- **C10 va después de C6:** no puede implementarse completa sin el archivado integrado.
- **C11 exige C7 validada,** además de C8, C9 y C10.
- **C12 y C13 respetan las garantías de C7:** el backup previo a una actualización, la restauración de `rollback --restore-db` y la restauración completa se juzgan con los criterios de C7.
- C9 se adelanta porque es el requisito de mayor riesgo y solo depende de C2. Revisión 2: sus garantías no dependen de HTTPS (C5), del monitoreo (C8) ni de los backups (C6 y C7), y C6, C10, C11 y C12 se apoyan en ella.
- C5 va tarde porque su parte real depende de contrataciones no autorizadas.

**Los cinco requisitos obligatorios y dónde se resuelven:**

| Requisito | Subetapa |
|---|---|
| 1. Bloqueo de escrituras efectivo y reversible | **C9‑A** (coordinación) y **C9‑B** (bloqueo), usado por C6, C11 y C12 |
| 2. Verificación correcta del WAL al apagar | **C10**, usada por C11 |
| 3. Retención validada restaurando todos los backups conservados | **C7** |
| 4. Consistencia de manifiestos y copias externas con archivado en curso | **C7** |
| 5. Criterios precisos de COMPLETE, INCOMPLETE y FAILED, con el último punto realmente recuperable | **C7**, usado por C11, C12 y C13 |

**Decisiones confirmadas por Enzo (2026-11-06):**
- **D1:** Python con biblioteca estándar para la lógica operativa (`deploy/windows/ops/`, `districom_ops`), reutilizando `districom_backup`. PowerShell para instalación, ACL y firewall.
- **D2:** WinSW para los servicios, con versión fija, checksum y pruebas de instalación y desinstalación.
- **D3:** `ALTER DATABASE … ALLOW_CONNECTIONS false`, terminación controlada de sesiones y diario de estado, sujeto a pruebas completas de reversión y recuperación.
- **D4:** Caddy compilado con `xcaddy` y el módulo de Cloudflare, con versiones fijadas y verificadas.
- **D5:** desarrollo secuencial desde C1; cada subetapa con su PR, su CI y aprobación expresa antes del merge.

## 2. Detalle por subetapa

### C1. Base operativa

1. **Objetivo:** dejar la estructura y las piezas comunes que usan todas las demás subetapas:
   - las carpetas `C:\DistriCom\` (`releases\`, `current`, `config\`, `logs\`, `state\`);
   - la lectura de la configuración, que es el equivalente a `production.env`;
   - el log estructurado sin datos personales;
   - el registro de operaciones;
   - la ventana de mantenimiento: archivo con hora, motivo y operación dueña (cierre, actualización o manual).
    - A los 60 min el monitor pasa a FAIL, igual que en Linux.
    - **El vencimiento nunca habilita escrituras ni cierra la ventana.** Con un cierre o una actualización incompletos, las escrituras siguen bloqueadas hasta que se termina o se revierte esa operación de forma explícita (C9).
2. **Archivos:**
   - `deploy/windows/ops/` (paquete, `pyproject.toml`, `uv.lock`, README);
   - `deploy/windows/ops/config.example.env` (solo nombres de variables, sin valores reales);
   - un job de CI nuevo, «Windows ops (W2-C)», que crece con cada subetapa.
3. **Dependencias:** ninguna.
4. **Pruebas PASS/FAIL:**
   - **C1‑1:** la configuración rechaza variables faltantes o inseguras sin imprimir secretos.
   - **C1‑2:** la ventana de mantenimiento se abre y se cierra. Al vencer, da FAIL sin cerrarse sola y sin cambiar el estado de las escrituras.
   - **C1‑3:** los logs no contienen secretos ni datos personales.
   - **C1‑4:** ruff, formato y mypy estricto.
5. **Riesgos:** bajos. El riesgo es diseñar de más; se limita a lo que usan C2 a C13.
6. **CI / laboratorio:** todo en CI.

### C2. Servicios nativos de Windows

1. **Objetivo:** instalar y arrancar los cuatro servicios como servicios de Windows:
   - **PostgreSQL 17**, con `pg_ctl register`, el clúster ICU `es-AR` con checksums y los parámetros de O7;
   - **backend** (uvicorn), **frontend** (`node server.js`) y **Caddy**, cada uno con **WinSW**, versión fijada con checksum.

   Comportamiento esperado:
   - arranque automático en orden: base, backend, frontend, Caddy;
   - reinicio ante caída con límite de intentos;
   - parada ordenada;
   - PostgreSQL arranca **solo si el disco de backup está presente** (marca `.districom-backup-disk`).
2. **Archivos:**
   - `deploy/windows/ops/services/`, con las plantillas XML de WinSW y el registro de servicios;
   - `deploy/windows/ops/src/districom_ops/services.py`;
   - un chequeo previo al arranque de PostgreSQL.
3. **Dependencias:** C1.
4. **Pruebas PASS/FAIL:**
   - **C2‑1:** los servicios se instalan y la instalación repetida es idempotente.
   - **C2‑2:** al arrancar, `/health/ready` responde 200 a través de Caddy.
   - **C2‑3:** el orden de dependencias se respeta.
   - **C2‑4:** si el backend se mata, el servicio lo reinicia.
   - **C2‑5:** la parada es ordenada: el log muestra «Finished server process», PostgreSQL apaga limpio y no quedan conexiones.
   - **C2‑6:** sin la marca del disco de backup, PostgreSQL **no** arranca y queda el motivo registrado.
   - **C2‑7:** se desinstala sin dejar servicios huérfanos.
5. **Riesgos:**
   - WinSW es una dependencia externa y se fija por versión y checksum. La alternativa es NSSM, sin mantenimiento activo; no se recomienda.
   - En Windows, el arranque condicionado al disco depende del orden en que el sistema monta los discos.
6. **CI / laboratorio:**
   - **En CI:** todo lo anterior.
   - **PENDIENTE:**
     - el arranque **después de un reinicio real y sin iniciar sesión**;
     - Windows 11 Pro;
     - el arranque con el disco de backup ausente de verdad, desconectado físicamente.

### C3. Cuentas de servicio y permisos NTFS

1. **Objetivo:** cada servicio corre con una cuenta virtual propia (`NT SERVICE\districom-backend`, etc.), sin administrador.

   ACL NTFS:
   - `config\`: solo las cuentas de servicio que lo leen, más el grupo IT;
   - datos de PostgreSQL y `repo1`: solo la cuenta de PostgreSQL, la de backups y el grupo IT;
   - `logs\`: escritura para cada servicio en lo suyo.

   Store Sys y su proveedor no tienen permisos sobre estas carpetas.
2. **Archivos:**
   - `deploy/windows/ops/install/acl.ps1`, envoltorio fino;
   - `districom_ops/acl.py`, que verifica leyendo las ACL efectivas.
3. **Dependencias:** C2.
4. **Pruebas PASS/FAIL:**
   - **C3‑1:** cada servicio corre con su cuenta, comprobado con `Get-CimInstance Win32_Service`.
   - **C3‑2:** una cuenta de prueba que no es de DistriCom no puede leer `config\` ni los datos.
   - **C3‑3:** el backend no puede escribir en `releases\`.
   - **C3‑4:** si se aplican las ACL dos veces, el resultado no cambia.
   - **C3‑5:** ningún secreto queda legible para `Users` ni para `Everyone`.
5. **Riesgos:**
   - Una ACL demasiado cerrada puede impedir el arranque; se cubre con C2‑2 después de aplicar C3.
   - Las cuentas de dominio o locales del cliente son desconocidas.
6. **CI / laboratorio:**
   - **En CI:** todo.
   - **PENDIENTE:** las cuentas reales del grupo IT y del proveedor de Store Sys, en Windows 11 Pro.

### C4. Firewall de Windows

1. **Objetivo:** reglas con nombre propio (`DistriCom-*`), idempotentes:
   - 443 y 80 solo desde la subred LAN configurada y `100.64.0.0/10`;
   - 3389 solo desde la LAN y Tailscale;
   - 5432, 8000 y 3000 solo en `127.0.0.1`. Esto se comprueba también por el puerto donde escucha cada servicio, no solo por las reglas.

   No toca las reglas de Store Sys ni las reglas ajenas.
2. **Archivos:**
   - `deploy/windows/ops/install/firewall.ps1`;
   - `districom_ops/firewall.py`, que verifica.
3. **Dependencias:** C2.
4. **Pruebas PASS/FAIL:**
   - **C4‑1:** las reglas existen con su alcance exacto.
   - **C4‑2:** aplicar dos veces no las duplica.
   - **C4‑3:** 5432, 8000 y 3000 no escuchan fuera de `127.0.0.1`.
   - **C4‑4:** quitar las reglas de DistriCom no toca ninguna regla ajena (en CI, una regla «Store Sys» ficticia).
5. **Riesgos:** dejar sin acceso al Escritorio remoto. Cualquier cambio de reglas en el equipo real se aplica con una sesión ya abierta y con una reversión programada.
6. **CI / laboratorio:**
   - **En CI:** C4‑1 a C4‑4.
   - **PENDIENTE:**
     - el acceso real desde otra PC de la LAN;
     - el acceso por Tailscale;
     - los puertos reales de Store Sys, que define su proveedor.

### C5. Caddy con HTTPS y Tailscale

1. **Objetivo:**
   - Caddy compilado con `caddy-dns/cloudflare` mediante `xcaddy`, en CI, con versión y checksum registrados;
   - el Caddyfile actual reutilizado con destinos de `127.0.0.1`;
   - el modo «en mantenimiento», que usarán C9, C11 y C12;
   - la instalación de Tailscale en modo desatendido, con la política de `deploy/tailscale/policy.hujson`.
2. **Archivos:**
   - `deploy/windows/ops/caddy/` (build y configuración);
   - `districom_ops/caddy.py`;
   - `deploy/windows/ops/install/tailscale.ps1`.
3. **Dependencias:** C2 y C4.
4. **Pruebas PASS/FAIL:**
   - **C5‑1:** el binario compilado incluye el módulo de Cloudflare (`caddy list-modules`).
   - **C5‑2:** HTTPS con CA interna: headers de seguridad, CSP y `/api` por el rewrite.
   - **C5‑3:** el modo mantenimiento responde 503 con su página a todo pedido nuevo y se revierte.
   - **C5‑4:** el certificado se renueva con la CA interna y vida corta.
   - **C5‑5:** el Caddyfile de Windows y el de Linux no se separan en lo de seguridad. Se comparan las directivas.
5. **Riesgos:**
   - La emisión real por DNS‑01 necesita dominio y token de Cloudflare, que no están contratados.
   - Si Tailscale y Store Sys usan rangos de red en conflicto, hay un problema de convivencia.
6. **CI / laboratorio:**
   - **En CI:** C5‑1 a C5‑5.
   - **PENDIENTE:**
     - el certificado real de Let's Encrypt por DNS‑01;
     - Tailscale real y su ACL;
     - el acceso remoto de punta a punta.

     Requieren dominio, cuenta de Cloudflare y tailnet, **no autorizados**.

### C6. Integración de los backups de W2‑A

1. **Objetivo:** llevar la prueba de concepto de W2‑A a la operación:
   - `archive_command` y `restore_command` configurados en el PostgreSQL de C2;
   - **Programador de tareas** con la cuenta de backups:
     - backup base diario de 15 a 17, con verificación;
     - subida asíncrona a `repo2`;
     - `pg_dump` diario cifrado;
     - retención;
     - revisión de huecos;
   - bloqueo para que dos tareas pesadas no corran a la vez;
   - registro del estado de cada tarea para C8.
2. **Archivos:**
   - `deploy/windows/ops/tasks/` (definiciones XML del Programador de tareas);
   - `districom_ops/schedule.py`;
   - uso de `districom_backup` sin cambiar su lógica. Si hiciera falta un cambio, se hace en un PR con sus tests.
3. **Dependencias:** C2, C3 y C9‑A (bloqueo de operaciones).
4. **Pruebas PASS/FAIL:**
   - **C6‑1:** con el servicio real de PostgreSQL archivando, cada segmento llega a `repo1`.
   - **C6‑2:** las tareas se registran con su cuenta y su horario, y la registración es idempotente.
   - **C6‑3:** corridas forzadas de cada tarea terminan bien y registran su estado.
   - **C6‑4:** dos tareas pesadas no se superponen.
   - **C6‑5:** sin Internet (remoto inaccesible), la base no se frena y el atraso queda registrado.
   - **C6‑6:** el dump está cifrado y se puede restaurar.
   - **C6‑7 (revisión 2):** cada tarea toma el bloqueo exclusivo de C9‑A y respeta el diario de C9‑B. Con el bloqueo tomado por otra operación, o con un bloqueo de escrituras incompleto, no empieza y lo registra.
5. **Riesgos:**
   - La tarea programada corre sin sesión; en CI se corre con `schtasks /run`, no por horario real.
   - El rendimiento del i3‑9100 durante el backup base es desconocido.
6. **CI / laboratorio:**
   - **En CI:** C6‑1 a C6‑6.
   - **PENDIENTE:**
     - la ejecución por horario real durante días;
     - Defender real con las exclusiones;
     - el bucket real con bloqueo de objetos;
     - los tiempos en el hardware real.

### C7. Requisitos de backups: retención, consistencia y criterios de recuperación

1. **Objetivo:** resolver los requisitos 3, 4 y 5 de §8.3.
   - **Retención segura:** después de aplicar la retención, se restaura completo **cada** backup conservado, incluidos los de otras timelines, y se compara cada uno con su estado esperado (huella de `verify.sql` tomada al hacer el backup).
   - **Consistencia con archivado en curso:**
     - el manifiesto de la copia externa se toma de un estado del repositorio que no cambia mientras se copia: la lista de segmentos queda fijada al empezar y lo que llega después va a la próxima copia;
     - una copia interrumpida nunca reemplaza a una válida;
     - todo manifiesto publicado se puede verificar y restaurar.
   - **Criterios por escrito de COMPLETE, INCOMPLETE y FAILED:** cada restauración informa siempre tres puntos distintos:
     - el **punto pedido**;
     - el **último WAL archivado**;
     - el **último punto realmente recuperable**: LSN, hora y timeline alcanzados al promover.
   - **Punto demostrado frente a punto inferido:**
     - **Demostrado:** el que se obtuvo restaurando de verdad. Es el LSN de replay y la timeline que reporta la instancia restaurada al promover, más un dato de control leído en ella: la última fila con su LSN de commit, o el punto de restauración con su nombre.
     - **Inferido:** el que se deduce sin restaurar, por los nombres de los segmentos archivados, sus fechas o el LSN del corte.
     - El informe dice siempre de cuál se trata. Un punto inferido nunca se presenta como recuperable. **COMPLETE exige un punto demostrado.**
2. **Archivos:**
   - `deploy/windows/backup/` (retención, copia externa y restauración), con sus tests;
   - `docs/WINDOWS_DEPLOYMENT.md` §5, con los criterios.
3. **Dependencias:** C6.
4. **Pruebas PASS/FAIL:**
   - **C7‑1:** con N backups conservados en dos o más timelines, se restauran los N y todos dan IDENTICAL contra su huella.
   - **C7‑2:** la retención nunca deja un backup conservado que no se pueda restaurar, con inyección de un segmento o un `.history` faltante.
   - **C7‑3:** con escritura y archivado continuos durante la copia externa:
     - el manifiesto es válido;
     - la copia se verifica;
     - restaurar solo desde ella llega al punto que declara.
   - **C7‑4:** si la copia se corta a mitad, la copia anterior sigue siendo la vigente y es restaurable.
   - **C7‑5:** cada escenario de la tabla de criterios da exactamente el resultado definido:
     - objetivo alcanzado;
     - hueco antes del objetivo;
     - hueco sin objetivo;
     - objetivo posterior al último WAL;
     - backup corrupto;
     - timeline equivocada.
   - **C7‑6:** el informe siempre trae los tres puntos y nunca confunde el recuperable con el pedido.
   - **C7‑7:** sin restauración, el informe solo puede dar un punto «inferido», y COMPLETE es imposible.
5. **Riesgos:**
   - Restaurar todos los backups conservados tarda en proporción a la retención. En CI se usan bases chicas. En el equipo real se planifica como simulacro programado.
6. **CI / laboratorio:**
   - **En CI:** C7‑1 a C7‑6.
   - **PENDIENTE:** la restauración desde el bucket real hacia **otro** Windows (simulacro trimestral) y el tiempo real.

### C8. Monitoreo y alertas

1. **Objetivo:** el equivalente de `deploy/bin/monitor`, cada 5 min por el Programador de tareas. Distingue:
   - «APP DOWN», «DATABASE DOWN» y «MIGRATION MISMATCH»;
   - el archivado de WAL y su atraso;
   - el atraso de `repo2`;
   - los huecos;
   - el espacio en disco;
   - el último backup base y su verificación;
   - el estado de las tareas de C6;
   - la ventana de mantenimiento (aviso, no alerta, hasta 60 min);
   - «arranque OK» al iniciar.

   Las alertas salen por `ALERT_COMMAND`, como en Linux: el informe se entrega por stdin a un comando configurable, y sin canal quedan solo en el log.
2. **Archivos:**
   - `districom_ops/monitor.py`;
   - la tarea programada;
   - `deploy/windows/ops/README` (qué significa cada alerta).
3. **Dependencias:** C2 y C6.
4. **Pruebas PASS/FAIL:**
   - **C8‑1:** cada falla simulada produce **su** alerta y solo esa:
     - servicio detenido;
     - base caída;
     - migración distinta;
     - archivado roto;
     - hueco;
     - disco lleno, simulado con cuota;
     - backup vencido.
   - **C8‑2:** con la ventana de mantenimiento abierta, se informa mantenimiento; vencida, se da FAIL.
   - **C8‑3:** ningún chequeo cuelga más que su timeout.
   - **C8‑4:** las alertas no contienen secretos ni datos personales.
5. **Riesgos:**
   - El canal de alertas real no está definido; es el mismo pendiente que en Linux.
   - Leer el estado de Intel RST depende de su herramienta.
6. **CI / laboratorio:**
   - **En CI:** C8‑1 a C8‑4.
   - **PENDIENTE:**
     - el estado real del **RAID de Intel RST** y **SMART** de los discos;
     - el canal de alertas real;
     - el comportamiento durante semanas.

### C9. Bloqueo reversible de escrituras (requisito 1), en dos subetapas

La revisión 2 parte C9 en dos PR, aprobados por Enzo el 2026-11-11:
- **C9‑A, coordinación de operaciones.** Va primero y no toca `ALLOW_CONNECTIONS`.
- **C9‑B, bloqueo reversible de escrituras.** Necesita una autorización nueva después de revisar C9‑A.

Lo que estaba en `main` (`274ad4d`) antes de la revisión:
- `writes_resume_check` existía, pero nadie la usaba para desbloquear.
- No había ningún bloqueo exclusivo entre operaciones.
- El supervisor no miraba la ventana de mantenimiento ni el registro de operaciones.
- El rol de la app era dueño de la base `districom` y podía revertir `ALLOW_CONNECTIONS` desde la base `postgres` (hallazgo H1, comprobado en una instancia descartable de PostgreSQL 16).

#### C9‑A. Coordinación de operaciones

1. **Objetivo:** que dos operaciones incompatibles nunca corran a la vez, y que el supervisor no abra la app con un estado incompleto o inseguro.

   **Bloqueo exclusivo entre procesos:**
   - Es el archivo `state\operations.lock`, abierto en exclusiva con el bloqueo de archivos de Windows.
   - Si el proceso dueño muere, el sistema operativo lo libera.
   - Al lado se escribe quién lo tiene: la operación, el proceso y la hora. Solo sirve para el mensaje de error, nunca para decidir.
   - No vence. Quien no lo consigue falla enseguida, o tras un tiempo máximo corto, y nombra la operación dueña. Nunca espera sin límite.
   - **Que un proceso muera libera el bloqueo, pero no toca el estado persistente.** La ventana de mantenimiento, el registro de operaciones y, en C9‑B, el diario y la marca de la base quedan como estaban. La operación interrumpida queda incompleta y la tiene que resolver una operación explícita.

   **Quién lo toma:**
   - `services start` y `services stop`;
   - `maintenance open` y `maintenance close`;
   - `resume`, en C9‑B;
   - todo lo que venga: las tareas de C6, `cierre-jornada` de C11, y `update` y `rollback` de C12 (requisito transversal).

   **La carrera de `maintenance open`:**
   - Antes comprobaba que no hubiera una ventana y después la escribía, así que dos aperturas simultáneas podían pasar las dos.
   - Ahora la comprobación y la escritura se hacen dentro del bloqueo, y la ventana se crea con creación exclusiva del archivo.

   **`services start` y `services stop`:**
   - Toman el bloqueo.
   - Se niegan con una ventana de mantenimiento abierta o vencida.
   - También se niegan con una operación incompleta o incierta en el registro.
   - Una operación que tiene la ventana arranca y detiene los servicios desde adentro, con el bloqueo ya tomado. No vuelve a pedirlo por la CLI.

   **El supervisor:**
   - Toma el bloqueo solo para decidir al arrancar, no durante su vigilancia normal.
   - Si encuentra cualquiera de estos estados:
     - una ventana abierta o vencida;
     - una operación incompleta o incierta;
     - en C9‑B, un diario de bloqueo sin resolver;

     arranca **solo PostgreSQL**, que hace falta para recuperar. No arranca backend, frontend ni Caddy, y registra la fase **`blocked`** con el motivo en `state\supervisor.json`.
   - `services start` informa `blocked` como FAIL explícito, con el motivo. Nunca lo cuenta como un arranque correcto.
   - Volver a intentar el arranque, reiniciar Windows o que venza una espera no cambia nada: sigue en `blocked` hasta que una operación explícita resuelva el estado.
   - El reinicio de PostgreSQL después de una caída se mantiene. En C9‑B, la marca de la base sobrevive al reinicio porque vive en el catálogo.
2. **Archivos:**
   - `districom_ops/oplock.py`;
   - cambios en `maintenance.py`, `services.py` y `cli.py`;
   - las pruebas en el job «Windows services (W2-C2)» y en tests unitarios.
3. **Dependencias:** C1 y C2.
4. **Pruebas PASS/FAIL:**
   - **C9A‑1:** el bloqueo es exclusivo entre procesos reales. La segunda operación falla y nombra la primera.
   - **C9A‑2:** si se mata el proceso dueño, el bloqueo queda libre. La ventana, el registro y el resto del estado persistente quedan idénticos.
   - **C9A‑3:** la carrera de `maintenance open` se reproduce sin el arreglo (regresión) y con el arreglo nunca hay dos ventanas.
   - **C9A‑4:** `services start` y `services stop` se niegan con una ventana abierta, vencida o ilegible, y con una operación incompleta o incierta.
   - **C9A‑5:** el arranque del Stack con un estado inseguro deja PostgreSQL arriba y la app abajo. Registra `blocked` con su motivo, y `services start` da FAIL.
   - **C9A‑6:** volver a arrancar el Stack, reiniciar PostgreSQL o dejar vencer la ventana no saca a la instalación de `blocked`.
   - **C9A‑7:** dos `services start` simultáneos: uno arranca y el otro falla al instante, sin confirmación equivocada.
   - **C9A‑8:** ruff, formato, mypy estricto, y los logs sin secretos ni datos personales.
5. **Riesgos:**
   - Un bloqueo de archivos que no se libere en algún caso de Windows. Se cubre con C9A‑2 sobre procesos reales.
   - Que el estado `blocked` deje la app caída sin aviso hasta C8. Mientras tanto, queda en el log y en `services status`.
6. **CI / laboratorio:**
   - **En CI:** todo.
   - **PENDIENTE:** el reinicio real de Windows con un estado `blocked`, y Windows 11 Pro.

#### C9‑B. Bloqueo reversible de escrituras

1. **Objetivo:** cumplir las cinco condiciones de §6:
   - cortar también las sesiones abiertas;
   - cubrir todos los roles que pueden escribir;
   - ser reversible;
   - recuperarse tras un error a mitad;
   - restablecerse solo en el siguiente arranque, únicamente tras un cierre completo y verificablemente seguro.

   **Modelo de propiedad y permisos de PostgreSQL (D1 de C9, Enzo 2026-11-11):**
   - **Tres roles separados:**
     - **aplicación:** el de `DATABASE_URL`;
     - **operación:** `districom_ops`, `LOGIN`, `NOSUPERUSER`, `NOCREATEDB` y `NOCREATEROLE`. Es el dueño de la base `districom` y miembro de `pg_signal_backend`, para terminar las sesiones de la app;
     - **administración:** el superusuario `postgres`, cuya contraseña solo leen los administradores.
   - **La aplicación deja de ser dueña de la base:**
     - Conserva `CONNECT` y `CREATE` sobre `districom`, para que las migraciones sigan creando sus schemas.
     - Sigue siendo dueña de los schemas y objetos que crea.
     - No puede ejecutar `ALTER DATABASE districom WITH ALLOW_CONNECTIONS true`.
     - No es miembro de ningún otro rol, así que no puede asumir `districom_ops` con `SET ROLE` ni por herencia. Se comprueba en `pg_auth_members` y con `pg_has_role`.
   - **Acceso a la base administrativa, sin revocaciones globales:**
     - `pg_hba.conf` agrega reglas solo para el rol de la app: puede conectarse a `districom` y se rechaza su conexión a cualquier otra base (`postgres`, `template1`).
     - No se quita `CONNECT` a `PUBLIC` en `postgres` ni en ninguna otra base.
     - No se cambia ningún otro rol ni ninguna otra base.
   - **Dónde se aplica:** solo en la inicialización del clúster de Windows (`database.init`) y en clústeres de prueba descartables, sin datos reales.
     - No se modifican migraciones de Alembic, ni comerciales ni contables.
     - **Si el modelo obligara a cambiar el contrato de migraciones** (quién las corre, con qué rol o qué permisos necesitan), C9‑B se detiene y presenta alternativas antes de cambiarlo.
   - **Credencial operativa:** `config\ops-db.secret`, que leen solo el Stack y los administradores. Es una regla nueva de C3, verificada con la política de C3.

   **Bloqueo (pasos):**
   1. Caddy pasa a modo mantenimiento, de C5. Hasta C5 es un paso de enganche: el diario lo registra como «no aplica todavía».
   2. Se suspenden las tareas que escriben, de C6. Hasta C6, también es un enganche registrado.
   3. `ALTER DATABASE districom WITH ALLOW_CONNECTIONS false`, ejecutado por `districom_ops` desde la base `postgres`. Rechaza toda conexión nueva de cualquier rol, superusuario incluido.
   4. Se espera que terminen las transacciones en curso, con un tiempo máximo. Si vence, no se fuerza: se revierte todo.
   5. Se terminan las sesiones restantes de `districom` (`pg_terminate_backend`).
   6. Se comprueba en `pg_database` y en `pg_stat_activity` que la marca está en `false` y que no queda ninguna sesión en `districom`.

   **Diario de estado (`state\write-block.json`):**
   - Se escribe **antes** de cada paso, con `fsync` y renombre. Registra:
     - la operación dueña (cierre, actualización o manual);
     - el paso;
     - qué se cambió.
   - La reversión deshace exactamente lo registrado y es idempotente.

   **Máquina de estados del diario** (se documenta y se prueba completa):

   | Estado | Significa | Sale por |
   |---|---|---|
   | `none` | sin bloqueo | `blocking` (inicio de un bloqueo) |
   | `blocking` (paso *k*) | bloqueo en curso | `blocked` (paso 6 verificado) · `reverting` (falla o tiempo máximo) |
   | `blocked` | bloqueo verificado; la operación dueña sigue | `closed_complete` (C11, con las garantías de C10) · `reverting` (la operación dueña desiste) |
   | `closed_complete` | cierre terminado y verificado | `resuming` (automático al arrancar, o `resume`) |
   | `reverting` | deshaciendo lo registrado | `none` (verificado) |
   | `resuming` | restableciendo las escrituras | `none` (verificado) |

   Cualquier estado distinto de `none` deja al supervisor en `blocked` (C9‑A). Hasta C10 y C11, ningún cierre real llega a `closed_complete`.

   **Restablecimiento automático (D2 de C9, Enzo 2026-11-11).** Solo pasa cuando se cumple todo esto:
   - el diario está en `closed_complete`, con la evidencia que exigen C10 y C11;
   - `writes_resume_check` da sí: ventana cerrada, registro coherente y ninguna operación incompleta, incierta ni incompatible;
   - se cumplen las comprobaciones de seguridad aplicables (disco de backup y conexión operativa).

   El supervisor lo hace con el bloqueo exclusivo tomado. **Nunca** habilita escrituras porque terminó una espera, venció una ventana o se reinició Windows. Un cierre incompleto, una actualización fallida o cualquier duda dejan el sistema en `blocked`.

   **Hasta C10 y C11 el mecanismo queda preparado pero no validado:**
   - C9‑B lo implementa y lo prueba en CI con diarios fabricados para la prueba;
   - el cierre completo no se declara validado antes de esas etapas.

   **Restablecimiento manual (`districom-ops resume`):**
   - Requisitos para ejecutarlo:
     - administrador por Escritorio remoto;
     - el id de la operación a resolver (`--operation`) y un motivo (`--reason`).
   - Registro de auditoría en el registro de operaciones:
     - la cuenta de Windows que lo ejecuta (su SID);
     - el estado que encontró;
     - cada paso que dio.

     Sin otros datos personales.
   - Antes de habilitar nada, verifica el estado real: el diario, `pg_database` y `pg_stat_activity`.

   **Sin ventana entre `writes_resume_check` y el desbloqueo** (requisito de Enzo del 2026-11-09). Todo esto pasa con el bloqueo exclusivo tomado, y cualquier operación nueva necesita el mismo bloqueo, así que ninguna puede empezar en el medio:
   1. tomar el bloqueo exclusivo;
   2. llamar a `writes_resume_check`;
   3. `ALLOW_CONNECTIONS true` por la conexión operativa a `postgres`;
   4. verificar en `pg_database`;
   5. abrir una conexión de prueba con el rol de la app;
   6. recién entonces volver Caddy al modo normal (desde C5);
   7. marcar el diario `none`;
   8. soltar el bloqueo.

   **La recuperación no se bloquea a sí misma:**
   - `resume` es una operación del registro. `writes_resume_check` recibe el id de la operación que la llama, y solo puede pasar por alto esa operación, que tiene que ser de tipo `resume` y tener el bloqueo.
   - Si un `resume` anterior murió a mitad, el siguiente lo cierra como FAILED después de volver a verificar el estado real, y lo deja registrado.
   - Los pasos de `resume` son idempotentes, así que repetirlo siempre puede terminar.

   **Fallas y reinicios:**
   - Si PostgreSQL no está levantado, no se restablece nada.
   - Si la conexión operativa falla (rol, contraseña o `pg_hba`), las escrituras siguen bloqueadas, la app queda en `blocked` y queda la alerta. Nunca se habilitan escrituras a medias.

   **Por qué no `REVOKE CONNECT` ni `ALTER ROLE`:**
   - `REVOKE` no alcanza a quien conserva el permiso por `PUBLIC` ni corta sesiones.
   - `ALTER ROLE … NOLOGIN` habría que repetirlo por cada rol y cambia objetos de seguridad.
   - `ALLOW_CONNECTIONS` es una sola marca de la base, fácil de verificar y de revertir.
2. **Archivos:**
   - `districom_ops/write_block.py`;
   - `resume` en `cli.py`;
   - cambios en `database.py` (roles y `pg_hba`), `services.py` (supervisor), `permissions.py` (credencial operativa) y `safety.py` (operación propia).
3. **Dependencias:** C9‑A y C3. El paso de Caddy se completa con C5 y el de las tareas con C6; antes se prueban sin ellos.
4. **Pruebas PASS/FAIL** (PostgreSQL 17 real en CI):
   - **C9‑1:** con sesiones abiertas de **todos** los roles, incluido uno con permiso por `PUBLIC` y uno de prueba, después del bloqueo:
     - ninguna puede escribir;
     - ninguna conexión nueva entra.
   - **C9‑2:** una transacción larga en curso termina bien antes de cortar, o el cierre vence y revierte sin cortarla.
   - **C9‑3:** la reversión deja todo como estaba, comparado con el estado anterior.
   - **C9‑4:** el proceso se mata en cada paso del 1 al 6 y en cada estado de la máquina (inyección de fallas). En todos los casos:
     - el bloqueo exclusivo queda libre;
     - el diario y la marca quedan como estaban;
     - el supervisor queda en `blocked`;
     - `resume` termina después de verificar.
   - **C9‑5:** se verifica que no queda ninguna sesión en `districom`, en `pg_database` y en `pg_stat_activity`.
   - **C9‑6:** con la marca en `false`, se reinicia PostgreSQL. La marca sigue en `false`, y el restablecimiento por la base `postgres` con `districom_ops` funciona.
   - **C9‑7:** con la conexión operativa rota (contraseña mala o rol sin permiso), nada se habilita y queda la alerta.
   - **C9‑8:** la ventana vencida, un reinicio del Stack o una espera terminada no habilitan escrituras.
   - **C9‑9:** repetir la reversión, o un `resume` sobre otro `resume` interrumpido, no cambia nada y termina.
   - **C9‑H1 (regresiones del hallazgo H1):**
     - el rol de la app no puede ejecutar `ALTER DATABASE districom WITH ALLOW_CONNECTIONS true`, ni desde `districom` ni desde ninguna otra base;
     - su conexión a `postgres` y a `template1` se rechaza;
     - no puede `SET ROLE districom_ops` ni hereda sus permisos;
     - `districom_ops`, sin ser superusuario, sí puede cambiar la marca.
   - **C9‑M (migraciones y permisos):**
     - en el clúster de Windows con el nuevo modelo, `alembic upgrade head` desde vacío, `downgrade base` y `upgrade head` terminan bien;
     - el backend pasa su suite contra ese clúster;
     - el rol de la app es dueño de los schemas, tablas, secuencias y demás objetos que creó, y puede usarlos.
   - **C9‑R (restablecimiento automático):**
     - con un diario `closed_complete` fabricado y todo en regla, el Stack restablece las escrituras;
     - con cualquier otra combinación (diario en otro estado, operación incompleta, ventana abierta, conexión operativa rota), queda en `blocked`.
5. **Riesgos:**
   - Un superusuario conectado por fuera queda cubierto por el paso 5, pero un superusuario puede revertir la marca. Queda documentado como acción de administrador.
   - `districom_ops` es dueño de la base y podría borrarla. Su credencial la leen solo el Stack y los administradores (C3).
   - Store Sys no se ve afectado porque es otra base. Hay que confirmar con su proveedor que no use el mismo clúster.
6. **CI / laboratorio:**
   - **En CI:** C9‑1 a C9‑9, C9‑H1, C9‑M y C9‑R.
   - **PENDIENTE:**
     - el ensayo con la app real cargada por usuarios en la LAN;
     - el reinicio real de Windows a mitad de un cierre, en el laboratorio;
     - Windows 11 Pro;
     - el cierre completo, que no se valida antes de C10 y C11.

### C10. Verificación del WAL al apagar (requisito 2)

1. **Objetivo:** certificar que después del LSN registrado no hubo escrituras de datos y que el apagado fue limpio, sin depender solo de `pg_controldata`.

   **Diseño propuesto:**
   1. Con el bloqueo de C9 activo:
      - se crea el punto de restauración del día;
      - se ejecuta `pg_switch_wal()`;
      - se registra el LSN de corte.
   2. Se espera la confirmación de que todos los segmentos hasta el LSN de corte están en `repo1` **y** en `repo2`, verificados por SHA‑256.
   3. Se detiene PostgreSQL con apagado limpio.
   4. Se analiza con `pg_waldump` **todo** el WAL local posterior al LSN de corte:
      - solo se aceptan los registros de una lista cerrada (el `CHECKPOINT_SHUTDOWN` y los registros de control propios del cambio de segmento y del apagado);
      - cualquier registro de datos (Heap, Btree, Transaction COMMIT, etc.) es FAIL.
   5. Se cruza con `pg_controldata`:
      - estado «shut down»;
      - la ubicación del último checkpoint coincide con el último registro que leyó `pg_waldump`;
      - la timeline es la esperada.

   Si algo no cierra, no se apaga Windows: se arranca la base sin la app, se repite y se avisa.
2. **Archivos:** `districom_ops/wal_shutdown.py`, con la lista de registros permitidos documentada.
   6. **Continuidad y consistencia del WAL:** en `repo1` y en `repo2`, los segmentos desde el backup base más antiguo conservado hasta el del LSN de corte son contiguos, por timeline, con sus `.history`. Cada uno coincide byte a byte (SHA‑256) con el del servidor.
3. **Dependencias:** C6, para la confirmación en `repo1` y `repo2`, y C9‑B, para el bloqueo. Va después de C6.
4. **Pruebas PASS/FAIL, positivas y negativas:**
   - **C10‑1 (positiva):** un apagado limpio sin escrituras da PASS, con la lista de registros encontrados y la continuidad verificada en los dos repositorios.
   - **C10‑2 (negativa):** una escritura inyectada después del LSN de corte, con una sesión de superusuario que se salta el bloqueo, da FAIL y la nombra.
   - **C10‑3 (negativa):** un COMMIT de una transacción abierta antes del corte y confirmada después da FAIL.
   - **C10‑4 (negativa):** un apagado inmediato (`-m immediate`) o un corte del proceso dan FAIL.
   - **C10‑5 (negativa):** un segmento ausente, truncado o distinto en `repo1` o en `repo2` da FAIL y nombra el segmento.
   - **C10‑6 (negativa):** un hueco en la secuencia, o un `.history` faltante con un cambio de timeline, da FAIL.
   - **C10‑7 (positiva):** un cambio de timeline legítimo (después de una restauración con promoción) no da un falso FAIL.
   - **C10‑8:** la lista de registros permitidos se valida contra lo que PostgreSQL 17 escribe realmente, en CI con la versión fijada.
5. **Riesgos:** los nombres de registros de `pg_waldump` pueden cambiar entre versiones mayores. La versión se fija y C10‑5 lo detecta si cambia.
6. **CI / laboratorio:**
   - **En CI:** todo.
   - **PENDIENTE:** el mismo procedimiento con PostgreSQL como servicio en Windows 11 Pro y discos reales.

### C11. Cierre seguro de jornada

1. **Objetivo:** el comando `cierre-jornada`, que se usa por Escritorio remoto y es el único modo de apagado. Ejecuta los pasos 1 a 8 de §6 usando C9 y C10:
   1. chequeos previos: sin backup, subida, simulacro ni actualización en curso;
   2. bloqueo de escrituras (C9);
   3. espera de las operaciones pendientes;
   4. parada de backend y frontend;
   5. WAL (C10);
   6. parada de PostgreSQL y verificación;
   7. Store Sys, cuando su proveedor lo defina;
   8. registro y apagado.

   Si falla antes del paso 4, la app vuelve a quedar disponible. Si falla después, queda detenida, con aviso, y no se apaga sin resolver o sin registrar el riesgo, que consiste en qué LSN quedó solo en el equipo.
2. **Archivos:**
   - `districom_ops/day_close.py`;
   - un acceso directo para el operador;
   - `docs/WINDOWS_DEPLOYMENT.md` §6.
3. **Dependencias:** C5 (modo mantenimiento, C5‑3), C7 validada, C8, C9‑B y C10. Toma el bloqueo exclusivo de C9‑A durante todo el cierre.
4. **Pruebas PASS/FAIL:**
   - **C11‑1:** el camino feliz completo termina con un registro de cada paso y el último LSN fuera del local. En CI, el apagado de Windows se reemplaza por un comando registrado.
   - **C11‑2:** con una falla inyectada en cada paso, el resultado es el definido (app disponible o detenida con aviso).
   - **C11‑3:** sin Internet:
     - avisa;
     - permite esperar o seguir;
     - registra el riesgo con su LSN.
   - **C11‑4:** si hay un backup en curso, no empieza.
   - **C11‑5:** al arrancar al día siguiente:
     - los servicios vuelven;
     - con el cierre terminado, el bloqueo queda levantado; con uno incompleto, sigue bloqueado y alerta (C9);
     - el monitor envía «arranque OK».
5. **Riesgos:**
   - El cierre de Store Sys es desconocido: es una dependencia del proveedor.
   - Que el operador apague Windows por otro camino. Se mitiga con una directiva del botón de apagado y con el aviso del monitor al día siguiente.
6. **CI / laboratorio:**
   - **En CI:** C11‑1 a C11‑5, salvo el apagado real.
   - **PENDIENTE:**
     - el apagado y el encendido reales;
     - el arranque sin sesión;
     - el corte de energía con UPS;
     - Windows Update en el cierre;
     - la convivencia con el cierre de Store Sys.

### C12. Actualizaciones y rollback

1. **Objetivo:** `update` y `rollback` de §7 con las mismas protecciones que en Linux.

   `update` hace, en este orden:
   1. chequeos;
   2. versión nueva en `releases\<versión>`;
   3. ventana de mantenimiento y bloqueo (C9);
   4. backup completo etiquetado con huella;
   5. migraciones;
   6. cambio del enlace `current`;
   7. arranque;
   8. smoke con `/health/ready`.

   `rollback` vuelve al `current` anterior. `rollback --restore-db` tiene tres protecciones:
   - sin confirmación solo informa;
   - pide la etiqueta del backup para confirmar;
   - antes de restaurar hace un backup del estado que se descarta.
2. **Archivos:** `districom_ops/update.py` y `districom_ops/rollback.py`.
3. **Dependencias:** C2, C5 (modo mantenimiento, C5‑3), C6, C7 validada, C8 y C9‑B. `update` y `rollback` toman el bloqueo exclusivo de C9‑A.
   - El backup previo a la actualización se acepta solo si está verificado.
   - `rollback --restore-db` se juzga con los criterios de C7 e informa el punto **demostrado**.
   - Una actualización incompleta deja las escrituras bloqueadas (C9) hasta que se termina o se revierte.
4. **Pruebas PASS/FAIL:**
   - **C12‑1:** actualización de una versión A a una B con una migración nueva. En CI, A es el `main` actual y B una rama de prueba con una migración ficticia que **no** se mergea.
   - **C12‑2:** un smoke fallido deja el resultado en FAIL y no en «ok».
   - **C12‑3:** el rollback sin migración funciona.
   - **C12‑4:** `--restore-db` sin confirmación solo informa, tabla por tabla.
   - **C12‑5:** `--restore-db` con la etiqueta correcta:
     - restaura;
     - deja el backup pre‑rollback;
     - rechaza un segundo rollback de la misma actualización.
   - **C12‑6:** una actualización interrumpida en cada paso queda recuperable y con aviso del monitor.
5. **Riesgos:**
   - El enlace `current` en Windows es una *junction* o un enlace simbólico, y los servicios abren archivos en uso. Se cambia con los servicios detenidos.
   - Python y Node se instalan por versión.
6. **CI / laboratorio:**
   - **En CI:** todo.
   - **PENDIENTE:** la actualización real con datos de staging y los tiempos reales.

### C13. Instalador, suite de aceptación y restauración completa

1. **Objetivo:**
   - **`install.ps1`:** idempotente, con confirmaciones, que aplica C1 a C8 sobre un Windows limpio. No toca nada fuera de lo de DistriCom.
   - **Suite de aceptación:** sobre un runner recién instalado con `install.ps1`:
     - E2E críticos a través de Caddy HTTPS;
     - monitor en verde;
     - cierre de jornada;
     - arranque de servicios;
     - backup;
     - actualización y rollback.
   - **Restauración completa (pérdida total):** en **otro** runner, solo con `repo2` y la clave:
     - instalar;
     - restaurar al último punto recuperable;
     - comparar con `verify.sql` contra la huella del origen;
     - medir el RTO en el runner.
2. **Archivos:**
   - `deploy/windows/install.ps1`;
   - `deploy/windows/acceptance/`;
   - el job de aceptación de Windows;
   - `docs/WINDOWS_DEPLOYMENT.md` (instalación).
3. **Dependencias:** todas las anteriores.
4. **Pruebas PASS/FAIL:**
   - **C13‑1:** se instala desde cero y una segunda corrida no cambia nada.
   - **C13‑2:** los 107 E2E pasan a través de Caddy HTTPS sobre la instalación.
   - **C13‑3:** el ciclo completo funciona: backup, cierre, arranque, actualización y rollback.
   - **C13‑4:** la pérdida total se restaura en otro runner y da IDENTICAL, con los tres puntos de C7 y el recuperable **demostrado**. Es una simulación: la recuperación física real sigue PENDIENTE.
   - **C13‑5:** la desinstalación deja el equipo sin rastros de DistriCom y sin tocar reglas ni servicios ajenos.
5. **Riesgos:**
   - El tiempo del job, que se mide y se informa sin omitir pruebas.
   - Dos runners distintos necesitan un repositorio externo de prueba: un remoto `crypt` local o un artefacto del job, nunca un bucket real.
6. **CI / laboratorio:**
   - **En CI:** C13‑1 a C13‑5.
   - **PENDIENTE (W3, laboratorio Windows 11 Pro):**
     - toda la suite en Windows 11 Pro;
     - reinicios y cortes de energía reales;
     - Defender;
     - disco desconectado;
     - el bucket real con bloqueo de objetos;
     - el acceso LAN y Tailscale;
     - el esquema real con datos de staging;
     - los tiempos de RPO y RTO;
     - la convivencia con Store Sys, en un laboratorio que la simule, nunca en el equipo donde funciona.

## 3. Qué queda fuera de W2‑C

- Preparar el equipo (W4): RAM, RAID, Windows 11 Pro, BitLocker y UPS.
- Contrataciones: dominio, Cloudflare, bucket y tailnet.
- Staging y go‑live (W5 a W8) y la llegada de Store Sys (W9).
- Pasar el ADR 016 a «Aceptado»: solo después de W2‑C y del laboratorio, con decisión de Enzo.
- Cambios en la infraestructura Linux, que sigue vigente y sin modificaciones.

## 4. Decisiones

Las cinco decisiones D1 a D5 quedaron confirmadas por Enzo el 2026-11-06 (ver §1).

**Decisiones de C9, confirmadas por Enzo el 2026-11-11** (detalle en §2, C9‑B):
- **Propiedad:** `districom_ops` es dueño de `districom`, sin ser superusuario. La app deja de ser dueña, y su acceso a otras bases se restringe solo para ella, sin revocaciones globales.
- **Restablecimiento automático:** solo después de un cierre completado y verificablemente seguro. En cualquier otro caso, el sistema queda bloqueado hasta una recuperación manual verificada y auditada.
- **División:** C9‑A y C9‑B en PR separados.

**Autorizado:**
- esta revisión 2 de la propuesta y el ROADMAP, en un PR solo documental, que no se mergea sin la aprobación de Enzo;
- una vez mergeada, **solo C9‑A**, en su rama y su PR, sin merge automático.

**No autorizado:**
- la implementación de C9‑B, aprobada conceptualmente, que necesita una autorización nueva después de revisar C9‑A;
- C5 a C8 y C10 a C13;
- servidores, hardware, Store Sys, producción, infraestructura Linux, compras, contrataciones y despliegues.

**Sigue abierto:**
- A‑14;
- ADR 016 «Propuesto»;
- todas las pruebas físicas, PENDIENTES.
