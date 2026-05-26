"""
optimize_images.py -- Procesamiento y hosting de imagenes para Tiendanube
=========================================================================

Flujo:
  1. Descarga la imagen desde ML o PS
  2. La optimiza: nitidez, contraste, resize a 400x500 con fondo blanco
  3. La sube a imgbb.com (hosting confiable con API gratuita)
  4. Retorna la URL publica lista para add_image() en Tiendanube
"""

import base64
import io
import os
import requests
from PIL import Image, ImageFilter, ImageEnhance

TARGET_W = 400
TARGET_H = 500
BG_COLOR = (255, 255, 255)


def _descargar_imagen(url: str) -> bytes:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.content


def _procesar_imagen(raw_bytes: bytes) -> bytes:
    """
    Procesa la imagen:
    - Convierte a RGB con fondo blanco
    - Mejora nitidez y contraste
    - Redimensiona a 400x500 centrado con fondo blanco
    """
    img = Image.open(io.BytesIO(raw_bytes))

    # Convertir a RGB
    if img.mode in ("RGBA", "LA", "P"):
        fondo = Image.new("RGB", img.size, BG_COLOR)
        if img.mode == "RGBA":
            fondo.paste(img, mask=img.split()[3])
        else:
            fondo.paste(img)
        img = fondo
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Mejorar nitidez
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=3))

    # Mejorar contraste levemente
    img = ImageEnhance.Contrast(img).enhance(1.1)

    # Redimensionar manteniendo proporcion
    img.thumbnail((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)

    # Canvas blanco 400x500 centrado
    canvas = Image.new("RGB", (TARGET_W, TARGET_H), BG_COLOR)
    x = (TARGET_W - img.width) // 2
    y = (TARGET_H - img.height) // 2
    canvas.paste(img, (x, y))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=90, optimize=True)
    return out.getvalue()


def _subir_imgbb(image_bytes: bytes) -> str:
    """
    Sube imagen a imgbb.com y retorna URL publica permanente.
    Requiere que IMGBB_API_KEY esté configurado en el .env
    """
    api_key = os.environ.get("IMGBB_API_KEY")
    if not api_key:
        raise ValueError("IMGBB_API_KEY no está configurado en el .env")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": b64},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise Exception(f"imgbb error: {data}")
    return data["data"]["url"]


def procesar_y_hostear(url_original: str) -> str:
    """
    Pipeline completo: descarga -> mejora calidad 400x500 -> sube a imgbb -> URL publica.
    """
    raw = _descargar_imagen(url_original)
    procesada = _procesar_imagen(raw)
    return _subir_imgbb(procesada)
