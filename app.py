"""
app.py — Punto de entrada de la aplicación.

Qué hace este archivo:
  1. Configura la página de Streamlit (título, ícono, layout)
  2. Inicializa los clientes de API (ML, Tiendanube, PrestaShop) en session_state
     para que estén disponibles en toda la app sin tener que reconectarse en cada render
  3. Crea las 5 pestañas y delega el renderizado a su módulo correspondiente en tabs/

Estructura del proyecto:
  app.py                  ← Este archivo (punto de entrada)
  utils.py                ← Funciones compartidas: publicar, exportar CSV, cargar SKUs
  tabs/
    ml_tiendanube.py      ← Pestaña: Mercado Libre → Tiendanube
    ps_tiendanube.py      ← Pestaña: PrestaShop → Tiendanube
    ps_ml.py              ← Pestaña: PrestaShop → Mercado Libre
    historial.py          ← Pestaña: Historial de CSVs generados
    config.py             ← Pestaña: Configuración y prueba de conexión
  clients/
    mercadolibre.py       ← Cliente API de Mercado Libre (autenticación, items)
    mercadolibre_publish.py ← Publicación en ML: categorías, atributos, comisiones
    tiendanube.py         ← Cliente API de Tiendanube (productos, imágenes, SKUs)
    prestashop.py         ← Cliente API de PrestaShop/Infoandina
  mapper.py               ← Convierte formato ML → formato Tiendanube
  enricher.py             ← Enriquece productos con IA (Gemini): título SEO, descripción
  optimize_images.py      ← Procesa y hostea imágenes en ImgBB antes de subirlas a TN

Para correr la app:
  streamlit run app.py
"""

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from clients.mercadolibre import MercadoLibreClient, MercadoLibreAuthError
from clients.tiendanube import TiendanubeClient
from clients.prestashop import PrestaShopClient

# Importar cada pestaña como módulo independiente
from tabs import ml_tiendanube, ps_tiendanube, ps_ml, historial, config

# ── Constantes ────────────────────────────────────────────────────────────────

# Archivo donde ML guarda los tokens OAuth actualizados después de cada refresh
TOKEN_FILE = Path("ml_tokens.json")


# ── Configuración de página ───────────────────────────────────────────────────

st.set_page_config(
    page_title="Sync ML / PS → Tiendanube",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🛒 Sincronizador → Tiendanube")
st.caption("Club Digital Store — Mercado Libre y PrestaShop/Infoandina → Tiendanube")


# ── Inicialización de sesión ──────────────────────────────────────────────────

def _load_ml_tokens() -> tuple[str, str]:
    """
    Carga los tokens de ML: primero busca en ml_tokens.json (tokens frescos del último
    refresh OAuth), si no existe usa las variables del .env (primer arranque).
    """
    at = os.environ.get("ML_ACCESS_TOKEN", "")
    rt = os.environ.get("ML_REFRESH_TOKEN", "")
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data["access_token"], data["refresh_token"]
        except Exception:
            pass
    return at, rt


def _init_session():
    """
    Inicializa los clientes de API y las variables de estado la primera vez que
    carga la app. Streamlit llama a este archivo en cada interacción del usuario;
    el flag 'ready' evita reinicializar los clientes en cada render.
    """
    if "ready" in st.session_state:
        return

    at, rt = _load_ml_tokens()

    # Cliente de Mercado Libre (maneja autenticación OAuth y refresh de tokens)
    st.session_state.ml = MercadoLibreClient(
        client_id=os.environ.get("ML_CLIENT_ID", ""),
        client_secret=os.environ.get("ML_CLIENT_SECRET", ""),
        access_token=at,
        refresh_token=rt,
    )

    # Cliente de Tiendanube (gestión de productos, variantes e imágenes)
    st.session_state.tn = TiendanubeClient(
        store_id=os.environ.get("TN_STORE_ID", ""),
        access_token=os.environ.get("TN_ACCESS_TOKEN", ""),
        app_name=os.environ.get("TN_APP_NAME", "ML-Tiendanube-Sync"),
        user_email=os.environ.get("TN_USER_EMAIL", "user@example.com"),
    )

    # Cliente de PrestaShop/Infoandina (búsqueda de productos e imágenes por SKU)
    st.session_state.ps = PrestaShopClient(api_key=os.environ.get("PS_API_KEY", ""))

    # Caché de SKUs de TN (se llena la primera vez que se usa la pestaña ML o PS)
    st.session_state.existing_skus: set[str] = set()
    st.session_state.skus_loaded = False

    # Estado del catálogo de ML (lotes cargados y offset de paginación)
    st.session_state.catalog: list[dict] = []
    st.session_state.ml_offset = 0
    st.session_state.ml_total = 0

    st.session_state.ready = True


_init_session()


# ── Pestañas principales ──────────────────────────────────────────────────────
# Cada tab delega toda su lógica a su módulo en tabs/

tab_ml, tab_ps, tab_ps_ml, tab_hist, tab_cfg = st.tabs([
    "📦 ML → Tiendanube",
    "🏪 PS → Tiendanube (Infoandina)",
    "📢 PS → Mercado Libre",
    "📋 Historial",
    "⚙️ Configuración",
])

with tab_ml:
    ml_tiendanube.render()

with tab_ps:
    ps_tiendanube.render()

with tab_ps_ml:
    ps_ml.render()

with tab_hist:
    historial.render()

with tab_cfg:
    config.render()
