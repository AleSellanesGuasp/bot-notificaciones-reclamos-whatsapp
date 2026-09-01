from dotenv import load_dotenv
load_dotenv()
import sqlite3
import os
import re
import time
import random
from datetime import datetime
from playwright.sync_api import sync_playwright

RUTA_NOTIFICACIONES = "notificaciones.db"
RUTA_BD = os.environ["RUTA_BD_DONDE_CONSULTA"]
CARPETA_SESION = "sesion_whatsapp"
NOMBRE_GRUPO = os.environ["NOMBRE_GRUPO"]
INTERVALO_SEGUNDOS = int(os.environ["INTERVALO_SEGUNDOS"])
DIAS_SEMANA = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes"}
CONTACTOS_AUTORIZADOS = set(nombre.strip() for nombre in os.environ["CONTACTOS_AUTORIZADOS"].split(","))
HORA_INICIO_TRABAJO = (6, 50)
HORA_FIN_TRABAJO = (18, 0)
PLANTILLAS_SALUDO = [
    "Buen día a todos, feliz {dia} 💪 Iniciamos con todo las actividades de hoy.",
    "¡Feliz {dia} para todo el equipo! Que tengamos una jornada productiva.",
    "Buen día, feliz {dia}. Arrancamos con energía 🚀",
    "¡Feliz {dia}! A darle con todo hoy.",
    "Buen {dia} equipo, Arriba!! A Ganaaaarrr😎.",
]
fecha_ultimo_saludo = None

MENSAJE_MENU = """👋 ¡Hola! Soy Ticto, el asistente del Departamento Operativo.
Puedo ayudarte con esto:
📋 *Consultar un reclamo*
Escribí: estado #45
🖨️ *Consultar stock de tóner*
Escribí: stock toner
📦 *Pedir tóner para una impresora*
Escribí: pedir toner [número de serie]
Escribime cualquiera de estas opciones tal como aparecen arriba y te ayudo enseguida."""

nombres_grupos = set()  # se completa una sola vez al arrancar, con obtener_nombres_de_grupos()

en_horario_laboral_anterior = None  # global, para detectar el cambio de estado

def dentro_de_horario_laboral():
    ahora = datetime.now()
    if ahora.weekday() > 4:  # sábado=5, domingo=6 → fuera de horario directo
        return False
    minutos_actuales = ahora.hour * 60 + ahora.minute
    minutos_inicio = HORA_INICIO_TRABAJO[0] * 60 + HORA_INICIO_TRABAJO[1]
    minutos_fin = HORA_FIN_TRABAJO[0] * 60 + HORA_FIN_TRABAJO[1]
    return minutos_inicio <= minutos_actuales <= minutos_fin

def verificar_cambio_de_horario():
    global en_horario_laboral_anterior
    en_horario_ahora = dentro_de_horario_laboral()

    if en_horario_laboral_anterior is None:
        # primera vuelta del bucle, solo guardamos el estado sin loguear
        en_horario_laboral_anterior = en_horario_ahora
        return en_horario_ahora

    if en_horario_ahora != en_horario_laboral_anterior:
        if en_horario_ahora:
            registrar("Entrando en horario laboral (06:50-18:00) — reanudando actividad")
        else:
            registrar("Fuera de horario laboral — en pausa hasta mañana 06:50")
        en_horario_laboral_anterior = en_horario_ahora

    return en_horario_ahora

def registrar(mensaje):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{ahora}] {mensaje}"
    print(linea)
    with open("bot_log.txt", "a", encoding="utf-8") as archivo:
        archivo.write(linea + "\n")

def obtener_conexion_notificaciones():
    conexion = sqlite3.connect(RUTA_NOTIFICACIONES)
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS notificados (
            reclamo_id INTEGER PRIMARY KEY,
            tecnico_id_notificado INTEGER,
            tecnico_nombre_notificado TEXT
        )
    """)
    conexion.commit()
    return conexion

def obtener_reclamos_todos():
    conexion = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    cursor = conexion.cursor()
    consulta = """
        SELECT
            r.id,
            r.descripcion,
            r.nombre_reporta,
            r.tecnico_id AS tecnico_id_actual,
            o.nombre AS oficina_nombre,
            o.piso,
            o.bloque,
            t.nombre AS tecnico_nombre,
            t.apellido AS tecnico_apellido
        FROM reclamos r
        JOIN oficinas o ON r.oficina_id = o.id
        JOIN tecnicos t ON r.tecnico_id = t.id
        ORDER BY r.id ASC
    """
    cursor.execute(consulta)
    filas = cursor.fetchall()
    conexion.close()
    return filas

def obtener_registros_notificados(conexion_notif):
    cursor = conexion_notif.execute("SELECT reclamo_id, tecnico_id_notificado, tecnico_nombre_notificado FROM notificados")
    return {fila[0]: (fila[1], fila[2]) for fila in cursor.fetchall()}

def marcar_notificado(conexion_notif, reclamo_id, tecnico_id, tecnico_nombre_completo):
    conexion_notif.execute(
        "INSERT OR REPLACE INTO notificados (reclamo_id, tecnico_id_notificado, tecnico_nombre_notificado) VALUES (?, ?, ?)",
        (reclamo_id, tecnico_id, tecnico_nombre_completo)
    )
    conexion_notif.commit()

def armar_texto_nuevo(fila):
    nombre_busqueda = texto_busqueda_mencion(fila['tecnico_nombre'])
    texto_antes = (
        f"📋 #{fila['id']} {fila['descripcion']} - {fila['oficina_nombre']} "
        f"{fila['piso']}/{fila['bloque']} - {fila['nombre_reporta']} "
        f"@{nombre_busqueda}"
    )
    return texto_antes, ""

def armar_texto_reasignado(fila, nombre_tecnico_anterior):
    nombre_busqueda = texto_busqueda_mencion(fila['tecnico_nombre'])
    texto_antes = f"🔄 Reclamo reasignado de {nombre_tecnico_anterior} a @{nombre_busqueda}"
    texto_despues = (
        f": 📋 #{fila['id']} {fila['descripcion']} - {fila['oficina_nombre']} "
        f"{fila['piso']}/{fila['bloque']} - {fila['nombre_reporta']}"
    )
    return texto_antes, texto_despues

def texto_busqueda_mencion(nombre):
    """Devuelve la parte del nombre segura para tipear (sin tildes/ñ/etc.),
    cortando justo antes del primer carácter especial, para evitar problemas
    de tecleo de caracteres acentuados en algunos entornos."""
    seguro = ""
    for caracter in nombre:
        if ord(caracter) > 127:
            break
        seguro += caracter
    return seguro if seguro else nombre

def enviar_mensaje_simple(pagina, texto):
    caja_mensaje = pagina.locator("footer div[contenteditable='true']")
    caja_mensaje.click()
    pagina.keyboard.press("Control+A")
    pagina.keyboard.press("Delete")

    lineas = texto.split("\n")
    for i, linea in enumerate(lineas):
        if linea:
            caja_mensaje.press_sequentially(linea, delay=15)
        if i < len(lineas) - 1:
            pagina.keyboard.press("Shift+Enter")

    caja_mensaje.press("Enter")
    pagina.wait_for_timeout(1000)
    
def verificar_saludo_diario(pagina):
    global fecha_ultimo_saludo
    ahora = datetime.now()
    hoy = ahora.date()

    if ahora.weekday() > 4:
        return

    if ahora.hour == 7 and fecha_ultimo_saludo != hoy:
        dia_texto = DIAS_SEMANA[ahora.weekday()]
        mensaje = random.choice(PLANTILLAS_SALUDO).format(dia=dia_texto)

        enviar_mensaje_simple(pagina, mensaje)   # <- sacamos abrir_grupo(pagina) de acá
        fecha_ultimo_saludo = hoy
        registrar(f"Saludo diario enviado: {mensaje}")

def enviar_mensaje_con_mencion(pagina, texto_antes, nombre_busqueda, texto_despues=""):
    caja_mensaje = pagina.locator("footer div[contenteditable='true']")
    caja_mensaje.click()
    pagina.keyboard.press("Control+A")
    pagina.keyboard.press("Delete")
    caja_mensaje.press_sequentially(texto_antes, delay=80)
    pagina.wait_for_timeout(1500)

    sugerencias = pagina.locator("div[data-testid='contact-mention-list-item']")
    cantidad = sugerencias.count()

    if cantidad == 0:
        pagina.keyboard.press("Control+A")
        pagina.keyboard.press("Delete")
        return False, f"No se encontró ningún contacto que coincida con '{nombre_busqueda}'"

    if cantidad > 1:
        pagina.keyboard.press("Control+A")
        pagina.keyboard.press("Delete")
        return False, f"Se encontraron {cantidad} coincidencias para '{nombre_busqueda}' (ambiguo, revisar a mano)"

    sugerencias.first.click()
    pagina.wait_for_timeout(500)

    if texto_despues:
        caja_mensaje.press_sequentially(texto_despues, delay=30)
        pagina.wait_for_timeout(500)

    caja_mensaje.press("Enter")
    pagina.wait_for_timeout(1000)
    return True, "OK"

def abrir_grupo(pagina):
    buscador = pagina.get_by_role("textbox", name="Buscar un chat o iniciar uno nuevo")
    buscador.click()
    buscador.fill(NOMBRE_GRUPO)
    pagina.wait_for_timeout(2000)
    pagina.get_by_text(NOMBRE_GRUPO, exact=True).first.click()
    pagina.wait_for_timeout(1500)

    try:
        boton_limpiar = pagina.locator("[aria-label='End icon button']").first
        boton_limpiar.click(timeout=5000)
        pagina.wait_for_timeout(500)
    except Exception as error:
        registrar(f"No se pudo limpiar el buscador (no crítico): {error}")

def obtener_nombres_de_grupos(pagina):
    boton_grupos = pagina.get_by_text("Grupos", exact=False).first
    boton_grupos.click()
    pagina.wait_for_timeout(1500)

    filas = pagina.locator("div[data-testid='cell-frame-container']")
    nombres = set()
    for i in range(filas.count()):
        texto = filas.nth(i).inner_text()
        primera_linea = texto.split("\n")[0].strip()
        if primera_linea:
            nombres.add(primera_linea)

    boton_todos = pagina.get_by_text("Todos", exact=True).first
    boton_todos.click()
    pagina.wait_for_timeout(1000)
    return nombres

def clasificar_comando(texto):
    texto = texto.strip().lower()
    coincidencia = re.match(r'^estado\s*#?\s*(\d+)$', texto)
    if coincidencia:
        return ("estado", coincidencia.group(1))
    if re.match(r'^stock(\s+de)?\s+t[oó]ner$', texto):
        return ("stock", None)
    coincidencia = re.match(r'^pedir\s+t[oó]ner\s+(.+)$', texto)
    if coincidencia:
        return ("pedir_toner", coincidencia.group(1).strip())
    return (None, None)

def obtener_stock_toner():
    conexion = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    cursor = conexion.cursor()
    cursor.execute("""
        SELECT nombre, stock_actual
        FROM repuestos
        WHERE UPPER(nombre) LIKE '%TONER%' OR UPPER(nombre) LIKE '%TÓNER%'
        ORDER BY nombre
    """)
    filas = cursor.fetchall()
    conexion.close()
    return filas

def armar_respuesta_stock():
    filas = obtener_stock_toner()
    if not filas:
        return "No encontré tóners registrados en el stock."
    lineas = ["🖨️ Stock de tóner disponible:", ""]
    for fila in filas:
        unidad = "unidad" if fila["stock_actual"] == 1 else "unidades"
        lineas.append(f"• {fila['nombre']}: {fila['stock_actual']} {unidad}")
    return "\n".join(lineas)

def obtener_reclamo_por_id(id_reclamo):
    conexion = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    conexion.row_factory = sqlite3.Row
    cursor = conexion.cursor()
    cursor.execute("""
        SELECT
            r.id, r.descripcion, r.estado, r.trabajo_realizado, r.fecha_resolucion,
            o.nombre AS oficina_nombre, o.piso, o.bloque,
            t.nombre AS tecnico_nombre, t.apellido AS tecnico_apellido
        FROM reclamos r
        JOIN oficinas o ON r.oficina_id = o.id
        JOIN tecnicos t ON r.tecnico_id = t.id
        WHERE r.id = ?
    """, (id_reclamo,))
    fila = cursor.fetchone()
    conexion.close()
    return fila

def armar_respuesta_estado(id_reclamo):
    fila = obtener_reclamo_por_id(id_reclamo)
    if fila is None:
        return f"No encontré ningún reclamo con el número #{id_reclamo}. Verificá el número e intentá de nuevo."
    lineas = [
        f"📋 Reclamo #{fila['id']}",
        f"Descripción: {fila['descripcion']}",
        f"Oficina: {fila['oficina_nombre']} {fila['piso']}/{fila['bloque']}",
        f"Estado: {fila['estado']}",
        f"Técnico asignado: {fila['tecnico_nombre']} {fila['tecnico_apellido']}",
    ]
    if fila["estado"] == "Resuelto":
        if fila["trabajo_realizado"]:
            lineas.append(f"Trabajo realizado: {fila['trabajo_realizado']}")
        if fila["fecha_resolucion"]:
            lineas.append(f"Fecha de resolución: {fila['fecha_resolucion']}")
    return "\n".join(lineas)

def revisar_y_notificar(pagina, conexion_notif):
    todos_los_reclamos = obtener_reclamos_todos()
    registros_notificados = obtener_registros_notificados(conexion_notif)

    reclamos_nuevos = []
    reclamos_reasignados = []

    for reclamo in todos_los_reclamos:
        registro_previo = registros_notificados.get(reclamo["id"])
        if registro_previo is None:
            reclamos_nuevos.append(reclamo)
        else:
            tecnico_id_guardado, tecnico_nombre_guardado = registro_previo
            if tecnico_id_guardado != reclamo["tecnico_id_actual"]:
                reclamos_reasignados.append((reclamo, tecnico_nombre_guardado))

    if not reclamos_nuevos and not reclamos_reasignados:
        return  # nada nuevo, no ensuciamos el log

    registrar(f"Detectado: {len(reclamos_nuevos)} nuevo(s), {len(reclamos_reasignados)} reasignación(es).")

    for reclamo in reclamos_nuevos:
        texto_antes, texto_despues = armar_texto_nuevo(reclamo)
        exito, motivo = enviar_mensaje_con_mencion(pagina, texto_antes, reclamo["tecnico_nombre"], texto_despues)
        if exito:
            nombre_completo = f"{reclamo['tecnico_nombre']} {reclamo['tecnico_apellido']}"
            registrar(f"Reclamo {reclamo['id']} (nuevo) enviado con mención a {nombre_completo}.")
            marcar_notificado(conexion_notif, reclamo["id"], reclamo["tecnico_id_actual"], nombre_completo)
        else:
            registrar(f"ADVERTENCIA: Reclamo {reclamo['id']} (nuevo) NO enviado. Motivo: {motivo}")

    for reclamo, nombre_anterior in reclamos_reasignados:
        texto_antes, texto_despues = armar_texto_reasignado(reclamo, nombre_anterior)
        exito, motivo = enviar_mensaje_con_mencion(pagina, texto_antes, reclamo["tecnico_nombre"], texto_despues)
        if exito:
            nombre_completo = f"{reclamo['tecnico_nombre']} {reclamo['tecnico_apellido']}"
            registrar(f"Reclamo {reclamo['id']} (reasignado de {nombre_anterior}) enviado con mención a {nombre_completo}.")
            marcar_notificado(conexion_notif, reclamo["id"], reclamo["tecnico_id_actual"], nombre_completo)
        else:
            registrar(f"ADVERTENCIA: Reclamo {reclamo['id']} (reasignado) NO enviado. Motivo: {motivo}")

def revisar_mensajes_privados(pagina):
    no_leidos = pagina.locator("[aria-label*='no leído']")
    cantidad = no_leidos.count()

    for i in range(cantidad):
        elemento = no_leidos.nth(i)
        fila = elemento.locator("xpath=ancestor::div[@data-testid='cell-frame-container']").first
        texto_fila = fila.inner_text()
        primera_linea_fila = texto_fila.split("\n")[0].strip()

        if primera_linea_fila in nombres_grupos:
            continue

        if primera_linea_fila not in CONTACTOS_AUTORIZADOS:
            fila.click()  # abrimos igual, para marcarlo como leído y que no quede repitiéndose cada 15s
            pagina.wait_for_timeout(1000)
            registrar(f"Mensaje privado IGNORADO (no autorizado): '{primera_linea_fila}'")
            abrir_grupo(pagina)
            continue

        fila.click()
        pagina.wait_for_timeout(1500)

        mensajes = pagina.locator("span[data-testid='selectable-text']")
        intentos = 0
        while mensajes.count() == 0 and intentos < 6:
            pagina.wait_for_timeout(2000)
            intentos += 1

        if mensajes.count() == 0:
            registrar(f"ADVERTENCIA: no se pudo leer el mensaje privado de '{primera_linea_fila}' tras varios intentos.")
            continue

        ultimo_mensaje = mensajes.last.inner_text()
        tipo, dato = clasificar_comando(ultimo_mensaje)
        registrar(f"Mensaje privado de '{primera_linea_fila}': '{ultimo_mensaje}' → comando: {tipo}")

        if tipo is None:
            enviar_mensaje_simple(pagina, MENSAJE_MENU)
        elif tipo == "estado":
            enviar_mensaje_simple(pagina, armar_respuesta_estado(dato))
        elif tipo == "stock":
            enviar_mensaje_simple(pagina, armar_respuesta_stock())
        elif tipo == "pedir_toner":
            registrar(f"(Todavía no implementado: pedir tóner para serie '{dato}')")
            enviar_mensaje_simple(pagina, "Por ahora esa función todavía no está disponible, estamos trabajando en ella. 🙏")

        pagina.wait_for_timeout(1000)
        abrir_grupo(pagina)
        pagina.wait_for_timeout(1000)

# --- Programa principal (modo continuo) ---

registrar("=== Bot de WhatsApp iniciado en modo continuo ===")

es_primera_vez = not os.path.exists(RUTA_NOTIFICACIONES)
conexion_notif = obtener_conexion_notificaciones()

if es_primera_vez:
    todos_los_reclamos = obtener_reclamos_todos()
    registrar(f"Primera ejecución detectada. Marcando {len(todos_los_reclamos)} reclamos existentes como ya notificados, SIN enviar mensajes...")
    for reclamo in todos_los_reclamos:
        nombre_completo = f"{reclamo['tecnico_nombre']} {reclamo['tecnico_apellido']}"
        marcar_notificado(conexion_notif, reclamo["id"], reclamo["tecnico_id_actual"], nombre_completo)
    registrar("Inicialización completa.")

with sync_playwright() as p:
    contexto = p.chromium.launch_persistent_context(CARPETA_SESION, headless=False)
    pagina = contexto.new_page()
    pagina.goto("https://web.whatsapp.com")
    pagina.wait_for_selector("#pane-side", timeout=30000)

    # Cerramos cualquier modal/aviso emergente que pueda tapar la pantalla
    pagina.wait_for_timeout(2000)
    pagina.keyboard.press("Escape")
    pagina.wait_for_timeout(1000)
    pagina.keyboard.press("Escape")
    pagina.wait_for_timeout(1000)

    abrir_grupo(pagina)
    nombres_grupos = obtener_nombres_de_grupos(pagina)
    registrar(f"Grupos detectados (se van a ignorar): {nombres_grupos}")    
    
    try:
        while True:
            try:
                revisar_mensajes_privados(pagina)
            except Exception as e:
                registrar(f"Error en mensajes privados: {e}")

            if verificar_cambio_de_horario():
                try:
                    verificar_saludo_diario(pagina)
                except Exception as e:
                    registrar(f"Error al enviar saludo diario: {e}")
                try:
                    revisar_y_notificar(pagina, conexion_notif)
                except Exception as error:
                    registrar(f"ERROR durante la revisión: {error}")

            time.sleep(INTERVALO_SEGUNDOS)
    except KeyboardInterrupt:
        registrar("Detenido manualmente (Ctrl+C).")
    finally:
        contexto.close()
        conexion_notif.close()