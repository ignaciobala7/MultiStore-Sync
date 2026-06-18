"""
_previa_publicacion.py — Preview del flujo PS → ML para SKU A23-001-0009

NO publica nada. Muestra todos los datos que se van a enviar a ML
para que el usuario confirme antes de ejecutar _publicar.py.
"""

import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

# Asegurarse de estar en el directorio del proyecto
os.chdir(Path(__file__).parent)
load_dotenv()

from clients.prestashop import PrestaShopClient
from clients.flexxus import FlexxusClient
from clients.mercadolibre_publish import MercadoLibrePublisher

SKU = "A23-001-0009"
CATEGORY_ID = "MLA7524"           # Calculadoras
CATALOG_PRODUCT_ID = "MLA28090996"  # Calculadora Casio Mx-8b
LISTING_TYPE = "gold_special"

# ── 1. Producto de PS ─────────────────────────────────────────────────────────
print("=" * 60)
print(f"PREVIEW DE PUBLICACIÓN — SKU: {SKU}")
print("=" * 60)

print("\n[1/5] Consultando PrestaShop...")
ps = PrestaShopClient(os.environ["PS_API_KEY"])
p = ps.get_product_details_by_sku(SKU)
if not p:
    print(f"❌ SKU {SKU} no encontrado en PrestaShop (activos). Abortando.")
    sys.exit(1)

print(f"  ✓ Nombre  : {p['name']}")
print(f"  ✓ SKU PS  : {p['reference']}")
print(f"  ✓ Precio PS: USD {p['price']:.2f}")
print(f"  ✓ Stock PS : {p['stock']}")
print(f"  ✓ Imágenes : {len(p['image_urls'])} → {p['image_urls']}")

# ── 2. Precio desde Flexxus ───────────────────────────────────────────────────
print("\n[2/5] Consultando Flexxus...")
try:
    flexxus = FlexxusClient()
    fx = flexxus.get_precio(SKU)
except Exception as e:
    print(f"  ⚠️ Flexxus no disponible: {e}")
    fx = None

if fx:
    precio_usd_con_iva = fx["usd"] * (1 + fx["iva"])
    precio_ars_base = precio_usd_con_iva * fx["tipo_cambio"]
    print(f"  ✓ USD (s/IVA)     : USD {fx['usd']:.2f}")
    print(f"  ✓ IVA             : {fx['iva']*100:.0f}%")
    print(f"  ✓ USD (c/IVA)     : USD {precio_usd_con_iva:.2f}")
    print(f"  ✓ Tipo de cambio  : {fx['tipo_cambio']}")
    print(f"  ✓ Precio base ARS : ${precio_ars_base:,.0f}")
else:
    from clients.prestashop import obtener_cotizacion_dolar
    tc = obtener_cotizacion_dolar()
    precio_ars_base = p["price"] * tc
    print(f"  ⚠️ Fallback PS: USD {p['price']:.2f} × TC {tc} = ${precio_ars_base:,.0f}")

# ── 3. Comisión ML ────────────────────────────────────────────────────────────
print("\n[3/5] Calculando comisión en ML...")
publisher = MercadoLibrePublisher()
pct_com, monto_com = publisher.obtener_comision(
    precio_ars_base, CATEGORY_ID, LISTING_TYPE, catalog_listing=True
)
precio_final = precio_ars_base + monto_com
print(f"  ✓ Comisión ML     : {pct_com}% → ${monto_com:,.0f} ARS")
print(f"  ✓ Precio FINAL ML : ${precio_final:,.0f} ARS")

# ── 4. Atributos de la descripción de PS ─────────────────────────────────────
print("\n[4/5] Extrayendo atributos desde descripción de PS...")
description = p.get("description", "")
print(f"  Descripción raw: {description[:200]!r}...")

# Parser: extrae pares "clave: valor" (clave = una sola palabra en español)
attr_pattern = re.compile(r'\b([a-záéíóúüñA-ZÁÉÍÓÚÜÑA-Z][a-záéíóúüñA-ZÁÉÍÓÚÜÑa-z]*):\s*([^:\n]+?)(?=\s+[a-záéíóúüñA-ZÁÉÍÓÚÜÑA-Z][a-záéíóúüñA-ZÁÉÍÓÚÜÑa-z]*:|$)', re.DOTALL)
desc_attrs = {}
for m in attr_pattern.finditer(description):
    key = m.group(1).strip()
    val = m.group(2).strip().rstrip(".,;")
    if key and val and len(key) < 30 and len(val) < 100:
        desc_attrs[key.lower()] = val

print(f"  Atributos encontrados en descripción: {desc_attrs}")

# Obtener atributos requeridos de la categoría
ml_attrs_raw = publisher.obtener_atributos(CATEGORY_ID)
required_attrs = [a for a in ml_attrs_raw if a.get("tags", {}).get("required", False)]
print(f"\n  Atributos requeridos por ML para {CATEGORY_ID}:")
for a in required_attrs:
    print(f"    - {a['id']} / {a['name']}")

# Mapear descripción PS → atributos ML
ATTR_MAP = {
    "BRAND": ["marca", "brand"],
    "MODEL": ["modelo", "model"],
    "CALCULATOR_TYPE": ["tipo", "tipo de calculadora", "calculator_type"],
}

attrs_ml = []
for attr in required_attrs:
    attr_id = attr["id"]
    keys_to_try = ATTR_MAP.get(attr_id, [attr["name"].lower()])
    valor = None
    for k in keys_to_try:
        if k in desc_attrs:
            valor = desc_attrs[k]
            break
    if valor:
        attrs_ml.append({"id": attr_id, "value_name": valor})
        print(f"  ✓ {attr_id} = {valor!r}")
    else:
        print(f"  ⚠️ {attr_id} no encontrado en descripción")

# ── 5. Resumen final del payload ──────────────────────────────────────────────
print("\n[5/5] RESUMEN DEL PAYLOAD A ENVIAR A ML:")
print("-" * 50)
print(f"  title              : {p['name']}")
print(f"  category_id        : {CATEGORY_ID}")
print(f"  catalog_product_id : {CATALOG_PRODUCT_ID}")
print(f"  price              : ${precio_final:,.0f} ARS")
print(f"  stock              : {max(p['stock'], 1)}")
print(f"  condition          : new")
print(f"  listing_type_id    : {LISTING_TYPE}")
print(f"  seller_custom_field: {p['reference']}")
print(f"  attributes         : {attrs_ml}")
print(f"  images             : {p['image_urls']} (se procesan y suben a ImgBB al publicar)")
print(f"  description        : {p['description'][:120]!r}...")
print("-" * 50)
print("\n✅ Preview completo. Si todo está OK, ejecutá: python _publicar.py")
print("   (La imagen se sube a ImgBB y se crea el item en ML)")
