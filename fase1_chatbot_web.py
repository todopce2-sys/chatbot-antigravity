import os
import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()

def scrape_url(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script","style","nav","footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:8000]

def chatbot(url):
    contenido = scrape_url(url)
    system = "Responde SOLO basandote en este contenido:\n" + contenido
    historial = []
    print("Chatbot listo. Escribe tu pregunta o salir")
    while True:
        pregunta = input("Tu: ").strip()
        if pregunta.lower() == "salir":
            break
        historial.append({"role":"user","content":pregunta})
        r = client.messages.create(model="claude-sonnet-4-20250514",max_tokens=1024,system=system,messages=historial)
        texto = r.content[0].text
        historial.append({"role":"assistant","content":texto})
        print("Claude:",texto)

chatbot("https://www.distribucionessl.com")
