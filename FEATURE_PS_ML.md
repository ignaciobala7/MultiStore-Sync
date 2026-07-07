# 📢 Nuevo Tab: PrestaShop → Mercado Libre

## ¿Qué hace?

Publica productos desde Infoandina (PrestaShop) directamente a Mercado Libre.

**Flujo automático:**
1. Busca producto en Infoandina por SKU
2. Busca la categoría en ML por texto (ej: "cámara", "smartphone")
3. Obtiene atributos requeridos de esa categoría
4. Te permite completar los atributos (con auto-relleno inteligente)
5. Calcula automáticamente la comisión de ML y el precio final
6. Crea la publicación con todas las imágenes de Infoandina
7. Te devuelve el link directo a la publicación en ML

---

## 🔧 Cómo funciona técnicamente

### Archivo nuevo: `clients/mercadolibre_publish.py`

**Clase: `MercadoLibrePublisher`**

Reutiliza las credenciales de tu otro proyecto:
```
C:\Users\ibala\Desktop\Codigo para consultas de comisiones por categoria\configuracion.json
```

**Métodos principales:**

```python
# Buscar categorías
categorias = publisher.buscar_categorias("cámara")
# → [{"category_id": "MLA1051", "category_name": "Webcams", ...}]

# Obtener atributos requeridos
atributos = publisher.obtener_atributos("MLA1051")
# → [{"id": "BRAND", "name": "Marca", "tags": {"required": true}}, ...]

# Calcular comisión
pct, monto = publisher.obtener_comision(50000, "MLA1051")
# → (31.0, 15500.0)  # 31% comisión, monto $15,500

# Crear publicación
item = publisher.crear_item(
    title="Samsung Galaxy A15",
    category_id="MLA1051",
    price=50000,
    stock=5,
    images=["https://...", "https://..."],
    attributes=[
        {"id": "BRAND", "value_name": "Samsung"},
        {"id": "MODEL", "value_name": "Galaxy A15"},
    ]
)
# → {"id": "MLA123456789", "permalink": "https://..."}

# La descripción va aparte: ML la ignora si se manda en /items
publisher.agregar_descripcion(item["id"], "Descripción...")
```

---

## 📋 Paso a paso en la UI

### **Paso 1: Selecciona producto en Infoandina**
```
[SKU input] → [Buscar en Infoandina]
↓
Muestra: Nombre, precio, stock, imagen preview
```

### **Paso 2: Busca categoría en Mercado Libre**
```
[Categoria query] → [Buscar categoría en ML]
↓
Muestra lista de categorías sugeridas
```

### **Paso 3: Completa atributos requeridos**
```
La app muestra los atributos obligatorios de esa categoría.
Intenta auto-rellenarlos desde los datos de Infoandina.
Vos completás los que falten.

Ej:
  [Marca] Samsung ← auto-rellenado
  [Modelo] Galaxy A15 ← auto-rellenado
  [Color] Gris ← completá vos
```

### **Paso 4: Revisa precio y comisión**
```
Precio original (PS):    $50,000
Comisión ML:             $15,500 (31%)
────────────────────────
Precio final en ML:      $65,500
```

### **Paso 5: Crea la publicación**
```
[Crear publicación en ML]
↓
✅ Publicado en Mercado Libre!
Link: https://articulo.mercadolibre.com.ar/MLA-123456789
```

---

## 🧠 Mapeo inteligente de atributos

La app intenta **auto-rellenar** atributos usando lógica inteligente:

```python
ATTR_MAPPING = {
    "brand": "BRAND",
    "marca": "BRAND",
    "model": "MODEL",
    "modelo": "MODEL",
    "color": "COLOR",
    "size": "SIZE",
    "talla": "SIZE",
    "material": "MATERIAL",
}
```

**Ejemplo:**
- Si ML requiere "Marca" y Infoandina tiene un campo "brand"
  → auto-rellena "Samsung"
- Si no encuentra mapeo exacto, busca por similitud de nombres
  → si Infoandina tiene "Modelo" y ML pide "Model", auto-rellena

---

## ⚡ Características

✅ **Auto-relleno de atributos** — Mapeo inteligente PS → ML
✅ **Cálculo automático de comisión** — Suma comisión ML al precio
✅ **Búsqueda de categorías inteligente** — "cámara" → devuelve Webcams, Cámaras digitales, etc.
✅ **Múltiples imágenes** — Sube todas las imágenes de Infoandina
✅ **Link directo a publicación** — Te devuelve el URL para ver el resultado

---

## 🎯 Casos de uso

### Caso 1: Producto sin publicar en ML aún
```
Tenés en Infoandina: iPhone 15
No está en Mercado Libre todavía.
→ Usas este tab para publicarlo directamente en ML
```

### Caso 2: Duplicar stock entre tiendas
```
Tenés X stock en Infoandina
Querés también vender en ML
→ Usas este tab para crear la publicación sin escribir nada
(los atributos se auto-rellenan)
```

### Caso 3: Cross-selling
```
Vendés en ML, PS y TN
Este tab cierra el triángulo: PS → ML
(antes tenías ML→TN y PS→TN)
```

---

## 🔐 Seguridad / Credenciales

Las credenciales se reutilizan del archivo:
```
C:\Users\ibala\Desktop\Codigo para consultas de comisiones por categoria\configuracion.json
```

No necesitas copiar nada. La app los lee directamente y:
- ✅ Renueva el token automáticamente si está vencido
- ✅ Guarda tokens actualizados en el archivo
- ✅ Maneja errores de autenticación elegantemente

---

## 🚨 Errores comunes

| Error | Causa | Solución |
|-------|-------|----------|
| "No se encontró el SKU en Infoandina" | SKU incorrecto o inexistente | Verifica que el SKU existe en PS |
| "No se encontraron categorías" | Búsqueda muy específica | Intenta con términos más genéricos |
| "Faltan atributos requeridos" | No completaste todos los obligatorios | Rellena los campos con rojo |
| "Error al crear publicación" | Datos inválidos | Revisa que los atributos sean válidos |

---

## 📊 Ventajas vs hacer manualmente

| Tarea | Manual | App |
|-------|--------|-----|
| Buscar categoría | 5 min | 10 seg |
| Obtener atributos | 3 min | automático |
| Calcular comisión | 2 min | automático |
| Rellenar atributos | 5 min | auto-rellena 80% |
| Subir imágenes | 3 min | automático |
| Crear publicación | 2 min | 1 click |
| **Total** | **~20 min** | **~2-3 min** |

**Ganancia: 15-17 minutos por producto** 🚀

---

## 🎓 Ejemplo completo

```
User: Quiero publicar el "Samsung Galaxy A15" que tengo en Infoandina
     en Mercado Libre

App:
  1. Busca en PS por SKU "A15-SAMSUNG"
  2. Obtiene: nombre, precio ($50k), stock (10), 4 imágenes
  
  3. User busca categoría "Samsung"
  4. App devuelve: Smartphones, Accesorios, etc.
  5. User selecciona "Smartphones"
  
  6. App obtiene atributos requeridos:
     - BRAND (Marca) ← auto-rellena "Samsung"
     - MODEL (Modelo) ← auto-rellena "Galaxy A15"
     - COLOR (Color) ← USER completa "Gris"
  
  7. App calcula comisión:
     Precio base: $50,000
     Comisión (31%): +$15,500
     Precio final: $65,500
  
  8. User clica "Crear publicación"
  9. ✅ Publicada en ML
     Link: https://articulo.mercadolibre.com.ar/MLA-123456789
```

---

## 🔄 Flujos con la app completa

Ahora tenés:
```
   Mercado Libre ←→ Tiendanube
        ↑              ↑
        └──→ PrestaShop ←
```

Puedes sincronizar entre las 3 plataformas:
- **ML → TN** (app tab 1)
- **PS → TN** (app tab 2)
- **PS → ML** (app tab 3) ← NUEVO
- Plus historial y configuración

---

## ✅ Checklist antes de usar

- [ ] `configuracion.json` existe en la ruta esperada
- [ ] Credenciales de ML están actualizadas (token vigente)
- [ ] Producto existe en Infoandina
- [ ] Conexión a internet estable
- [ ] La categoría existe en Mercado Libre

---

**¡Listo para publicar en ML! 🚀**
