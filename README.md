# Partidos del Abuelo ⚽

Muestra solo los partidos de Colo Colo, Manchester City, Barcelona, Selección
Chilena, Champions League y Copa del Rey que se pueden ver con las
suscripciones que tiene el abuelo: Disney+, DGO (TNT Sports), ESPN y TyC Sports.

## Cómo funciona
1. `scraper.py` revisa futbolenvivochile.com y guarda los partidos filtrados en `docs/data.json`.
2. `docs/index.html` muestra esos datos como página web, con botón de imprimir/PDF.
3. GitHub Actions (`.github/workflows/actualizar.yml`) corre el scraper todos los días solo, sin que tengas que hacer nada.

## Primeros pasos después de subir esto a GitHub
1. Ve a **Settings → Pages** en tu repositorio.
2. En "Source" (o "Build and deployment"), elige **Deploy from a branch**.
3. En "Branch" elige **main** y la carpeta **/docs**, luego **Save**.
4. En unos minutos tu página va a estar disponible en una URL tipo:
   `https://TU-USUARIO.github.io/futbol-abuelo/`

## Correrlo manualmente (sin esperar al día siguiente)
1. Ve a la pestaña **Actions** de tu repositorio.
2. Elige el workflow "Actualizar programación de partidos".
3. Click en **Run workflow**.

## Ajustar equipos o canales
Todo se configura al principio de `scraper.py`:
- `EQUIPOS` / `COMPETICIONES`: qué seguir.
- `CANALES_DISPONIBLES`: qué canales tiene el abuelo.
