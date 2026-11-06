# W2-C1: base operativa de DistriCom en Windows

Primera subetapa de W2‑C (ADR 016, en estado **Propuesto**; plan en `docs/WINDOWS_DEPLOYMENT.md` y propuesta W2‑C revisión 1). Son las piezas que usan las subetapas siguientes. Esta carpeta **no** instala servicios, no toca el firewall ni bloquea escrituras: eso es C2 a C13.

Python con solo la biblioteca estándar (decisión D1). Reutiliza `districom_backup` (W2‑A) para las escrituras durables.

## Piezas (`src/districom_ops/`)

| Módulo | Qué hace |
|---|---|
| `layout` | Carpetas `releases\`, `config\`, `logs\` y `state\` bajo la raíz. Las crea si faltan; correrlo de nuevo no cambia nada. El enlace `current` es de C12 |
| `config` | Lee `config\districom.env` en forma literal. Rechaza variables faltantes o inseguras (bases fuera de `127.0.0.1`, contraseñas débiles o de ejemplo, el mismo rol para la app y la operación, una LAN pública) y nombra cada problema **sin mostrar el valor**. Los secretos nunca aparecen en un `repr` ni en un log |
| `logs` | Un JSON por línea en `logs\ops.log`. Enmascara los secretos registrados, las credenciales en URL, los pares `password=…`, los campos con nombre de secreto y los CUIT. De una excepción guarda el tipo y el mensaje enmascarado, nunca el traceback |
| `oplog` | Registro de operaciones `state\operations.jsonl`, solo agregado, con `fsync` en cada evento. Cada operación (cierre, actualización, rollback, manual) tiene un id, sus pasos y un único final: `completed`, `failed` o `reverted`. Sin final queda **incompleta**. Una línea cortada por un corte se informa y se ignora, sin reescribir el archivo |
| `maintenance` | Ventana de mantenimiento `state\maintenance.json` con la **operación dueña**. Solo su dueña la cierra y no se abre una segunda. Al pasar `MAINTENANCE_MAX_MINUTES` da **FAIL** (alerta), pero **nunca se cierra sola ni habilita escrituras**. Un archivo ilegible cuenta como ventana abierta con dueña desconocida |
| `cli` | `districom-ops init`, `check-config`, `maintenance open\|close\|status` y `operations` |

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
- los permisos NTFS de `config\` y `state\` (C3);
- Windows 11 Pro;
- un corte de energía real durante una escritura del registro.
