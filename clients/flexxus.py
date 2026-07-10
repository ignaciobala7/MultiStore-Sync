"""
clients/flexxus.py — Lee precios de artículos desde el Excel exportado de Flexxus.

Por qué usamos el Excel y no la API directa:
  - El Excel se actualiza con un clic desde Flexxus (sin tocar el servidor)
  - Cero riesgo de saturar la API
  - Los precios son idénticos a los de la API (verificado)

Cómo se calcula el precio final:
  precio_pesos = PRECIOVENTA5 × (1 + COEFICIENTE) × tipo_de_cambio

  Donde:
    PRECIOVENTA5  = precio de venta Lista 5, en dólares, sin IVA — YA INCLUYE
                    margen (MARGEN5 en el Excel, ej: 109%), no es el costo real.
    COEFICIENTE   = alícuota de IVA (1.0 = 21%, 0.5 = 10.5%, 0.0 = exento)
    tipo_de_cambio = cotización del dólar, leída desde la hoja MONEDAS del Excel

  El costo real de reposición es PRECIOCOMPRA (misma unidad que PRECIOVENTA5:
  USD sin IVA). get_precio() devuelve ambos — no usar "pesos"/"usd" (precio de
  VENTA) donde se necesite el costo real; para eso usar "costo_pesos"/"costo_usd".

Cómo usar este módulo:
  from clients.flexxus import FlexxusClient
  flexxus = FlexxusClient()
  precio = flexxus.get_precio("A19-210-1011")
  print(precio)
  # → {"usd": 44.27, "iva": 0.21, "tipo_cambio": 1460, "pesos": 64640.65,
  #    "costo_usd": 21.15, "costo_pesos": 30875.32}
"""

import re
from pathlib import Path

import pandas as pd

# Ruta al Excel exportado desde Flexxus.
# El archivo debe estar en la raíz del proyecto con este nombre exacto.
EXCEL_PATH = Path("articulos_flexxus.xlsx")


class FlexxusClient:
    def __init__(self, excel_path: Path = EXCEL_PATH):
        """
        Carga el Excel al inicializar. Esto se hace una sola vez cuando
        arranca la app — no en cada búsqueda. Así es rápido.
        """
        self.excel_path = excel_path
        self._articulos: pd.DataFrame | None = None
        self._tipo_cambio: float | None = None
        self._cargar_excel()

    def _cargar_excel(self):
        """
        Lee las dos hojas que necesitamos del Excel:
          - ARTICULOS: todos los productos con sus precios
          - MONEDAS: el tipo de cambio del dólar
        """
        if not self.excel_path.exists():
            raise FileNotFoundError(
                f"No se encontró el archivo '{self.excel_path}'. "
                "Asegurate de que esté en la raíz del proyecto."
            )

        # Leer artículos — forzamos CODIGOPARTICULAR como string
        # para que códigos como "A19-210-1011" no se conviertan en números
        self._articulos = pd.read_excel(
            self.excel_path,
            sheet_name="ARTICULOS",
            dtype={"CODIGOPARTICULAR": str},
        )

        # Leer tipo de cambio desde la hoja MONEDAS
        monedas = pd.read_excel(self.excel_path, sheet_name="MONEDAS")
        fila_dolar = monedas[monedas["CODIGOMONEDA"] == "DOLARES"]
        if fila_dolar.empty:
            raise ValueError("No se encontró la fila DOLARES en la hoja MONEDAS del Excel.")
        self._tipo_cambio = float(fila_dolar.iloc[0]["CAMBIO"])

    def get_precio(self, sku: str) -> dict | None:
        """
        Busca un artículo por su CODIGOPARTICULAR y devuelve su precio.

        Retorna un dict con:
          {
            "usd":         precio de VENTA sin IVA en dólares (ej: 44.27) — Lista 5,
                           ya incluye margen, NO es el costo real.
            "iva":         alícuota de IVA              (ej: 0.21)
            "tipo_cambio": cotización del dólar         (ej: 1460)
            "pesos":       precio de VENTA final en pesos con IVA (ej: 64640.65)
            "costo_usd":   costo real de reposición en USD, sin IVA (PRECIOCOMPRA,
                           sin margen) — o None si el Excel no trae ese dato.
            "costo_pesos": costo real en pesos, con el mismo IVA/TC que "pesos" —
                           usar ESTE valor (no "pesos") para comparar contra
                           precios de mercado y calcular margen real.
            "margen5_pct": margen % ya configurado en Flexxus para la Lista 5
                           (columna MARGEN5, ej: 109.07) — es un dato de
                           REFERENCIA, no el margen real calculado contra el
                           mercado. Viene del Excel exportado a mano, así que
                           puede estar desactualizado respecto a Flexxus en
                           vivo. PORTABILIDAD: en el dashboard JS este valor
                           debería traerse directo de la API de Flexxus V5
                           (más confiable que el Excel), no replicar esta
                           lectura de planilla.
          }

        Retorna None si el SKU no existe en el Excel.
        """
        if self._articulos is None:
            return None

        # Buscar la fila del producto
        fila = self._articulos[self._articulos["CODIGOPARTICULAR"] == sku.strip().upper()]

        if fila.empty:
            return None

        fila = fila.iloc[0]  # tomar la primera coincidencia

        precio_usd = float(fila["PRECIOVENTA5"])
        coeficiente = float(fila["COEFICIENTE"])

        # Convertir coeficiente a alícuota de IVA:
        # 1.0 → 21%,  0.5 → 10.5%,  0.0 → 0%
        iva = coeficiente * 0.21

        # Precio final = USD × (1 + IVA) × tipo de cambio
        precio_pesos = precio_usd * (1 + iva) * self._tipo_cambio

        # Costo real de reposición (PRECIOCOMPRA) — misma unidad que PRECIOVENTA5
        # (USD sin IVA), pero SIN el margen de venta (MARGEN5) aplicado.
        costo_usd = fila.get("PRECIOCOMPRA")
        tiene_costo = costo_usd is not None and not pd.isna(costo_usd)
        costo_usd = float(costo_usd) if tiene_costo else None
        costo_pesos = costo_usd * (1 + iva) * self._tipo_cambio if tiene_costo else None

        # Margen ya configurado en Flexxus (MARGEN5) — dato de referencia, tal
        # cual está cargado en el Excel. Ver nota de portabilidad en el
        # docstring de arriba: en JS/dashboard, traer esto en vivo de la API
        # de Flexxus V5 en vez de leerlo de un Excel exportado a mano.
        margen5 = fila.get("MARGEN5")
        tiene_margen5 = margen5 is not None and not pd.isna(margen5)
        margen5_pct = round(float(margen5), 2) if tiene_margen5 else None

        return {
            "usd": round(precio_usd, 2),
            "iva": round(iva, 4),
            "tipo_cambio": self._tipo_cambio,
            "pesos": round(precio_pesos, 2),
            "costo_usd": round(costo_usd, 2) if tiene_costo else None,
            "costo_pesos": round(costo_pesos, 2) if tiene_costo else None,
            "margen5_pct": margen5_pct,
        }

    def get_value_added_tax(self, sku: str) -> str | None:
        """
        Devuelve la alícuota de IVA como string para ML (ej: "21%", "10.5%", "Exento").
        Convierte COEFICIENTE: 1.0 → "21%", 0.5 → "10.5%", 0.0 → "Exento".
        """
        if self._articulos is None:
            return None
        fila = self._articulos[self._articulos["CODIGOPARTICULAR"] == sku.strip().upper()]
        if fila.empty:
            return None
        coef = float(fila.iloc[0]["COEFICIENTE"])
        if coef == 0.0:
            return "Exento"
        pct = coef * 21
        return f"{pct:g}%"

    def get_ean(self, sku: str) -> str | None:
        """
        Devuelve el código de barras (EAN/GTIN) del artículo, o None si está vacío.
        El valor puede venir como float del Excel (ej: 192545215831.0) — se convierte a
        string limpio. Algunos códigos vienen con una letra de prefijo antes del EAN
        numérico real (ej: "L6939554923562") — se descarta esa letra y se devuelve
        solo la parte numérica, ya que ML/GTIN requiere solo dígitos.
        """
        if self._articulos is None:
            return None
        fila = self._articulos[self._articulos["CODIGOPARTICULAR"] == sku.strip().upper()]
        if fila.empty:
            return None
        val = fila.iloc[0]["CODIGOBARRA"]
        if pd.isna(val) or str(val).strip() in ("", "nan"):
            return None
        val_str = str(val).strip()
        if val_str.replace(".", "").isdigit():
            return str(int(float(val_str)))
        match = re.match(r"^[A-Za-z]+(\d+)$", val_str)
        if match:
            return match.group(1)
        return val_str

    def get_ean_raw(self, sku: str) -> str | None:
        """
        Devuelve CODIGOBARRA tal cual está en el Excel, sin quitar el prefijo de letra
        que get_ean() sí descarta. Sirve como último recurso manual cuando el valor
        limpio no es un GTIN válido para ML — se manda el dato crudo tal cual está
        cargado en Flexxus, letras incluidas, y que ML decida si lo acepta.
        """
        if self._articulos is None:
            return None
        fila = self._articulos[self._articulos["CODIGOPARTICULAR"] == sku.strip().upper()]
        if fila.empty:
            return None
        val = fila.iloc[0]["CODIGOBARRA"]
        if pd.isna(val) or str(val).strip() in ("", "nan"):
            return None
        val_str = str(val).strip()
        if val_str.replace(".", "").isdigit():
            return str(int(float(val_str)))
        return val_str

    def recargar(self):
        """
        Recarga el Excel desde disco. Útil si el operario actualizó
        los precios en Flexxus y quiere que la app use los nuevos sin reiniciarla.
        """
        self._cargar_excel()
