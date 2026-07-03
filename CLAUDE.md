# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
streamlit run app.py
# or on Windows:
run_app.bat
```

Opens at **http://localhost:8501**. There is no build step, no test suite, and no linter configured.

## Environment setup

Copy `.env` (already present) and fill in all keys before running. Required variables:

```
ML_CLIENT_ID, ML_CLIENT_SECRET, ML_ACCESS_TOKEN, ML_REFRESH_TOKEN
TN_STORE_ID, TN_ACCESS_TOKEN, TN_APP_NAME, TN_USER_EMAIL
PS_API_KEY
GEMINI_API_KEY
IMGBB_API_KEY
```

ML OAuth tokens are also persisted to `ml_tokens.json` after each token refresh — this file takes precedence over the `.env` values on subsequent starts.

Flexxus pricing requires `articulos_flexxus.xlsx` in the project root (exported manually from Flexxus). If the file is absent, `FlexxusClient` is set to `None` in session state and the tab degrades gracefully.

## Architecture

Streamlit single-page app with tab-based navigation. `app.py` initializes all API clients once into `st.session_state` (keyed `ml`, `tn`, `ps`, `flexxus`) to avoid reconnecting on every Streamlit re-render. Each tab is a module under `tabs/` with a single `render()` entry point.

### Data flow by tab

| Tab | Source → Destination | Key modules |
|-----|---------------------|-------------|
| ML → Tiendanube | Mercado Libre API → Tiendanube API | `tabs/ml_tiendanube.py`, `utils.publicar_item_ml`, `mapper.ml_to_tiendanube` |
| PS → Tiendanube | PrestaShop/Infoandina API → Tiendanube API | `tabs/ps_tiendanube.py` |
| PS → Mercado Libre | PrestaShop/Infoandina API → ML API | `tabs/ps_ml.py`, `clients/mercadolibre_publish.py` |
| Historial | Local CSV files → display | `tabs/historial.py` |
| Configuración | `.env` display + connection tests | `tabs/config.py` |

### Key modules

- **`clients/mercadolibre.py`** — OAuth2 client for ML; handles token refresh automatically. Tokens are synced to `ml_tokens.json` after each refresh.
- **`clients/mercadolibre_publish.py`** — `MercadoLibrePublisher` class for creating ML listings; also contains `auto_match_categoria` (AI-based category detection), `buscar_categorias`, `obtener_atributos`, `obtener_comision`, and `get_popular_categories_with_correct_ids`.
- **`clients/tiendanube.py`** — Tiendanube REST client; `get_all_skus()` paginates through all products to build the deduplication set.
- **`clients/prestashop.py`** — PrestaShop XML API client for Infoandina; `get_product_details_by_sku()` is the main entry point, returning name, price, stock, description (HTML→plaintext), and image URLs. Also exports `obtener_cotizacion_dolar()` as a fallback exchange rate source.
- **`clients/flexxus.py`** — Reads `articulos_flexxus.xlsx` once at startup. `get_precio(sku)` returns `{usd, iva, tipo_cambio, pesos}`. SKU lookup is case-insensitive and uses `CODIGOPARTICULAR` column.
- **`mapper.py`** — `ml_to_tiendanube(item, description)` converts an ML item dict to a Tiendanube product payload. `extract_sku()` tries ML attribute IDs `SELLER_SKU`, `SKU`, `SELLER_CUSTOM_FIELD` before falling back to the ML item ID.
- **`enricher.py`** — Calls Google Gemini to generate SEO titles, HTML descriptions, and tags. Has a 35-second retry on rate-limit errors and a `None` fallback if the API is unavailable.
- **`optimize_images.py`** — Downloads, resizes to 400×500px with UnsharpMask sharpening, and uploads to ImgBB. Returns a public URL for use in ML/TN image APIs.
- **`utils.py`** — `publicar_item_ml()` orchestrates the full ML→TN flow (description fetch → mapping → AI enrichment → product creation → image upload). `cargar_skus_tn()` caches TN SKUs in session state; call with `force=True` after publishing a batch. `exportar_csv()` writes `skus_publicados_YYYYMMDD_HHMMSS.csv` to the project root.

### PS → ML tab specifics

The `MercadoLibrePublisher` in `tabs/ps_ml.py` is initialized by copying tokens from the already-authenticated `st.session_state.ml` client. Its `token_expires_at` is set to `float("inf")` to prevent an independent refresh cycle. Tokens are re-synced from the main client on every render to stay current.

Pricing in PS→ML: base price comes from Flexxus (`precio_usd × (1 + iva) × tipo_de_cambio`) when available; falls back to PS price × exchange rate from `obtener_cotizacion_dolar()`. ML commission is fetched dynamically via `publisher.obtener_comision()` and added to the final listing price.

### SKU→MLA Tracker

Módulo para rastrear qué SKUs de PrestaShop ya tienen publicación en ML.

**Archivo:** `clients/sku_tracker.py` — clase `SKUTracker`

**Backend actual:** `sku_mla_map.csv` en la raíz del proyecto.
Columnas: `sku, mla_id, catalog_product_id, titulo, precio_ars, fecha_publicacion, estado, ultima_verificacion`

El backend está encapsulado en `_read_all` / `_write_all` — para migrar a
Google Sheets o base de datos en el futuro, solo se reemplazan esos dos métodos.

**Métodos:**
- `get(sku)` → dict | None (búsqueda case-insensitive)
- `save(sku, mla_id, catalog_product_id, titulo, precio_ars)` → crea o actualiza
- `verify_with_ml(sku, ml_client)` → consulta ML y actualiza estado
- `get_all()` → lista de todos los registros

**Integración en `tabs/ps_ml.py` (Paso 6):**
Antes de publicar, consulta el tracker. Si el SKU ya existe, muestra advertencia
con tres opciones: Ver en ML / Actualizar precio y stock / Publicar de todas formas.
Tras publicar exitosamente, llama `tracker.save()` automáticamente.
El estado de elección se guarda en session_state como `ps_ml_choice_{sku}` y
se limpia al publicar o al buscar otro SKU.

**Lookup retroactivo (SKUs publicados antes del tracker):**
Si `tracker.get(sku)` devuelve None, el Paso 6 consulta ML automáticamente via
`search_items_by_sku(sku)` antes de mostrar el botón de publicar.
- Encuentra resultado → guarda en CSV y muestra advertencia con los 3 botones
- No encuentra nada → flujo normal de publicación
Para evitar llamadas repetidas en rerenders, marca `ps_ml_ml_checked_{sku} = True`
en session_state. Esta key se limpia junto con `ps_ml_choice_*` al buscar un SKU nuevo.

**Fixes aplicados post-implementación:**
- `sku_tracker.py`: `verify_with_ml` captura excepciones sin relanzar — el error
  se loguea en consola de Streamlit pero no interrumpe el flujo de la UI.
- `ps_ml.py`: al hacer nueva búsqueda exitosa, limpia todas las keys de
  session_state que empiecen con `ps_ml_choice_` — cubre el caso de cambiar
  de SKU sin haber llegado a publicar.
- `mercadolibre.py`: nuevo método público `update_item(item_id, payload)` que
  delega al `_put` interno. El tab ps_ml.py usa este método en lugar de llamar
  a `_put` directamente.

### EAN/GTIN desde Flexxus

`FlexxusClient` tiene un método `get_ean(sku)` que devuelve el código de barras
del artículo desde la columna `CODIGOBARRA` del Excel, o `None` si está vacío.
Los valores vienen como float del Excel (ej: 192545215831.0) y se convierten a
string limpio automáticamente.

El EAN se busca al mismo tiempo que el precio (Paso 1) y se guarda en
`st.session_state["ps_ml_ean"]`. Al publicar, se manda a ML como atributo
`{"id": "GTIN", "value_name": ean}`.

En modo catálogo, ML no acepta atributos arbitrarios pero sí acepta GTIN —
`crear_item()` maneja esto separando el GTIN del resto de atributos.

### Mejoras de performance

Subida de imágenes paralela en `tabs/ps_ml.py` usando `concurrent.futures.ThreadPoolExecutor`
(máximo 5 workers). Las imágenes se suben todas al mismo tiempo en lugar de una por una.
El orden puede variar respecto al original pero ML permite reordenarlas después.

### Búsqueda retroactiva de SKUs en ML

`search_items_by_sku()` en `clients/mercadolibre.py` intenta dos parámetros en orden:
1. `?seller_sku=` — para publicaciones en modo no-catálogo
2. `?sku=` — para publicaciones en modo catálogo (busca por `seller_custom_field`)

Esto cubre SKUs publicados antes de que existiera el tracker local.

### Fixes sesión 2026-06-25

**Bug: ps_ml_attrs_confirmed se reseteaba en cada re-render**
El `else` en `_render_pasos_2_a_5` borraba `ps_ml_attrs_confirmed` en cada re-run.
Fix: solo se limpia cuando hay transición real desde modo catálogo
(`was_using_catalog`). Archivo: `tabs/ps_ml.py` — commit 1d9c833

**Bug: ML 400 "fields [title, seller_sku] are invalid"**
El GTIN matcheaba un producto del catálogo → ML auto-forzaba modo catálogo →
`title` y `seller_sku` se volvían inválidos.
Fix: `seller_sku` eliminado del payload permanentemente. Retry automático en
`crear_item()` si ML rechaza con error que contiene "title" + "invalid": reintenta
sin `title` ni `seller_sku`. Archivo: `clients/mercadolibre_publish.py` — commit 0e7b416

### Fixes sesión 2026-06-26

**Filtro de atributos Paso 4 (ps_ml.py)**
El filtro anterior solo mostraba `required: true`. Ahora incluye también
`conditional_required: true`. Los atributos `hidden` (SELLER_PACKAGE_*) se
muestran como inputs separados de dimensiones, solo cuando no hay
`catalog_product_id` seleccionado.
Una lista negra `ATTRS_BLACKLIST = {"EMPTY_GTIN_REASON", "VALUE_ADDED_TAX",
"IMPORT_DUTY"}` excluye esos IDs del formulario visible (se mandan igual via
`crear_item()`). Commits: 82d2584, 5451c80

**IVA desde Flexxus y atributos fiscales en crear_item()**
`FlexxusClient.get_value_added_tax(sku)` convierte `COEFICIENTE` del Excel a
string para ML: `1.0 → "21%"`, `0.5 → "10.5%"`, `0.0 → "Exento"`. Se llama
en Paso 1 junto con `get_ean()` y se guarda en `st.session_state["ps_ml_vat"]`.

`crear_item()` tiene nuevos parámetros opcionales `value_added_tax: str = ""`
e `import_duty: str = "No aplica"`. Siempre se incluyen en `attributes`.
En modo catálogo el filtro permite pasar GTIN + VALUE_ADDED_TAX + IMPORT_DUTY
(antes solo pasaba GTIN).

Paso 4 muestra 4 inputs de dimensiones (ancho/largo/altura/peso en cm/kg)
solo cuando no hay catálogo. Al publicar, los valores > 0 se agregan a
`attrs_ml` como `SELLER_PACKAGE_WIDTH/LENGTH/HEIGHT/WEIGHT`. Commit: d7e89df

### Fixes sesión 2026-07-02

**SELLER_PACKAGE_* — unidades en dimensiones**
Los inputs de alto/ancho/largo se mandan como `"{valor} cm"` y el peso
se ingresa en kg y se convierte automáticamente a gramos para ML (`×1000`).
Commits: ca3c2a2, d377b7b

**IMPORT_DUTY — valor dinámico desde ML**
Ya no se hardcodea "No aplica". Se consulta `GET /categories/{id}/attributes`
para obtener los valores válidos y se elige el que contenga "no"/"0"/"exento".
Si no hay match, no se manda el campo. Commit: ca3c2a2

**GTIN — validación numérica**
Antes de mandar el EAN a ML se valida con `isdigit()`. Si no es numérico
(ej: "LFDB00803") se ignora. Commit: ca3c2a2

**Búsqueda catálogo ML — más resultados**
El Paso 3 ahora pide 20 resultados en vez de 6 y no filtra por status
(se muestra el status en el selector). Commit: 9468b60

### Fixes sesión 2026-07-03

**SELLER_PACKAGE_* aceptado en modo catálogo**
Antes `crear_item()` descartaba estos atributos en modo catálogo (solo dejaba
GTIN/VALUE_ADDED_TAX/IMPORT_DUTY), asumiendo que el catálogo siempre trae las
dimensiones. Algunas categorías las exigen igual (error 400 "seller_package_*
are all required"). Ahora se incluyen en el `allowed` set de modo catálogo.
Paso 4 muestra los 4 campos de dimensiones siempre (antes se ocultaban con
catálogo seleccionado), renombrados **Ancho/Alto/Profundidad/Peso**, y avisa
con un warning si `ml_attrs` marca alguno como `required`/`conditional_required`.
Commit: d054fb5

**Búsqueda de catálogo por EAN/GTIN (prioridad sobre nombre)**
Nuevo método `buscar_en_catalogo_por_gtin()` en `mercadolibre_publish.py` usa
`product_identifier` (mismo parámetro que la pantalla "Por código" de ML).
Paso 3 lo intenta automáticamente si Flexxus devolvió un EAN numérico, antes
de la búsqueda manual por nombre. Búsqueda por SKU propio no es posible: la
API de catálogo de ML solo acepta `q` o `product_identifier`, no conoce SKUs
internos de vendedor. Los resultados por nombre ahora muestran miniatura
(`pictures[0].url`) + botón "Elegir" en vez de un selectbox de texto.
Commit: d054fb5

**Campo GTIN duplicado en Paso 4**
El listado de atributos requeridos incluía un input de texto libre para GTIN,
redundante con el que ya arma `crear_item()` automáticamente desde el EAN de
Flexxus — podía mandar el atributo GTIN duplicado en el payload. Se agregó
`"GTIN"` a `ATTRS_BLACKLIST` (mismo tratamiento que VALUE_ADDED_TAX/IMPORT_DUTY,
que también se resuelven por fuera del listado genérico). Commit: 81d1946

**SKU propio no aparecía en publicaciones de catálogo (SELLER_SKU)**
El código solo mandaba `seller_custom_field` (top-level), que ML no usa para
poblar el campo "Código de identificación (SKU)" visible en el editor de la
publicación ni para la búsqueda `?seller_sku=`. La forma correcta en modo
catálogo es el **atributo** `SELLER_SKU` (distinto del campo top-level
`seller_sku`, que sigue rechazado por ML en catálogo). Ahora `crear_item()`
agrega `{"id": "SELLER_SKU", "value_name": seller_custom_field}` a los
atributos permitidos en ambos modos, sin tocar `seller_custom_field` (quedan
sin relación entre sí, cada uno con su propio uso). Commit: c1e7d5b

**Dimensiones mínimas sugeridas desde el catálogo**
Error "seller_package_height are too small for the product dimensions":
el paquete declarado era menor a las medidas reales del producto de catálogo
(`PACKAGE_WIDTH/HEIGHT/LENGTH/WEIGHT`, sin prefijo `SELLER_`, vienen en el
detalle de `get_catalog_product()`). Ahora se parsean (`_parse_pkg_dims_from_catalog`)
y se precargan como piso mínimo editable en los inputs de Ancho/Alto/Profundidad/Peso
del Paso 4, con un aviso de dónde salió el valor. Solo aplica con catalog_product_id
seleccionado. Commit: 157c277

**Precio de resumen (Paso 1) no reflejaba Flexxus**
La tarjeta de resumen mostraba "Precio PS en ARS" = precio de PrestaShop × TC,
incluso cuando había precio de Flexxus (Lista 5) disponible — confundía porque
parecía el precio real pero no era el que se termina usando. Ahora, si hay
`flexxus_price`, muestra "Precio Flexxus (Lista 5)" = `flexxus_price['pesos']`.

**precio_final ignoraba el tipo de cambio editable (Paso 5)**
`precio_final` usaba `flexxus_price["pesos"]`, calculado con el TC congelado
del Excel al momento de cargarlo — si el usuario ajustaba el input "Tipo de
cambio USD → ARS" (pensado justamente para eso), el ajuste se reflejaba en el
`st.metric` de arriba pero no en el precio realmente publicado en ML. Ahora
`precio_final = precio_ars`, que ya usa la base correcta (Flexxus con IVA, o
PS de fallback) junto con el TC editable — consistente con lo que se muestra
en pantalla.

## Branches

- `main` — stable/production
- `testdev` — active development branch
