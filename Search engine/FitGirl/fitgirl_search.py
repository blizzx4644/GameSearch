#!/usr/bin/env python3
"""
🎮 FitGirl Repacks Search CLI
Recherche via https://fitgirl-repacks.site/?s={query} + parsing HTML.
Fallback WordPress REST API : /wp-json/wp/v2/posts?search={query}
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

BASE_URL        = "https://fitgirl-repacks.site"
SEARCH_URL      = f"{BASE_URL}/?s="
# WordPress REST API (site = WordPress)
WP_API_SEARCH   = f"{BASE_URL}/wp-json/wp/v2/posts"

CACHE_DIR       = Path.home() / ".fitgirl_cache"
HISTORY_FILE    = CACHE_DIR / "search_history.json"
DEBUG_FILE      = CACHE_DIR / "debug_page.html"
DEBUG_JSON_FILE = CACHE_DIR / "debug_api.json"

# FitGirl search = 10 résultats par page
MAX_PAGES       = 3   # On scrape jusqu'à 3 pages (30 résultats)

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
 ║        🎮  FitGirl Repacks Search CLI  🎮                ║
 ║   Scraping HTML + WP API · fitgirl-repacks.site          ║
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
        "User-Agent":      USER_AGENTS[0],
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":  "en-US,en;q=0.9",
        "Referer":          f"{BASE_URL}/",
    })
    return session


# ═══════════════════════════════════════════════════════════════
#  METHODE 1 : SCRAPING HTML  — ?s={query}
# ═══════════════════════════════════════════════════════════════

def fetch_search_html(query: str, page: int = 1, session=None, verbose=True) -> str | None:
    """GET fitgirl-repacks.site/?s={query}&paged={page} → HTML brut."""
    if session is None:
        session = _make_session()

    encoded = quote_plus(query)
    if page > 1:
        url = f"{SEARCH_URL}{encoded}&paged={page}"
    else:
        url = f"{SEARCH_URL}{encoded}"

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        try:
            if verbose:
                print(f"  {C.DIM}🌐 GET {url}{C.RST}")

            resp = session.get(url, timeout=(10 if not verbose else 25))

            if resp.status_code == 200 and len(resp.text) > 1000:
                lower = resp.text[:3000].lower()
                if "just a moment" in lower and "cloudflare" in lower:
                    if verbose:
                        print(f"  {C.YEL}⚠ Cloudflare challenge.{C.RST}")
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
    """Nettoie un titre FitGirl : retire les suffixes courants."""
    text = unescape(text).strip()
    # Retirer suffixes de type « + All DLCs + Bonus »
    # mais garder le nom du jeu principal
    text = re.sub(r'\s*–\s*$', '', text).strip()
    return text


def _extract_repack_meta(article_tag) -> dict:
    """
    Extrait les métadonnées d'un article de résultat FitGirl.
    Structure WordPress typique :
      <article class="post-xxx … category-lossless-repack">
        <header>
          <h1 class="entry-title"><a href="…">Titre du Jeu</a></h1>
          <time datetime="2024-…">…</time>
        </header>
        <div class="entry-content">
          <p>Genres/Tags: …</p>
          <p>Companies: …</p>
          <p>Original Size: … / Repack Size: …</p>
        </div>
      </article>
    """
    meta = {}

    # ── Entry content text ──
    content = article_tag.find("div", class_="entry-content")
    if not content:
        content = article_tag

    text = content.get_text("\n", strip=True)

    # Genres
    m = re.search(r'Genres?\s*/?\s*Tags?\s*:\s*(.+)', text, re.I)
    if m:
        meta["genres"] = m.group(1).split("\n")[0].strip()

    # Companies
    m = re.search(r'Compan(?:y|ies)\s*:\s*(.+)', text, re.I)
    if m:
        meta["companies"] = m.group(1).split("\n")[0].strip()

    # Languages
    m = re.search(r'Languages?\s*:\s*(.+)', text, re.I)
    if m:
        meta["languages"] = m.group(1).split("\n")[0].strip()

    # Original Size
    m = re.search(r'Original\s+Size\s*:\s*([0-9.,]+\s*[KMGT]?B)', text, re.I)
    if m:
        meta["original_size"] = m.group(1).strip()

    # Repack Size
    m = re.search(r'Repack\s+Size\s*:\s*((?:from\s+)?[0-9.,]+\s*[KMGT]?B)', text, re.I)
    if m:
        meta["repack_size"] = m.group(1).strip()

    return meta


def parse_search_html(html: str, verbose=True) -> list[dict]:
    """
    Parse le HTML de la page de recherche FitGirl.
    Structure : <article> contenant <h1 class="entry-title"><a href="…">Titre</a></h1>
    """
    soup = BeautifulSoup(html, "html.parser")
    games = []
    seen_urls = set()

    # ── Stratégie 1 : <article> avec h1.entry-title (WordPress classique) ──
    articles = soup.find_all("article")

    for art in articles:
        # Trouver le titre : h1.entry-title > a  ou  h2.entry-title > a
        title_tag = None
        for h_level in ["h1", "h2", "h3"]:
            t = art.find(h_level, class_="entry-title")
            if t:
                title_tag = t
                break
            # Fallback sans classe
            t = art.find(h_level)
            if t and t.find("a", href=True):
                title_tag = t
                break

        if not title_tag:
            continue

        a_tag = title_tag.find("a", href=True)
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

        # Filtrer les non-jeux (pages about, contact, etc.)
        parsed = urlparse(href)
        path = parsed.path.strip("/")
        skip = {"about", "contact", "faq", "dmca", "request", "privacy",
                "terms", "category", "tag", "author", "page", "wp-admin",
                "donate", "donations", "how-to"}
        if path.split("/")[0].lower() in skip:
            continue

        # Date
        time_tag = art.find("time")
        date = ""
        if time_tag:
            date = time_tag.get("datetime", time_tag.get_text(strip=True))
            if date:
                date = date[:10]

        # Image / thumbnail
        image = ""
        img = art.find("img")
        if img:
            image = img.get("src", img.get("data-src", ""))

        # Catégories (depuis les classes CSS de l'article)
        classes = " ".join(art.get("class", []))
        categories = []
        for cls in art.get("class", []):
            if cls.startswith("category-"):
                cat = cls.replace("category-", "").replace("-", " ").title()
                categories.append(cat)

        # Métadonnées depuis le contenu
        meta = _extract_repack_meta(art)

        game = {
            "name":          name,
            "url":           href,
            "date":          date,
            "image":         image,
            "categories":    categories,
            "genres":        meta.get("genres", ""),
            "companies":     meta.get("companies", ""),
            "languages":     meta.get("languages", ""),
            "original_size": meta.get("original_size", ""),
            "repack_size":   meta.get("repack_size", ""),
        }
        games.append(game)

    # ── Stratégie 2 : Fallback – tous les <a> avec href contenant le domaine ──
    if not games:
        if verbose:
            print(f"  {C.DIM}   ⚠ Aucun <article> trouvé, fallback liens…{C.RST}")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "fitgirl-repacks.site/" not in href and not href.startswith("/"):
                continue
            text = _clean_title(a.get_text(strip=True))
            if not text or len(text) < 4:
                continue

            parsed = urlparse(href)
            path = parsed.path.strip("/")
            if not path or "/" in path:
                continue
            skip = {"about", "contact", "faq", "dmca", "request",
                    "privacy", "category", "tag", "author", "page"}
            if path.split("/")[0].lower() in skip:
                continue

            key = href.rstrip("/").lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)

            if not href.startswith("http"):
                href = BASE_URL + "/" + path + "/"

            games.append({
                "name": text, "url": href, "date": "", "image": "",
                "categories": [], "genres": "", "companies": "",
                "languages": "", "original_size": "", "repack_size": "",
            })

    if verbose:
        print(f"  {C.DIM}   🎯 {len(games)} résultat(s) parsés depuis le HTML{C.RST}")

    return games


def _has_next_page(html: str) -> bool:
    """Détecte s'il y a une page suivante (pagination WordPress)."""
    soup = BeautifulSoup(html, "html.parser")
    # <a class="next page-numbers"> ou <a class="next">
    nxt = soup.find("a", class_="next")
    if nxt:
        return True
    nxt = soup.find("a", class_=re.compile(r'\bnext\b'))
    if nxt:
        return True
    return False


def search_html(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    """Recherche multi-pages via scraping HTML."""
    session = _make_session()

    # Pré-visite home (cookies)
    try:
        session.get(BASE_URL, timeout=(10 if not verbose else 15))
        time.sleep(0.1 if not verbose else 0.3)
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

        time.sleep(0.1 if not verbose else 0.5)  # Politesse

    return all_games


# ═══════════════════════════════════════════════════════════════
#  METHODE 2 : WORDPRESS REST API (fallback)
# ═══════════════════════════════════════════════════════════════

def search_wp_api(query: str, verbose=True) -> list[dict]:
    """
    Recherche via l'API WordPress REST :
    GET /wp-json/wp/v2/posts?search={query}&per_page=20
    Retourne du JSON natif — pas besoin de BS4.
    """
    session = _make_session()
    session.headers["Accept"] = "application/json"

    params = {
        "search":   query,
        "per_page":  20,
        "orderby":   "relevance",
        "_fields":   "id,title,link,date,excerpt,categories",
    }

    try:
        if verbose:
            print(f"  {C.DIM}🌐 WP API: {WP_API_SEARCH}?search={query}{C.RST}")

        resp = session.get(WP_API_SEARCH, params=params, timeout=(10 if not verbose else 20))

        if resp.status_code == 200:
            data = resp.json()

            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            DEBUG_JSON_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2)[:300_000],
                encoding="utf-8"
            )

            if not isinstance(data, list):
                if verbose:
                    print(f"  {C.YEL}⚠ Réponse API inattendue{C.RST}")
                return []

            if verbose:
                print(f"  {C.GREEN}✔ API: {len(data)} résultat(s){C.RST}")

            games = []
            for post in data:
                title_raw = post.get("title", {})
                if isinstance(title_raw, dict):
                    name = unescape(title_raw.get("rendered", "")).strip()
                else:
                    name = unescape(str(title_raw)).strip()

                url = post.get("link", "")
                date = (post.get("date") or "")[:10]

                # Excerpt peut contenir des infos
                excerpt_raw = post.get("excerpt", {})
                if isinstance(excerpt_raw, dict):
                    excerpt_html = excerpt_raw.get("rendered", "")
                else:
                    excerpt_html = str(excerpt_raw)

                excerpt_text = BeautifulSoup(excerpt_html, "html.parser").get_text("\n", strip=True)

                meta = {}
                m = re.search(r'Genres?\s*/?\s*Tags?\s*:\s*(.+)', excerpt_text, re.I)
                if m:
                    meta["genres"] = m.group(1).split("\n")[0].strip()
                m = re.search(r'Compan(?:y|ies)\s*:\s*(.+)', excerpt_text, re.I)
                if m:
                    meta["companies"] = m.group(1).split("\n")[0].strip()
                m = re.search(r'Repack\s+Size\s*:\s*((?:from\s+)?[0-9.,]+\s*[KMGT]?B)', excerpt_text, re.I)
                if m:
                    meta["repack_size"] = m.group(1).strip()
                m = re.search(r'Original\s+Size\s*:\s*([0-9.,]+\s*[KMGT]?B)', excerpt_text, re.I)
                if m:
                    meta["original_size"] = m.group(1).strip()

                if name:
                    games.append({
                        "name":          _clean_title(name),
                        "url":           url,
                        "date":          date,
                        "image":         "",
                        "categories":    [],
                        "genres":        meta.get("genres", ""),
                        "companies":     meta.get("companies", ""),
                        "languages":     "",
                        "original_size": meta.get("original_size", ""),
                        "repack_size":   meta.get("repack_size", ""),
                    })

            return games

        if verbose:
            print(f"  {C.DIM}  → HTTP {resp.status_code}{C.RST}")
        return []

    except requests.exceptions.JSONDecodeError:
        if verbose:
            print(f"  {C.RED}✖ Réponse non-JSON{C.RST}")
        return []
    except requests.RequestException as e:
        if verbose:
            print(f"  {C.RED}✖ Erreur : {e}{C.RST}")
        return []

# ═══════════════════════════════════════════════════════════════
#  RECHERCHE COMBINÉE  (HTML → WP API fallback)
# ═══════════════════════════════════════════════════════════════

def search_online(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Méthode 1 : Scraping HTML{C.RST}")

    results = search_html(query, max_pages=max_pages, verbose=verbose)

    if results:
        return results

    if verbose:
        print(f"\n  {C.YEL}⚠ HTML vide — fallback WP REST API…{C.RST}")
        print(f"  {C.WHITE}{C.BOLD}📡 Méthode 2 : WordPress REST API{C.RST}")

    results = search_wp_api(query, verbose=verbose)

    if not results and verbose:
        _print_debug_help()

    return results


# ═══════════════════════════════════════════════════════════════
#  SCORING LOCAL  (identique à steamrip_game-list.py)
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

        # Bonus si query dans companies / genres
        comp = normalize(g.get("companies", ""))
        genres = normalize(g.get("genres", ""))
        if q_norm in comp or q_norm in genres:
            sc += 60
        elif any(t in comp or t in genres for t in q_tokens):
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
            cats = f"  {C.DIM}[{', '.join(r['categories'])}]{C.RST}"
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}{cats}")

        # URL
        print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        # Métadonnées
        meta = []
        if r.get("repack_size"):
            meta.append(f"📦 Repack: {r['repack_size']}")
        if r.get("original_size"):
            meta.append(f"💿 Original: {r['original_size']}")
        if r.get("date"):
            meta.append(f"📅 {r['date']}")
        if meta:
            print(f"        {C.DIM}{' │ '.join(meta)}{C.RST}")

        meta2 = []
        if r.get("genres"):
            meta2.append(f"🏷  {r['genres']}")
        if r.get("companies"):
            meta2.append(f"🛠  {r['companies']}")
        if meta2:
            print(f"        {C.DIM}{' │ '.join(meta2)}{C.RST}")

        if r.get("languages"):
            print(f"        {C.DIM}🌐 {r['languages']}{C.RST}")

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
  {C.WHITE}5.{C.RST} Debug API sauvegardé  : {DEBUG_JSON_FILE}
""")


# ═══════════════════════════════════════════════════════════════
#  MODES
# ═══════════════════════════════════════════════════════════════

def do_search(query: str, limit: int = 15, max_pages: int = MAX_PAGES,
              verbose=True, from_file: str | None = None) -> list[dict]:
    """Recherche complète : online (HTML+API) ou fichier local → ranking."""

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
        print(f"  {C.YEL}💡 pip install cloudscraper  (recommandé){C.RST}")

    print(f"  {C.DIM}Commandes :  <jeu>  │  history  │  q{C.RST}\n")

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
    {C.CYAN}<nom du jeu>{C.RST}    Rechercher un repack
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
            "results": [
                {
                    "name":          r["name"],
                    "url":           r["url"],
                    "date":          r.get("date", ""),
                    "genres":        r.get("genres", ""),
                    "companies":     r.get("companies", ""),
                    "languages":     r.get("languages", ""),
                    "original_size": r.get("original_size", ""),
                    "repack_size":   r.get("repack_size", ""),
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
        description="🎮 FitGirl Repacks Search CLI — fitgirl-repacks.site",
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