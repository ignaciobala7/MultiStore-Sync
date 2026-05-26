"""
Transformación de datos: Mercado Libre → Tiendanube.

Convierte el formato de ítem de ML al payload que espera
el endpoint POST /products de la API de Tiendanube.
"""

import re
import unicodedata

# IDs de atributos en ML que representan el SKU del vendedor
_ML_SKU_ATTRIBUTE_IDS = {"SELLER_SKU", "SKU", "SELLER_CUSTOM_FIELD"}


def slugify(text: str, max_length: int = 255) -> str:
    """
    Genera un slug URL-safe a partir de un texto.
    Ejemplo: "Remera Azul XL" → "remera-azul-xl"
    """
    # Normaliza acentos y caracteres especiales
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_length]


def extract_sku(item: dict) -> str:
    """
    Busca el SKU del vendedor dentro de los atributos del ítem de ML.
    Si no encuentra ninguno, usa el ID de la publicación como fallback.
    """
    for attr in item.get("attributes", []):
        if attr.get("id") in _ML_SKU_ATTRIBUTE_IDS:
            value = (attr.get("value_name") or "").strip()
            if value:
                return value
    # Fallback: el ID de ML garantiza unicidad
    return str(item.get("id", ""))


def extract_image_urls(item: dict) -> list[str]:
    """
    Extrae las URLs de las imágenes en máxima resolución.
    Reemplaza el sufijo de tamaño por '-O' (original) para obtener
    la versión de mayor calidad disponible en los servidores de ML.
    """
    urls = []
    for picture in item.get("pictures", []):
        url = picture.get("secure_url") or picture.get("url") or ""
        if url:
            # Reemplazar cualquier sufijo de tamaño (-V, -F, -S, -B) por -O (original)
            url = re.sub(r"-[VFSB](\.\w+)$", r"-O\1", url)
            urls.append(url)
    return urls


def ml_to_tiendanube(item: dict, description: str) -> tuple[dict, str]:
    """
    Convierte un ítem de Mercado Libre al payload de creación de Tiendanube.

    Retorna una tupla (payload, sku) donde:
      - payload: dict listo para POST /products
      - sku: string con el SKU del producto (para verificar duplicados)
    """
    title = (item.get("title") or "Sin título").strip()
    price = float(item.get("price") or 0)
    stock = int(item.get("available_quantity") or 0)
    sku = extract_sku(item)

    payload = {
        "name": {"es": title},
        "description": {"es": description or ""},
        # El handle es el slug en la URL de la tienda
        "handle": {"es": slugify(title)},
        "published": True,
        "requires_shipping": True,
        "variants": [
            {
                # Tiendanube acepta precio como string con punto decimal
                "price": f"{price:.2f}",
                "stock_management": True,
                "stock": stock,
                "sku": sku,
            }
        ],
    }

    return payload, sku
