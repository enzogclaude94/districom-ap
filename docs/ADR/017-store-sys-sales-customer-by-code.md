# ADR 017 — STORE SYS sales resolve customers by customer code

- **Estado:** Aceptado · 2026-11-06. Lo aprobó Enzo al revisar F1.3a (PR #73).
- **Reemplaza en parte:** ADR 014 §2.6, en la viñeta «Cliente», solo lo que dice sobre las ventas:
  - las ventas sin código se resuelven por el mapping `customer / STORE_SYS / customer_name`, con confirmación manual;
  - el sistema propone al cliente con ese nombre exacto normalizado;
  - un nombre repetido queda `ambiguous`.
  
  También cierra el supuesto S4 de ADR 014 §3. El resto de ADR 014 sigue vigente, incluido que los hechos sin resolver se guardan con `customer_id NULL` y el valor fuente. ADR 014 no se edita.
- **Detalle operativo:** `docs/IMPORTS.md §3.3` y §6.

## Contexto

ADR 014 decidió resolver el cliente de las ventas por nombre porque la primera muestra de *Ventas artículos/clientes* (`ventas.xls`, un solo día) no traía código de cliente.

## Evidencia nueva

El export productivo de varios días validado en F1.3a (octubre 2026, del 01 al 31) trae `cod_cliente`:
- 408 códigos, y los 408 existen en el maestro de clientes;
- las 7414 filas se pueden resolver de forma exacta;
- 0 inconsistencias entre código y nombre;
- tres nombres aparecen con dos códigos distintos. Por nombre habrían quedado ambiguos; por código se resuelven sin intervención.

## Decisión

En `commercial.store_sys.sales_lines`, la identidad del cliente se resuelve **exclusivamente** con el mapping `customer / STORE_SYS / customer` (el que crea el maestro), usando el `cod_cliente` exacto.

`cliente` se conserva solo como `source_customer_name`. Queda prohibido:
- resolver por nombre;
- crear mappings `customer_name`;
- hacer fuzzy matching;
- crear clientes desde ventas.

Un `cod_cliente` desconocido:
- conserva `source_customer_code`;
- deja `customer_id NULL`;
- deja la fila `pending_resolution`;
- genera `commercial.customer.unresolved_code`.

## Consecuencias

- El formato productivo de ventas requiere `cod_cliente`. Si falta, el archivo se rechaza con `missing_columns`.
- La muestra antigua de un día sin código deja de ser un formato aceptado.
- Desaparece la ambigüedad de los nombres repetidos. Desaparecen también la cola de confirmación manual por nombre y el issue `commercial.sale_line.customer_pending`.
- `sale_lines.source_customer_code` es NOT NULL (migración 0037, todavía sin mergear) y `source_customer_name` es nullable.
- ADR 014 queda reemplazado solo en esta parte.
