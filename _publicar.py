"""
_publicar.py — Publicación REAL en ML para SKU A23-001-0009

Ejecutar SOLO después de revisar y confirmar _previa_publicacion.py.
Llama directamente a publisher.crear_item() (el mismo código que usa ps_ml.py).
"""

import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

os.chdir(Path(__file__).parent)
load_dotenv()

from clients.prestashop import PrestaShopClient
from clients.flexxus import FlexxusClient
from clients.mercadolibre_publish import MercadoLibrePublisher
from optimize_images import procesar_y_hostear

SKU = "A23-001-0009"
CATEGORY_ID = "MLA7524"
CATALOG_PRODUCT_ID = "MLA28090996"
LISTING_TYPE = "gold_special"

print("=" * 60)
print(f"PUBLICACIÓN REAL EN ML — SKU: {SKU}")
print("=" * 60)

# ── Producto PS ───────────────────────────────────────────────────────────────
print("\n[1/5] Consultando PrestaShop...")
ps = PrestaShopClient(os.environ["PS_API_KEY"])
p = ps.get_product_details_by_sku(SKU)
if not p:
    print(f"❌ SKU {SKU} no encontrado en PS (activos). Abortando.")
    sys.exit(1)
print(f"  ✓ {p['name']} — {len(p['image_urls'])} imagen/es")

# ── Precio ────────────────────────────────────────────────────────────────────
print("\n[2/5] Calculando precio...")
publisher = MercadoLibrePublisher()

try:
    flexxus = FlexxusClient()
    fx = flexxus.get_precio(SKU)
except Exception:
    fx = None

if fx:
    precio_usd_con_iva = fx["usd"] * (1 + fx["iva"])
    precio_ars_base = precio_usd_con_iva * fx["tipo_cambio"]
    print(f"  ✓ Flexxus: USD {fx['usd']} + IVA {fx['iva']*100:.0f}% × TC {fx['tipo_cambio']} = ${precio_ars_base:,.0f}")
else:
    from clients.prestashop import obtener_cotizacion_dolar
    tc = obtener_cotizacion_dolar()
    precio_ars_base = p["price"] * tc
    print(f"  ⚠️ Fallback PS: ${precio_ars_base:,.0f}")

pct_com, monto_com = publisher.obtener_comision(
    precio_ars_base, CATEGORY_ID, LISTING_TYPE, catalog_listing=True
)
precio_final = fx["pesos"] if fx else precio_ars_base
print(f"  ✓ Comisión: {pct_com}% = ${monto_com:,.0f} (solo informativa)")
print(f"  ✓ Precio final: ${precio_final:,.2f} ARS (Lista 5 Flexxus con IVA)")

# ── Atributos ─────────────────────────────────────────────────────────────────
print("\n[3/5] Extrayendo atributos...")
description = p.get("description", "")
attr_pattern = re.compile(r'\b([a-záéíóúüñA-ZÁÉÍÓÚÜÑA-Z][a-záéíóúüñA-ZÁÉÍÓÚÜÑa-z]*):\s*([^:\n]+?)(?=\s+[a-záéíóúüñA-ZÁÉÍÓÚÜÑA-Z][a-záéíóúüñA-ZÁÉÍÓÚÜÑa-z]*:|$)', re.DOTALL)
desc_attrs = {}
for m in attr_pattern.finditer(description):
    key = m.group(1).strip()
    val = m.group(2).strip().rstrip(".,;")
    if key and val and len(key) < 30 and len(val) < 100:
        desc_attrs[key.lower()] = val

ml_attrs_raw = publisher.obtener_atributos(CATEGORY_ID)
required_attrs = [a for a in ml_attrs_raw if a.get("tags", {}).get("required", False)]

ATTR_MAP = {
    "BRAND": ["marca", "brand"],
    "MODEL": ["modelo", "model"],
    "CALCULATOR_TYPE": ["tipo", "tipo de calculadora", "calculator_type"],
}

# Valores manuales para atributos no presentes en la descripción de PS
ATTR_FALLBACK = {
    "BRAND": "Casio",
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
    if not valor and attr_id in ATTR_FALLBACK:
        valor = ATTR_FALLBACK[attr_id]
        print(f"  ✓ {attr_id} = {valor!r} (fallback manual)")
    if valor:
        attrs_ml.append({"id": attr_id, "value_name": valor})
        print(f"  ✓ {attr_id} = {valor!r}")
    else:
        print(f"  ⚠️ {attr_id} sin valor — se omite")

# ── Imágenes ──────────────────────────────────────────────────────────────────
print("\n[4/5] Subiendo imágenes a ImgBB...")
imgs_publicas = []
for url in p["image_urls"]:
    try:
        pub_url = procesar_y_hostear(url)
        imgs_publicas.append(pub_url)
        print(f"  ✓ {url[:60]}... → {pub_url[:60]}...")
    except Exception as e:
        print(f"  ⚠️ No se pudo subir imagen: {e}")

if not imgs_publicas:
    print("❌ Sin imágenes disponibles para ML. Abortando.")
    sys.exit(1)

# ── Publicar ──────────────────────────────────────────────────────────────────
print("\n[5/5] Creando publicación en ML...")
stock_ml = max(p["stock"], 1)

item = publisher.crear_item(
    title=p["name"],
    category_id=CATEGORY_ID,
    price=precio_final,
    stock=stock_ml,
    description=p.get("description", ""),
    images=imgs_publicas,
    attributes=attrs_ml,
    condition="new",
    listing_type_id=LISTING_TYPE,
    family_name="Calculadora Casio Mx-8b",
    catalog_product_id=CATALOG_PRODUCT_ID,
    seller_custom_field=p.get("reference", ""),
)

if item:
    print("\n✅ PUBLICACIÓN EXITOSA")
    print(f"  ML ID      : {item.get('id')}")
    print(f"  Estado     : {item.get('status')}")
    print(f"  Precio     : ${item.get('price'):,.0f} ARS")
    print(f"  Permalink  : {item.get('permalink')}")
else:
    print("❌ No se recibió respuesta de ML. Revisar logs.")
