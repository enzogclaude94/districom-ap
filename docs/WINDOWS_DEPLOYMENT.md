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

Desde W2‑C4 (`districom-ops firewall apply|verify|remove`, §8.5) las reglas de DistriCom están en el grupo `DistriCom` y se llaman `DistriCom-*`. Nunca se crean, cambian ni quitan reglas de Store Sys, del Escritorio remoto de Windows ni de otros programas, ni los perfiles del firewall.

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
| **W2‑B** | Pruebas automatizadas de los componentes compatibles con Windows (backend en Windows, orden de textos, frontend) | GitHub Actions (Windows) | DONE a nivel de compatibilidad y pruebas automatizadas (PR #75, 2026-11-06); lo de laboratorio sigue PENDIENTE: ver §8.4 |
| **W2‑C** | El resto (operación, `install.ps1`, servicios, firewall, cierre de jornada, suite de aceptación) | CI y laboratorio Windows 11 Pro | En curso por subetapas (C1 a C13, ver §8.5): C1 DONE; C2 y C3 DONE en implementación y CI (Windows 11 Pro real PENDIENTE); C4 en curso; C5 a C13 sin autorizar |
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

### 8.4 W2‑B: compatibilidad de la aplicación
- **Estado (Enzo, 2026-11-06):** DONE a nivel de compatibilidad y pruebas automatizadas (PR #75, merge `b42efa0`). No valida lo PENDIENTE de abajo. ADR 016 sigue «Propuesto».
- **Dónde está:** tres jobs de CI sobre `windows-latest` con PostgreSQL 17.6 (ICU `es-AR`, logs de O7, bases descartables y datos ficticios), la acción `.github/actions/windows-postgres` y los chequeos de `deploy/windows/compat/` (README). Los cinco jobs anteriores no cambian.
- **Resultados en CI (B‑1 a B‑10 en PASS):**

  | Criterio | Qué | Resultado |
  |---|---|---|
  | B‑1 | Suite completa del backend (1965 tests) | PASS |
  | B‑2 | `upgrade head`, `downgrade base`, `upgrade head`, `alembic check` | PASS |
  | B‑3 | `ruff`, formato, `mypy` estricto, `lint-imports` | PASS |
  | B‑4 | Orden ICU `es-AR` en los listados que ordena el backend (difiere de en‑US en la ñ) | PASS |
  | B‑5 | `America/Argentina/Buenos_Aires` con `tzdata` explícito (Windows no trae base de zonas) | PASS |
  | B‑6 | uvicorn en modo producción: health, login, cookies, parada ordenada, config insegura rechazada, logs sin secretos | PASS |
  | B‑7 | Frontend: instalación, lint, formato, tipos, 377 tests y build | PASS |
  | B‑8 | Frontend standalone con `node server.js`: headers, CSP y rewrite de `/api` | PASS |
  | B‑9 | Los 107 flujos E2E, más la entrega parcial de boca repetida 10 veces | PASS |
  | B‑10 | `verify.sql`, logs seguros de O7 y Caddyfile en modo interno | PASS |

- **Incompatibilidades encontradas y corregidas (autorización de Enzo, 2026-11-06):**
  - **Plataforma:** en Windows, el FATAL de `idle_in_transaction_session_timeout` puede perderse al cerrarse el socket; el driver ve la conexión cerrada sin SQLSTATE y el backend respondía un 500. Ahora ese caso verificable (sin SQLSTATE y con la conexión invalidada) es 503 `database_connection_lost`, que pide revisar el resultado antes de reintentar. 25P03 sigue siendo `database_timeout`; no hay reintentos automáticos (`docs/SECURITY.md`).
  - **Rendiciones:** el diálogo «Entregar efectivo» tomaba la caja receptora solo al abrirse; si las cajas llegaban después, el campo quedaba vacío. Ahora completa la caja principal cuando llega, sin pisar una elección del operador; sin caja principal activa no preselecciona ninguna.
  - Ajustes solo de tests para Windows: variable `SYSTEMROOT`, lectura en UTF‑8 y permisos de solo lectura.
- **PENDIENTE (no se puede dar PASS en CI):** Windows 11 Pro (los runners son Windows Server), servicios de Windows (PostgreSQL como servicio; backend, frontend y Caddy con WinSW: W2‑C), Defender, rendimiento en el hardware real y convivencia con Store Sys. Caddy con el módulo de Cloudflare se valida en W2‑C o en el laboratorio.

### 8.5 W2‑C: infraestructura por subetapas
- **Plan:** propuesta W2‑C revisión 1, aprobada por Enzo el 2026-11-06 con las decisiones:
  - **D1:** Python con biblioteca estándar, más PowerShell para instalación, ACL y firewall;
  - **D2:** WinSW;
  - **D3:** `ALLOW_CONNECTIONS false` con diario de estado;
  - **D4:** Caddy compilado con `xcaddy`;
  - **D5:** subetapas secuenciales, cada una con su PR, su CI y aprobación expresa antes del merge.
- **Orden:** C1 → C2 → C3 → C4 → C9 → C6 → C10 → C7 → C8 → C5 → C11 → C12 → C13. C11, C12 y C13 exigen C7 validada.
- **C1, base operativa** (`deploy/windows/ops/`, README; job de CI «Windows ops (W2-C)»). **DONE**: PR #79, merge `98a201b`, aprobada por Enzo el 2026-11-09.
  - Carpetas de la instalación.
  - Configuración que rechaza variables faltantes o inseguras sin mostrar valores.
  - Logs JSON sin secretos ni datos personales.
  - Registro de operaciones de solo agregado.
  - Ventana de mantenimiento con su operación dueña. El vencimiento genera una alerta, pero nunca cierra la ventana ni habilita escrituras.
  - Lectura estricta del registro: una línea estructuralmente inválida deja incierta su operación, o todo el registro; solo se tolera la última línea cortada por un corte. El archivo nunca se reescribe.
  - Verificación conjunta `writes_resume_check` (ventana + registro): una ventana ausente con una operación incompleta o incierta **no** permite reactivar escrituras. **C9 debe consultarla** antes de reactivar la aplicación; C1 no desbloquea nada.
  - Criterios C1‑1 a C1‑4.
  - PENDIENTE: los permisos NTFS (C3), Windows 11 Pro y un corte de energía real.
- **C2, servicios nativos** (`deploy/windows/ops/`, README §C2; job de CI «Windows services (W2-C2)»). **DONE a nivel de implementación y CI** (PR #80, merge `91b3092`, Enzo 2026-11-09). No declara validada la operación en Windows 11 Pro real.
  - Cinco servicios, cada uno con su cuenta virtual `NT SERVICE\<nombre>` (sin contraseña ni permisos de administrador):
    - `DistriComPostgreSQL`: `pg_ctl register`, inicio manual;
    - `DistriComBackend`, `DistriComFrontend` y `DistriComCaddy`: WinSW 2.12, inicio manual;
    - `DistriComStack`: WinSW, inicio automático diferido. Verifica el disco de backup y arranca la cadena en orden.
  - **PostgreSQL no es dependencia de ningún servicio en el SCM, no tiene inicio automático y el SCM no lo reinicia.** Si lo fuera, Windows lo arrancaría sin verificar el disco: al iniciar, como dependencia o después de una caída. Solo lo arranca el Stack, o un administrador a mano. El Stack lo reinicia después de una caída, siempre después de volver a verificar el disco y como máximo tres veces por hora.
  - Orden por disponibilidad:
    - el backend arranca cuando PostgreSQL acepta conexiones;
    - el frontend, cuando el backend responde `/health/live`;
    - Caddy, cuando responde el frontend.
    La parada es en orden inverso. PostgreSQL se detiene cuando no queda ninguna sesión de la app.
  - WinSW reinicia el backend, el frontend y Caddy después de una caída: tres veces, y después deja de intentar y alerta. El contador se reinicia al cabo de una hora.
  - **Identidad del disco de backup.** Una marca o una letra no prueban identidad. Antes de arrancar PostgreSQL se verifica que coincidan:
    - el id de la marca con `BACKUP_DISK_ID`;
    - el GUID del volumen con el registrado al preparar el disco.
    Además, el volumen tiene que ser distinto del de sistema y del de los datos. El número de serie físico queda PENDIENTE para el laboratorio, porque leerlo requiere privilegios. Si el disco se monta tarde, el Stack lo espera `BACKUP_DISK_WAIT_SECONDS`.
  - Los runtimes (WinSW, PostgreSQL, Caddy y Node) están fijados por versión y checksum en `templates/versions.json`. Una descarga con otro hash se borra sin extraer.
  - **Puertos de Caddy:** 80 y 443 por defecto; `HTTP_PORT` y `HTTPS_PORT` los cambian. En el runner de GitHub el puerto 80 lo ocupa el proceso System (PID 4, `http.sys`), así que el CI usa 8080 y 8443. En el servidor real hay que comprobar antes qué programa usa 80 y 443 (IIS, `http.sys` o Store Sys): PENDIENTE de laboratorio.
  - Permisos: desde C3, `services install` aplica las ACL definitivas (abajo).
  - PENDIENTE, nunca PASS:
    - el arranque después de un reinicio real y sin iniciar sesión;
    - Windows 11 Pro;
    - el disco desconectado físicamente (en CI se desconecta un disco virtual);
    - la parada durante el apagado de Windows (C10 y C11).
- **C3, cuentas de servicio y permisos NTFS** (`deploy/windows/ops/`, README §C3; job de CI «Windows permissions (W2-C3)»). DONE a nivel de implementación y CI (PR #81, aprobada por Enzo el 2026-11-10); Windows 11 Pro real PENDIENTE.
  - La raíz de la instalación **no hereda de `C:\`** (que por defecto deja escribir a cualquier usuario que inició sesión): queda protegida, con Administradores y SYSTEM. Cada cuenta virtual recibe solo lo suyo:
    - `runtime\<runtime>`: lectura y ejecución, solo los servicios que lo usan;
    - `services\<servicio>.exe/.xml`: lectura y ejecución, ese servicio;
    - la release en uso: lectura y ejecución (`backend` → backend, `frontend` → frontend, `windows` → Stack). **Ningún servicio escribe una release, un runtime ni una definición de servicio**;
    - `config\`: solo administradores. `districom.env` lo lee el Stack, `config\backend\` el backend, `Caddyfile` Caddy. `postgres.secret` e `integrity.json`, nadie más;
    - los datos de PostgreSQL: solo PostgreSQL (control total);
    - `data\uploads`: el backend (modificar);
    - `logs\` y `state\`: el Stack (modificar). Cada servicio escribe sus logs en su propia carpeta protegida (`logs\services\<servicio>`, `logs\postgresql`); `state\caddy` (con las claves de Caddy) es solo de Caddy;
    - la marca del disco de backup: lectura para el Stack;
    - cada carpeta por encima de un permiso, hasta la raíz, y la carpeta de datos de PostgreSQL para el Stack: solo «atravesar» (leer atributos, sin listar ni heredar). Node.js lee los atributos de cada carpeta de una ruta y el Stack comprueba el volumen de los datos de PostgreSQL sin poder leerlos.
  - «Modificar» nunca incluye cambiar permisos, tomar posesión ni borrar lo de otros.
  - **`.next\cache`:** Next.js escribe ahí al servir. Es una junction a `state\frontend-cache`, del frontend, así la release sigue de solo lectura.
  - **Junctions:** nunca se siguen al recorrer. Si una apunta fuera de su release, `permissions apply` se niega sin cambiar nada.
  - Las ACL se escriben y se verifican por SID con `advapi32`, leyendo las listas efectivas de cada objeto. Aplicarlas dos veces no cambia nada, y los permisos sueltos debajo de una regla (por ejemplo, los de C2) se quitan.
  - **Integridad de ejecutables:** `config\integrity.json` guarda el SHA‑256 de runtimes, envoltorios y release. `integrity verify` detecta un cambio aunque lo haga un administrador.
  - No cambia las ACL generales del sistema ni las de otros programas: solo la raíz de DistriCom, los datos de PostgreSQL y la marca del disco de backup.
  - PENDIENTE, nunca PASS:
    - las cuentas y el grupo de IT reales del cliente (hoy: Administradores);
    - que Store Sys y su proveedor no tengan acceso, comprobado con sus cuentas reales;
    - la cuenta de backups y la carpeta de backups en el disco (C6);
    - Windows 11 Pro y Defender.
- **C4, Firewall de Windows** (`deploy/windows/ops/`, README §C4; job de CI «Windows firewall (W2-C4)»). En curso, sin merge.
  - Reglas propias, en el grupo `DistriCom`:
    - `DistriCom-HTTP-In` y `DistriCom-HTTPS-In`: permiten `HTTP_PORT` y `HTTPS_PORT` (TCP) hacia el `caddy.exe` instalado, solo desde `LAN_SUBNET` y `100.64.0.0/10`;
    - `DistriCom-RDP-In`: permite 3389 (TCP) solo desde `LAN_SUBNET` y `100.64.0.0/10`;
    - `DistriCom-Block-PostgreSQL`, `-Backend` y `-Frontend`: bloquean 5432, 8000 y 3000 (TCP) hacia el programa exacto que escucha en cada uno (`postgres.exe`, el intérprete de Python del backend y `node.exe`). Nombrar programa y puerto evita afectar a otro programa en el mismo puerto. Windows no filtra el tráfico de `127.0.0.1`, así que los servicios siguen hablando entre sí.
  - **Dos verificaciones:**
    - `firewall verify` es estática: compara las reglas y los perfiles, con los servicios en marcha o detenidos.
    - `firewall verify --services-running` revisa además la instalación en marcha. Exige que 5432, 8000 y 3000 tengan un listener, solo en `127.0.0.1`, abierto por el programa que nombra su regla. Un listener ausente, fuera de `127.0.0.1` o de otro programa es FAIL.
    - Solo son advertencias las condiciones ajenas a DistriCom que deciden IT, como las reglas propias de Windows para el Escritorio remoto.
  - **Escritorio remoto:** DistriCom nunca bloquea 3389 ni cambia las reglas propias de Windows para el Escritorio remoto; solo agrega un permiso. Si esas reglas siguen abiertas a cualquier origen, `verify` lo avisa como decisión de IT, sin tocarlas. En el equipo real, `firewall.ps1` se corre desde una sesión de Escritorio remoto ya abierta.
  - **Idempotente y reversible:** `apply` no reescribe una regla que ya está bien y corrige una alterada o sobrante del grupo; `remove` quita todas las reglas de DistriCom y nada más.
  - `verify` informa un perfil del firewall apagado o con entrada permitida por defecto, pero no cambia perfiles.
  - **Pruebas de CI separadas de las de red real:** el runner no tiene otra máquina en su red. CI prueba las reglas como las informa Windows, los sockets y que las reglas ajenas (dos reglas ficticias de Store Sys, las del runner y las del Escritorio remoto) queden idénticas. PENDIENTE, nunca PASS: el acceso real desde otra PC de la LAN, Tailscale, el Escritorio remoto del grupo IT, los puertos reales de Store Sys, el perfil de red del equipo y Windows 11 Pro.
- **Requisito pendiente para C5** (Enzo, 2026-11-10): el supervisor tiene que verificar de forma segura el certificado de Caddy. Hoy, por las ACL de C3, el Stack no lee la CA de `state\caddy` y su prueba de disponibilidad usa TLS sin verificar. No se resuelve desactivando TLS.
- **Riesgos que siguen abiertos para las etapas siguientes** (Enzo, 2026-11-09):
  - la verificación física del disco y su número de serie;
  - el reinicio real de Windows sin sesión iniciada;
  - la ocupación de los puertos 80 y 443 por otros servicios;
  - la compatibilidad con Defender;
  - la validación independiente de la procedencia del paquete de PostgreSQL de EDB, antes de producción;
  - el control de integridad y de permisos de los ejecutables instalados (C3 lo implementa; su ejecución periódica y la alerta son de etapas posteriores);
  - la coordinación del supervisor con las operaciones de mantenimiento y el bloqueo de escrituras;
  - el cierre de PostgreSQL solo después de completar las garantías de C9, C10 y C11.
  Las simulaciones de GitHub Actions no dan PASS a las pruebas físicas.
- **Requisito obligatorio para C9** (Enzo, 2026-11-09): la comprobación de reanudación (`writes_resume_check`) y el desbloqueo efectivo de escrituras tienen que estar coordinados, sin condición de carrera. Entre la comprobación y el desbloqueo no puede abrirse una ventana ni empezar una operación que la comprobación no haya visto. Por ejemplo, ambos se hacen bajo un mismo bloqueo exclusivo, que también toman `maintenance open` y el inicio de operaciones, y la comprobación se repite dentro de ese bloqueo justo antes de desbloquear.
- **A‑14** (PR #73, segmento cerrado que no llegó en 180 s) sigue PENDIENTE, registrada aparte.
- **Regla de logs para C2 a C13** (Enzo, 2026-11-07): los logs operativos no deben incorporar información personal de clientes, teléfonos, direcciones ni valores completos de consultas SQL.
- Simular algo en GitHub Actions no prueba un acceso remoto, un apagado real ni una recuperación física.

## 9. Pendientes que no dependen del desarrollo
- Requisitos de Store Sys (RAM, disco, puertos, su base de datos, cómo se detiene).
- Acceso del proveedor de Store Sys: cuenta, permisos, supervisión y ventanas de cambio.
- **Licencias de Windows 11 Pro.** Sus términos limitan a **20 dispositivos** el acceso a ciertos servicios **del propio Windows**: archivos e impresión compartidos, Internet Information Services, conexión compartida y telefonía. Ese límite **no equivale** al número de usuarios web de DistriCom, que se conectan a Caddy y no a esos servicios. Sí puede aplicar a Store Sys si usa carpetas compartidas de Windows, y el Escritorio remoto de Windows cliente admite una sola sesión interactiva a la vez. A confirmar con el proveedor de licencias y con el de Store Sys; si no alcanza, se evalúa Windows Server.
- Laboratorio Windows 11 Pro.
- Régimen de fin de semana y encendido diario.
- Del plan S3 v2 siguen vigentes: dominio, IdP, Tailscale, bucket con bloqueo de objetos (pruebas R2‑1 a R2‑7), alertas, responsable y suplente, áreas protegidas antes del go‑live.
