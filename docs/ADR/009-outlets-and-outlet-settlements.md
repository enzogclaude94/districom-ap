# ADR 009 — Bocas de expendio y rendiciones

- Estado: Aceptado · 2026-10-11 (aprobación de la arquitectura de R7 por Enzo, alternativa B, decisiones D1–D9 y ajustes 1–5)
- Amplía: ADR 006 (plataforma modular), ADR 007 (módulo Rendiciones) y ADR 008 (ledger). No los reemplaza.

Este ADR separa lo **DECIDIDO** (vale para todo R7) de lo **IMPLEMENTADO** (lo que ya existe en el código). Al cierre de R7‑1 solo están implementados el maestro de bocas y los responsables; todo lo demás está decidido y llega en R7‑2, R7‑3 y R7‑4.

## Contexto
Una boca de expendio es un punto de venta que rinde lo cobrado en el día, con entregas parciales de efectivo durante la jornada. Enzo pidió la propuesta completa antes de cualquier código (`PROPUESTA_R7_BOCA_DE_EXPENDIO.md`) y aprobó la alternativa B: la boca rinde con la misma rendición que el reparto, y lo entregado durante el día se lleva en una cuenta técnica por boca, sin una caja ficticia.

## Decisión

### 1. La boca es un maestro de plataforma — DECIDIDO · IMPLEMENTADO (R7‑1, `0021`)
- `public.outlets`: `code`, `name`, `name_key`, `description`, `active`, `created_*`, `updated_*`. Sin dirección ni localidad.
- **Código:** obligatorio, único e **inmutable**, formato `^[A-Z0-9-]{2,20}$` (se guarda en mayúsculas; los espacios alrededor se quitan; cualquier otra cosa se rechaza, no se corrige). Es la identidad de la boca junto con su id. El nombre nunca es identidad.
- **Nombre:** se corrige. `name_key` (minúsculas, sin acentos ni puntuación, espacios simples) es **único**: dos bocas no pueden tener nombres operativamente iguales. Si aparece un caso real de homónimos, se consulta a Enzo antes de relajar la invariante.
- Sin DELETE (API y base). Auditoría `platform.outlet.{created, name_changed, description_changed, deactivated, reactivated}`; el código nunca aparece en un cambio.
- Lectura para todo usuario autenticado; alta, corrección y (des)activación para el admin de plataforma. Pantalla `/bocas`.
- **Boca inactiva (ajuste 1):** no abre rendiciones nuevas ni suma responsables. Lo abierto (OPEN o BEING_CORRECTED) se termina normalmente: entregas, efectivo, transferencias, cheques, gastos, cierre y corrección. Desactivar no se bloquea; la pantalla advierte. *Implementado en R7‑1:* lo que toca a responsables y la advertencia general. *Pendiente (R7‑2):* el bloqueo de apertura y la advertencia con la cantidad de rendiciones abiertas.

### 2. Los responsables son del módulo — DECIDIDO · IMPLEMENTADO (R7‑1, `0022`)
- `settlements.outlet_responsibilities`: `outlet_id`, `employee_id`, `valid_from`, `valid_to` (ambos **inclusive**), `created_by`, `ended_by`, `ended_at`, `end_reason`.
- Vigencia por día, sin turnos (D3). Puede haber varios responsables a la vez; un empleado puede ser responsable de varias bocas, también el mismo día. La única exclusión es el **mismo empleado en la misma boca con rangos superpuestos** (exclusión GiST). Sin restricción global por empleado (ajuste 5).
- Cualquier empleado activo; **no** existe la función `outlet_attendant` (D3).
- Nace abierta; se termina **una sola vez**, con motivo, quién y cuándo, y `valid_to >= valid_from`. Nada más cambia; nada se borra. La historia nunca se reescribe.
- Nueva responsabilidad: boca activa y empleado activo. Una ya abierta sigue visible y se puede terminar aunque después se desactive la boca o el empleado.
- Vigente el día `d`: `valid_from <= d <= valid_to` (sin tope mientras `valid_to` es nulo). API `GET /settlements/outlets/{id}/responsibilities?on=YYYY-MM-DD`.

### 3. La rendición de boca reutiliza `settlements.settlements` — DECIDIDO (R7‑2), no implementado
- `type = OUTLET` en la misma tabla y el mismo ciclo (OPEN → CLOSED → BEING_CORRECTED → CLOSED), con la misma lógica de conteo, transferencias, cheques, gastos, diferencia y cierre atómico al ledger.
- **Importe esperado (D2):** para OUTLET puede ser nulo mientras está abierta y es obligatorio para cerrar; puede ser 0. Abierta: `expected_amount IS NULL OR >= 0`; para cerrar: `IS NOT NULL AND >= 0`. La regla de DELIVERY (`> 0`) no cambia.
- **Sin pendientes (D4):** la base lo prohíbe (`pending_not_enabled_for_outlets`); sin sección en pantalla; R3 no se modifica.
- **Diferencias (D5):** la misma cuenta «Diferencias de rendición»; sin `OUTLET_DIFFERENCES`. La asignación es siempre explícita, nunca se reparte sola entre responsables (D3).
- **Cheques (D8):** entran a Cartera al cierre; no hay entrega parcial de cheques.

### 4. Fondos: cuentas técnicas, sin caja ficticia — DECIDIDO (R7‑2/R7‑3), no implementado
- **Sin `CASH_BOX` por boca (D1, modelo 2).** El efectivo final del cierre va a Caja principal o a Caja de cambio.
- **«Cobranzas de boca» (`OUTLET_COLLECTIONS`, D9):** una cuenta técnica; la pata del importe esperado del cierre OUTLET.
- **«Entregas a cuenta — {Boca}» (`OUTLET_ADVANCES`, D9):** una por boca.
- **Entrega parcial (`OUTLET_REMITTANCE`):** `OUTLET_ADVANCES` −X, caja destino +X. Destino: cualquier `CASH_BOX` activa (por defecto Caja principal); nunca Banco ni una cuenta técnica (D7). Una entrega que supera el esperado no se bloquea: se confirma con «Con esta entrega lo entregado supera el importe esperado en $X.» (también si el esperado es nulo, D6).
- **Cuenta técnica cerrada (ajuste 2):** solo tocan `OUTLET_ADVANCES` las `OUTLET_REMITTANCE` (−X), el `SETTLEMENT_CLOSE` de OUTLET (+ el total exacto de las entregas vigentes) y sus REVERSAL documentales. Prohibido: traspasos genéricos, ajustes manuales, saldo inicial, extracción, depósito, PAYMENT, DIRECT_EXPENSE, operaciones de socios y cualquier otra. Cajas no muestra acciones sobre ella. El saldo negativo es válido mientras haya entregas sin cerrar; la excepción sale de reglas exactas por operación y documento, no solo de `allow_negative = true`. Positivo solo transitoriamente durante BEING_CORRECTED; al confirmar el nuevo cierre vuelve a ser coherente. `OUTLET_COLLECTIONS` tiene la misma disciplina: solo los cierres OUTLET autorizados y sus reversos.
- **Fechas (ajuste 3):** `business_date` es la fecha financiera; `delivered_at` es el instante real o declarado de la entrega, no futuro y con fecha (America/Argentina/Buenos_Aires) igual a la `business_date` de la rendición; `created_at` es el registro.
- **Conteo final (ajuste 4):** EL CONTEO FINAL ES EL EFECTIVO FÍSICO QUE QUEDA EN LA BOCA DESPUÉS DE LAS REMESAS YA REALIZADAS. Ejemplo: $900.000 cobrados, $700.000 entregados durante el día: el conteo es $200.000.

### 5. El ledger es la única fuente — DECIDIDO
Todo movimiento de valores de la boca pasa por `ledger/service.py` (ADR 008). Ningún saldo de boca se guarda ni se edita; se deriva de las entradas.

## División en PRs
| PR | Contenido | Estado |
|---|---|---|
| R7‑1 | Maestro de bocas y responsables (`0021`, `0022`) | Implementado, en revisión |
| R7‑2 | Rendición OUTLET (esperado nulo/≥ 0, sin pendientes, cuentas técnicas, cierre) | Decidido, sin código |
| R7‑3 | Entregas parciales `OUTLET_REMITTANCE` y su reverso | Decidido, sin código |
| R7‑4 | Pantallas de boca, móvil, ayuda del conteo final y E2E de punta a punta | Decidido, sin código |

## Consecuencias
- Una boca existe una vez para todos los módulos; Rendiciones la nombra por id y nunca guarda su nombre.
- Quién responde por una boca un día es historia del módulo, consultable por fecha, sin funciones de empleado nuevas.
- Las rendiciones de boca no duplican la lógica de reparto; la diferencia está en el tipo, el importe esperado, las cuentas técnicas y las entregas parciales.
- Sin caja por boca: los saldos de efectivo viven solo en cajas reales; lo entregado a cuenta queda en una cuenta técnica cerrada que se salda con el cierre.
