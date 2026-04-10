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
BASE_URL_CATEGORIAS = "https://distribucionessl.com/wp-json/wc/store/v1/products/categories"
CACHE_FILE = BASE_DIR / "productos_cache.json"
CACHE_FILE_CATEGORIAS = BASE_DIR / "categorias_cache.json"
INFO_CACHE_FILE = BASE_DIR / "info_cache.json"

# TodoPCE (Messenger / retail)
BASE_URL_TODOPCE = "https://todopce.com.ar/wp-json/wc/store/v1/products"
CACHE_FILE_TODOPCE = BASE_DIR / "productos_cache_todopce.json"
INFO_CACHE_FILE_TODOPCE = BASE_DIR / "info_cache_todopce.json"
META_PAGE_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "").strip()
MESSENGER_API_URL = "https://graph.facebook.com/v19.0/me/messages"

# Páginas a scrapear para información institucional
PAGINAS_INFO = {
    "contacto":    "https://distribucionessl.com/our-contacts/",
    "envios":      "https://distribucionessl.com/delivery-return-2/",
    "locales":     "https://distribucionessl.com/stores/",
}

PAGINAS_INFO_TODOPCE = {
    "contacto":  "https://todopce.com.ar/contacto/",
    "envios":    "https://todopce.com.ar/envios/",
}

# Estado global
estado = {
    "productos": [],
    "categorias": [],
    "cotizacion": 1404.0,
    "info": {},
    "productos_todopce": [],
    "info_todopce": {},
}

# Historial por sesion { session_id: [mensajes] }
sesiones: dict[str, list] = {}

# IDs de mensajes ya procesados (anti-duplicados de Meta)
mensajes_vistos: set[str] = set()


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


def convertir_precio_ars(precio_raw: str) -> str:
    """Convierte precio en centavos ARS a pesos formateados."""
    try:
        return f"${int(precio_raw) / 100:,.0f} ARS"
    except Exception:
        return "Consultar precio"


def descargar_productos_todopce() -> list:
    todos = []
    pagina = 1
    while True:
        try:
            r = requests.get(BASE_URL_TODOPCE, params={"per_page": 100, "page": pagina}, timeout=15)
            if r.status_code != 200:
                break
            lote = r.json()
            if not lote:
                break
            for p in lote:
                precio_raw = p.get("prices", {}).get("price", "")
                todos.append({
                    "nombre": p.get("name", "Sin nombre"),
                    "precio": convertir_precio_ars(precio_raw) if precio_raw else "Consultar precio",
                    "categoria": ", ".join(c["name"] for c in p.get("categories", [])),
                    "url": p.get("permalink", ""),
                })
            if len(lote) < 100:
                break
            pagina += 1
        except Exception:
            break
    return todos


def cargar_o_actualizar_productos_todopce(forzar: bool = False) -> list:
    if not forzar and CACHE_FILE_TODOPCE.exists():
        with open(CACHE_FILE_TODOPCE, encoding="utf-8") as f:
            return json.load(f)
    productos = descargar_productos_todopce()
    with open(CACHE_FILE_TODOPCE, "w", encoding="utf-8") as f:
        json.dump(productos, f, ensure_ascii=False, indent=2)
    return productos


def cargar_info_todopce(forzar: bool = False) -> dict:
    if not forzar and INFO_CACHE_FILE_TODOPCE.exists():
        with open(INFO_CACHE_FILE_TODOPCE, encoding="utf-8") as f:
            return json.load(f)
    print("Descargando páginas institucionales TodoPCE...")
    info = {}
    for nombre, url in PAGINAS_INFO_TODOPCE.items():
        info[nombre] = scrape_pagina(url)
        print(f"  [{nombre}] OK")
    with open(INFO_CACHE_FILE_TODOPCE, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def descargar_categorias() -> list:
    todas = []
    pagina = 1
    while True:
        try:
            r = requests.get(BASE_URL_CATEGORIAS, params={"per_page": 100, "page": pagina}, timeout=15)
            if r.status_code != 200:
                break
            lote = r.json()
            if not lote:
                break
            for c in lote:
                if c.get("count", 0) > 0:  # solo categorías con productos
                    todas.append({
                        "nombre": c.get("name", ""),
                        "slug": c.get("slug", ""),
                        "url": c.get("link", ""),
                        "count": c.get("count", 0),
                    })
            if len(lote) < 100:
                break
            pagina += 1
        except Exception:
            break
    return todas


def cargar_o_actualizar_categorias(forzar: bool = False) -> list:
    if not forzar and CACHE_FILE_CATEGORIAS.exists():
        with open(CACHE_FILE_CATEGORIAS, encoding="utf-8") as f:
            return json.load(f)
    categorias = descargar_categorias()
    with open(CACHE_FILE_CATEGORIAS, "w", encoding="utf-8") as f:
        json.dump(categorias, f, ensure_ascii=False, indent=2)
    return categorias


def buscar_categoria(pregunta: str, categorias: list) -> dict | None:
    """Busca la categoría más relevante para la pregunta del usuario."""
    texto = pregunta.lower()
    palabras = [w for w in texto.split() if w not in PALABRAS_RUIDO and len(w) > 2]
    if not palabras:
        return None
    mejor = None
    mejor_puntaje = 0
    for cat in categorias:
        texto_cat = (cat["nombre"] + " " + cat["slug"]).lower()
        puntaje = sum(1 for w in palabras if w in texto_cat)
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor = cat
    return mejor if mejor_puntaje > 0 else None


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

ROL: Orientar al cliente hacia la web y derivar consultas de compra al vendedor humano.

COMPORTAMIENTO PRINCIPAL:
- Cuando el cliente pregunta por un producto o categoría, respondé con 1-2 oraciones e incluí SIEMPRE el link usando EXACTAMENTE la URL que aparece en el contexto del mensaje (campo URL). Nunca construyas ni inventes URLs.
- Formato del link: [nombre de la categoría](URL exacta del contexto)
- No listes productos individuales ni precios. Solo dirigí al cliente a la categoría con su link.
- Si el cliente quiere comprar, hacer un pedido, consultar precio final, stock, medios de pago o condiciones mayoristas → derivalo al WhatsApp de ventas: +54 2664583129

REGLAS:
- Compra mínima: $80.000 ARS
- Local físico: Rivadavia 1005, San Luis Capital, CP 5700
- Precios en pesos argentinos, cotización Banco Nación: ${cotizacion:.0f} por dólar
- Nunca inventar precios, stock ni promociones

LONGITUD: Máximo 2-3 oraciones. Directo y simple.

Respondé siempre en español, tono amigable y profesional.

{info_section}"""


def system_prompt_messenger(info: dict = None) -> str:
    if info:
        secciones = "\n\n".join(
            f"=== {nombre.upper()} ===\n{contenido}"
            for nombre, contenido in info.items()
            if contenido and not contenido.startswith("[No disponible")
        )
        info_section = f"\n# INFORMACIÓN DEL SITIO\n{secciones}" if secciones else ""
    else:
        info_section = ""

    return f"""Sos el asistente virtual de TodoPCE, tienda de tecnología en Argentina para consumidores finales.

ROL: Asesor de ventas amigable y directo. Ayudás a la gente a elegir el producto que necesita.

IMPORTANTE SOBRE EL CATÁLOGO:
- Los productos relevantes para cada consulta aparecen directamente en el mensaje
- Nunca digas que no tenés acceso al catálogo — los productos disponibles son los que aparecen en el contexto
- Si no aparece un producto, ofrecé las opciones que sí tenés

REGLAS COMERCIALES:
- Sin compra mínima — cualquier persona puede comprar
- Precios en pesos argentinos (ARS), ya están actualizados
- Contacto WhatsApp para consultas y pedidos: +54 2664583129
- Siempre incluir el link del producto cuando lo mencionés: "Ver producto: [nombre](url)"
- Nunca inventar precios, stock ni promociones

LONGITUD DE RESPUESTA: Máximo 3-4 oraciones por mensaje. Estás en Messenger, no escribiendo un email. Sin listas largas. Si hay muchos productos, mencioná los 2-3 más relevantes.

Respondé siempre en español, tono amigable y cercano, nunca robótico.

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
    try:
        estado["categorias"] = cargar_o_actualizar_categorias()
        print(f"[OK] {len(estado['categorias'])} categorías cargadas")
    except Exception as e:
        print(f"[WARN] Error cargando categorías: {e}")
    import asyncio

    async def cargar_todopce_background():
        """Carga datos de TodoPCE en background para no bloquear el startup."""
        await asyncio.sleep(5)
        try:
            estado["productos_todopce"] = cargar_o_actualizar_productos_todopce()
            print(f"[OK] {len(estado['productos_todopce'])} productos TodoPCE cargados")
        except Exception as e:
            print(f"[WARN] Error cargando productos TodoPCE: {e}")
        try:
            estado["info_todopce"] = cargar_info_todopce()
            print(f"[OK] Info TodoPCE cargada ({len(estado['info_todopce'])} secciones)")
        except Exception as e:
            print(f"[WARN] Error cargando info TodoPCE: {e}")

    asyncio.create_task(cargar_todopce_background())

    async def actualizar_cada_12h():
        while True:
            await asyncio.sleep(12 * 60 * 60)
            try:
                productos, cotizacion = cargar_o_actualizar_productos(forzar=True)
                estado["productos"] = productos
                estado["cotizacion"] = cotizacion
                estado["info"] = cargar_info_institucional(forzar=True)
                estado["categorias"] = cargar_o_actualizar_categorias(forzar=True)
                estado["productos_todopce"] = cargar_o_actualizar_productos_todopce(forzar=True)
                estado["info_todopce"] = cargar_info_todopce(forzar=True)
                print(f"[AUTO] Actualización: {len(productos)} San Luis | {len(estado['productos_todopce'])} TodoPCE | Dólar: ${cotizacion:.0f}")
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
            max_tokens=500,
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
        msg_id = msg.get("id", "")
    except (KeyError, IndexError):
        return PlainTextResponse("ok")

    if not mensaje:
        return PlainTextResponse("ok")

    # Anti-duplicados: ignorar si ya procesamos este mensaje
    if msg_id and msg_id in mensajes_vistos:
        return PlainTextResponse("ok")
    if msg_id:
        mensajes_vistos.add(msg_id)
        # Limpiar el set si crece demasiado (evitar memory leak)
        if len(mensajes_vistos) > 10000:
            mensajes_vistos.clear()

    if numero not in sesiones:
        sesiones[numero] = []

    historial = sesiones[numero]
    categoria = buscar_categoria(mensaje, estado["categorias"])

    if categoria:
        # Respuesta directa sin pasar por Claude — garantiza URL correcta y ahorra tokens
        texto = (
            f"Podés ver todos los *{categoria['nombre']}* disponibles acá:\n"
            f"{categoria['url']}\n\n"
            f"Para consultar precios, stock o hacer un pedido mayorista escribinos al WhatsApp: +54 2664583129 💬"
        )
        enviar_meta(numero, texto)
        return PlainTextResponse("ok")

    # Sin categoría: usar Claude para respuesta general
    historial.append({"role": "user", "content": mensaje})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
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


def enviar_messenger(psid: str, texto: str):
    """Envía un mensaje de texto via Messenger API."""
    headers = {
        "Authorization": f"Bearer {META_PAGE_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "recipient": {"id": psid},
        "message": {"text": texto},
    }
    r = requests.post(MESSENGER_API_URL, json=payload, headers=headers, timeout=15)
    if not r.ok:
        print(f"[ERROR messenger] {r.status_code} {r.text}")


@app.get("/messenger")
async def messenger_verify(request: Request):
    """Verificación del webhook de Messenger."""
    params = dict(request.query_params)
    if (params.get("hub.mode") == "subscribe" and
            params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)


@app.post("/messenger")
async def messenger_webhook(request: Request):
    """Webhook que recibe mensajes de Facebook Messenger."""
    try:
        data = await request.json()
    except Exception:
        return PlainTextResponse("ok")

    try:
        entry = data["entry"][0]
        messaging = entry["messaging"][0]
        psid = messaging["sender"]["id"]
        if "message" not in messaging:
            return PlainTextResponse("ok")
        msg = messaging["message"]
        # Ignorar mensajes del propio bot (eco)
        if msg.get("is_echo"):
            return PlainTextResponse("ok")
        msg_id = msg.get("mid", "")
        mensaje = msg.get("text", "").strip()
    except (KeyError, IndexError):
        return PlainTextResponse("ok")

    if not mensaje:
        return PlainTextResponse("ok")

    # Anti-duplicados
    if msg_id and msg_id in mensajes_vistos:
        return PlainTextResponse("ok")
    if msg_id:
        mensajes_vistos.add(msg_id)
        if len(mensajes_vistos) > 10000:
            mensajes_vistos.clear()

    session_key = f"messenger_{psid}"
    if session_key not in sesiones:
        sesiones[session_key] = []

    historial = sesiones[session_key]
    relevantes = buscar_productos(mensaje, estado["productos_todopce"])

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
            max_tokens=500,
            system=system_prompt_messenger(estado["info_todopce"]),
            messages=historial,
        )
        texto = respuesta.content[0].text
        historial.append({"role": "assistant", "content": texto})

        if len(historial) > 40:
            sesiones[session_key] = historial[-40:]

        enviar_messenger(psid, texto)
    except Exception as e:
        print(f"[ERROR messenger] {e}")

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
