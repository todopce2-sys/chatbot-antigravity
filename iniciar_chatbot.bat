@echo off
cd /d "D:\Usuario\Desktop\Proyectos_Antigravity\chatbot-claude"

:: Iniciar servidor Python en segundo plano
start "DSL Soporte API" python fase3_chatbot_api.py

:: Esperar a que el servidor arranque y descargue el conocimiento
timeout /t 10 /nobreak >nul

:: Iniciar ngrok con dominio estático
start "ngrok" "C:\chatbot-claude\ngrok.exe" http --domain=kellen-noncontumacious-vivienne.ngrok-free.dev 8000

echo Agente de soporte DSL Sistemas iniciado.
echo Servidor:          http://localhost:8000
echo WhatsApp webhook:  https://kellen-noncontumacious-vivienne.ngrok-free.dev/whatsapp
echo Messenger webhook: https://kellen-noncontumacious-vivienne.ngrok-free.dev/messenger
echo Estado:            http://localhost:8000/status
