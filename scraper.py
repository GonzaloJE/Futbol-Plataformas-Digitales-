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
"""

import json
import re
import time
from datetime import datetime, date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.futbolenvivochile.com"

# --- Configuración: qué equipos seguir --------------------------------------
# slug: parte final de la URL (/equipo/colo-colo -> "colo-colo")
# nombre_filtro: texto que debe aparecer en el nombre del equipo dentro del
#                partido, para descartar partidos de OTROS equipos que a
#                veces se cuelan desde otras secciones de la página.
EQUIPOS = {
    "Colo Colo": {"slug": "equipo/colo-colo", "nombre_filtro": "Colo Colo"},
    "Manchester City": {"slug": "equipo/manchester-city", "nombre_filtro": "Manchester City"},
    "FC Barcelona": {"slug": "equipo/fc-barcelona", "nombre_filtro": "Barcelona"},
    "Selección Chilena": {"slug": "equipo/chile", "nombre_filtro": "Chile"},
}

COMPETICIONES = {
    "Champions League": "competicion/liga-campeones",
    "Copa del Rey": "competicion/copa-del-rey",
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

# Marcadores de texto que indican el FIN de la zona de partidos en la página
# (todo lo que viene después es ranking, estadísticas, etc. y hay que ignorarlo)
FIN_ZONA_RE = re.compile(r"ranking por|datos estad[ií]sticos", re.IGNORECASE)


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


def zona_de_partidos(soup: BeautifulSoup):
    """
    Devuelve solo los elementos del DOM que están DESPUÉS del título
    principal (h1) de la página y ANTES de la sección de rankings/
    estadísticas. Esa es la única zona donde confiamos en los datos.
    """
    todos = soup.find_all(True)
    h1 = soup.find("h1")
    if h1 is None:
        return todos

    try:
        inicio = todos.index(h1)
    except ValueError:
        inicio = 0

    fin = len(todos)
    for i in range(inicio + 1, len(todos)):
        texto = todos[i].get_text(" ", strip=True)
        if FIN_ZONA_RE.search(texto) and len(texto) < 80:
            fin = i
            break

    return todos[inicio + 1 : fin]


def extraer_partidos(soup: BeautifulSoup, fuente: str):
    partidos = []
    fecha_actual = None

    for el in zona_de_partidos(soup):
        texto = el.get_text(" ", strip=True)

        m = DATE_RE.search(texto)
        if m and len(texto) < 60:
            dia, mes, anio = int(m.group(2)), int(m.group(3)), int(m.group(4))
            try:
                fecha_actual = date(anio, mes, dia)
            except ValueError:
                pass

        enlaces_equipo = el.find_all("a", href=re.compile(r"/equipo/"))
        enlaces_canal = el.find_all("a", href=re.compile(r"/canal/"))

        if len(enlaces_equipo) >= 2 and len(enlaces_canal) >= 1:
            hijos_validos = [
                c for c in el.find_all(True, recursive=False)
                if len(c.find_all("a", href=re.compile(r"/equipo/"))) >= 2
                and len(c.find_all("a", href=re.compile(r"/canal/"))) >= 1
            ]
            if hijos_validos:
                continue

            equipos_txt = [a.get_text(strip=True) for a in enlaces_equipo[:2]]
            canales_txt = sorted(set(a.get_text(strip=True) for a in enlaces_canal))

            hora_match = re.search(r"\b(\d{1,2}:\d{2})\b", texto)
            hora = hora_match.group(1) if hora_match else None

            if not equipos_txt[0] or not equipos_txt[1]:
                continue
            if not fecha_actual:
                continue  # sin fecha no sirve para armar la programación semanal

            partidos.append(
                {
                    "fuente": fuente,
                    "fecha": fecha_actual.isoformat(),
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
            existente = vistos[clave]
            existente["canales"] = sorted(set(existente["canales"]) | set(p["canales"]))
            if p["fuente"] not in existente["fuente"]:
                existente["fuente"] += f", {p['fuente']}"
    return list(vistos.values())


def main():
    todos = []
    hoy = date.today()

    # --- Equipos (con verificación de que el equipo realmente esté en el partido) ---
    for nombre, cfg in EQUIPOS.items():
        url = urljoin(BASE_URL + "/", cfg["slug"])
        print(f"Descargando {nombre} -> {url}")
        try:
            soup = fetch(url)
        except Exception as e:
            print(f"  ERROR al descargar {nombre}: {e}")
            continue
        partidos = extraer_partidos(soup, nombre)
        antes = len(partidos)
        partidos = [p for p in partidos if equipo_coincide(cfg["nombre_filtro"], p["equipos"])]
        print(f"  {antes} partidos en bruto -> {len(partidos)} después de validar equipo")
        todos.extend(partidos)
        time.sleep(1.5)

    # --- Competiciones (no se puede validar un solo equipo, pero sí la zona) ---
    for nombre, slug in COMPETICIONES.items():
        url = urljoin(BASE_URL + "/", slug)
        print(f"Descargando {nombre} -> {url}")
        try:
            soup = fetch(url)
        except Exception as e:
            print(f"  ERROR al descargar {nombre}: {e}")
            continue
        partidos = extraer_partidos(soup, nombre)
        print(f"  {len(partidos)} partidos encontrados")
        todos.extend(partidos)
        time.sleep(1.5)

    todos = deduplicar(todos)

    # Filtrar solo partidos futuros (hoy o después) con canal disponible
    filtrados = []
    for p in todos:
        fecha_partido = date.fromisoformat(p["fecha"])
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
