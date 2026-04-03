@echo off
cd /d "C:\Users\Usuario\Desktop\Proyectos_Antigravity\chatbot-claude"

:: Iniciar servidor Python en segundo plano
start "Chatbot API" python fase3_chatbot_api.py

:: Esperar a que el servidor arranque
timeout /t 10 /nobreak >nul

:: Iniciar ngrok con dominio estático
start "ngrok" ngrok http --domain=kellen-noncontumacious-vivienne.ngrok-free.dev 8000

echo Chatbot iniciado correctamente.
echo Servidor: http://localhost:8000
echo WhatsApp webhook: https://kellen-noncontumacious-vivienne.ngrok-free.dev/whatsapp
