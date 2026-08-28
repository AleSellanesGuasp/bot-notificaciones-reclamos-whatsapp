# 🔔 Bot de Notificaciones de Reclamos por WhatsApp

Automatización que conecta un sistema interno de gestión de tickets/reclamos con un grupo de WhatsApp, notificando en tiempo real cuando se crea un reclamo nuevo o se reasigna a otro técnico — mencionando (@) al técnico correspondiente de forma real, no como texto plano.

## ¿Qué hace?

1. **Detecta reclamos nuevos** en la base de datos del sistema de gestión (en modo solo lectura, nunca escribe en ella).
2. **Detecta reasignaciones**: si un reclamo pendiente cambia de técnico asignado, avisa al nuevo técnico y menciona quién lo tenía antes.
3. **Envía el aviso al grupo de WhatsApp** con una mención real (@) al técnico correspondiente — no solo su nombre como texto.
4. Corre en un **bucle continuo**, revisando la base de datos cada pocos segundos, sin necesidad de tareas programadas externas.
5. Incluye protección para no reenviar avisos duplicados, y para no "bombardear" el grupo con el historial completo la primera vez que se activa.

## Tecnologías

- **Python 3**
- **Playwright** — automatización de WhatsApp Web
- **SQLite** — lectura de la base de datos del sistema de gestión
- **systemd + Xvfb** — despliegue como servicio persistente en un servidor Linux sin monitor

## Configuración

1. Instalá las dependencias:

Ejecutar en consola: pip install -r requirements.txt
playwright install

2. Creá un archivo `.env` basado en `.env.example`, con la ruta a tu base de datos y el nombre de tu grupo de WhatsApp.
3. La primera vez, hace falta escanear un código QR para vincular el número de WhatsApp que va a actuar como bot.
   - Si tenés entorno gráfico: corré `python bot_whatsapp_reclamos.py` directamente y escaneá el QR que aparece en pantalla.
   - Si estás en un servidor sin monitor (headless): podés adaptar el script para tomar una captura de pantalla del QR con `page.screenshot()` y descargarla para escanearla remotamente.

## Despliegue en un servidor Linux (systemd)

En servidores sin entorno gráfico, WhatsApp Web puede detectar el navegador headless y bloquear el acceso. La solución es usar `Xvfb` (pantalla virtual):

Ejecutar en consola: sudo apt install xvfb -y


Se incluye una plantilla de servicio (`bot-whatsapp.service.example`) para correr el bot de forma persistente, con reinicio automático ante fallos. Copiala a `/etc/systemd/system/`, ajustá las rutas, y activala con:

Ejecutar en consola: sudo systemctl daemon-reload
sudo systemctl enable bot-whatsapp.service
sudo systemctl start bot-whatsapp.service