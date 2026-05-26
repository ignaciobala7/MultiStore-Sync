"""
Cliente para la API de Tiendanube (Nuvemshop).

Maneja autenticación por token estático, paginación, creación de productos
e imágenes, y construcción del caché de SKUs existentes.
"""

import time
import requests

TN_BASE_URL = "https://api.tiendanube.com/v1"

# Máximo de productos por página soportado por Tiendanube
PAGE_SIZE = 200


class TiendanubeClient:
    def __init__(self, store_id: str, access_token: str, app_name: str, user_email: str):
        self.store_id = store_id
        self.base = f"{TN_BASE_URL}/{store_id}"
        # Tiendanube exige User-Agent con el formato "AppName (email)"
        self.headers = {
            "Authentication": f"bearer {access_token}",
            "User-Agent": f"{app_name} ({user_email})",
            "Content-Type": "application/json",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        resp = requests.get(
            f"{self.base}{path}",
            headers=self.headers,
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base}{path}",
            headers=self.headers,
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Métodos de negocio
    # ──────────────────────────────────────────────────────────────────────────

    def get_all_skus(self) -> set[str]:
        """
        Construye un set con todos los SKUs existentes en la tienda.
        Itera sobre todas las páginas de productos y sus variantes.
        Se usa como caché local para evitar duplicados sin llamar a la API por ítem.
        """
        skus: set[str] = set()
        page = 1

        while True:
            products = self._get(
                "/products",
                params={"page": page, "per_page": PAGE_SIZE, "fields": "id,variants"},
            )

            # La API devuelve lista vacía o [] cuando no hay más páginas
            if not products:
                break

            for product in products:
                for variant in product.get("variants", []):
                    sku = variant.get("sku") or ""
                    if sku.strip():
                        skus.add(sku.strip())

            # Si devolvió menos ítems que el tamaño de página, es la última
            if len(products) < PAGE_SIZE:
                break

            page += 1
            time.sleep(0.3)

        return skus

    def create_product(self, payload: dict) -> dict:
        """
        Crea un producto en Tiendanube y devuelve el objeto creado.
        El payload debe seguir el formato de POST /products de la API.
        """
        return self._post("/products", payload)

    def add_image(self, product_id: int | str, image_url: str) -> dict:
        """Asocia una URL de imagen a un producto (TN la descarga desde la URL)."""
        return self._post(f"/products/{product_id}/images", {"src": image_url})

    def add_image_bytes(self, product_id: int | str, image_bytes: bytes) -> dict:
        """Sube imagen como binario directo a TN — máxima calidad sin recompresión."""
        headers = {k: v for k, v in self.headers.items() if k != "Content-Type"}
        files = {"filename": ("image.jpg", image_bytes, "image/jpeg")}
        resp = requests.post(
            f"{self.base}/products/{product_id}/images",
            headers=headers,
            files=files,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()
