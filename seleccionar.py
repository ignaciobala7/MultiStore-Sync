"""
seleccionar.py -- Publicador interactivo Mercado Libre -> Tiendanube
==================================================================

Flujo:
  1. Conecta a ML y lista todas tus publicaciones activas.
  2. Las muestra en pantalla con título, precio y stock.
  3. Vos elegís cuáles querés publicar en Tiendanube (por número).
  4. Solo se crean las que seleccionaste.
  5. Se omiten automáticamente las que ya existen en Tiendanube (por SKU).

Ejecución:
  python seleccionar.py
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from clients.mercadolibre import MercadoLibreClient, MercadoLibreAuthError
from clients.tiendanube import TiendanubeClient
from clients.prestashop import PrestaShopClient
from mapper import extract_image_urls, ml_to_tiendanube
from enricher import enriquecer_producto, limpiar_descripcion_ml
from optimize_images import procesar_y_hostear

TOKEN_FILE = Path("ml_tokens.json")
IMAGE_DELAY = 0.3


# ------------------------------------------------------------------------------
# Config y clientes (igual que sync.py)
# ------------------------------------------------------------------------------

def load_config() -> dict:
    load_dotenv()
    required = ["ML_CLIENT_ID", "ML_CLIENT_SECRET", "ML_ACCESS_TOKEN",
                "ML_REFRESH_TOKEN", "TN_STORE_ID", "TN_ACCESS_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n[ERROR] Faltan variables en el .env: {', '.join(missing)}")
        sys.exit(1)
    return {
        "ml_client_id":     os.environ["ML_CLIENT_ID"],
        "ml_client_secret": os.environ["ML_CLIENT_SECRET"],
        "ml_access_token":  os.environ["ML_ACCESS_TOKEN"],
        "ml_refresh_token": os.environ["ML_REFRESH_TOKEN"],
        "tn_store_id":      os.environ["TN_STORE_ID"],
        "tn_access_token":  os.environ["TN_ACCESS_TOKEN"],
        "tn_app_name":      os.environ.get("TN_APP_NAME", "ML-Tiendanube-Sync"),
        "tn_user_email":    os.environ.get("TN_USER_EMAIL", "user@example.com"),
    }


def load_ml_tokens(config: dict) -> tuple[str, str]:
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data["access_token"], data["refresh_token"]
        except (json.JSONDecodeError, KeyError):
            pass
    return config["ml_access_token"], config["ml_refresh_token"]


# ------------------------------------------------------------------------------
# Selección interactiva
# ------------------------------------------------------------------------------

def mostrar_catalogo(items_con_detalle: list[dict], existing_skus: set[str], offset_num: int = 0) -> None:
    """Imprime la tabla de productos. offset_num ajusta la numeracion si es un lote parcial."""
    print()
    print(f"  {'#':<5} {'ID ML':<15} {'TITULO':<45} {'PRECIO':>10} {'STOCK':>6}  {'ESTADO'}")
    print("  " + "-" * 100)

    for i, item in enumerate(items_con_detalle, start=1):
        from mapper import extract_sku
        sku = extract_sku(item)
        ya_existe = sku in existing_skus

        titulo = (item.get("title") or "")[:43]
        precio = item.get("price", 0)
        stock  = item.get("available_quantity", 0)
        if ya_existe:
            estado = "[OK] ya en TN"
        elif stock <= 0:
            estado = "sin stock"
        else:
            estado = "disponible"

        num = i  # numero relativo al lote para seleccion
        print(f"  {num:<5} {item['id']:<15} {titulo:<45} ${precio:>9,.0f} {stock:>6}   {estado}")


def parsear_seleccion(entrada: str, total: int) -> list[int]:
    """
    Interpreta la entrada del usuario y devuelve una lista de índices (0-based).

    Formatos aceptados:
      - "todos"        -> todos los productos
      - "1 3 5"        -> números sueltos separados por espacio o coma
      - "2-6"          -> rangos
      - Combinaciones: "1 3-5 8"
    """
    entrada = entrada.strip().lower()

    if entrada in ("todos", "all", "*"):
        return list(range(total))

    indices = set()
    partes = entrada.replace(",", " ").split()

    for parte in partes:
        if "-" in parte:
            try:
                inicio, fin = parte.split("-", 1)
                for n in range(int(inicio), int(fin) + 1):
                    if 1 <= n <= total:
                        indices.add(n - 1)
            except ValueError:
                print(f"  [WARN] Rango inválido ignorado: '{parte}'")
        else:
            try:
                n = int(parte)
                if 1 <= n <= total:
                    indices.add(n - 1)
                else:
                    print(f"  [WARN] Número fuera de rango ignorado: {n}")
            except ValueError:
                print(f"  [WARN] Valor inválido ignorado: '{parte}'")

    return sorted(indices)


# ------------------------------------------------------------------------------
# Publicación
# ------------------------------------------------------------------------------

def publicar_producto(tn: TiendanubeClient, ps: PrestaShopClient, item: dict, description: str) -> tuple[bool, str, int, str]:
    """
    Enriquece con IA y crea el producto en Tiendanube. Sube sus imágenes.
    Prioriza imágenes de PrestaShop (alta calidad) sobre las de ML.
    Retorna (éxito, mensaje, tn_id, image_source).
    """
    desc_limpia = limpiar_descripcion_ml(description)
    payload, sku = ml_to_tiendanube(item, desc_limpia)

    # Enriquecimiento con IA: genera título SEO, descripción HTML y tags
    print(f"\n    [IA] Generando ficha optimizada...", end=" ", flush=True)
    enriched = enriquecer_producto(item, description)
    if enriched:
        payload = _aplicar_enriquecimiento(payload, enriched)
        print("OK")
    else:
        print("falló, usando descripción limpia.")

    creado = tn.create_product(payload)
    product_id = creado["id"]

    # Imagenes: intenta PS primero, si falla usa ML como fallback
    ps_urls = ps.get_image_urls_by_sku(sku)
    ml_urls = extract_image_urls(item)

    imgs_ok = 0
    fuente = "ML"

    if ps_urls:
        for url in ps_urls:
            try:
                url_procesada = procesar_y_hostear(url)
                tn.add_image(product_id, url_procesada)
                imgs_ok += 1
                fuente = "PS"
                time.sleep(IMAGE_DELAY)
            except Exception:
                pass

    if imgs_ok == 0:
        fuente = "ML"
        for url in ml_urls:
            try:
                url_procesada = procesar_y_hostear(url)
                tn.add_image(product_id, url_procesada)
                imgs_ok += 1
                time.sleep(IMAGE_DELAY)
            except Exception as e:
                print(f"    [WARN] Imagen no subida: {e}")

    total_imgs = len(ps_urls) if ps_urls else len(ml_urls)
    ia_label = " [IA]" if enriched else ""
    return True, f"TN ID: {product_id} | SKU: '{sku}' | {imgs_ok}/{total_imgs} imgs [{fuente}]{ia_label}", product_id, fuente


def _aplicar_enriquecimiento(payload: dict, enriched: dict) -> dict:
    """Sobreescribe el payload base con los datos generados por la IA."""
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


# ------------------------------------------------------------------------------
# Flujo: publicar SKU especifico
# ------------------------------------------------------------------------------

def _flujo_sku_especifico(ml, tn, ps, existing_skus: set[str]) -> None:
    """Busca un SKU especifico en ML y lo publica en Tiendanube."""
    import csv
    from datetime import datetime

    print()
    while True:
        sku_input = input("  Ingresa el SKU a publicar: ").strip()
        if sku_input:
            break
        print("  El SKU no puede estar vacio.")

    # Verificar si ya existe en TN
    if sku_input in existing_skus:
        print(f"\n  El SKU '{sku_input}' ya existe en Tiendanube. No se publicara.")
        return

    print(f"\n  Buscando SKU '{sku_input}' en Mercado Libre...", end=" ", flush=True)
    items = ml.search_items_by_sku(sku_input)

    if not items:
        print("no encontrado.")
        print(f"  No se encontro ninguna publicacion activa con SKU '{sku_input}' en ML.")
        return

    print(f"{len(items)} publicacion/es encontrada/s.")
    print()

    # Mostrar los items encontrados
    for i, item in enumerate(items, start=1):
        from mapper import extract_sku
        sku_real = extract_sku(item)
        titulo = (item.get("title") or "")[:55]
        precio = item.get("price", 0)
        stock  = item.get("available_quantity", 0)
        print(f"  [{i}] {item['id']} | {titulo} | ${precio:,.0f} | Stock: {stock} | SKU: {sku_real}")

    print()

    # Si hay mas de uno, elegir cual
    if len(items) == 1:
        elegido = items[0]
    else:
        while True:
            try:
                n = int(input(f"  Cual publicar? (1-{len(items)}): ").strip())
                if 1 <= n <= len(items):
                    elegido = items[n - 1]
                    break
            except ValueError:
                pass
            print(f"  Ingresa un numero entre 1 y {len(items)}.")

    stock = elegido.get("available_quantity", 0)
    if stock <= 0:
        print(f"\n  El producto no tiene stock. No se publicara.")
        return

    from mapper import extract_sku
    sku_final = extract_sku(elegido)
    titulo = (elegido.get("title") or "")[:55]
    print(f"\n  Producto: {titulo}")
    print(f"  SKU: {sku_final} | Stock: {stock}")
    print()

    conf = input("  Confirmas publicar este producto en Tiendanube? (s/n): ").strip().lower()
    if conf not in ("s", "si", "y", "yes"):
        print("\n  Cancelado.")
        return

    print()
    print("=" * 65)
    print("  PUBLICANDO EN TIENDANUBE...")
    print("=" * 65)

    try:
        description = ml.get_item_description(elegido["id"])
        ok, msg, tn_id, image_source = publicar_producto(tn, ps, elegido, description)
        existing_skus.add(sku_final)
        print(f"\n  OK - {msg}")

        # Exportar CSV
        csv_path = Path(f"skus_publicados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["tn_id", "sku", "titulo", "mla_id", "image_source"])
            writer.writeheader()
            writer.writerow({"tn_id": tn_id, "sku": sku_final, "titulo": elegido.get("title", ""), "mla_id": elegido["id"], "image_source": image_source})
        print(f"  CSV exportado -> {csv_path}")
    except Exception as e:
        print(f"\n  ERROR: {e}")


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

def main() -> None:
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
    ps = PrestaShopClient(api_key=os.environ.get("PS_API_KEY", ""))

    print()
    print("=" * 65)
    print("  Publicador interactivo: Mercado Libre -> Tiendanube")
    print("=" * 65)

    # -- SKUs existentes en TN -------------------------------------------------
    print("\nCargando SKUs existentes en Tiendanube...", end=" ", flush=True)
    existing_skus = tn.get_all_skus()
    print(f"{len(existing_skus)} encontrados.")

    # -- MENU PRINCIPAL --------------------------------------------------------
    print()
    print("  Que deseas hacer?")
    print()
    print("  [1] Explorar catalogo de ML y elegir productos a publicar")
    print("  [2] Publicar un SKU especifico")
    print()
    while True:
        opcion = input("  Tu opcion (1/2): ").strip()
        if opcion in ("1", "2"):
            break
        print("  Ingresa 1 o 2.")

    # ==========================================================================
    # OPCION 2: SKU ESPECIFICO
    # ==========================================================================
    if opcion == "2":
        _flujo_sku_especifico(ml, tn, ps, existing_skus)
        return

    # ==========================================================================
    # OPCION 1: CATALOGO POR LOTES
    # ==========================================================================
    LOTE = 200  # productos por lote

    try:
        _, total_ml = ml.get_active_item_ids_page(offset=0, limit=1)
    except MercadoLibreAuthError as e:
        print(f"\n[ERROR] Autenticacion ML fallo: {e}")
        sys.exit(1)

    print(f"\nTenes {total_ml} publicaciones activas en Mercado Libre.")
    print(f"Se cargan de a {LOTE} para no esperar tanto.\n")

    items_detalle: list[dict] = []
    offset = 0
    a_publicar: list[int] = []

    while offset < min(total_ml, 1000):
        # -- Cargar lote de IDs ------------------------------------------------
        ids_lote, _ = ml.get_active_item_ids_page(offset=offset, limit=LOTE)
        if not ids_lote:
            break

        print(f"Descargando detalles ({offset + 1}-{offset + len(ids_lote)} de {total_ml})...",
              flush=True)
        base_idx = len(items_detalle)
        for i, item_id in enumerate(ids_lote, start=1):
            try:
                items_detalle.append(ml.get_item_details(item_id))
                if i % 50 == 0 or i == len(ids_lote):
                    print(f"  {i}/{len(ids_lote)}...", flush=True)
                time.sleep(0.2)
            except Exception as e:
                print(f"  [WARN] No se pudo obtener {item_id}: {e}")

        # -- Mostrar catalogo del lote -----------------------------------------
        print()
        print("=" * 65)
        print(f"  PRODUCTOS {offset + 1} - {offset + len(ids_lote)}")
        print("=" * 65)
        mostrar_catalogo(items_detalle[base_idx:], existing_skus, offset_num=base_idx)

        disp_lote = sum(
            1 for item in items_detalle[base_idx:]
            if __sku_disponible(item, existing_skus)
        )
        print()
        print(f"  En este lote: {len(ids_lote)} productos | {disp_lote} disponibles para publicar")
        print()

        # -- Seleccion del lote ------------------------------------------------
        print("  Que deseas hacer?")
        print("  [s] Seleccionar productos de este lote para publicar")
        print("  [m] Cargar mas productos (siguiente lote)")
        print("  [n] Cancelar")
        print()
        while True:
            accion = input("  Accion: ").strip().lower()
            if accion in ("s", "m", "n"):
                break
            print("  Ingresa s, m o n.")

        if accion == "n":
            print("\n  Cancelado.")
            sys.exit(0)

        if accion == "s":
            # Seleccionar del lote actual (numeros relativos al lote)
            print()
            print("  Ingresa los numeros del lote actual (los que ves arriba):")
            print("  Ej: 1-10  |  1 3 5  |  todos")
            print()
            while True:
                entrada = input("  Tu seleccion: ").strip()
                if not entrada:
                    continue

                indices_rel = parsear_seleccion(entrada, len(ids_lote))
                indices_abs = [base_idx + r for r in indices_rel
                               if __sku_disponible(items_detalle[base_idx + r], existing_skus)]
                ya_en_tn = len(indices_rel) - len(indices_abs)

                if not indices_abs:
                    print("  Todos los seleccionados ya estan en Tiendanube. Elegí otros.")
                    continue

                print()
                print(f"  A publicar: {len(indices_abs)}  |  Ya en TN (omitidos): {ya_en_tn}")
                conf = input(f"  Confirmas publicar {len(indices_abs)} producto/s? (s/n): ").strip().lower()
                if conf in ("s", "si", "y", "yes"):
                    a_publicar.extend(indices_abs)
                    break
                elif conf in ("n", "no"):
                    break
                else:
                    print("  Ingresa s o n.")

            # Preguntar si ademas quiere cargar mas
            if offset + LOTE < min(total_ml, 1000):
                print()
                mas = input("  Cargar otro lote tambien? (s/n): ").strip().lower()
                if mas not in ("s", "si", "y"):
                    break
            else:
                break

        offset += LOTE

    if not a_publicar:
        print("\nNada seleccionado para publicar. Hasta luego.")
        sys.exit(0)

    # -- 6. Publicación --------------------------------------------------------
    print()
    print("=" * 65)
    print("  PUBLICANDO EN TIENDANUBE...")
    print("=" * 65)
    print()

    creados = 0
    errores = 0
    skus_nuevos: list[dict] = []

    for orden, idx in enumerate(a_publicar, start=1):
        item = items_detalle[idx]
        titulo = (item.get("title") or "")[:50]
        print(f"  [{orden}/{len(a_publicar)}] {titulo}...", end=" ", flush=True)

        from mapper import extract_sku
        sku = extract_sku(item)
        if sku in existing_skus:
            print(f"OMITIDO — SKU '{sku}' ya publicado en esta sesión")
            continue

        stock = item.get("available_quantity", 0)
        if not stock or stock <= 0:
            print(f"OMITIDO — sin stock")
            continue

        try:
            description = ml.get_item_description(item["id"])
            ok, msg, tn_id, image_source = publicar_producto(tn, ps, item, description)
            existing_skus.add(sku)
            skus_nuevos.append({"tn_id": tn_id, "sku": sku, "titulo": item.get("title", ""), "mla_id": item["id"], "image_source": image_source})
            print(f"OK — {msg}")
            creados += 1
        except Exception as e:
            print(f"ERROR — {e}")
            errores += 1

        time.sleep(0.6)

    # -- 7. Exportar SKUs para Ground ------------------------------------------
    if skus_nuevos:
        import csv
        from datetime import datetime
        csv_path = Path(f"skus_publicados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["tn_id", "sku", "titulo", "mla_id", "image_source"])
            writer.writeheader()
            writer.writerows(skus_nuevos)
        print(f"\n  SKUs exportados para Ground -> {csv_path}")

    # -- 8. Resumen ------------------------------------------------------------
    print()
    print("=" * 65)
    print("  RESUMEN")
    print("=" * 65)
    print(f"  Publicados exitosamente : {creados}")
    print(f"  Errores                 : {errores}")
    print("=" * 65)
    print()


def __sku_disponible(item: dict, existing_skus: set[str]) -> bool:
    from mapper import extract_sku
    return extract_sku(item) not in existing_skus


if __name__ == "__main__":
    main()
