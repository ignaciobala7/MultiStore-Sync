"""
Cliente para la API de PrestaShop (Infoandina).

Busca productos por SKU y devuelve las URLs públicas de sus imágenes
en alta resolución para usarlas al publicar en Tiendanube.
"""

import re
import requests


PS_BASE_URL = "https://shop.infoandina.com"


def obtener_cotizacion_dolar() -> float:
    """
    Tipo de cambio USD→ARS (dólar blue).
    Fuente primaria: header de Infoandina.
    Fallback: dolarapi.com.
    """
    # 1. Infoandina
    try:
        from bs4 import BeautifulSoup
        r = requests.get(PS_BASE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        header = soup.find("header") or soup.find(id="header") or soup.find(class_="header")
        text = header.get_text(" ", strip=True) if header else r.text[:2000]
        match = re.search(r"Cotizaci.n\s+d.lar[:\s]+\$?([\d,.]+)", text)
        if match:
            return float(match.group(1).replace(",", ""))
    except Exception:
        pass

    # 2. dolarapi.com (blue)
    try:
        r = requests.get("https://dolarapi.com/v1/dolares/blue", timeout=8)
        if r.status_code == 200:
            return float(r.json().get("venta", 1400))
    except Exception:
        pass

    return 1400.0  # último fallback
PS_API_URL = f"{PS_BASE_URL}/api"


class PrestaShopClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.auth = (api_key, "")

    def _build_image_url(self, image_id: int | str) -> str:
        """
        Construye la URL pública de una imagen de PrestaShop.
        PS almacena las imágenes en subdirectorios por cada dígito del ID.
        Ej: 38474 → /img/p/3/8/4/7/4/38474-large_default.jpg
        """
        img_id = str(image_id)
        path = "/".join(img_id) + f"/{img_id}-large_default.jpg"
        return f"{PS_BASE_URL}/img/p/{path}"

    def get_image_urls_by_sku(self, sku: str) -> list[str]:
        """
        Busca un producto por SKU y devuelve URLs públicas de sus imágenes.

        Usa display=full para obtener las asociaciones de imágenes directamente
        desde la respuesta del producto (associations.images.image), que es más
        confiable que el endpoint separado /api/images/products/{id}.

        Fallback: si no hay asociaciones, usa el campo id_default_image del producto.
        """
        try:
            resp = requests.get(
                f"{PS_API_URL}/products",
                auth=self.auth,
                params={
                    "output_format": "JSON",
                    "display": "full",
                    "filter[reference]": sku,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            products = resp.json().get("products", [])
            if not products:
                return []
            p = products[0]

            image_ids = self._extract_image_ids(p)
            return [self._build_image_url(img_id) for img_id in image_ids]
        except Exception:
            return []

    def _extract_image_ids(self, product: dict) -> list:
        """
        Extrae los IDs de imágenes de un producto PS obtenido con display=full.

        La API de PS puede devolver associations.images en dos formatos:
          - Lista directa:  associations.images = [{"id": "10"}, {"id": "11"}]
          - Dict anidado:   associations.images = {"image": [{"id": "10"}, ...]}

        Si no hay imágenes en associations, usa id_default_image como fallback.
        """
        images_raw = product.get("associations", {}).get("images", [])

        # Formato 1: lista directa → [{"id": "45476"}, {"id": "45477"}]
        if isinstance(images_raw, list):
            img_list = images_raw
        # Formato 2: dict anidado → {"image": [{"id": "45476"}, ...]}
        elif isinstance(images_raw, dict):
            img_list = images_raw.get("image", [])
            if isinstance(img_list, dict):
                img_list = [img_list]
        else:
            img_list = []

        image_ids = [str(img["id"]) for img in img_list if img.get("id")]

        # Fallback: imagen por defecto del producto
        if not image_ids:
            default_img = product.get("id_default_image")
            if default_img and str(default_img) != "0":
                image_ids = [str(default_img)]

        return image_ids

    def get_product_details_by_sku(self, sku: str) -> dict | None:
        """
        Busca un producto por SKU/referencia en PrestaShop y devuelve un dict con:
          id, reference, name, description, price, stock, image_urls

        Usa display=full para obtener todo en una sola llamada, incluyendo las URLs
        de imagen construidas desde las asociaciones del producto.
        Retorna None si no se encuentra o si falla la API.
        """
        try:
            resp = requests.get(
                f"{PS_API_URL}/products",
                auth=self.auth,
                params={
                    "output_format": "JSON",
                    "display": "full",
                    "filter[reference]": sku,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            products = resp.json().get("products", [])
            if not products:
                return None
            p = products[0]

            # Nombre (campo multilingüe: lista de {"id": "1", "value": "..."})
            name_raw = p.get("name", [])
            name = name_raw[0].get("value", "") if isinstance(name_raw, list) and name_raw else str(name_raw)
            if not name:
                name = p.get("reference", sku)

            # Descripción: puede ser string HTML o lista multilingüe
            desc_raw = p.get("description", "")
            if isinstance(desc_raw, list) and desc_raw:
                desc_html = desc_raw[0].get("value", "")
            elif isinstance(desc_raw, str):
                desc_html = desc_raw
            else:
                desc_html = ""
            # Convertir HTML a texto plano para ML
            description = re.sub(r"<[^>]+>", " ", desc_html).strip()
            description = re.sub(r"\s+", " ", description)

            # Precio
            try:
                price = float(p.get("price", 0))
            except (ValueError, TypeError):
                price = 0.0

            # Stock: el campo "quantity" del producto siempre es 0 en PS.
            # El stock real está en stock_availables, se consulta por id_product.
            stock = 0
            try:
                sa_resp = requests.get(
                    f"{PS_API_URL}/stock_availables",
                    auth=self.auth,
                    params={
                        "output_format": "JSON",
                        "filter[id_product]": p["id"],
                        "filter[id_product_attribute]": 0,
                        "display": "[quantity]",
                    },
                    timeout=10,
                )
                if sa_resp.status_code == 200:
                    sa_list = sa_resp.json().get("stock_availables", [])
                    if sa_list:
                        stock = int(sa_list[0].get("quantity", 0))
            except Exception:
                stock = 0

            # Imágenes: en try/except propio para que un error acá
            # no impida devolver el producto (fallback a lista vacía)
            try:
                image_ids = self._extract_image_ids(p)
                image_urls = [self._build_image_url(img_id) for img_id in image_ids]
            except Exception:
                image_urls = []

            return {
                "id": p.get("id"),
                "reference": p.get("reference", sku),
                "name": name,
                "description": description,
                "price": price,
                "stock": stock,
                "image_urls": image_urls,
            }
        except Exception:
            return None

    def get_images_by_sku(self, sku: str) -> list[bytes]:
        """
        Busca un producto por SKU y descarga sus imágenes en bytes directamente
        desde la API autenticada (máxima calidad, sin pasar por URLs públicas).
        Usado cuando se necesita re-hostear las imágenes (ej: subir a TN).
        """
        try:
            resp = requests.get(
                f"{PS_API_URL}/products",
                auth=self.auth,
                params={
                    "output_format": "JSON",
                    "display": "full",
                    "filter[reference]": sku,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            products = resp.json().get("products", [])
            if not products:
                return []

            p = products[0]
            product_id = p["id"]
            image_ids = self._extract_image_ids(p)

            images_bytes = []
            for img_id in image_ids:
                # Descarga autenticada via endpoint de la API (no URL pública)
                img_url = f"{PS_API_URL}/images/products/{product_id}/{img_id}"
                img_data = requests.get(img_url, auth=self.auth, timeout=30)
                if img_data.status_code == 200:
                    images_bytes.append(img_data.content)

            return images_bytes

        except Exception:
            return []
