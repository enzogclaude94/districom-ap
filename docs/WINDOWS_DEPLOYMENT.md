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
- **Una transacción confirmada no es lo mismo que una transacción recuperable.** Es recuperable desde un repositorio cuando el segmento WAL que la contiene ya está archivado en él.
- Con el equipo funcionando, nada confirmado se pierde (lo protege el WAL local de PostgreSQL y el RAID 1).
- Ante la **pérdida total del local**, se pierde lo que no haya llegado a `repo2`: hasta `archive_timeout` más el atraso de la subida, o más si no había Internet. **No hay RPO cero frente a una pérdida total.**
- El cierre de jornada reduce ese riesgo para la noche, pero solo si se usa.

### 5.4 Simulacros
Instancia descartable con `pg_ctl`, en otra carpeta y otro puerto, comparada con el origen en el mismo punto con `deploy/sql/verify.sql` y `pg_amcheck`. Si la copia no llega al punto, el resultado es INCOMPLETE. Mensual desde `repo1`; trimestral desde `repo2` en **otro Windows**.

### 5.5 RPO y RTO objetivo

| Escenario | RPO | RTO objetivo |
|---|---|---|
| Error humano o corrupción | Al minuto elegido | 1–2 h |
| Actualización fallida | 0 (backup previo) | < 30 min |
| Falla un disco de datos | 0 (RAID 1) | Sin corte |
| Muere el equipo | Lo archivado en `repo1` | < 1 día, en otro Windows |
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

**Cierre de jornada** (`cierre-jornada`, por Escritorio remoto), el único modo de apagado. Si un paso falla, no apaga y avisa:
1. Verifica que no haya backups, subidas, simulacros, importaciones ni escrituras en curso.
2. Abre la ventana de mantenimiento.
3. Crea el punto de restauración del día y fuerza el cierre del segmento WAL.
4. Espera la confirmación de que todo hasta ese punto está en `repo1` y `repo2`. Sin Internet, avisa y deja elegir entre esperar o apagar con el riesgo registrado.
5. Detiene Caddy, frontend, backend y PostgreSQL, en ese orden.
6. Store Sys: según defina su proveedor (pendiente).
7. Registra el cierre y apaga Windows.

**Windows Update:** horas activas y aplazamiento por directiva; parches en el cierre de jornada.

## 7. Actualización y rollback
`update`: backup completo etiquetado → detener servicios → nueva carpeta de versión → `alembic upgrade head` → cambiar `current` → arrancar → smoke. `rollback`: volver al `current` anterior y, si hace falta, restaurar la base del backup previo, con las mismas confirmaciones que en Linux.

## 8. Etapas

| Etapa | Qué | Dónde se prueba | Estado |
|---|---|---|---|
| **W1** | ADR 016 (Propuesto) y este plan | CI (documentación) | En revisión |
| **W2‑A** | Prueba de concepto aislada de backups físicos, WAL, PITR y restauración (`deploy/windows/backup/`) | GitHub Actions (Windows); lo no reproducible queda PENDIENTE | Alcance aprobado en lo general; implementación **no autorizada** |
| **W2‑B** | Pruebas automatizadas de los componentes compatibles con Windows (backend en Windows, orden de textos, frontend) | GitHub Actions (Windows) | Por definir |
| **W2‑C** | El resto (operación, `install.ps1`, servicios, firewall, cierre de jornada, suite de aceptación) | CI y laboratorio Windows 11 Pro | Se planifica solo después de validar W2‑A |
| W3 | Laboratorio Windows 11 Pro: suite completa y pruebas pendientes | PC de laboratorio (no el servidor) | Sin laboratorio confirmado |
| W4 | Preparación del equipo: RAM, RAID, SMART, Windows 11 Pro, BitLocker, UPS | Servidor | No autorizado |
| W5–W8 | Staging, pruebas en el servidor, transición y go‑live (O10) | Servidor | Equivalen a S3 |
| W9 | Llegada de Store Sys y pruebas con los dos sistemas | Servidor | Depende del proveedor |

### 8.1 Dónde se puede probar cada cosa
- **GitHub Actions (`windows-latest`):** lógica y herramientas de PostgreSQL en Windows, archivado, backups, PITR, restauración, huecos, retención, cifrado contra un destino local. Los runners son **Windows Server**, no Windows 11 Pro.
- **Laboratorio Windows 11 Pro (una PC de prueba):** PostgreSQL como servicio y sus permisos, Defender, arranque sin sesión, Programador de tareas, BitLocker, Windows Update, desconexión real de un disco, corte de energía real, bucket remoto real.
- **Servidor físico (S3):** Intel RST RAID 1, discos reales, tiempos reales, convivencia con Store Sys, UPS. Sin ensayos destructivos mientras Store Sys funcione en él.

## 9. Pendientes que no dependen del desarrollo
- Requisitos de Store Sys (RAM, disco, puertos, su base de datos, cómo se detiene).
- Acceso del proveedor de Store Sys: cuenta, permisos, supervisión y ventanas de cambio.
- Licencias: límite de 20 dispositivos de Windows cliente frente a los usuarios de los dos sistemas.
- Laboratorio Windows 11 Pro.
- Régimen de fin de semana y encendido diario.
- Del plan S3 v2 siguen vigentes: dominio, IdP, Tailscale, bucket con bloqueo de objetos (pruebas R2‑1 a R2‑7), alertas, responsable y suplente, áreas protegidas antes del go‑live.
