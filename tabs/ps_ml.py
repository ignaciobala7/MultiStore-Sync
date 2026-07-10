"""
tabs/ps_ml.py — Pestaña "📢 PS → Mercado Libre"

Qué hace esta pestaña:
  Permite publicar productos de Infoandina (PrestaShop) directamente en Mercado Libre.
  Guía al usuario paso a paso a través de un flujo de 5 pasos.

Flujo completo:
  Paso 1 — Buscar producto en Infoandina por SKU
  Paso 2 — Seleccionar la categoría correcta en ML
             (auto-detección con IA, búsqueda manual, o categorías populares)
  Paso 3 — Completar los atributos requeridos por la categoría de ML
             (algunos se auto-completan si el producto de PS tiene esos datos)
  Paso 4 — Ver precio final = precio PS + comisión ML calculada automáticamente
  Paso 5 — Crear la publicación en ML

Módulos de soporte:
  - clients/mercadolibre_publish.py : MercadoLibrePublisher, búsqueda de categorías,
                                      atributos, comisiones, auto-match
"""

import re

import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from clients.sku_tracker import SKUTracker
from clients.mercadolibre_publish import (
    MercadoLibrePublisher,
    sugerir_terminos_busqueda,
    auto_match_categoria,
    get_popular_categories_with_correct_ids,
)
from clients.prestashop import obtener_cotizacion_dolar
from optimize_images import procesar_y_hostear

ATTRS_BLACKLIST = {"EMPTY_GTIN_REASON", "VALUE_ADDED_TAX", "IMPORT_DUTY", "GTIN"}
PACKAGE_ATTR_IDS = {"SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_WEIGHT"}

# Margen bruto mínimo aceptable sobre el costo de reposición (con IVA incluido)
# para publicar un producto — regla de negocio, no restricción de ML.
MARGEN_MINIMO_PCT = 34

# Mapea los atributos de dimensiones "reales" del producto de catálogo (declaradas por el
# fabricante, sin el prefijo SELLER_) a nuestras claves internas de sugerencia.
_CATALOG_PKG_ATTR_MAP = {
    "PACKAGE_WIDTH": "width",
    "PACKAGE_HEIGHT": "height",
    "PACKAGE_LENGTH": "length",
    "PACKAGE_WEIGHT": "weight_kg",
}


def _parse_pkg_dims_from_catalog(attrs_raw):
    """
    Extrae ancho/alto/profundidad/peso reales del producto de catálogo (ej. "18.48 cm",
    "381.01 g") para usarlos como piso mínimo sugerido del paquete — ML rechaza publicar
    si el paquete declarado es más chico que el producto real.
    Devuelve solo las claves que se pudieron parsear.
    """
    result = {}
    for a in attrs_raw:
        dim_key = _CATALOG_PKG_ATTR_MAP.get(a.get("id"))
        if not dim_key:
            continue
        match = re.search(r"[\d.]+", a.get("value_name") or "")
        if not match:
            continue
        valor = float(match.group())
        if dim_key == "weight_kg":
            valor = valor / 1000  # ML lo expresa en gramos
        result[dim_key] = valor
    return result


_PKG_FIELD_LABELS = {
    "seller_package_width": "Ancho",
    "seller_package_length": "Profundidad",
    "seller_package_height": "Alto",
    "seller_package_weight": "Peso",
}


def _es_gtin_valido(valor) -> bool:
    """
    ML rechaza GTIN/EAN con formato inválido aunque sea numérico (ej. "4618", 4 dígitos,
    que en realidad es un fragmento del nombre del producto, no un código de barras real).
    Los códigos de barras estándar tienen 8 (EAN-8), 12 (UPC-A), 13 (EAN-13) o 14 (GTIN-14) dígitos.
    """
    s = str(valor or "").strip()
    return s.isdigit() and len(s) in (8, 12, 13, 14)


def _margen_pct(costo_real: float, precio_venta: float) -> float:
    """
    Margen bruto %, calculado sobre el costo de reposición (costo_real, ya con
    IVA incluido) — no sobre el precio de venta. Puede dar negativo si
    precio_venta < costo_real (se vendería por debajo del costo).
    """
    return (precio_venta - costo_real) / costo_real * 100


def _mensaje_error_publicacion(err_msg: str) -> str:
    """
    Traduce el error de ML a algo accionable. Para "packaging attributes [...] are
    too small for the product dimensions": ML no expone el mínimo real por API para
    todos los productos de catálogo (algunos no tienen PACKAGE_WIDTH/HEIGHT/LENGTH/WEIGHT
    cargados) — solo indica qué campo rechazó, hay que agrandarlo a mano y reintentar.
    """
    match = re.search(r"packaging attributes \[([^\]]+)\] are too small", err_msg, re.IGNORECASE)
    if match:
        campos = [c.strip().lower() for c in match.group(1).split(",")]
        etiquetas = [_PKG_FIELD_LABELS.get(c, c) for c in campos]
        return (
            f"⚠️ ML rechazó **{', '.join(etiquetas)}** por ser menor al tamaño real del producto. "
            "Este producto de catálogo no expone su medida real por API, así que no hay un mínimo "
            "exacto para sugerir acá — subí ese valor en el Paso 4 (probá de a poco: +1-2 cm o "
            "+50 g) y reintentá publicar."
        )
    if re.search(r"attributes? \[GTIN\] (?:is|are) required", err_msg, re.IGNORECASE):
        return (
            "⚠️ Esta categoría exige un GTIN/EAN real — no acepta \"El producto no tiene código "
            "registrado\" como alternativa, aunque la lista de atributos de la categoría lo permita. "
            "No hay forma de publicar acá sin un código de barras real. Opciones: buscá el producto "
            "en el catálogo de ML por nombre en el Paso 3 (el catálogo trae su propio GTIN), o "
            "conseguí el EAN real y cargalo manualmente en el Paso 1."
        )
    return f"Error: {err_msg}"


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
    st.markdown('<div id="ps_ml_top"></div>', unsafe_allow_html=True)
    st.header("Infoandina (PrestaShop) → Mercado Libre")
    st.info(
        "Publica productos desde Infoandina directamente a Mercado Libre. "
        "La app busca la categoría correcta en ML y maneja automáticamente los atributos requeridos."
    )

    ps = st.session_state.ps
    ml = st.session_state.ml  # cliente ML principal (ya autenticado)

    # Inicializar el publisher con los mismos tokens del cliente ML que ya funciona.
    # Sin esto, el publisher crea su propia auth independiente y puede fallar.
    if "ml_publisher" not in st.session_state:
        publisher = MercadoLibrePublisher()
        publisher.access_token = ml.access_token
        publisher.refresh_token = ml.refresh_token
        publisher.client_id = ml.client_id
        publisher.client_secret = ml.client_secret
        publisher.token_expires_at = float("inf")  # evitar refresh automático
        st.session_state.ml_publisher = publisher

    publisher = st.session_state.ml_publisher

    # Mantener tokens sincronizados en caso de que el cliente principal los haya renovado
    publisher.access_token = ml.access_token
    publisher.refresh_token = ml.refresh_token

    if "ps_ml_tracker" not in st.session_state:
        st.session_state["ps_ml_tracker"] = SKUTracker()
    tracker = st.session_state["ps_ml_tracker"]

    # ── Paso 1: Buscar producto en PS ─────────────────────────────────────────
    st.subheader("Paso 1: Selecciona el producto en Infoandina")

    sku_ps_ml = st.text_input(
        "SKU / Referencia en PrestaShop:",
        placeholder="Ej: ABC-1234",
        key="ps_ml_sku_input",
    )

    if st.button("🔍 Buscar en Infoandina", key="ps_ml_buscar"):
        if not sku_ps_ml.strip():
            st.warning("Ingresá un SKU.")
        else:
            with st.spinner("Buscando en PrestaShop..."):
                # Una sola llamada trae producto + imágenes juntos
                ps_prod_ml = ps.get_product_details_by_sku(sku_ps_ml)

            if not ps_prod_ml:
                st.error(f"No se encontró el SKU **{sku_ps_ml}** en Infoandina.")
            else:
                st.session_state["ps_ml_product"] = ps_prod_ml
                st.session_state["ps_ml_images"] = ps_prod_ml.get("image_urls", [])
                st.session_state["ps_ml_sku_query"] = sku_ps_ml
                # Buscar precio con IVA en Flexxus
                flexxus = st.session_state.get("flexxus")
                flexxus_price = flexxus.get_precio(sku_ps_ml) if flexxus else None
                st.session_state["ps_ml_flexxus_price"] = flexxus_price
                st.session_state["ps_ml_ean"] = flexxus.get_ean(sku_ps_ml) if flexxus else None
                st.session_state["ps_ml_ean_raw"] = flexxus.get_ean_raw(sku_ps_ml) if flexxus else None
                st.session_state["ps_ml_vat"] = flexxus.get_value_added_tax(sku_ps_ml) if flexxus else None
                # Resetear tipo de cambio para que se inicialice desde Flexxus
                st.session_state.pop("ps_ml_tc", None)
                # Limpiar elecciones y checks del tracker de búsquedas anteriores
                for k in list(st.session_state.keys()):
                    if k.startswith("ps_ml_choice_") or k.startswith("ps_ml_ml_checked_"):
                        del st.session_state[k]
                st.session_state.pop("ps_ml_catalog_attrs", None)
                st.session_state.pop("ps_ml_gemini_attrs", None)
                st.session_state.pop("ps_ml_attrs_confirmed", None)
                for k in ("ps_ml_catalogo", "ps_ml_catalogo_sel_id", "ps_ml_catalogo_sel_name",
                          "ps_ml_gtin_result", "ps_ml_gtin_checked", "ps_ml_use_gtin_match",
                          "ps_ml_ean_input", "ps_ml_gtin_manual", "ps_ml_gtin_required"):
                    st.session_state.pop(k, None)
                st.rerun()

    # Solo continuar si hay un producto buscado que coincide con el input actual
    if (
        "ps_ml_product" in st.session_state
        and st.session_state.get("ps_ml_sku_query") == sku_ps_ml
    ):
        p = st.session_state["ps_ml_product"]
        imgs = st.session_state.get("ps_ml_images", [])
        flexxus_price = st.session_state.get("ps_ml_flexxus_price")
        _render_pasos_2_a_5(p, imgs, publisher, flexxus_price, tracker=tracker)


# ── Pasos 2 a 5 (solo se muestran cuando hay un producto encontrado) ──────────

def _render_pasos_2_a_5(p, imgs, publisher, flexxus_price=None, tracker=None):
    """
    Renderiza los pasos 2-5 del flujo PS → ML:
    categoría, atributos, precio con comisión, y publicación final.
    """
    # Mostrar resumen del producto encontrado
    st.success(f"Producto encontrado: **{p['name']}**")
    tc_resumen = (
        flexxus_price["tipo_cambio"] if flexxus_price
        else st.session_state.get("ps_ml_tc")
    )
    # Misma cantidad de columnas en ambas filas para que los bordes alineen entre sí.
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Referencia (SKU)", p["reference"])
    col2.metric("Stock disponible", p["stock"])

    def _on_ean_change():
        # El EAN cambió — invalidar la búsqueda de catálogo por GTIN para que se repita con el nuevo valor
        for k in ("ps_ml_gtin_result", "ps_ml_gtin_checked", "ps_ml_use_gtin_match"):
            st.session_state.pop(k, None)

    def _clear_ean():
        st.session_state["ps_ml_ean_input"] = ""
        _on_ean_change()

    with col3:
        st.caption("EAN (editable — Flexxus lo carga por defecto)")
        ean_input = st.text_input(
            "EAN",
            value=st.session_state.get("ps_ml_ean") or "",
            key="ps_ml_ean_input",
            label_visibility="collapsed",
            on_change=_on_ean_change,
        )
        st.button(
            "✖ Publicar sin EAN", key="ps_ml_ean_clear",
            on_click=_clear_ean, disabled=not ean_input,
        )
    ean = ean_input.strip()
    st.session_state["ps_ml_ean"] = ean
    # col4 queda vacía a propósito, alineada arriba de "Precio Flexxus (ARS)"

    col_ps_usd, col_ps_ars, col_fx_usd, col_fx_ars = st.columns(4)
    col_ps_usd.metric("Precio PS (USD)", f"USD {p['price']:,.2f}")
    if tc_resumen:
        col_ps_ars.metric("Precio PS (ARS)", f"${p['price'] * tc_resumen:,.0f}")
        col_ps_ars.caption(f"TC: {tc_resumen:,.0f}")
    else:
        col_ps_ars.metric("Precio PS (ARS)", "—")

    if flexxus_price:
        # Flexxus (Lista 5) es la fuente de verdad cuando está disponible — mismo precio
        # que termina usándose como precio_final más abajo.
        col_fx_usd.metric("Precio Flexxus (USD)", f"USD {flexxus_price['usd']:,.2f}")
        col_fx_usd.caption(f"IVA {flexxus_price['iva']*100:.1f}%")
        col_fx_ars.metric("Precio Flexxus (ARS)", f"${flexxus_price['pesos']:,.0f}")
        col_fx_ars.caption(f"TC: {flexxus_price['tipo_cambio']:,.0f}")
    else:
        col_fx_usd.metric("Precio Flexxus (USD)", "—")
        col_fx_ars.metric("Precio Flexxus (ARS)", "—")
    if imgs:
        st.image(imgs[0], width=180, caption="Vista previa")

    # ── Paso 2: Selección de categoría en ML ──────────────────────────────────
    st.divider()
    st.subheader("Paso 2: Selecciona categoría en Mercado Libre")

    # Sugerir términos de búsqueda basados en el nombre del producto
    terminos_sugeridos = sugerir_terminos_busqueda(p['name'])
    if terminos_sugeridos:
        st.info(f"💡 **Sugerencias:** {', '.join(terminos_sugeridos)}")

    col_auto, col_manual = st.columns([1, 1])

    with col_auto:
        # Auto-detección: usa IA para mapear el nombre del producto a una categoría de ML
        if st.button("🤖 Auto-detectar categoría", use_container_width=True, key="ps_ml_auto_match"):
            try:
                with st.spinner("Buscando categoría automáticamente..."):
                    cat_auto = auto_match_categoria(p['name'], publisher)
                if cat_auto:
                    st.session_state["ps_ml_auto_category"] = cat_auto
                    st.session_state["ps_ml_categories"] = [cat_auto]
                    st.rerun()
                else:
                    st.warning("No se pudo detectar automáticamente. Busca manualmente.")
            except Exception as e:
                st.error(f"Error al auto-detectar categoría: {e}")

    with col_manual:
        st.write("**O busca manualmente:**")

    query_cat = st.text_input(
        "Ingresa término de búsqueda:",
        placeholder="ej: 'teclado', 'mouse', 'periféricos'",
        key="ps_ml_cat_query",
    )

    if st.button("🔍 Buscar categoría en ML", key="ps_ml_buscar_cat"):
        if not query_cat.strip():
            st.warning("Ingresá una categoría.")
        else:
            try:
                with st.spinner("Buscando categorías en ML..."):
                    cats = publisher.buscar_categorias(query_cat, limit=10)
                st.session_state["ps_ml_categories"] = cats
                if not cats:
                    st.warning("No se encontraron categorías con ese término.")
            except Exception as e:
                st.error(f"Error al buscar categorías: {e}")

    # Si no hay resultados de búsqueda, mostrar categorías populares como atajos
    if "ps_ml_categories" not in st.session_state or not st.session_state.get("ps_ml_categories"):
        st.write("**Categorías populares:**")

        # Cachear los IDs validados para no llamar a la API en cada render
        if "ps_ml_popular_cats" not in st.session_state:
            with st.spinner("Validando categorías..."):
                publisher_temp = MercadoLibrePublisher()
                st.session_state["ps_ml_popular_cats"] = get_popular_categories_with_correct_ids(publisher_temp)

        popular_cats = st.session_state["ps_ml_popular_cats"]
        col_cat1, col_cat2 = st.columns(2)
        for i, (cat_name, cat_id) in enumerate(popular_cats.items()):
            col = col_cat1 if i % 2 == 0 else col_cat2
            # Key única por nombre + ID para evitar colisiones de keys en Streamlit
            unique_key = f"pop_cat_{cat_name.replace(' ', '_')}_{cat_id}"
            if col.button(f"→ {cat_name}", use_container_width=True, key=unique_key):
                cat_obj = {"category_id": cat_id, "category_name": cat_name}
                st.session_state["ps_ml_categories"] = [cat_obj]
                st.rerun()

    # Selector de categoría y continuación del flujo
    if "ps_ml_categories" in st.session_state:
        cats = st.session_state["ps_ml_categories"]
        if not cats:
            st.warning("No se encontraron categorías. Intenta con otro término.")
        else:
            cat_options = {
                f"{c['category_name']} (ID: {c['category_id']})": c
                for c in cats
            }
            sel_cat_label = st.selectbox("Selecciona la categoría:", list(cat_options.keys()))
            selected_cat = cat_options[sel_cat_label]
            cat_id = selected_cat["category_id"]

            # ── Paso 3: Catálogo de ML ────────────────────────────────────────
            st.divider()
            st.subheader("Paso 3: Producto en catálogo ML (recomendado)")
            st.caption("Muchas categorías requieren asociar la publicación al catálogo interno de ML. Si encontrás el producto, usalo.")

            catalog_product_id = ""
            catalog_family_name = ""

            def _pic_url(r):
                pics = r.get("pictures") or []
                return pics[0]["url"] if pics else None

            # Prioridad 1: búsqueda automática por EAN/GTIN — es lo más preciso
            # (ML lo ofrece como primera opción, "Por código", en su propio flujo de publicación).
            if _es_gtin_valido(ean) and "ps_ml_gtin_checked" not in st.session_state:
                with st.spinner(f"Buscando en catálogo por EAN {ean}..."):
                    st.session_state["ps_ml_gtin_result"] = publisher.buscar_en_catalogo_por_gtin(str(ean))
                st.session_state["ps_ml_gtin_checked"] = True

            gtin_match = st.session_state.get("ps_ml_gtin_result")
            usar_gtin = st.session_state.get("ps_ml_use_gtin_match", True)

            if gtin_match and usar_gtin:
                col_img, col_info = st.columns([1, 5])
                with col_img:
                    url = _pic_url(gtin_match)
                    if url:
                        st.image(url, width=70)
                with col_info:
                    st.success(f"✅ Encontrado por EAN **{ean}**: **{gtin_match['name']}** ({gtin_match['id']})")
                    if st.button("✖ No es este producto — buscar por nombre", key="ps_ml_rechazar_gtin"):
                        st.session_state["ps_ml_use_gtin_match"] = False
                        st.rerun()
                catalog_product_id = gtin_match["id"]
                catalog_family_name = gtin_match["name"]
            else:
                if _es_gtin_valido(ean) and not gtin_match:
                    st.caption(f"No se encontró coincidencia en el catálogo por EAN {ean}.")
                elif not ean:
                    st.caption("Sin EAN cargado para este SKU — no se puede buscar por código.")
                elif not _es_gtin_valido(ean):
                    st.caption(f"⚠️ El EAN cargado (**{ean}**) no tiene un formato de código de barras válido (8/12/13/14 dígitos) — no se usó para buscar en catálogo.")

                # Nota: la API de catálogo de ML no acepta búsqueda por SKU propio —
                # el catálogo es global entre vendedores, no conoce tu SKU interno.
                # Solo admite palabra clave (q) o código universal (product_identifier / EAN).
                st.caption(
                    "ℹ️ No es posible buscar por tu SKU interno: el catálogo de ML es global "
                    "y solo admite búsqueda por EAN/GTIN o por nombre."
                )

                if st.button("🔎 Buscar en catálogo por nombre", key="ps_ml_buscar_catalogo"):
                    with st.spinner("Buscando en catálogo..."):
                        st.session_state["ps_ml_catalogo"] = publisher.buscar_en_catalogo(p['name'], limit=20)

                catalogo = st.session_state.get("ps_ml_catalogo")

                if catalogo is not None:
                    if not catalogo:
                        if st.button("Continuar sin catálogo →", key="ps_ml_continuar_sin_catalogo"):
                            st.session_state["ps_ml_skip_catalog"] = True
                            st.rerun()
                    else:
                        st.caption(f"{len(catalogo)} resultado/s encontrado/s en el catálogo.")
                        sel_id = st.session_state.get("ps_ml_catalogo_sel_id", "")

                        if st.button("— Publicar con título manual —", key="ps_ml_catalogo_sel_none"):
                            st.session_state["ps_ml_catalogo_sel_id"] = ""
                            st.session_state["ps_ml_catalogo_sel_name"] = ""
                            st.session_state["ps_ml_skip_catalog"] = True
                            st.rerun()

                        for r in catalogo:
                            col_img, col_info, col_btn = st.columns([1, 5, 2])
                            with col_img:
                                url = _pic_url(r)
                                if url:
                                    st.image(url, width=60)
                            with col_info:
                                label = r["name"] if r.get("status") == "active" else f"{r['name']} ({r.get('status', '?')})"
                                st.write(label)
                            with col_btn:
                                is_sel = sel_id == r["id"]
                                if st.button(
                                    "✓ Elegido" if is_sel else "Elegir",
                                    key=f"ps_ml_sel_{r['id']}",
                                    disabled=is_sel,
                                ):
                                    st.session_state["ps_ml_catalogo_sel_id"] = r["id"]
                                    st.session_state["ps_ml_catalogo_sel_name"] = r["name"]
                                    st.rerun()

                        sel_id = st.session_state.get("ps_ml_catalogo_sel_id", "")
                        if sel_id:
                            catalog_product_id = sel_id
                            catalog_family_name = st.session_state.get("ps_ml_catalogo_sel_name", "")
                            st.success(f"Catálogo: **{catalog_family_name}** ({catalog_product_id})")

            # ── Ajuste automático de categoría desde catálogo ─────────────────
            if catalog_product_id:
                last_cpid = st.session_state.get("ps_ml_last_catalog_product_id", "")
                if catalog_product_id != last_cpid:
                    with st.spinner("Verificando categoría y atributos del producto en catálogo..."):
                        # Las 3 llamadas son independientes entre sí (mismo catalog_product_id,
                        # sin dependencias de datos entre una y otra) — se corren en paralelo
                        # en vez de en secuencia, mismo patrón que la subida de imágenes.
                        with ThreadPoolExecutor(max_workers=3) as pool:
                            fut_cat = pool.submit(
                                publisher.obtener_categoria_de_catalogo,
                                catalog_product_id, product_name=catalog_family_name,
                            )
                            fut_prod = pool.submit(publisher.get_catalog_product, catalog_product_id)
                            fut_precio = pool.submit(
                                publisher.obtener_precio_referencia_catalogo, catalog_product_id
                            )
                            cat_from_catalog = fut_cat.result()
                            catalog_product_data = fut_prod.result()
                            precio_ref_catalogo = fut_precio.result()
                    st.session_state["ps_ml_last_catalog_product_id"] = catalog_product_id
                    st.session_state["ps_ml_catalog_category_id"] = cat_from_catalog
                    st.session_state["ps_ml_catalog_precio_ref"] = precio_ref_catalogo
                    if catalog_product_data:
                        catalog_attrs_raw = catalog_product_data.get("attributes", [])
                        st.session_state["ps_ml_catalog_attrs"] = {
                            a["id"]: a.get("value_name") or a.get("value_id", "")
                            for a in catalog_attrs_raw
                            if a.get("value_name") or a.get("value_id")
                        }
                        pkg_suggested = _parse_pkg_dims_from_catalog(catalog_attrs_raw)
                        st.session_state["ps_ml_catalog_pkg_dims"] = pkg_suggested
                        # Precarga los inputs de dimensiones como "piso mínimo" editable —
                        # solo al cambiar de producto de catálogo, no en cada re-render.
                        for dim_key, widget_key in (
                            ("width", "ps_ml_pkg_width"), ("height", "ps_ml_pkg_height"),
                            ("length", "ps_ml_pkg_length"), ("weight_kg", "ps_ml_pkg_weight"),
                        ):
                            if dim_key in pkg_suggested:
                                st.session_state[widget_key] = pkg_suggested[dim_key]
                            else:
                                st.session_state.pop(widget_key, None)
                    else:
                        st.session_state.pop("ps_ml_catalog_attrs", None)
                        st.session_state.pop("ps_ml_catalog_pkg_dims", None)
                    st.session_state.pop("ps_ml_gemini_attrs", None)
                    st.session_state.pop("ps_ml_attrs_confirmed", None)
                cat_from_catalog = st.session_state.get("ps_ml_catalog_category_id")
                if cat_from_catalog:
                    if cat_from_catalog != cat_id:
                        st.info(
                            f"Categoría ajustada automáticamente al catálogo: **{cat_from_catalog}** "
                            f"(la elegida manualmente era **{cat_id}**)"
                        )
                        cat_id = cat_from_catalog
                else:
                    st.warning(
                        "⚠️ No se pudo verificar la categoría del catálogo. "
                        "Se usará la categoría seleccionada manualmente."
                    )

                # ── Precio de referencia del catálogo vs. costo real ───────────
                # OJO: flexxus_price["pesos"] es el precio de VENTA Lista 5 (ya
                # incluye margen, ej. 48%) — NO es el costo. El costo real de
                # reposición es flexxus_price["costo_pesos"] (PRECIOCOMPRA, sin
                # margen). Usar "pesos" acá subestima el margen real disponible.
                precio_ref_catalogo = st.session_state.get("ps_ml_catalog_precio_ref")
                if precio_ref_catalogo:
                    col_ref1, col_ref2, col_ref3 = st.columns(3)
                    col_ref1.metric(
                        "Precio de referencia (buy box)",
                        f"${precio_ref_catalogo['precio_buybox']:,.0f}",
                    )
                    col_ref2.metric(
                        "Rango de precios activos",
                        f"${precio_ref_catalogo['precio_min']:,.0f} – ${precio_ref_catalogo['precio_max']:,.0f}",
                    )
                    col_ref3.metric(
                        "Publicaciones activas",
                        precio_ref_catalogo["cantidad"],
                    )
                    costo_real = flexxus_price.get("costo_pesos") if flexxus_price else None
                    # margen5_pct = margen que Flexxus ya tiene configurado para la Lista 5
                    # (columna MARGEN5 del Excel) — dato de REFERENCIA, no el margen real
                    # calculado contra el mercado. Ver nota de portabilidad en flexxus.py:
                    # acá viene de un Excel exportado a mano (puede estar desactualizado);
                    # en el dashboard JS conviene traerlo en vivo de la API de Flexxus V5.
                    margen5_pct = flexxus_price.get("margen5_pct") if flexxus_price else None

                    col_m1, col_m2 = st.columns(2)
                    with col_m1:
                        if costo_real and costo_real > 0:
                            margen_pct = _margen_pct(costo_real, precio_ref_catalogo["precio_buybox"])
                            if margen_pct < MARGEN_MINIMO_PCT:
                                st.error(
                                    f"⚠️ Margen vs. catálogo: ~{margen_pct:,.0f}% "
                                    f"(costo real ${costo_real:,.0f} vs. "
                                    f"${precio_ref_catalogo['precio_buybox']:,.0f}) — por debajo "
                                    f"del mínimo aceptable de {MARGEN_MINIMO_PCT}%. Revisá el precio "
                                    "antes de publicar en este catálogo."
                                )
                            else:
                                st.caption(
                                    f"✅ Margen vs. catálogo: ~{margen_pct:,.0f}% "
                                    f"(mínimo aceptable: {MARGEN_MINIMO_PCT}%)."
                                )
                        elif flexxus_price:
                            st.caption(
                                "ℹ️ Flexxus no trae costo de reposición (PRECIOCOMPRA) para este "
                                "SKU — no se puede calcular el margen real frente al catálogo."
                            )
                    with col_m2:
                        if margen5_pct is not None:
                            st.metric("Margen configurado en Flexxus (Lista 5)", f"{margen5_pct:,.0f}%")
                            st.caption(
                                "⚠️ Dato de referencia tal cual está en el Excel exportado — puede "
                                "estar desactualizado. No es el margen real calculado contra el "
                                "catálogo (columna de la izquierda)."
                            )
                        else:
                            st.caption("ℹ️ Flexxus no trae Margen 5 (MARGEN5) para este SKU.")
                else:
                    st.caption(
                        "ℹ️ No se encontraron publicaciones activas para este producto de catálogo "
                        "— no hay precio de referencia disponible para comparar."
                    )
            else:
                # Solo limpiar attrs si veníamos de modo catálogo (transición, no cada re-render)
                was_using_catalog = "ps_ml_last_catalog_product_id" in st.session_state
                st.session_state.pop("ps_ml_last_catalog_product_id", None)
                st.session_state.pop("ps_ml_catalog_category_id", None)
                st.session_state.pop("ps_ml_catalog_attrs", None)
                st.session_state.pop("ps_ml_catalog_pkg_dims", None)
                st.session_state.pop("ps_ml_catalog_precio_ref", None)
                if was_using_catalog:
                    st.session_state.pop("ps_ml_gemini_attrs", None)
                    st.session_state.pop("ps_ml_attrs_confirmed", None)

            # ── Aviso: categoría puede exigir GTIN real ────────────────────────
            # Se chequea acá (Paso 3), no recién al publicar en Paso 6, para no
            # hacer subir imágenes y crear el ítem en ML solo para enterarse del
            # rechazo. Ver requiere_gtin_real() en mercadolibre_publish.py.
            last_cat_gtin_check = st.session_state.get("ps_ml_last_cat_gtin_check", "")
            if cat_id != last_cat_gtin_check:
                st.session_state["ps_ml_cat_requiere_gtin"] = publisher.requiere_gtin_real(cat_id)
                st.session_state["ps_ml_last_cat_gtin_check"] = cat_id

            if (
                st.session_state.get("ps_ml_cat_requiere_gtin")
                and not catalog_product_id
                and not _es_gtin_valido(ean)
            ):
                st.warning(
                    "⚠️ Esta categoría suele exigir un GTIN/EAN real para publicar, aunque su "
                    "metadata declare aceptar \"sin código\" como alternativa. Sin EAN de Flexxus "
                    "ni un producto de catálogo seleccionado arriba, ML puede rechazar la "
                    "publicación recién en el Paso 6 (después de subir imágenes y crear el ítem). "
                    "Buscá el producto en el catálogo de ML (Paso 3) o conseguí el código de "
                    "barras real antes de continuar."
                )

            # ── Paso 4: Atributos de la categoría ────────────────────────────────
            st.divider()
            st.subheader("Paso 4: Completa los atributos requeridos")

            with st.spinner("Obteniendo atributos de ML..."):
                ml_attrs = publisher.obtener_atributos(cat_id)

            import_duty_attr = next((a for a in ml_attrs if a.get("id") == "IMPORT_DUTY"), None)
            import_duty_value = ""
            if import_duty_attr:
                for val in import_duty_attr.get("values", []) or []:
                    val_name = (val.get("name") or "").lower()
                    if "no" in val_name or "0" in val_name or "exento" in val_name:
                        import_duty_value = val.get("name", "")
                        break
            st.session_state["ps_ml_import_duty"] = import_duty_value

            if not ml_attrs:
                st.warning("No se pudieron obtener atributos. Intenta con otra categoría.")
            else:
                required_attrs = [
                    a for a in ml_attrs
                    if a.get("id") not in ATTRS_BLACKLIST
                    and a.get("id") not in PACKAGE_ATTR_IDS
                    and (
                        a.get("tags", {}).get("required")
                        or a.get("tags", {}).get("conditional_required")
                    )
                ]
                pkg_required = any(
                    a.get("id") in PACKAGE_ATTR_IDS
                    and (a.get("tags", {}).get("required") or a.get("tags", {}).get("conditional_required"))
                    for a in ml_attrs
                )
                st.write(f"**{len(required_attrs)} atributo/s a completar:**")
                gtin_attr = next((a for a in ml_attrs if a.get("id") == "GTIN"), None)
                gtin_required = bool(gtin_attr and (
                    gtin_attr.get("tags", {}).get("required")
                    or gtin_attr.get("tags", {}).get("conditional_required")
                ))
                st.session_state["ps_ml_gtin_required"] = gtin_required
                if _es_gtin_valido(ean):
                    st.caption(f"ℹ️ GTIN se toma automáticamente del EAN de Flexxus: **{ean}**")
                elif gtin_required:
                    ean_raw = st.session_state.get("ps_ml_ean_raw") or ""
                    # Solo pre-cargar el manual si el valor crudo de Flexxus pasa el chequeo de
                    # formato (8/12/13/14 dígitos) — precargar algo como "LCRIMP003" (fragmento
                    # de código de producto, no un código de barras) confunde más que ayuda.
                    prefill = ean_raw if _es_gtin_valido(ean_raw) else ""
                    if ean_raw and not prefill:
                        st.warning(f"⚠️ El EAN de Flexxus (**{ean_raw}**) no tiene formato de código de barras válido — no se precargó. Completá el GTIN manualmente si lo tenés, o dejalo vacío para publicar sin GTIN:")
                    elif ean_raw:
                        st.warning(f"⚠️ El EAN de Flexxus (**{ean_raw}**) se precargó — probá publicar así, corregilo, o dejalo vacío para publicar sin GTIN:")
                    else:
                        st.warning("⚠️ Esta categoría suele exigir GTIN y no hay EAN cargado en Flexxus — completalo si lo tenés, o dejalo vacío para publicar sin GTIN:")
                    if "ps_ml_gtin_manual" not in st.session_state:
                        st.session_state["ps_ml_gtin_manual"] = prefill
                    st.text_input("GTIN manual (opcional — se manda tal cual, aunque no sea puramente numérico):", key="ps_ml_gtin_manual")
                elif ean:
                    st.caption(f"ℹ️ El EAN de Flexxus (**{ean}**) no tiene formato de código de barras válido (8/12/13/14 dígitos) — se publicará sin GTIN.")
                else:
                    st.caption("ℹ️ GTIN: sin EAN numérico disponible en Flexxus, se publicará sin GTIN.")

                catalog_attrs = st.session_state.get("ps_ml_catalog_attrs", {})

                # Fallback Gemini: solo si no hay catálogo y aún no se intentó
                if not catalog_attrs and "ps_ml_gemini_attrs" not in st.session_state:
                    with st.spinner("Detectando atributos con IA..."):
                        from enricher import extraer_atributos
                        st.session_state["ps_ml_gemini_attrs"] = extraer_atributos(
                            p["name"],
                            [{"id": a.get("id", ""), "name": a.get("name", a.get("id", ""))} for a in required_attrs],
                        )

                gemini_attrs = st.session_state.get("ps_ml_gemini_attrs", {})
                pre_fill = catalog_attrs if catalog_attrs else gemini_attrs
                source = "catalog" if catalog_attrs else ("gemini" if gemini_attrs else "none")

                # Inicializar keys en session_state antes de renderizar widgets
                # (no usar value= en st.text_input para evitar conflictos con state existente)
                for attr in required_attrs:
                    attr_id = attr.get("id", "")
                    attr_name = attr.get("name", attr_id)
                    attr_key = f"attr_{cat_id}_{attr_id}"
                    if attr_key not in st.session_state:
                        auto_valor = pre_fill.get(attr_id)
                        if not auto_valor:
                            for k, v in p.items():
                                if isinstance(v, str) and (attr_name.lower() in k.lower() or k.lower() in attr_name.lower()):
                                    auto_valor = v
                                    break
                        if not auto_valor:
                            if attr_id == "BRAND":
                                auto_valor = p.get("manufacturer") or p.get("brand") or p.get("marca")
                                if not auto_valor:
                                    nombre = p.get("name", "")
                                    marcas_conocidas = ["Casio", "HP", "Canon", "Sharp", "Citizen", "Texas"]
                                    for marca in marcas_conocidas:
                                        if marca.lower() in nombre.lower():
                                            auto_valor = marca
                                            break
                                    if not auto_valor:
                                        palabras = nombre.split()
                                        auto_valor = palabras[1] if len(palabras) > 1 else ""
                            elif attr_id == "MODEL":
                                auto_valor = p.get("reference") or p.get("sku")
                        st.session_state[attr_key] = auto_valor or ""

                attr_values = {}
                for attr in required_attrs:
                    attr_id = attr.get("id", "")
                    attr_name = attr.get("name", attr_id)
                    attr_values[attr_id] = st.text_input(
                        f"**{attr_name}** ({attr_id}):",
                        key=f"attr_{cat_id}_{attr_id}",
                    )

                if pkg_required:
                    st.warning("⚠️ Esta categoría exige dimensiones de paquete (ancho, alto, profundidad y peso), incluso en modo catálogo. Completalas abajo antes de publicar.")

                pkg_suggested = st.session_state.get("ps_ml_catalog_pkg_dims") or {}
                if pkg_suggested:
                    _labels = {"width": "ancho", "height": "alto", "length": "profundidad", "weight_kg": "peso"}
                    _partes = [
                        f"{_labels[k]} {v:.1f} cm" if k != "weight_kg" else f"{_labels[k]} {v:.3f} kg"
                        for k, v in pkg_suggested.items()
                    ]
                    st.info(
                        f"💡 ML sugiere estas medidas como piso mínimo del producto: {', '.join(_partes)}. "
                        "Ya están precargadas abajo — podés agrandarlas si tu embalaje es más grande, "
                        "pero ML rechaza valores menores a esto."
                    )

                st.markdown("**Dimensiones del paquete:**")
                col_w, col_h, col_d, col_wt = st.columns(4)
                with col_w:
                    st.number_input("Ancho (cm)", min_value=0.0, step=0.1, value=None, placeholder="10", key="ps_ml_pkg_width")
                with col_h:
                    st.number_input("Alto (cm)", min_value=0.0, step=0.1, value=None, placeholder="10", key="ps_ml_pkg_height")
                with col_d:
                    st.number_input("Profundidad (cm)", min_value=0.0, step=0.1, value=None, placeholder="10", key="ps_ml_pkg_length")
                with col_wt:
                    st.number_input("Peso (kg)", min_value=0.0, step=0.001, value=None, placeholder="10", key="ps_ml_pkg_weight")

                btn_label = "⚠️ Revisar info y confirmar" if source == "gemini" else "✓ Confirmar atributos"
                if st.button(btn_label, type="primary", key="ps_ml_confirm_attrs"):
                    valores = {
                        attr.get("id", ""): st.session_state.get(f"attr_{cat_id}_{attr.get('id', '')}", "")
                        for attr in required_attrs
                    }
                    faltantes = [
                        attr.get("name", attr.get("id", ""))
                        for attr in required_attrs
                        if attr.get("tags", {}).get("required")
                        and not valores.get(attr.get("id", ""), "").strip()
                    ]
                    if faltantes:
                        st.error(
                            f"⚠️ Faltan atributos obligatorios: {', '.join(faltantes)}. "
                            "ML rechaza la publicación sin estos valores."
                        )
                        st.session_state["ps_ml_attrs_confirmed"] = False
                    else:
                        st.session_state["ps_ml_attrs_values"] = valores
                        st.session_state["ps_ml_attrs_confirmed"] = True

                if not st.session_state.get("ps_ml_attrs_confirmed"):
                    st.info("Completá los atributos y confirmá para continuar.")
                    return

                st.success("Atributos confirmados.")

                # ── Paso 5: Precio con comisión ───────────────────────────────
                st.divider()
                st.subheader("Paso 5: Precio y comisión ML")

                if "ps_ml_tc" not in st.session_state:
                    if flexxus_price:
                        st.session_state["ps_ml_tc"] = flexxus_price["tipo_cambio"]
                    else:
                        st.session_state["ps_ml_tc"] = obtener_cotizacion_dolar()

                tipo_de_cambio = st.number_input(
                    "Tipo de cambio USD → ARS:",
                    min_value=1.0,
                    value=float(st.session_state["ps_ml_tc"]),
                    step=10.0,
                    key="ps_ml_tc",
                )

                if flexxus_price:
                    precio_usd_con_iva = flexxus_price["usd"] * (1 + flexxus_price["iva"])
                    precio_ars = precio_usd_con_iva * tipo_de_cambio
                else:
                    precio_usd_con_iva = None
                    precio_ars = p['price'] * tipo_de_cambio
                    st.warning("⚠️ SKU no encontrado en Flexxus — precio sin IVA. Verificar manualmente.")

                col_a, col_b = st.columns(2)
                with col_a:
                    if flexxus_price:
                        st.metric("Precio USD (con IVA)", f"USD {precio_usd_con_iva:,.2f}")
                        st.caption(f"Base: USD {flexxus_price['usd']:,.2f} + IVA {flexxus_price['iva']*100:.0f}%")
                    else:
                        st.metric("Precio PS (USD, sin IVA)", f"USD {p['price']:,.2f}")
                    st.metric("Precio en ARS", f"${precio_ars:,.0f}")
                with col_b:
                    with st.spinner("Calculando comisión..."):
                        pct_com, monto_com = publisher.obtener_comision(
                            precio_ars, cat_id, "gold_special",
                            catalog_listing=bool(catalog_product_id),
                        )
                    st.metric("Comisión ML", f"${monto_com:,.2f} ({pct_com}%)")
                    if monto_com == 0:
                        st.caption("⚠️ No se pudo calcular la comisión")
                    else:
                        st.caption("Solo informativa — no se suma al precio de publicación")

                # precio_ars ya usa la base correcta (Flexxus con IVA, o PS de fallback) con
                # el tipo_de_cambio editable de arriba — no volver a flexxus_price["pesos"],
                # que queda congelado con el TC del Excel e ignora el ajuste manual del usuario.
                precio_final = precio_ars
                col_x, col_y = st.columns(2)
                with col_x:
                    st.metric("Precio final en ML", f"${precio_final:,.2f} ARS")
                with col_y:
                    st.write("")
                    if flexxus_price:
                        st.write("*Lista 5 Flexxus con IVA (la comisión es solo informativa)*")
                    else:
                        st.write("*Precio PS × tipo de cambio (la comisión es solo informativa)*")

                # ── Paso 6: Publicar en ML ────────────────────────────────────
                st.divider()
                st.subheader("Paso 6: Publicar en Mercado Libre")

                # ML no acepta publicaciones con stock 0 — avisar y forzar mínimo 1
                stock_ml = max(p['stock'], 1)
                if p['stock'] == 0:
                    st.warning("⚠️ El stock en PS es 0. ML requiere al menos 1 unidad para publicar — se enviará stock = 1.")

                # ── Verificar SKU en tracker ──────────────────────────────────────
                sku_ref = p.get('reference', '').strip()
                existing_record = tracker.get(sku_ref) if (tracker and sku_ref) else None

                # Si no está en CSV, consultar ML por si fue publicado antes del tracker
                if not existing_record and tracker and sku_ref:
                    _ml_check_key = f"ps_ml_ml_checked_{sku_ref}"
                    if not st.session_state.get(_ml_check_key):
                        with st.spinner("Verificando publicaciones previas en ML..."):
                            try:
                                ml_items = st.session_state.ml.search_items_by_sku(sku_ref)
                            except Exception:
                                ml_items = []
                        if ml_items:
                            item_ml = ml_items[0]
                            existing_record = tracker.save(
                                sku=sku_ref,
                                mla_id=item_ml.get("id", ""),
                                catalog_product_id=item_ml.get("catalog_product_id", "") or "",
                                titulo=item_ml.get("title", ""),
                                precio_ars=item_ml.get("price", 0),
                            )
                        else:
                            st.session_state[_ml_check_key] = True

                _choice_key = f"ps_ml_choice_{sku_ref}"
                _show_publish_btn = True

                if existing_record:
                    _choice = st.session_state.get(_choice_key)

                    if _choice is None:
                        _show_publish_btn = False
                        mla_id_ex = existing_record["mla_id"]
                        ml_url_ex = f"https://articulo.mercadolibre.com.ar/{mla_id_ex.replace('MLA', 'MLA-')}"
                        st.warning(
                            f"⚠️ Este SKU ya tiene una publicación registrada: **{mla_id_ex}** "
                            f"— publicado el {existing_record.get('fecha_publicacion', '—')}, "
                            f"estado: **{existing_record.get('estado', '—')}**"
                        )
                        col_v, col_u, col_f = st.columns(3)
                        with col_v:
                            st.link_button("👁️ Ver en ML", url=ml_url_ex, use_container_width=True)
                        with col_u:
                            if st.button("🔄 Actualizar precio y stock", use_container_width=True, key="ps_ml_tracker_update"):
                                st.session_state[_choice_key] = "update"
                                st.rerun()
                        with col_f:
                            if st.button("⚠️ Publicar de todas formas", use_container_width=True, key="ps_ml_tracker_force"):
                                st.session_state[_choice_key] = "force"
                                st.rerun()

                    elif _choice == "update":
                        _show_publish_btn = False
                        mla_id_ex = existing_record["mla_id"]
                        st.info(
                            f"Vas a actualizar precio y stock de **{mla_id_ex}** "
                            f"con los valores actuales: **${precio_final:,.0f} ARS**, stock: **{stock_ml}**."
                        )
                        col_ok, col_back = st.columns(2)
                        with col_ok:
                            if st.button("✅ Confirmar actualización", type="primary", key="ps_ml_confirm_update"):
                                try:
                                    ml_client = st.session_state.ml
                                    ml_client.update_item(
                                        mla_id_ex,
                                        {"price": int(precio_final), "available_quantity": stock_ml},
                                    )
                                    tracker.save(
                                        sku=sku_ref,
                                        mla_id=mla_id_ex,
                                        catalog_product_id=existing_record.get("catalog_product_id", ""),
                                        titulo=existing_record.get("titulo", p['name']),
                                        precio_ars=precio_final,
                                    )
                                    st.success(f"✅ **{mla_id_ex}** actualizado — precio: ${precio_final:,.0f} ARS, stock: {stock_ml}")
                                    st.session_state.pop(_choice_key, None)
                                except Exception as exc:
                                    st.error(f"Error al actualizar en ML: {exc}")
                        with col_back:
                            if st.button("← Volver", key="ps_ml_back_update"):
                                st.session_state.pop(_choice_key, None)
                                st.rerun()

                    else:  # _choice == "force"
                        st.info(f"Publicando de todas formas (MLA existente: {existing_record['mla_id']})")

                if _show_publish_btn:
                    _manual_gtin = (st.session_state.get("ps_ml_gtin_manual") or "").strip()
                    # El manual se manda tal cual lo escribió/dejó el usuario, letras incluidas —
                    # que ML decida si lo acepta, en vez de bloquearlo nosotros de antemano.

                    st.write("**Confirmá antes de publicar:**")
                    col_conf1, col_conf2, col_conf3 = st.columns(3)
                    col_conf1.metric("SKU", sku_ref or "—")
                    col_conf2.metric("Precio", f"${precio_final:,.0f} ARS")
                    if _es_gtin_valido(ean):
                        col_conf3.metric("EAN", ean)
                    elif _manual_gtin:
                        col_conf3.metric("GTIN manual", _manual_gtin)
                    else:
                        col_conf3.metric("EAN", "Sin EAN")
                    st.markdown(
                        '<a href="#ps_ml_top">✏️ ¿Algo mal? Corregir SKU, precio o EAN — volver al Paso 1</a>',
                        unsafe_allow_html=True,
                    )
                    st.write("")

                    if not catalog_product_id:
                        family_name = st.text_input(
                            "Nombre de familia (requerido por ML si no usás catálogo):",
                            value=p['name'],
                            key="ps_ml_family_name",
                        )
                    else:
                        family_name = catalog_family_name

                    if st.session_state.get("ps_ml_gtin_required", False) and not (_es_gtin_valido(ean) or _manual_gtin):
                        st.info("ℹ️ No hay GTIN disponible — se publicará indicando \"El producto no tiene código registrado\" (EMPTY_GTIN_REASON), que ML acepta como alternativa válida.")

                    if st.button("🚀 Crear publicación en ML", type="primary", key="ps_ml_pub"):
                        with st.status("Publicando en Mercado Libre...", expanded=True) as s:
                            try:
                                st.write("• Preparando atributos...")
                                attrs_ml = [
                                    {"id": k, "value_name": v}
                                    for k, v in attr_values.items()
                                    if v.strip()
                                ]
                                peso_kg = st.session_state.get("ps_ml_pkg_weight", 0)
                                pkg_dims = {
                                    "SELLER_PACKAGE_WIDTH": (st.session_state.get("ps_ml_pkg_width", 0), "cm"),
                                    "SELLER_PACKAGE_LENGTH": (st.session_state.get("ps_ml_pkg_length", 0), "cm"),
                                    "SELLER_PACKAGE_HEIGHT": (st.session_state.get("ps_ml_pkg_height", 0), "cm"),
                                    # el input está en kg; ML espera SELLER_PACKAGE_WEIGHT en gramos
                                    "SELLER_PACKAGE_WEIGHT": (peso_kg * 1000 if peso_kg else 0, "g"),
                                }
                                for attr_id, (v, unit) in pkg_dims.items():
                                    if v and v > 0:
                                        if isinstance(v, float) and v.is_integer():
                                            v = int(v)
                                        attrs_ml.append({"id": attr_id, "value_name": f"{v} {unit}"})

                                st.write("• Subiendo imágenes a hosting público...")
                                imgs_publicas = []
                                imgs_fallidas = 0
                                with ThreadPoolExecutor(max_workers=5) as pool:
                                    futuros = {pool.submit(procesar_y_hostear, url): url for url in imgs}
                                    for futuro in as_completed(futuros):
                                        try:
                                            imgs_publicas.append(futuro.result())
                                        except Exception:
                                            imgs_fallidas += 1
                                if imgs_fallidas:
                                    st.warning(f"⚠️ {imgs_fallidas} imagen/es no se pudieron subir a ImgBB y fueron omitidas.")
                                if not imgs_publicas:
                                    s.update(label="❌ Sin imágenes", state="error")
                                    st.error("ML requiere al menos una imagen. No se pudo subir ninguna a ImgBB. Verificá que IMGBB_API_KEY esté configurada.")
                                    st.stop()

                                st.write("• Creando publicación...")
                                _ean = st.session_state.get("ps_ml_ean")
                                if _es_gtin_valido(_ean):
                                    gtin_val = str(_ean)
                                elif _manual_gtin:
                                    # Se manda tal cual lo dejó el usuario (puede tener letras) —
                                    # es ML quien valida el formato real al recibirlo.
                                    gtin_val = _manual_gtin
                                else:
                                    gtin_val = ""
                                item = publisher.crear_item(
                                    title=p['name'],
                                    category_id=cat_id,
                                    price=precio_final,
                                    stock=stock_ml,
                                    images=imgs_publicas,
                                    attributes=attrs_ml,
                                    condition="new",
                                    listing_type_id="gold_special",
                                    family_name=family_name,
                                    catalog_product_id=catalog_product_id,
                                    seller_custom_field=p.get('reference', ''),
                                    gtin=gtin_val,
                                    value_added_tax=st.session_state.get("ps_ml_vat") or "",
                                    import_duty=st.session_state.get("ps_ml_import_duty", ""),
                                )

                                if item:
                                    item_id = item.get("id", "")
                                    permalink = item.get("permalink", "")
                                    st.write(f"• Publicación creada: {item_id}")
                                    try:
                                        ml_client = st.session_state.ml
                                        ml_client.update_item(item_id, {
                                            "sale_terms": [
                                                {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
                                                {"id": "WARRANTY_TIME", "value_name": "6 meses"},
                                                {"id": "INVOICE", "value_name": "Factura A"},
                                            ],
                                            "shipping": {
                                                "mode": "me2",
                                                "local_pick_up": True,
                                                "free_shipping": precio_final >= 32000,
                                            },
                                        })
                                        st.write("• Garantía, factura y retiro confirmados.")
                                    except Exception as e_patch:
                                        st.warning(f"⚠️ Publicado pero no se pudo confirmar garantía/factura/retiro: {e_patch}")

                                    descripcion_ps = p.get('description', '')
                                    if catalog_product_id:
                                        st.info("ℹ️ Publicación en modo catálogo: la descripción la define el catálogo de ML, no se envía la descripción de PrestaShop.")
                                    elif descripcion_ps:
                                        try:
                                            publisher.agregar_descripcion(item_id, descripcion_ps)
                                            st.write("• Descripción cargada.")
                                        except Exception as e_desc:
                                            st.warning(f"⚠️ Publicado pero no se pudo cargar la descripción: {e_desc}")

                                    s.update(label="✅ Publicado en Mercado Libre!", state="complete")
                                    st.success(
                                        f"**ML ID:** {item_id}\n\n"
                                        f"**SKU:** {p.get('reference', '')}\n\n"
                                        f"**Precio:** ${precio_final:,.2f}\n\n"
                                        f"**Stock:** {p['stock']}\n\n"
                                        f"**Categoría:** {selected_cat['category_name']}"
                                    )
                                    st.write(f"[Ver publicación en ML]({permalink})")
                                    st.markdown(
                                        '<a href="#ps_ml_top">⬆️ Volver al inicio — publicar otro producto</a>',
                                        unsafe_allow_html=True,
                                    )
                                    if tracker and sku_ref:
                                        tracker.save(
                                            sku=sku_ref,
                                            mla_id=item_id,
                                            catalog_product_id=catalog_product_id,
                                            titulo=p['name'],
                                            precio_ars=precio_final,
                                        )
                                    st.session_state.pop(_choice_key, None)
                                    del st.session_state["ps_ml_product"]
                                else:
                                    s.update(label="❌ Error al publicar", state="error")
                                    st.error("No se pudo crear la publicación. Verifica los datos.")
                            except Exception as e:
                                s.update(label="❌ Error", state="error")
                                st.error(_mensaje_error_publicacion(str(e)))
