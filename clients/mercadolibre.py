"""
Cliente para la API de Mercado Libre.

Maneja autenticación OAuth2, renovación automática del access_token
y todas las consultas necesarias para el proceso de sincronización.
"""

import json
import time
import requests
from pathlib import Path

ML_BASE_URL = "https://api.mercadolibre.com"
TOKEN_FILE = Path("ml_tokens.json")

# Límite de ítems por página (máximo permitido por ML)
PAGE_LIMIT = 100


class MercadoLibreAuthError(Exception):
    pass


class MercadoLibreClient:
    def __init__(self, client_id: str, client_secret: str, access_token: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._user_id: str | None = None

    # ──────────────────────────────────────────────────────────────────────────
    # Auth
    # ──────────────────────────────────────────────────────────────────────────

    def _save_tokens(self) -> None:
        """Persiste los tokens renovados en disco para la próxima ejecución."""
        TOKEN_FILE.write_text(
            json.dumps(
                {"access_token": self.access_token, "refresh_token": self.refresh_token},
                indent=2,
            )
        )
        print("[ML] Tokens actualizados en ml_tokens.json")

    def _refresh_access_token(self) -> None:
        """Renueva el access_token usando el refresh_token actual."""
        print("[ML] Renovando access_token...")
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
        if resp.status_code != 200:
            raise MercadoLibreAuthError(
                f"No se pudo renovar el token: {resp.status_code} {resp.text}"
            )
        tokens = resp.json()
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens["refresh_token"]
        self._save_tokens()
        print("[ML] Token renovado exitosamente.")

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP
    # ──────────────────────────────────────────────────────────────────────────

    def _put(self, path: str, body: dict, _retry: bool = True) -> dict:
        """PUT autenticado con renovación automática del token en caso de 401."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        resp = requests.put(
            f"{ML_BASE_URL}{path}",
            headers=headers,
            json=body,
            timeout=20,
        )
        if resp.status_code == 401 and _retry:
            self._refresh_access_token()
            return self._put(path, body, _retry=False)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict | None = None, _retry: bool = True) -> dict:
        """
        GET autenticado con renovación automática del token en caso de 401.
        `_retry=False` evita bucles infinitos si el token renovado también falla.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        resp = requests.get(
            f"{ML_BASE_URL}{path}",
            headers=headers,
            params=params,
            timeout=20,
        )

        if resp.status_code == 401 and _retry:
            self._refresh_access_token()
            return self._get(path, params=params, _retry=False)

        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Métodos de negocio
    # ──────────────────────────────────────────────────────────────────────────

    def get_user_id(self) -> str:
        """Obtiene el user_id del propietario del token (se cachea en memoria)."""
        if not self._user_id:
            data = self._get("/users/me")
            self._user_id = str(data["id"])
        return self._user_id

    def get_active_item_ids(self) -> list[str]:
        """
        Devuelve la lista completa de IDs de publicaciones activas del usuario,
        manejando la paginación automáticamente.

        Nota: la API de ML limita el offset máximo a 1000 resultados por búsqueda.
        Si el usuario tiene más de 1000 publicaciones activas se retornan las
        primeras 1000 (comportamiento documentado por Mercado Libre).
        """
        user_id = self.get_user_id()
        all_ids: list[str] = []
        offset = 0
        ML_MAX_OFFSET = 1000  # Límite documentado de la API de ML

        while True:
            data = self._get(
                f"/users/{user_id}/items/search",
                params={"status": "active", "limit": PAGE_LIMIT, "offset": offset},
            )
            results: list[str] = data.get("results", [])
            all_ids.extend(results)

            total = data["paging"]["total"]
            offset += PAGE_LIMIT

            if not results or offset >= total or offset >= ML_MAX_OFFSET:
                break

            time.sleep(0.3)  # Pausa preventiva para no saturar la API

        return all_ids

    def get_item_details(self, item_id: str) -> dict:
        """Devuelve el detalle completo de una publicación."""
        return self._get(f"/items/{item_id}")

    def get_active_item_ids_page(self, offset: int, limit: int = 200) -> tuple[list[str], int]:
        """
        Devuelve una pagina de IDs activos y el total disponible.
        Retorna (ids, total).
        """
        user_id = self.get_user_id()
        data = self._get(
            f"/users/{user_id}/items/search",
            params={"status": "active", "limit": min(limit, PAGE_LIMIT * 2), "offset": offset},
        )
        return data.get("results", []), data["paging"]["total"]

    def search_items_by_sku(self, sku: str) -> list[dict]:
        """
        Busca publicaciones activas por SKU.
        Intenta seller_sku primero (items no-catálogo); si no encuentra,
        intenta seller_custom_field (items en modo catálogo).
        Retorna lista de items con detalle completo.
        """
        user_id = self.get_user_id()

        ids: list[str] = self._get(
            f"/users/{user_id}/items/search",
            params={"status": "active", "seller_sku": sku, "limit": 10},
        ).get("results", [])

        if not ids:
            try:
                # ?sku= es el parametro correcto para buscar por seller_custom_field (doc ML)
                ids = self._get(
                    f"/users/{user_id}/items/search",
                    params={"status": "active", "sku": sku, "limit": 10},
                ).get("results", [])
            except Exception:
                pass

        items = []
        for item_id in ids:
            try:
                items.append(self.get_item_details(item_id))
            except Exception:
                pass
        return items

    def update_item(self, item_id: str, payload: dict) -> dict:
        """Actualiza campos de una publicación existente via PUT /items/{item_id}."""
        return self._put(f"/items/{item_id}", payload)

    def get_item_description(self, item_id: str) -> str:
        """
        Devuelve la descripción en texto plano de una publicación.
        Retorna string vacío si no tiene descripción o si falla el endpoint.
        """
        try:
            data = self._get(f"/items/{item_id}/description")
            return data.get("plain_text", "").strip()
        except requests.HTTPError as e:
            # 404 es normal: publicaciones sin descripción
            if e.response is not None and e.response.status_code == 404:
                return ""
            raise
