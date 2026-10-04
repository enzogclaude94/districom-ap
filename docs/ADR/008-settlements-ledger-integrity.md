# ADR 008 — Ledger de Rendiciones: idempotencia, concurrencia y única puerta de escritura

- Estado: Aceptado · 2026-10-04 (aprobación de R1 por Enzo, ajustes técnicos 1 y 2)
- Amplía: ADR 007 §8 (no lo reemplaza)

## Contexto
ADR 007 aprobó un ledger propio de Rendiciones (`accounts`, `financial_operations`, `ledger_entries`) con entradas inmutables, operaciones balanceadas, idempotencia y reversos. Al aprobar R1, Enzo pidió:
1. que un `client_request_id` reutilizado por error con **otro contenido** no reciba en silencio la operación anterior;
2. que el servicio de operaciones sea la **única puerta** de escritura del ledger, como regla arquitectónica del módulo, con una protección automatizada razonable;
3. que las cajas físicas nunca queden en negativo, tampoco con operaciones simultáneas ni con operaciones retroactivas (revisión de R1‑1), y que Banco arranque sin descubierto (D5).

## Decisión
1. **Idempotencia por huella del comando.** Cada operación guarda `client_request_id` (UNIQUE) y `request_fingerprint`: el SHA‑256 de una representación canónica del comando (usuario, tipo, fecha de negocio, descripción, patas ordenadas con importes normalizados a dos decimales, documento origen y operación revertida).
   - Mismo id y misma huella → se devuelve la operación existente (`created = false`), aunque los saldos hayan cambiado desde entonces.
   - Mismo id y huella distinta → `409 idempotency_conflict`, con el id de la operación existente en `details`.
   - El orden de las patas y el formato del importe (`500000` / `500000.00`) no cambian la huella; el usuario sí: el mismo id usado por otra persona es un conflicto.
   - Dos solicitudes simultáneas con el mismo id: la segunda espera a la primera en el índice único y luego recibe la respuesta de la regla anterior.
2. **Única puerta.** `app/modules/settlements/ledger/service.py` (`post_operation`, `reverse_operation`) es el único código que escribe `financial_operations` y `ledger_entries`. Corre en la transacción de quien llama, que hace el commit, para que un documento (rendición, pago…) y su operación se graben juntos. La regla se verifica con `tests/modules/settlements/test_ledger_single_writer.py`, que lee el código de `app/` y falla si otro archivo importa los modelos del ledger, construye o escribe sus filas, o tiene SQL sobre esas tablas.
3. **Reglas por tipo de operación en el servicio.** Cada tipo declara su rol mínimo y la forma de sus patas (`ledger/rules.py`): `operator` para traspasos entre cajas, depósitos y extracciones; `admin` para saldo inicial, aportes y devoluciones de socios, ajustes y reversos. Un ajuste exige motivo; un reverso exige motivo, no se revierte a sí mismo y ocurre una sola vez.
4. **Invariantes también en la base** (migración 0005), para código que se saltee el servicio:
   - suma cero y al menos dos patas por operación (constraint trigger diferido, verificado al commit);
   - `financial_operations` y `ledger_entries` sin UPDATE ni DELETE (trigger);
   - sin saldo negativo en cuentas con `allow_negative = false`, **ni actual ni histórico**: al commit, con la cuenta bloqueada, el saldo de cierre de la fecha de negocio del débito y de cada fecha posterior con movimientos debe ser ≥ 0 (constraint trigger diferido);
   - una caja (`CASH_BOX`) no puede tener `allow_negative = true` (CHECK); Banco se crea con `false`;
   - motivo obligatorio en ajustes y reversos, vínculo obligatorio del reverso, un único reverso por operación y una única cuenta técnica de cada tipo.
5. **Concurrencia.** Antes de validar saldos, el servicio bloquea con `SELECT … FOR NO KEY UPDATE`, en orden de id, cada cuenta sin saldo negativo permitido que pierde valor en la operación. Dos retiros simultáneos de la misma caja se serializan: el segundo ve lo que el primero confirmó y, si no alcanza, recibe `409 insufficient_funds`. `NO KEY UPDATE` no choca con los controles de clave foránea de otras inserciones y el orden fijo evita esperas cruzadas (traspasos opuestos simultáneos no se bloquean entre sí). Requiere el aislamiento por defecto de PostgreSQL (READ COMMITTED).
6. **Saldo histórico** (pedido de Enzo al revisar R1‑1). Un débito con fecha `d` baja el saldo de cierre de `d` y de todos los días siguientes. El servicio, con la cuenta bloqueada, calcula en una pasada indexada sobre las entradas de la cuenta el menor saldo de cierre entre `d` y hoy (`d` y cada día posterior con movimientos, únicos días donde el saldo cambia) y rechaza con `409 insufficient_funds` si el débito lo deja negativo, informando el primer día afectado. La base repite el mismo control al commit. La unidad es el **día de negocio**: dentro de un día no hay orden entre operaciones y cuenta el saldo de cierre. Con el volumen de una distribuidora (decenas a cientos de movimientos diarios por caja) la consulta es barata; si algún día no lo fuera, se agrega un saldo diario materializado sin cambiar la regla.

## Consecuencias
- Las fases siguientes (cierre de rendición, acreditación de pendientes, pagos, liquidación) publican sus movimientos llamando al servicio, nunca escribiendo tablas.
- Un reintento con el mismo id recibe siempre la misma respuesta. Si el cliente omite la fecha y reintenta después de medianoche, la fecha resuelta cambia y el reintento es un conflicto: el cliente debe reenviar el comando tal cual lo envió (las pantallas mandan la fecha explícita).
- `TRUNCATE` no está bloqueado (lo usan los tests); en producción solo lo ejecutaría un administrador de la base.
- Quitar un invariante de la base requiere una migración y un ADR nuevo.
