# W2-C1: base operativa de DistriCom en Windows

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
| `cli` | `districom-ops init`, `check-config`, `maintenance open\|close\|status`, `operations` y `resume-check` |

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
- los permisos NTFS de `config\` y `state\` (C3);
- Windows 11 Pro;
- un corte de energía real durante una escritura del registro.
