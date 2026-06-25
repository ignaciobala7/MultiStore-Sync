"""
enricher.py — Enriquecimiento de productos con IA (Copywriting & SEO)
=====================================================================

Usa Google Gemini Flash (gratuito) para generar fichas optimizadas
listas para Tiendanube a partir de datos crudos de Mercado Libre.

Salida JSON: { marca, tags, seo_titulo, seo_descripcion, descripcion_html }

Si la llamada a la IA falla por cualquier motivo, retorna None y el
proceso de publicación continúa usando los datos originales de ML.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """
Rol: Sos un experto en copywriting para e-commerce de tecnología y especialista en SEO para mercados hispanohablantes (especialmente Argentina, México y España). Tenés más de 10 años de experiencia escribiendo fichas de producto para tiendas online de tecnología y electrónica de consumo.

Tarea principal: Recibís datos crudos y especificaciones técnicas de un producto (extraídos de una publicación de Mercado Libre) y los transformás en una ficha de producto optimizada, lista para ser publicada en una plataforma de e-commerce como Tiendanube.

═══════════════════════════════════════════════════════════
INSTRUCCIONES GENERALES
═══════════════════════════════════════════════════════════

1. Analizá exhaustivamente toda la información técnica proporcionada en el mensaje del usuario.
2. Identificá la marca del producto a partir del título y los atributos.
3. Procesá la información y generá el contenido estructurado siguiendo exactamente las reglas de salida.
4. Tu única respuesta debe ser un objeto JSON válido, sin texto adicional antes o después. Sin bloques de código Markdown (sin ```json```). Solo el JSON puro.
5. Escribí siempre en español, con vocabulario neutro comprensible en Argentina, México y España.
6. Usá un tono profesional, persuasivo y orientado a la conversión. Destacá beneficios antes que características.

═══════════════════════════════════════════════════════════
REGLAS PARA EL CAMPO "marca"
═══════════════════════════════════════════════════════════

- Extraé el nombre oficial de la marca del título o los atributos del producto.
- Usá el nombre tal como lo escribe la propia empresa (ej: "Apple", "Samsung", "HP", "Lenovo", "Xiaomi").
- Si no podés determinar la marca con certeza, usá "Genérico".

═══════════════════════════════════════════════════════════
REGLAS PARA EL CAMPO "tags"
═══════════════════════════════════════════════════════════

- Generá entre 8 y 15 palabras clave relevantes separadas por coma.
- Incluí: nombre de la marca, modelo exacto, categoría del producto, características principales, casos de uso.
- Priorizá términos que los compradores realmente buscan en Google y Mercado Libre.
- Mezclá términos específicos (long-tail) con términos más generales.

═══════════════════════════════════════════════════════════
REGLAS PARA EL CAMPO "seo_titulo"
═══════════════════════════════════════════════════════════

- Máximo 65 caracteres (incluyendo espacios). Este es un límite estricto.
- Formato recomendado: [Marca] [Modelo] [Característica principal diferenciadora]
- Incluí el término de búsqueda más importante al principio.
- No uses signos de puntuación innecesarios ni mayúsculas en cada palabra.

═══════════════════════════════════════════════════════════
REGLAS PARA EL CAMPO "seo_descripcion"
═══════════════════════════════════════════════════════════

- Máximo 160 caracteres (incluyendo espacios). Este es un límite estricto.
- Debe ser un resumen atractivo y accionable que aparecería en los resultados de Google.
- Incluí la propuesta de valor principal y una llamada a la acción implícita.

═══════════════════════════════════════════════════════════
REGLAS PARA EL CAMPO "descripcion_html"
═══════════════════════════════════════════════════════════

Seguí ESTRICTAMENTE estas reglas de estructura y formato HTML:

REGLA 1 — ENCABEZADO PRINCIPAL:
Iniciá siempre con un <h3> que contenga el nombre completo del producto y una frase que resalte su valor principal.

REGLA 2 — INTRODUCCIÓN PERSUASIVA:
Escribí uno o dos párrafos <p> con tono profesional y persuasivo. Explicá para qué tipo de uso o usuario es ideal el producto.

REGLA 3 — ESPECIFICACIONES TÉCNICAS:
Agrupá las especificaciones técnicas lógicamente usando listas <ul> y <li>. Solo usá las categorías que tengan datos reales; no inventes especificaciones.

REGLA 4 — ETIQUETAS <strong>:
Usá <strong> dentro de los párrafos y listas para resaltar componentes clave.

REGLA 5 — ETIQUETAS PROHIBIDAS:
NO usés etiquetas estructurales globales como <html>, <body>, <head> ni <div>. Solo usás: <h3>, <p>, <ul>, <li>, <strong>, <em>, <br>.

REGLA 6 — CIERRE:
Terminá con un párrafo <p> breve que refuerce la propuesta de valor y genere confianza.

═══════════════════════════════════════════════════════════
FORMATO DE SALIDA — JSON ESTRICTO (sin texto extra)
═══════════════════════════════════════════════════════════

{
  "marca": "Nombre oficial de la marca",
  "tags": "Tag1, Tag2, Tag3, Tag4, Tag5, Tag6, Tag7, Tag8",
  "seo_titulo": "Título SEO optimizado (máx. 65 caracteres)",
  "seo_descripcion": "Descripción corta para Google (máx. 160 caracteres)",
  "descripcion_html": "<h3>Título</h3><p>Introducción...</p><ul><li><strong>Spec:</strong> valor</li></ul><p>Cierre.</p>"
}

Recordá: solo el JSON. Sin explicaciones, sin ```json```, sin texto antes ni después.
""".strip()


def enriquecer_producto(item: dict, description: str) -> dict | None:
    """
    Enriquece un producto de ML con copywriting y SEO generado por Gemini.
    Retorna dict con: marca, tags, seo_titulo, seo_descripcion, descripcion_html
    Retorna None si falla (el proceso continúa con datos originales de ML).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return _descripcion_fallback(item, description)

    user_message = _construir_mensaje_usuario(item, description)
    prompt = f"{SYSTEM_PROMPT}\n\n{user_message}"

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for intento in range(2):  # 2 intentos: inmediato + 1 reintento
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            raw_text = (
                result["candidates"][0]["content"]["parts"][0]["text"]
            ).strip()

            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                raw_text = raw_text.rsplit("```", 1)[0].strip()

            enriched = json.loads(raw_text)

            required_keys = {"marca", "tags", "seo_titulo", "seo_descripcion", "descripcion_html"}
            if required_keys - enriched.keys():
                break

            if len(enriched.get("seo_titulo", "")) > 65:
                enriched["seo_titulo"] = enriched["seo_titulo"][:65].rsplit(" ", 1)[0]
            if len(enriched.get("seo_descripcion", "")) > 160:
                enriched["seo_descripcion"] = enriched["seo_descripcion"][:157] + "..."

            return enriched

        except urllib.error.HTTPError as e:
            if e.code == 429 and intento == 0:
                print("    [WARN] Rate limit Gemini. Reintentando en 35s...", end=" ", flush=True)
                time.sleep(35)
                continue
            else:
                print(f"    [WARN] Error Gemini HTTP {e.code}")
                break
        except Exception as e:
            print(f"    [WARN] Gemini no disponible: {type(e).__name__}")
            break

    return _descripcion_fallback(item, description)


def _descripcion_fallback(item: dict, description: str) -> dict:
    """
    Genera una ficha HTML completa y profesional sin IA.
    Se usa cuando Gemini no esta disponible o supera el rate limit.
    """
    titulo = item.get("title", "Producto")
    condicion = "Nuevo" if item.get("condition") == "new" else "Usado"

    # Extraer atributos tecnicos
    atributos = {}
    for attr in item.get("attributes", []):
        nombre = (attr.get("name") or "").strip()
        valor = (attr.get("value_name") or "").strip()
        if nombre and valor:
            atributos[nombre] = valor

    # Extraer marca
    marca = (
        atributos.get("Marca") or atributos.get("Brand") or
        atributos.get("marca") or titulo.split()[0]
    )

    # Descripcion limpia de ML
    desc_limpia = limpiar_descripcion_ml(description)

    # --- Introduccion persuasiva ---
    intro = (
        f"<p>Presenta el <strong>{titulo}</strong>, un producto <strong>{condicion}</strong> "
        f"de la reconocida marca <strong>{marca}</strong>. Disenado para quienes exigen "
        f"lo mejor en tecnologia y rendimiento, combinando calidad superior con una "
        f"propuesta de valor inigualable.</p>"
    )

    # --- Descripcion del vendedor (si existe) ---
    desc_html = ""
    if desc_limpia and len(desc_limpia) > 50:
        parrafos = [p.strip() for p in desc_limpia.split("\n") if p.strip()]
        contenido = ""
        for p in parrafos[:5]:  # max 5 parrafos
            if len(p) > 20:
                contenido += f"<p>{p}</p>"
        if contenido:
            desc_html = contenido

    # --- Especificaciones tecnicas agrupadas ---
    specs_html = ""
    if atributos:
        # Separar en grupos logicos
        specs_principales = []
        specs_secundarias = []
        claves_principales = {
            "Marca", "Modelo", "Tipo", "Color", "Material",
            "Capacidad", "Voltaje", "Potencia", "Frecuencia",
            "Conectividad", "Interfaz", "Resolucion", "Tamano",
            "Peso", "Dimensiones", "Garantia", "Pais de origen"
        }
        for k, v in atributos.items():
            if k in claves_principales:
                specs_principales.append((k, v))
            else:
                specs_secundarias.append((k, v))

        todos = specs_principales + specs_secundarias
        if todos:
            items_li = "".join(
                f"<li><strong>{k}:</strong> {v}</li>"
                for k, v in todos[:20]
            )
            specs_html = (
                f"<h3>Especificaciones tecnicas</h3>"
                f"<ul>{items_li}</ul>"
            )

    # --- Cierre ---
    cierre = (
        f"<p>Compra con total confianza. <strong>Envio a todo el pais</strong> "
        f"con Andreani. Garantia asegurada y atencion personalizada post-venta.</p>"
    )

    descripcion_html = (
        f"<h3>{titulo}</h3>"
        f"{intro}"
        f"{desc_html}"
        f"{specs_html}"
        f"{cierre}"
    )

    # SEO titulo (max 65 chars)
    seo_titulo = titulo if len(titulo) <= 65 else titulo[:65].rsplit(" ", 1)[0]

    # SEO descripcion (max 160 chars)
    seo_desc = f"{marca} {titulo} - {condicion}. Envio a todo el pais con garantia."
    if len(seo_desc) > 160:
        seo_desc = seo_desc[:157] + "..."

    # Tags
    tags_lista = (
        [marca, condicion, "tecnologia", "electronica"] +
        [titulo.split()[i] for i in range(min(4, len(titulo.split())))] +
        list(atributos.values())[:8]
    )
    tags = ", ".join(t for t in dict.fromkeys(tags_lista) if t and len(t) > 2)

    return {
        "marca": marca,
        "tags": tags,
        "seo_titulo": seo_titulo,
        "seo_descripcion": seo_desc,
        "descripcion_html": descripcion_html,
    }


def extraer_atributos(nombre_producto: str, atributos_requeridos: list[dict]) -> dict:
    """
    Dado el nombre de un producto y una lista de atributos requeridos [{id, name}],
    pide a Gemini que extraiga los valores y devuelve {attr_id: valor}.
    Retorna dict vacío si falla o si GEMINI_API_KEY no está configurada.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not atributos_requeridos:
        return {}

    attrs_desc = ", ".join(f"{a['name']} (id: {a['id']})" for a in atributos_requeridos)
    prompt = (
        "Dado el nombre de producto: \"" + nombre_producto + "\"\n"
        "Extraé los valores para estos atributos: " + attrs_desc + "\n"
        "Respondé ÚNICAMENTE con un objeto JSON válido, sin texto adicional, sin markdown, sin backticks. "
        "Usá el id del atributo como clave y el valor extraído como string. "
        "Si no podés determinar un valor con certeza, omití la clave. Ejemplo:\n"
        "{\"BRAND\": \"Casio\", \"MODEL\": \"FX-82\"}"
    )

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        raw_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        extracted = json.loads(raw_text)
        return {k: str(v) for k, v in extracted.items() if v}
    except Exception as e:
        print(f"    [WARN] extraer_atributos Gemini: {type(e).__name__}: {e}")
        return {}


def limpiar_descripcion_ml(description: str) -> str:
    """
    Limpia la descripción cruda de ML eliminando el bloque de texto
    comercial de la tienda (todo lo que viene después de los guiones)
    y devuelve solo las especificaciones técnicas del producto.
    """
    if not description:
        return ""
    # Cortar en el primer bloque de guiones largos (texto de la tienda)
    cortado = re.split(r"-{5,}", description)[0].strip()
    # Cortar en frases típicas del boilerplate de vendedores
    for patron in ["CLUB DIGITAL", "Compartimos tu pasión", "Mercado Líder", "¿CÓMO COMPRAR"]:
        idx = cortado.find(patron)
        if idx > 0:
            cortado = cortado[:idx].strip()
    return cortado if cortado else description.split("·")[0].strip()


def _construir_mensaje_usuario(item: dict, description: str) -> str:
    titulo = item.get("title", "Sin título")
    precio = item.get("price", 0)
    stock = item.get("available_quantity", 0)
    id_ml = item.get("id", "")
    condicion = item.get("condition", "")

    atributos = {}
    for attr in item.get("attributes", []):
        nombre = (attr.get("name") or "").strip()
        valor = (attr.get("value_name") or "").strip()
        if nombre and valor:
            atributos[nombre] = valor

    lineas = [
        "DATOS DEL PRODUCTO PARA ENRIQUECER:",
        "",
        f"Título original en ML: {titulo}",
        f"ID de publicación: {id_ml}",
        f"Precio: ${precio:,.2f}",
        f"Stock disponible: {stock} unidades",
    ]

    if condicion:
        lineas.append(f"Condición: {'Nuevo' if condicion == 'new' else 'Usado'}")

    if atributos:
        lineas.append("")
        lineas.append("Especificaciones técnicas:")
        for k, v in atributos.items():
            lineas.append(f"  - {k}: {v}")

    if description and description.strip():
        desc_truncada = description.strip()[:3000]
        if len(description.strip()) > 3000:
            desc_truncada += "... [descripción truncada]"
        lineas.append("")
        lineas.append("Descripción original del vendedor:")
        lineas.append(desc_truncada)

    lineas.append("")
    lineas.append("Generá la ficha optimizada en JSON siguiendo exactamente el formato indicado.")

    return "\n".join(lineas)
