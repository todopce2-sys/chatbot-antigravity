import os
import json
import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()

BASE_URL = "https://distribucionessl.com/wp-json/wc/store/v1/products"
CACHE_FILE = "productos_cache.json"


def obtener_cotizacion():
    """Obtiene el dolar oficial venta del Banco Nacion."""
    try:
        r = requests.get("https://api.bluelytics.com.ar/v2/latest", timeout=10)
        cotizacion = r.json()["oficial"]["value_sell"]
        print(f"Cotizacion dolar oficial (Banco Nacion): ${cotizacion:.0f}")
        return cotizacion
    except Exception as e:
        print(f"No se pudo obtener cotizacion: {e}. Usando $1404 como referencia.")
        return 1404.0


def convertir_precio(precio_raw, cotizacion):
    """Convierte precio de centavos de dolar a pesos argentinos."""
    try:
        precio_usd = int(precio_raw) / 100
        precio_ars = precio_usd * cotizacion
        return f"${precio_ars:,.0f} ARS (USD {precio_usd:.2f})"
    except:
        return "Consultar precio"


def obtener_productos(cotizacion):
    """Descarga todos los productos via API de WooCommerce."""
    todos = []
    pagina = 1

    print("\nDescargando productos desde la API...")
    while True:
        try:
            r = requests.get(BASE_URL, params={"per_page": 100, "page": pagina}, timeout=15)
            if r.status_code != 200:
                break
            productos = r.json()
            if not productos:
                break
            for p in productos:
                nombre = p.get("name", "Sin nombre")
                precio_raw = p.get("prices", {}).get("price", "")
                precio_fmt = convertir_precio(precio_raw, cotizacion) if precio_raw else "Consultar precio"
                url = p.get("permalink", "")
                categoria = ", ".join([c["name"] for c in p.get("categories", [])])
                todos.append({
                    "nombre": nombre,
                    "precio": precio_fmt,
                    "categoria": categoria,
                    "url": url,
                })
            print(f"  Pagina {pagina}: {len(productos)} productos")
            if len(productos) < 100:
                break
            pagina += 1
        except Exception as e:
            print(f"  Error: {e}")
            break

    return todos


def construir_base():
    """Siempre descarga precios frescos con cotizacion actualizada."""
    cotizacion = obtener_cotizacion()

    if os.path.exists(CACHE_FILE):
        print("Cache existente encontrado. Actualizando precios con cotizacion de hoy...")
        os.remove(CACHE_FILE)

    productos = obtener_productos(cotizacion)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(productos, f, ensure_ascii=False, indent=2)
    print(f"\nTotal: {len(productos)} productos con precios actualizados.")
    return productos, cotizacion


def buscar_productos(pregunta, productos, max_resultados=10):
    palabras = pregunta.lower().split()
    resultados = []
    for p in productos:
        texto = (p["nombre"] + " " + p["categoria"]).lower()
        puntaje = sum(1 for palabra in palabras if palabra in texto)
        if puntaje > 0:
            resultados.append((puntaje, p))
    resultados.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in resultados[:max_resultados]]


def chatbot_ecommerce(productos, cotizacion):
    system = f"""Eres el asistente virtual de Distribuciones San Luis, distribuidor mayorista de tecnologia en Argentina.

REGLAS:
- Compra minima: $80.000 ARS
- Siempre mencionar consultar disponibilidad de stock
- Contacto WhatsApp: +54 2664583129
- Solo hablar de productos del contexto
- Los precios ya estan convertidos a pesos argentinos usando cotizacion del Banco Nacion: ${cotizacion:.0f} por dolar
- Mostrar siempre el precio en ARS cuando este disponible
- Ser amable y profesional
- Responder en espanol"""

    historial = []
    print("\n" + "="*55)
    print(" Chatbot Distribuciones San Luis listo!")
    print(f" {len(productos)} productos | Dolar: ${cotizacion:.0f}")
    print(" Escribe tu pregunta o 'salir' para terminar")
    print("="*55 + "\n")

    while True:
        pregunta = input("Cliente: ").strip()
        if pregunta.lower() == "salir":
            print("Hasta luego!")
            break
        if not pregunta:
            continue

        relevantes = buscar_productos(pregunta, productos)
        if relevantes:
            contexto = "\n".join([
                f"- {p['nombre']} | Precio: {p['precio']} | Categoria: {p['categoria']} | Link: {p['url']}"
                for p in relevantes
            ])
        else:
            contexto = "No se encontraron productos especificos. Sugerir contacto por WhatsApp."

        mensaje = f"Pregunta: {pregunta}\n\nProductos disponibles:\n{contexto}\n\nResponde de forma clara mostrando precios en pesos argentinos."
        historial.append({"role": "user", "content": mensaje})

        try:
            respuesta = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=system,
                messages=historial,
            )
            texto = respuesta.content[0].text
            historial.append({"role": "assistant", "content": texto})
            print(f"\nAsistente: {texto}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    print("\n=== CHATBOT DISTRIBUCIONES SAN LUIS — FASE 2 ===")
    productos, cotizacion = construir_base()
    if productos:
        chatbot_ecommerce(productos, cotizacion)
    else:
        print("No se encontraron productos. Verificar conexion.")
