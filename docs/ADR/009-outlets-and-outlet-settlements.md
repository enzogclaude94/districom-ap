# ADR 009 — Bocas de expendio y rendiciones

- Estado: Aceptado · 2026-10-11 (aprobación de la arquitectura de R7 por Enzo, alternativa B, decisiones D1–D9 y ajustes 1–5)
- Amplía: ADR 006 (plataforma modular), ADR 007 (módulo Rendiciones) y ADR 008 (ledger). No los reemplaza.

Este ADR separa lo **DECIDIDO** (vale para todo R7) de lo **IMPLEMENTADO** (lo que ya existe en el código). Al cierre de R7‑4 está implementado todo R7: el maestro de bocas, los responsables, la rendición de boca (con el cierre administrativo cero), las entregas parciales con «Entregas a cuenta — {Boca}» (`OUTLET_ADVANCES`) y la operación de punta a punta (tablero Rendiciones › Bocas, ficha, detalle, ayuda del conteo final, historia, móvil y E2E), sin migración en R7‑4.

## Contexto
Una boca de expendio es un punto de venta que rinde lo cobrado en el día, con entregas parciales de efectivo durante la jornada. Enzo pidió la propuesta completa antes de cualquier código (`PROPUESTA_R7_BOCA_DE_EXPENDIO.md`) y aprobó la alternativa B: la boca rinde con la misma rendición que el reparto, y lo entregado durante el día se lleva en una cuenta técnica por boca, sin una caja ficticia.

## Decisión

### 1. La boca es un maestro de plataforma — DECIDIDO · IMPLEMENTADO (R7‑1, `0021`)
- `public.outlets`: `code`, `name`, `name_key`, `description`, `active`, `created_*`, `updated_*`. Sin dirección ni localidad.
- **Código:** obligatorio, único e **inmutable**, formato `^[A-Z0-9-]{2,20}$` (se guarda en mayúsculas; los espacios alrededor se quitan; cualquier otra cosa se rechaza, no se corrige). Es la identidad de la boca junto con su id. El nombre nunca es identidad.
- **Nombre:** se corrige. `name_key` (minúsculas, sin acentos ni puntuación, espacios simples) es **único**: dos bocas no pueden tener nombres operativamente iguales. Si aparece un caso real de homónimos, se consulta a Enzo antes de relajar la invariante.
- Sin DELETE (API y base). Auditoría `platform.outlet.{created, name_changed, description_changed, deactivated, reactivated}`; el código nunca aparece en un cambio.
- Lectura para todo usuario autenticado; alta, corrección y (des)activación para el admin de plataforma. Pantalla `/bocas`.
- **Boca inactiva (ajuste 1):** no abre rendiciones nuevas ni suma responsables. Lo abierto (OPEN o BEING_CORRECTED) se termina normalmente: entregas, efectivo, transferencias, cheques, gastos, cierre y corrección. Desactivar no se bloquea; la pantalla advierte. *Implementado en R7‑1:* lo que toca a responsables y la advertencia general. *Implementado en R7‑2:* el bloqueo de apertura (servicio y trigger `trg_settlements_outlet_active`, con la boca tomada `FOR SHARE`) y «N rendiciones abiertas» en la ficha de la boca.

### 2. Los responsables son del módulo — DECIDIDO · IMPLEMENTADO (R7‑1, `0022`)
- `settlements.outlet_responsibilities`: `outlet_id`, `employee_id`, `valid_from`, `valid_to` (ambos **inclusive**), `created_by`, `ended_by`, `ended_at`, `end_reason`.
- Vigencia por día, sin turnos (D3). Puede haber varios responsables a la vez; un empleado puede ser responsable de varias bocas, también el mismo día. La única exclusión es el **mismo empleado en la misma boca con rangos superpuestos** (exclusión GiST). Sin restricción global por empleado (ajuste 5).
- Cualquier empleado activo; **no** existe la función `outlet_attendant` (D3).
- Nace abierta; se termina **una sola vez**, con motivo, quién y cuándo, y `valid_to >= valid_from`. Nada más cambia; nada se borra. La historia nunca se reescribe.
- Nueva responsabilidad: boca activa y empleado activo. Una ya abierta sigue visible y se puede terminar aunque después se desactive la boca o el empleado.
- Vigente el día `d`: `valid_from <= d <= valid_to` (sin tope mientras `valid_to` es nulo). API `GET /settlements/outlets/{id}/responsibilities?on=YYYY-MM-DD`.

### 3. La rendición de boca reutiliza `settlements.settlements` — DECIDIDO · IMPLEMENTADO (R7‑2, `0023`)
- `type = OUTLET` en la misma tabla y el mismo ciclo (OPEN → CLOSED → BEING_CORRECTED → CLOSED), con la misma lógica de conteo, transferencias, cheques, gastos, diferencia y cierre atómico al ledger.
- **Importe esperado (D2):** para OUTLET puede ser nulo mientras está abierta y es obligatorio para cerrar; puede ser 0. Abierta: `expected_amount IS NULL OR >= 0`; para cerrar: `IS NOT NULL AND >= 0`. La regla de DELIVERY (`> 0`) no cambia.
- **Sin pendientes (D4):** la base lo prohíbe (`pending_not_enabled_for_outlets`); sin sección en pantalla; R3 no se modifica.
- **Diferencias (D5):** la misma cuenta «Diferencias de rendición»; sin `OUTLET_DIFFERENCES`. La asignación es siempre explícita, nunca se reparte sola entre responsables (D3).
- **Cheques (D8):** entran a Cartera al cierre; no hay entrega parcial de cheques.
- **Sin combustible (R7‑2):** un gasto de boca nunca es `FUEL` (`fuel_not_allowed_for_outlet`, servicio y base).
- **Identidad:** OUTLET lleva `outlet_id` y `employee_id` nulo; DELIVERY al revés (`ck_settlements_identity_by_type`). Numeración por boca y día (`uq_settlements_outlet_sequence`, parcial) separada de la de reparto (`uq_settlements_sequence`, ahora parcial a DELIVERY con el mismo nombre). La Rendición 2 del mismo día exige confirmación explícita (409 `additional_settlement_confirmation_required`).
- **Responsables:** los del `business_date` se muestran como sugerencia en el detalle y en la vista previa del cierre; nunca precargan un importe, nunca bloquean el cierre y se puede asignar a un empleado que no lo era ese día.
- **Cierre administrativo cero (R7‑2‑Q1, respuesta de Enzo):** una rendición OUTLET con esperado 0, sin efectivo contado, sin agregados, sin transferencias verificadas (ni declaradas), sin cheques que justifiquen o esperen verificación, sin gastos, sin pendientes, sin entregas (R7‑3), sin diferencia y sin un cierre propio vigente en el ledger **se cierra sin operación de ledger**: no hay patas de $0, ninguna cuenta se mueve. Quedan `settlement_closings` (`kind = ADMINISTRATIVE_ZERO`, `operation_id` nulo, `snapshot` con los importes en "0.00"), la versión, el token de vista previa, quién y cuándo, la auditoría, el estado CLOSED y la historia. `closed_has_close` no se relaja en general: DELIVERY CLOSED ⇒ operación financiera (`ck_settlements_delivery_close_financial`); OUTLET CLOSED ⇒ operación o un cierre documental vigente que la base verifica completo al commit (`settlements.assert_zero_close`). Reabrir un cierre cero no revierte nada; volver a cerrar en cero es otro cierre administrativo; si aparece actividad, el nuevo cierre es un `SETTLEMENT_CLOSE` normal. Financiero → reapertura → corregido a cero: se revierte la operación anterior (`ledger.withdraw_document_operation`, en la fecha original) y el nuevo cierre es administrativo.

### 4. Fondos: cuentas técnicas, sin caja ficticia — DECIDIDO · IMPLEMENTADO `OUTLET_COLLECTIONS` (R7‑2) y `OUTLET_ADVANCES` con las entregas parciales (R7‑3)
- **Sin `CASH_BOX` por boca (D1, modelo 2).** El efectivo final del cierre va a Caja principal o a Caja de cambio.
- **«Cobranzas de boca» (`OUTLET_COLLECTIONS`, D9):** una cuenta técnica; la pata del importe esperado del cierre OUTLET (−esperado; sin pata cuando el esperado es 0). *Implementado en R7‑2:* sembrada por `0023`, puede ser negativa, fuera del tablero de Cajas; la mueven solo el `SETTLEMENT_CLOSE` de una OUTLET (negativo) y su reverso (positivo): regla del ledger (`at_most_one_of`), trigger por pata `trg_ledger_entries_outlet_collections` y trigger diferido `trg_financial_operations_outlet_close_collections` (exactamente −esperado, nunca «Cobranzas de reparto»). El cierre cero no la toca.
- **«Entregas a cuenta — {Boca}» (`OUTLET_ADVANCES`, D9):** una por boca.
- **Entrega parcial (`OUTLET_REMITTANCE`):** `OUTLET_ADVANCES` −X, caja destino +X. Destino: cualquier `CASH_BOX` activa (por defecto Caja principal); nunca Banco ni una cuenta técnica (D7). Una entrega que supera el esperado no se bloquea: se confirma con «Con esta entrega lo entregado supera el importe esperado en $X.» (también si el esperado es nulo, D6).
- **Cuenta técnica cerrada (ajuste 2):** solo tocan `OUTLET_ADVANCES` las `OUTLET_REMITTANCE` (−X), el `SETTLEMENT_CLOSE` de OUTLET (+ el total exacto de las entregas vigentes) y sus REVERSAL documentales. Prohibido: traspasos genéricos, ajustes manuales, saldo inicial, extracción, depósito, PAYMENT, DIRECT_EXPENSE, operaciones de socios y cualquier otra. Cajas no muestra acciones sobre ella. El saldo negativo es válido mientras haya entregas sin cerrar; la excepción sale de reglas exactas por operación y documento, no solo de `allow_negative = true`. Positivo solo transitoriamente durante BEING_CORRECTED; al confirmar el nuevo cierre vuelve a ser coherente. `OUTLET_COLLECTIONS` tiene la misma disciplina: solo los cierres OUTLET autorizados y sus reversos.
- **Fechas (ajuste 3):** `business_date` es la fecha financiera; `delivered_at` es el instante real o declarado de la entrega, no futuro y con fecha (America/Argentina/Buenos_Aires) igual a la `business_date` de la rendición; `created_at` es el registro.
- **Conteo final (ajuste 4):** EL CONTEO FINAL ES EL EFECTIVO FÍSICO QUE QUEDA EN LA BOCA DESPUÉS DE LAS REMESAS YA REALIZADAS. Ejemplo: $900.000 cobrados, $700.000 entregados durante el día: el conteo es $200.000.
- **Implementado en R7‑3 (`0024`, respuestas de Enzo a R7‑3‑Q1 a Q3 del 2026-10-13; detalle en `DATA_MODEL.md §13.7.2`):**
  - La cuenta de la boca se crea en su primera entrega (no se siembra), `accounts.outlet_id` la ata a su boca (única) y su tipo, boca, actividad y `allow_negative` no cambian. La disciplina cerrada está en el servicio, en las reglas del ledger (`OUTLET_REMITTANCE` y la pata `OUTLET_ADVANCES` del `SETTLEMENT_CLOSE`) y en la base (`trg_ledger_entries_outlet_advances`, por pata: solo la entrega de esa boca, el cierre de una rendición de esa boca y sus reversos).
  - Documento `settlements.outlet_remittances` (sin DELETE, dinero inmutable; boca y fecha las de la rendición, verificadas por la base). Operator o admin en una rendición abierta nunca cerrada; solo admin en corrección; nunca en CLOSED (Q2). Reverso solo admin, con motivo, en OPEN o en corrección; nunca se edita el importe ni se borra (Q1). Destino: cualquier `CASH_BOX` activa, Caja principal por defecto en pantalla (Q3).
  - El cierre suma lo entregado al justificado y consolida exactamente las entregas vigentes de **esa** rendición con una pata `OUTLET_ADVANCES` +Σ; la caja no se vuelve a mover. `settlement_closing_remittances` congela qué entregas consolidó cada cierre, como `settlement_closing_cheques` para los cheques: es lo que permite verificar la regla global por boca con varias rendiciones por día y durante una corrección (el cierre vigente sigue consolidando lo que consolidó hasta el nuevo cierre).
  - **Regla global por boca** (`settlements.assert_outlet_advances`, diferida): saldo = −Σ entregas vigentes de la boca + Σ lo que consolidan los cierres vigentes de sus rendiciones; fuera de corrección eso es −Σ(entregas vigentes no consolidadas por un cierre vigente), y cada rendición CLOSED consolidó exactamente sus entregas vigentes.
  - **Esperado nulo (precisión de R7‑3, punto 7):** la entrega se permite y no pide confirmación de exceso, porque no hay esperado contra el cual medirla; la confirmación aparece cuando lo entregado vigente supera un esperado cargado. Esto precisa la frase «también si el esperado es nulo» de D6.
  - **Quién entregó (`delivered_by_employee_id`, opcional):** el empleado tiene que existir y no haber ingresado después de la fecha; un empleado hoy inactivo se acepta si era responsable de la boca ese día (vigencia de `outlet_responsibilities`) o si un admin documenta la entrega durante una corrección (`employee_inactive` en otro caso). Es un dato del día: no reparte diferencias ni cambia el cálculo.

### 5. El ledger es la única fuente — DECIDIDO
Todo movimiento de valores de la boca pasa por `ledger/service.py` (ADR 008). Ningún saldo de boca se guarda ni se edita; se deriva de las entradas.

### 6. Operación diaria de la boca — DECIDIDO · IMPLEMENTADO (R7‑4, sin migración)
Pedido de Enzo del 2026-10-14. La pantalla principal es **Rendiciones › Bocas** (`/rendiciones/boca`): todas las bocas de un día, con su estado (Sin rendición, Abierta, Cerrada, En corrección), sus cifras y acciones según el rol; la ficha `/bocas/{id}` sigue siendo el maestro y muestra la «Operación de Rendiciones» sin reemplazar el tablero. Mientras una rendición está abierta lo que falta es «Resta rendir o justificar», nunca una diferencia; la diferencia existe solo al cerrar. Dos rendiciones del mismo día nunca se suman. El conteo final se cuenta, nunca se deriva del esperado. Detalle en `DATA_MODEL.md §13.7.3`.

## División en PRs
| PR | Contenido | Estado |
|---|---|---|
| R7‑1 | Maestro de bocas y responsables (`0021`, `0022`) | Implementado y aprobado (PR #34) |
| R7‑2 | Rendición OUTLET sin entregas parciales (esperado nulo/≥ 0, sin pendientes ni combustible, «Cobranzas de boca», cierre, cierre administrativo cero; `0023`) | Implementado y aprobado (PR #35) |
| R7‑3 | Entregas parciales `OUTLET_REMITTANCE`, su reverso y su consolidación al cierre (`0024`) | Implementado y aprobado (PR #36) |
| R7‑4 | Pantallas de boca, móvil, ayuda del conteo final y E2E de punta a punta (sin migración) | Implementado, en revisión |

## Consecuencias
- Una boca existe una vez para todos los módulos; Rendiciones la nombra por id y nunca guarda su nombre.
- Quién responde por una boca un día es historia del módulo, consultable por fecha, sin funciones de empleado nuevas.
- Las rendiciones de boca no duplican la lógica de reparto; la diferencia está en el tipo, el importe esperado, las cuentas técnicas y las entregas parciales.
- Sin caja por boca: los saldos de efectivo viven solo en cajas reales; lo entregado a cuenta queda en una cuenta técnica cerrada que se salda con el cierre.
