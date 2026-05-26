"""
utils.py — Funciones auxiliares compartidas entre todas las pestañas.

Qué hay acá:
  - cargar_skus_tn    : carga (y cachea) los SKUs ya publicados en Tiendanube
  - _aplicar_enriched : aplica los datos generados por IA (Gemini) al payload de TN
  - publicar_item_ml  : flujo completo para publicar un ítem de ML en Tiendanube
  - exportar_csv      : guarda los resultados de una sesión de publicación en un CSV

Nota: estas funciones leen los clientes (ml, tn, ps) desde st.session_state,
que es inicializado al arrancar la app en app.py.
"""

import csv
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from mapper import extract_image_urls, ml_to_tiendanube, extract_sku
from enricher import enriquecer_producto, limpiar_descripcion_ml
from optimize_images import procesar_y_hostear

# Pausa entre subidas de imágenes para no superar el rate-limit de la API
IMAGE_DELAY = 0.3


# ── SKUs de Tiendanube ─────────────────────────────────────────────────────────

def cargar_skus_tn(force: bool = False) -> set[str]:
    """
    Devuelve el conjunto de SKUs que ya existen en Tiendanube.

    La primera vez consulta la API; las siguientes usa el caché en session_state.
    Pasá force=True para forzar una recarga (ej: después de publicar un lote).
    """
    tn = st.session_state.tn
    if not st.session_state.skus_loaded or force:
        with st.spinner("Cargando SKUs existentes en Tiendanube..."):
            st.session_state.existing_skus = tn.get_all_skus()
            st.session_state.skus_loaded = True
    return st.session_state.existing_skus


# ── Enriquecimiento con IA ─────────────────────────────────────────────────────

def _aplicar_enriched(payload: dict, enriched: dict) -> dict:
    """
    Sobrescribe campos del payload de Tiendanube con los datos generados por IA.

    Solo aplica los campos que la IA haya completado: puede que Gemini devuelva
    solo descripción pero no tags, por ejemplo — se respetan los campos vacíos.
    """
    if enriched.get("seo_titulo"):
        payload["name"] = {"es": enriched["seo_titulo"]}
    if enriched.get("descripcion_html"):
        payload["description"] = {"es": enriched["descripcion_html"]}
    if enriched.get("tags"):
        payload["tags"] = enriched["tags"]
    if enriched.get("seo_titulo"):
        payload["seo_title"] = {"es": enriched["seo_titulo"]}
    if enriched.get("seo_descripcion"):
        payload["seo_description"] = {"es": enriched["seo_descripcion"]}
    return payload


# ── Publicación ML → Tiendanube ───────────────────────────────────────────────

def publicar_item_ml(item: dict, log_fn=None) -> dict:
    """
    Flujo completo para publicar un ítem de Mercado Libre en Tiendanube.

    Pasos que ejecuta:
      1. Obtiene la descripción del ítem desde la API de ML y la limpia (quita HTML roto, etc.)
      2. Convierte el ítem al formato que espera la API de TN (via mapper.py)
      3. Llama a Gemini para enriquecer título, descripción y tags (via enricher.py)
      4. Crea el producto en Tiendanube
      5. Sube imágenes: primero intenta desde PrestaShop/Infoandina,
         si no hay, usa las imágenes de ML como respaldo
      6. Retorna un dict con el resumen: id TN, SKU, imágenes subidas, fuente, etc.

    Args:
      item    : dict con los datos del ítem de ML (id, title, price, etc.)
      log_fn  : función opcional para mostrar logs en la UI; recibe un string.
                Si es None, los logs se silencian.
    """
    ml = st.session_state.ml
    tn = st.session_state.tn
    ps = st.session_state.ps

    def log(msg):
        if log_fn:
            log_fn(msg)

    # Paso 1: descripción limpia
    description = ml.get_item_description(item["id"])
    desc_limpia = limpiar_descripcion_ml(description)
    payload, sku = ml_to_tiendanube(item, desc_limpia)

    # Paso 2: enriquecimiento con IA
    log("Generando descripcion con IA (Gemini)...")
    enriched = enriquecer_producto(item, description)
    if enriched:
        payload = _aplicar_enriched(payload, enriched)
        log("Descripcion IA lista.")
    else:
        log("IA no disponible, usando descripcion limpia de ML.")

    # Paso 3: crear producto en TN
    log("Creando producto en Tiendanube...")
    creado = tn.create_product(payload)
    product_id = creado["id"]

    # Paso 4: subir imágenes (PS tiene prioridad sobre ML)
    ps_urls = ps.get_image_urls_by_sku(sku)
    ml_urls = extract_image_urls(item)
    imgs_ok = 0
    fuente = "ML"

    if ps_urls:
        log(f"Subiendo {len(ps_urls)} imagen/es desde PrestaShop...")
        for url in ps_urls:
            try:
                url_p = procesar_y_hostear(url)
                tn.add_image(product_id, url_p)
                imgs_ok += 1
                fuente = "PS"
                time.sleep(IMAGE_DELAY)
            except Exception as e:
                log(f"  Imagen PS fallida: {e}")

    # Si no se subió ninguna imagen de PS, usar las de ML
    if imgs_ok == 0:
        fuente = "ML"
        log(f"Subiendo {len(ml_urls)} imagen/es desde Mercado Libre...")
        for url in ml_urls:
            try:
                url_p = procesar_y_hostear(url)
                tn.add_image(product_id, url_p)
                imgs_ok += 1
                time.sleep(IMAGE_DELAY)
            except Exception as e:
                log(f"  Imagen ML fallida: {e}")

    total_imgs = len(ps_urls) if ps_urls else len(ml_urls)
    return {
        "tn_id": product_id,
        "sku": sku,
        "titulo": item.get("title", ""),
        "mla_id": item["id"],
        "image_source": fuente,
        "imagenes": f"{imgs_ok}/{total_imgs}",
        "ia": bool(enriched),
    }


# ── Exportación CSV ───────────────────────────────────────────────────────────

def exportar_csv(rows: list[dict]) -> Path:
    """
    Guarda los resultados de publicación en un CSV con timestamp en el nombre.

    El archivo se crea en el directorio raíz del proyecto:
      skus_publicados_YYYYMMDD_HHMMSS.csv

    Columnas exportadas: tn_id, sku, titulo, mla_id, image_source
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"skus_publicados_{ts}.csv")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["tn_id", "sku", "titulo", "mla_id", "image_source"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {k: r.get(k, "") for k in ["tn_id", "sku", "titulo", "mla_id", "image_source"]}
            )
    return path
