# Guía de UX

Aplica junto con [`BRAND.md`](BRAND.md): esta guía define el comportamiento; la marca define la apariencia.

## Principios
- Cada pantalla responde al menos una de: ¿qué está pasando?, ¿dónde perdemos oportunidades?, ¿qué acción tomamos?
- Todo KPI lleva a una explicación, un drill-down o una lista de clientes/productos.
- Jerarquía antes que cantidad: nunca 25 cards equivalentes.

## Layout
Sidebar jerárquica (Inicio · Comercial · Portfolio · Gestión · Boca de expendio · Datos · Configuración), topbar con filtros globales (Fase 2) y contenido con ancho máximo `7xl`. Los módulos no disponibles se muestran deshabilitados con su fase.

## Marca, color y tipografía
Ver [`docs/BRAND.md`](BRAND.md): wordmark e ícono, paleta oficial como tokens, Manrope (marca) + Inter (producto), y reglas de uso. Resumen:
- Azul Francia como acento; neutros luminosos como base.
- Verde / ámbar / rojo solo para estados de negocio, siempre con ícono o texto además del color.
- Números con `tabular-nums`. Light mode primero; dark mode preparado por tokens.

## Estados obligatorios
- **Loading:** skeletons con la forma del contenido; nunca spinner de pantalla completa para consultas chicas.
- **Empty:** explica por qué no hay datos y qué hacer ("No hay oportunidades de alta prioridad con estos filtros").
- **Error:** mensaje comprensible + código de referencia (`request_id`) + reintentar.

## Tablas y gráficos
- Tablas: paginación, orden y filtros del lado del servidor; headers sticky; drill-down.
- Gráficos según la pregunta: línea (evolución), barras (comparación), barras apiladas (composición), heatmap (intensidad), scatter (relación), funnel (pipeline). Donuts solo en casos simples; sin gauges decorativos.

## Formularios de dinero (Rendiciones)
- Cada formulario genera su `client_request_id` al abrirse y lo reusa en cada reintento. Si el resultado de un envío es incierto (error de red, timeout, 5xx), el pedido exacto queda en `sessionStorage` (por formulario y usuario) hasta que se confirme o el usuario lo descarte tras una advertencia: reabrir el formulario ofrece reintentar la misma operación con el mismo id. Solo una operación nueva genera otro id.
- Los diálogos atrapan el foco mientras están abiertos y lo devuelven al control que los abrió.
- Siempre envían `business_date` explícita; el botón queda deshabilitado mientras se espera la respuesta.
- Los saldos se vuelven a leer de la API después de cada operación: el frontend solo muestra provisionalmente el importe interpretado.
- Los errores de negocio se explican en palabras del usuario (nunca códigos, SQLSTATE ni texto interno).
- Ajustes, saldo inicial y reversos se presentan como acciones administrativas, separadas de los movimientos cotidianos. Un reverso se llama “Revertir operación”, nunca “Eliminar”.
- Las cuentas técnicas (Saldo inicial, Ajustes) no se muestran como dinero disponible; solo en el detalle de una operación.

## Responsive
Desktop excelente → tablet → móvil de consulta. Matrices grandes pueden simplificarse en móvil, pero nada se rompe.

## Idioma
Español rioplatense, tono profesional y directo ("Ingresá", "Revisá").
