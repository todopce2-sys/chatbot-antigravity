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

app = FastAPI(title="DSL Sistemas – Agente de Soporte Técnico")
CHAT_HTML = BASE_DIR / "templates" / "chat.html"

client = Anthropic()
META_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
META_PHONE_ID = os.environ.get("META_PHONE_NUMBER_ID", "1045946858607234")
META_API_URL = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
META_PAGE_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "").strip()
MESSENGER_API_URL = "https://graph.facebook.com/v19.0/me/messages"
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "antigravity2024")

CONOCIMIENTO_CACHE = BASE_DIR / "dsl_conocimiento.json"

# Páginas del sitio web de DSL Sistemas a indexar como base de conocimiento
PAGINAS_DSL = {
    "inicio":    "https://www.dslsistemas.com.ar",
    "servicios": "https://www.dslsistemas.com.ar/servicios",
    "contacto":  "https://www.dslsistemas.com.ar/contacto",
}

# Estado global
estado = {
    "conocimiento": {},
}

# Historial por sesión { session_id: [mensajes] }
sesiones: dict[str, list] = {}

# IDs de mensajes ya procesados (anti-duplicados de Meta)
mensajes_vistos: set[str] = set()


# ---------------------------------------------------------------------------
# Scraping y base de conocimiento
# ---------------------------------------------------------------------------

def scrape_pagina(url: str, max_chars: int = 5000) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        texto = soup.get_text(separator="\n", strip=True)
        lineas = [l for l in texto.splitlines() if l.strip()]
        return "\n".join(lineas)[:max_chars]
    except Exception as e:
        return f"[No disponible: {e}]"


def cargar_conocimiento(forzar: bool = False) -> dict:
    if not forzar and CONOCIMIENTO_CACHE.exists():
        with open(CONOCIMIENTO_CACHE, encoding="utf-8") as f:
            return json.load(f)
    print("Descargando contenido de dslsistemas.com.ar...")
    conocimiento = {}
    for nombre, url in PAGINAS_DSL.items():
        conocimiento[nombre] = scrape_pagina(url)
        print(f"  [{nombre}] OK")
    with open(CONOCIMIENTO_CACHE, "w", encoding="utf-8") as f:
        json.dump(conocimiento, f, ensure_ascii=False, indent=2)
    return conocimiento


# ---------------------------------------------------------------------------
# System prompt de soporte técnico
# ---------------------------------------------------------------------------

def system_prompt(conocimiento: dict) -> str:
    secciones = "\n\n".join(
        f"=== {nombre.upper()} ===\n{contenido}"
        for nombre, contenido in conocimiento.items()
        if contenido and not contenido.startswith("[No disponible")
    )
    base_conocimiento = f"\n\n# BASE DE CONOCIMIENTO DEL SITIO WEB\n{secciones}" if secciones else ""

    return f"""Sos el agente de soporte técnico de DSL Sistemas, empresa de desarrollo de software de San Luis, Argentina.

ROL: Asistente de soporte técnico profesional. Respondés consultas sobre los servicios de DSL Sistemas, ayudás a identificar qué solución tecnológica necesita cada cliente y los guiás al canal correcto para resolver su problema.

EMPRESA: DSL Sistemas
- Especialidad: Desarrollo de software, automatización con IA, transformación digital
- Ubicación: San Luis, Argentina
- Experiencia: +5 años, +50 proyectos, 98% de satisfacción de clientes

SERVICIOS:
1. Gestión de Stock – control de inventario en tiempo real, multiusuario, alertas de stock mínimo
2. Desarrollo Web – landing pages, e-commerce, sitios corporativos con SEO optimizado
3. Automatización con IA – chatbots 24/7, automatización de procesos, análisis predictivo
4. Aplicaciones Móviles – apps nativas para Android e iOS
5. Sistemas SaaS – plataformas en la nube escalables (planes Starter, Profesional, Enterprise)
6. Software a Medida – desarrollo personalizado con relevamiento, testing y soporte continuo

TECNOLOGÍAS: React, Flutter, Next.js, Node.js, Python, Laravel, PostgreSQL, MongoDB, AWS, Docker

PLANES SAAS:
- Starter: hasta 3 usuarios, módulos básicos, 5 GB, soporte por email
- Profesional: hasta 15 usuarios, todos los módulos, Business Intelligence, soporte 24/5, 50 GB, API de integraciones, módulo de IA incluido
- Enterprise: usuarios ilimitados, desarrollo a medida, soporte dedicado 24/7, SLA garantizado, onboarding presencial

CANALES DE CONTACTO:
- Email general: info@dslsistemas.com.ar
- Email soporte: soporte@dslsistemas.com.ar
- Tiempo de respuesta email: menos de 24 horas hábiles

CRITERIOS DE DERIVACIÓN A HUMANO:
- SOLO derivar a un humano cuando el problema no se puede resolver por este canal
- Cuando sea necesario derivar por soporte técnico, el contacto es: WhatsApp soporte +54 266 5258519
- Para consultas sobre nuevos servicios o contrataciones → derivar a soporte@dslsistemas.com.ar

CONSULTAS DE PRECIOS O COSTOS:
- Si el usuario pregunta por precios, costos, tarifas, presupuestos o cuánto vale/cuesta cualquier producto o servicio, derivar SIEMPRE al área de ventas
- Contacto de ventas: WhatsApp +54 266 458-3129 y correo ventas@dslsistemas.com.ar
- NUNCA dar precios específicos; indicar que el área de ventas puede asesorarlos con un presupuesto personalizado

REGLAS:
- Nunca inventar precios, características ni plazos de entrega no confirmados
- Ante cualquier consulta de precio o costo, derivar siempre al área de ventas (no dar valores específicos)
- Siempre intentá resolver el problema antes de derivar a un humano
- Acompañá al usuario paso a paso hasta encontrar la solución
- Solo derivar a humano cuando agotaste las posibilidades de resolver por este canal
- Escuchá bien la necesidad antes de proponer una solución

LONGITUD DE RESPUESTA: Conciso y claro. Máximo 4-5 oraciones por respuesta. Estás en un canal de chat, no escribiendo un informe. Si necesitás listar opciones, usá máximo 3 puntos concretos.

Respondé siempre en español, tono profesional y cercano, nunca robótico.
{base_conocimiento}"""


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    import asyncio

    try:
        estado["conocimiento"] = cargar_conocimiento()
        print(f"[OK] Base de conocimiento DSL cargada ({len(estado['conocimiento'])} secciones)")
    except Exception as e:
        print(f"[WARN] Error cargando conocimiento DSL: {e}")

    async def actualizar_cada_24h():
        while True:
            await asyncio.sleep(24 * 60 * 60)
            try:
                estado["conocimiento"] = cargar_conocimiento(forzar=True)
                print(f"[AUTO] Base de conocimiento actualizada: {len(estado['conocimiento'])} secciones")
            except Exception as e:
                print(f"[WARN] Error en actualización automática: {e}")

    asyncio.create_task(actualizar_cada_24h())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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
    return HTMLResponse(content=CHAT_HTML.read_text(encoding="utf-8"))


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
    historial.append({"role": "user", "content": mensaje})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt(estado["conocimiento"]),
            messages=historial,
        )
        texto = respuesta.content[0].text
        historial.append({"role": "assistant", "content": texto})
        if len(historial) > 40:
            sesiones[session_id] = historial[-40:]
        return JSONResponse({"respuesta": texto, "session_id": session_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/status")
async def status():
    return {
        "sesiones_activas": len(sesiones),
        "secciones_conocimiento": list(estado["conocimiento"].keys()),
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

    try:
        entry = data["entry"][0]
        change = entry["changes"][0]["value"]
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

    if msg_id and msg_id in mensajes_vistos:
        return PlainTextResponse("ok")
    if msg_id:
        mensajes_vistos.add(msg_id)
        if len(mensajes_vistos) > 10000:
            mensajes_vistos.clear()

    if numero not in sesiones:
        sesiones[numero] = []

    historial = sesiones[numero]
    historial.append({"role": "user", "content": mensaje})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt(estado["conocimiento"]),
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
        if msg.get("is_echo"):
            return PlainTextResponse("ok")
        msg_id = msg.get("mid", "")
        mensaje = msg.get("text", "").strip()
    except (KeyError, IndexError):
        return PlainTextResponse("ok")

    if not mensaje:
        return PlainTextResponse("ok")

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
    historial.append({"role": "user", "content": mensaje})

    try:
        respuesta = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt(estado["conocimiento"]),
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
    """Fuerza recarga del contenido del sitio web de DSL Sistemas."""
    estado["conocimiento"] = cargar_conocimiento(forzar=True)
    return {
        "secciones": list(estado["conocimiento"].keys()),
        "mensaje": "Base de conocimiento actualizada correctamente",
    }


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
