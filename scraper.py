"""
Scraper de futbolenvivochile.com para el abuelo de Gonz.

Enfoque (v3):
  1. Cada partido futuro (más allá del inmediato) aparece en tablas HTML
     reales dentro de la página, una tabla por fecha. Se leen directamente
     esas tablas: hora | competición | equipo1 | equipo2 | canales.
  2. El partido MÁS INMEDIATO de cada equipo/competición no siempre está
     en esas tablas (a veces solo aparece en una frase de resumen tipo
     "El próximo partido que podrás ver será X - Y ... transmitido por
     Z, W"). Esa frase se parsea aparte con una expresión regular.

Se queda solo con partidos que se transmiten por algún canal/plataforma
que el abuelo SÍ tiene:
  - Disney+ Premium
  - DGO (DirecTV GO) - incluye su canal de deporte y TNT Sports Premium
  - TNT Sports Premium / TNT Sports Premium HD
  - ESPN (los distintos ESPN)
  - TyC Sports (Internacional)

Genera:
  - docs/data.json   -> datos crudos filtrados (para la página web)
"""

import json
import re
import time
import traceback
from datetime import datetime, date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.futbolenvivochile.com"

# --- Configuración: qué equipos seguir --------------------------------------
EQUIPOS = {
    "Colo Colo": {"slug": "equipo/colo-colo", "nombre_filtro": "Colo Colo"},
    "Manchester City": {"slug": "equipo/manchester-city", "nombre_filtro": "Manchester City"},
    "FC Barcelona": {"slug": "equipo/fc-barcelona", "nombre_filtro": "Barcelona"},
    "Selección Chilena": {"slug": "equipo/chile", "nombre_filtro": "Chile"},

    # Equipos agregados
    "Real Madrid": {"slug": "equipo/real-madrid", "nombre_filtro": "Real Madrid"},
    "Liverpool": {"slug": "equipo/liverpool", "nombre_filtro": "Liverpool"},
    "Arsenal": {"slug": "equipo/arsenal", "nombre_filtro": "Arsenal"},
    # Ojo: en este sitio el equipo aparece como "Manchester Utd.", no
    # "Manchester United" completo, por eso el filtro busca "Manchester Utd".
    "Manchester United": {"slug": "equipo/manchester-utd", "nombre_filtro": "Manchester Utd"},
    "Bayern Munich": {"slug": "equipo/bayern-munich", "nombre_filtro": "Bayern"},
    "Real Betis": {"slug": "equipo/real-betis", "nombre_filtro": "Real Betis"},
    "Inter de Milan": {"slug": "equipo/inter-milan", "nombre_filtro": "Inter Milan"},
    "Boca Juniors": {"slug": "equipo/boca-juniors", "nombre_filtro": "Boca Juniors"},
}

COMPETICIONES = {
    "Champions League": "competicion/liga-campeones",
    "Copa del Rey": "competicion/copa-del-rey",

    # Ligas/competiciones agregadas
    "Premier League": "competicion/premier-league",
    "La Liga": "competicion/la-liga",
    "Europa League": "competicion/europa-league",
    # OJO: el slug de la liga chilena (probablemente
    # "competicion/campeonato-itau" o similar, el sitio la llama
    # "Campeonato Itaú") todavía no está confirmado 100% -> verificar
    # entrando directo a futbolenvivochile.com antes de activar esta línea.
    # "Liga de Primera (Chile)": "competicion/campeonato-itau",
}

# --- Configuración: qué canales tiene el abuelo -----------------------------
CANALES_DISPONIBLES = [
    "disney+",
    "disney plus",
    "dgo",
    "directv go",
    "tnt sports premium",
    "tnt sports",
    "espn",
    "tyc sports",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

DATE_RE = re.compile(
    r"(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)[,\s]+(\d{1,2})-(\d{1,2})-(\d{4})",
    re.IGNORECASE,
)

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
PROXIMO_PARTIDO_FECHA_RE = re.compile(
    r"(\d{1,2}) de (\w+) de (\d{4}).*?(\d{1,2}):(\d{2})", re.IGNORECASE | re.DOTALL
)

# Frase completa que la web repite en cada página de equipo/competición.
# Se busca sobre el TEXTO PLANO (sin depender de qué partes estén en negrita,
# porque eso varía levemente entre "podrás ver" / "se podrá ver").
PROXIMO_PARTIDO_RE = re.compile(
    r"pr[oó]ximo partido que (?:podr[aá]s ver|se podr[aá] ver) ser[aá] el (.+?) "
    r"que se disputar\w+ el pr[oó]ximo (.+? a las \d{1,2}:\d{2}) "
    r"y que ser[aá] transmitido por (.+?)\.",
    re.IGNORECASE | re.DOTALL,
)


def normalizar(texto: str) -> str:
    texto = texto.lower()
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto


def canal_disponible(nombre_canal: str) -> bool:
    n = normalizar(nombre_canal)
    return any(c in n for c in CANALES_DISPONIBLES)


def equipo_coincide(nombre_buscado: str, equipos_encontrados) -> bool:
    n_buscado = normalizar(nombre_buscado)
    for e in equipos_encontrados:
        n_e = normalizar(e)
        if n_buscado in n_e or n_e in n_buscado:
            return True
    return False


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def extraer_de_tablas(soup: BeautifulSoup, fuente: str):
    """Recorre todas las <table> de la página: cada una representa un día,
    con filas de partidos [hora | competición | equipo1 | equipo2 | canales]."""
    partidos = []

    for tabla in soup.find_all("table"):
        fecha_actual = None
        for fila in tabla.find_all("tr"):
            celdas = fila.find_all(["td", "th"])
            texto_fila = fila.get_text(" ", strip=True)

            # Fila de encabezado con la fecha (suele tener pocas celdas por colspan)
            if len(celdas) <= 2:
                m = DATE_RE.search(texto_fila)
                if m:
                    dia, mes, anio = int(m.group(2)), int(m.group(3)), int(m.group(4))
                    try:
                        fecha_actual = date(anio, mes, dia)
                    except ValueError:
                        fecha_actual = None
                continue

            if len(celdas) < 5 or fecha_actual is None:
                continue

            hora_m = re.search(r"\b(\d{1,2}:\d{2})\b", celdas[0].get_text())
            hora = hora_m.group(1) if hora_m else None

            competicion = celdas[1].get_text(" ", strip=True)

            equipo1 = celdas[2].get_text(" ", strip=True)
            equipo2 = celdas[3].get_text(" ", strip=True)

            # Escudo de cada equipo: la propia página ya trae una <img> con
            # el logo dentro de la celda del equipo. Lo tomamos directo de
            # ahí (URL absoluta) para no tener que mantener logos a mano.
            img1 = celdas[2].find("img")
            img2 = celdas[3].find("img")
            logo1 = urljoin(BASE_URL + "/", img1["src"]) if img1 and img1.get("src") else None
            logo2 = urljoin(BASE_URL + "/", img2["src"]) if img2 and img2.get("src") else None

            canal_links = celdas[4].find_all("a", href=re.compile(r"/canal/"))
            canales = sorted(set(a.get_text(strip=True) for a in canal_links))

            # Sanidad: descartar filas raras (sorteos, celdas vacías, etc.)
            if not equipo1 or not equipo2 or not canales:
                continue
            if len(equipo1) > 40 or len(equipo2) > 40:
                continue

            partidos.append(
                {
                    "fuente": fuente,
                    "competicion": competicion or None,
                    "fecha": fecha_actual.isoformat(),
                    "hora": hora,
                    "equipos": [equipo1, equipo2],
                    "logos": [logo1, logo2],
                    "canales": canales,
                }
            )

    return partidos


def extraer_proximo_partido(soup: BeautifulSoup, fuente: str):
    """Busca la frase 'El próximo partido que podrás ver será X - Y que se
    disputará el próximo <fecha> a las <hora> ... transmitido por <canales>'
    sobre el TEXTO PLANO de la página (sin depender de negritas, que
    varían levemente de página en página). Devuelve 0 o 1 partido."""
    texto_completo = soup.get_text(" ", strip=True)
    m = PROXIMO_PARTIDO_RE.search(texto_completo)
    if not m:
        return []

    equipos_str, fecha_hora_str, canales_str = m.groups()

    if " - " not in equipos_str:
        return []
    equipo1, equipo2 = [e.strip() for e in equipos_str.split(" - ", 1)]
    # Sanidad: si esto viene mal cortado, los nombres de equipo se disparan de largo
    if len(equipo1) > 40 or len(equipo2) > 40:
        return []

    fm = PROXIMO_PARTIDO_FECHA_RE.search(fecha_hora_str)
    if not fm:
        return []
    dia, mes_nombre, anio, hh, mm = fm.groups()
    mes = MESES.get(normalizar(mes_nombre))
    if not mes:
        return []
    try:
        fecha = date(int(anio), mes, int(dia))
    except ValueError:
        return []
    hora = f"{int(hh):02d}:{mm}"

    canales = [c.strip() for c in canales_str.split(",") if c.strip()]
    if not canales:
        return []

    return [
        {
            "fuente": fuente,
            "competicion": None,
            "fecha": fecha.isoformat(),
            "hora": hora,
            "equipos": [equipo1, equipo2],
            "logos": [None, None],
            "canales": canales,
        }
    ]


def deduplicar(partidos):
    vistos = {}
    for p in partidos:
        clave = (p["fecha"], p["hora"], tuple(sorted(p["equipos"])))
        if clave not in vistos:
            vistos[clave] = p
        else:
            existente = vistos[clave]
            existente["canales"] = sorted(set(existente["canales"]) | set(p["canales"]))
            if not existente.get("competicion") and p.get("competicion"):
                existente["competicion"] = p["competicion"]
            for i in (0, 1):
                if not existente.get("logos", [None, None])[i] and p.get("logos", [None, None])[i]:
                    existente["logos"][i] = p["logos"][i]
            if p["fuente"] not in existente["fuente"]:
                existente["fuente"] += f", {p['fuente']}"
    return list(vistos.values())


def procesar_pagina(nombre, url, es_equipo, nombre_filtro=None):
    """Descarga y extrae los partidos de una página. Nunca lanza excepción:
    si algo falla, imprime el error y devuelve lista vacía, para que el
    resto del scraper siga funcionando."""
    print(f"Descargando {nombre} -> {url}")
    try:
        soup = fetch(url)
    except Exception as e:
        print(f"  ERROR al descargar {nombre}: {e}")
        return []

    partidos = []
    try:
        partidos += extraer_de_tablas(soup, nombre)
    except Exception:
        print(f"  ERROR extrayendo tablas de {nombre}:")
        traceback.print_exc()

    try:
        partidos += extraer_proximo_partido(soup, nombre)
    except Exception:
        print(f"  ERROR extrayendo 'próximo partido' de {nombre}:")
        traceback.print_exc()

    if es_equipo and nombre_filtro:
        antes = len(partidos)
        partidos = [p for p in partidos if equipo_coincide(nombre_filtro, p["equipos"])]
        print(f"  {antes} partidos en bruto -> {len(partidos)} después de validar equipo")
    else:
        print(f"  {len(partidos)} partidos encontrados")

    return partidos


def main():
    todos = []
    hoy = date.today()

    for nombre, cfg in EQUIPOS.items():
        url = urljoin(BASE_URL + "/", cfg["slug"])
        todos += procesar_pagina(nombre, url, es_equipo=True, nombre_filtro=cfg["nombre_filtro"])
        time.sleep(1.5)

    for nombre, slug in COMPETICIONES.items():
        url = urljoin(BASE_URL + "/", slug)
        todos += procesar_pagina(nombre, url, es_equipo=False)
        time.sleep(1.5)

    todos = deduplicar(todos)

    filtrados = []
    for p in todos:
        try:
            fecha_partido = date.fromisoformat(p["fecha"])
        except Exception:
            continue
        if fecha_partido < hoy:
            continue
        canales_ok = [c for c in p["canales"] if canal_disponible(c)]
        if canales_ok:
            p["canales_abuelo"] = canales_ok
            filtrados.append(p)

    filtrados.sort(key=lambda p: (p["fecha"], p["hora"] or "99:99"))

    salida = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "partidos": filtrados,
    }

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"\nTotal partidos filtrados para el abuelo: {len(filtrados)}")
    print("Guardado en docs/data.json")


if __name__ == "__main__":
    main()
