"""
tabs/ml_tiendanube.py — Pestaña "📦 ML → Tiendanube"

Qué hace esta pestaña:
  Permite sincronizar productos activos de Mercado Libre a Tiendanube.
  Tiene dos sub-modos accesibles por sub-pestañas:

  1. CATÁLOGO POR LOTES
     - Carga hasta 1000 publicaciones activas de ML de a 200 por vez
     - Muestra una tabla editable donde el usuario tilda los productos a publicar
     - Filtra automáticamente los que ya están en TN (por SKU) y los sin stock
     - Publica todos los tildados de una sola vez y genera un CSV con el resultado

  2. SKU ESPECÍFICO
     - El usuario ingresa un SKU de ML y la app lo busca en las publicaciones activas
     - Muestra precio, stock y permite publicarlo directamente en TN

Para ambos modos, el flujo de publicación está en utils.publicar_item_ml().
"""

import time

import pandas as pd
import streamlit as st

from mapper import extract_sku
from utils import cargar_skus_tn, publicar_item_ml, exportar_csv

# Cuántos productos cargar por lote al explorar el catálogo de ML
LOTE = 200


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
    st.header("Mercado Libre → Tiendanube")

    sub_cat, sub_sku = st.tabs(["Catálogo por lotes", "SKU específico"])

    with sub_cat:
        _render_catalogo()

    with sub_sku:
        _render_sku_especifico()


# ── Sub-pestaña: Catálogo por lotes ───────────────────────────────────────────

def _render_catalogo():
    """
    Muestra y gestiona la carga del catálogo de ML en lotes de 200 productos.
    Permite al usuario seleccionar cuáles publicar en TN mediante una tabla interactiva.
    """
    ml = st.session_state.ml
    tn = st.session_state.tn

    # Fila superior: estado de conexión TN + botón para actualizar SKUs
    col_info, col_btn = st.columns([4, 1])
    with col_info:
        if st.session_state.skus_loaded:
            st.success(f"✅ {len(st.session_state.existing_skus)} SKUs ya publicados en Tiendanube.")
        else:
            st.info("Primero cargá los SKUs de TN para saber qué productos ya existen.")
    with col_btn:
        if st.button("🔄 Conectar / Actualizar TN", use_container_width=True):
            cargar_skus_tn(force=True)
            st.rerun()

    st.divider()

    # Controles de carga del catálogo: progreso + botón siguiente lote + limpiar
    col_a, col_b, col_c = st.columns([3, 2, 1])
    with col_a:
        if st.session_state.ml_total > 0:
            st.write(
                f"**Total en ML:** {st.session_state.ml_total} publicaciones "
                f"| **Cargadas:** {len(st.session_state.catalog)}"
            )
        else:
            st.write("Usá el botón para cargar el catálogo de Mercado Libre.")

    with col_b:
        puede_cargar = (
            st.session_state.ml_total == 0
            or st.session_state.ml_offset < min(st.session_state.ml_total, 1000)
        )
        if puede_cargar:
            lote_label = (
                f"📥 Cargar lote ({st.session_state.ml_offset + 1}–"
                f"{min(st.session_state.ml_offset + LOTE, st.session_state.ml_total or LOTE)})"
                if st.session_state.ml_total > 0
                else "📥 Cargar primer lote (200 productos)"
            )
            if st.button(lote_label, use_container_width=True):
                if not st.session_state.skus_loaded:
                    cargar_skus_tn()
                with st.spinner(f"Descargando {LOTE} productos de ML..."):
                    ids, total = ml.get_active_item_ids_page(
                        offset=st.session_state.ml_offset, limit=LOTE
                    )
                    st.session_state.ml_total = total
                    for item_id in ids:
                        try:
                            st.session_state.catalog.append(ml.get_item_details(item_id))
                            time.sleep(0.15)
                        except Exception:
                            pass
                    st.session_state.ml_offset += len(ids)
                st.rerun()
        else:
            st.info("Cargaste todos los productos disponibles (máx. 1000).")

    with col_c:
        if st.session_state.catalog:
            if st.button("🗑️ Limpiar", use_container_width=True):
                st.session_state.catalog = []
                st.session_state.ml_offset = 0
                st.session_state.ml_total = 0
                st.rerun()

    # Tabla interactiva: muestra el catálogo cargado con checkbox "Publicar"
    if st.session_state.catalog:
        _render_tabla_catalogo()


def _render_tabla_catalogo():
    """
    Construye la tabla editable con los productos cargados.
    Marca automáticamente 'Ya en TN' y 'Sin stock' para que el usuario no tenga que revisar.
    El usuario solo tilda los que quiere publicar y presiona el botón.
    """
    existing = st.session_state.existing_skus
    catalog = st.session_state.catalog

    # Armar filas: una por producto del catálogo
    rows = []
    for item in catalog:
        sku = extract_sku(item)
        stock = item.get("available_quantity", 0)
        ya_existe = sku in existing
        if ya_existe:
            estado = "Ya en TN"
        elif stock <= 0:
            estado = "Sin stock"
        else:
            estado = "Disponible"
        rows.append({
            "Publicar": (not ya_existe) and (stock > 0),
            "ID ML": item.get("id", ""),
            "Título": (item.get("title") or "")[:60],
            "SKU": sku,
            "Precio": float(item.get("price", 0)),
            "Stock": int(stock),
            "Estado": estado,
        })

    df = pd.DataFrame(rows)
    st.write(f"**{len(df)} productos cargados** — Marcá los que querés publicar:")

    edited_df = st.data_editor(
        df,
        column_config={
            "Publicar": st.column_config.CheckboxColumn("Publicar", default=False),
            "Precio": st.column_config.NumberColumn("Precio", format="$%.0f"),
            "Stock": st.column_config.NumberColumn("Stock"),
            "Estado": st.column_config.TextColumn("Estado"),
        },
        disabled=["ID ML", "Título", "SKU", "Precio", "Stock", "Estado"],
        hide_index=True,
        use_container_width=True,
        key="catalog_editor",
    )

    # Métricas de resumen
    seleccionados_idx = edited_df.index[edited_df["Publicar"]].tolist()
    n_sel = len(seleccionados_idx)
    disponibles = edited_df[edited_df["Estado"] == "Disponible"].shape[0]

    col_r1, col_r2, col_r3 = st.columns(3)
    col_r1.metric("Seleccionados", n_sel)
    col_r2.metric("Disponibles en lote", disponibles)
    col_r3.metric("Ya en TN (omitidos)", edited_df[edited_df["Estado"] == "Ya en TN"].shape[0])

    # Botón de publicación masiva
    if n_sel > 0:
        if st.button(f"🚀 Publicar {n_sel} producto/s en Tiendanube", type="primary"):
            _ejecutar_publicacion_lote(catalog, seleccionados_idx, n_sel)


def _ejecutar_publicacion_lote(catalog, seleccionados_idx, n_sel):
    """
    Itera sobre los productos seleccionados y los publica uno a uno en TN.
    Muestra progreso en tiempo real y genera un CSV con los resultados al terminar.
    """
    resultados = []
    errores = []
    progress = st.progress(0, text="Preparando...")
    status_text = st.empty()
    log_area = st.empty()

    for i, idx in enumerate(seleccionados_idx):
        item = catalog[idx]
        titulo = (item.get("title") or "")[:50]
        progress.progress((i) / n_sel, text=f"[{i+1}/{n_sel}] {titulo[:40]}...")
        status_text.info(f"Publicando: {titulo}")

        logs: list[str] = []

        def log_fn(msg, _logs=logs):
            _logs.append(f"  • {msg}")
            log_area.code("\n".join(_logs[-6:]))

        try:
            r = publicar_item_ml(item, log_fn)
            st.session_state.existing_skus.add(r["sku"])
            resultados.append(r)
        except Exception as e:
            errores.append({"titulo": titulo, "error": str(e)})

        time.sleep(0.4)

    progress.progress(1.0, text="Completado.")
    log_area.empty()

    if resultados:
        status_text.success(f"✅ {len(resultados)} publicados | {len(errores)} errores")
        csv_path = exportar_csv(resultados)
        st.success(f"CSV guardado: **{csv_path}**")
        st.dataframe(pd.DataFrame(resultados), use_container_width=True)
    else:
        status_text.error("No se pudo publicar ningún producto.")

    if errores:
        with st.expander(f"❌ {len(errores)} errores"):
            for e in errores:
                st.write(f"- **{e['titulo']}**: {e['error']}")


# ── Sub-pestaña: SKU específico ───────────────────────────────────────────────

def _render_sku_especifico():
    """
    Permite buscar y publicar un único producto de ML por su seller_sku.
    Útil para publicar un producto puntual sin cargar todo el catálogo.
    """
    ml = st.session_state.ml

    st.subheader("Publicar un SKU específico de Mercado Libre")

    sku_ml_input = st.text_input(
        "SKU a buscar en ML (seller_sku):",
        placeholder="Ej: ABC-1234",
        key="sku_ml_input",
    )

    if st.button("🔍 Buscar en ML", key="btn_buscar_sku_ml"):
        if not sku_ml_input.strip():
            st.warning("Ingresá un SKU.")
        else:
            cargar_skus_tn()
            if sku_ml_input in st.session_state.existing_skus:
                st.warning(f"El SKU **{sku_ml_input}** ya está publicado en Tiendanube.")
            else:
                with st.spinner("Buscando en Mercado Libre..."):
                    found = ml.search_items_by_sku(sku_ml_input)
                if not found:
                    st.error(f"No se encontró ninguna publicación activa con SKU **{sku_ml_input}**.")
                else:
                    st.session_state["ml_sku_results"] = found
                    st.session_state["ml_sku_query"] = sku_ml_input
                    st.rerun()

    # Mostrar resultados si la búsqueda coincide con lo que hay en sesión
    if (
        "ml_sku_results" in st.session_state
        and st.session_state.get("ml_sku_query") == sku_ml_input
    ):
        found = st.session_state["ml_sku_results"]
        st.success(f"Se encontraron {len(found)} publicación/es:")

        opciones = {
            f"{i+1}. {(it.get('title',''))[:55]}  |  ${it.get('price',0):,.0f}  |  Stock: {it.get('available_quantity',0)}": i
            for i, it in enumerate(found)
        }
        seleccion_label = st.selectbox("Seleccioná la publicación:", list(opciones.keys()))
        elegido = found[opciones[seleccion_label]]

        col1, col2 = st.columns(2)
        col1.metric("Precio", f"${elegido.get('price', 0):,.0f}")
        col2.metric("Stock", elegido.get("available_quantity", 0))

        stock_disp = elegido.get("available_quantity", 0)
        if stock_disp <= 0:
            st.warning("Este producto no tiene stock disponible.")
        else:
            if st.button("🚀 Publicar en Tiendanube", type="primary", key="pub_sku_ml"):
                with st.status("Publicando...", expanded=True) as s:
                    try:
                        def log_sku(msg):
                            st.write(f"• {msg}")

                        r = publicar_item_ml(elegido, log_sku)
                        st.session_state.existing_skus.add(r["sku"])
                        csv_path = exportar_csv([r])
                        s.update(label="✅ Publicado!", state="complete")
                        st.success(
                            f"TN ID: **{r['tn_id']}** | SKU: {r['sku']} | "
                            f"Imgs: {r['imagenes']} [{r['image_source']}]"
                        )
                        st.info(f"CSV: {csv_path}")
                        del st.session_state["ml_sku_results"]
                    except Exception as e:
                        s.update(label="❌ Error al publicar", state="error")
                        st.error(str(e))
