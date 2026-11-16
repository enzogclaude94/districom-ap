# ADR 018 — AXUM «Compró / No compró»: fuente, cliente por código y reimport con decisión

- **Estado:** Aceptado · 2026-11-11. Lo decidió Enzo (D1–D6) al revisar la inspección de la muestra real antes de F1.4.
- **Reemplaza en parte:** ADR 014, solo en:
  - §1.2 y el supuesto S6 (§3): la fuente esperada era un CSV `NC_Vend_TodosFD_…csv`;
  - §2.6, la viñeta «AXUM y STORE SYS son espacios de IDs independientes (D‑B)», solo para el cliente: `commercial.axum_customer_code_matches_store_sys` pasa a `true`;
  - §2.7.4, la estrategia contra el reimport, que ADR 014 dejaba abierta.

  El resto de ADR 014 sigue vigente: sin UNIQUE por fecha + cliente + preventista + motivo, con `occurrence`, sin crear clientes ni empleados, sin fuzzy matching. ADR 014 no se edita.
- **Detalle operativo:** `docs/IMPORTS.md §3.6` y §5.4. **Tabla:** `DATA_MODEL.md §6.3`.

## Contexto

ADR 014 esperaba un CSV de No Compra con columnas `Codigo`, `Motivo` y `Preventista`, y supuso que el código de cliente de AXUM era un espacio propio. El 2026-11-11 llegó el export real (`Compro no compro - Octubre.xlsx`). Lo que se verificó en él está en el informe de inspección (`INFORME_F1_4_MUESTRA_AXUM.md`, fuera del repositorio) y en `docs/IMPORTS.md §3.6`.

## Decisiones

1. **La fuente es un XLSX, no un CSV.** El archivo es el export automático de AXUM (generado por `System.IO.Packaging`, no guardado por Excel). Su estructura es el contrato:
   - una hoja `Data`;
   - encabezado en la fila 1, con la columna A vacía;
   - una fila de grupo `Vendedor: NN` antes de las filas de cada preventista, y una fila en blanco entre grupos;
   - todas las celdas son texto.
2. **Es un reporte «Compró / No compró», no solo de No Compra.** La columna `Compro?` dice `Si` o el motivo de no compra. No hay columnas `Codigo`, `Motivo` ni `Preventista`: el motivo es `Compro?` y el preventista es el código de la fila de grupo.
3. **El cliente es `Cliente Id`.** No se asume ninguna otra columna como cliente.
4. **Equivalencia comprobada.** En la muestra real:
   - 401 de los 402 `Cliente Id` existen como `codigo` del maestro de STORE SYS con el mismo nombre (y 399 con la misma dirección);
   - 1484 de las 1490 filas `Si` tienen una venta de STORE SYS del mismo cliente el mismo día.
5. **Regla activada y auditada.** `commercial.axum_customer_code_matches_store_sys = true`: la siembra la crea con el motivo de esta decisión y un evento de auditoría, y solo un admin de Comercial la cambia, con motivo (`PUT /api/v1/commercial/axum-customer-code-rule`). El orden de resolución es:
   1. el mapping `customer/AXUM/customer` exacto, si ya existe, manda;
   2. si no hay y la regla está activa, se busca `customer/STORE_SYS/customer` con el mismo código y se compara el nombre (normalizado, exacto) con el nombre de fantasía o la razón social del cliente canónico;
   3. código y nombre coinciden → se crea o actualiza el mapping `customer/AXUM/customer` `confirmed`, con `method = rule`, el mismo `customer_id` y un evento `commercial.axum.customers_linked_by_rule`;
   4. el código existe pero el nombre no coincide → el mapping queda pendiente con el cliente como candidato, el evento con `customer_id NULL` y el issue `commercial.axum_customer.name_mismatch`;
   5. el código no existe → pendiente, con los valores fuente y el issue `commercial.axum_customer.unresolved`;
   6. con la regla apagada, todo código sin mapping propio queda pendiente.

   Nunca se crea un cliente, nunca se resuelve solo por nombre y nunca hay fuzzy matching.
6. **Las filas `Si` no son no compras.** Quedan en `import_rows` como `skipped`, con el mensaje «Compró: la fila queda trazable pero no genera un evento de no compra.». No se modelan visitas ni se infiere de ellas cobertura, cumplimiento o conversión.
7. **Fecha de negocio = día de `Fecha y Hora Subida`.** El reporte no trae la fecha de la visita. Se guardan la hora de subida, la de inicio, la de fin y la duración tal como vienen, sin combinarlas. Una fila cuya hora de inicio es posterior a la subida conserva el día de la subida, con el aviso `start_after_upload`.
8. **Toda superposición la decide un admin.** El reporte no trae el período pedido ni los filtros, así que no se puede probar qué reemplaza:
   - el mismo archivo → `duplicate`;
   - un período sin no compras cargadas → se carga;
   - cualquier no compra activa en el período del archivo → `import_decision_required`. Un admin elige «reemplazar» (las anteriores de los preventistas que trae el archivo quedan `SUPERSEDED`, nunca borradas; las de otros preventistas no se tocan) o «adicional» (se conservan todas), siempre con motivo y auditoría.

   No se copia el reemplazo automático por scope `FULL` de ventas.
9. **Los preventistas de AXUM son un espacio propio.** Cada código del reporte (también uno con solo compras) se observa como mapping `employee/AXUM/salesperson` pendiente, con el issue `commercial.axum_salesperson.unmapped`. No se confirma ninguna equivalencia con empleados (eso es F1.5), no se crean empleados y no se usa `employee/STORE_SYS/seller`.

## Consecuencias

- Tabla nueva `commercial.no_purchase_events` (migración 0038): una fila por motivo de no compra, con `occurrence`, estado `ACTIVE`/`SUPERSEDED`, trazabilidad al job y a la fila, sin DELETE (trigger) y con un downgrade que se niega si hay eventos.
- Un export posterior del mismo mes, con alguna visita sincronizada tarde, siempre pide una decisión. Si AXUM permite exportar con el rango y los preventistas visibles, la regla se puede relajar con un ADR nuevo.
- El historial de Importaciones cuenta como «Filas con datos» las filas que se procesan: en este reporte, solo las no compras (1271 de las 2761 filas de la muestra). El detalle muestra las 2761.
- Las filas `Si` quedan disponibles en el staging del job por si un diseño futuro de visitas las necesita; ese diseño requiere su propia aprobación.
