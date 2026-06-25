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

**Búsqueda en ML:** `GET /users/{user_id}/items/search?sku={seller_custom_field}`
Permite recuperar el MLA de un SKU aunque no esté en el CSV local (útil para
SKUs publicados antes de implementar el tracker).

**Fixes aplicados post-implementación:**
- `sku_tracker.py`: `verify_with_ml` captura excepciones sin relanzar — el error
  se loguea en consola de Streamlit pero no interrumpe el flujo de la UI.
- `ps_ml.py`: al hacer nueva búsqueda exitosa, limpia todas las keys de
  session_state que empiecen con `ps_ml_choice_` — cubre el caso de cambiar
  de SKU sin haber llegado a publicar.
- `mercadolibre.py`: nuevo método público `update_item(item_id, payload)` que
  delega al `_put` interno. El tab ps_ml.py usa este método en lugar de llamar
  a `_put` directamente.

## Branches

- `main` — stable/production
- `testdev` — active development branch
