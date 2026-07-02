"""
Cliente mejorado para ML con capacidad de PUBLICAR items (no solo leer).

Funcionalidades:
  - Buscar categorías por texto
  - Obtener atributos requeridos de una categoría
  - Calcular comisión por categoría
  - Crear publicaciones (POST /items)
  - Subir imágenes a ML
"""

import json
import os
import requests
import time
from pathlib import Path

ML_BASE_URL = "https://api.mercadolibre.com"
SITE_ID = "MLA"  # Argentina

# Archivo donde se guardan los tokens OAuth de ML (compartido con el cliente principal)
TOKEN_FILE = Path("ml_tokens.json")


class MercadoLibrePublisher:
    def __init__(self):
        """
        Inicializa con las mismas credenciales que usa el resto de la app:
        primero busca en ml_tokens.json, si no hay usa las variables de entorno.
        """
        self.access_token = os.environ.get("ML_ACCESS_TOKEN", "")
        self.refresh_token = os.environ.get("ML_REFRESH_TOKEN", "")
        self.client_id = os.environ.get("ML_CLIENT_ID", "")
        self.client_secret = os.environ.get("ML_CLIENT_SECRET", "")
        self.token_expires_at = 0
        # Sobreescribir con tokens frescos si existe el archivo
        self._load_tokens()

    def _load_tokens(self):
        """Carga tokens desde ml_tokens.json si existe (tokens más recientes)."""
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                self.access_token = data.get("access_token", self.access_token)
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self.token_expires_at = data.get("token_expires_at", 0)
            except Exception:
                pass

    def _save_config(self):
        """Persiste los tokens actualizados en ml_tokens.json."""
        TOKEN_FILE.write_text(json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expires_at": self.token_expires_at,
        }, indent=2))

    def _ensure_token_valid(self):
        """Renueva el token si está vencido (chequea 5 min antes para evitar expiración en mitad de una llamada)"""
        if time.time() >= self.token_expires_at - 300:
            self._refresh_token()

    def _refresh_token(self):
        """Renueva el access_token usando refresh_token"""
        try:
            resp = requests.post(
                f"{ML_BASE_URL}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                self.token_expires_at = time.time() + data.get("expires_in", 21600)
                self._save_config()
        except Exception:
            pass

    def _get(self, path: str, params: dict | None = None, _retry: bool = True) -> dict | list:
        """GET con token. Si recibe 401, refresca el token y reintenta una vez."""
        self._ensure_token_valid()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        resp = requests.get(
            f"{ML_BASE_URL}{path}",
            headers=headers,
            params=params,
            timeout=20,
        )
        if resp.status_code == 401 and _retry:
            self._refresh_token()
            return self._get(path, params=params, _retry=False)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict, _retry: bool = True) -> dict:
        """POST con token. Si recibe 401, refresca el token y reintenta una vez."""
        self._ensure_token_valid()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{ML_BASE_URL}{path}",
            headers=headers,
            json=payload,
            timeout=20,
        )
        if resp.status_code == 401 and _retry:
            self._refresh_token()
            return self._post(path, payload, _retry=False)
        if not resp.ok:
            try:
                err = resp.json()
                causes = [c.get("message") for c in err.get("cause", []) if c.get("message")]
                base = err.get("error") or err.get("message", "")
                msg = f"{base} — {'; '.join(causes)}" if causes else base or str(err)
            except Exception:
                msg = resp.text
            raise Exception(f"ML {resp.status_code}: {msg}")
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Búsqueda y meta-datos
    # ──────────────────────────────────────────────────────────────────────────

    def buscar_categorias(self, query: str, limit: int = 8) -> list[dict]:
        """
        Busca categorías en ML por texto.
        Ej: buscar_categorias("teclado") → [{"category_id": "MLA9916", "category_name": "Teclados", ...}]
        """
        # El parámetro `limit` no es soportado por el endpoint; se aplica el corte en Python
        data = self._get(
            f"/sites/{SITE_ID}/domain_discovery/search",
            params={"q": query},
        )
        results = data if isinstance(data, list) else []

        return [
            {"category_id": r.get("category_id"), "category_name": r.get("category_name")}
            for r in results
        ][:limit]

    def obtener_atributos(self, category_id: str) -> list[dict]:
        """
        Obtiene atributos requeridos de una categoría.
        Devuelve: [{"id": "BRAND", "name": "Marca", "tags": {"required": true, ...}}, ...]
        """
        try:
            data = self._get(f"/categories/{category_id}/attributes")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def obtener_comision(self, price: float, category_id: str, listing_type_id: str = "gold_special", catalog_listing: bool = False) -> tuple[float, float]:
        """
        Calcula comisión por categoría.
        Retorna: (porcentaje_comision, monto_comision)
        """
        try:
            params = {
                "price": price,
                "category_id": category_id,
                "listing_type_id": listing_type_id,
            }
            if catalog_listing:
                params["catalog_listing"] = "true"
            data = self._get(
                f"/sites/{SITE_ID}/listing_prices",
                params=params,
            )
            pct = data.get("sale_fee_details", {}).get("percentage_fee", 0)
            monto = data.get("sale_fee_amount", 0)
            return pct, monto
        except Exception:
            return 0.0, 0.0

    def obtener_categoria_de_catalogo(self, catalog_product_id: str, product_name: str = "") -> str | None:
        """
        Devuelve el category_id real de un catalog_product_id.
        Intento 1: /sites/MLA/search?catalog_product_id=... (requiere scope de búsqueda).
        Intento 2 (fallback): domain_discovery con el nombre del producto.
        Retorna None si ambos fallan.
        """
        try:
            data = self._get(
                f"/sites/{SITE_ID}/search",
                params={"catalog_product_id": catalog_product_id, "limit": 1},
            )
            results = data.get("results", [])
            if results and results[0].get("category_id"):
                return results[0]["category_id"]
        except Exception:
            pass

        if product_name:
            cats = self.buscar_categorias(product_name, limit=1)
            if cats:
                return cats[0].get("category_id")

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Creación de publicaciones
    # ──────────────────────────────────────────────────────────────────────────

    def buscar_en_catalogo(self, query: str, limit: int = 20) -> list[dict]:
        """
        Busca productos en el catálogo de ML.
        Retorna lista de {id, name, status} para que el usuario elija el correcto.
        No filtra por status: ML puede devolver productos "under_review" que
        igual son válidos para publicar en modo catálogo, y filtrarlos de más
        ocultaba matches que sí aparecen al publicar manualmente desde ML.
        """
        try:
            data = self._get(
                "/products/search",
                params={"site_id": SITE_ID, "q": query, "limit": limit},
            )
            return [
                {"id": r.get("id"), "name": r.get("name"), "status": r.get("status")}
                for r in data.get("results", [])
            ]
        except Exception:
            return []

    def get_catalog_product(self, catalog_product_id: str) -> dict | None:
        try:
            return self._get(f"/products/{catalog_product_id}")
        except Exception:
            return None

    def crear_item(
        self,
        title: str,
        category_id: str,
        price: float,
        stock: int,
        description: str = "",
        images: list[str] | None = None,
        attributes: list[dict] | None = None,
        condition: str = "new",
        listing_type_id: str = "gold_special",
        family_name: str = "",
        catalog_product_id: str = "",
        seller_custom_field: str = "",
        gtin: str = "",
        value_added_tax: str = "",
        import_duty: str = "",
    ) -> dict | None:
        """
        Crea una publicación en Mercado Libre.
        Si se provee catalog_product_id, usa modo catálogo (ML pone el título automáticamente).
        Retorna el ítem creado o None si falla.
        """
        if images is None:
            images = []
        if attributes is None:
            attributes = []

        payload = {
            "category_id": category_id,
            "price": int(price),
            "currency_id": "ARS",
            "available_quantity": int(stock),
            "buying_mode": "buy_it_now",
            "listing_type_id": listing_type_id,
            "condition": condition,
        }

        if catalog_product_id:
            payload["catalog_product_id"] = catalog_product_id
            payload["catalog_listing"] = True
            payload["family_name"] = family_name
        else:
            payload["title"] = title
            if family_name:
                payload["family_name"] = family_name

        if description:
            payload["description"] = {"plain_text": description}

        if images:
            payload["pictures"] = [{"source": url} for url in images if url]

        if gtin:
            attributes = list(attributes) + [{"id": "GTIN", "value_name": gtin}]
        if value_added_tax:
            attributes = list(attributes) + [{"id": "VALUE_ADDED_TAX", "value_name": value_added_tax}]
        if import_duty:
            attributes = list(attributes) + [{"id": "IMPORT_DUTY", "value_name": import_duty}]

        if attributes:
            if catalog_product_id:
                # En modo catálogo solo mandamos atributos fiscales y GTIN
                allowed = {"GTIN", "VALUE_ADDED_TAX", "IMPORT_DUTY"}
                fiscal_attrs = [a for a in attributes if a.get("id") in allowed]
                if fiscal_attrs:
                    payload["attributes"] = fiscal_attrs
            else:
                payload["attributes"] = attributes

        if seller_custom_field:
            payload["seller_custom_field"] = seller_custom_field
            # seller_sku es rechazado en modo catálogo (incluso auto-catálogo por GTIN)
            # seller_custom_field es suficiente para rastrear el SKU

        payload["sale_terms"] = [
            {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
            {"id": "WARRANTY_TIME", "value_name": "6 meses"},
            {"id": "INVOICE", "value_name": "Factura A"},
        ]

        payload["shipping"] = {
            "mode": "me2",
            "local_pick_up": True,
            "free_shipping": price >= 32000,
        }

        try:
            result = self._post("/items", payload)
        except Exception as e:
            err_msg = str(e)
            # ML puede auto-forzar catálogo si el GTIN matchea un producto del catálogo.
            # En ese caso rechaza title y seller_sku — reintentamos sin esos campos.
            if "title" in err_msg and "invalid" in err_msg.lower():
                payload.pop("title", None)
                payload.pop("seller_sku", None)
                result = self._post("/items", payload)
            else:
                raise
        return result

    def subir_imagen(self, item_id: str, image_url: str) -> dict | None:
        """
        Sube una imagen a una publicación existente.
        """
        payload = {"source": image_url}
        try:
            result = self._post(f"/items/{item_id}/images", payload)
            return result
        except Exception as e:
            print(f"Error subiendo imagen a ML: {e}")
            return None


# ──────────────────────────────────────────────────────────────────────────────
# Sugerencia de categorías y matching inteligente
# ──────────────────────────────────────────────────────────────────────────────

# Categorías más populares en ML Argentina
# NOTA: Los IDs se obtienen dinámicamente en la función get_popular_categories()
# Los valores hardcoded aquí son fallback solamente
POPULAR_CATEGORIES = {
    "Smartphones": "MLA1051",
    "Notebooks": "MLA1649",
    "Tablets": "MLA1055",
    "Cámaras Digitales": "MLA1040",
    "Auriculares": "MLA1093",
    "Teclados": "MLA1051",  # Fallback - será reemplazado por búsqueda dinámica
    "Mouse": "MLA1051",     # Fallback
    "Monitores": "MLA1051", # Fallback
    "Impresoras": "MLA1083",
    "Routers": "MLA1096",
    "Webcams": "MLA1073",
    "Parlantes": "MLA1090",
    "Cables y Conectores": "MLA1051", # Fallback
    "Memorias USB": "MLA1051", # Fallback
    "Baterías": "MLA1051", # Fallback
}

KEYWORDS_MAPPING = {
    # Palabras clave → términos de búsqueda sugeridos
    "teclado": ["teclado", "keyboard"],
    "mouse": ["mouse", "ratón"],
    "auricular": ["auriculares", "headphones"],
    "cámara": ["cámara", "camara"],
    "pantalla": ["monitor", "pantalla"],
    "notebook": ["notebook", "laptop"],
    "smartphone": ["smartphone", "celular"],
    "tablet": ["tablet", "ipad"],
    "impresora": ["impresora", "printer"],
    "router": ["router", "modem"],
    "webcam": ["webcam", "camara web"],
    "parlante": ["parlante", "speaker"],
    "cable": ["cables", "conectores"],
    "batería": ["batería", "bateria"],
    "memoria": ["memoria", "usb"],
}


def extraer_keywords_producto(nombre_producto: str) -> list[str]:
    """
    Extrae palabras clave del nombre del producto para sugerir búsquedas.

    Ej: "Teclado + Mouse Logitech Pop Blanco"
    → ["teclado", "mouse", "logitech"]
    """
    palabras = nombre_producto.lower().split()
    stopwords = {"y", "+", "de", "con", "para", "blanco", "negro", "gris", "rojo", "azul", "pop", "kit"}

    keywords = []
    for palabra in palabras:
        palabra_limpia = palabra.strip("+-()[]{}.,")
        if len(palabra_limpia) > 2 and palabra_limpia not in stopwords:
            keywords.append(palabra_limpia)

    return keywords[:5]


def sugerir_terminos_busqueda(nombre_producto: str) -> list[str]:
    """
    Sugiere términos de búsqueda basados en el nombre del producto.

    Ej: "Teclado + Mouse Logitech"
    → ["teclado", "mouse", "teclado y mouse", "periféricos"]
    """
    keywords = extraer_keywords_producto(nombre_producto)
    sugerencias = set()

    for kw in keywords:
        if kw in KEYWORDS_MAPPING:
            sugerencias.update(KEYWORDS_MAPPING[kw])
        else:
            sugerencias.add(kw)

    if "teclado" in keywords and "mouse" in keywords:
        sugerencias.add("teclado y mouse")

    return sorted(list(sugerencias))[:5]


def get_popular_categories_with_correct_ids(publisher) -> dict:
    """
    Obtiene los IDs correctos de las categorías populares buscándolas en ML.
    Cachea los resultados para evitar búsquedas repetidas.
    Retorna un dict actualizado con los IDs correctos.
    """
    categorias_a_buscar = [
        "Smartphones", "Notebooks", "Tablets", "Cámaras Digitales",
        "Auriculares", "Teclados", "Mouse", "Monitores", "Impresoras",
        "Routers", "Webcams", "Parlantes", "Cables y Conectores",
        "Memorias USB", "Baterías"
    ]

    resultado = {}
    for cat_nombre in categorias_a_buscar:
        try:
            # Buscar la categoría en ML
            busqueda = publisher.buscar_categorias(cat_nombre, limit=1)
            if busqueda and len(busqueda) > 0:
                cat_id = busqueda[0].get("category_id")
                resultado[cat_nombre] = cat_id
            else:
                # Fallback a POPULAR_CATEGORIES si no encuentra
                resultado[cat_nombre] = POPULAR_CATEGORIES.get(cat_nombre, "")
        except Exception:
            # Si hay error, usa el valor por defecto
            resultado[cat_nombre] = POPULAR_CATEGORIES.get(cat_nombre, "")

    return resultado


def auto_match_categoria(nombre_producto: str, publisher) -> dict | None:
    """
    Intenta hacer match automático de categoría buscando términos clave.

    Retorna la categoría encontrada o None si no hay coincidencia clara.
    """
    sugerencias = sugerir_terminos_busqueda(nombre_producto)

    for termino in sugerencias:
        resultados = publisher.buscar_categorias(termino, limit=3)
        if resultados:
            return resultados[0]

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Mapeo inteligente PS attributes → ML attributes
# ──────────────────────────────────────────────────────────────────────────────

ATTR_MAPPING = {
    # PS field → ML attribute ID
    "brand": "BRAND",
    "marca": "BRAND",
    "model": "MODEL",
    "modelo": "MODEL",
    "color": "COLOR",
    "size": "SIZE",
    "talla": "SIZE",
    "material": "MATERIAL",
}


def mapear_atributos_ps_ml(ps_attributes: dict, ml_required_attrs: list[dict]) -> list[dict]:
    """
    Mapea atributos de PS a formato de ML.

    Input:
      ps_attributes: dict con datos de PS (ej: {"marca": "Samsung", "modelo": "Galaxy"})
      ml_required_attrs: list de atributos requeridos en ML

    Output:
      list de atributos en formato ML: [{"id": "BRAND", "value_name": "Samsung"}, ...]
    """
    resultado = []
    ps_keys_lower = {k.lower(): v for k, v in (ps_attributes or {}).items()}

    for ml_attr in ml_required_attrs:
        attr_id = ml_attr.get("id", "").upper()
        attr_name = ml_attr.get("name", "").lower()

        # Buscar en mapeo inteligente
        valor = None
        for ps_key, ml_id in ATTR_MAPPING.items():
            if ml_id == attr_id and ps_key in ps_keys_lower:
                valor = ps_keys_lower[ps_key]
                break

        # Si no encontró, buscar por nombre similar
        if not valor:
            for ps_key, ps_val in ps_keys_lower.items():
                if attr_name in ps_key or ps_key in attr_name:
                    valor = ps_val
                    break

        if valor:
            resultado.append({"id": attr_id, "value_name": str(valor)})

    return resultado
