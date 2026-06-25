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

import streamlit as st

from clients.sku_tracker import SKUTracker
from clients.mercadolibre_publish import (
    MercadoLibrePublisher,
    sugerir_terminos_busqueda,
    auto_match_categoria,
    get_popular_categories_with_correct_ids,
)
from clients.prestashop import obtener_cotizacion_dolar
from optimize_images import procesar_y_hostear


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
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
                # Resetear tipo de cambio para que se inicialice desde Flexxus
                st.session_state.pop("ps_ml_tc", None)
                # Limpiar elecciones y checks del tracker de búsquedas anteriores
                for k in list(st.session_state.keys()):
                    if k.startswith("ps_ml_choice_") or k.startswith("ps_ml_ml_checked_"):
                        del st.session_state[k]
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
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Referencia (SKU)", p["reference"])
    col2.metric("Precio PS", f"USD {p['price']:,.2f}")
    if tc_resumen:
        col3.metric("Precio PS en ARS", f"${p['price'] * tc_resumen:,.0f}")
        col3.caption(f"TC: {tc_resumen:,.0f}")
    else:
        col3.metric("Precio PS en ARS", "—")
    col4.metric("Stock disponible", p["stock"])
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

            if st.button("🔎 Buscar en catálogo ML", key="ps_ml_buscar_catalogo"):
                with st.spinner("Buscando en catálogo..."):
                    resultados = publisher.buscar_en_catalogo(p['name'], limit=6)
                st.session_state["ps_ml_catalogo"] = resultados

            catalogo = st.session_state.get("ps_ml_catalogo")
            catalog_product_id = ""
            catalog_family_name = ""

            if catalogo is not None:
                if not catalogo:
                    st.info("No se encontró el producto en el catálogo. Se publicará con título manual.")
                else:
                    opciones = {"— Ninguno (usar título manual) —": ("", "")}
                    opciones.update({r["name"]: (r["id"], r["name"]) for r in catalogo})
                    sel = st.selectbox("Seleccioná el producto del catálogo:", list(opciones.keys()), key="ps_ml_catalogo_sel")
                    catalog_product_id, catalog_family_name = opciones[sel]
                    if catalog_product_id:
                        st.success(f"Catálogo: **{catalog_family_name}** ({catalog_product_id})")

            # ── Ajuste automático de categoría desde catálogo ─────────────────
            if catalog_product_id:
                last_cpid = st.session_state.get("ps_ml_last_catalog_product_id", "")
                if catalog_product_id != last_cpid:
                    with st.spinner("Verificando categoría del producto en catálogo..."):
                        cat_from_catalog = publisher.obtener_categoria_de_catalogo(
                            catalog_product_id, product_name=catalog_family_name
                        )
                    st.session_state["ps_ml_last_catalog_product_id"] = catalog_product_id
                    st.session_state["ps_ml_catalog_category_id"] = cat_from_catalog
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
            else:
                st.session_state.pop("ps_ml_last_catalog_product_id", None)
                st.session_state.pop("ps_ml_catalog_category_id", None)

            # ── Paso 4: Atributos de la categoría ────────────────────────────────
            st.divider()
            st.subheader("Paso 4: Completa los atributos requeridos")

            with st.spinner("Obteniendo atributos de ML..."):
                ml_attrs = publisher.obtener_atributos(cat_id)

            if not ml_attrs:
                st.warning("No se pudieron obtener atributos. Intenta con otra categoría.")
            else:
                required_attrs = [a for a in ml_attrs if a.get("tags", {}).get("required", False)]
                st.write(f"**{len(required_attrs)} atributo/s requerido/s:**")

                attr_values = {}
                for attr in required_attrs:
                    attr_id = attr.get("id", "")
                    attr_name = attr.get("name", attr_id)
                    # Intentar pre-rellenar desde los datos del producto de PS
                    auto_valor = None
                    for k, v in p.items():
                        if isinstance(v, str) and (attr_name.lower() in k.lower() or k.lower() in attr_name.lower()):
                            auto_valor = v
                            break
                    attr_values[attr_id] = st.text_input(
                        f"**{attr_name}** ({attr_id}):",
                        value=auto_valor or "",
                        key=f"attr_{cat_id}_{attr_id}",
                    )

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

                precio_final = flexxus_price["pesos"] if flexxus_price else precio_ars
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

                if not catalog_product_id:
                    family_name = st.text_input(
                        "Nombre de familia (requerido por ML si no usás catálogo):",
                        value=p['name'],
                        key="ps_ml_family_name",
                    )
                else:
                    family_name = catalog_family_name

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
                    if st.button("🚀 Crear publicación en ML", type="primary", key="ps_ml_pub"):
                        with st.status("Publicando en Mercado Libre...", expanded=True) as s:
                            try:
                                st.write("• Preparando atributos...")
                                attrs_ml = [
                                    {"id": k, "value_name": v}
                                    for k, v in attr_values.items()
                                    if v.strip()
                                ]

                                st.write("• Subiendo imágenes a hosting público...")
                                imgs_publicas = []
                                imgs_fallidas = 0
                                for url in imgs:
                                    try:
                                        imgs_publicas.append(procesar_y_hostear(url))
                                    except Exception:
                                        imgs_fallidas += 1
                                if imgs_fallidas:
                                    st.warning(f"⚠️ {imgs_fallidas} imagen/es no se pudieron subir a ImgBB y fueron omitidas.")
                                if not imgs_publicas:
                                    s.update(label="❌ Sin imágenes", state="error")
                                    st.error("ML requiere al menos una imagen. No se pudo subir ninguna a ImgBB. Verificá que IMGBB_API_KEY esté configurada.")
                                    st.stop()

                                st.write("• Creando publicación...")
                                item = publisher.crear_item(
                                    title=p['name'],
                                    category_id=cat_id,
                                    price=precio_final,
                                    stock=stock_ml,
                                    description=p.get('description', ''),
                                    images=imgs_publicas,
                                    attributes=attrs_ml,
                                    condition="new",
                                    listing_type_id="gold_special",
                                    family_name=family_name,
                                    catalog_product_id=catalog_product_id,
                                    seller_custom_field=p.get('reference', ''),
                                )

                                if item:
                                    item_id = item.get("id", "")
                                    permalink = item.get("permalink", "")
                                    st.write(f"• Publicación creada: {item_id}")
                                    s.update(label="✅ Publicado en Mercado Libre!", state="complete")
                                    st.success(
                                        f"**ML ID:** {item_id}\n\n"
                                        f"**Precio:** ${precio_final:,.2f}\n\n"
                                        f"**Stock:** {p['stock']}\n\n"
                                        f"**Categoría:** {selected_cat['category_name']}"
                                    )
                                    st.write(f"[Ver publicación en ML]({permalink})")
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
                                st.error(f"Error: {e}")
