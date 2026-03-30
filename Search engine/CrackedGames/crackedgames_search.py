#!/usr/bin/env python3
"""
🎮 Cracked-Games.org Search CLI
Recherche via https://cracked-games.org/search?q={query} + parsing HTML.
Système de scoring identique à fitgirl_search.py
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
from urllib.parse import quote_plus, urljoin, urlparse
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

BASE_URL        = "https://cracked-games.org"
SEARCH_URL      = f"{BASE_URL}/search"
GAMES_URL       = f"{BASE_URL}/games"

CACHE_DIR       = Path.home() / ".crackedgames_cache"
HISTORY_FILE    = CACHE_DIR / "search_history.json"
DEBUG_FILE      = CACHE_DIR / "debug_page.html"

# Nombre max de pages à scraper (si pagination disponible)
MAX_PAGES       = 3

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
    RST  = "\033[0m";  BOLD = "\033[1m";  DIM  = "\033[2m"
    CYAN = "\033[96m";  GREEN = "\033[92m"; YEL  = "\033[93m"
    RED  = "\033[91m";  MAG  = "\033[95m";  BLUE = "\033[94m"
    WHITE = "\033[97m"

BANNER = f"""{C.CYAN}{C.BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║      🎮  Cracked-Games.org Search CLI  🎮                ║
 ║   Scraping HTML · cracked-games.org/search               ║
 ╚═══════════════════════════════════════════════════════════╝{C.RST}
"""
SEP = f"  {C.DIM}{'─' * 62}{C.RST}"


# ═══════════════════════════════════════════════════════════════
#  HTTP SESSION
# ═══════════════════════════════════════════════════════════════

def _make_session():
    """Crée une session HTTP (cloudscraper si dispo, sinon requests)."""
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


# ═══════════════════════════════════════════════════════════════
#  SCRAPING HTML  — /search?q={query}
# ═══════════════════════════════════════════════════════════════

def fetch_search_html(query: str, page: int = 1, session=None, verbose=True) -> str | None:
    """
    GET cracked-games.org/search?q={query}[&page={page}] → HTML brut.
    Le site utilise le paramètre ?q= pour la recherche.
    """
    if session is None:
        session = _make_session()

    encoded = quote_plus(query)
    params = {"q": encoded}
    if page > 1:
        params["page"] = str(page)

    url = f"{SEARCH_URL}?q={encoded}" + (f"&page={page}" if page > 1 else "")

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        try:
            if verbose:
                print(f"  {C.DIM}🌐 GET {url}{C.RST}")

            resp = session.get(url, timeout=(10 if not verbose else 25))

            if resp.status_code == 200 and len(resp.text) > 500:
                lower = resp.text[:3000].lower()
                if "just a moment" in lower and "cloudflare" in lower:
                    if verbose:
                        print(f"  {C.YEL}⚠ Cloudflare challenge détecté.{C.RST}")
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


def _clean_title(text: str) -> str:
    """Nettoie un titre : retire suffixes, HTML entities, etc."""
    text = unescape(text).strip()
    text = re.sub(r'\s*–\s*$', '', text).strip()
    # Retirer "Free Download" et variantes
    text = re.sub(r'\s*[\-–]\s*Free\s+Download.*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s+Free\s+Download\s*$', '', text, flags=re.I).strip()
    # Retirer "Cracked" suffixe
    text = re.sub(r'\s*[\-–]\s*Cracked\s*$', '', text, flags=re.I).strip()
    # Retirer "| CRACKED-GAMES.ORG"
    text = re.sub(r'\s*\|\s*CRACKED-GAMES\.ORG\s*$', '', text, flags=re.I).strip()
    return text


def _extract_game_meta(card_tag) -> dict:
    """
    Extrait les métadonnées d'une carte/article de résultat.
    Adapté à la structure HTML de cracked-games.org.
    Le site affiche typiquement :
      - Nom du jeu
      - Genre / Tags
      - Taille du fichier
      - Groupe de release (TENOKE, RUNE, ElAmigos, etc.)
      - Date de sortie / date d'ajout
    """
    meta = {}

    text = card_tag.get_text("\n", strip=True)

    # Genre / Tags
    m = re.search(r'Genres?\s*[:/]\s*(.+)', text, re.I)
    if m:
        meta["genres"] = m.group(1).split("\n")[0].strip()
    else:
        # Parfois affiché comme badge/tag
        tags = card_tag.find_all(class_=re.compile(r'(?:tag|genre|badge|category)', re.I))
        if tags:
            genre_list = [t.get_text(strip=True) for t in tags if len(t.get_text(strip=True)) > 1]
            if genre_list:
                meta["genres"] = ", ".join(genre_list[:5])

    # Release group
    m = re.search(r'(?:Release\s+Group|Cracked\s+by|Scene)\s*[:/]\s*(.+)', text, re.I)
    if m:
        meta["release_group"] = m.group(1).split("\n")[0].strip()

    # Taille
    m = re.search(r'(?:Size|Taille|Download\s+Size)\s*[:/]\s*([0-9.,]+\s*[KMGT]?B)', text, re.I)
    if m:
        meta["size"] = m.group(1).strip()
    else:
        # Chercher un pattern de taille isolé (ex: "60.9 GB")
        m = re.search(r'(\d+\.?\d*)\s*(GB|MB|TB)', text, re.I)
        if m:
            meta["size"] = f"{m.group(1)} {m.group(2).upper()}"

    # Date
    m = re.search(r'(\d{1,2}\s+\w{3,9},?\s+\d{4}|\d{4}[-/]\d{2}[-/]\d{2})', text)
    if m:
        meta["date"] = m.group(1).strip()

    # Developer / Publisher
    m = re.search(r'Developer\s*[:/]\s*(.+)', text, re.I)
    if m:
        meta["developer"] = m.group(1).split("\n")[0].strip()

    m = re.search(r'Publisher\s*[:/]\s*(.+)', text, re.I)
    if m:
        meta["publisher"] = m.group(1).split("\n")[0].strip()

    # Build / Version
    m = re.search(r'(?:Build|Version|v)\s*[:/]?\s*(\S+)', text, re.I)
    if m:
        meta["version"] = m.group(1).strip()

    return meta


def parse_search_html(html: str, verbose=True) -> list[dict]:
    """
    Parse le HTML de la page de recherche cracked-games.org.

    Plusieurs stratégies de parsing :
    1. Cards de jeu (<div class="game-card"> ou similaire)
    2. Éléments de liste (<article>, <div class="card">, etc.)
    3. Liens avec href contenant le domaine (fallback)
    """
    soup = BeautifulSoup(html, "html.parser")
    games = []
    seen_urls = set()

    # ── Stratégie 1 : Cards de jeu (CSS class game-card, game-item, card, etc.) ──
    card_selectors = [
        # Sélecteurs courants pour les cards de jeu
        {"class_": re.compile(r'game[-_]?card|game[-_]?item|game[-_]?entry|game[-_]?block', re.I)},
        {"class_": re.compile(r'^card$|card[-_]?item|product[-_]?card', re.I)},
        {"class_": re.compile(r'search[-_]?result|result[-_]?item|result[-_]?card', re.I)},
        {"class_": re.compile(r'post[-_]?card|post[-_]?item|entry[-_]?card', re.I)},
    ]

    cards = []
    for selector in card_selectors:
        found = soup.find_all("div", **selector)
        if not found:
            found = soup.find_all("a", **selector)
        if not found:
            found = soup.find_all("li", **selector)
        if found:
            cards = found
            if verbose:
                print(f"  {C.DIM}   🔍 Trouvé {len(cards)} cards via sélecteur {selector}{C.RST}")
            break

    # Essayer aussi <article>
    if not cards:
        cards = soup.find_all("article")
        if cards and verbose:
            print(f"  {C.DIM}   🔍 Trouvé {len(cards)} <article>{C.RST}")

    for card in cards:
        # Trouver le lien principal
        a_tag = None
        # Chercher dans h1/h2/h3/h4 d'abord
        for h in ["h1", "h2", "h3", "h4", "h5"]:
            ht = card.find(h)
            if ht:
                a = ht.find("a", href=True)
                if a:
                    a_tag = a
                    break

        # Si pas trouvé dans les headers, chercher le premier <a> significatif
        if not a_tag:
            # Si la card elle-même est un <a>
            if card.name == "a" and card.get("href"):
                a_tag = card
            else:
                for a in card.find_all("a", href=True):
                    href = a["href"].strip()
                    text = a.get_text(strip=True)
                    # Filtrer les liens de navigation / petits liens
                    if text and len(text) >= 3 and "cracked-games" in href or href.startswith("/"):
                        a_tag = a
                        break

        if not a_tag:
            continue

        href = a_tag["href"].strip()
        name = _clean_title(a_tag.get_text(strip=True))

        # Fallback : titre depuis un h-tag dans la card
        if not name or len(name) < 2:
            for h in ["h1", "h2", "h3", "h4", "h5"]:
                ht = card.find(h)
                if ht:
                    name = _clean_title(ht.get_text(strip=True))
                    if name and len(name) >= 2:
                        break

        if not name or len(name) < 2:
            continue

        # URL absolue
        if href.startswith("/"):
            href = BASE_URL + href
        elif not href.startswith("http"):
            href = BASE_URL + "/" + href

        # Dédup par URL
        key = href.rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)

        # Filtrer les pages non-jeu
        parsed = urlparse(href)
        path = parsed.path.strip("/")
        skip = {"about", "contact", "faq", "dmca", "request", "privacy",
                "terms", "category", "tag", "author", "page", "wp-admin",
                "donate", "donations", "how-to", "search", "login",
                "register", "account", "sitemap", "legal", "imprint"}
        if path and path.split("/")[0].lower() in skip:
            continue

        # Image / thumbnail
        image = ""
        img = card.find("img")
        if img:
            image = img.get("src", img.get("data-src", img.get("data-lazy-src", "")))

        # Date
        date = ""
        time_tag = card.find("time")
        if time_tag:
            date = time_tag.get("datetime", time_tag.get_text(strip=True))
            if date:
                date = date[:10]

        # Catégories / badges
        categories = []
        for badge in card.find_all(class_=re.compile(r'badge|category|tag|label|chip', re.I)):
            txt = badge.get_text(strip=True)
            if txt and len(txt) > 1 and len(txt) < 40:
                categories.append(txt)
        categories = list(dict.fromkeys(categories))[:6]  # Dédup + limite

        # Métadonnées
        meta = _extract_game_meta(card)

        game = {
            "name":           name,
            "url":            href,
            "date":           date or meta.get("date", ""),
            "image":          image,
            "categories":     categories,
            "genres":         meta.get("genres", ""),
            "developer":      meta.get("developer", ""),
            "publisher":      meta.get("publisher", ""),
            "size":           meta.get("size", ""),
            "release_group":  meta.get("release_group", ""),
            "version":        meta.get("version", ""),
        }
        games.append(game)

    # ── Stratégie 2 : Grille / liste de liens (fallback si pas de cards) ──
    if not games:
        if verbose:
            print(f"  {C.DIM}   ⚠ Aucune card trouvée, fallback liens directs…{C.RST}")

        # Chercher des <a> qui pointent vers des pages de jeu
        # Pattern typique : /game/nom-du-jeu ou /nom-du-jeu.html
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()

            # Ne garder que les liens internes
            is_internal = (
                "cracked-games.org" in href
                or href.startswith("/")
            )
            if not is_internal:
                continue

            text = _clean_title(a.get_text(strip=True))
            if not text or len(text) < 3:
                continue

            # URL absolue
            if href.startswith("/"):
                full_href = BASE_URL + href
            elif not href.startswith("http"):
                full_href = BASE_URL + "/" + href
            else:
                full_href = href

            parsed = urlparse(full_href)
            path = parsed.path.strip("/")

            # Filtrer les pages système
            skip = {"about", "contact", "faq", "dmca", "request", "privacy",
                    "terms", "category", "tag", "author", "page", "search",
                    "login", "register", "account", "games", "software",
                    "sitemap", "legal", "imprint", "wp-admin", "donate",
                    "css", "js", "images", "assets", "static", "fonts"}

            if not path:
                continue
            first_segment = path.split("/")[0].lower()
            if first_segment in skip:
                continue

            # Un lien vers un jeu a généralement un slug significatif
            if len(path) < 3:
                continue

            key = full_href.rstrip("/").lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)

            # Image à proximité
            image = ""
            parent = a.parent
            if parent:
                img = parent.find("img")
                if img:
                    image = img.get("src", img.get("data-src", ""))

            games.append({
                "name":          text,
                "url":           full_href,
                "date":          "",
                "image":         image,
                "categories":    [],
                "genres":        "",
                "developer":     "",
                "publisher":     "",
                "size":          "",
                "release_group": "",
                "version":       "",
            })

    # ── Stratégie 3 : Parsing JSON embarqué (certains sites SSR/Next.js) ──
    if not games:
        if verbose:
            print(f"  {C.DIM}   ⚠ Tentative parsing JSON embarqué…{C.RST}")

        # Chercher des blocs <script type="application/json"> ou __NEXT_DATA__
        for script in soup.find_all("script"):
            text = script.string or ""
            if "__NEXT_DATA__" in text or '"props"' in text:
                try:
                    # Extraire le JSON
                    m = re.search(r'(\{.*\})', text, re.DOTALL)
                    if m:
                        data = json.loads(m.group(1))
                        games.extend(_parse_embedded_json(data))
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
                break

            # JSON-LD
            if script.get("type") == "application/ld+json":
                try:
                    data = json.loads(text)
                    if isinstance(data, list):
                        for item in data:
                            if item.get("@type") in ("Product", "Game", "SoftwareApplication"):
                                name = _clean_title(item.get("name", ""))
                                url = item.get("url", "")
                                if name and url:
                                    games.append({
                                        "name": name, "url": url, "date": "",
                                        "image": item.get("image", ""),
                                        "categories": [], "genres": "",
                                        "developer": "", "publisher": "",
                                        "size": "", "release_group": "",
                                        "version": "",
                                    })
                except (json.JSONDecodeError, KeyError):
                    pass

    # Dédupliquer une dernière fois
    final = []
    final_urls = set()
    for g in games:
        k = g["url"].rstrip("/").lower()
        if k not in final_urls:
            final_urls.add(k)
            final.append(g)

    if verbose:
        print(f"  {C.DIM}   🎯 {len(final)} résultat(s) parsés depuis le HTML{C.RST}")

    return final


def _parse_embedded_json(data: dict) -> list[dict]:
    """Tente d'extraire des jeux depuis un JSON embarqué (Next.js, etc.)."""
    games = []

    # Next.js : data → props → pageProps → ...
    def _recurse(obj, depth=0):
        if depth > 8:
            return
        if isinstance(obj, dict):
            # Chercher des objets qui ressemblent à des jeux
            if "name" in obj and ("url" in obj or "slug" in obj or "href" in obj):
                name = _clean_title(str(obj.get("name", "")))
                url = obj.get("url") or obj.get("href") or ""
                slug = obj.get("slug", "")
                if not url and slug:
                    url = f"{BASE_URL}/{slug}"
                if url and not url.startswith("http"):
                    url = BASE_URL + "/" + url.lstrip("/")
                if name and len(name) >= 3:
                    games.append({
                        "name":          name,
                        "url":           url,
                        "date":          str(obj.get("date", obj.get("releaseDate", "")))[:10],
                        "image":         obj.get("image", obj.get("thumbnail", obj.get("cover", ""))),
                        "categories":    [],
                        "genres":        obj.get("genre", obj.get("genres", "")),
                        "developer":     obj.get("developer", ""),
                        "publisher":     obj.get("publisher", ""),
                        "size":          obj.get("size", obj.get("fileSize", "")),
                        "release_group": obj.get("releaseGroup", obj.get("scene", "")),
                        "version":       obj.get("version", obj.get("build", "")),
                    })
            for v in obj.values():
                _recurse(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item, depth + 1)

    _recurse(data)
    return games


def _has_next_page(html: str) -> bool:
    """Détecte s'il y a une page suivante (pagination)."""
    soup = BeautifulSoup(html, "html.parser")

    # Chercher un lien "Next" ou ">"
    for a in soup.find_all("a", href=True):
        classes = " ".join(a.get("class", [])).lower()
        text = a.get_text(strip=True).lower()
        if "next" in classes or "next" in text or text in (">", "›", "»"):
            return True

    # Chercher un bouton next
    for btn in soup.find_all("button"):
        classes = " ".join(btn.get("class", [])).lower()
        text = btn.get_text(strip=True).lower()
        if "next" in classes or "next" in text:
            return True

    # Pattern de pagination (Page X of Y)
    text = soup.get_text()
    m = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', text, re.I)
    if m and int(m.group(1)) < int(m.group(2)):
        return True

    return False


def search_html(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    """Recherche multi-pages via scraping HTML."""
    session = _make_session()

    # Pré-visite home (cookies, CF challenge)
    try:
        if verbose:
            print(f"  {C.DIM}🏠 Pré-visite {BASE_URL}…{C.RST}")
        session.get(BASE_URL, timeout=15)
        time.sleep(0.5)
    except Exception:
        pass

    all_games = []
    seen_urls = set()

    for page_num in range(1, max_pages + 1):
        html = fetch_search_html(query, page=page_num, session=session, verbose=verbose)
        if not html:
            break

        games = parse_search_html(html, verbose=(verbose and page_num == 1))
        for g in games:
            key = g["url"].rstrip("/").lower()
            if key not in seen_urls:
                seen_urls.add(key)
                all_games.append(g)

        if verbose and page_num > 1:
            print(f"  {C.DIM}   📄 Page {page_num}: +{len(games)} résultats{C.RST}")

        # Vérifier s'il y a une page suivante
        if not _has_next_page(html):
            break

        time.sleep(0.5)  # Politesse

    return all_games


# ═══════════════════════════════════════════════════════════════
#  RECHERCHE COMBINÉE
# ═══════════════════════════════════════════════════════════════

def search_online(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Scraping HTML : cracked-games.org{C.RST}")

    results = search_html(query, max_pages=max_pages, verbose=verbose)

    if not results and verbose:
        _print_debug_help()

    return results


# ═══════════════════════════════════════════════════════════════
#  SCORING LOCAL  (identique à fitgirl_search.py)
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

    # Bonus : le nom du jeu commence par la query
    if g_norm.startswith(q_norm):
        score += 600

    # Bonus : query contenue dans le nom
    if q_norm in g_norm:
        score += 400
    elif g_norm in q_norm:
        score += 200

    # Couverture des tokens
    found = sum(1 for t in q_tokens if any(t in gt for gt in g_tokens))
    cov = found / len(q_tokens) if q_tokens else 0
    score += cov * 300
    if cov == 1.0:
        score += 150

    # Substring match des tokens
    sub = sum(1 for qt in q_tokens if any(qt in gt for gt in g_tokens))
    score += (sub / max(len(q_tokens), 1)) * 100

    # Jaccard + couverture tokens
    sa, sb = set(q_tokens), set(g_tokens)
    inter = sa & sb
    union = sa | sb
    if union:
        jaccard = len(inter) / len(union)
        tcov = len(inter) / len(sa) if sa else 0
        score += (jaccard * 0.4 + tcov * 0.6) * 200

    # Similarité séquentielle
    score += SequenceMatcher(None, q_norm, g_norm).ratio() * 150

    # Similarité tokens triés
    qs = " ".join(sorted(q_tokens))
    gs = " ".join(sorted(g_tokens))
    score += SequenceMatcher(None, qs, gs).ratio() * 120

    # Partial ratio
    score += partial_ratio(q_norm, g_norm) * 250

    # Acronyme
    if len(q_tokens) == 1 and len(q_norm) <= 6:
        acr = "".join(t[0] for t in g_tokens if t)
        if q_norm == acr:
            score += 500

    # Préfixe match
    pre = sum(1 for qt in q_tokens if any(gt.startswith(qt) for gt in g_tokens))
    score += (pre / max(len(q_tokens), 1)) * 100

    # Pénalité de longueur
    score -= abs(len(q_norm) - len(g_norm)) * 0.8

    # Bonus même nombre de tokens
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

        # Bonus si query dans developer / publisher / genres
        dev = normalize(g.get("developer", ""))
        pub = normalize(g.get("publisher", ""))
        genres = normalize(g.get("genres", ""))
        if q_norm in dev or q_norm in pub or q_norm in genres:
            sc += 60
        elif any(t in dev or t in pub or t in genres for t in q_tokens):
            sc += 20

        g["score"] = round(sc, 1)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
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
        # Nom + catégories
        cats = ""
        if r.get("categories"):
            cats = f"  {C.DIM}[{', '.join(r['categories'][:4])}]{C.RST}"
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}{cats}")

        # URL
        print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        # Métadonnées ligne 1
        meta = []
        if r.get("size"):
            meta.append(f"📦 {r['size']}")
        if r.get("release_group"):
            meta.append(f"🏴 {r['release_group']}")
        if r.get("date"):
            meta.append(f"📅 {r['date']}")
        if r.get("version"):
            meta.append(f"🔖 {r['version']}")
        if meta:
            print(f"        {C.DIM}{' │ '.join(meta)}{C.RST}")

        # Métadonnées ligne 2
        meta2 = []
        if r.get("genres"):
            meta2.append(f"🏷  {r['genres']}")
        if r.get("developer"):
            meta2.append(f"🛠  {r['developer']}")
        if r.get("publisher"):
            meta2.append(f"📤 {r['publisher']}")
        if meta2:
            print(f"        {C.DIM}{' │ '.join(meta2)}{C.RST}")

        # Score
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
  {C.YEL}{C.BOLD}💡 Aucun résultat — Solutions possibles :{C.RST}
  {C.WHITE}1.{C.RST} pip install cloudscraper  (bypass Cloudflare)
  {C.WHITE}2.{C.RST} Sauvez la page HTML manuellement (Ctrl+S) puis :
     python {sys.argv[0]} --from-file "page.html" "query"
  {C.WHITE}3.{C.RST} Vérifiez l'accès :
     curl -sI {BASE_URL}
  {C.WHITE}4.{C.RST} Debug HTML sauvegardé : {DEBUG_FILE}
  {C.WHITE}5.{C.RST} Le site peut avoir changé de structure HTML.
     Inspectez {DEBUG_FILE} pour adapter le parser.
""")


# ═══════════════════════════════════════════════════════════════
#  MODES
# ═══════════════════════════════════════════════════════════════

def do_search(query: str, limit: int = 15, max_pages: int = MAX_PAGES,
              verbose=True, from_file: str | None = None) -> list[dict]:
    """Recherche complète : online ou fichier local → ranking."""

    if not verbose:
        max_pages = min(int(max_pages or 1), 1)

    if from_file:
        p = Path(from_file)
        if p.exists():
            if verbose:
                print(f"  {C.DIM}📂 Chargement depuis {p}{C.RST}")
            html = p.read_text(encoding="utf-8", errors="replace")
            results = parse_search_html(html, verbose)
        else:
            print(f"  {C.RED}✖ Fichier introuvable : {p}{C.RST}")
            return []
    else:
        results = search_online(query, max_pages=max_pages, verbose=verbose)

    if results:
        results = rank_results(query, results, limit=limit)

    save_to_history(query, results)
    return results


def interactive_mode(limit: int = 15, max_pages: int = MAX_PAGES,
                     from_file: str | None = None):
    print(BANNER)

    if not HAS_CLOUDSCRAPER:
        print(f"  {C.YEL}💡 pip install cloudscraper  (recommandé pour bypass Cloudflare){C.RST}")

    print(f"  {C.DIM}Commandes :  <jeu>  │  history  │  help  │  q{C.RST}\n")

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

        if cmd == "help":
            print(f"""
  {C.WHITE}{C.BOLD}Commandes :{C.RST}
    {C.CYAN}<nom du jeu>{C.RST}    Rechercher un jeu cracké
    {C.CYAN}history{C.RST}         Historique des recherches
    {C.CYAN}help{C.RST}            Cette aide
    {C.CYAN}q{C.RST}               Quitter
""")
            continue

        print()
        results = do_search(raw, limit=limit, max_pages=max_pages,
                            from_file=from_file)
        displayed = display_results(results, raw)
        prompt_open(displayed or [])
        print()


def single_search(game_name: str, limit: int = 15, max_pages: int = MAX_PAGES,
                  auto_open: bool = False, from_file: str | None = None,
                  json_output: bool = False):
    if not json_output:
        print(BANNER)

    results = do_search(game_name, limit=limit, max_pages=max_pages,
                        verbose=not json_output, from_file=from_file)

    if json_output:
        out = {
            "query":   game_name,
            "count":   len(results),
            "source":  "cracked-games.org",
            "results": [
                {
                    "name":          r["name"],
                    "url":           r["url"],
                    "date":          r.get("date", ""),
                    "genres":        r.get("genres", ""),
                    "developer":     r.get("developer", ""),
                    "publisher":     r.get("publisher", ""),
                    "size":          r.get("size", ""),
                    "release_group": r.get("release_group", ""),
                    "version":       r.get("version", ""),
                    "score":         r.get("score", 0),
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
        description="🎮 Cracked-Games.org Search CLI — cracked-games.org",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                              # interactif
  python {sys.argv[0]} "Elden Ring"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --json "Stardew Valley"       # sortie JSON
  python {sys.argv[0]} --from-file page.html "Elden Ring"
  python {sys.argv[0]} --pages {MAX_PAGES} "Witcher"            # {MAX_PAGES} pages max
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
    parser.add_argument("-p", "--pages", type=int, default=MAX_PAGES,
                        help=f"Pages max à scraper (défaut: {MAX_PAGES})")

    args = parser.parse_args()

    if args.interactive or not args.game:
        interactive_mode(args.num, max_pages=args.pages, from_file=args.from_file)
    else:
        single_search(
            " ".join(args.game),
            limit=args.num,
            max_pages=args.pages,
            auto_open=args.open,
            from_file=args.from_file,
            json_output=args.json,
        )


if __name__ == "__main__":
    main()