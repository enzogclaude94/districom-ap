# ADR 011 — Contrato de ingreso Store Sys → DistriCom (importe esperado)

- Estado: Aceptado · 2026-10-26 (propuesta R9‑1 aprobada por Enzo con los ajustes 1–5; R9‑1 autorizada, migración `0030`)
- Amplía: ADR 007 (módulo Rendiciones), ADR 008 (integridad del ledger), ADR 009 (bocas) y ADR 010 (áreas protegidas). No los reemplaza.
- Alcance: **solo el contrato canónico** por el que el importe esperado de una rendición entra a DistriCom. No hay integración real con Store Sys.

Este ADR tiene tres partes, como pidió Enzo: lo **verificado** en el DistriCom actual, las **decisiones del contrato canónico** y los **supuestos sobre Store Sys sin verificar** (P1–P6). Un supuesto sin verificar nunca se presenta como hecho.

## 1. VERIFIED FROM CURRENT DISTRICOM

Lo que existía antes de R9‑1 y se verificó en el código y la base (`0007`, `0023`, `0029`):

- `settlements.settlements.expected_amount` es `numeric(14,2)`; `expected_source` existía con CHECK solo `MANUAL`. Ninguna rendición tenía otro origen.
- Una rendición de reparto (`DELIVERY`) exigía esperado `> 0` al abrirse (`ck_settlements_expected_amount_by_type`). Una boca (`OUTLET`, R7‑2) ya podía abrirse sin esperado o con `0`.
- Cerrar exige esperado cargado (`ck_settlements_closed_has_expected`, R7‑2). El cierre (`settlement_closings`) no guardaba contra qué esperado se cerró.
- Las correcciones del esperado eran un `PATCH` con auditoría en `audit_events`; no había historia propia del esperado ni distinción entre lo cargado a mano y lo informado por un sistema.
- Identidades: `employees.external_store_id` (plataforma, `varchar(100)`, único) existía sin uso y lo exponía `EmployeeOut`; las bocas tienen `outlets.code` (único, mayúsculas, R7‑1). No existe `outlets.external_store_id` y no se agrega.
- Áreas protegidas (ADR 010): `ModuleSpec.protected_areas`, `require_area_access`, `explicit_grant_required`; sin el área, 404.
- Store Sys es un sistema externo: DistriCom no tiene conexión, archivos, credenciales ni muestras reales de sus exportaciones.

## 2. CANONICAL CONTRACT DECISIONS

### 2.1 El contrato
- `POST /api/v1/settlements/imports/store-sys/expected-amounts` con `{source: "STORE_SYS", client_request_id, records: [1..200]}`. Cada registro: `external_reference`, `revision` (≥ 1), `settlement_type` (`DELIVERY` | `OUTLET`), `business_date`, `employee_store_code` (reparto) o `outlet_code` (boca), `sequence_number` opcional, `expected_amount` (decimal en texto, dos decimales, nunca float), `reported_at` opcional.
- `employee_store_code` y `outlet_code` son **campos canónicos de DistriCom** (ajuste 4). El adaptador que algún día lea Store Sys los traduce; el contrato no conoce columnas de Store Sys.
- **Atomicidad por registro:** cada registro es su propia transacción y su propio resultado (`index`, `outcome` o `code` + `message`, rendición alcanzada). Un registro malo nunca hace caer el lote. Respuesta con `counts` (recibidos, guardados, rechazados, por outcome).
- **Resolución sin nombres:** reparto por `employees.external_store_id` de un empleado activo; boca por `outlets.code` de una boca activa; luego tipo + fecha + (número si viene). Ninguna rendición con ese código y día: `settlement_not_found`, no se guarda nada y el reenvío se reevalúa. Más de una sin número: `ambiguous_settlement`. Nunca se crea una rendición automáticamente.
- Una referencia queda **ligada** a la rendición que alcanzó su primera revisión; si otra revisión apunta a otra rendición: `external_reference_conflict`.

### 2.2 Eventos del esperado (ajustes 2 y 5)
- `settlements.expected_amount_events`: **todo** cambio del esperado es un evento, manual o de Store Sys: el alta con importe, el `PATCH` de reparto y de boca, cada registro de Store Sys y su decisión. Sin `DELETE`; un evento se decide una sola vez.
- **Outcomes (únicamente):** `MANUAL`, `APPLIED`, `MATCHED`, `PENDING_CONFIRMATION`, `CONFIRMED`, `KEPT`, `REJECTED_CLOSED`, `SUPERSEDED`. No existe `UNCHANGED`: un reintento devuelve el mismo evento, sin crear otro.
- `settlements.expected_event_id` apunta al evento que fijó el esperado vigente. **Sin backfill:** las rendiciones anteriores a R9‑1 quedan con `NULL` y se leen «Manual · anterior a integración Store Sys».
- La base verifica: el importe y el origen cambian solo con un evento nuevo; el evento vigente coincide con el importe; el `previous_*` de un evento es el vigente; nunca un evento que fija el importe sobre una rendición cerrada.

### 2.3 Autoridad
| Rendición | Esperado vigente | Resultado |
|---|---|---|
| nunca cerrada | ninguno | `APPLIED` |
| nunca cerrada | Store Sys, misma referencia, revisión posterior | `APPLIED` |
| nunca cerrada | manual (o anterior a R9‑1), mismo importe | `MATCHED` (el origen pasa a Store Sys) |
| nunca cerrada | manual con otro importe, o Store Sys de otra referencia | `PENDING_CONFIRMATION` |
| en corrección (`BEING_CORRECTED`) | cualquiera | `PENDING_CONFIRMATION` |
| cerrada | cualquiera | `REJECTED_CLOSED`: no se toca la rendición ni se reabre |

- Un pendiente no cambia el esperado hasta que alguien lo **acepta** (`CONFIRMED`) o **mantiene** el cargado con motivo (`KEPT`). Mientras espera, la rendición **no cierra** y el esperado no se edita a mano (`expected_update_pending`, también en la base). A lo sumo un pendiente por rendición: un registro posterior lo deja `SUPERSEDED` y apunta a él (`supersedes_event_id`).
- Corregir a mano un esperado que vino de Store Sys exige motivo (`expected_reason_required`).
- Decidir: operator o admin de Rendiciones en una rendición nunca cerrada; admin en una en corrección.

### 2.4 Revisiones e idempotencia
- Clave: (`STORE_SYS`, `external_reference`, `revision`), única en la base. Misma clave y misma huella: devuelve el evento que ya existe, en su estado actual (por ejemplo `CONFIRMED` si ya se aceptó). Misma clave y otra huella: `external_reference_conflict`, no se escribe nada.
- Una revisión mayor es un evento nuevo. Una revisión menor que la última recibida se guarda `SUPERSEDED` y no cambia nada.

### 2.5 Reparto abierto sin esperado (ajuste 1)
- Un `DELIVERY` abre con esperado `NULL` o `> 0` (`0` y negativos son inválidos). Para cerrar hace falta `> 0`: el preview lo muestra como bloqueo y el cierre responde `expected_amount_required`. Una vez cargado, el esperado de un reparto no se borra.
- Sin esperado: «Esperado: Sin cargar» y ninguna diferencia definitiva (diferencia, tipo y monto a asignar `null` en el backend). Funcionan los dos caminos: abrir → Store Sys → cerrar, y abrir → carga manual → cerrar. Las bocas no cambian.

### 2.6 Snapshot del cierre
Desde `0030`, `settlement_closings` congela `expected_amount`, `expected_source`, `expected_event_id`, `expected_reference` y `expected_revision` del esperado contra el que se cerró; la base completa los nulos desde la rendición y rechaza uno distinto.

### 2.7 Códigos de empleado y permisos (ajuste 3)
- El área protegida **«Integración Store Sys»** (`settlements.store_sys_import`, `explicit_grant_required = True`): el admin de plataforma no la hereda; sin el área, 404.
- Importar: rol `operator`/`admin` del área **y** `operator`/`admin` de Rendiciones.
- Leer códigos y su historia: `viewer` del área. Cambiar el código de un empleado: solo `admin` del área; corregirlo o quitarlo exige motivo; nada se borra en silencio. Cada cambio queda auditado (`settlements.store_sys.employee_code_changed`: antes, después, quién, cuándo, motivo).
- `employees.external_store_id` se reutiliza, sin migración de plataforma; la escribe una función de plataforma (`parties.service.set_external_store_id`) llamada desde el área. `EmployeeOut` ya no lo expone.

### 2.8 Fuera de alcance (NO IMPLEMENTAR en R9‑1)
Parser XLS/XLSX, carpeta vigilada, conexión directa a Store Sys, cron, RPA, API key, creación automática de rendiciones, mapping genérico, `outlets.external_store_id`, cambios en el ledger.

## 3. UNVERIFIED STORE SYS ADAPTER ASSUMPTIONS

Ninguno de estos puntos se verificó contra Store Sys real. Cada uno tiene un default que **no bloquea** el contrato; cuando haya muestras se confirman o se ajusta solo el adaptador.

| # | Pregunta | Default asumido |
|---|---|---|
| P1 | ¿Store Sys da el importe esperado ya calculado o hay que calcularlo (facturas, notas de crédito, devoluciones)? | El adaptador entrega el importe ya calculado; DistriCom no lo recalcula. |
| P2 | ¿Qué identifica una liquidación en Store Sys y cómo se corrige? | `external_reference` + `revision` creciente. |
| P3 | ¿Cómo nombra Store Sys a una boca? | `outlets.code` de DistriCom (el adaptador traduce). |
| P4 | ¿Cómo nombra Store Sys a un repartidor? | `employees.external_store_id`, **provisorio**: cómo lo provee Store Sys no está confirmado. |
| P5 | ¿Store Sys sabe qué número de rendición del día es? | `sequence_number` opcional; sin él y con más de una rendición del día, `ambiguous_settlement`. |
| P6 | ¿Dónde se ven las actualizaciones pendientes? | En la rendición (banner con Aceptar / Mantener) y un indicador «Store Sys pendiente» en las listas y el tablero. |

**Riesgo anotado:** Comercial Fase 1 preveía completar `external_store_id` desde sus importaciones; desde R9‑1 solo el admin del área lo escribe. Si Fase 1 lo necesita, se decide en esa fase.
