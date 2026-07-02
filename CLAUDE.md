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

### Fixes sesión 2026-07-02: 3 errores de validación ML

**SELLER_PACKAGE_* sin unidad**
ML rechazaba estos atributos porque el `value_name` era un número pelado
(ej. `"10"`) sin unidad. Ahora Paso 4 concatena la unidad al guardar:
ancho/largo/altura → `"{valor} cm"`, peso → `"{valor} g"`. El input de peso
sigue en kg (label "Peso (kg)", como lo carga el usuario) pero al publicar
se convierte a gramos (`×1000`) antes de mandarlo, porque
`SELLER_PACKAGE_WEIGHT` en ML espera gramos. Los 4 inputs ahora usan
`value=None, placeholder="10"` en vez de arrancar en `0.0`, para que quede
claro qué formato se espera. Archivo: `tabs/ps_ml.py`

**IMPORT_DUTY hardcodeado a "No aplica"**
Ese string no es necesariamente un `value_name` válido para todas las
categorías. Ahora, al obtener `ml_attrs` en Paso 4, se busca el atributo
`IMPORT_DUTY` y se toma el primer valor cuyo nombre contenga "no", "0" o
"exento" (case-insensitive), guardado en `st.session_state["ps_ml_import_duty"]`.
Si no se encuentra ninguno, no se manda el atributo. `crear_item()` ahora
tiene `import_duty: str = ""` (antes `"No aplica"`) y solo lo agrega a
`attributes` si viene no vacío. Archivos: `tabs/ps_ml.py`,
`clients/mercadolibre_publish.py`

**GTIN no numérico**
`ps_ml_ean` (viene de `FlexxusClient.get_ean()`) podía traer valores no
puramente numéricos. Antes de pasarlo a `crear_item()`, Paso 5 ahora valida
`str(ean).isdigit()`; si no lo es, se manda `gtin=""`. Archivo: `tabs/ps_ml.py`

### Fix: búsqueda en catálogo ML devolvía muy pocos resultados

`buscar_en_catalogo()` pedía `limit=6` y filtraba a solo `status == "active"`,
por lo que el usuario veía 2-3 opciones aunque ML mostrara más matches en el
flujo de publicación manual (algunos con status `under_review`, válidos igual
para catálogo). Fix: `limit` default subió a 20 (Paso 3 en `ps_ml.py` pide
20), y se sacó el filtro de status — ahora se listan todos los resultados que
devuelve `/products/search`, mostrando el status entre paréntesis en el
selectbox cuando no es `active` para que el usuario decida. Archivos:
`clients/mercadolibre_publish.py`, `tabs/ps_ml.py`

## Branches

- `main` — stable/production
- `testdev` — active development branch
