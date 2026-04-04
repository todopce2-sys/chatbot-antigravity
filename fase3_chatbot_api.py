import os
import re
import json
import uuid
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

def sanitize_str(text: str) -> str:
    """Elimina surrogates Unicode inválidos que rompen la serialización JSON."""
    return re.sub(r'[\ud800-\udfff]', '', text)


BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Chatbot Distribuciones San Luis")
CHAT_HTML = BASE_DIR / "templates" / "chat.html"

client = Anthropic()
META_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
META_PHONE_ID = os.environ.get("META_PHONE_NUMBER_ID", "1045946858607234")
META_API_URL = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
BASE_URL = "https://distribucionessl.com/wp-json/wc/store/v1/products"
CACHE_FILE = BASE_DIR / "productos_cache.json"
INFO_CACHE_FILE = BASE_DIR / "info_cache.json"

# Páginas a scrapear para información institucional
# Configurable: cambiar estas URLs para usar el chatbot en otro sitio
PAGINAS_INFO = {
    "contacto":    "https://distribucionessl.com/our-contacts/",
    "envios":      "https://distribucionessl.com/delivery-return-2/",
    "locales":     "https://distribucionessl.com/stores/",
}

# Estado global
estado = {
    "productos": [],
    "cotizacion": 1404.0,
    "info": {},
}

# Historial por sesion { session_id: [mensajes] }
sesiones: dict[str, list] = {}


# ---------------------------------------------------------------------------
# Scraping de páginas institucionales
# ---------------------------------------------------------------------------

def scrape_pagina(url: str, max_chars: int = 3000) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        texto = soup.get_text(separator="\n", strip=True)
        # Limpiar líneas vacías repetidas
        lineas = [l for l in texto.splitlines() if l.strip()]
        return "\n".join(lineas)[:max_chars]
    except Exception as e:
        return f"[No disponible: {e}]"


def cargar_info_institucional(forzar: bool = False) -> dict:
    if not forzar and INFO_CACHE_FILE.exists():
        with open(INFO_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    print("Descargando páginas institucionales...")
    info = {}
    for nombre, url in PAGINAS_INFO.items():
        info[nombre] = scrape_pagina(url)
        print(f"  [{nombre}] OK")
    with open(INFO_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


# ---------------------------------------------------------------------------
# Utilidades de productos y cotizacion
# ---------------------------------------------------------------------------

def obtener_cotizacion() -> float:
    try:
        r = requests.get("https://api.bluelytics.com.ar/v2/latest", timeout=10)
        return float(r.json()["oficial"]["value_sell"])
    except Exception:
        return 1404.0


def convertir_precio(precio_raw: str, cotizacion: float) -> str:
    try:
        precio_usd = int(precio_raw) / 100
        return f"${precio_usd * cotizacion:,.0f} ARS (USD {precio_usd:.2f})"
    except Exception:
        return "Consultar precio"


def descargar_productos(cotizacion: float) -> list:
    todos = []
    pagina = 1
    while True:
        try:
            r = requests.get(BASE_URL, params={"per_page": 100, "page": pagina}, timeout=15)
            if r.status_code != 200:
                break
            lote = r.json()
            if not lote:
                break
            for p in lote:
                precio_raw = p.get("prices", {}).get("price", "")
                todos.append({
                    "nombre": p.get("name", "Sin nombre"),
                    "precio": convertir_precio(precio_raw, cotizacion) if precio_raw else "Consultar precio",
                    "categoria": ", ".join(c["name"] for c in p.get("categories", [])),
                    "url": p.get("permalink", ""),
                })
            if len(lote) < 100:
                break
            pagina += 1
        except Exception:
            break
    return todos


def cargar_o_actualizar_productos(forzar: bool = False) -> tuple[list, float]:
    cotizacion = obtener_cotizacion()
    if not forzar and CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            productos = json.load(f)
    else:
        productos = descargar_productos(cotizacion)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(productos, f, ensure_ascii=False, indent=2)
    return productos, cotizacion


PALABRAS_PRECIO = {"económic", "economi", "barato", "barata", "bajos", "baja", "menor", "minimo", "mínimo",
                   "caro", "cara", "alto", "mayor", "maximo", "máximo", "costoso", "costosa"}

PALABRAS_RUIDO = {"cual", "cuál", "es", "la", "el", "lo", "los", "las", "que", "tienen", "hay",
                  "más", "mas", "me", "un", "una", "de", "para", "con", "por", "del", "al",
                  "busco", "quiero", "necesito", "tienen", "tengo", "ver", "mostrar", "dame"}

def _precio_numerico(p: dict) -> float:
    """Extrae precio numérico del string formateado para poder ordenar."""
    try:
        return float(p["precio"].replace("$", "").replace(".", "").replace(",", ".").split()[0])
    except Exception:
        return float("inf")

def buscar_productos(pregunta: str, productos: list, max_resultados: int = 15) -> list:
    texto_pregunta = pregunta.lower()
    palabras = texto_pregunta.split()

    # Detectar si es consulta de comparación de precios
    es_consulta_precio = any(kw in texto_pregunta for kw in PALABRAS_PRECIO)

    # Palabras de búsqueda = sin ruido ni palabras de precio
    palabras_busqueda = [w for w in palabras
                         if w not in PALABRAS_RUIDO and w not in PALABRAS_PRECIO and len(w) > 2]

    # Si no quedan palabras útiles, usar todas
    if not palabras_busqueda:
        palabras_busqueda = [w for w in palabras if len(w) > 3]

    resultados = []
    for p in productos:
        texto = (p["nombre"] + " " + p["categoria"]).lower()
        puntaje = sum(1 for w in palabras_busqueda if w in texto)
        if puntaje > 0:
            resultados.append((puntaje, p))

    # Para consultas de precio, ordenar los matches más relevantes por precio
    if es_consulta_precio and resultados:
        puntaje_max = max(p for p, _ in resultados)
        # Solo tomar productos con el puntaje máximo (los que más coinciden con lo buscado)
        relevantes = [p for puntaje, p in resultados if puntaje == puntaje_max]
        ascendente = not any(w in texto_pregunta for w in ["caro", "cara", "alto", "mayor", "maximo", "máximo", "costoso", "costosa"])
        relevantes.sort(key=_precio_numerico, reverse=not ascendente)
        return relevantes[:20]

    resultados.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in resultados[:max_resultados]]


# ---------------------------------------------------------------------------
# Sistema prompt comercial
# ---------------------------------------------------------------------------

def system_prompt(cotizacion: float, info: dict = None) -> str:
    if info:
        secciones = "\n\n".join(
            f"=== {nombre.upper()} ===\n{contenido}"
            for nombre, contenido in info.items()
            if contenido and not contenido.startswith("[No disponible")
        )
        info_section = f"\n# INFORMACIÓN INSTITUCIONAL DEL SITIO\nUsá este contenido para responder consultas sobre contacto, envíos, devoluciones y locales:\n\n{secciones}" if secciones else ""
    else:
        info_section = ""

    return f"""Sos el asistente virtual de Distribuciones San Luis, distribuidor mayorista de tecnología en Argentina.

ROL: Vendedor digital profesional + asesor comercial. No solo respondés: vendés.

IMPORTANTE SOBRE EL CATÁLOGO:
- El sistema te provee los productos relevantes para cada consulta directamente en el mensaje
- Cuando se trata de comparar precios (más económico, más barato, etc.), los productos ya vienen ordenados por precio de menor a mayor
- Nunca digas que no tenés acceso al catálogo — los productos disponibles son exactamente los que aparecen en el contexto de cada mensaje
- Si no aparece un producto en el contexto, ofrecé las opciones que sí tenés disponibles

REGLAS COMERCIALES:
- Compra mínima: $80.000 ARS
- Contacto WhatsApp para cerrar pedidos: +54 2664583129
- Local físico: Rivadavia 1005, San Luis Capital, CP 5700
- Cuando el cliente pregunte por dirección, horarios o cómo llegar, dar la dirección del local y sugerir confirmar horarios por WhatsApp
- Precios en pesos argentinos usando cotización Banco Nación: ${cotizacion:.0f} por dólar
- Mostrar siempre precio en ARS cuando esté disponible
- Siempre incluir el link del producto cuando lo mencionés, como: "Ver producto: [nombre](url)"
- Nunca inventar precios, stock ni promociones no confirmadas
- Si no tenés el dato, decilo y ofrecé verificar

CLASIFICACIÓN DE INTENCIÓN (detectar en cada mensaje):
- consulta_precio | consulta_tecnica | comparacion | stock | medios_pago | envios | reventa | cierre | reclamo | saludo

TEMPERATURA DEL LEAD:
- Frío: consulta general → educar, orientar, no presionar
- Tibio: preguntas específicas → resolver objeciones, propuesta concreta
- Caliente: pregunta precio/stock/pago → responder rápido, microcerrar, facilitar cierre

ESTRUCTURA DE RESPUESTA COMERCIAL:
1. Apertura natural
2. Respuesta clara con precio si aplica
3. Beneficio concreto
4. Microcierre ("¿Querés que te pase disponibilidad y medios de pago?")

MODO REVENDEDOR: Si detectás intención de reventa, hablar de margen, rotación y volumen.

DERIVACIÓN A HUMANO: Si hay reclamo complejo, negociación fuera de política o el usuario lo pide.

Respondé siempre en español, tono profesional pero cercano, nunca robótico.

{info_section}"""


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    try:
        productos, cotizacion = cargar_o_actualizar_productos()
        estado["productos"] = productos
        estado["cotizacion"] = cotizacion
        print(f"[OK] {len(productos)} productos cargados | Dólar: ${cotizacion:.0f}")
    except Exception as e:
        print(f"[WARN] Error cargando productos: {e}")
    try:
        estado["info"] = cargar_info_institucional()
        print(f"[OK] Info institucional cargada ({len(estado['info'])} secciones)")
    except Exception as e:
        print(f"[WARN] Error cargando info institucional: {e}")

    import asyncio

    async def actualizar_cada_12h():
        while True:
            await asyncio.sleep(12 * 60 * 60)
            try:
                productos, cotizacion = cargar_o_actualizar_productos(forzar=True)
                estado["productos"] = productos
                estado["cotizacion"] = cotizacion
                estado["info"] = cargar_info_institucional(forzar=True)
                print(f"[AUTO] Actualización: {len(productos)} productos | Dólar: ${cotizacion:.0f}")
            except Exception as e:
                print(f"[WARN] Error en actualización automática: {e}")

    asyncio.create_task(actualizar_cada_12h())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "antigravity2024")

@app.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """Verificación del webhook de Meta."""
    params = dict(request.query_params)
    if (params.get("hub.mode") == "subscribe" and
            params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)


@app.get("/webhook")
async def webhook_verify(request: Request):
    """Verificación alternativa del webhook de Meta."""
    params = dict(request.query_params)
    if (params.get("hub.mode") == "subscribe" and
            params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)


@app.get("/", response_class=HTMLResponse)
async def index():
    html = CHAT_HTML.read_text(encoding="utf-8")
    html = html.replace("{{ total_productos }}", str(len(estado["productos"])))
    html = html.replace("{{ cotizacion }}", f"{estado['cotizacion']:.0f}")
    return HTMLResponse(content=html)


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    session_id: str = body.get("session_id") or str(uuid.uuid4())
    mensaje: str = body.get("mensaje", "").strip()

    if not mensaje:
        return JSONResponse({"error": "Mensaje vacío"}, status_code=400)

    if session_id not in sesiones:
        sesiones[session_id] = []

    historial = sesiones[session_id]

    # Buscar productos relevantes
    relevantes = buscar_productos(mensaje, estado["productos"])
    if relevantes:
        contexto = "\n".join(
            f"- {sanitize_str(p['nombre'])} | {p['precio']} | {sanitize_str(p['categoria'])} | {p['url']}"
            for p in relevantes
        )
        contenido_usuario = f"{mensaje}\n\n[Productos relevantes encontrados]\n{contexto}"
    else:
        contenido_usuario = f"{mensaje}\n\n[Sin productos específicos. Sugerir contacto por WhatsApp si corresponde.]"

    historial.append({"role": "user", "content": contenido_usuario})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt(estado["cotizacion"], estado["info"]),
            messages=historial,
        )
        texto = respuesta.content[0].text
        historial.append({"role": "assistant", "content": texto})
        # Limitar historial a 20 turnos para no crecer indefinidamente
        if len(historial) > 40:
            sesiones[session_id] = historial[-40:]
        return JSONResponse({"respuesta": texto, "session_id": session_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/status")
async def status():
    return {
        "productos": len(estado["productos"]),
        "cotizacion": estado["cotizacion"],
        "sesiones_activas": len(sesiones),
    }


def enviar_meta(numero: str, texto: str):
    """Envía un mensaje de texto via WhatsApp Cloud API de Meta."""
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": texto},
    }
    r = requests.post(META_API_URL, json=payload, headers=headers, timeout=15)
    if not r.ok:
        print(f"[ERROR meta] {r.status_code} {r.text}")


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """Webhook que recibe mensajes de WhatsApp via Meta Cloud API."""
    try:
        data = await request.json()
    except Exception:
        return PlainTextResponse("ok")

    # Extraer mensaje del payload de Meta
    try:
        entry = data["entry"][0]
        change = entry["changes"][0]["value"]
        # Ignorar notificaciones de estado (delivered, read, etc.)
        if "messages" not in change:
            return PlainTextResponse("ok")
        msg = change["messages"][0]
        if msg.get("type") != "text":
            return PlainTextResponse("ok")
        numero = msg["from"]
        mensaje = msg["text"]["body"].strip()
    except (KeyError, IndexError):
        return PlainTextResponse("ok")

    if not mensaje:
        return PlainTextResponse("ok")

    if numero not in sesiones:
        sesiones[numero] = []

    historial = sesiones[numero]
    relevantes = buscar_productos(mensaje, estado["productos"])

    if relevantes:
        contexto = "\n".join(
            f"- {sanitize_str(p['nombre'])} | {p['precio']} | {sanitize_str(p['categoria'])} | {p['url']}"
            for p in relevantes
        )
        contenido = f"{mensaje}\n\n[Productos relevantes]\n{contexto}"
    else:
        contenido = f"{mensaje}\n\n[Sin productos específicos. Sugerir contacto por WhatsApp si corresponde.]"

    historial.append({"role": "user", "content": contenido})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt(estado["cotizacion"], estado["info"]),
            messages=historial,
        )
        texto = respuesta.content[0].text
        historial.append({"role": "assistant", "content": texto})

        if len(historial) > 40:
            sesiones[numero] = historial[-40:]

        enviar_meta(numero, texto)
    except Exception as e:
        print(f"[ERROR whatsapp] {e}")

    return PlainTextResponse("ok")


@app.post("/actualizar")
async def actualizar():
    """Fuerza recarga de productos, cotización e info institucional."""
    productos, cotizacion = cargar_o_actualizar_productos(forzar=True)
    estado["productos"] = productos
    estado["cotizacion"] = cotizacion
    estado["info"] = cargar_info_institucional(forzar=True)
    return {"productos": len(productos), "cotizacion": cotizacion, "secciones_info": list(estado["info"].keys())}


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
