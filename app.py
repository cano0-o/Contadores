"""
WebMonitor - Contadores Ricoh (MP / IM)
========================================
Aplicacion de escritorio (Flet) para monitorear los contadores de impresion
de equipos Ricoh de las lineas **MP** e **IM** (monocromos y a color) de
Masprint.

Caracteristicas:
- Lista de impresoras persistente en JSON, con alta, edicion y baja.
- Alta/edicion restringida a equipos de linea MP o IM (mono o color); el
  resto de las lineas Ricoh (Aficio "puro", Pro, etc.) no se puede dar de
  alta desde la app, para mantener el monitoreo enfocado en el parque real
  de equipos que se necesita vigilar.
- Cada tarjeta muestra una etiqueta de familia (MP/IM) y si el equipo es a
  color o monocromo, detectado a partir del nombre del modelo.
- Extraccion robusta de contadores desde la interfaz web de cada equipo
  (tabla HTML + fallback por expresiones regulares), mostrando TODOS los
  contadores encontrados, no solo el total.
- La tarjeta principal YA NO muestra un total destacado: solo indica
  cuantos contadores se encontraron. El desglose completo (incluido el
  total, si el equipo lo expone) se ve en el detalle emergente.
- Al abrir la app, consulta automaticamente TODAS las impresoras en paralelo
  (no hay que darle click una por una).
- Numero de serie por equipo.
- Buscador por modelo, IP o numero de serie.

Requisitos: flet, requests, beautifulsoup4  (ver requirements.txt)
Ejecutar con: python main.py
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

import flet as ft
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuracion general / logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("webmonitor")

DATA_FILE = Path(__file__).parent / "impresoras.json"
REQUEST_TIMEOUT = 10  # segundos
MAX_CONSULTAS_SIMULTANEAS = 6  # cuantas impresoras se consultan en paralelo

# Impresoras iniciales: solo se usan la primera vez, si no existe impresoras.json
# Solo se incluyen equipos de linea MP e IM (mono y color), que es el
# alcance soportado por esta version de la app.
EQUIPOS_INICIALES = [
    {"modelo": "Ricoh Aficio MP W3601", "ip": "192.168.1.77", "serie": ""},
    {"modelo": "Ricoh MP 501", "ip": "192.168.1.164", "serie": ""},
    {"modelo": "Ricoh MP 5055", "ip": "192.168.1.35", "serie": ""},
    {"modelo": "Ricoh MP 4055", "ip": "192.168.1.201", "serie": ""},
    {"modelo": "Ricoh MP 4055", "ip": "192.168.1.34", "serie": ""},
    {"modelo": "Ricoh IM C4500", "ip": "192.168.1.25", "serie": ""},
    {"modelo": "Ricoh IM C4500", "ip": "192.168.1.36", "serie": ""},
]

IP_REGEX = re.compile(
    r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)

# --------------------------------------------------------------------------
# Deteccion de familia (MP / IM) y variante color/mono a partir del modelo
# --------------------------------------------------------------------------
#
# Regla practica de Ricoh: la letra "C" pegada al numero de modelo indica
# la variante a color (ej. "MP C4504", "IM C4500"); sin la "C" es mono
# (ej. "MP 4055", "IM 350"). Se usan \b para no confundir "MP"/"IM" con
# substrings de otras palabras (p.ej. "IM" dentro de "PRIME").

REGEX_FAMILIA = re.compile(r"\b(MP|IM)\b", re.IGNORECASE)
REGEX_MODELO_COLOR = re.compile(r"\b(MP|IM)\s*C\d", re.IGNORECASE)

FAMILIAS_SOPORTADAS = ("MP", "IM")


def familia_de_modelo(modelo: str) -> Optional[str]:
    """Devuelve 'MP' o 'IM' si el modelo pertenece a esa familia, si no None."""
    coincidencia = REGEX_FAMILIA.search(modelo)
    return coincidencia.group(1).upper() if coincidencia else None


def es_modelo_color(modelo: str) -> bool:
    """True si el modelo corresponde a la variante a color de MP/IM."""
    return bool(REGEX_MODELO_COLOR.search(modelo))


def es_familia_soportada(modelo: str) -> bool:
    """True si el modelo pertenece a una de las familias soportadas (MP/IM)."""
    return familia_de_modelo(modelo) is not None


# Palabras clave -> icono, para que el detalle de contadores sea mas intuitivo
# de leer de un vistazo (se compara contra la etiqueta en minusculas).
# Se incluyen variantes en ingles porque algunos firmwares de MP/IM
# devuelven las etiquetas de color/BN en ingles aunque el resto este en es.
ICONOS_POR_PALABRA_CLAVE: list[tuple[str, str]] = [
    ("total", ft.Icons.SUMMARIZE),
    ("full color", ft.Icons.PALETTE),
    ("color", ft.Icons.PALETTE),
    ("black & white", ft.Icons.CIRCLE),
    ("black and white", ft.Icons.CIRCLE),
    ("negro", ft.Icons.CIRCLE),
    ("mono", ft.Icons.CIRCLE),
    ("b/n", ft.Icons.CIRCLE),
    ("black", ft.Icons.CIRCLE),
    ("copia", ft.Icons.CONTENT_COPY),
    ("copier", ft.Icons.CONTENT_COPY),
    ("impres", ft.Icons.PRINT),
    ("printer", ft.Icons.PRINT),
    ("escane", ft.Icons.SCANNER),
    ("scan", ft.Icons.SCANNER),
    ("fax", ft.Icons.FAX),
    ("facsimile", ft.Icons.FAX),
    ("duplex", ft.Icons.FLIP),
    ("a3", ft.Icons.CROP_LANDSCAPE),
    ("a4", ft.Icons.CROP_PORTRAIT),
]


def icono_para_etiqueta(etiqueta: str) -> str:
    """Devuelve un icono representativo segun palabras clave en la etiqueta."""
    etiqueta_lower = etiqueta.lower()
    for palabra, icono in ICONOS_POR_PALABRA_CLAVE:
        if palabra in etiqueta_lower:
            return icono
    return ft.Icons.BAR_CHART


# --------------------------------------------------------------------------
# Modelo de datos
# --------------------------------------------------------------------------

@dataclass
class Impresora:
    """Representa un equipo Ricoh (MP/IM) monitoreado."""

    modelo: str
    ip: str
    serie: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def clave_busqueda(self) -> str:
        """Cadena usada por el buscador (modelo + ip + serie, en minusculas)."""
        return f"{self.modelo} {self.ip} {self.serie}".lower()


# --------------------------------------------------------------------------
# Persistencia (JSON)
# --------------------------------------------------------------------------

class AlmacenImpresoras:
    """
    Maneja la carga y el guardado persistente de la lista de impresoras.

    Los datos se guardan en un archivo JSON junto al script (impresoras.json).
    Si el archivo no existe o esta corrupto, se crea/recupera con la lista
    inicial (EQUIPOS_INICIALES), para que la app nunca truene por datos malos.
    """

    def __init__(self, ruta: Path = DATA_FILE):
        self.ruta = ruta
        self.impresoras: list[Impresora] = []
        self._cargar()

    def _cargar(self) -> None:
        if self.ruta.exists():
            try:
                bruto = json.loads(self.ruta.read_text(encoding="utf-8"))
                self.impresoras = [Impresora(**item) for item in bruto]
                logger.info("Cargadas %d impresoras desde %s", len(self.impresoras), self.ruta)
                return
            except (json.JSONDecodeError, TypeError) as e:
                logger.error("No se pudo leer %s (%s); se usara la lista inicial.", self.ruta, e)

        self.impresoras = [Impresora(**eq) for eq in EQUIPOS_INICIALES]
        self._guardar()

    def _guardar(self) -> None:
        datos = [asdict(imp) for imp in self.impresoras]
        self.ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Guardadas %d impresoras en %s", len(self.impresoras), self.ruta)

    def listar(self) -> list[Impresora]:
        return self.impresoras

    def agregar(self, modelo: str, ip: str, serie: str = "") -> Impresora:
        nueva = Impresora(modelo=modelo.strip(), ip=ip.strip(), serie=serie.strip())
        self.impresoras.append(nueva)
        self._guardar()
        return nueva

    def editar(self, id_impresora: str, modelo: str, ip: str, serie: str) -> Optional[Impresora]:
        for imp in self.impresoras:
            if imp.id == id_impresora:
                imp.modelo = modelo.strip()
                imp.ip = ip.strip()
                imp.serie = serie.strip()
                self._guardar()
                return imp
        return None

    def eliminar(self, id_impresora: str) -> None:
        self.impresoras = [imp for imp in self.impresoras if imp.id != id_impresora]
        self._guardar()

    def ip_en_uso(self, ip: str, excluir_id: Optional[str] = None) -> bool:
        return any(imp.ip == ip and imp.id != excluir_id for imp in self.impresoras)


# --------------------------------------------------------------------------
# Extraccion de contadores (scraping)
# --------------------------------------------------------------------------
#
# El firmware de Ricoh no arma esta pagina igual en todos los modelos:
# equipos viejos (Aficio) tienden a usar bloques "Etiqueta:Valor" con la
# clase CSS ".staticProp", mientras que equipos MP/IM mas nuevos suelen usar
# tablas HTML normales, con filas separadas para "Total", "Full Color" /
# "Twin Color" y "Black & White" en los modelos a color. Por eso se
# combinan tres estrategias de lectura en cada consulta (no se detienen en
# la primera que funciona), y ademas se reintenta con distintas rutas
# (idioma es/en, http/https) por si el equipo no expone la ruta por defecto.
#
# IMPORTANTE: varios equipos (p.ej. la MP 4055 cuando tiene habilitado
# "Solo comunicacion cifrada" / SSL forzado en Configuracion de Red) NO
# aceptan HTTP en absoluto y solo responden por HTTPS con certificado
# autofirmado. Por eso el ciclo de abajo NUNCA debe abortar por completo
# solo porque una de las combinaciones (p.ej. HTTP) truena por timeout o
# conexion rechazada: debe anotar el error y seguir probando el resto de
# las combinaciones (en particular HTTPS) antes de rendirse.

IDIOMAS_CANDIDATOS = ("es", "en")
ESQUEMAS_CANDIDATOS = ("https", "http")

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:  # pragma: no cover - urllib3 siempre viene con requests
    pass


def _urls_candidatas(ip: str) -> list[str]:
    """
    Genera las rutas a intentar. HTTPS va primero: es el esquema que mas
    equipos aceptan siempre (incluidos los que tienen SSL forzado), mientras
    que HTTP puede estar deshabilitado por completo en varios modelos
    (como la MP 4055 con "solo comunicacion cifrada" activado). Si un
    equipo sí acepta HTTP, de todas formas se prueba como respaldo.
    """
    return [
        f"{esquema}://{ip}/web/guest/{idioma}/websys/status/getUnificationCounter.cgi"
        for esquema in ESQUEMAS_CANDIDATOS
        for idioma in IDIOMAS_CANDIDATOS
    ]


def limpiar_valor_numerico(texto: str) -> Optional[str]:
    """
    Extrae solo los digitos de un texto (soporta separadores de miles con
    coma o punto, y texto pegado como 'sheets'/'hojas'). Devuelve None si
    no quedan digitos o si el resultado es demasiado largo para ser un
    contador real (senal de que se coló texto que no era un numero).
    """
    solo_digitos = re.sub(r"[^\d]", "", texto)
    if not solo_digitos or len(solo_digitos) > 12:
        return None
    return solo_digitos


def _agregar_contador(contadores: dict[str, str], etiqueta: str, valor: str) -> None:
    """
    Agrega un contador al diccionario sin perder datos por colisión de
    nombres entre las distintas estrategias de lectura: si la etiqueta ya
    existe con el MISMO valor, se ignora (es el mismo dato visto dos
    veces). Si existe con un valor DISTINTO, se agrega con un sufijo
    numerado para conservar ambos.
    """
    etiqueta = re.sub(r"\s+", " ", etiqueta).strip().strip(":").strip()
    if not etiqueta:
        return

    prefijo = etiqueta.lower()
    for clave_existente, valor_existente in contadores.items():
        clave_base = re.sub(r"\s\(\d+\)$", "", clave_existente).lower()
        if clave_base == prefijo and valor_existente == valor:
            return  # mismo dato, ya lo tenemos

    clave_final = etiqueta
    n = 2
    while clave_final in contadores:
        clave_final = f"{etiqueta} ({n})"
        n += 1
    contadores[clave_final] = valor


def _es_posible_encabezado(tag) -> bool:
    if getattr(tag, "name", None) in ("h1", "h2", "h3", "h4", "caption", "legend"):
        return True
    clases = tag.get("class") or [] if hasattr(tag, "get") else []
    return any(("title" in c.lower() or "head" in c.lower() or "categ" in c.lower()) for c in clases)


def _etiqueta_contextual(elemento, indice: int) -> str:
    """
    Intenta describir a que función pertenece un contador ".staticProp"
    (Copiadora, Impresora, Escáner, Fax...) usando el encabezado más
    cercano hacia atrás en el HTML. Si no encuentra nada usable, regresa
    un nombre genérico numerado para no perder el dato de todas formas.
    """
    try:
        previo = elemento.find_previous(_es_posible_encabezado)
    except Exception:
        previo = None
    if previo:
        texto = previo.get_text(strip=True)
        if texto and len(texto) < 40:
            return texto
    return f"Contador {indice}"


def _extraer_contadores_de_html(html: str) -> dict[str, str]:
    """Corre las tres estrategias de lectura sobre un HTML ya descargado."""
    soup = BeautifulSoup(html, "html.parser")
    contadores: dict[str, str] = {}

    # --- Estrategia 1: bloques ".staticProp" con texto "Etiqueta:Valor" ---
    for indice, elemento in enumerate(soup.select(".staticProp"), start=1):
        texto = elemento.get_text(separator=" ", strip=True)
        if ":" not in texto:
            continue
        etiqueta_inline, _, valor_texto = texto.partition(":")
        etiqueta_inline = re.sub(r"\s+", " ", etiqueta_inline).strip()
        valor = limpiar_valor_numerico(valor_texto)
        if not valor:
            continue
        contexto = _etiqueta_contextual(elemento, indice)
        if etiqueta_inline and etiqueta_inline.lower() != contexto.lower():
            etiqueta = f"{contexto} - {etiqueta_inline}" if contexto else etiqueta_inline
        else:
            etiqueta = contexto or etiqueta_inline or f"Contador {indice}"
        _agregar_contador(contadores, etiqueta, valor)

    # --- Estrategia 2: filas de tabla, usando la ultima celda "tipo valor" ---
    # Una celda cuenta como valor si EMPIEZA con un dígito (admite texto
    # pegado despues, como "5.678 hojas" o "1,234 pages"); así no hace
    # falta que la celda sea puramente numérica para reconocerla. Este es
    # el formato mas comun en las paginas de contador de equipos MP/IM.
    empieza_con_digito = re.compile(r"^\d")
    for fila in soup.find_all("tr"):
        celdas = fila.find_all(["td", "th"])
        if len(celdas) < 2:
            continue
        textos = [c.get_text(strip=True) for c in celdas]
        for i in range(len(textos) - 1, 0, -1):
            candidato = textos[i]
            if not candidato or not empieza_con_digito.match(candidato):
                continue
            valor = limpiar_valor_numerico(candidato)
            if not valor:
                continue
            etiqueta = " ".join(
                t for t in textos[:i] if t and not empieza_con_digito.match(t)
            )
            if etiqueta:
                _agregar_contador(contadores, etiqueta, valor)
            break

    # --- Estrategia 3: texto libre (ultimo recurso) ---
    if not contadores:
        texto_completo = soup.get_text()
        coincidencias = re.findall(
            r"([a-zA-ZáéíóúÁÉÍÓÚñÑ/\s]+)[:\s]+([\d,.]+)", texto_completo
        )
        for etiqueta, valor_texto in coincidencias:
            etiqueta_limpia = re.sub(r"\s+", " ", etiqueta).strip()
            valor = limpiar_valor_numerico(valor_texto)
            if len(etiqueta_limpia) > 2 and valor:
                _agregar_contador(contadores, etiqueta_limpia, valor)

    return contadores


def obtener_datos_impresora(ip: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """
    Consulta la página de contador unificado de una impresora Ricoh MP/IM y
    devuelve TODOS los pares (etiqueta -> valor numérico) que encuentre:
    copias, impresiones, escaneos, fax, por color/B&N, por función, etc.
    -- lo que exponga cada modelo -- no solo el total.

    Prueba varias rutas (idioma es/en, https/http) hasta encontrar una que
    responda con datos, ya que no todos los firmwares exponen la misma
    ruta ni aceptan el mismo esquema (varios equipos, como la MP 4055 con
    "solo comunicacion cifrada" activado, rechazan HTTP por completo).

    A diferencia de una version anterior, esta funcion YA NO se rinde en
    cuanto una sola combinacion de esquema/idioma falla por timeout o
    conexion rechazada: sigue probando el resto de las combinaciones y
    solo reporta error si NINGUNA de ellas logro conectar.

    Retorna:
        {"success": True,  "datos": {"Copiadora - Total": "12345", ...}}
        {"success": False, "error": "mensaje legible para mostrar en la UI"}
    """
    if not ip:
        return {"success": False, "error": "Falta la IP"}

    ultimo_error: Optional[str] = None
    hubo_conexion = False  # True si al menos una URL respondio algo (aunque sea error HTTP)

    for url in _urls_candidatas(ip):
        es_https = url.startswith("https")
        try:
            response = requests.get(url, timeout=timeout, verify=not es_https)
        except requests.exceptions.SSLError as e:
            # Certificado autofirmado rechazado u otro problema de TLS:
            # se anota y se sigue probando (p.ej. la variante HTTP, o el
            # otro idioma), en vez de abortar todo el ciclo.
            ultimo_error = f"Error SSL al conectar por {url}: {e}"
            continue
        except requests.exceptions.Timeout:
            ultimo_error = f"Tiempo de espera agotado en {url}"
            continue
        except requests.exceptions.ConnectionError:
            ultimo_error = f"No se pudo conectar en {url} (puerto cerrado o esquema no soportado)."
            continue
        except requests.exceptions.RequestException as e:
            ultimo_error = f"Error de red ({url}): {e}"
            continue

        hubo_conexion = True

        if response.status_code != 200:
            ultimo_error = f"El equipo respondió con error HTTP {response.status_code} en {url}"
            continue

        contadores = _extraer_contadores_de_html(response.text)
        if contadores:
            return {"success": True, "datos": contadores}
        ultimo_error = "No se encontraron datos numéricos en la página del equipo."

    if not hubo_conexion:
        return {
            "success": False,
            "error": f"No hay conexión con {ip}. Verifica que la IP sea correcta y que el equipo esté encendido.",
        }
    return {"success": False, "error": ultimo_error or "No se pudo obtener información del equipo."}


def formatear_numero(valor: str) -> str:
    """Formatea '12345' como '12.345' (separador de miles estilo ES/MX)."""
    try:
        return f"{int(valor):,}".replace(",", ".")
    except ValueError:
        return valor


def extraer_total(datos: dict[str, str]) -> Optional[tuple[str, str]]:
    """Busca la entrada que represente el total general entre los contadores."""
    # Preferencia 1: una etiqueta que sea "total" a secas (o casi)
    for etiqueta, valor in datos.items():
        if etiqueta.strip().lower() in {"total", "total general", "gran total"}:
            return etiqueta, valor
    # Preferencia 2: cualquier etiqueta que contenga "total"
    for etiqueta, valor in datos.items():
        if "total" in etiqueta.lower():
            return etiqueta, valor
    return None


# --------------------------------------------------------------------------
# Interfaz (Flet)
# --------------------------------------------------------------------------

def main(page: ft.Page) -> None:
    page.title = "WebMonitor — Contadores Ricoh MP/IM"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#f4f7fb"
    page.padding = 30
    page.window.width = 1150
    page.window.height = 780

    almacen = AlmacenImpresoras()
    executor = ThreadPoolExecutor(max_workers=MAX_CONSULTAS_SIMULTANEAS)

    titulo = ft.Text("Consulta de contadores — Sucursal México", size=28,
                      weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
    subtitulo = ft.Text(
        "Equipos Ricoh línea MP e IM (mono y color). Al abrir, se consultan todos automáticamente.",
        size=16, color=ft.Colors.BLACK54, text_align=ft.TextAlign.CENTER,
    )

    grid = ft.GridView(expand=True, runs_count=3, max_extent=350,
                        child_aspect_ratio=0.82, spacing=20, run_spacing=20)

    # Guarda, por tarjeta, la funcion que dispara su propia consulta —
    # asi se puede reutilizar tanto para la carga automatica al inicio
    # como para el botón "Actualizar todas".
    disparadores_extraccion: list[Callable[[], None]] = []

    # ---- Dialogo de detalle (todos los contadores de un equipo) ----
    # Se inicializa con placeholders (Flet exige title/content/actions desde
    # la creación del control, aunque esté cerrado) y se rellena al abrirlo.
    detalle_dialogo = ft.AlertDialog(
        modal=True,
        title=ft.Text(""),
        content=ft.Text(""),
        actions=[],
    )
    page.overlay.append(detalle_dialogo)

    def cerrar_detalle(e=None):
        detalle_dialogo.open = False
        page.update()

    def abrir_detalle(imp: Impresora, datos: dict[str, str]):
        filas: list[ft.Control] = []
        pares_ordenados = sorted(datos.items(), key=lambda kv: ("total" not in kv[0].lower(), kv[0]))

        for idx, (etiqueta, valor) in enumerate(pares_ordenados):
            es_total = "total" in etiqueta.lower()
            filas.append(
                ft.Container(
                    padding=ft.Padding(left=14, right=14, top=10, bottom=10),
                    bgcolor=ft.Colors.BLUE_50 if es_total else (
                        ft.Colors.GREY_100 if idx % 2 == 0 else ft.Colors.WHITE
                    ),
                    border_radius=8,
                    content=ft.Row([
                        ft.Icon(icono_para_etiqueta(etiqueta), size=18,
                                color=ft.Colors.BLUE_700 if es_total else ft.Colors.BLACK45),
                        ft.Text(etiqueta, size=14,
                                weight=ft.FontWeight.BOLD if es_total else ft.FontWeight.NORMAL,
                                expand=True),
                        ft.Text(formatear_numero(valor), size=16 if es_total else 14,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.BLUE_700 if es_total else ft.Colors.BLACK87),
                    ]),
                )
            )

        detalle_dialogo.title = ft.Row([
            ft.Icon(ft.Icons.PRINT, color=ft.Colors.BLUE_700),
            ft.Column([
                ft.Text(imp.modelo, size=16, weight=ft.FontWeight.BOLD),
                ft.Text(imp.ip, size=12, color=ft.Colors.BLACK54, font_family="monospace"),
            ], spacing=0, tight=True),
        ], spacing=10)
        detalle_dialogo.content = ft.Container(
            width=460,
            content=ft.Column(filas, spacing=6, scroll=ft.ScrollMode.AUTO, tight=True),
        )
        detalle_dialogo.actions = [ft.TextButton("Cerrar", on_click=cerrar_detalle)]
        detalle_dialogo.actions_alignment = ft.MainAxisAlignment.END
        detalle_dialogo.open = True
        page.update()

    # ---- Dialogo agregar/editar ----
    campo_modelo = ft.TextField(label="Modelo (debe ser línea MP o IM)", width=350)
    campo_ip = ft.TextField(label="Dirección IP", width=350)
    campo_serie = ft.TextField(label="Número de serie", width=350)
    texto_error_dialogo = ft.Text("", color=ft.Colors.RED_600, size=12)
    impresora_en_edicion: dict[str, Optional[str]] = {"id": None}

    def cerrar_dialogo(e=None):
        dialogo.open = False
        page.update()

    def guardar_dialogo(e):
        modelo = campo_modelo.value or ""
        ip = campo_ip.value or ""
        serie = campo_serie.value or ""

        if not modelo.strip() or not ip.strip():
            texto_error_dialogo.value = "Modelo e IP son obligatorios."
            page.update()
            return
        if not es_familia_soportada(modelo):
            texto_error_dialogo.value = (
                "Este monitor solo soporta equipos Ricoh de línea MP o IM "
                "(ej. 'MP 4055', 'MP C4504', 'IM C4500')."
            )
            page.update()
            return
        if not IP_REGEX.match(ip.strip()):
            texto_error_dialogo.value = "La IP no tiene un formato válido."
            page.update()
            return
        if almacen.ip_en_uso(ip.strip(), excluir_id=impresora_en_edicion["id"]):
            texto_error_dialogo.value = "Ya existe una impresora con esa IP."
            page.update()
            return

        es_nueva = impresora_en_edicion["id"] is None
        if es_nueva:
            nueva = almacen.agregar(modelo, ip, serie)
        else:
            almacen.editar(impresora_en_edicion["id"], modelo, ip, serie)

        cerrar_dialogo()
        refrescar_grid()
        if es_nueva:
            # Consulta de inmediato la impresora recien agregada
            for disparador in disparadores_extraccion[-1:]:
                executor.submit(disparador)

    dialogo = ft.AlertDialog(
        modal=True,
        title=ft.Text("Impresora"),
        content=ft.Column([campo_modelo, campo_ip, campo_serie, texto_error_dialogo],
                           tight=True, spacing=10),
        actions=[
            ft.TextButton("Cancelar", on_click=cerrar_dialogo),
            ft.Button("Guardar", on_click=guardar_dialogo),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(dialogo)

    def abrir_dialogo_agregar(e=None):
        impresora_en_edicion["id"] = None
        campo_modelo.value = ""
        campo_ip.value = ""
        campo_serie.value = ""
        texto_error_dialogo.value = ""
        dialogo.title = ft.Text("Agregar impresora (línea MP/IM)")
        dialogo.open = True
        page.update()

    def abrir_dialogo_editar(imp: Impresora):
        impresora_en_edicion["id"] = imp.id
        campo_modelo.value = imp.modelo
        campo_ip.value = imp.ip
        campo_serie.value = imp.serie
        texto_error_dialogo.value = ""
        dialogo.title = ft.Text("Editar impresora")
        dialogo.open = True
        page.update()

    # ---- Confirmacion de borrado ----
    confirm_dialogo = ft.AlertDialog(modal=True, title=ft.Text("Confirmar"))
    page.overlay.append(confirm_dialogo)

    def confirmar_eliminar(imp: Impresora):
        def si(e):
            almacen.eliminar(imp.id)
            confirm_dialogo.open = False
            page.update()
            refrescar_grid()

        def no(e):
            confirm_dialogo.open = False
            page.update()

        confirm_dialogo.content = ft.Text(f"¿Eliminar '{imp.modelo}' ({imp.ip})?")
        confirm_dialogo.actions = [
            ft.TextButton("Cancelar", on_click=no),
            ft.Button("Eliminar", on_click=si, bgcolor=ft.Colors.RED_600, color=ft.Colors.WHITE),
        ]
        confirm_dialogo.open = True
        page.update()

    # ---- Tarjeta de impresora ----
    def crear_tarjeta(imp: Impresora) -> ft.Card:
        punto_estado = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY_400, size=12)
        texto_estado = ft.Text("Consultando…", size=12, color=ft.Colors.BLACK54)
        anillo_carga = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=True)

        # Etiquetas de familia (MP/IM) y variante color/mono, deducidas del modelo.
        familia = familia_de_modelo(imp.modelo) or "?"
        es_color = es_modelo_color(imp.modelo)
        badge_familia = ft.Container(
            padding=ft.Padding(left=8, right=8, top=2, bottom=2),
            bgcolor=ft.Colors.INDIGO_50,
            border_radius=12,
            content=ft.Text(familia, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.INDIGO_700),
        )
        badge_color = ft.Container(
            padding=ft.Padding(left=8, right=8, top=2, bottom=2),
            bgcolor=ft.Colors.PINK_50 if es_color else ft.Colors.GREY_200,
            border_radius=12,
            content=ft.Row([
                ft.Icon(ft.Icons.PALETTE if es_color else ft.Icons.CIRCLE, size=12,
                        color=ft.Colors.PINK_600 if es_color else ft.Colors.BLACK45),
                ft.Text("Color" if es_color else "B/N", size=11, weight=ft.FontWeight.BOLD,
                        color=ft.Colors.PINK_700 if es_color else ft.Colors.BLACK54),
            ], spacing=4, tight=True),
        )

        # Caja de resultado resumido dentro de la tarjeta. Ya NO muestra el
        # total destacado: solo indica cuantos contadores se encontraron.
        # El desglose completo (con el total, si el equipo lo expone) vive
        # en el detalle emergente ("Ver todos los contadores").
        caja_resumen = ft.Container(
            padding=14,
            border_radius=10,
            bgcolor=ft.Colors.GREY_100,
            alignment=ft.Alignment.CENTER,
            content=ft.Text("Consultando…", size=13, color=ft.Colors.BLACK54,
                             text_align=ft.TextAlign.CENTER),
        )
        boton_detalle = ft.OutlinedButton(
            "Ver todos los contadores", icon=ft.Icons.LIST_ALT, visible=False,
            on_click=lambda e: None,  # se define abajo, tras tener los datos
        )
        boton_refrescar = ft.IconButton(ft.Icons.REFRESH, icon_size=18, tooltip="Actualizar contador")

        def poner_estado_cargando():
            punto_estado.color = ft.Colors.GREY_400
            texto_estado.value = "Consultando…"
            anillo_carga.visible = True
            boton_refrescar.disabled = True
            boton_detalle.visible = False
            caja_resumen.bgcolor = ft.Colors.GREY_100
            caja_resumen.content = ft.Row(
                [ft.ProgressRing(width=16, height=16, stroke_width=2),
                 ft.Text("Consultando…", size=13, color=ft.Colors.BLACK54)],
                alignment=ft.MainAxisAlignment.CENTER, spacing=10,
            )

        def poner_estado_exito(datos: dict[str, str]):
            punto_estado.color = ft.Colors.GREEN_400
            texto_estado.value = "Disponible"
            anillo_carga.visible = False
            boton_refrescar.disabled = False

            caja_resumen.bgcolor = ft.Colors.BLUE_50
            caja_resumen.content = ft.Row([
                ft.Icon(ft.Icons.CHECKLIST, color=ft.Colors.BLUE_700, size=20),
                ft.Text(f"{len(datos)} contadores disponibles", size=14,
                        color=ft.Colors.BLUE_900, weight=ft.FontWeight.BOLD),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8)

            boton_detalle.visible = True
            boton_detalle.text = f"Ver todos los contadores ({len(datos)})"
            boton_detalle.on_click = lambda e, i=imp, d=datos: abrir_detalle(i, d)

        def poner_estado_error(mensaje: str):
            punto_estado.color = ft.Colors.RED_400
            texto_estado.value = "Error"
            anillo_carga.visible = False
            boton_refrescar.disabled = False
            boton_detalle.visible = False
            caja_resumen.bgcolor = ft.Colors.RED_50
            caja_resumen.content = ft.Row([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_600, size=18),
                ft.Text(mensaje, size=12, color=ft.Colors.RED_700, expand=True),
            ], spacing=8)

        def ejecutar_consulta():
            """Corre en un hilo aparte para no congelar la interfaz."""
            poner_estado_cargando()
            page.update()

            resultado = obtener_datos_impresora(imp.ip)

            if resultado["success"]:
                poner_estado_exito(resultado["datos"])
            else:
                poner_estado_error(resultado["error"])
            page.update()

        boton_refrescar.on_click = lambda e: executor.submit(ejecutar_consulta)
        disparadores_extraccion.append(ejecutar_consulta)

        fila_serie = (
            ft.Text(f"S/N: {imp.serie}", size=11, color=ft.Colors.BLACK45)
            if imp.serie else ft.Text("S/N: no registrado", size=11, color=ft.Colors.BLACK26)
        )

        return ft.Card(
            elevation=4,
            data=imp.clave_busqueda(),
            content=ft.Container(
                padding=18,
                bgcolor=ft.Colors.WHITE,
                border_radius=10,
                content=ft.Column([
                    ft.Row([
                        punto_estado, texto_estado,
                        ft.Container(expand=True),
                        boton_refrescar,
                        ft.IconButton(ft.Icons.EDIT, icon_size=16,
                                      on_click=lambda e, i=imp: abrir_dialogo_editar(i)),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                      icon_color=ft.Colors.RED_400,
                                      on_click=lambda e, i=imp: confirmar_eliminar(i)),
                    ]),
                    ft.Text(imp.modelo, size=17, weight=ft.FontWeight.BOLD),
                    ft.Row([badge_familia, badge_color], spacing=6),
                    ft.Row([
                        ft.Container(
                            content=ft.Text(imp.ip, font_family="monospace", size=12),
                            bgcolor=ft.Colors.GREY_200, padding=5, border_radius=5,
                        ),
                        fila_serie,
                    ], spacing=10),
                    ft.Container(height=4),
                    caja_resumen,
                    boton_detalle,
                ], horizontal_alignment=ft.CrossAxisAlignment.STRETCH, spacing=8),
            ),
        )

    def refrescar_grid():
        disparadores_extraccion.clear()
        grid.controls = [crear_tarjeta(imp) for imp in almacen.listar()]
        page.update()

    def consultar_todas(e=None):
        for disparador in list(disparadores_extraccion):
            executor.submit(disparador)

    # ---- Buscador ----
    def filtrar_equipos(e):
        query = (e.control.value or "").lower()
        for tarjeta in grid.controls:
            tarjeta.visible = query in tarjeta.data
        page.update()

    buscador = ft.TextField(
        hint_text="Buscar por modelo, IP o número de serie...",
        prefix_icon=ft.Icons.SEARCH, on_change=filtrar_equipos,
        width=380, border_radius=20, bgcolor=ft.Colors.WHITE,
    )

    boton_agregar = ft.Button(
        "Agregar impresora", icon=ft.Icons.ADD, on_click=abrir_dialogo_agregar,
        bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE,
    )
    boton_actualizar_todas = ft.Button(
        "Actualizar todas", icon=ft.Icons.REFRESH, on_click=consultar_todas,
        bgcolor=ft.Colors.BLUE_700, color=ft.Colors.WHITE,
    )

    refrescar_grid()

    page.add(
        ft.Column([
            titulo, subtitulo,
            ft.Row([buscador, boton_actualizar_todas, boton_agregar],
                   alignment=ft.MainAxisAlignment.CENTER, spacing=15),
            ft.Container(height=10),
            grid,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True)
    )

    # Consulta automatica de TODAS las impresoras al abrir la app,
    # en paralelo (hasta MAX_CONSULTAS_SIMULTANEAS a la vez) para que
    # el usuario vea los totales sin tener que darle click uno por uno.
    consultar_todas()


if __name__ == "__main__":
    ft.run(main)