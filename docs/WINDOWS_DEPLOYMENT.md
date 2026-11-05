# Despliegue de DistriCom en Windows 11 Pro (plan)

**Estado:** plan de la propuesta del **ADR 016 (Propuesto)**. Nada de esto está implementado ni instalado. Mientras el ADR 016 no se acepte, la operación vigente es la de Linux (ADR 013, `docs/OPERATIONS.md`, `deploy/`), que no se modifica.

**Reglas de este plan:**
- Cada etapa tiene su rama, su PR, sus pruebas y sus criterios PASS/FAIL. Ningún merge sin aprobación de Enzo.
- Las pruebas que el CI no reproduce de forma suficiente quedan **PENDIENTES**, nunca PASS. Se completan en un laboratorio Windows 11 Pro adecuado antes de aprobar la instalación productiva.
- **No se usa el servidor donde funciona Store Sys** para ensayos destructivos, desconexiones de discos ni cortes de energía.
- Solo datos ficticios en las pruebas; el esquema real de DistriCom se valida en una etapa posterior.
- Sin cambios funcionales en Comercial ni en Rendiciones.

## 1. Equipo y sistema

| Ítem | Situación | Necesario antes de instalar |
|---|---|---|
| Placa y CPU | ASUS PRIME Z390‑A, Intel Core i3‑9100 (4 núcleos) | Requisitos de Store Sys pedidos a su proveedor, para confirmar que alcanza |
| RAM | 8 GB; ampliación con 16 GB prevista, pendiente de verificación | 16 GB como mínimo para los dos sistemas; 24–32 GB recomendado |
| ECC | No soportada por la placa | Riesgo aceptado (ADR 016, garantía 3) |
| Discos de datos | RAID 1 de Intel RST, 250 GB, a verificar | Confirmar que son dos discos reales en espejo, su estado SMART y el monitoreo de Intel RST |
| Disco de backup | WD Purple 1 TB, a verificar | Estado SMART y horas de uso. Es `repo1` |
| Sistema | Windows 10 Pro 22H2, sin soporte | **Windows 11 Pro** (TPM 2.0 por Intel PTT, Secure Boot, disco GPT/UEFI), BitLocker con la clave al resguardo |
| UPS | No informada | Para el servidor y el router/ONT, con apagado ordenado |

**El equipo no se modifica** hasta que Enzo lo autorice (etapa W4).

## 2. Arquitectura de servicios

| Servicio | Programa | Escucha en | Cuenta | Arranque |
|---|---|---|---|---|
| Base de datos | PostgreSQL 17 (instalador oficial) | `127.0.0.1:5432` | Cuenta de servicio de PostgreSQL | Automático, solo si está el disco de backup |
| Backend | Python 3.12 + uvicorn, con WinSW | `127.0.0.1:8000` | Cuenta de servicio virtual propia | Automático, después de la base |
| Frontend | Node 22 + `node server.js`, con WinSW | `127.0.0.1:3000` | Cuenta de servicio virtual propia | Automático, después del backend |
| HTTPS | Caddy con `caddy-dns/cloudflare`, con WinSW | `:443` y `:80` | Cuenta de servicio virtual propia | Automático, después del frontend |
| Acceso remoto | Tailscale, modo desatendido | Interfaz de Tailscale | Servicio de Tailscale | Automático |
| Tareas | Programador de tareas | — | Cuenta de servicio de backups | Ver §5 |

- Caddy envía `/api/*` a `127.0.0.1:8000` y el resto a `127.0.0.1:3000`. El Caddyfile actual se reutiliza con los destinos tomados de variables.
- `FORWARDED_ALLOW_IPS=127.0.0.1`. `API_INTERNAL_URL=http://127.0.0.1:8000` al compilar el frontend.
- La base se crea en **UTF‑8 con ICU `es-AR`** y `--data-checksums`, con los parámetros de log seguros de O7.

## 3. Carpetas y permisos

```
C:\DistriCom\
  releases\<versión>\   backend y frontend de cada versión
  current               enlace a la versión en uso
  config\               variables y secretos (NTFS: solo las cuentas de servicio y el grupo IT)
  logs\
D:\ (disco de backup)
  .districom-backup-disk   marca de disco preparado
  repo1\                   backups base y WAL archivado
```

- Permisos NTFS restringidos a las cuentas de servicio de DistriCom y al grupo IT de la empresa.
- Exclusiones de Microsoft Defender para los datos de PostgreSQL, `pg_wal` y `repo1`, documentadas y verificadas en laboratorio.
- **Store Sys y su proveedor** usan cuentas propias, sin permisos sobre estas carpetas y sin administrador permanente.

## 4. Red y firewall

| Puerto | Desde | Para |
|---|---|---|
| 443 | Subred LAN y `100.64.0.0/10` | La app |
| 80 | Ídem | Redirección a 443 |
| 3389 | Subred LAN y Tailscale (grupo IT) | Escritorio remoto, con NLA y cuentas nominales |
| 5432, 8000, 3000 | Solo `127.0.0.1` | Internos |
| Puertos de Store Sys | Según su proveedor | A definir |

Nada expuesto a Internet; el router no reenvía puertos. La política de Tailscale es la misma que la de Linux.

## 5. Backups y recuperación

### 5.1 Objetivo
Conservar las capacidades del diseño Linux:
- restaurar a cualquier momento (PITR);
- una copia local y otra fuera del local, cifradas;
- inmutabilidad externa;
- simulacros con comparación exacta.

`pg_dump` queda como formato secundario y portable, **nunca** como reemplazo del PITR.

### 5.2 Piezas

| Pieza | Cómo |
|---|---|
| Archivado de WAL | `archive_command` propio hacia `repo1`: copia con nombre temporal, `fsync` y renombre, con reintentos si Windows tiene el archivo abierto. Mismo contenido: OK; contenido distinto: error, nunca sobrescribe. `archive_timeout = 60 s` |
| Copia fuera del local | Tarea asíncrona con `rclone crypt` hacia el bucket (`repo2`). Un corte de Internet no frena la base. Registra hasta qué segmento está fuera del local |
| Backup base | `pg_basebackup` en formato **plain** con manifiesto, diario de 15 a 17 (`pg_verifybackup` de PostgreSQL 17 solo verifica ese formato) |
| Verificación | `pg_verifybackup` después de cada backup; alerta si falla |
| PITR | Instancia nueva con el backup base, `restore_command` contra `repo1` o `repo2`, `recovery_target_time` o `recovery_target_name` |
| Huecos | Revisión de la secuencia de segmentos de cada repositorio **por timeline**, desde el inicio del backup base más antiguo conservado, incluidos los archivos `.history` |
| Retención | Se conserva cada backup base junto con **todos** los segmentos y archivos `.history` que necesita para restaurarse; nunca se borra lo que necesita el backup más antiguo que se conserva |
| Punto diario | `pg_create_restore_point('cierre-AAAAMMDD')` en el cierre de jornada |
| Dump lógico | `pg_dump` diario, cifrado, a los dos repositorios |

### 5.3 Qué garantiza y qué no
Ninguna de estas capas garantiza la ausencia de pérdida frente a **toda** falla física, eléctrica o la pérdida del equipo. Cada una protege contra fallas distintas:

| Capa | Qué protege | Qué **no** protege |
|---|---|---|
| **Durabilidad local** (WAL de PostgreSQL con `fsync` al confirmar) | Una caída del proceso o de Windows, o un corte de energía, **si** los discos y la controladora respetan el vaciado de su caché | Discos o controladora que mienten sobre el vaciado, corrupción del sistema de archivos, falla de ambos discos, robo, incendio o sobretensión |
| **RAID 1** (Intel RST) | La falla de **un** disco de datos | Corrupción o borrado (se replican al espejo), falla de la controladora o del driver, falla simultánea de los dos discos, pérdida del equipo |
| **Archivado en `repo1`** (disco de backup) | Pérdida o corrupción de los discos de datos: se recupera hasta el último segmento WAL **archivado** | Lo confirmado y todavía no archivado (hasta `archive_timeout`); la pérdida del equipo entero, porque `repo1` está en el mismo gabinete |
| **Recuperación externa desde `repo2`** | La pérdida total del local | Lo que no llegó a `repo2`: hasta `archive_timeout` más el atraso de la subida, o más si no había Internet |

- **Una transacción confirmada no es lo mismo que una transacción recuperable.** Es recuperable desde un repositorio solo cuando el segmento WAL que la contiene ya está archivado en él y ese repositorio se puede leer.
- **No hay RPO cero** frente a una pérdida de los discos de datos, del equipo o del local.
- El cierre de jornada (§6) reduce el riesgo de la noche, pero solo si se usa y termina bien.

### 5.4 Simulacros
Instancia descartable con `pg_ctl`, en otra carpeta y otro puerto, comparada con el origen en el mismo punto con `deploy/sql/verify.sql` y `pg_amcheck`. Si la copia no llega al punto, el resultado es INCOMPLETE. Mensual desde `repo1`; trimestral desde `repo2` en **otro Windows**.

### 5.5 RPO y RTO objetivo

| Escenario | RPO | RTO objetivo |
|---|---|---|
| Error humano o corrupción | Al minuto elegido | 1–2 h |
| Actualización fallida | Hasta el backup previo a la actualización, tomado con la app detenida | < 30 min |
| Falla **un** disco de datos | Sin pérdida esperada **si** el espejo estaba sano y sincronizado (se verifica en el monitor) | Sin corte esperado; reemplazo del disco |
| Fallan los discos de datos o muere el equipo, con `repo1` legible | Lo archivado en `repo1` | < 1 día, en otro Windows |
| Pérdida total del local | Lo que llegó a `repo2` (1–5 min con Internet y cierre de jornada completo) | < 1 día hábil, en otro Windows |

Son objetivos a medir en las pruebas, no garantías ya verificadas.

## 6. Operación diaria

**Arranque:**
- servicios automáticos en orden, sin iniciar sesión;
- chequeo del disco de backup antes de iniciar PostgreSQL;
- el monitor envía «arranque OK» al canal de alertas.

**Durante el día:**
- monitor periódico (equivalente a `deploy/bin/monitor`): app, base, migración, archivado de WAL, atraso de `repo2`, huecos, espacio y RAID;
- backup base de 15 a 17.

**Cierre de jornada** (`cierre-jornada`, por Escritorio remoto), el único modo de apagado. El orden evita carreras: primero se cierra la entrada de escrituras, y la verificación final se hace cuando ya nadie puede escribir.

1. **Chequeos previos:** no hay backup base, subida, simulacro ni actualización en curso. Si hay, no empieza.
2. **Impedir nuevas escrituras:**
   - abre la ventana de mantenimiento (el monitor no alerta);
   - Caddy responde «en mantenimiento» a todo pedido nuevo;
   - se suspenden las tareas programadas que escriben (importaciones, dump, simulacros);
   - se quita el permiso de conexión nueva a la base a los roles de la app (`REVOKE CONNECT`); las sesiones abiertas siguen hasta terminar.
3. **Esperar que terminen las operaciones pendientes:** pedidos en curso, importaciones y transacciones de escritura (`pg_stat_activity`), con un tiempo máximo. Si vence, **no fuerza**: avisa, revierte el paso 2 y deja la app funcionando.
4. **Detener backend y frontend.** Desde aquí no queda ningún proceso de la app que pueda escribir.
5. **Verificar y sincronizar el WAL:**
   - crea el punto de restauración del día y fuerza el cierre del segmento (`pg_switch_wal`), y registra el LSN;
   - comprueba que no haya ninguna sesión de escritura, salvo la del propio cierre;
   - espera la confirmación de que todos los segmentos hasta ese LSN están en `repo1` **y** en `repo2`;
   - sin Internet o con `repo2` atrasado: avisa y deja elegir entre esperar o seguir con el riesgo **registrado** (qué LSN quedó solo en el equipo).
6. **Detener PostgreSQL** con un apagado limpio. Con `pg_controldata` verifica que lo único posterior al LSN confirmado sea el checkpoint de apagado. Si hay algo más, no apaga: vuelve a arrancar la base sin la app, repite el paso 5 y avisa.
7. **Store Sys:** según defina su proveedor (pendiente). Su cierre también tiene que terminar bien antes de apagar.
8. **Registrar el cierre y apagar.** Registra la hora, el último LSN fuera del local y el estado de cada paso. El mismo comando apaga Windows de inmediato, sin volver a levantar servicios entre la verificación y el apagado.

Si un paso falla antes del 4, la app vuelve a quedar disponible. Si falla después, los servicios quedan detenidos y se avisa al responsable: no se apaga hasta resolverlo o registrar el riesgo.

**Requisitos obligatorios para validar W2‑C** (observaciones de Enzo del 2026-11-04). La secuencia de cierre no se considera segura hasta resolverlos y probarlos:
1. **Bloqueo de escrituras efectivo y reversible.** `REVOKE CONNECT` no desconecta las sesiones existentes, y puede no alcanzar si otros roles conservan el permiso por `PUBLIC`. Hay que diseñar un bloqueo que:
   - corte también las sesiones abiertas;
   - cubra todos los roles que pueden escribir;
   - se pueda revertir;
   - se recupere tras un error a mitad del cierre;
   - se restablezca solo en el siguiente arranque.
2. **Comprobación correcta del WAL y del apagado limpio.** `pg_controldata` no alcanza por sí solo para certificar que no hubo escrituras posteriores a un LSN. Hay que diseñar y probar una comprobación correcta del WAL hasta el apagado, por ejemplo con el contenido real del WAL posterior al LSN y el estado del clúster.

**Windows Update:** horas activas y aplazamiento por directiva; parches en el cierre de jornada.

## 7. Actualización y rollback
`update`: backup completo etiquetado → detener servicios → nueva carpeta de versión → `alembic upgrade head` → cambiar `current` → arrancar → smoke. `rollback`: volver al `current` anterior y, si hace falta, restaurar la base del backup previo, con las mismas confirmaciones que en Linux.

## 8. Etapas

| Etapa | Qué | Dónde se prueba | Estado |
|---|---|---|---|
| **W1** | ADR 016 (Propuesto) y este plan | CI (documentación) | DONE (PR #70, 2026-11-04) |
| **W2‑A** | Prueba de concepto aislada de backups físicos, WAL, PITR y restauración (`deploy/windows/backup/`) | GitHub Actions (Windows); lo no reproducible queda PENDIENTE | DONE como prueba de concepto y CI (PR #72, 2026-11-05); lo de laboratorio sigue PENDIENTE: ver §8.2 |
| **W2‑B** | Pruebas automatizadas de los componentes compatibles con Windows (backend en Windows, orden de textos, frontend) | GitHub Actions (Windows) | Por definir |
| **W2‑C** | El resto (operación, `install.ps1`, servicios, firewall, cierre de jornada, suite de aceptación) | CI y laboratorio Windows 11 Pro | Se planifica solo después de validar W2‑A |
| W3 | Laboratorio Windows 11 Pro: suite completa y pruebas pendientes | PC de laboratorio (no el servidor) | Sin laboratorio confirmado |
| W4 | Preparación del equipo: RAM, RAID, SMART, Windows 11 Pro, BitLocker, UPS | Servidor | No autorizado |
| W5–W8 | Staging, pruebas en el servidor, transición y go‑live (O10) | Servidor | Equivalen a S3 |
| W9 | Llegada de Store Sys y pruebas con los dos sistemas | Servidor | Depende del proveedor |

### 8.0 Go‑live del servidor compartido
- El servidor compartido se da por aceptado **solo** cuando están validados los dos sistemas funcionando **a la vez**: concurrencia, rendimiento, arranque, cierre de jornada, energía, actualizaciones y backups de ambos.
- Si Store Sys se migra **después** del primer go‑live de DistriCom (W8), ese go‑live vale solo para DistriCom en el equipo sin Store Sys. Al llegar Store Sys (W9), el sistema consolidado necesita **nuevas pruebas y una nueva aprobación de Enzo** antes de considerarse aceptado.
- Mientras tanto, no se hacen en ese equipo ensayos destructivos, desconexiones de discos ni cortes de energía con Store Sys funcionando.

### 8.1 Dónde se puede probar cada cosa
- **GitHub Actions (`windows-latest`):** lógica y herramientas de PostgreSQL en Windows, archivado, backups, PITR, restauración, huecos, retención, cifrado contra un destino local. Los runners son **Windows Server**, no Windows 11 Pro.
- **Laboratorio Windows 11 Pro (una PC de prueba):** PostgreSQL como servicio y sus permisos, Defender, arranque sin sesión, Programador de tareas, BitLocker, Windows Update, desconexión real de un disco, corte de energía real, bucket remoto real.
- **Servidor físico (S3):** Intel RST RAID 1, discos reales, tiempos reales, convivencia con Store Sys, UPS. Sin ensayos destructivos mientras Store Sys funcione en él.

### 8.2 W2‑A: prueba de concepto
- **Estado (Enzo, 2026-11-05):**
  - completada **a nivel de prueba de concepto y CI** (PR #72, merge `f2a7555`);
  - no valida nada de lo PENDIENTE de abajo, que es **condición previa a producción**;
  - ADR 016 sigue «Propuesto».
- **Dónde está:** `deploy/windows/backup/` (README), un paquete Python que solo usa la biblioteca estándar. Nada de la aplicación ni del despliegue Linux lo usa.
- **Cómo corre:** en el job de CI «Windows backup PoC (W2-A)», sobre `windows-latest`, con los binarios oficiales de PostgreSQL 17.6 y `rclone`, todo con datos ficticios.
- **Qué se probó:**
  - criterios A‑1 a A‑17, más calidad (A‑18) y diseño Linux intacto (A‑19);
  - el reporte del job da PASS o FAIL por criterio y lista lo PENDIENTE.
- **Resultado en CI (2026-11-05, commit `7217b24`):**
  - entorno: Windows Server 2025 (`windows-latest`), PostgreSQL 17.6 y Python 3.12;
  - 56 de 56 tests en verde, y A‑1 a A‑17 en PASS;
  - A‑18: ruff, ruff format y mypy estricto en verde;
  - A‑19: los 4 jobs existentes en verde, y el diff solo toca este paquete, el job nuevo y la documentación.
  - En A‑4, A‑5, A‑11 y A‑13 el PASS cubre solo lo que se reproduce en CI; la variante real de cada uno sigue PENDIENTE.
- **Mediciones en el runner (no son límites ni una predicción del servidor):**
  - `archive_timeout` efectivo de 63 s;
  - base de 1 GB: el backup base con verificación tarda 22,5 s y la restauración (copia, recuperación y promoción) 40,2 s;
  - A‑11, corte durante escrituras:
    - 836 transacciones confirmadas;
    - 563 archivadas, y las mismas 563 recuperadas desde el repositorio;
    - 273 confirmadas que no estaban archivadas en el momento del corte; el reinicio local las conserva todas;
    - esto confirma que el RPO desde el repositorio no es cero.
- **Decisiones que surgieron de las pruebas:**
  - **Segmento del cambio de timeline:** el que contiene el punto de cambio existe en las dos timelines, y recuperar con cualquiera de las dos copias alcanza. Que falte solo una no es un hueco.
  - **Huecos sin objetivo:** si se restaura hasta el final y hay un hueco, PostgreSQL se detiene en él y promueve sin avisar. Por eso la herramienta revisa los huecos antes de restaurar y, en ese caso, informa **INCOMPLETE**, nunca COMPLETE.
  - **Configuración heredada:** el backup de un servidor restaurado trae su configuración de recuperación. La restauración la limpia explícitamente antes de fijar el objetivo nuevo.
  - **Directorios vacíos:** `rclone` no copia directorios vacíos, y un backup base los necesita. El manifiesto de la copia externa los registra y se recrean al traerla.
  - **Texto en Windows:**
    - las herramientas de PostgreSQL se leen siempre en UTF‑8 (`PGCLIENTENCODING`);
    - el SQL con texto no ASCII se envía por entrada estándar, porque Windows pasa los argumentos de línea de comandos en la página de códigos ANSI.
  - **Salida de `pg_ctl start`:** va a un archivo y nunca a una tubería, porque el servidor hereda el handle y la lectura no terminaría nunca.
  - **Reinicio tras un corte:** los procesos del servidor terminado pueden retener el log un instante. El arranque reintenta solo ese error, durante 60 s como máximo.
  - **Archivos abiertos por otro proceso en Windows:** se cubren con nombre temporal y renombre con reintentos. Con Defender real queda PENDIENTE.
- **PENDIENTE, nunca PASS:**
  - Defender real;
  - un disco desconectado de verdad;
  - un corte de energía real;
  - el bucket real con bloqueo de objetos y retención;
  - PostgreSQL como servicio de Windows;
  - Windows 11 Pro;
  - el esquema real de DistriCom.

  Se completan en un laboratorio adecuado antes de aprobar la instalación productiva, nunca en el servidor donde funciona Store Sys.

### 8.3 Requisitos de backups para el desarrollo posterior
Enzo los fijó el 2026-11-05 al cerrar W2‑A. Se suman a los dos requisitos del cierre de jornada de §6: el bloqueo de escrituras reversible y la verificación correcta del WAL.
1. **Retención contra restauraciones completas:**
   - después de aplicar la retención, restaurar completo **cada** backup conservado, no solo algunos, incluidos los de otras timelines;
   - comparar cada uno con su estado esperado.
2. **Consistencia con archivado en curso:** comprobar que los manifiestos y las copias externas siguen siendo consistentes y verificables mientras PostgreSQL sigue archivando WAL. Un segmento que llega durante la copia no puede dejar un manifiesto inválido ni una copia que no se pueda restaurar.
3. **Criterios precisos de COMPLETE, INCOMPLETE y FAILED:**
   - definir por escrito qué condiciones dan cada resultado;
   - informar siempre el **último punto realmente recuperable** (LSN, hora y timeline);
   - distinguirlo del punto pedido y del último WAL archivado.

## 9. Pendientes que no dependen del desarrollo
- Requisitos de Store Sys (RAM, disco, puertos, su base de datos, cómo se detiene).
- Acceso del proveedor de Store Sys: cuenta, permisos, supervisión y ventanas de cambio.
- **Licencias de Windows 11 Pro.** Sus términos limitan a **20 dispositivos** el acceso a ciertos servicios **del propio Windows**: archivos e impresión compartidos, Internet Information Services, conexión compartida y telefonía. Ese límite **no equivale** al número de usuarios web de DistriCom, que se conectan a Caddy y no a esos servicios. Sí puede aplicar a Store Sys si usa carpetas compartidas de Windows, y el Escritorio remoto de Windows cliente admite una sola sesión interactiva a la vez. A confirmar con el proveedor de licencias y con el de Store Sys; si no alcanza, se evalúa Windows Server.
- Laboratorio Windows 11 Pro.
- Régimen de fin de semana y encendido diario.
- Del plan S3 v2 siguen vigentes: dominio, IdP, Tailscale, bucket con bloqueo de objetos (pruebas R2‑1 a R2‑7), alertas, responsable y suplente, áreas protegidas antes del go‑live.
