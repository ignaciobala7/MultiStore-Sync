"""
tabs/historial.py — Pestaña "📋 Historial"

Qué hace esta pestaña:
  Muestra los archivos CSV generados por publicaciones anteriores.
  Cada vez que se publican productos (desde cualquier pestaña), la app crea
  un archivo "skus_publicados_YYYYMMDD_HHMMSS.csv" en el directorio raíz.

  Esta pestaña permite:
  - Ver la lista de todos esos archivos ordenados por fecha (más reciente primero)
  - Seleccionar uno y ver su contenido en tabla
  - Ver métricas: total publicados, imágenes de PS vs ML
  - Descargar el CSV seleccionado
"""

from pathlib import Path

import pandas as pd
import streamlit as st


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
    st.header("Historial de publicaciones")

    if st.button("🔄 Actualizar lista"):
        st.rerun()

    _render_historial_ml_tn()
    _render_publicaciones_ps_ml()


def _render_historial_ml_tn():
    """Muestra los CSV de sesión generados por el flujo ML → Tiendanube (exportar_csv)."""
    # Buscar todos los CSVs de historial en el directorio raíz del proyecto
    csvs = sorted(Path(".").glob("skus_publicados_*.csv"), reverse=True)

    if not csvs:
        st.info(
            "No hay archivos de historial todavía. "
            "Se generan automáticamente cada vez que publicás productos."
        )
        return

    st.write(f"**{len(csvs)} archivo/s encontrado/s:**")
    csv_names = [p.name for p in csvs]
    sel = st.selectbox("Seleccioná un archivo:", csv_names)

    if not sel:
        return

    try:
        df = pd.read_csv(sel)

        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**{len(df)} productos publicados** en esta sesión:")
            st.dataframe(df, use_container_width=True)
        with col2:
            st.metric("Total publicados", len(df))

            # Contar imágenes por fuente si la columna existe
            ps_count = (
                len(df[df["image_source"] == "PS"])
                if "image_source" in df.columns else 0
            )
            ml_count = (
                len(df[df["image_source"] == "ML"])
                if "image_source" in df.columns else 0
            )
            st.metric("Imágenes de PS", ps_count)
            st.metric("Imágenes de ML", ml_count)

            with open(sel, "rb") as f:
                st.download_button(
                    "⬇️ Descargar CSV",
                    f,
                    file_name=sel,
                    mime="text/csv",
                    use_container_width=True,
                )
    except Exception as e:
        st.error(f"No se pudo leer el archivo: {e}")


SKU_MLA_CSV = Path("sku_mla_map.csv")


def _render_publicaciones_ps_ml():
    """
    Muestra el historial de publicaciones creadas desde PS → ML (SKUTracker),
    con el SKU y el MLA con el que se publicó cada una.
    """
    st.divider()
    st.subheader("Publicaciones PS → Mercado Libre (SKU ↔ MLA)")

    if not SKU_MLA_CSV.exists():
        st.info("Todavía no se publicó nada desde la pestaña PS → ML.")
        return

    try:
        df_mla = pd.read_csv(SKU_MLA_CSV, dtype={"sku": str, "mla_id": str, "catalog_product_id": str})
    except Exception as e:
        st.error(f"No se pudo leer sku_mla_map.csv: {e}")
        return

    if df_mla.empty:
        st.info("Todavía no se publicó nada desde la pestaña PS → ML.")
        return

    df_mla["precio_ars"] = pd.to_numeric(df_mla["precio_ars"], errors="coerce")
    df_mla = df_mla.sort_values("fecha_publicacion", ascending=False)
    df_mla["link_ml"] = df_mla["mla_id"].apply(
        lambda mla: f"https://articulo.mercadolibre.com.ar/{mla[:3]}-{mla[3:]}" if mla and len(mla) > 3 else None
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        st.dataframe(
            df_mla[["sku", "mla_id", "titulo", "precio_ars", "estado", "fecha_publicacion", "link_ml"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "sku": "SKU",
                "mla_id": "MLA",
                "titulo": "Título",
                "precio_ars": st.column_config.NumberColumn("Precio ARS", format="$%.0f"),
                "estado": "Estado",
                "fecha_publicacion": "Publicado",
                "link_ml": st.column_config.LinkColumn("Ver en ML", display_text="Abrir"),
            },
        )
    with col2:
        st.metric("Total publicados", len(df_mla))
        activos = int((df_mla["estado"] == "active").sum())
        st.metric("Activos", activos)
        with open(SKU_MLA_CSV, "rb") as f:
            st.download_button(
                "⬇️ Descargar CSV",
                f,
                file_name=SKU_MLA_CSV.name,
                mime="text/csv",
                use_container_width=True,
                key="download_sku_mla_map",
            )
