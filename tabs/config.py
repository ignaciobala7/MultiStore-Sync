"""
tabs/config.py — Pestaña "⚙️ Configuración"

Qué hace esta pestaña:
  Panel de control para verificar y diagnosticar el estado de la app.

  Secciones:
  1. VARIABLES DE ENTORNO
     Muestra qué variables del archivo .env están configuradas y cuáles faltan.
     Los valores se muestran enmascarados (solo los últimos 6 caracteres visibles).

  2. PRUEBA DE CONEXIÓN
     Botones individuales para probar la conexión con cada API:
     - Mercado Libre: obtiene el User ID y el total de publicaciones activas
     - Tiendanube: cuenta los SKUs existentes en la tienda
     - Infoandina (PS): verifica que la API responda

  3. TOKENS DE ML
     Muestra si existe el archivo ml_tokens.json (tokens guardados por OAuth).
     Permite recargarlos sin reiniciar la app.

  4. REINICIAR SESIÓN
     Borra todo el estado de la sesión (catálogo cargado, SKUs, etc.).
     Útil si algo se traba o se quiere empezar de cero sin recargar la página.
"""

import os

import streamlit as st


def render():
    """Punto de entrada de la pestaña. Llamado desde app.py."""
    st.header("Configuración y prueba de conexión")

    _render_variables_entorno()
    st.divider()
    _render_prueba_conexion()
    st.divider()
    _render_tokens_ml()
    st.divider()
    _render_reiniciar_sesion()


# ── Variables de entorno ───────────────────────────────────────────────────────

def _render_variables_entorno():
    """
    Muestra el estado de cada variable de entorno necesaria.
    Verde = configurada, Rojo = falta configurar en el .env
    """
    st.subheader("Variables de entorno (.env)")

    # Mapa: nombre de variable → grupo al que pertenece (para mostrar contexto)
    env_vars = {
        "ML_CLIENT_ID":      "Mercado Libre",
        "ML_CLIENT_SECRET":  "Mercado Libre",
        "ML_ACCESS_TOKEN":   "Mercado Libre",
        "ML_REFRESH_TOKEN":  "Mercado Libre",
        "TN_STORE_ID":       "Tiendanube",
        "TN_ACCESS_TOKEN":   "Tiendanube",
        "PS_API_KEY":        "PrestaShop",
        "GEMINI_API_KEY":    "IA (Gemini)",
        "IMGBB_API_KEY":     "Hosting de imágenes",
    }

    col1, col2 = st.columns(2)
    items_list = list(env_vars.items())
    mid = len(items_list) // 2

    for col, chunk in zip([col1, col2], [items_list[:mid], items_list[mid:]]):
        with col:
            for k, grupo in chunk:
                v = os.environ.get(k, "")
                if v:
                    # Mostrar solo los últimos 6 caracteres para no exponer el token completo
                    masked = ("*" * max(0, len(v) - 6)) + v[-6:] if len(v) > 6 else "****"
                    st.success(f"✅ **{k}** ({grupo}): `{masked}`")
                else:
                    st.error(f"❌ **{k}** ({grupo}): no configurado")


# ── Prueba de conexión ─────────────────────────────────────────────────────────

def _render_prueba_conexion():
    """
    Botones para testear cada API de forma independiente.
    Útil para diagnosticar si un token expiró o hay problemas de red.
    """
    st.subheader("Prueba de conexión")

    ml = st.session_state.ml
    tn = st.session_state.tn
    ps = st.session_state.ps

    col_ml, col_tn, col_ps = st.columns(3)

    with col_ml:
        if st.button("🧪 Probar Mercado Libre", use_container_width=True):
            try:
                uid = ml.get_user_id()
                _, total = ml.get_active_item_ids_page(offset=0, limit=1)
                st.success(f"✅ ML OK\nUser ID: {uid}\nPublicaciones activas: {total}")
            except Exception as e:
                st.error(f"❌ ML Error:\n{e}")

    with col_tn:
        if st.button("🧪 Probar Tiendanube", use_container_width=True):
            try:
                skus = tn.get_all_skus()
                st.success(f"✅ TN OK\n{len(skus)} SKUs existentes")
            except Exception as e:
                st.error(f"❌ TN Error:\n{e}")

    with col_ps:
        if st.button("🧪 Probar Infoandina (PS)", use_container_width=True):
            try:
                # Se usa un SKU ficticio; solo se verifica que la API responda sin error de auth
                ps.get_image_urls_by_sku("TEST-CONN-001")
                st.success("✅ PS OK\nConexión establecida")
            except Exception as e:
                st.error(f"❌ PS Error:\n{e}")


# ── Tokens de ML ──────────────────────────────────────────────────────────────

def _render_tokens_ml():
    """
    ML guarda tokens OAuth actualizados en ml_tokens.json después de cada refresh.
    Esta sección permite verificar su estado y recargarlos en memoria sin reiniciar.
    """
    from pathlib import Path
    TOKEN_FILE = Path("ml_tokens.json")

    st.subheader("Tokens de ML")

    if TOKEN_FILE.exists():
        st.info(f"Tokens actualizados guardados en: `{TOKEN_FILE.absolute()}`")
        if st.button("🔄 Recargar tokens desde archivo"):
            import json
            try:
                data = json.loads(TOKEN_FILE.read_text())
                st.session_state.ml.access_token = data["access_token"]
                st.session_state.ml.refresh_token = data["refresh_token"]
                st.success("Tokens recargados correctamente.")
            except Exception as e:
                st.error(f"Error al recargar tokens: {e}")
    else:
        st.warning("No hay `ml_tokens.json`. Se usan los tokens del `.env` (primera ejecución).")


# ── Reiniciar sesión ───────────────────────────────────────────────────────────

def _render_reiniciar_sesion():
    """
    Borra todo el estado de la sesión de Streamlit y recarga la app.
    Útil si el catálogo quedó en estado inconsistente o los clientes se trabaron.
    """
    st.subheader("Reiniciar sesión")
    if st.button("🔁 Reiniciar todos los datos de sesión"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
