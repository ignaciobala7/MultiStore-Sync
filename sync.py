"""
sync.py — Sincronizador de productos Mercado Libre → Tiendanube
===============================================================

Flujo principal:
  1. Carga tokens de ML (desde ml_tokens.json o variables de entorno).
  2. Construye un caché de SKUs existentes en Tiendanube.
  3. Itera sobre todas las publicaciones activas de ML.
  4. Si el SKU ya existe en Tiendanube → lo omite.
  5. Llama a Claude (IA) para generar título SEO, descripción HTML y tags.
  6. Si IA falla → usa los datos originales de ML como fallback.
  7. Crea el producto en Tiendanube e intenta subir las imágenes.
  8. Imprime un resumen final de resultados.

Ejecución:
  python sync.py

Programación (ver README.md para más opciones):
  - Windows Task Scheduler
  - Cron (Linux/Mac)
  - n8n → nodo Execute Command
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from clients.mercadolibre import MercadoLibreClient, MercadoLibreAuthError
from clients.tiendanube import TiendanubeClient
from mapper import extract_image_urls, ml_to_tiendanube
from enricher import enriquecer_producto, limpiar_descripcion_ml
from optimize_images import procesar_y_hostear

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

TOKEN_FILE = Path("ml_tokens.json")

# Pausa entre el procesamiento de cada ítem (segundos).
# Evita saturar las APIs y superar los rate limits.
ITEM_DELAY = 0.6

# Pausa entre subidas de imagen por producto.
IMAGE_DELAY = 0.3


# ──────────────────────────────────────────────────────────────────────────────
# Carga de configuración
# ──────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """
    Lee variables desde .env y valida que las obligatorias estén presentes.
    Levanta SystemExit con mensaje claro si falta alguna.
    """
    load_dotenv()

    required = [
        "ML_CLIENT_ID",
        "ML_CLIENT_SECRET",
        "ML_ACCESS_TOKEN",
        "ML_REFRESH_TOKEN",
        "TN_STORE_ID",
        "TN_ACCESS_TOKEN",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"[ERROR] Faltan variables de entorno: {', '.join(missing)}")
        print("        Copiá .env.example a .env y completá los valores.")
        sys.exit(1)

    return {
        "ml_client_id": os.environ["ML_CLIENT_ID"],
        "ml_client_secret": os.environ["ML_CLIENT_SECRET"],
        "ml_access_token": os.environ["ML_ACCESS_TOKEN"],
        "ml_refresh_token": os.environ["ML_REFRESH_TOKEN"],
        "tn_store_id": os.environ["TN_STORE_ID"],
        "tn_access_token": os.environ["TN_ACCESS_TOKEN"],
        "tn_app_name": os.environ.get("TN_APP_NAME", "ML-Tiendanube-Sync"),
        "tn_user_email": os.environ.get("TN_USER_EMAIL", "user@example.com"),
    }


def load_ml_tokens(config: dict) -> tuple[str, str]:
    """
    Prioridad de tokens:
      1. ml_tokens.json (contiene los tokens más recientes tras renovaciones)
      2. Variables de entorno del .env (primera ejecución)
    """
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data["access_token"], data["refresh_token"]
        except (json.JSONDecodeError, KeyError):
            print("[WARN] ml_tokens.json inválido, usando tokens del .env")

    return config["ml_access_token"], config["ml_refresh_token"]


# ──────────────────────────────────────────────────────────────────────────────
# Procesamiento de imágenes
# ──────────────────────────────────────────────────────────────────────────────

def _aplicar_enriquecimiento(payload: dict, enriched: dict) -> dict:
    """
    Sobreescribe los campos del payload base con los datos generados por la IA.
    Solo reemplaza los campos que la IA devolvió con contenido válido.
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


def upload_images(tn: TiendanubeClient, product_id: int, item: dict) -> int:
    """
    Sube todas las imagenes de un item de ML al producto recien creado en TN.
    Procesa cada imagen (nitidez + 400x500) y la hostea en catbox.moe antes de subir.
    Continua aunque falle alguna imagen individual.

    Retorna la cantidad de imagenes subidas exitosamente.
    """
    urls = extract_image_urls(item)
    uploaded = 0

    for url in urls:
        try:
            url_publica = procesar_y_hostear(url)
            tn.add_image(product_id, url_publica)
            uploaded += 1
            time.sleep(IMAGE_DELAY)
        except Exception as e:
            print(f"\n      [WARN] No se pudo subir imagen ({url[:60]}...): {e}")

    return uploaded


# ──────────────────────────────────────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Configuración ──────────────────────────────────────────────────────────
    config = load_config()
    access_token, refresh_token = load_ml_tokens(config)

    ml = MercadoLibreClient(
        client_id=config["ml_client_id"],
        client_secret=config["ml_client_secret"],
        access_token=access_token,
        refresh_token=refresh_token,
    )
    tn = TiendanubeClient(
        store_id=config["tn_store_id"],
        access_token=config["tn_access_token"],
        app_name=config["tn_app_name"],
        user_email=config["tn_user_email"],
    )

    # Contadores del resumen final
    total_analyzed = 0
    total_skipped = 0
    total_created = 0
    total_errors = 0

    print()
    print("=" * 65)
    print("  Sincronización Mercado Libre → Tiendanube")
    print("=" * 65)

    # ── Paso 1: Caché de SKUs de Tiendanube ───────────────────────────────────
    print("\n[1/4] Cargando SKUs existentes en Tiendanube...")
    try:
        existing_skus = tn.get_all_skus()
    except Exception as e:
        print(f"[ERROR FATAL] No se pudo conectar a Tiendanube: {e}")
        sys.exit(1)
    print(f"      {len(existing_skus)} SKUs encontrados.")

    # ── Paso 2: Listado de publicaciones activas en ML ─────────────────────────
    print("\n[2/4] Obteniendo publicaciones activas de Mercado Libre...")
    try:
        item_ids = ml.get_active_item_ids()
    except MercadoLibreAuthError as e:
        print(f"[ERROR FATAL] Autenticación con Mercado Libre falló: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR FATAL] No se pudo obtener el listado de ML: {e}")
        sys.exit(1)

    total_items = len(item_ids)
    print(f"      {total_items} publicaciones activas encontradas.")

    if total_items == 0:
        print("\nNo hay publicaciones activas. Finalizando.")
        sys.exit(0)

    # ── Paso 3: Procesamiento ítem por ítem ───────────────────────────────────
    print(f"\n[3/4] Procesando {total_items} publicaciones...\n")

    for idx, item_id in enumerate(item_ids, start=1):
        prefix = f"  [{idx:>4}/{total_items}] {item_id}"
        total_analyzed += 1

        try:
            # Obtener detalles del ítem
            item = ml.get_item_details(item_id)

            # Obtener descripción (request separado en la API de ML)
            description = ml.get_item_description(item_id)

            # Mapear al formato base de Tiendanube (con descripción limpia como fallback)
            desc_limpia = limpiar_descripcion_ml(description)
            payload, sku = ml_to_tiendanube(item, desc_limpia)

            # ── Verificación de stock ──────────────────────────────────────────
            if not item.get("available_quantity", 0):
                print(f"{prefix} → OMITIDO  (sin stock)")
                total_skipped += 1
                continue

            # ── Verificación de duplicado ──────────────────────────────────────
            if sku in existing_skus:
                print(f"{prefix} → OMITIDO  (SKU '{sku}' ya existe)")
                total_skipped += 1
                continue

            # ── Enriquecimiento con IA (copywriting + SEO) ────────────────────
            # Si falla, el producto se publica con los datos originales de ML.
            enriched = enriquecer_producto(item, description)
            if enriched:
                payload = _aplicar_enriquecimiento(payload, enriched)

            # ── Creación del producto ──────────────────────────────────────────
            created = tn.create_product(payload)
            product_id = created["id"]

            # ── Subida de imágenes (no bloquea si falla) ───────────────────────
            img_count = upload_images(tn, product_id, item)

            # Registrar SKU en el caché local para evitar dobles creaciones
            # si el mismo SKU aparece duplicado dentro de ML.
            existing_skus.add(sku)
            total_created += 1

            ia_label = " [IA]" if enriched else ""
            print(
                f"{prefix} → CREADO{ia_label}   "
                f"(TN ID: {product_id}, SKU: '{sku}', {img_count} imágen/es)"
            )

        except Exception as e:
            total_errors += 1
            print(f"{prefix} → ERROR    {e}")

        time.sleep(ITEM_DELAY)

    # ── Paso 4: Resumen ────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("[4/4] Sincronización completada — Resumen")
    print("=" * 65)
    print(f"  Publicaciones analizadas : {total_analyzed}")
    print(f"  Omitidas (ya existían)   : {total_skipped}")
    print(f"  Creadas exitosamente     : {total_created}")
    print(f"  Errores                  : {total_errors}")
    print("=" * 65)
    print()

    # Exit code no-zero si hubo errores (útil para monitoreo en CI/n8n)
    if total_errors > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
