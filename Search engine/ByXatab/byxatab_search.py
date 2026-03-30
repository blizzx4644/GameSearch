#!/usr/bin/env python3
"""
🎮 Byxatab Repacks Search CLI
Recherche via https://byxatab.com/?do=search&subaction=search&story={query}
Parsing HTML — site basé sur DataLife Engine (DLE).
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

BASE_URL        = "https://byxatab.com"
# DLE search : POST ou GET ?do=search&subaction=search&story=...
SEARCH_URL      = f"{BASE_URL}/?do=search&subaction=search&story="

CACHE_DIR       = Path.home() / ".byxatab_cache"
HISTORY_FILE    = CACHE_DIR / "search_history.json"
DEBUG_FILE      = CACHE_DIR / "debug_page.html"

MAX_PAGES       = 3   # Scrape jusqu'à 3 pages

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
 ║        🎮  Byxatab Repacks Search CLI  🎮                ║
 ║   Scraping HTML · byxatab.com (DLE Engine)               ║
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
        "Accept-Language":  "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer":          f"{BASE_URL}/",
    })
    return session


# ═══════════════════════════════════════════════════════════════
#  METHODE 1 : SCRAPING HTML via DLE search (GET)
# ═══════════════════════════════════════════════════════════════

def fetch_search_html(query: str, page: int = 1, session=None, verbose=True) -> str | None:
    """
    GET byxatab.com/?do=search&subaction=search&story={query}
    DLE supporte aussi la pagination via &search_start={page}
    """
    if session is None:
        session = _make_session()

    encoded = quote(query, safe='')

    if page > 1:
        url = f"{SEARCH_URL}{encoded}&search_start={page}"
    else:
        url = f"{SEARCH_URL}{encoded}"

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


def fetch_search_html_post(query: str, page: int = 1, session=None, verbose=True) -> str | None:
    """
    Fallback : POST /index.php?do=search  (méthode DLE classique).
    """
    if session is None:
        session = _make_session()

    url = f"{BASE_URL}/index.php?do=search"

    data = {
        "do":            "search",
        "subaction":     "search",
        "story":         query,
        "search_start":  str(page),
        "full_search":   "1",
        "result_from":   str((page - 1) * 10 + 1) if page > 1 else "1",
        "titleonly":     "0",
        "replyless":     "0",
        "replylimit":    "0",
        "searchdate":    "0",
        "searchuser":    "",
        "catlist[]":     "0",
        "sortby":        "date",
        "resorder":      "desc",
    }

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        session.headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            if verbose:
                print(f"  {C.DIM}🌐 POST {url} (story={query}, page={page}){C.RST}")

            resp = session.post(url, data=data, timeout=(10 if not verbose else 25))

            if resp.status_code == 200 and len(resp.text) > 500:
                lower = resp.text[:3000].lower()
                if "just a moment" in lower and "cloudflare" in lower:
                    if verbose:
                        print(f"  {C.YEL}⚠ Cloudflare challenge.{C.RST}")
                    continue

                if verbose:
                    print(f"  {C.GREEN}✔ Page POST reçue ({len(resp.text):,} octets){C.RST}")

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
    """Nettoie un titre : retire HTML entities, espaces multiples, etc."""
    text = unescape(text).strip()
    # Retirer " скачать торрент" et variantes de fin
    text = re.sub(r'\s*скачать\s+торрент.*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*скачать\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*Torrent\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*\[.*?\]\s*$', '', text).strip()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_game_meta(item_tag) -> dict:
    """
    Extrait les métadonnées d'un item de résultat byxatab.
    Structure DLE typique (byxatab) :
      <div class="entry"> ou <div class="shortstory">
        <div class="entry__title"> / <h2><a href="…">Titre</a></h2>
        <div class="entry__info"> → date, catégorie
        <div class="entry__content"> → description, infos
        <img src="…">
    """
    meta = {}

    # Tout le texte de l'item
    text = item_tag.get_text("\n", strip=True)

    # Année de sortie
    m = re.search(r'(?:Год\s*выхода|Дата\s*выхода|Release\s*date)\s*[:\-]?\s*(\d{4})', text, re.I)
    if m:
        meta["year"] = m.group(1)

    # Genre
    m = re.search(r'(?:Жанр|Genre)\s*[:\-]?\s*(.+?)(?:\n|$)', text, re.I)
    if m:
        meta["genres"] = m.group(1).strip().rstrip(',.')

    # Разработчик / Developer
    m = re.search(r'(?:Разработчик|Developer)\s*[:\-]?\s*(.+?)(?:\n|$)', text, re.I)
    if m:
        meta["developer"] = m.group(1).strip().rstrip(',.')

    # Издатель / Publisher
    m = re.search(r'(?:Издатель|Publisher)\s*[:\-]?\s*(.+?)(?:\n|$)', text, re.I)
    if m:
        meta["publisher"] = m.group(1).strip().rstrip(',.')

    # Версия / Version
    m = re.search(r'(?:Версия|Version)\s*[:\-]?\s*([\w\d.]+)', text, re.I)
    if m:
        meta["version"] = m.group(1)

    # Размер / Size
    m = re.search(r'(?:Размер|Size)\s*[:\-]?\s*([0-9.,]+\s*[КKMГGТT]?[Бб]?[B]?)', text, re.I)
    if m:
        meta["size"] = m.group(1).strip()

    # Repack size or download size
    m = re.search(r'(?:Размер\s*репака|Repack\s*[Ss]ize)\s*[:\-]?\s*([0-9.,]+\s*[КKMГGТT]?[Бб]?[B]?)', text, re.I)
    if m:
        meta["repack_size"] = m.group(1).strip()

    # Язык / Language
    m = re.search(r'(?:Язык|Языки?|Language)\s*[:\-]?\s*(.+?)(?:\n|$)', text, re.I)
    if m:
        meta["languages"] = m.group(1).strip().rstrip(',.')

    # Таблетка / Crack
    m = re.search(r'(?:Таблетка|Crack)\s*[:\-]?\s*(.+?)(?:\n|$)', text, re.I)
    if m:
        meta["crack"] = m.group(1).strip()

    return meta


def parse_search_html(html: str, verbose=True) -> list[dict]:
    """
    Parse le HTML de la page de recherche Byxatab.
    DLE structures possibles :
      - <div class="entry"> ou <div class="shortstory">
      - <article>
      - <div class="search-result"> ou similaire
    On essaie plusieurs stratégies.
    """
    soup = BeautifulSoup(html, "html.parser")
    games = []
    seen_urls = set()

    # ── Stratégie 1 : <div class="entry"> (byxatab typical) ──
    # Chercher les blocs d'entrée/article courants sur DLE
    selectors = [
        # Priorité : classes spécifiques byxatab
        {"class_": re.compile(r'\bentry\b')},
        {"class_": re.compile(r'\bshortstory\b')},
        {"class_": re.compile(r'\bshort-entry\b')},
        {"class_": re.compile(r'\bsearch[-_]?result\b')},
        {"class_": re.compile(r'\bitem\b')},
    ]

    articles = []
    for sel in selectors:
        articles = soup.find_all("div", **sel)
        if articles:
            break
    
    # Fallback : <article> tags
    if not articles:
        articles = soup.find_all("article")

    # Fallback : chercher des blocs contenant des liens vers des jeux
    if not articles:
        # Chercher tout div contenant un h2/h3 avec un lien
        for div in soup.find_all("div"):
            for h in div.find_all(["h1", "h2", "h3"], limit=1):
                a = h.find("a", href=True)
                if a and "byxatab" in a["href"]:
                    articles.append(div)
                    break

    if verbose:
        print(f"  {C.DIM}   📦 {len(articles)} blocs trouvés{C.RST}")

    for art in articles:
        # ── Trouver le titre ──
        title_tag = None
        a_tag = None

        # Chercher dans les headings h1-h3
        for h_level in ["h2", "h3", "h1", "h4"]:
            # Avec classe entry__title ou title
            t = art.find(h_level, class_=re.compile(r'title', re.I))
            if t:
                title_tag = t
                break
            # Sans classe spécifique
            t = art.find(h_level)
            if t and t.find("a", href=True):
                title_tag = t
                break

        # Fallback : div/span avec class *title*
        if not title_tag:
            title_tag = art.find(class_=re.compile(r'(?:entry[_-]?title|title)', re.I))

        if title_tag:
            a_tag = title_tag.find("a", href=True)
        
        # Fallback ultime : premier lien significatif
        if not a_tag:
            for a in art.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if text and len(text) > 3 and ("byxatab" in href or href.startswith("/")):
                    # Ignorer les liens "Lire plus" / "Подробнее"
                    if not re.search(r'(подробнее|читать|more|далее)', text, re.I):
                        a_tag = a
                        break

        if not a_tag:
            continue

        href = a_tag["href"].strip()
        name = _clean_title(a_tag.get_text(strip=True))

        if not name or len(name) < 2:
            continue

        # Dédup par URL
        key = href.rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)

        # URL absolue
        if href.startswith("/"):
            href = BASE_URL + href
        elif not href.startswith("http"):
            href = BASE_URL + "/" + href

        # Filtrer les pages non-jeu
        parsed = urlparse(href)
        path = parsed.path.strip("/")
        skip_prefixes = {
            "user", "index.php", "admin", "feedback", "contact",
            "rules", "register", "newposts", "favorites",
            "statistics", "pm", "tags",
        }
        first_seg = path.split("/")[0].lower() if path else ""
        if first_seg in skip_prefixes:
            continue

        # ── Date ──
        date = ""
        # Chercher une balise time
        time_tag = art.find("time")
        if time_tag:
            date = time_tag.get("datetime", time_tag.get_text(strip=True))
            if date:
                date = date[:10]
        else:
            # Chercher dans les classes info/date
            date_el = art.find(class_=re.compile(r'date|time|info', re.I))
            if date_el:
                date_text = date_el.get_text(strip=True)
                m = re.search(r'(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})', date_text)
                if m:
                    date = m.group(1)
                else:
                    m = re.search(r'(\d{4}[./\-]\d{1,2}[./\-]\d{1,2})', date_text)
                    if m:
                        date = m.group(1)

        # ── Image / thumbnail ──
        image = ""
        img = art.find("img")
        if img:
            image = img.get("src", img.get("data-src", img.get("data-lazy-src", "")))
            if image and not image.startswith("http"):
                image = BASE_URL + "/" + image.lstrip("/")

        # ── Catégories ──
        categories = []
        cat_el = art.find(class_=re.compile(r'categor|tag|genre', re.I))
        if cat_el:
            for cat_a in cat_el.find_all("a"):
                cat_text = cat_a.get_text(strip=True)
                if cat_text and len(cat_text) > 1:
                    categories.append(cat_text)

        # Si pas trouvé, chercher les liens de catégorie dans tout l'article
        if not categories:
            for a in art.find_all("a", href=True):
                if "/category/" in a["href"] or "/tags/" in a["href"]:
                    ct = a.get_text(strip=True)
                    if ct and len(ct) > 1:
                        categories.append(ct)

        # ── Métadonnées ──
        meta = _extract_game_meta(art)

        game = {
            "name":          name,
            "url":           href,
            "date":          date,
            "image":         image,
            "categories":    categories,
            "genres":        meta.get("genres", ""),
            "developer":     meta.get("developer", ""),
            "publisher":     meta.get("publisher", ""),
            "languages":     meta.get("languages", ""),
            "size":          meta.get("size", ""),
            "repack_size":   meta.get("repack_size", ""),
            "version":       meta.get("version", ""),
            "year":          meta.get("year", ""),
            "crack":         meta.get("crack", ""),
        }
        games.append(game)

    # ── Stratégie Fallback : tous les liens du domaine ──
    if not games:
        if verbose:
            print(f"  {C.DIM}   ⚠ Aucun bloc trouvé, fallback liens…{C.RST}")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "byxatab.com/" not in href and not href.startswith("/"):
                continue
            text = _clean_title(a.get_text(strip=True))
            if not text or len(text) < 4:
                continue

            parsed = urlparse(href)
            path = parsed.path.strip("/")
            if not path:
                continue

            # Garder seulement les pages qui ressemblent à des jeux
            # DLE : /1234-nom-du-jeu.html ou /games/nom.html
            if not re.search(r'\d+.*\.html$|/games/', path, re.I):
                # Possiblement un slug propre
                if "." in path.split("/")[-1] and not path.endswith(".html"):
                    continue

            skip_prefixes = {
                "user", "index.php", "admin", "feedback", "contact",
                "rules", "register", "newposts", "favorites",
                "statistics", "pm", "tags", "page",
            }
            first_seg = path.split("/")[0].lower()
            if first_seg in skip_prefixes:
                continue

            key = href.rstrip("/").lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)

            if not href.startswith("http"):
                href = BASE_URL + "/" + path

            games.append({
                "name": text, "url": href, "date": "", "image": "",
                "categories": [], "genres": "", "developer": "",
                "publisher": "", "languages": "", "size": "",
                "repack_size": "", "version": "", "year": "", "crack": "",
            })

    if verbose:
        print(f"  {C.DIM}   🎯 {len(games)} résultat(s) parsés depuis le HTML{C.RST}")

    return games


def _has_next_page(html: str) -> bool:
    """Détecte s'il y a une page suivante (pagination DLE)."""
    soup = BeautifulSoup(html, "html.parser")
    # DLE : <a class="pnext"> ou <span class="pnext"> ou navigation__next
    nxt = soup.find("a", class_=re.compile(r'next|pnext', re.I))
    if nxt:
        return True
    # Chercher les liens de pagination avec un numéro supérieur
    nav = soup.find(class_=re.compile(r'paginat|navigation|pages', re.I))
    if nav:
        links = nav.find_all("a", href=True)
        if links:
            return True
    return False


def search_html(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    """Recherche multi-pages via scraping HTML."""
    session = _make_session()

    # Pré-visite home (cookies Cloudflare / DLE)
    try:
        if verbose:
            print(f"  {C.DIM}🏠 Pré-visite {BASE_URL} (cookies)…{C.RST}")
        session.get(BASE_URL, timeout=(10 if not verbose else 15))
        time.sleep(0.1 if not verbose else 0.5)
    except Exception:
        pass

    all_games = []
    seen_urls = set()

    for page_num in range(1, max_pages + 1):
        # Essayer d'abord GET, puis POST si échec
        html = fetch_search_html(query, page=page_num, session=session, verbose=verbose)

        if not html or len(html) < 1000:
            if verbose:
                print(f"  {C.YEL}⚠ GET échoué, essai POST…{C.RST}")
            html = fetch_search_html_post(query, page=page_num, session=session, verbose=verbose)

        if not html:
            break

        # Vérifier s'il y a un message "rien trouvé"
        lower = html.lower()
        if "ничего не найдено" in lower or "не найдено" in lower:
            if verbose and page_num == 1:
                print(f"  {C.YEL}⚠ Le site indique : rien trouvé.{C.RST}")
            break

        games = parse_search_html(html, verbose=(verbose and page_num == 1))
        for g in games:
            key = g["url"].rstrip("/").lower()
            if key not in seen_urls:
                seen_urls.add(key)
                all_games.append(g)

        if verbose and page_num > 1:
            print(f"  {C.DIM}   📄 Page {page_num}: +{len(games)} résultats{C.RST}")

        if not _has_next_page(html):
            break

        time.sleep(0.1 if not verbose else 0.5)  # Politesse

    return all_games


# ═══════════════════════════════════════════════════════════════
#  RECHERCHE COMBINÉE
# ═══════════════════════════════════════════════════════════════

def search_online(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Scraping HTML — byxatab.com (DLE){C.RST}")

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
    text = re.sub(r'[^a-z0-9а-яё]+', ' ', text).strip()  # Garder aussi le cyrillique
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

        # Bonus si query dans developer / genres / publisher
        dev = normalize(g.get("developer", ""))
        genres = normalize(g.get("genres", ""))
        pub = normalize(g.get("publisher", ""))
        for field in [dev, genres, pub]:
            if q_norm in field:
                sc += 60
                break
            elif any(t in field for t in q_tokens):
                sc += 20
                break

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
        print(f"  {C.DIM}Essayez un autre terme (en anglais ou en russe).{C.RST}\n")
        return None

    has_scores = any("score" in r for r in results)
    mx = max(r.get("score", 1) for r in results) if has_scores else 1

    print(f"\n  {C.GREEN}{C.BOLD}✔ {len(results)} résultat(s) pour « {query} »{C.RST}")
    print(SEP)

    for i, r in enumerate(results, 1):
        # Nom + catégories
        cats = ""
        if r.get("categories"):
            cats = f"  {C.DIM}[{', '.join(r['categories'][:3])}]{C.RST}"
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}{cats}")

        # URL
        print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        # Métadonnées ligne 1
        meta = []
        if r.get("repack_size"):
            meta.append(f"📦 Repack: {r['repack_size']}")
        elif r.get("size"):
            meta.append(f"💿 Size: {r['size']}")
        if r.get("version"):
            meta.append(f"🔢 v{r['version']}")
        if r.get("year"):
            meta.append(f"📅 {r['year']}")
        elif r.get("date"):
            meta.append(f"📅 {r['date']}")
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

        if r.get("languages"):
            print(f"        {C.DIM}🌐 {r['languages']}{C.RST}")

        if r.get("crack"):
            print(f"        {C.DIM}💊 {r['crack']}{C.RST}")

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
  {C.YEL}{C.BOLD}💡 Solutions :{C.RST}
  {C.WHITE}1.{C.RST} pip install cloudscraper  (bypass Cloudflare)
  {C.WHITE}2.{C.RST} Sauvez la page HTML manuellement (Ctrl+S) puis :
     python {sys.argv[0]} --from-file "page.html" "query"
  {C.WHITE}3.{C.RST} Vérifiez l'accès :
     curl -sI {BASE_URL}
  {C.WHITE}4.{C.RST} Debug HTML sauvegardé : {DEBUG_FILE}
  {C.WHITE}5.{C.RST} Le site est en russe — essayez la recherche en anglais
     ou en russe (ex: "Elden Ring" ou "Элден Ринг")
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
        print(f"  {C.YEL}💡 pip install cloudscraper  (recommandé — Cloudflare){C.RST}")

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
    {C.CYAN}<nom du jeu>{C.RST}    Rechercher un repack (EN ou RU)
    {C.CYAN}history{C.RST}         Historique des recherches
    {C.CYAN}help{C.RST}            Cette aide
    {C.CYAN}q{C.RST}               Quitter

  {C.DIM}💡 byxatab.com est un site russe — les titres de jeux sont
     souvent en anglais, mais les descriptions en russe.{C.RST}
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
            "results": [
                {
                    "name":          r["name"],
                    "url":           r["url"],
                    "date":          r.get("date", ""),
                    "genres":        r.get("genres", ""),
                    "developer":     r.get("developer", ""),
                    "publisher":     r.get("publisher", ""),
                    "languages":     r.get("languages", ""),
                    "size":          r.get("size", ""),
                    "repack_size":   r.get("repack_size", ""),
                    "version":       r.get("version", ""),
                    "year":          r.get("year", ""),
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
        description="🎮 Byxatab Repacks Search CLI — byxatab.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                              # interactif
  python {sys.argv[0]} "Elden Ring"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --json "Stardew Valley"       # sortie JSON
  python {sys.argv[0]} --from-file page.html "Elden Ring"
  python {sys.argv[0]} --pages {MAX_PAGES} "Witcher"
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