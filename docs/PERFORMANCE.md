# Performance de Rendiciones (R9‑4)

Cómo se mide la performance de Rendiciones, con qué datos y qué se cambió por evidencia. El informe con todas las
cifras es `INFORME_R9-4_PERFORMANCE.md` (carpeta de informes del proyecto). Esta guía es la referencia estable.

## Reglas

- **Nada se optimiza sin medir antes.** Cada cambio tiene ANTES (consulta, EXPLAIN, tiempos, p95), CAMBIO y DESPUÉS
  (mismo dataset, mismo benchmark, EXPLAIN nuevo).
- **Los benchmarks nunca corren en CI** ni hacen fallar nada por tiempo. En CI corren solo los tests de corrección
  (por ejemplo, que el saldo a cualquier fecha sea igual a la suma simple de asientos).
- **Base separada.** El generador y el benchmark se niegan a correr contra una base cuyo nombre no contenga `perf`:
  nunca tocan la base de desarrollo, la de tests ni la del E2E.
- **Tres capas que no se comparan entre sí:** HTTP local (la app en proceso con todo su stack HTTP, sin red),
  servicio en proceso (funciones llamadas con una sesión) y SQL puro (`EXPLAIN (ANALYZE, BUFFERS)`).
- **Store Sys: máximo 200 registros por envío** (contrato R9‑1). Se mide con 1, 50 y 200; nunca más.

## Dataset

`backend/perf/generate.py` crea un año de actividad a través de la API de la app (TestClient en proceso), como un
admin con las áreas Store Sys y Empleados: cada fila es una que el sistema real escribiría, por la única puerta del
ledger, con su auditoría, idempotencia e invariantes. Nada se inserta a mano.

- Repartos (transferencias, cheques, combustible, gastos, pendientes, faltantes y sobrantes), bocas (entregas a
  cuenta, transferencia, arqueo), un envío Store Sys por día, cierres, correcciones (reapertura, revisión 2 de Store
  Sys, aceptación, recierre), cobros y resoluciones de pendientes, depósitos, acreditaciones y rechazos de cheques,
  pagos a proveedores, gastos directos, adelantos, consumos, préstamos, liquidaciones mensuales y su pago.
- Domingos sin actividad. Fechas de negocio desde hace 365 días hasta ayer.
- `--scale` multiplica personas y actividad de un worker; `--workers` corre workers independientes en paralelo
  (cada uno con sus repartidores, bocas y proveedores, y su semilla `seed*100+worker`). Mismo seed y escala ⇒ mismos
  datos de negocio.

| Dataset | Comando | Contenido |
| --- | --- | --- |
| Realista | `--scale 1 --workers 2` | 24 repartidores, 6 bocas, 80 proveedores, 1 año |
| Stress | `--scale 2 --workers 4` | 4× el realista |

```bash
cd backend
createdb districom_perf   # o DROP/CREATE como superusuario
export PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf
DATABASE_URL=$PERF_DATABASE_URL uv run alembic upgrade head
PYTHONPATH=. uv run python -m perf.generate --scale 1 --workers 2 --days 365
pg_dump -Fc districom_perf > districom_perf.dump   # el benchmark escribe: se mide sobre una copia restaurada
```

## Benchmark

`backend/perf/bench.py` mide, para cada endpoint, mediana, p95 (rango más cercano), máximo, queries por request
(eventos del engine), tiempo SQL, filas y bytes; guarda el `EXPLAIN (ANALYZE, BUFFERS)` de las consultas más lentas
de cada request (con sus parámetros reales, dentro de una transacción que se revierte), las piezas del Cierre del día
en proceso, el import Store Sys (1/50/200: aplicado, rechazado por cerrada, reintento idempotente, sin rendición) y un
pago en efectivo de la caja con más historia (hoy y 90 días atrás).

```bash
PYTHONPATH=. uv run python -m perf.bench --runs 30 --out ../perf-results/realistic
```

Las rutas se miden con y sin el área Empleados (un admin de plataforma sin el área: 404 y montos reservados).

## Objetivos

- Lecturas interactivas: p95 < 300 ms; ideal < 150 ms.
- Store Sys, 200 registros: cada registro es atómico e independiente (R9‑1), así que el costo es lineal; objetivo
  p95 < 5 s por envío de 200 en el dataset stress.
- Escrituras de dinero: no deben crecer con la historia de la cuenta.

## Qué cambió en R9‑4 (sin migración)

- **Saldos.** `account_balances(on=D)` para un día del último mes = suma total menos lo fechado después de D (la fecha
  de negocio de un asiento es la de su operación y nunca cambia); para días más viejos, la suma hasta D. Mismo número.
  `account_balance(id)` suma solo esa cuenta. `ledger.accounts()` da nombres y tipos sin sumar el libro: quince
  pantallas sumaban todos los saldos solo para mostrar el nombre de una cuenta.
- **Control de fondos.** El saldo mínimo desde la fecha de un débito lee solo los días posteriores a esa fecha.
- **Movimientos de una cuenta.** El total cuenta los asientos de la cuenta sin los joins de autor y reverso.
- **Cierre del día.** El saldo de apertura es el de cierre menos lo que movió el día.
- **N+1.** Cheques y pendientes en vigor de una página, y pagos de cada liquidación (lista y resumen de pago), en una
  consulta por página.

## Migración `0031` (aprobada por Enzo el 2026-10-29)

- `assert_outlet_advances` compara el cierre vigente con `=`: usa el índice de `settlement_closing_remittances`.
- `check_account_not_negative`, para un débito de los últimos 31 días, lee solo los días posteriores (total − lo
  posterior); uno más viejo conserva la pasada única de `0013`, que es más barata cuando casi toda la historia es
  posterior. Mismo resultado y mismo mensaje.
- `ix_ledger_entries_account_id_include_amount`: el total de una cuenta sin leer la tabla.

`perf/guards.py` mide las escrituras que esas guardas frenan (entrega a cuenta y cierre de boca, pago en efectivo hoy
y a 90 días, `account_balance`). Se corre sobre una copia restaurada, antes y después de `alembic upgrade head`:

```bash
PYTHONPATH=. uv run python -m perf.guards --runs 30 --out ../perf-results/guards-after
```

## Saldos materializados

No hacen falta hoy (decisión de Enzo, 2026-10-29): el ledger sigue siendo la fuente calculada.
