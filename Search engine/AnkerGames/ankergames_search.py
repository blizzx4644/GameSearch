#!/usr/bin/env python3
"""
🎮 AnkerGames Search CLI
Recherche via https://ankergames.net
  - Méthode 1 : Page /games-list (données complètes en HTML + JSON embarqué)
  - Méthode 2 : Accès direct par slug /game/{slug}
  - Méthode 3 : Scraping /browse (cards avec images)
Même système de scoring que fitgirl_search.py.

Architecture du site :
  - Laravel + Alpine.js + Livewire (composants mineurs)
  - /games-list : table complète de tous les jeux (SSR HTML)
  - /browse     : grille de cards avec images (Alpine.js x-data)
  - /game/{slug}: page individuelle de chaque jeu
"""

import sys
import os
import json
import time
import re
import argparse
import webbrowser
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse, quote
from html import unescape

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Dépendances manquantes :")
    print("   pip install requests beautifulsoup4")
    sys.exit(1)

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False


# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

BASE_URL          = "https://ankergames.net"
GAMES_LIST_URL    = f"{BASE_URL}/games-list"
BROWSE_URL        = f"{BASE_URL}/browse"

CACHE_DIR         = Path.home() / ".ankergames_cache"
HISTORY_FILE      = CACHE_DIR / "search_history.json"
DEBUG_FILE        = CACHE_DIR / "debug_page.html"
DEBUG_JSON_FILE   = CACHE_DIR / "debug_data.json"
GAMES_CACHE_FILE  = CACHE_DIR / "all_games.json"

# Durée de validité du cache local (en secondes) — 6 heures
CACHE_TTL         = 6 * 3600

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
]


# ═══════════════════════════════════════════════════════════════
#  ANSI
# ═══════════════════════════════════════════════════════════════

class C:
    RST = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    CYAN = "\033[96m"; GREEN = "\033[92m"; YEL = "\033[93m"
    RED = "\033[91m"; MAG = "\033[95m"; BLUE = "\033[94m"
    WHITE = "\033[97m"

BANNER = f"""{C.CYAN}{C.BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║        🎮  AnkerGames Search CLI  🎮                     ║
 ║   HTML Scraping + Local Scoring · ankergames.net         ║
 ╚═══════════════════════════════════════════════════════════╝{C.RST}
"""
SEP = f"  {C.DIM}{'─' * 62}{C.RST}"


# ═══════════════════════════════════════════════════════════════
#  HTTP SESSION
# ═══════════════════════════════════════════════════════════════

def _make_session():
    if HAS_CLOUDSCRAPER:
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows"}
        )
    else:
        session = requests.Session()

    session.headers.update({
        "User-Agent":       USER_AGENTS[0],
        "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":  "en-US,en;q=0.9",
        "Referer":          f"{BASE_URL}/",
    })
    return session


def _clean_title(text: str) -> str:
    """Nettoie un titre AnkerGames."""
    text = unescape(text).strip()
    text = re.sub(r'\s*\|\s*AnkerGames\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*-\s*AnkerGames\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*Free\s+Download\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*–\s*$', '', text).strip()
    return text


def _slug_to_title(slug: str) -> str:
    """Convertit un slug URL en titre lisible."""
    # Retirer les suffixes numériques (-2, -3, etc.)
    clean = re.sub(r'-(\d+)$', '', slug)
    # Mots à garder en minuscule
    small_words = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to",
                   "for", "with", "by", "vs", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
    parts = clean.split("-")
    titled = []
    for i, part in enumerate(parts):
        if part.upper() == part and len(part) > 1:
            titled.append(part.upper())
        elif i > 0 and part.lower() in small_words and len(part) <= 3:
            titled.append(part.lower())
        else:
            titled.append(part.capitalize())
    return " ".join(titled)


def _is_cloudflare_challenge(text: str) -> bool:
    lower = text[:3000].lower()
    return "just a moment" in lower and "cloudflare" in lower


# ═══════════════════════════════════════════════════════════════
#  MÉTHODE 1 : SCRAPING /games-list
#
#  La page /games-list contient la liste complète de TOUS les
#  jeux du site (~1400+), rendue en HTML côté serveur (SSR).
#  C'est la source la plus fiable pour construire un index local.
#
#  Structure observée :
#  - Chaque jeu est un lien <a href="/game/{slug}"> dans une
#    table ou grille
#  - Les titres sont visibles dans le HTML statique
#  - Les métadonnées (size, version, date) sont dans la même row
#
# ═══════════════════════════════════════════════════════════════

def _fetch_page(url: str, session=None, verbose=True) -> str | None:
    """GET générique avec gestion Cloudflare."""
    if session is None:
        session = _make_session()

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        try:
            if verbose:
                print(f"  {C.DIM}🌐 GET {url}{C.RST}")

            resp = session.get(url, timeout=(10 if not verbose else 30))

            if resp.status_code == 200 and len(resp.text) > 500:
                if _is_cloudflare_challenge(resp.text):
                    if verbose:
                        print(f"  {C.YEL}⚠ Cloudflare challenge{C.RST}")
                    continue

                if verbose:
                    print(f"  {C.GREEN}✔ Page reçue ({len(resp.text):,} octets){C.RST}")

                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                DEBUG_FILE.write_text(resp.text[:500_000], encoding="utf-8")
                return resp.text

            if verbose:
                print(f"  {C.DIM}  → HTTP {resp.status_code}{C.RST}")

        except requests.RequestException as e:
            if verbose:
                print(f"  {C.DIM}  → Erreur : {e}{C.RST}")

    return None


def parse_games_list_page(html: str, verbose=True) -> list[dict]:
    """
    Parse la page /games-list d'AnkerGames.

    La page contient une table/grille de tous les jeux avec :
    - Liens /game/{slug}
    - Titre du jeu (dans le texte du lien ou cellule adjacente)
    - Métadonnées (version, taille, année)

    STRATÉGIE : Pour chaque lien /game/{slug}, on isole le
    conteneur le plus petit qui contient CE SEUL lien game,
    puis on extrait le titre et les métadonnées de ce conteneur.
    """
    soup = BeautifulSoup(html, "html.parser")
    games = []
    seen_slugs = set()

    # ── Collecter TOUS les liens /game/{slug} ──
    game_links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()

        # Accepter /game/xxx ou https://ankergames.net/game/xxx
        if href.startswith("/game/"):
            slug = href[6:].strip("/")
        elif href.startswith(f"{BASE_URL}/game/"):
            slug = href[len(f"{BASE_URL}/game/"):].strip("/")
        else:
            continue

        # Valider le slug
        if not slug or not re.match(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$', slug):
            if len(slug) <= 2:
                continue  # Trop court
            if not re.match(r'^[a-z0-9\-]+$', slug):
                continue  # Caractères invalides

        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        game_links.append((a_tag, slug))

    if verbose:
        print(f"  {C.DIM}   📊 {len(game_links)} liens /game/ trouvés{C.RST}")

    # ── Pour chaque lien, extraire titre + métadonnées ──
    for a_tag, slug in game_links:
        url = f"{BASE_URL}/game/{slug}"
        name = ""
        size = ""
        version = ""
        year = ""
        genres = ""
        image = ""

        # ── TITRE : Stratégie multi-niveaux ──

        # 1. Attribut title ou aria-label sur le lien
        if a_tag.get("title"):
            name = _clean_title(a_tag["title"])
        elif a_tag.get("aria-label"):
            name = _clean_title(a_tag["aria-label"])

        # 2. Image alt à l'intérieur du lien
        if not name:
            img = a_tag.find("img")
            if img:
                alt = img.get("alt", "").strip()
                if alt and len(alt) > 2:
                    name = _clean_title(alt)
                image = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""

        # 3. Texte direct du lien (filtrer les textes génériques)
        if not name:
            link_text = a_tag.get_text(strip=True)
            if link_text and len(link_text) > 2:
                # Vérifier que ce n'est pas juste un bouton
                lower_text = link_text.lower()
                if lower_text not in ("download", "view", "more", "details",
                                      "free download", "play now", "get it",
                                      "click here", "read more", "visit"):
                    name = _clean_title(link_text)

        # 4. Conteneur ISOLÉ : remonter pour trouver le plus petit parent
        #    contenant UN SEUL lien /game/
        if not name or not _looks_like_game_title(name):
            container = _find_isolated_container(a_tag, slug)
            if container:
                # Chercher un titre dans ce conteneur isolé
                for h_tag in ["h1", "h2", "h3", "h4", "h5"]:
                    h = container.find(h_tag)
                    if h:
                        candidate = _clean_title(h.get_text(strip=True))
                        if candidate and len(candidate) > 2 and _looks_like_game_title(candidate):
                            name = candidate
                            break

                # Chercher aussi dans des spans/divs avec class "title"/"name"
                if not name or not _looks_like_game_title(name):
                    for cls_kw in ["title", "name", "heading", "game-title"]:
                        el = container.find(class_=re.compile(cls_kw, re.I))
                        if el:
                            candidate = _clean_title(el.get_text(strip=True))
                            if candidate and len(candidate) > 2 and _looks_like_game_title(candidate):
                                name = candidate
                                break

                # Extraire métadonnées depuis le conteneur
                container_text = container.get_text(" ", strip=True)
                _extract_meta_from_text(container_text, locals_dict := {})
                size = locals_dict.get("size", size)
                version = locals_dict.get("version", version)
                year = locals_dict.get("year", year)

                # Image depuis le conteneur
                if not image:
                    img = container.find("img")
                    if img:
                        image = img.get("src") or img.get("data-src") or ""

                # Genres depuis badges dans le conteneur
                if not genres:
                    genre_els = container.find_all(
                        class_=re.compile(r'badge|tag|genre|category|chip', re.I)
                    )
                    g_list = []
                    for ge in genre_els:
                        gt = ge.get_text(strip=True)
                        if gt and 2 < len(gt) < 30:
                            g_list.append(gt)
                    if g_list:
                        genres = ", ".join(g_list[:5])

        # 5. Dernier recours : slug → titre
        if not name or not _looks_like_game_title(name):
            name = _slug_to_title(slug)

        # ── Nettoyage final du titre ──
        # Retirer les textes de catégorie collés (ex: "State of Decay 2ActionGame")
        name = re.sub(r'((?:Action|Adventure|Horror|Racing|Shooting|Simulation|Strategy|'
                       r'Sports|Puzzle|RPG|VR|Multiplayer|Indie|Arcade|Casual|Platformer|'
                       r'Survival|Fighting|Stealth|Open World|Sandbox|MMORPG|Battle Royale)'
                       r'(?:Game|Games)?)\s*$', '', name, flags=re.I).strip()
        # Aussi pattern collé sans espace : "Decay 2ActionGame" → "Decay 2"
        name = re.sub(r'(?<=[a-z0-9])(?:Action|Adventure|Horror|Racing|Shooting|Simulation|'
                       r'Strategy|Sports|Puzzle|RPG|VR|Multiplayer|Indie|Arcade|Casual|'
                       r'Platformer|Survival|Fighting|Stealth)(?:Game|Games?)?$',
                       '', name, flags=re.I).strip()

        game = {
            "name":      name,
            "url":       url,
            "slug":      slug,
            "date":      year,
            "image":     image,
            "genres":    genres,
            "version":   version,
            "size":      size,
            "companies": "",
            "languages": "",
        }
        games.append(game)

    if verbose:
        print(f"  {C.DIM}   🎯 {len(games)} jeu(x) parsés{C.RST}")

    return games


def _looks_like_game_title(text: str) -> bool:
    """Vérifie qu'un texte ressemble à un titre de jeu (pas un bouton, nav, etc.)."""
    if not text or len(text) < 2:
        return False
    lower = text.lower()
    # Textes génériques qui ne sont pas des titres
    generic = {"download", "view", "more", "details", "free download",
               "play now", "get it", "click here", "read more", "visit",
               "home", "browse", "games", "all games", "about", "contact",
               "faq", "login", "sign up", "register", "search", "load more",
               "show more", "next", "previous", "menu", "close"}
    if lower in generic:
        return False
    if len(text) > 200:
        return False
    return True


def _find_isolated_container(a_tag, target_slug: str):
    """
    Remonte l'arbre DOM pour trouver le plus petit conteneur
    qui contient EXACTEMENT UN lien /game/.

    C'est la clé pour éviter le bug où on prend un conteneur trop
    large (qui contient TOUS les jeux) et on extrait le mauvais titre.
    """
    parent = a_tag.parent
    depth = 0

    while parent and depth < 6:
        if parent.name in ("body", "html", "main", "section", "nav", "header", "footer"):
            break

        if parent.name in ("div", "article", "li", "td", "tr", "a", "figure"):
            # Compter les liens /game/ dans ce parent
            game_links_in_parent = parent.find_all(
                "a", href=re.compile(r'^(/game/|https?://ankergames\.net/game/)')
            )

            if len(game_links_in_parent) == 1:
                # Ce parent contient UN SEUL lien game → c'est notre card !
                return parent

            if len(game_links_in_parent) > 1 and len(game_links_in_parent) <= 3:
                # 2-3 liens : peut-être ok si c'est le même jeu (ex: image + titre)
                slugs = set()
                for gl in game_links_in_parent:
                    h = gl["href"]
                    s = h.split("/game/")[-1].strip("/") if "/game/" in h else ""
                    slugs.add(s)
                if len(slugs) == 1:
                    return parent

            if len(game_links_in_parent) > 3:
                # Trop de liens → on est dans un conteneur global, STOP
                break

        parent = parent.parent
        depth += 1

    # Fallback : parent direct
    direct = a_tag.parent
    if direct and direct.name in ("div", "td", "li", "article", "figure"):
        return direct

    return None


def _extract_meta_from_text(text: str, result: dict):
    """Extrait taille, version, année depuis un texte."""
    # Size
    m = re.search(r'(\d+(?:[.,]\d+)?\s*[KMGT]B)\b', text, re.I)
    if m:
        result["size"] = m.group(1).strip()

    # Version
    m = re.search(r'(?:Version|Build|[Vv])\s*[.:\s]*([0-9][0-9a-zA-Z._\-]{1,30})', text)
    if m:
        ver = m.group(1).strip().rstrip(".")
        if len(ver) > 20:
            ver = ver[:20] + "…"
        result["version"] = ver

    # Year
    m = re.search(r'\b(20[0-2]\d)\b', text)
    if m:
        result["year"] = m.group(1)


# ═══════════════════════════════════════════════════════════════
#  MÉTHODE 2 : ACCÈS DIRECT PAR SLUG
# ═══════════════════════════════════════════════════════════════

def search_by_slug(query: str, session=None, verbose=True) -> list[dict]:
    """
    Tente d'accéder directement à /game/{slug}.
    Génère plusieurs variantes de slug depuis la requête.
    """
    if session is None:
        session = _make_session()

    # Générer le slug de base
    base_slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')
    slugs_to_try = [base_slug]

    # Variantes avec suffixe numérique (ankergames ajoute -2, -3 parfois)
    for suffix in ["-2", "-3", "-4"]:
        slugs_to_try.append(base_slug + suffix)

    # Sans articles
    for article in ["the-", "a-", "an-"]:
        if base_slug.startswith(article):
            s = base_slug[len(article):]
            if s and s not in slugs_to_try:
                slugs_to_try.append(s)
                slugs_to_try.append(s + "-2")

    # Variantes avec articles déplacés
    parts = base_slug.split("-")
    if len(parts) > 1:
        # "witcher-3-wild-hunt" déjà bon, mais on essaie sans chiffres
        no_numbers = "-".join(p for p in parts if not p.isdigit())
        if no_numbers and no_numbers not in slugs_to_try:
            slugs_to_try.append(no_numbers)

    games = []
    for slug in slugs_to_try[:8]:
        url = f"{BASE_URL}/game/{slug}"
        try:
            if verbose:
                print(f"  {C.DIM}🌐 GET {url}{C.RST}")

            resp = session.get(url, timeout=(10 if not verbose else 15), allow_redirects=True)

            if resp.status_code == 200 and len(resp.text) > 2000:
                if _is_cloudflare_challenge(resp.text):
                    continue

                # Vérifier que c'est une page de jeu (pas une 404 soft)
                if "404" in resp.text[:1000] and "not found" in resp.text[:1000].lower():
                    continue

                game = _parse_single_game_page(resp.text, resp.url)
                if game:
                    if verbose:
                        print(f"  {C.GREEN}✔ Trouvé : {game['name']}{C.RST}")
                    games.append(game)
                    break  # Un match direct suffit

            elif resp.status_code == 404:
                if verbose:
                    print(f"  {C.DIM}   → 404{C.RST}")
            else:
                if verbose:
                    print(f"  {C.DIM}   → HTTP {resp.status_code}{C.RST}")

            time.sleep(0.05 if not verbose else 0.3)

        except requests.RequestException as e:
            if verbose:
                print(f"  {C.DIM}   → Erreur : {e}{C.RST}")

    return games


def _parse_single_game_page(html: str, final_url: str) -> dict | None:
    """Parse une page individuelle /game/{slug}."""
    soup = BeautifulSoup(html, "html.parser")

    # Extraire l'URL slug
    parsed = urlparse(final_url)
    path = parsed.path.strip("/")
    slug = path.replace("game/", "", 1) if path.startswith("game/") else ""

    # Titre : og:title > <title> > <h1>
    title = ""
    og = soup.find("meta", property="og:title")
    if og:
        title = _clean_title(og.get("content", ""))
    if not title:
        t = soup.find("title")
        if t:
            title = _clean_title(t.get_text(strip=True))
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = _clean_title(h1.get_text(strip=True))
    if not title and slug:
        title = _slug_to_title(slug)

    # Image
    image = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image = og_img.get("content", "")

    # Métadonnées
    body_text = soup.get_text(" ", strip=True)
    meta = {}
    _extract_meta_from_text(body_text, meta)

    # Genres
    genres = ""
    m = re.search(r'Genre\s*[:\s]+(.+?)(?:\n|\.{2,}|$)', body_text, re.I)
    if m:
        genres = m.group(1).strip()[:80]

    return {
        "name":      title,
        "url":       final_url,
        "slug":      slug,
        "date":      meta.get("year", ""),
        "image":     image,
        "genres":    genres,
        "version":   meta.get("version", ""),
        "size":      meta.get("size", ""),
        "companies": "",
        "languages": "",
    }


# ═══════════════════════════════════════════════════════════════
#  MÉTHODE 3 : SCRAPING /browse (cards avec images)
# ═══════════════════════════════════════════════════════════════

def scrape_browse_page(session=None, verbose=True) -> list[dict]:
    """Scrape /browse pour récupérer les cards de jeux."""
    if session is None:
        session = _make_session()

    html = _fetch_page(BROWSE_URL, session=session, verbose=verbose)
    if not html:
        return []

    return parse_games_list_page(html, verbose=verbose)


# ═══════════════════════════════════════════════════════════════
#  CACHE LOCAL
#  On télécharge /games-list une fois, on cache tout localement,
#  puis on fait la recherche/scoring côté client.
# ═══════════════════════════════════════════════════════════════

def _is_cache_valid() -> bool:
    """Vérifie si le cache est encore valide."""
    if not GAMES_CACHE_FILE.exists():
        return False
    age = time.time() - GAMES_CACHE_FILE.stat().st_mtime
    return age < CACHE_TTL


def _load_cache() -> list[dict]:
    """Charge les jeux depuis le cache local."""
    try:
        data = json.loads(GAMES_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) > 0:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_cache(games: list[dict]):
    """Sauvegarde les jeux dans le cache local."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    GAMES_CACHE_FILE.write_text(
        json.dumps(games, ensure_ascii=False, indent=1),
        encoding="utf-8"
    )


def build_games_index(force_refresh: bool = False, verbose=True) -> list[dict]:
    """
    Construit l'index local de tous les jeux.

    1. Vérifie le cache local
    2. Sinon, scrape /games-list et /browse
    3. Fusionne et déduplique
    4. Sauvegarde en cache
    """
    if not force_refresh and _is_cache_valid():
        cached = _load_cache()
        if cached:
            if verbose:
                print(f"  {C.DIM}📦 Cache local : {len(cached)} jeux "
                      f"(expire dans {int((CACHE_TTL - (time.time() - GAMES_CACHE_FILE.stat().st_mtime)) / 60)}min){C.RST}")
            return cached

    if verbose:
        print(f"  {C.WHITE}{C.BOLD}📥 Construction de l'index (scraping)…{C.RST}")

    session = _make_session()

    # Pré-visite home (cookies Cloudflare)
    try:
        session.get(BASE_URL, timeout=(10 if not verbose else 15))
        time.sleep(0.05 if not verbose else 0.3)
    except Exception:
        pass

    all_games = []
    seen_slugs = set()

    def _merge(new_games: list[dict]):
        for g in new_games:
            s = g.get("slug", "")
            if not s:
                # Extraire slug depuis l'URL
                path = urlparse(g["url"]).path.strip("/")
                s = path.replace("game/", "", 1) if path.startswith("game/") else path
            if s and s not in seen_slugs:
                seen_slugs.add(s)
                g["slug"] = s
                all_games.append(g)

    # ── /games-list (source principale) ──
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Source 1 : /games-list{C.RST}")

    html = _fetch_page(GAMES_LIST_URL, session=session, verbose=verbose)
    if html:
        games = parse_games_list_page(html, verbose=verbose)
        _merge(games)

    # ── /browse (source complémentaire) ──
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Source 2 : /browse{C.RST}")

    html = _fetch_page(BROWSE_URL, session=session, verbose=verbose)
    if html:
        games = parse_games_list_page(html, verbose=verbose)
        _merge(games)

    if all_games:
        _save_cache(all_games)
        if verbose:
            print(f"\n  {C.GREEN}✔ Index : {len(all_games)} jeux indexés et mis en cache{C.RST}")

    return all_games


# ═══════════════════════════════════════════════════════════════
#  RECHERCHE COMBINÉE
# ═══════════════════════════════════════════════════════════════

def search_online(query: str, force_refresh: bool = False, verbose=True) -> list[dict]:
    """
    Recherche combinée :
    1. Index local (cache ou scraping /games-list + /browse)
    2. Scoring local pour filtrer les résultats
    3. Complément par accès direct slug si pas de bons résultats
    """
    all_results = []
    seen_slugs = set()

    def _merge(new_games):
        for g in new_games:
            s = g.get("slug", "")
            if s and s not in seen_slugs:
                seen_slugs.add(s)
                all_results.append(g)

    # ── Étape 1 : Index local ──
    index = build_games_index(force_refresh=force_refresh, verbose=verbose)
    _merge(index)

    # ── Étape 2 : Si l'index est vide ou petit, essayer slug direct ──
    if len(all_results) < 10:
        if verbose:
            print(f"\n  {C.WHITE}{C.BOLD}📡 Complément : accès direct /game/slug{C.RST}")

        session = _make_session()
        slug_results = search_by_slug(query, session=session, verbose=verbose)
        _merge(slug_results)
    else:
        # Vérifier si le slug direct existe et n'est pas dans l'index
        base_slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')
        matching = [g for g in all_results if base_slug in g.get("slug", "")]
        if not matching:
            if verbose:
                print(f"\n  {C.WHITE}{C.BOLD}📡 Complément : accès direct /game/slug{C.RST}")
            session = _make_session()
            slug_results = search_by_slug(query, session=session, verbose=verbose)
            _merge(slug_results)

    if not all_results and verbose:
        _print_debug_help()

    return all_results


# ═══════════════════════════════════════════════════════════════
#  SCORING LOCAL (identique à fitgirl_search.py)
# ═══════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = re.sub(r'[\u0300-\u036f]', '', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    return text

def tokenize(t: str) -> list[str]:
    return t.split()

def partial_ratio(short: str, long: str) -> float:
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    best = 0.0
    w = len(short)
    for i in range(len(long) - w + 1):
        r = SequenceMatcher(None, short, long[i:i + w]).ratio()
        if r > best:
            best = r
            if best == 1.0:
                return 1.0
    return best

def compute_score(q_norm, q_tokens, g_norm, g_tokens) -> float:
    if q_norm == g_norm:
        return 10000.0
    score = 0.0
    if g_norm.startswith(q_norm):
        score += 600
    if q_norm in g_norm:
        score += 400
    elif g_norm in q_norm:
        score += 200
    found = sum(1 for t in q_tokens if any(t in gt for gt in g_tokens))
    cov = found / len(q_tokens) if q_tokens else 0
    score += cov * 300
    if cov == 1.0:
        score += 150
    sub = sum(1 for qt in q_tokens if any(qt in gt for gt in g_tokens))
    score += (sub / max(len(q_tokens), 1)) * 100
    sa, sb = set(q_tokens), set(g_tokens)
    inter = sa & sb
    union = sa | sb
    if union:
        jaccard = len(inter) / len(union)
        tcov = len(inter) / len(sa) if sa else 0
        score += (jaccard * 0.4 + tcov * 0.6) * 200
    score += SequenceMatcher(None, q_norm, g_norm).ratio() * 150
    qs = " ".join(sorted(q_tokens))
    gs = " ".join(sorted(g_tokens))
    score += SequenceMatcher(None, qs, gs).ratio() * 120
    score += partial_ratio(q_norm, g_norm) * 250
    if len(q_tokens) == 1 and len(q_norm) <= 6:
        acr = "".join(t[0] for t in g_tokens if t)
        if q_norm == acr:
            score += 500
    pre = sum(1 for qt in q_tokens if any(gt.startswith(qt) for gt in g_tokens))
    score += (pre / max(len(q_tokens), 1)) * 100
    score -= abs(len(q_norm) - len(g_norm)) * 0.8
    if len(q_tokens) == len(g_tokens):
        score += 30
    return score

def rank_results(query: str, results: list[dict], limit: int = 15) -> list[dict]:
    q_norm = normalize(query)
    q_tokens = tokenize(q_norm)
    if not q_norm:
        return results[:limit]

    for g in results:
        gn = normalize(g["name"])
        gt = tokenize(gn)
        sc = compute_score(q_norm, q_tokens, gn, gt) if gn else 0.0

        # Bonus si query matche aussi le slug
        slug_norm = normalize(g.get("slug", "").replace("-", " "))
        if slug_norm:
            slug_tokens = tokenize(slug_norm)
            slug_sc = compute_score(q_norm, q_tokens, slug_norm, slug_tokens)
            # Prendre le meilleur des deux
            sc = max(sc, slug_sc)

        # Bonus si query dans companies / genres
        comp = normalize(g.get("companies", ""))
        genres = normalize(g.get("genres", ""))
        if q_norm in comp or q_norm in genres:
            sc += 60
        elif any(t in comp or t in genres for t in q_tokens):
            sc += 20

        g["score"] = round(sc, 1)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Filtrage : ne garder que les résultats avec un score minimum
    if results and results[0].get("score", 0) > 100:
        min_score = results[0]["score"] * 0.15  # Au moins 15% du meilleur
        results = [r for r in results if r.get("score", 0) >= min_score]

    return results[:limit]


# ═══════════════════════════════════════════════════════════════
#  HISTORIQUE
# ═══════════════════════════════════════════════════════════════

def save_to_history(query: str, results: list[dict]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    history.append({
        "ts": time.time(), "query": query, "count": len(results),
        "top3": [{"name": r["name"], "url": r["url"]} for r in results[:3]],
    })
    history = history[-200:]
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")

def show_history():
    if not HISTORY_FILE.exists():
        print(f"  {C.DIM}Aucun historique.{C.RST}")
        return
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        print(f"  {C.DIM}Historique corrompu.{C.RST}")
        return
    print(f"\n  {C.WHITE}{C.BOLD}📜 Dernières recherches :{C.RST}")
    for h in history[-25:]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"]))
        print(f"    {C.DIM}{ts}{C.RST}  {C.CYAN}{h['query']}{C.RST}  "
              f"{C.DIM}({h['count']} résultats){C.RST}")
    print()


# ═══════════════════════════════════════════════════════════════
#  AFFICHAGE
# ═══════════════════════════════════════════════════════════════

def score_bar(score, mx):
    if mx <= 0:
        mx = 1
    pct = min(score / mx, 1.0)
    f = int(pct * 20)
    color = C.GREEN if pct >= 0.8 else (C.YEL if pct >= 0.5 else C.RED)
    return f"{color}{'█' * f}{C.DIM}{'░' * (20 - f)}{C.RST}"


def display_results(results: list[dict], query: str):
    if not results:
        print(f"\n  {C.YEL}⚠  Aucun résultat pour « {query} »{C.RST}")
        print(f"  {C.DIM}Essayez un autre terme de recherche.{C.RST}\n")
        return None

    has_scores = any("score" in r for r in results)
    mx = max(r.get("score", 1) for r in results) if has_scores else 1

    print(f"\n  {C.GREEN}{C.BOLD}✔ {len(results)} résultat(s) pour « {query} »{C.RST}")
    print(SEP)

    for i, r in enumerate(results, 1):
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}")
        print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        meta = []
        if r.get("size"):
            meta.append(f"📦 Size: {r['size']}")
        if r.get("version"):
            meta.append(f"🏷  {r['version']}")
        if r.get("date"):
            meta.append(f"📅 {r['date']}")
        if meta:
            print(f"        {C.DIM}{' │ '.join(meta)}{C.RST}")

        meta2 = []
        if r.get("genres"):
            meta2.append(f"🎮 {r['genres']}")
        if r.get("companies"):
            meta2.append(f"🛠  {r['companies']}")
        if meta2:
            print(f"        {C.DIM}{' │ '.join(meta2)}{C.RST}")

        if r.get("languages"):
            print(f"        {C.DIM}🌐 {r['languages']}{C.RST}")

        if has_scores and "score" in r:
            bar = score_bar(r["score"], mx)
            print(f"        {bar}  {C.DIM}score: {r['score']}{C.RST}")

        print(SEP)

    return results


def prompt_open(results: list[dict]):
    if not results:
        return
    print(f"\n  {C.MAG}Ouvrir ? Tapez le numéro (Entrée = passer){C.RST}")
    try:
        ch = input(f"  {C.MAG}{C.BOLD}▸ {C.RST}").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    if ch.isdigit():
        idx = int(ch) - 1
        if 0 <= idx < len(results):
            print(f"  {C.GREEN}🌐 {results[idx]['url']}{C.RST}\n")
            webbrowser.open(results[idx]["url"])
        else:
            print(f"  {C.RED}Numéro invalide.{C.RST}")


# ═══════════════════════════════════════════════════════════════
#  DEBUG
# ═══════════════════════════════════════════════════════════════

def _print_debug_help():
    print(f"""
  {C.YEL}{C.BOLD}💡 Solutions :{C.RST}
  {C.WHITE}1.{C.RST} pip install cloudscraper  (bypass Cloudflare)
  {C.WHITE}2.{C.RST} Sauvez la page HTML manuellement (Ctrl+S) puis :
     python {sys.argv[0]} --from-file "page.html" "query"
  {C.WHITE}3.{C.RST} Vérifiez l'accès :
     curl -sI {BASE_URL}
  {C.WHITE}4.{C.RST} Debug HTML sauvegardé  : {DEBUG_FILE}
  {C.WHITE}5.{C.RST} Forcer un rafraîchissement du cache :
     python {sys.argv[0]} --refresh "query"
  {C.WHITE}6.{C.RST} Cache jeux : {GAMES_CACHE_FILE}
""")


# ═══════════════════════════════════════════════════════════════
#  MODES
# ═══════════════════════════════════════════════════════════════

def do_search(query: str, limit: int = 15,
              verbose=True, from_file: str | None = None,
              force_refresh: bool = False) -> list[dict]:
    if from_file:
        p = Path(from_file)
        if p.exists():
            if verbose:
                print(f"  {C.DIM}📂 Chargement depuis {p}{C.RST}")
            html = p.read_text(encoding="utf-8", errors="replace")
            results = parse_games_list_page(html, verbose)
        else:
            print(f"  {C.RED}✖ Fichier introuvable : {p}{C.RST}")
            return []
    else:
        results = search_online(query, force_refresh=force_refresh, verbose=verbose)

    if results:
        results = rank_results(query, results, limit=limit)

    save_to_history(query, results)
    return results


def interactive_mode(limit: int = 15, from_file: str | None = None):
    print(BANNER)

    if not HAS_CLOUDSCRAPER:
        print(f"  {C.YEL}💡 pip install cloudscraper  (recommandé pour Cloudflare){C.RST}")

    print(f"  {C.DIM}Commandes :  <jeu>  │  history  │  refresh  │  q{C.RST}\n")

    index_built = False

    while True:
        try:
            raw = input(f"  {C.MAG}{C.BOLD}🔍 Rechercher ▸ {C.RST}").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {C.CYAN}👋 Bye !{C.RST}\n")
            break

        if not raw:
            continue
        cmd = raw.lower()

        if cmd in ("q", "quit", "exit"):
            print(f"\n  {C.CYAN}👋 Bye !{C.RST}\n")
            break

        if cmd == "history":
            show_history()
            continue

        if cmd == "refresh":
            print(f"\n  {C.YEL}🔄 Rafraîchissement du cache…{C.RST}")
            index = build_games_index(force_refresh=True, verbose=True)
            print(f"  {C.GREEN}✔ {len(index)} jeux indexés{C.RST}\n")
            continue

        if cmd == "help":
            print(f"""
  {C.WHITE}{C.BOLD}Commandes :{C.RST}
    {C.CYAN}<nom du jeu>{C.RST}    Rechercher un jeu
    {C.CYAN}history{C.RST}         Historique des recherches
    {C.CYAN}refresh{C.RST}         Rafraîchir le cache des jeux
    {C.CYAN}help{C.RST}            Cette aide
    {C.CYAN}q{C.RST}               Quitter
""")
            continue

        print()
        # La première recherche construit l'index (cache ou scraping)
        results = do_search(raw, limit=limit, from_file=from_file,
                            force_refresh=False)
        displayed = display_results(results, raw)
        prompt_open(displayed or [])
        print()


def single_search(game_name: str, limit: int = 15,
                  auto_open: bool = False, from_file: str | None = None,
                  json_output: bool = False, force_refresh: bool = False):
    if not json_output:
        print(BANNER)

    results = do_search(game_name, limit=limit,
                        verbose=not json_output, from_file=from_file,
                        force_refresh=force_refresh)

    if json_output:
        out = {
            "query":   game_name,
            "count":   len(results),
            "results": [
                {
                    "name":      r["name"],
                    "url":       r["url"],
                    "slug":      r.get("slug", ""),
                    "date":      r.get("date", ""),
                    "genres":    r.get("genres", ""),
                    "companies": r.get("companies", ""),
                    "languages": r.get("languages", ""),
                    "version":   r.get("version", ""),
                    "size":      r.get("size", ""),
                    "score":     r.get("score", 0),
                }
                for r in results
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    displayed = display_results(results, game_name)

    if auto_open and displayed:
        print(f"  {C.GREEN}🌐 {displayed[0]['url']}{C.RST}\n")
        webbrowser.open(displayed[0]["url"])
    elif displayed:
        prompt_open(displayed)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🎮 AnkerGames Search CLI — ankergames.net",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                              # interactif
  python {sys.argv[0]} "Cult of the Lamb"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --json "Stardew Valley"
  python {sys.argv[0]} --from-file page.html "Elden Ring"
  python {sys.argv[0]} --refresh "Witcher"          # forcer le refresh
        """,
    )
    parser.add_argument("game", nargs="*", help="Nom du jeu à rechercher")
    parser.add_argument("-n", "--num", type=int, default=15,
                        help="Nombre max de résultats (défaut: 15)")
    parser.add_argument("-o", "--open", action="store_true",
                        help="Ouvrir le 1er résultat dans le navigateur")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Forcer le mode interactif")
    parser.add_argument("-j", "--json", action="store_true",
                        help="Sortie JSON (pour piping)")
    parser.add_argument("-f", "--from-file", type=str, default=None,
                        help="HTML local (Ctrl+S depuis navigateur)")
    parser.add_argument("-r", "--refresh", action="store_true",
                        help="Forcer le rafraîchissement du cache")

    args = parser.parse_args()

    if args.interactive or not args.game:
        interactive_mode(args.num, from_file=args.from_file)
    else:
        single_search(
            " ".join(args.game),
            limit=args.num,
            auto_open=args.open,
            from_file=args.from_file,
            json_output=args.json,
            force_refresh=args.refresh,
        )


if __name__ == "__main__":
    main()