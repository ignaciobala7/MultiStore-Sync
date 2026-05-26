# MultiStore-Sync

Aplicación gráfica para sincronizar y publicar productos entre **Mercado Libre (ML)**, 
**PrestaShop/Infoandina (PS)** y **Tiendanube (TN)** de forma automática e inteligente.

## ✨ Características

### 📦 ML → Tiendanube
- Carga catálogos de 200 productos por lote
- Búsqueda por SKU específico
- Enriquecimiento automático con IA (Google Gemini)
- Optimización y hosting de imágenes en imgbb
- Exporta CSV de auditoría con fuente de imágenes

### 🏪 PS → Tiendanube
- Busca productos en PrestaShop/Infoandina por SKU
- Auto-rellena datos desde PS
- Cálculo automático de comisiones
- Sube imágenes optimizadas (400x500px)
- Publicación con un clic

### 📢 PS → Mercado Libre
- Publica productos de PS directamente en ML
- **Smart Category Matching**: 
  - Auto-sugerencias de categoría basadas en producto
  - Auto-detección automática con 1 clic
  - Dropdown de categorías populares
- Auto-mapeo inteligente de atributos (BRAND, MODEL, COLOR, etc)
- Cálculo automático de comisión de ML
- Validación dinámica de IDs de categorías

### 📋 Historial
- Visualiza todos los CSVs generados
- Descarga directo desde la UI
- Auditoría de fuentes de imágenes

### ⚙️ Configuración
- Panel de variables .env (masked para seguridad)
- Pruebas de conexión a ML, TN y PS
- Recarga manual de tokens

## 🚀 Quick Start

### Requisitos
- Python 3.9+
- Cuenta en Mercado Libre, Tiendanube y PrestaShop/Infoandina
- APIs configuradas en `.env`

### Instalación

```bash
# Clonar repo
git clone https://github.com/ignaciobala7/MultiStore-Sync.git
cd MultiStore-Sync

# Instalar dependencias
pip install -r requirements.txt

# Crear .env con tus credenciales
cp .env.example .env
# Edita .env con tus tokens y claves

# Ejecutar
python -m streamlit run app.py
```

Abre: **http://localhost:8501**

## 📊 Stack Tecnológico

- **Frontend**: Streamlit (UI gráfica interactiva)
- **APIs**: Mercado Libre, Tiendanube, PrestaShop
- **IA**: Google Gemini Flash (enriquecimiento de descripciones)
- **Imágenes**: PIL/Pillow (procesamiento) + imgbb (hosting)
- **Datos**: Pandas (tablas), CSV (auditoría)

## 📁 Estructura

```
.
├── app.py                      # Punto de entrada (Streamlit)
├── tabs/
│   ├── ml_tiendanube.py       # ML → TN
│   ├── ps_tiendanube.py       # PS → TN
│   ├── ps_ml.py               # PS → ML (Smart Matching)
│   ├── historial.py           # Historial de CSVs
│   └── config.py              # Configuración
├── clients/
│   ├── mercadolibre.py        # Cliente ML (OAuth2)
│   ├── mercadolibre_publish.py # Publicación ML + Smart Matching
│   ├── tiendanube.py          # Cliente TN
│   └── prestashop.py          # Cliente PS
├── mapper.py                  # Conversión ML → TN
├── enricher.py                # Enriquecimiento con IA (Gemini)
└── optimize_images.py         # Procesamiento de imágenes
```

## 🎯 Flujos principales

### Caso 1: ML → Tiendanube
1. Carga lotes de ML (200 productos)
2. Selecciona cuáles publicar
3. Genera descripciones con IA
4. Sube imágenes optimizadas
5. Crea productos en TN
6. Exporta CSV de auditoría

### Caso 2: PS → Tiendanube
1. Ingresa SKU de PrestaShop
2. Obtiene datos automáticamente
3. Genera descripción con IA
4. Procesa imágenes
5. Publica en TN

### Caso 3: PS → Mercado Libre ⭐ NUEVO
1. Ingresa SKU de PrestaShop
2. Auto-match de categoría (sugerencias + auto-detect + populares)
3. Auto-mapeo de atributos
4. Cálculo de comisión ML
5. Publica en ML

## 🔑 Variables .env necesarias

```env
# Mercado Libre (OAuth2)
ML_CLIENT_ID=xxxxx
ML_CLIENT_SECRET=xxxxx
ML_ACCESS_TOKEN=xxxxx
ML_REFRESH_TOKEN=xxxxx

# Tiendanube
TN_STORE_ID=xxxxx
TN_ACCESS_TOKEN=xxxxx
TN_APP_NAME=MultiStore-Sync
TN_USER_EMAIL=user@example.com

# PrestaShop / Infoandina
PS_API_KEY=xxxxx

# Google Gemini (IA para descripciones)
GEMINI_API_KEY=xxxxx

# imgbb (hosting de imágenes)
IMGBB_API_KEY=xxxxx
```

## ⚡ Velocidades

- Cargar 200 items de ML: ~30-40s
- Enriquecer con IA (Gemini): ~3-5s por producto
- Procesar imagen (400x500px): ~2-3s
- Publicar 10 productos: ~3-5 minutos

## 🛠️ Troubleshooting

| Error | Solución |
|-------|----------|
| "Faltan variables en .env" | Completa todas las variables listadas arriba |
| "Rate limit Gemini" | La app espera 35s e intenta de nuevo; fallback automático |
| "Imagen borrosa en TN" | Verifica IMGBB_API_KEY; la app redimensiona con UnsharpMask |
| "Categoría incorrecta en ML" | Usa auto-match o busca manualmente; IDs se validan dinámicamente |
| "No se encuentra producto en PS" | Verifica que el SKU sea exacto en PrestaShop |

## 📈 Mejoras recientes

- ✨ Smart Category Matching para PS → ML (auto-detect + sugerencias)
- 🔍 Búsqueda dinámica de categorías ML
- 🎯 Validación automática de IDs de categorías
- 🔄 Fallback inteligente en búsquedas

## 📝 Licencia

Privado - Uso interno Club Digital Store

---

**¡Listo para publicar en múltiples plataformas! 🚀**
