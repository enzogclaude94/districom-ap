# Guía de UX

## Principios
- Cada pantalla responde al menos una de: ¿qué está pasando?, ¿dónde perdemos oportunidades?, ¿qué acción tomamos?
- Todo KPI lleva a una explicación, un drill-down o una lista de clientes/productos.
- Jerarquía antes que cantidad: nunca 25 cards equivalentes.

## Layout
Sidebar jerárquica (Inicio · Comercial · Portfolio · Gestión · Boca de expendio · Datos · Configuración), topbar con filtros globales (Fase 2) y contenido con ancho máximo `7xl`. Los módulos no disponibles se muestran deshabilitados con su fase.

## Tokens (`frontend/src/app/globals.css`)
| Token | Uso |
|---|---|
| `success` | resultado favorable |
| `warning` | atención |
| `danger` | problema |
| `info` / `primary` | información, acción principal |
| `muted` | fondos secundarios, texto auxiliar |

El color nunca es la única señal: acompañar con ícono, texto o símbolo (▲ ▼). Números con `tabular-nums`. Dark mode preparado por tokens (`.dark`), light mode primero.

## Estados obligatorios
- **Loading:** skeletons con la forma del contenido; nunca spinner de pantalla completa para consultas chicas.
- **Empty:** explica por qué no hay datos y qué hacer ("No hay oportunidades de alta prioridad con estos filtros").
- **Error:** mensaje comprensible + código de referencia (`request_id`) + reintentar.

## Tablas y gráficos
- Tablas: paginación, orden y filtros del lado del servidor; headers sticky; drill-down.
- Gráficos según la pregunta: línea (evolución), barras (comparación), barras apiladas (composición), heatmap (intensidad), scatter (relación), funnel (pipeline). Donuts solo en casos simples; sin gauges decorativos.

## Responsive
Desktop excelente → tablet → móvil de consulta. Matrices grandes pueden simplificarse en móvil, pero nada se rompe.

## Idioma
Español rioplatense, tono profesional y directo ("Ingresá", "Revisá").
