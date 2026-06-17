"""
tabs/ps_tiendanube.py — Pestaña "🏪 PS → Tiendanube (Infoandina)"

Qué hace esta pestaña:
  Permite publicar productos que están en PrestaShop/Infoandina directamente en
  Tiendanube, aunque ese producto NO esté en Mercado Libre.

  Útil para productos exclusivos de Infoandina que no fueron publicados en ML.

Flujo:
  1. El usuario ingresa el SKU/referencia del producto en PrestaShop
  2. La app busca el producto y sus imágenes en Infoandina
  3. Se muestra un preview con nombre, precio, stock e imagen
  4. El usuario confirma y la app:
       a. Enriquece título/descripción con IA (Gemini)
       b. Crea el producto en Tiendanube
       c. Sube las imágenes desde Infoandina
       d. Guarda el resultado en un CSV
"""

import time

import streamlit as st

from optimize_images import procesar_y_hostear
from enricher import enriquecer_producto
from utils import cargar_skus_tn, _aplicar_enriched, exportar_csv, IMAGE_DELAY


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
    st.header("Infoandina (PrestaShop) → Tiendanube")
    st.info(
        "Publicá productos desde PrestaShop/Infoandina aunque no estén publicados en Mercado Libre. "
        "Las imágenes se toman directamente desde Infoandina."
    )

    ps = st.session_state.ps
    tn = st.session_state.tn

    # Campo de búsqueda por SKU
    sku_ps_input = st.text_input(
        "SKU / Referencia en PrestaShop:",
        placeholder="Ej: ABC-1234",
        key="ps_sku_input",
    )

    if st.button("🔍 Buscar en Infoandina", key="btn_buscar_ps"):
        if not sku_ps_input.strip():
            st.warning("Ingresá un SKU.")
        else:
            cargar_skus_tn()
            if sku_ps_input in st.session_state.existing_skus:
                st.warning(f"El SKU **{sku_ps_input}** ya está publicado en Tiendanube.")
            else:
                with st.spinner("Buscando en PrestaShop..."):
                    # Una sola llamada trae producto + imágenes juntos
                    ps_prod = ps.get_product_details_by_sku(sku_ps_input)

                if not ps_prod:
                    st.error(f"No se encontró el SKU **{sku_ps_input}** en Infoandina.")
                else:
                    st.session_state["ps_result"] = ps_prod
                    st.session_state["ps_imgs"] = ps_prod.get("image_urls", [])
                    st.session_state["ps_sku_query"] = sku_ps_input
                    st.rerun()

    # Mostrar preview del producto encontrado y botón de publicación
    if (
        "ps_result" in st.session_state
        and st.session_state.get("ps_sku_query") == sku_ps_input
    ):
        _render_preview_y_publicar(tn)


def _render_preview_y_publicar(tn):
    """
    Muestra la info del producto encontrado en PS y ejecuta la publicación en TN
    cuando el usuario presiona el botón.
    """
    p = st.session_state["ps_result"]
    imgs = st.session_state.get("ps_imgs", [])

    st.success(f"Producto encontrado: **{p['name']}**")

    col1, col2 = st.columns(2)
    col1.metric("Referencia (SKU)", p["reference"])
    col2.metric("Precio", f"${p['price']:,.2f}")

    if imgs:
        st.image(imgs[0], width=180, caption="Vista previa")

    if p["stock"] <= 0:
        st.warning("Stock en 0 según PrestaShop. El producto se publicará con stock 0.")

    if st.button("🚀 Publicar en Tiendanube (desde Infoandina)", type="primary", key="pub_ps"):
        with st.status("Publicando desde PrestaShop...", expanded=True) as s:
            try:
                titulo = p["name"]
                ref = p["reference"]
                precio = p["price"]
                stock_ps = p["stock"]
                description_ps = p.get("description", "")

                # Payload base con los datos de PS
                payload = {
                    "name": {"es": titulo},
                    "description": {"es": description_ps or ""},
                    "published": True,
                    "requires_shipping": True,
                    "variants": [{
                        "price": f"{float(precio):.2f}",
                        "stock_management": True,
                        "stock": int(stock_ps),
                        "sku": ref,
                    }],
                }

                # Intentar enriquecer con IA; si falla, se usan los datos de PS
                st.write("• Generando descripcion con IA...")
                fake_item = {
                    "id": f"PS-{ref}",
                    "title": titulo,
                    "price": float(precio),
                    "available_quantity": int(stock_ps),
                    "condition": "new",
                    "attributes": [],
                }
                enriched = enriquecer_producto(fake_item, description_ps)
                if enriched:
                    payload = _aplicar_enriched(payload, enriched)
                    st.write("  ✅ Descripcion IA lista.")
                else:
                    st.write("  ⚠️ IA no disponible, usando datos de PS.")

                # Crear el producto en TN
                st.write("• Creando producto en Tiendanube...")
                creado = tn.create_product(payload)
                product_id = creado["id"]

                # Subir imágenes desde PS
                imgs_ok = 0
                if imgs:
                    st.write(f"• Subiendo {len(imgs)} imagen/es...")
                    for url in imgs:
                        try:
                            url_p = procesar_y_hostear(url)
                            tn.add_image(product_id, url_p)
                            imgs_ok += 1
                            time.sleep(IMAGE_DELAY)
                        except Exception as e:
                            st.write(f"  ⚠️ Imagen fallida: {e}")

                # Guardar en caché y exportar CSV
                st.session_state.existing_skus.add(ref)
                row = {
                    "tn_id": product_id,
                    "sku": ref,
                    "titulo": titulo,
                    "mla_id": f"PS-{ref}",
                    "image_source": "PS",
                }
                csv_path = exportar_csv([row])
                del st.session_state["ps_result"]

                s.update(label="✅ Publicado exitosamente!", state="complete")
                st.success(
                    f"TN ID: **{product_id}** | SKU: {ref} | "
                    f"{imgs_ok}/{len(imgs)} imágenes subidas"
                )
                st.info(f"CSV guardado: {csv_path}")

            except Exception as e:
                s.update(label="❌ Error al publicar", state="error")
                st.error(str(e))
