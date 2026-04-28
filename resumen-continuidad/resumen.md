# Resumen de Continuidad – Chatbot DSL Sistemas
Fecha: 2026-04-27

---

## ¿Qué es este proyecto?
Chatbot de soporte técnico para **DSL Sistemas** (empresa de desarrollo de software, San Luis, Argentina).
Opera en **WhatsApp**, **Facebook Messenger** y **Web**.

---

## Estado actual
- El chatbot está desplegado en **Render** (conectado al repo GitHub: `todopce2-sys/chatbot-antigravity`)
- El webhook de WhatsApp en Meta apunta a Render (NO a ngrok local)
- El bot se presenta como agente de soporte técnico de DSL Sistemas
- La base de conocimiento se extrae del sitio web `www.dslsistemas.com.ar`

---

## Lo que se hizo en esta sesión

### Cambio principal
Se convirtió el chatbot de **vendedor de ecommerce** (Distribuciones San Luis + TodoPCE) a **agente de soporte técnico de DSL Sistemas**.

### Archivos modificados
| Archivo | Cambio |
|---|---|
| `fase3_chatbot_api.py` | Eliminado todo el código de WooCommerce, cotización dólar, productos. Agregado scraping de dslsistemas.com.ar como base de conocimiento. Nuevo system prompt de soporte técnico. |
| `templates/chat.html` | Branding actualizado a DSL Sistemas. Eliminados placeholders de productos y cotización. |
| `iniciar_chatbot.bat` | Corregida ruta de Python y ngrok para uso local. |

### Archivos eliminados
- `productos_cache.json`
- `info_cache.json`

### Archivo nuevo generado en runtime
- `dsl_conocimiento.json` — caché del contenido scrapeado de dslsistemas.com.ar (se regenera cada 24h)

---

## Configuración técnica

### Render
- Deploy manual o automático desde GitHub (rama `main`)
- Cuando se hace push a `main`, Render detecta el cambio y redespliega
- Si no actualiza solo, hacer deploy manual desde el dashboard de Render

### Variables de entorno (en Render y en `.env` local)
```
ANTHROPIC_API_KEY=sk-ant-api03-...
META_ACCESS_TOKEN=EAAS4jcYGwDE...
META_PHONE_NUMBER_ID=1045946858607234
WHATSAPP_VERIFY_TOKEN=antigravity2024
PAGE_ACCESS_TOKEN=(vacío por ahora)
```

### Webhook Meta
- URL WhatsApp: `https://[url-render]/whatsapp`
- Token verificación: `antigravity2024`

### Uso local (para pruebas)
```
cd D:\Usuario\Desktop\Proyectos_Antigravity\chatbot-claude
python fase3_chatbot_api.py
```
Abrir: http://localhost:8000

---

## Números de contacto configurados en el bot
- **Soporte humano (derivación):** +54 266 5258519
- **Ventas (NO usar en soporte):** +54 266 458-3129

---

## Próximos pasos pendientes
1. **Agregar PDF de soporte técnico** a la base de conocimiento
   - El usuario va a pasar el PDF
   - Se implementará lectura de PDFs como fuente adicional de conocimiento
   - Opción: carpeta vigilada donde se puedan agregar más PDFs sin tocar código

2. Evaluar si agregar más páginas del sitio web de DSL al scraping (`PAGINAS_DSL` en `fase3_chatbot_api.py`)

---

## Estructura del proyecto
```
chatbot-claude/
├── fase3_chatbot_api.py          ← API principal (FastAPI)
├── templates/
│   └── chat.html                 ← Frontend web
├── resumen-continuidad/
│   └── resumen.md                ← Este archivo
├── dsl_conocimiento.json         ← Caché del sitio web (se genera solo)
├── .env                          ← Credenciales locales
├── iniciar_chatbot.bat           ← Arranque local
├── requirements.txt              ← Dependencias Python
├── fase1_chatbot_web.py          ← Demo básico (no se usa)
└── fase2_chatbot_ecommerce.py    ← Demo ecommerce (no se usa)
```

---

## Cómo actualizar el código en Render
1. Editar los archivos en `D:\Usuario\Desktop\Proyectos_Antigravity\chatbot-claude`
2. Hacer commit y push:
   ```
   git add .
   git commit -m "descripción del cambio"
   git push origin main
   ```
3. Ir a Render → dashboard → hacer deploy manual si no se actualiza solo
