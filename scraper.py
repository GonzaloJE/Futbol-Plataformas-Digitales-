"""
Scraper de futbolenvivochile.com para el abuelo de Gonz.

Recorre las páginas de equipos/competiciones prioritarias, extrae los
próximos partidos y se queda solo con los que se transmiten por algún
canal/plataforma que el abuelo SÍ tiene:
  - Disney+ Premium
  - DGO (DirecTV GO) - incluye su canal de deporte y TNT Sports Premium
  - TNT Sports Premium / TNT Sports Premium HD
  - ESPN (los distintos ESPN)
  - TyC Sports (Internacional)

Genera:
  - docs/data.json   -> datos crudos filtrados (para la página web)
  - docs/index.html  -> página web con la programación
"""

import json
import re
import time
from datetime import datetime, date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.futbolenvivochile.com"

# --- Configuración: qué equipos/competiciones seguir -----------------------
# El "slug" es la parte final de la URL, ej: /equipo/colo-colo -> "colo-colo"
# Si alguno no calza (la web cambió el slug, o el nombre es distinto),
# hay que ajustarlo acá.
EQUIPOS = {
    "Colo Colo": "equipo/colo-colo",
    "Manchester City": "equipo/manchester-city",
    "FC Barcelona": "equipo/fc-barcelona",
    "Selección Chilena": "equipo/chile",
}

COMPETICIONES = {
    "Champions League": "competicion/liga-campeones",
    "Copa del Rey": "competicion/copa-del-rey",
}

# --- Configuración: qué canales tiene el abuelo -----------------------------
# Coincidencia flexible: si el nombre del canal en la web CONTIENE alguno
# de estos textos (sin importar mayúsculas/tildes), se considera disponible.
CANALES_DISPONIBLES = [
    "disney+",
    "disney plus",
    "dgo",
    "directv go",
    "tnt sports premium",
    "tnt sports",  # cubre variantes tipo "TNT Sports 2/3" que vienen con DGO
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
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def normalizar(texto: str) -> str:
    """minúsculas y sin tildes, para comparar nombres de canal."""
    texto = texto.lower()
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto


def canal_disponible(nombre_canal: str) -> bool:
    n = normalizar(nombre_canal)
    return any(c in n for c in CANALES_DISPONIBLES)


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def extraer_partidos(soup: BeautifulSoup, fuente: str):
    """
    Recorre el contenido de una página de equipo/competición y devuelve
    una lista de partidos encontrados con: fecha, hora, equipos, canales.

    Estrategia: la web organiza los partidos en bloques donde aparecen
    enlaces a /equipo/... (los dos equipos) y a /canal/... (los canales
    que transmiten). Buscamos esos bloques y agrupamos por fecha usando
    los encabezados de fecha que aparecen como texto tipo
    "Domingo, 23-08-2026".
    """
    partidos = []
    fecha_actual = None

    # Recorremos todos los elementos del documento en orden, para poder
    # ir "recordando" la última fecha vista antes de cada partido.
    for el in soup.find_all(True):
        texto = el.get_text(" ", strip=True)

        m = DATE_RE.search(texto)
        if m and len(texto) < 60:  # encabezados de fecha son cortos
            dia, mes, anio = int(m.group(2)), int(m.group(3)), int(m.group(4))
            try:
                fecha_actual = date(anio, mes, dia)
            except ValueError:
                pass

        # Un "bloque de partido" es un elemento que contiene exactamente
        # (al menos) dos enlaces a /equipo/ y al menos un enlace a /canal/
        enlaces_equipo = el.find_all("a", href=re.compile(r"/equipo/"))
        enlaces_canal = el.find_all("a", href=re.compile(r"/canal/"))

        if len(enlaces_equipo) >= 2 and len(enlaces_canal) >= 1:
            # Evitar procesar el mismo partido varias veces por anidamiento:
            # solo lo tomamos si ESTE elemento es el más chico que cumple
            # la condición (sus hijos directos no la cumplen igual).
            hijos_validos = [
                c for c in el.find_all(True, recursive=False)
                if len(c.find_all("a", href=re.compile(r"/equipo/"))) >= 2
                and len(c.find_all("a", href=re.compile(r"/canal/"))) >= 1
            ]
            if hijos_validos:
                continue  # ya se procesará más abajo en el hijo

            equipos_txt = [a.get_text(strip=True) for a in enlaces_equipo[:2]]
            canales_txt = sorted(set(a.get_text(strip=True) for a in enlaces_canal))

            hora_match = re.search(r"\b(\d{1,2}:\d{2})\b", texto)
            hora = hora_match.group(1) if hora_match else None

            if not equipos_txt[0] or not equipos_txt[1]:
                continue

            partidos.append(
                {
                    "fuente": fuente,
                    "fecha": fecha_actual.isoformat() if fecha_actual else None,
                    "hora": hora,
                    "equipos": equipos_txt,
                    "canales": canales_txt,
                }
            )

    return partidos


def deduplicar(partidos):
    vistos = {}
    for p in partidos:
        clave = (p["fecha"], p["hora"], tuple(sorted(p["equipos"])))
        if clave not in vistos:
            vistos[clave] = p
        else:
            # combinar canales y fuentes si el mismo partido aparece 2 veces
            existente = vistos[clave]
            existente["canales"] = sorted(set(existente["canales"]) | set(p["canales"]))
            if p["fuente"] not in existente["fuente"]:
                existente["fuente"] += f", {p['fuente']}"
    return list(vistos.values())


def main():
    todos = []
    fuentes = {**{k: v for k, v in EQUIPOS.items()}, **{k: v for k, v in COMPETICIONES.items()}}

    for nombre, slug in fuentes.items():
        url = urljoin(BASE_URL + "/", slug)
        print(f"Descargando {nombre} -> {url}")
        try:
            soup = fetch(url)
        except Exception as e:
            print(f"  ERROR al descargar {nombre}: {e}")
            continue
        partidos = extraer_partidos(soup, nombre)
        print(f"  {len(partidos)} partidos encontrados en bruto")
        todos.extend(partidos)
        time.sleep(1.5)  # ser amable con el servidor

    todos = deduplicar(todos)

    # Filtrar solo partidos con al menos un canal disponible para el abuelo
    filtrados = []
    for p in todos:
        canales_ok = [c for c in p["canales"] if canal_disponible(c)]
        if canales_ok:
            p["canales_abuelo"] = canales_ok
            filtrados.append(p)

    # Ordenar por fecha y hora
    def orden_key(p):
        return (p["fecha"] or "9999-99-99", p["hora"] or "99:99")

    filtrados.sort(key=orden_key)

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
