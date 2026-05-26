# 🛒 Sincronizador ML / PS → Tiendanube

Aplicación gráfica Streamlit para publicar productos desde Mercado Libre y/o PrestaShop (Infoandina) a Tiendanube.

## ✨ Características

✅ **Catálogo por lotes** — Carga 200 productos de ML a la vez
✅ **SKU específico** — Busca un SKU particular en ML o PS
✅ **Imágenes optimizadas** — 400x500px, nitidez, contraste, hosted en imgbb
✅ **Enriquecimiento con IA** — Google Gemini genera títulos SEO, descripciones HTML, tags
✅ **Fallback inteligente** — Si IA no está disponible, usa descripción limpia
✅ **Prioridad de imágenes** — PS primero, ML como respaldo
✅ **CSV de auditoría** — Exporta tabla de productos publicados con fuente de imágenes
✅ **Configuración visual** — Panel para test de conexiones y chequear variables .env

## 🚀 Cómo ejecutar

### Opción 1: Doble clic (Windows)
```
run_app.bat
```

### Opción 2: PowerShell / CMD
```powershell
python -m streamlit run app.py
```

Luego abre el navegador en: **http://localhost:8501**

## 📋 Estructura de la app

### Tab 1: 📦 ML → Tiendanube

#### Catálogo por lotes
- **[Conectar / Actualizar TN]** — Carga los SKUs ya publicados en Tiendanube
- **[Cargar lote]** — Descarga 200 productos de ML
- **Tabla editable** — Marca los que querés publicar
- **[Publicar N productos]** — Publica todos los seleccionados, genera CSV

**Flujo:**
1. Conecta a TN (carga SKUs existentes)
2. Carga lotes de 200 productos de ML
3. Selecciona cuáles publicar (los que tengan stock > 0 y no estén en TN)
4. Publica → la app genera descripción con IA y sube imágenes
5. Descarga CSV `skus_publicados_YYYYMMDD_HHMMSS.csv`

#### SKU específico
- Ingresa un `seller_sku` de ML
- La app lo busca
- Lo muestra con precio y stock
- Publica en 1 clic

### Tab 2: 🏪 PS → Tiendanube (Infoandina)

Para publicar productos que están en PrestaShop pero no en ML:

1. Ingresa el SKU / Referencia de Infoandina
2. La app lo busca en PS
3. Muestra datos: precio, stock, imágenes disponibles
4. Genera descripción con IA
5. Sube imágenes desde PS a imgbb
6. Publica en Tiendanube
7. Exporta CSV

### Tab 3: 📋 Historial

- Lista todos los CSVs generados (`skus_publicados_*.csv`)
- Abre un CSV y muestra la tabla
- Descarga directo desde la UI
- Cuenta cuántas imágenes vinieron de PS vs ML

### Tab 4: ⚙️ Configuración

**Variables .env:**
- Muestra qué variables están configuradas y cuáles faltan
- Mascaradas para seguridad (solo últimos 6 caracteres visibles)

**Prueba de conexión:**
- 🧪 Probar Mercado Libre — Obtiene User ID y total de publicaciones activas
- 🧪 Probar Tiendanube — Cuenta SKUs existentes
- 🧪 Probar Infoandina — Verifica conectividad a PS

**Tokens ML:**
- Si existe `ml_tokens.json`, muestra dónde se guardó
- Botón para recargar tokens si fueron actualizados

## 📝 Variables necesarias en `.env`

```env
# Mercado Libre (OAuth2)
ML_CLIENT_ID=xxxxx
ML_CLIENT_SECRET=xxxxx
ML_ACCESS_TOKEN=xxxxx
ML_REFRESH_TOKEN=xxxxx

# Tiendanube (token estático)
TN_STORE_ID=xxxxx
TN_ACCESS_TOKEN=xxxxx
TN_APP_NAME=ML-Tiendanube-Sync
TN_USER_EMAIL=user@example.com

# PrestaShop / Infoandina
PS_API_KEY=xxxxx

# Google Gemini (para IA)
GEMINI_API_KEY=xxxxx

# imgbb (para hosting de imágenes)
IMGBB_API_KEY=xxxxx
```

## 🎯 Flujo completo (ejemplo)

1. Abre `run_app.bat` → se abre la app en localhost:8501
2. Tab **"ML → Tiendanube"** → **"Catálogo por lotes"**
3. Clica **"[Conectar / Actualizar TN]"** → carga SKUs de TN
4. Clica **"[Cargar lote]"** → descarga 200 productos de ML
5. Marca los que querés con el checkbox "Publicar"
6. Clica **"[🚀 Publicar N productos]"**
7. Espera mientras:
   - Genera descripciones con IA (Gemini)
   - Crea productos en TN
   - Busca imágenes en PS (si existen)
   - Si no hay en PS, usa de ML
   - Procesa imágenes (400x500, nitidez, contraste)
   - Sube a imgbb.com
   - Asocia URL a TN
8. **CSV exportado** → `skus_publicados_20260521_092105.csv`

## 🖼️ Procesamiento de imágenes

Cada imagen pasa por:
1. **Descarga** desde ML o PS
2. **Procesamiento PIL:**
   - Convierte a RGB con fondo blanco
   - Aplica UnsharpMask (nitidez +120%)
   - Aumenta contraste 1.1x
   - Redimensiona a 400x500 (mantiene proporción)
   - Centra en canvas blanco
   - Comprime JPEG quality=90
3. **Hosting** en imgbb.com (URL pública permanente)
4. **Asociación** a TN via API

## 💡 Enriquecimiento con IA (Gemini)

Si está disponible `GEMINI_API_KEY`:

**Input:** Datos crudos de ML/PS
**Output JSON:**
```json
{
  "marca": "Samsung",
  "tags": "Samsung, Galaxy, Smartphone, Android, 5G",
  "seo_titulo": "Samsung Galaxy A15 5G - 128GB",
  "seo_descripcion": "Smartphone 5G Samsung Galaxy A15 de última generación",
  "descripcion_html": "<h3>Samsung Galaxy A15...</h3><p>...</p><ul>..."
}
```

**Fallback inteligente:**
- Si IA falla (rate limit, timeout), se usa `_descripcion_fallback()`
- Genera HTML profesional automáticamente desde datos del producto

## 📊 CSV de auditoría

Cada sesión exporta un CSV con:

| tn_id | sku | titulo | mla_id | image_source |
|-------|-----|--------|--------|--------------|
| 12345 | ABC-1234 | Samsung Galaxy A15 | MLxxxxx | PS |
| 12346 | ABC-5678 | iPhone 15 | MLyyyyy | ML |

- **image_source** = "PS" o "ML" (origen de las imágenes)
- Útil para auditoría y saber qué versión de imagen se subió

## ⚡ Velocidades

- **Cargar 200 items de ML:** ~30-40 segundos
- **Enriquecer con IA (Gemini):** ~3-5 segundos por producto
- **Procesar imagen (400x500):** ~2-3 segundos
- **Subir imagen a Tiendanube:** ~1 segundo
- **Publicar 10 productos (ML→TN):** ~3-5 minutos

## 🔧 Troubleshooting

### "Faltan variables en el .env"
→ Completa el `.env` con todas las variables listadas arriba.

### "Rate limit Gemini"
→ La app automáticamente espera 35s e intenta de nuevo.
→ Si sigue fallando, usa fallback (descripción limpia generada automáticamente).

### "Imagen borrosa en Tiendanube"
→ Asegúrate que `IMGBB_API_KEY` está correcta.
→ La app redimensiona a 400x500 con UnsharpMask y contraste.

### "Productos no se publican en Tiendanube"
→ Verifica en Tab "Configuración" que **TN → STORE_ID** y **TN_ACCESS_TOKEN** sean correctos.
→ Prueba la conexión con 🧪 **"Probar Tiendanube"**.

### "Imágenes de PrestaShop no se usan"
→ Asegúrate que el SKU en PS coincide exactamente con el de ML.
→ La app busca por `seller_sku` en ML y por `reference` en PS.
→ Si PS_API_KEY no está correcto, fallback a ML automáticamente.

## 📧 Soporte

- **Mercado Libre API:** https://developers.mercadolibre.com.ar
- **Tiendanube API:** https://tiendanube.dev
- **Google Gemini:** https://ai.google.dev
- **imgbb API:** https://api.imgbb.com

---

**Buena sincronización! 🚀**
