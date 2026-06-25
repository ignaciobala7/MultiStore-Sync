"""
clients/sku_tracker.py — Rastreador de SKU → MLA publicados

Backend actual: CSV (sku_mla_map.csv en la raíz del proyecto).
Para cambiar de backend, subclasear y reemplazar solo _read_all y _write_all.
"""

import csv
from datetime import datetime
from pathlib import Path

CSV_PATH = Path("sku_mla_map.csv")
FIELDNAMES = [
    "sku",
    "mla_id",
    "catalog_product_id",
    "titulo",
    "precio_ars",
    "fecha_publicacion",
    "estado",
    "ultima_verificacion",
]


class SKUTracker:
    def __init__(self, csv_path: Path = CSV_PATH):
        self._path = Path(csv_path)
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self._path.exists():
            with self._path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

    def _read_all(self) -> list[dict]:
        with self._path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _write_all(self, rows: list[dict]) -> None:
        with self._path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(rows)

    def get(self, sku: str) -> dict | None:
        """Devuelve el registro del SKU o None si no existe."""
        sku_norm = sku.strip().upper()
        for row in self._read_all():
            if row.get("sku", "").strip().upper() == sku_norm:
                return row
        return None

    def save(
        self,
        sku: str,
        mla_id: str,
        catalog_product_id: str,
        titulo: str,
        precio_ars: float,
    ) -> dict:
        """Guarda o actualiza un registro. Estado inicial: 'active'."""
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sku_norm = sku.strip().upper()
        record = {
            "sku": sku_norm,
            "mla_id": mla_id,
            "catalog_product_id": catalog_product_id or "",
            "titulo": titulo,
            "precio_ars": str(precio_ars),
            "fecha_publicacion": ahora,
            "estado": "active",
            "ultima_verificacion": ahora,
        }
        rows = self._read_all()
        updated = False
        for i, row in enumerate(rows):
            if row.get("sku", "").strip().upper() == sku_norm:
                rows[i] = record
                updated = True
                break
        if not updated:
            rows.append(record)
        self._write_all(rows)
        return record

    def verify_with_ml(self, sku: str, ml_client) -> dict | None:
        """
        Consulta ML via GET /users/{user_id}/items/search?seller_sku={sku}
        y actualiza estado y ultima_verificacion en el CSV.
        Retorna el registro actualizado, o None si el SKU no estaba trackeado.
        """
        record = self.get(sku)
        if record is None:
            return None

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nuevo_estado = record["estado"]

        try:
            items = ml_client.search_items_by_sku(sku)
            if items:
                item = items[0]
                nuevo_estado = item.get("status", record["estado"])
                mla_id_remoto = item.get("id", record["mla_id"])
                record["mla_id"] = mla_id_remoto
            else:
                nuevo_estado = "not_found"
        except Exception as e:
            print(f"[SKUTracker] verify_with_ml error para SKU {sku}: {e}")

        record["estado"] = nuevo_estado
        record["ultima_verificacion"] = ahora

        rows = self._read_all()
        sku_norm = sku.strip().upper()
        for i, row in enumerate(rows):
            if row.get("sku", "").strip().upper() == sku_norm:
                rows[i] = record
                break
        self._write_all(rows)
        return record

    def get_all(self) -> list[dict]:
        """Devuelve todos los registros como lista de dicts."""
        return self._read_all()
