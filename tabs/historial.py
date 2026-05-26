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
