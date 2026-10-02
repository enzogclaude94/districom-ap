# Marca DistriCom

Fuente: *DistriCom Brand Concept v1* y la guía de identidad visual que entregó Enzo el 2026-10-02 (copias en [`docs/brand/`](brand/)).

**Prioridades:** la especificación funcional (prompt maestro, `METRICS.md`) manda sobre **qué** se muestra; la marca manda sobre **cómo** se ve. El dashboard del material de marca es solo una referencia visual: no se copian sus métricas, su navegación ni sus datos.

> Principio rector: *"El dato tiene que sentirse simple."* La identidad no compite con la información: la organiza.

## Nombre
Se escribe **DistriCom** (D y C mayúsculas) en toda la interfaz y en los documentos para usuarios.

## Logos

| Asset | Archivo | Uso |
|---|---|---|
| Wordmark color | `frontend/public/brand/districom-wordmark.svg` | Login, sidebar expandida, reportes, espacios amplios sobre fondo claro |
| Wordmark negativo | `frontend/public/brand/districom-wordmark-negative.svg` | Sobre Azul Francia o fondos oscuros |
| Wordmark monocromo | `frontend/public/brand/districom-wordmark-mono.svg` | Impresión en un color, documentos sobrios |
| Ícono DC | `frontend/public/brand/districom-icon.png` (512 px), `districom-icon-96.png` | Contextos compactos: header móvil, sidebar colapsada, reportes chicos |
| Favicon / app icon | `frontend/src/app/favicon.ico`, `icon.png`, `apple-icon.png` | Pestaña del navegador, accesos directos |

- El wordmark está generado como **trazos vectoriales de Manrope SemiBold** ("Distri" en Azul profundo, "Com" en Azul Francia, tracking −2,5 %), así no depende de que la fuente esté cargada.
- El ícono DC proviene del PNG entregado (recortado, esquinas transparentes). Original en `docs/brand/icono-dc-original.png`. Cuando exista una versión vectorial oficial, reemplazar los PNG.
- En React usar siempre `<Wordmark />` y `<BrandIcon />` (`frontend/src/components/brand/`), nunca `<img>` sueltos.
- **No** cambiar colores, no distorsionar, no agregar efectos ni íconos al wordmark. Respetar espacio libre alrededor (al menos la altura de la "o").

## Tipografía

| Rol | Fuente | Pesos | Token |
|---|---|---|---|
| Marca y títulos de alta jerarquía (H1 de página, títulos de login/reportes) | Manrope | SemiBold 600 | `font-display` |
| Producto: UI, tablas, filtros, formularios, métricas | Inter | 400 / 500 / 600 | `font-sans` (default) |

Las cifras usan números tabulares (`font-feature-settings: "tnum"`) para que KPIs y columnas alineen. Fuentes servidas por `next/font` (self-hosted en build). Manrope: SIL OFL 1.1 (`docs/brand/MANROPE-OFL.txt`).

## Paleta

Definida **solo** como tokens en `frontend/src/app/globals.css`. Los componentes usan clases de token (`bg-primary`, `text-muted-foreground`…); está prohibido escribir hex en componentes.

| Nombre | Hex | Token | Uso |
|---|---|---|---|
| Azul Francia | `#1455D9` | `--brand-blue` → `primary` | Acciones, énfasis, marca, ítem activo |
| Azul profundo | `#173B72` | `--brand-deep` → `primary-deep` | Contraste, "Distri" del wordmark, estados oscuros |
| Azul claro | `#3B82F6` | `--brand-light` | Acento complementario, segunda serie en gráficos |
| Azul suave | `#E8F0FF` | `--brand-soft` → `primary-soft` | Fondos de selección, ítem activo de navegación |
| Blanco | `#FFFFFF` | `--surface` | Cards, paneles, sidebar |
| Fondo secundario | `#F7F9FC` | `--background` | Fondo de la aplicación |
| Texto principal | `#182230` | `--foreground` | Contenido y títulos |
| Texto secundario | `#667085` | `--muted-foreground` | Información auxiliar |

Neutros derivados (no son colores de marca): `--muted #F0F3F8` (rellenos), `--border #E4E9F1` (líneas finas).

### Estados de negocio (semánticos, no decorativos)
Verde, ámbar y rojo **solo** comunican estado: favorable, atención, problema. Nunca como color de marca, de fondo decorativo ni de categoría de gráfico.

| Estado | Texto | Fondo |
|---|---|---|
| `success` | `#12805C` | `#E7F6EF` |
| `warning` | `#B54708` | `#FEF4E6` |
| `danger` | `#C4320A` | `#FDECEB` |
| `info` | Azul Francia | Azul suave |

El color nunca es la única señal: siempre con ícono, texto o símbolo (▲ ▼).

## Lenguaje visual

| Sí | Evitar |
|---|---|
| Mucho espacio en blanco | Logos con íconos genéricos |
| Azul como acento, no como relleno masivo | Exceso de degradados |
| Jerarquía tipográfica clara | Demasiados colores |
| Superficies luminosas, bordes moderados (radio 10 px) | Sombras pesadas (`--shadow-card` es la única sombra de superficie) |
| Líneas finas (1 px, `--border`) | Estética industrial/logística (camiones, cajas, mapas) |
| Gráficos simples, pocos colores por gráfico | Gauges y donuts decorativos |
| Componentes consistentes | |

## Pendiente de marca
- Versión vectorial oficial del ícono DC.
- Variantes de wordmark (tecnológica / premium / cálida) que el Brand Concept propone explorar; hoy se usa la versión base.
- Tamaños mínimos y espacio libre formales.
- Sidebar colapsada: todavía no existe; cuando se implemente, usa `<BrandIcon />`.
