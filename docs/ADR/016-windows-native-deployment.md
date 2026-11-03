# ADR 016 — Despliegue nativo en Windows 11 Pro, en el mismo equipo que Store Sys

- Estado: **Propuesto** · 2026-11-03 (W1 del hilo «Districom Servidor»; decisión de arquitectura de Enzo del 2026-11-03)
- Reemplazará, cuando se acepte: la parte de **despliegue** del ADR 013 (contenedores, Docker Compose, systemd, `ufw` + `DOCKER-USER`, pgBackRest, scripts bash de `deploy/bin`). Hasta entonces **el ADR 013 sigue vigente** y no se modifica.
- Mantiene: ADR 001, 002, 004, 006 y 015, y del ADR 013 las decisiones que no dependen del sistema operativo (ver «Qué se conserva del ADR 013»).
- Alcance: cómo se instala, expone, respalda, actualiza y opera DistriCom en Windows. No cambia reglas de negocio, endpoints, pantallas, el esquema ni las migraciones de ningún módulo.
- Plan detallado: `docs/WINDOWS_DEPLOYMENT.md`.

## Condición para pasar a «Aceptado»

Este ADR queda **Propuesto** hasta que la infraestructura Windows esté suficientemente validada y Enzo lo apruebe expresamente. Como mínimo:

1. **W2‑A** (prueba de concepto de backups físicos, WAL, PITR y restauración) en PASS en CI, con las pruebas que el CI no reproduce marcadas **PENDIENTES**, nunca PASS.
2. **W2‑B** (pruebas automatizadas de los componentes compatibles con Windows) en PASS.
3. **W2‑C** planificada y aprobada, y las pruebas pendientes completadas en un laboratorio Windows 11 Pro adecuado **antes** de aprobar la instalación productiva.

Mientras tanto, el diseño Linux (ADR 013, `docs/OPERATIONS.md`, `deploy/`) se conserva completo.

## Contexto

- La empresa decidió (Enzo, 2026-11-03) instalar DistriCom **de forma nativa en Windows 11 Pro**, sin Ubuntu, WSL, Docker ni máquinas virtuales en producción.
- El servidor es un equipo existente: ASUS PRIME Z390‑A, Intel Core i3‑9100, RAID 1 de Intel RST (250 GB) y un disco WD Purple de 1 TB. Hoy tiene 8 GB de RAM; está prevista la ampliación con 16 GB adicionales (pendiente de verificación). Hoy corre Windows 10 Pro 22H2, sin soporte desde el 14/10/2025.
- **Store Sys** se instalará en el mismo Windows. Su proveedor hará esa migración más adelante; el servidor anterior de Store Sys queda como respaldo.
- La operación es diaria: encendido a la mañana, servicios sin iniciar sesión, acceso desde navegadores de la LAN y remotos por Tailscale, backup base de 15 a 17 y apagado a la noche por Escritorio remoto, con un cierre de jornada seguro.
- El hardware y el Windows actual **no se modifican** mientras este ADR esté propuesto.

## Decisiones

1. **Servicios nativos de Windows**, sin contenedores:
   - PostgreSQL 17 con el instalador oficial, como servicio;
   - backend (uvicorn) y frontend (Next.js standalone, `node server.js`) como servicios con **WinSW**;
   - Caddy (binario con el módulo `caddy-dns/cloudflare`) como servicio con WinSW;
   - Tailscale como servicio, en **modo desatendido**.

   Orden de arranque: PostgreSQL → backend → frontend → Caddy. Cada servicio corre con su propia **cuenta de servicio virtual**, sin privilegios de administrador.
2. **Caddy sigue siendo la única puerta.** Backend (8000), frontend (3000) y PostgreSQL (5432) escuchan **solo en 127.0.0.1**. Caddy publica 443 (y 80 para redirigir). `FORWARDED_ALLOW_IPS=127.0.0.1`.
3. **Firewall de Windows** en lugar de `ufw` + `DOCKER-USER`:
   - 80 y 443 solo desde la subred LAN y desde `100.64.0.0/10` (Tailscale);
   - 3389 (Escritorio remoto) solo desde la LAN y Tailscale (grupo IT), con NLA y cuentas nominales;
   - nada más entrante.

   El router no reenvía ningún puerto.
4. **Backups sin pgBackRest**, que no tiene versión para Windows. Se usa una capa propia en Python sobre las herramientas de PostgreSQL 17, con su propia suite de pruebas:
   - `archive_command` propio hacia `repo1` (disco de backup), con escritura atómica;
   - `pg_basebackup` en formato plain con manifiesto, y `pg_verifybackup`;
   - copia fuera del local (`repo2`) cifrada con `rclone crypt`, asíncrona;
   - PITR con `restore_command` y `recovery_target_time` o `recovery_target_name`;
   - detección de huecos y retención que respetan timelines, archivos `.history` y la dependencia entre cada backup base y sus segmentos WAL;
   - simulacros con comparación exacta (`deploy/sql/verify.sql`).

   Las exportaciones SQL (`pg_dump`) quedan como formato **secundario** y portable, nunca como reemplazo del PITR.
5. **Automatización en Python** (el lenguaje del backend, testeable en CI), con PowerShell solo como envoltorio para el Programador de tareas y la instalación.
6. **Programador de tareas de Windows** en lugar de los timers de systemd, con una cuenta de servicio y «ejecutar aunque el usuario no haya iniciado sesión».
7. **Cierre de jornada** como único modo de apagado: crea el punto de restauración del día, fuerza el cierre del segmento WAL, espera que esté en `repo1` y `repo2`, detiene los servicios en orden y recién entonces apaga.
8. **Actualización y rollback por carpetas de versión** (`C:\DistriCom\releases\<versión>` y un enlace `current`), con backup completo previo y migración explícita, como hoy.
9. **CI:** los 4 jobs actuales (Linux) no cambian. Se agregan jobs en runners Windows de GitHub Actions para lo que se pueda probar ahí. Los runners son Windows Server, no Windows 11 Pro: lo que dependa del escritorio o del hardware se prueba en un laboratorio Windows 11 Pro y, al final, en el servidor.

## Qué se conserva del ADR 013

- Un nombre propio (`DISTRICOM_HOST`), un origen y un certificado público de Let's Encrypt por DNS‑01, sin abrir puertos.
- Acceso remoto por Tailscale con la misma política (`deploy/tailscale/policy.hujson`), sin Funnel, subnet router ni exit node.
- IP real del cliente, cookies, CSRF, verificación de `Origin`, sesiones, roles y áreas protegidas: viven en la aplicación.
- Dos repositorios de backup cifrados, uno local y otro fuera del local con bloqueo de objetos; RPO y RTO objetivo; simulacros con comparación exacta.
- Migraciones explícitas en producción, nunca al arrancar.
- PostgreSQL sin datos personales en su log (O7) y la lista «Antes del go‑live» (O10, `docs/OPERATIONS.md §2.1`), que se adaptará a Windows.

## Garantías que no son equivalentes al diseño Linux

| # | Garantía | En Linux (ADR 013) | En Windows | Mitigación |
|---|---|---|---|---|
| 1 | **Madurez de la herramienta de backups** | pgBackRest, herramienta madura y usada en producción | Código propio | Diseño mínimo sobre herramientas oficiales de PostgreSQL; suite de pruebas en CI (W2‑A) y en laboratorio; simulacros periódicos. Sigue sin ser equivalente en madurez |
| 2 | **Aislamiento** | Contenedores, redes internas, usuario sin privilegios por imagen, equipo dedicado | Procesos que comparten sistema de archivos y registro; **equipo compartido con Store Sys y con el acceso de su proveedor**. Un administrador de Windows puede leer la base y los secretos | Cuentas de servicio virtuales por servicio; puertos internos en 127.0.0.1; permisos NTFS; BitLocker; el proveedor de Store Sys sin administrador permanente, con cuenta propia y accesos registrados |
| 3 | **Memoria ECC** | Preferida en el plan v2 | La Z390‑A no la soporta | Base inicializada con `initdb --data-checksums` (en PostgreSQL 17 no viene activo por defecto), `pg_amcheck` en los simulacros. El riesgo residual se acepta |
| 4 | **Restauración en cualquier máquina** | Un backup físico se restaura en otro Linux o en una VM | Un backup físico hecho en Windows **solo se restaura en otro Windows** con PostgreSQL 17 | Procedimiento de recuperación en otro Windows, ensayado; base en UTF‑8 con ICU `es-AR`; `pg_dump` lógico diario como copia portable |
| 5 | **Equipo compartido con el sistema de facturación** | Servidor dedicado | Competencia por CPU, RAM y disco; el proveedor instala, actualiza y reinicia por su cuenta | Requisitos de Store Sys pedidos al proveedor antes de dimensionar; ventanas de cambio acordadas; pruebas de concurrencia con los dos sistemas funcionando |

Ninguna de estas cinco se presenta como equivalente. Las demás garantías (exposición, HTTPS, acceso remoto, PITR, dos repositorios, RPO) son equivalentes **solo si pasan sus pruebas** en Windows.

## Alternativas descartadas

- **Servidor Linux dedicado** (plan S3 v2): es la opción de menor riesgo técnico y ya tiene la suite S1 en PASS, pero no cumple el objetivo de un solo equipo para DistriCom y Store Sys. Su diseño se conserva en el repo hasta validar Windows.
- **Linux en una VM o en WSL dentro del Windows:** descartado por decisión de Enzo (sin VM ni WSL en producción); suma una capa más para operar y recuperar.
- **Docker Desktop en Windows:** depende de WSL 2 o Hyper‑V; mismo motivo.
- **Windows 10 con actualizaciones extendidas (ESU):** costo anual creciente y fecha de fin; Windows 11 Pro es la plataforma definitiva.
- **Backups solo con `pg_dump`:** pierden la restauración a un momento determinado.
- **Barman o WAL‑G:** sin soporte nativo confiable en Windows.

## Consecuencias

- Desarrollo y los 4 jobs actuales del CI no cambian. Se agrega un job Windows por etapa (W2‑A en adelante).
- Hay que escribir y mantener: la capa de backups, la operación (equivalente a `deploy/bin`), `install.ps1`, la suite de aceptación para Windows y el cierre de jornada.
- `docs/OPERATIONS.md` se adaptará a Windows recién cuando este ADR se acepte.
- La aplicación solo necesita cambios menores de configuración (destinos de Caddy por variable, `tzdata` explícito); se harán en su etapa, con su PR.
- Antes del go‑live siguen vigentes: S3, la lista O10 verificada en el equipo real y la asignación de las áreas protegidas según la lista de Enzo.
