# W2-A: prueba de concepto de backups físicos en Windows

Prueba de concepto **aislada** de la etapa W2‑A (ADR 016, en estado **Propuesto**). Nada de la aplicación ni del despliegue Linux la usa. Plan general en `docs/WINDOWS_DEPLOYMENT.md`.

## Qué demuestra

Usa las herramientas de PostgreSQL 17 y `rclone`, porque pgBackRest no tiene versión para Windows. Con datos ficticios, demuestra que se puede:
- archivar el WAL de forma continua y segura;
- hacer backups base y verificarlos;
- restaurar a un momento por hora, por nombre, por LSN o hasta el final;
- detectar huecos siguiendo las timelines;
- copiar el repositorio cifrado y restaurar solo desde esa copia;
- aplicar una retención que no borra nada que necesite un backup conservado.

Cada restauración se compara con el origen usando `deploy/sql/verify.sql`, el mismo archivo que usan los simulacros de Linux.

**Lo que no demuestra**, y queda **PENDIENTE**: Defender real, un disco desconectado de verdad, un corte de energía, el bucket real con bloqueo de objetos, PostgreSQL como servicio de Windows, Windows 11 Pro (los runners son Windows Server) y el esquema real de DistriCom. El reporte de resultados lo detalla.

## Piezas (`src/districom_backup/`)

| Módulo | Qué hace |
|---|---|
| `wal_archive` | `archive_command` y `restore_command`. Escribe con nombre temporal, `fsync` y renombre con reintentos. Si el archivo ya existe idéntico, devuelve OK; si existe distinto, da error y nunca sobrescribe. Sin la marca del repositorio, no escribe nada |
| `base_backup` | `pg_basebackup` en formato plain con manifiesto SHA‑256 y `-X stream`, más `pg_verifybackup`. El backup se escribe con nombre temporal y se renombra recién después de verificarlo |
| `restore` | PITR en una instancia nueva, en otra carpeta y otro puerto, con el archivado apagado. Termina en `COMPLETE`, `INCOMPLETE` (hay huecos y no había objetivo) o `FAILED` |
| `wal_gaps` | Continuidad del WAL siguiendo los archivos `.history`. Un cambio de timeline no es un hueco; un `.history` faltante sí |
| `offsite` | `rclone copy --immutable` (nunca `sync`) hacia un remoto `crypt`. Antes escribe un manifiesto con el SHA‑256 de cada archivo, y al traer la copia la verifica |
| `retention` | Conserva los N backups más nuevos, con todo su WAL desde el inicio y todos los `.history` |
| `compare` | Huella de `verify.sql`: el resultado es `IDENTICAL` o `DIFFERENT` |

## Cómo correrlo

Requisitos: Python 3.12, [uv](https://docs.astral.sh/uv/), los binarios de PostgreSQL 17 y `rclone`.

```powershell
cd deploy\windows\backup
uv sync --frozen
$env:DISTRICOM_PG_BIN = "C:\ruta\a\pgsql\bin"
$env:DISTRICOM_RCLONE = "C:\ruta\a\rclone.exe"
$env:DISTRICOM_POC_TIMING_MB = "1024"   # tamaño de la base para medir tiempos (A-17)
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
uv run pytest -v
```

Los resultados quedan en `test-results/w2a-results.md` y `.json`: cada criterio A‑1 a A‑17 con PASS, FAIL o NOT RUN, más las pruebas PENDIENTES y las mediciones. Si falta `DISTRICOM_PG_BIN` o `rclone`, los tests **fallan**: no se saltean.

En CI corre en el job «Windows backup PoC (W2-A)» sobre `windows-latest`. Ese job publica el reporte en el resumen de la ejecución y como artefacto.

En Linux corre igual, con los binarios de PostgreSQL del sistema. Hay que usar un usuario que no sea root, porque PostgreSQL no arranca como root.
