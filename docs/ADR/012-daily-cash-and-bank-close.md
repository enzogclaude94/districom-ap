# ADR 012 — Cierre del día de Cajas y Bancos (vista calculada)

- Estado: Aceptado · 2026-10-28 (decisiones 1–3 de Enzo para R9‑2, aceptado con la aprobación de R9‑2 y su ajuste de visibilidad de cuentas)
- Amplía: ADR 007 (módulo Rendiciones), ADR 008 (integridad del ledger), ADR 010 (áreas protegidas) y ADR 011 (contrato Store Sys). No los reemplaza.
- Alcance: un reporte de **solo lectura** por fecha de negocio. Sin migración, sin tablas nuevas, sin escribir en el ledger.

## Decisiones

1. **Fecha de negocio.** Todo número sale de las operaciones con `business_date` igual al día, nunca de `created_at`, `closed_at` ni `received_at`. Una rendición con fecha de negocio lunes que se cierra el martes es del lunes; un reverso de un documento lleva la fecha original del documento (ADR 008), así que cae en el mismo día que lo que revierte.
2. **Rendición en corrección.** Se muestra el **cierre vigente en Cajas** (`current_closing_id` / `current_close_operation_id`) con la marca «En corrección». El borrador nunca se muestra como definitivo; una rendición sin cerrar muestra su esperado cargado, sin justificado ni diferencia.
3. **Vista calculada.** No se firma ni se congela; no crea documento de cierre, cierre diario, snapshot ni operación de ledger. Se recalcula en cada lectura (`GET /api/v1/settlements/daily-close?on=` y su `.csv`).

## Qué muestra

- Por caja (`CASH_BOX`) y banco (`BANK`): saldo inicial contable (ledger hasta `D − 1`), ingresos y egresos del día agrupados por tipo de operación, movimiento neto y saldo final contable. El saldo final se suma **por separado** hasta `D` y se compara con inicial + neto: si no coincide es un **error de invariante del ledger**, nunca una «diferencia de arqueo».
- Cuentas (ajuste de Enzo a R9‑2): una caja o banco aparece en `D` si tiene saldo inicial o final distinto de cero o movimientos en `D`, o si está activa hoy **y ya existía en `D`**. «Existía» se decide por su `created_at` convertido a fecha en el calendario de negocio (`business_timezone`, America/Argentina/Buenos_Aires), nunca comparando el instante UTC contra la fecha. `created_at` decide **solo** la existencia de la cuenta: nunca la fecha de negocio, los movimientos, ingresos, egresos ni saldos, que salen del ledger. Una cuenta creada después de `D` sin saldo ni movimiento no aparece; una inactiva hoy aparece donde tuvo saldo o movimiento, marcada «Hoy inactiva». Una inactiva hoy con todo en cero no aparece aunque existiera: no se guarda la historia de activación.
- Categorías: el tipo de operación; el cierre de una rendición se separa por su tipo (reparto o boca). Un reverso es **su propia categoría** («Reverso de …», con `reverses`) junto a lo que revierte; nunca se neta en silencio.
- Privacidad (ADR 010): sin `settlements.employee_finance`, adelantos, préstamos, pagos de liquidación y sus reversos se agrupan como «Movimiento de empleado (reservado)», sin tipo, empleado ni descripción; el importe suma igual en todos los totales.
- Rendiciones del día: totales (abiertas, cerradas, en corrección, sin esperado, Store Sys pendiente) y, por cierre vigente, esperado, justificado (= esperado + diferencia del cierre), diferencia y origen del esperado. Store Sys **no** toca el ledger ni tiene categoría monetaria.
- Cheques: cartera y depositados sin acreditar al final del día, sumados del ledger hasta `D`; depositados, acreditados, rechazados y devueltos en el día, por los documentos con esa fecha vigentes hoy. No se reconstruyen estados históricos de un cheque que no están guardados.

## Qué no existe

No hay arqueo físico de cajas («Arqueo físico: No registrado» solo aclara la limitación), extracto bancario, conciliación ni diferencia física. No se crean `daily_cash_count`, arqueo, cierre firmado, conciliación bancaria, saldo de extracto ni diferencias físicas. El conteo de una rendición no es el efectivo físico de una caja.

## Consecuencias

- Sin migración ni tabla materializada: las lecturas usan los índices existentes (`financial_operations.business_date`, `ledger_entries.operation_id`). Los saldos suman el ledger como Cajas desde R1; si con volumen real hace falta un índice o un saldo por día, se decide en R9‑4 con su plan de ejecución.
- Exportación: CSV (`;`, coma decimal, BOM UTF‑8) y página imprimible del navegador; sin PDF.
