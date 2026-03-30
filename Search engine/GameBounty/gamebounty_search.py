#!/usr/bin/env python3
"""
🎮 GameBounty Search CLI  v4
Recherche via https://gamebounty.world/?s={query} + parsing HTML.
Adapté Next.js (__NEXT_DATA__) + HTML cards + fallback liens.
Scoring avancé avec stopwords + seuil de pertinence.
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

BASE_URL    = "https://gamebounty.world"
SEARCH_URL  = f"{BASE_URL}/?s="

CACHE_DIR       = Path.home() / ".gamebounty_cache"
HISTORY_FILE    = CACHE_DIR / "search_history.json"
DEBUG_FILE      = CACHE_DIR / "debug_page.html"
DEBUG_JSON_FILE = CACHE_DIR / "debug_nextdata.json"

MAX_PAGES = 3

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
#  STOPWORDS — mots ignorés pour le scoring de pertinence
#  Ces mots sont trop communs pour discriminer les résultats
# ═══════════════════════════════════════════════════════════════

STOPWORDS = frozenset({
    # Articles / prépositions anglais
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
    "or", "by", "from", "with", "is", "it", "its", "as", "be",
    # Mots gaming génériques
    "game", "edition", "deluxe", "complete", "ultimate", "definitive",
    "goty", "remastered", "remake", "hd", "enhanced", "premium",
    "gold", "silver", "standard", "digital", "collectors",
    # Chiffres romains courts
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    # Suffixes DLC
    "dlc", "dlcs", "all", "bonus", "pack", "expansion",
    # Divers
    "plus", "pro", "vs", "new",
})


# ═══════════════════════════════════════════════════════════════
#  ANSI COLORS
# ═══════════════════════════════════════════════════════════════

class C:
    RST = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    CYAN = "\033[96m"; GREEN = "\033[92m"; YEL = "\033[93m"
    RED = "\033[91m"; MAG = "\033[95m"; BLUE = "\033[94m"
    WHITE = "\033[97m"

BANNER = f"""{C.CYAN}{C.BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║        🎮  GameBounty Search CLI  v4  🎮                 ║
 ║   Next.js + HTML scraping · gamebounty.world             ║
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


# ═══════════════════════════════════════════════════════════════
#  FETCH HTML
# ═══════════════════════════════════════════════════════════════

def fetch_search_html(query: str, page: int = 1, session=None, verbose=True) -> str | None:
    if session is None:
        session = _make_session()
    encoded = quote_plus(query)
    urls = []
    if page > 1:
        urls.append(f"{SEARCH_URL}{encoded}&paged={page}")
        urls.append(f"{BASE_URL}/page/{page}/?s={encoded}")
    else:
        urls.append(f"{SEARCH_URL}{encoded}")

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        for url in urls:
            try:
                if verbose:
                    print(f"  {C.DIM}🌐 GET {url}{C.RST}")
                resp = session.get(url, timeout=(10 if not verbose else 25))
                if resp.status_code == 200 and len(resp.text) > 500:
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


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _clean_title(text: str) -> str:
    text = unescape(text).strip()
    text = re.sub(r'\s*[-–|]\s*GameBounty\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*[-–]\s*$', '', text).strip()
    text = re.sub(r'\s*[-–]?\s*Free\s+Download\s*$', '', text, flags=re.I).strip()
    text = re.sub(r'\s*\(\s*Free\s+Download\s*\)\s*$', '', text, flags=re.I).strip()
    return text

_SKIP_SLUGS = frozenset({
    "about", "contact", "faq", "dmca", "request", "privacy",
    "terms", "category", "tag", "author", "page", "wp-admin",
    "donate", "donations", "how-to", "blog", "help", "login",
    "register", "account", "cart", "checkout", "search",
    "wp-json", "feed", "comments", "trackback", "xmlrpc",
    "sitemap", "ads.txt", "", "#",
})

def _is_game_url(href: str) -> bool:
    if not href:
        return False
    parsed = urlparse(href)
    host = parsed.hostname or ""
    if host and "gamebounty" not in host.lower():
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    first = path.split("/")[0].lower()
    if first in _SKIP_SLUGS:
        return False
    if re.search(r'\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|ttf|pdf|xml|json)$', path, re.I):
        return False
    return True

def _normalize_url(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE_URL + href
    if not href.startswith("http"):
        return BASE_URL + "/" + href.lstrip("/")
    return href

def _slug_to_url(slug: str) -> str:
    slug = slug.strip().strip("/")
    if slug.startswith("http"):
        return slug
    return f"{BASE_URL}/{slug}"


# ═══════════════════════════════════════════════════════════════
#  METADATA EXTRACTOR
# ═══════════════════════════════════════════════════════════════

def _extract_game_meta(text: str) -> dict:
    meta = {}
    patterns = {
        "genres":    [r'Genres?\s*/?\s*Tags?\s*:\s*(.+)'],
        "companies": [
            r'Develop(?:er|ed\s+by)\s*:\s*(.+)',
            r'Publish(?:er|ed\s+by)\s*:\s*(.+)',
            r'Compan(?:y|ies)\s*:\s*(.+)',
            r'Studio\s*:\s*(.+)',
        ],
        "languages": [r'Languages?\s*:\s*(.+)'],
        "size":      [
            r'(?:Download\s+)?Size\s*:\s*([0-9.,]+\s*[KMGT]?i?B)',
            r'File\s*Size\s*:\s*([0-9.,]+\s*[KMGT]?i?B)',
        ],
        "version":   [r'Version\s*:\s*(v?[\d][\d.]*[^\n,]*)'],
    }
    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.I)
            if m:
                meta[key] = m.group(1).split("\n")[0].strip()[:200]
                break
    return meta


# ═══════════════════════════════════════════════════════════════
#  __NEXT_DATA__  — Deep JSON Explorer
# ═══════════════════════════════════════════════════════════════

def _find_all_arrays(obj, path="", results=None, depth=0, max_depth=20):
    if results is None:
        results = []
    if depth > max_depth:
        return results
    if isinstance(obj, dict):
        for k, v in obj.items():
            _find_all_arrays(v, f"{path}.{k}" if path else k, results, depth+1, max_depth)
    elif isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts:
            results.append((path, dicts))
        for i, item in enumerate(obj):
            _find_all_arrays(item, f"{path}[{i}]", results, depth+1, max_depth)
    return results


def _score_array_as_games(arr: list[dict]) -> tuple[float, str | None, str | None]:
    if not arr:
        return 0.0, None, None
    sample = arr[0]
    keys = list(sample.keys())
    if not keys:
        return 0.0, None, None

    name_priority = [
        "title", "name", "gameName", "game_name", "gameTitle",
        "label", "heading", "postTitle", "post_title",
    ]
    name_key = None
    for nk in name_priority:
        for k in keys:
            if k.lower() == nk.lower():
                name_key = k
                break
        if name_key:
            break
    if not name_key:
        best_avg = 0
        for k in keys:
            vals = [item.get(k) for item in arr[:5] if isinstance(item.get(k), str)]
            if vals:
                avg = sum(len(v) for v in vals) / len(vals)
                if avg > best_avg and avg > 3:
                    best_avg = avg
                    name_key = k
    if not name_key:
        return 0.0, None, None

    url_priority = [
        "url", "href", "link", "slug", "path", "permalink", "uri",
        "pageUrl", "page_url", "postSlug", "post_slug", "handle",
    ]
    url_key = None
    for uk in url_priority:
        for k in keys:
            if k.lower() == uk.lower():
                url_key = k
                break
        if url_key:
            break
    if not url_key:
        for k in keys:
            if k == name_key:
                continue
            vals = [item.get(k) for item in arr[:5] if isinstance(item.get(k), str)]
            if vals and all(re.match(r'^[a-z0-9][a-z0-9-]*$', v) for v in vals):
                url_key = k
                break

    score = 0.0
    if any(name_key.lower() == nk.lower() for nk in name_priority[:5]):
        score += 100
    if url_key:
        score += 80
        if any(url_key.lower() == uk.lower() for uk in url_priority[:4]):
            score += 50
    score += min(len(arr), 50) * 2
    names = [item.get(name_key, "") for item in arr[:10] if isinstance(item.get(name_key), str)]
    if names:
        avg_len = sum(len(n) for n in names) / len(names)
        if avg_len > 5:
            score += 50
        if avg_len > 15:
            score += 30
        caps = sum(1 for n in names if n and n[0].isupper())
        score += (caps / len(names)) * 30

    game_hints = {"image", "thumbnail", "cover", "img", "coverImage",
                  "description", "excerpt", "summary", "category",
                  "genre", "developer", "publisher", "date",
                  "createdAt", "created_at", "publishedAt", "published_at",
                  "updatedAt", "updated_at", "downloads", "rating",
                  "tags", "categories", "platform", "platforms", "size"}
    hint_count = sum(1 for k in keys if k.lower() in {h.lower() for h in game_hints})
    score += hint_count * 20

    nav_hints = {"icon", "dropdown", "children", "submenu", "isActive", "target"}
    nav_count = sum(1 for k in keys if k.lower() in {h.lower() for h in nav_hints})
    score -= nav_count * 30

    return score, name_key, url_key


def _parse_nextdata(soup, verbose=True) -> list[dict]:
    games = []
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return games

    try:
        data = json.loads(script.string)
    except (json.JSONDecodeError, TypeError):
        return games

    if verbose:
        print(f"  {C.DIM}   🔍 __NEXT_DATA__ ({len(script.string):,} chars){C.RST}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DEBUG_JSON_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)[:2_000_000],
            encoding="utf-8"
        )
    except Exception:
        pass

    all_arrays = _find_all_arrays(data)
    if verbose:
        print(f"  {C.DIM}   📊 {len(all_arrays)} array(s) de dicts{C.RST}")
    if not all_arrays:
        return games

    scored = []
    for path, arr in all_arrays:
        sc, name_key, url_key = _score_array_as_games(arr)
        if sc > 0 and name_key:
            scored.append((sc, path, arr, name_key, url_key))
    scored.sort(key=lambda x: -x[0])

    if verbose and scored:
        print(f"  {C.DIM}   🏆 Meilleur array : {scored[0][1]} "
              f"({len(scored[0][2])} items, score={scored[0][0]:.0f}){C.RST}")

    if not scored:
        return games

    best_score = scored[0][0]
    seen = set()

    for sc, path, arr, name_key, url_key in scored:
        if sc < best_score * 0.5:
            break

        for item in arr:
            name = item.get(name_key)
            if not isinstance(name, str) or len(name) < 2:
                continue
            name = _clean_title(name)
            if not name:
                continue

            url = ""
            if url_key:
                raw_url = item.get(url_key, "")
                if isinstance(raw_url, str) and raw_url:
                    url = raw_url
            if not url:
                for ck in ("url", "href", "link", "slug", "path", "permalink", "uri", "handle"):
                    val = item.get(ck, "")
                    if isinstance(val, str) and val and val != name:
                        url = val
                        break
            if url and not url.startswith("http"):
                url = _slug_to_url(url) if "/" not in url or url.startswith("/") else _normalize_url(url)

            key = (url or name).rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)

            image = ""
            for ik in ("image", "thumbnail", "cover", "img", "coverImage",
                       "featuredImage", "featured_image", "poster", "banner"):
                val = item.get(ik, "")
                if isinstance(val, str) and val.startswith("http"):
                    image = val
                    break
                elif isinstance(val, dict):
                    image = val.get("url", val.get("src", ""))
                    if image:
                        break

            date = ""
            for dk in ("date", "createdAt", "created_at", "publishedAt",
                       "published_at", "updatedAt", "updated_at"):
                val = item.get(dk, "")
                if isinstance(val, str) and val:
                    date = val[:10]
                    break

            desc = ""
            for dsk in ("description", "excerpt", "summary", "content",
                        "shortDescription", "short_description"):
                val = item.get(dsk, "")
                if isinstance(val, str) and val:
                    desc = BeautifulSoup(val, "html.parser").get_text(" ", strip=True)[:300]
                    break

            genres = ""
            for gk in ("genre", "genres", "tags", "category", "categories"):
                val = item.get(gk)
                if isinstance(val, str) and val:
                    genres = val
                    break
                elif isinstance(val, list):
                    parts = []
                    for v in val:
                        if isinstance(v, str):
                            parts.append(v)
                        elif isinstance(v, dict):
                            parts.append(v.get("name", v.get("title", str(v))))
                    genres = ", ".join(parts)
                    break

            companies = ""
            for ck in ("developer", "publisher", "studio", "companies"):
                val = item.get(ck, "")
                if isinstance(val, str) and val:
                    companies = val
                    break
                elif isinstance(val, dict):
                    companies = val.get("name", "")
                    break

            size = ""
            for sk in ("size", "fileSize", "file_size", "downloadSize"):
                val = item.get(sk, "")
                if isinstance(val, (str, int, float)) and val:
                    size = str(val)
                    break

            version = ""
            for vk in ("version", "gameVersion", "game_version", "ver"):
                val = item.get(vk, "")
                if isinstance(val, str) and val:
                    version = val
                    break

            platforms = ""
            for pk in ("platform", "platforms"):
                val = item.get(pk)
                if isinstance(val, str) and val:
                    platforms = val
                    break
                elif isinstance(val, list):
                    platforms = ", ".join(str(v) for v in val)
                    break

            games.append({
                "name": name, "url": url, "date": date, "image": image,
                "categories": [], "description": desc, "genres": genres,
                "companies": companies, "languages": "", "size": size,
                "version": version, "platforms": platforms, "source": "nextdata",
            })

    if verbose:
        print(f"  {C.GREEN}   ✔ {len(games)} jeux dans __NEXT_DATA__{C.RST}")

    return games


# ═══════════════════════════════════════════════════════════════
#  HTML CARD PARSER
# ═══════════════════════════════════════════════════════════════

def _parse_html_cards(soup, verbose=True) -> list[dict]:
    games = []
    seen = set()
    candidates = []

    for el in soup.find_all(["div", "li", "section", "a", "article"],
                            class_=re.compile(
                                r'game|card|item|post|result|entry|product|tile', re.I)):
        candidates.append(el)

    if len(candidates) < 3:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href in ("#", "/"):
                continue
            has_img = a.find("img") is not None
            text = a.get_text(strip=True)
            if has_img and text and len(text) > 2:
                candidates.append(a)

    if len(candidates) < 3:
        for div in soup.find_all("div"):
            a = div.find("a", href=True)
            img = div.find("img")
            if a and img:
                text = div.get_text(strip=True)
                if text and 3 < len(text) < 500:
                    candidates.append(div)

    for el in candidates:
        if el.name == "a" and el.get("href"):
            a_tag = el
        else:
            a_tag = None
            for a in el.find_all("a", href=True):
                if a["href"] not in ("#", "/", ""):
                    a_tag = a
                    break
        if not a_tag:
            continue

        href = _normalize_url(a_tag["href"].strip())
        if not _is_game_url(href):
            continue

        key = href.rstrip("/").lower()
        if key in seen:
            continue

        name = ""
        for h in el.find_all(["h1", "h2", "h3", "h4", "h5"]):
            t = h.get_text(strip=True)
            if t and len(t) > 2:
                name = t
                break
        if not name:
            for tag in el.find_all(["span", "div", "p"],
                                   class_=re.compile(r'title|name|heading', re.I)):
                t = tag.get_text(strip=True)
                if t and len(t) > 2:
                    name = t
                    break
        if not name:
            name = a_tag.get_text(strip=True)
        if not name:
            name = el.get("title", "") or el.get("aria-label", "") or ""

        name = _clean_title(name)
        if not name or len(name) < 2:
            continue
        if name.lower() in ("home", "contact", "about", "faq", "search",
                            "menu", "gamebounty", "login", "register",
                            "discord", "request"):
            continue

        seen.add(key)

        image = ""
        img = el.find("img")
        if img:
            for attr in ("src", "data-src", "data-lazy-src"):
                val = img.get(attr, "")
                if val and val.startswith("http"):
                    image = val.split(",")[0].split(" ")[0]
                    break

        description = ""
        for p in el.find_all("p"):
            t = p.get_text(strip=True)
            if t and len(t) > 10:
                description = t[:300]
                break

        games.append({
            "name": name, "url": href, "date": "", "image": image,
            "categories": [], "description": description, "genres": "",
            "companies": "", "languages": "", "size": "", "version": "",
            "platforms": "", "source": "html_card",
        })

    if verbose and games:
        print(f"  {C.GREEN}   ✔ {len(games)} jeux via HTML cards{C.RST}")
    return games


# ═══════════════════════════════════════════════════════════════
#  FALLBACK LINKS
# ═══════════════════════════════════════════════════════════════

def _parse_links_fallback(soup, seen_urls: set, verbose=True) -> list[dict]:
    games = []
    nav_texts = frozenset({
        "home", "contact", "about", "faq", "search", "menu", "discord",
        "request", "dmca", "login", "register", "privacy", "terms",
        "gamebounty", "blog", "help", "donate", "sitemap", "all games",
        "latest", "popular", "trending", "new", "categories",
    })

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = _clean_title(a.get_text(strip=True))
        if not text or len(text) < 3 or text.lower() in nav_texts:
            continue
        href_full = _normalize_url(href)
        if not _is_game_url(href_full):
            continue
        parsed = urlparse(href_full)
        path = parsed.path.strip("/")
        if not path or path.count("/") > 2:
            continue
        key = href_full.rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)

        image = ""
        for ancestor in [a, a.parent, a.parent.parent if a.parent else None]:
            if ancestor:
                img = ancestor.find("img")
                if img:
                    image = img.get("src", img.get("data-src", ""))
                    break

        games.append({
            "name": text, "url": href_full, "date": "", "image": image,
            "categories": [], "description": "", "genres": "",
            "companies": "", "languages": "", "size": "", "version": "",
            "platforms": "", "source": "link_fallback",
        })

    if verbose and games:
        print(f"  {C.DIM}   🔍 {len(games)} jeux via fallback liens{C.RST}")
    return games


# ═══════════════════════════════════════════════════════════════
#  JSON-LD
# ═══════════════════════════════════════════════════════════════

def _parse_jsonld(soup, verbose=True) -> list[dict]:
    games = []
    seen = set()
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@graph" in item:
                items.extend(item["@graph"])
        for item in items:
            if not isinstance(item, dict):
                continue
            itype = item.get("@type", "")
            if itype in ("WebSite", "WebPage", "Organization", "BreadcrumbList", "SearchAction"):
                continue
            name = item.get("name", "") or item.get("headline", "")
            url  = item.get("url", "") or item.get("mainEntityOfPage", "")
            if isinstance(url, dict):
                url = url.get("@id", "")
            name = _clean_title(str(name))
            if not name or len(name) < 2:
                continue
            url = _normalize_url(str(url)) if url else ""
            key = (url or name).rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            img = item.get("image", "")
            if isinstance(img, dict):
                img = img.get("url", "")
            elif isinstance(img, list) and img:
                img = img[0] if isinstance(img[0], str) else img[0].get("url", "")
            games.append({
                "name": name, "url": url,
                "date": str(item.get("datePublished", ""))[:10],
                "image": str(img), "categories": [],
                "description": str(item.get("description", ""))[:300],
                "genres": "", "companies": "", "languages": "",
                "size": "", "version": "", "platforms": "",
                "source": "jsonld",
            })
    if verbose and games:
        print(f"  {C.DIM}   🔍 {len(games)} jeux via JSON-LD{C.RST}")
    return games


# ═══════════════════════════════════════════════════════════════
#  INLINE JSON
# ═══════════════════════════════════════════════════════════════

def _parse_inline_json(soup, verbose=True) -> list[dict]:
    games = []
    seen = set()
    for script in soup.find_all("script"):
        if script.get("type") == "application/ld+json" or script.get("id") == "__NEXT_DATA__":
            continue
        if not script.string:
            continue
        text = script.string
        blobs = re.findall(r'(\[(?:\s*\{[^{}]{10,500}\}\s*,?\s*){2,}\])', text, re.S)
        for blob in blobs:
            try:
                arr = json.loads(blob)
                if not isinstance(arr, list):
                    continue
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("title", "") or item.get("name", "")
                    url  = item.get("url", "") or item.get("slug", "") or item.get("href", "")
                    name = _clean_title(str(name))
                    if not name or len(name) < 2:
                        continue
                    url = str(url)
                    if url and not url.startswith("http"):
                        url = _slug_to_url(url)
                    key = (url or name).rstrip("/").lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    games.append({
                        "name": name, "url": url, "date": "",
                        "image": str(item.get("image", "") or ""),
                        "categories": [], "description": "", "genres": "",
                        "companies": "", "languages": "", "size": "",
                        "version": "", "platforms": "", "source": "inline_json",
                    })
            except (json.JSONDecodeError, TypeError):
                continue
    if verbose and games:
        print(f"  {C.DIM}   🔍 {len(games)} jeux via inline JSON{C.RST}")
    return games


# ═══════════════════════════════════════════════════════════════
#  PARSE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

def _analyze_html_structure(soup, verbose=True):
    if not verbose:
        return
    articles = len(soup.find_all("article"))
    scripts  = len(soup.find_all("script"))
    divs_gc  = len(soup.find_all(["div", "li"],
                    class_=re.compile(r'game|card|item|post|result', re.I)))
    nextdata = soup.find("script", id="__NEXT_DATA__") is not None
    jsonld   = len(soup.find_all("script", type="application/ld+json"))
    links_int = len([a for a in soup.find_all("a", href=True)
                     if "gamebounty" in a.get("href", "") or a["href"].startswith("/")])
    print(f"  {C.DIM}   📊 HTML: articles={articles} __NEXT_DATA__={'✔' if nextdata else '✖'} "
          f"JSON-LD={jsonld} scripts={scripts} cards={divs_gc} links={links_int}{C.RST}")


def parse_search_html(html: str, verbose=True) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    if verbose:
        _analyze_html_structure(soup, verbose)

    all_games = []
    seen_urls = set()

    def _merge(new_games):
        for g in new_games:
            key = (g.get("url") or g["name"]).rstrip("/").lower()
            if key not in seen_urls:
                seen_urls.add(key)
                all_games.append(g)

    _merge(_parse_nextdata(soup, verbose))
    _merge(_parse_html_cards(soup, verbose))
    if not all_games:
        _merge(_parse_inline_json(soup, verbose))
    if not all_games:
        _merge(_parse_jsonld(soup, verbose))
    if not all_games:
        if verbose:
            print(f"  {C.DIM}   ⚠ Stratégies 1-4 vides, fallback liens…{C.RST}")
        _merge(_parse_links_fallback(soup, seen_urls, verbose))

    if verbose:
        print(f"  {C.DIM}   🎯 {len(all_games)} jeux total en base{C.RST}")

    return all_games


def _has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for sel in [
        lambda: soup.find("a", class_=re.compile(r'\bnext\b', re.I)),
        lambda: soup.find("a", string=re.compile(r'next|suivant|→|›|»', re.I)),
        lambda: soup.find("a", attrs={"rel": "next"}),
    ]:
        if sel():
            return True
    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        if '"hasNextPage":true' in nd.string or '"nextPage"' in nd.string:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  MULTI-PAGE SEARCH
# ═══════════════════════════════════════════════════════════════

def search_html(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    session = _make_session()
    try:
        session.get(BASE_URL, timeout=(10 if not verbose else 15))
        time.sleep(0.1 if not verbose else 0.3)
    except Exception:
        pass

    all_games = []
    seen = set()

    for page_num in range(1, max_pages + 1):
        html = fetch_search_html(query, page=page_num, session=session, verbose=verbose)
        if not html:
            break
        games = parse_search_html(html, verbose=(verbose and page_num == 1))
        new = 0
        for g in games:
            key = (g.get("url") or g["name"]).rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                all_games.append(g)
                new += 1
        if verbose and page_num > 1:
            print(f"  {C.DIM}   📄 Page {page_num}: +{new}{C.RST}")
        if not _has_next_page(html):
            break
        time.sleep(0.1 if not verbose else 0.5)

    return all_games


def search_online(query: str, max_pages: int = MAX_PAGES, verbose=True) -> list[dict]:
    if verbose:
        print(f"\n  {C.WHITE}{C.BOLD}📡 Scraping — gamebounty.world{C.RST}")
    results = search_html(query, max_pages=max_pages, verbose=verbose)
    if not results and verbose:
        _print_debug_help()
    return results


# ═══════════════════════════════════════════════════════════════
#  ENRICHISSEMENT
# ═══════════════════════════════════════════════════════════════

def enrich_game_page(game: dict, session=None, verbose=False) -> dict:
    if session is None:
        session = _make_session()
    if not game.get("url"):
        return game
    try:
        resp = session.get(game["url"], timeout=(10 if not verbose else 20))
        if resp.status_code != 200:
            return game
        soup = BeautifulSoup(resp.text, "html.parser")

        if not game.get("description"):
            desc = soup.find("meta", attrs={"name": "description"})
            if desc and desc.get("content"):
                game["description"] = desc["content"].strip()[:300]
        if not game.get("image"):
            og = soup.find("meta", attrs={"property": "og:image"})
            if og and og.get("content"):
                game["image"] = og["content"]

        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            try:
                data = json.loads(nd.string)
                arrays = _find_all_arrays(data)
                for path, arr in arrays:
                    if len(arr) == 1:
                        item = arr[0]
                        for k, gk in [("genres", "genre"), ("genres", "genres"),
                                       ("companies", "developer"), ("companies", "publisher"),
                                       ("size", "size"), ("version", "version"),
                                       ("languages", "languages")]:
                            if not game.get(k):
                                val = item.get(gk, "")
                                if isinstance(val, str) and val:
                                    game[k] = val
            except (json.JSONDecodeError, TypeError):
                pass

        content = soup.find("main") or soup.body
        if content:
            meta = _extract_game_meta(content.get_text("\n", strip=True))
            for k, v in meta.items():
                if v and not game.get(k):
                    game[k] = v
    except Exception as e:
        if verbose:
            print(f"  {C.DIM}  → Enrichissement échoué: {e}{C.RST}")
    return game


# ═══════════════════════════════════════════════════════════════
#  SCORING v4 — Stopword-aware + seuil de pertinence
# ═══════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = re.sub(r'[\u0300-\u036f]', '', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    return text

def tokenize(t: str) -> list[str]:
    return t.split()

def tokenize_significant(t: str) -> list[str]:
    """Tokenize en retirant les stopwords."""
    return [w for w in t.split() if w not in STOPWORDS]

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


def compute_score(q_norm, q_tokens, q_sig, g_norm, g_tokens, g_sig) -> float:
    """
    Scoring avec séparation mots significatifs / stopwords.
    q_sig / g_sig = tokens sans stopwords (les mots qui comptent).
    """
    # ── Match exact = score max ──
    if q_norm == g_norm:
        return 10000.0

    score = 0.0

    # ═══════════════════════════════════════════════════════
    #  MOTS SIGNIFICATIFS — pondération forte
    # ═══════════════════════════════════════════════════════

    if q_sig:
        # Combien de mots significatifs de la query sont dans le titre ?
        sig_found = sum(1 for t in q_sig if any(t in gt for gt in g_sig))
        sig_cov = sig_found / len(q_sig)

        # Bonus proportionnel aux mots significatifs matchés
        score += sig_cov * 500  # max 500

        # Bonus ALL significant words found
        if sig_cov == 1.0:
            score += 300

        # Bonus exact token match (pas substring)
        sig_exact = sum(1 for t in q_sig if t in set(g_sig))
        exact_cov = sig_exact / len(q_sig)
        score += exact_cov * 200  # max 200

        # Bonus si les mots significatifs matchent en prefix
        sig_pre = sum(1 for qt in q_sig if any(gt.startswith(qt) for gt in g_sig))
        score += (sig_pre / len(q_sig)) * 100

        # Malus si AUCUN mot significatif ne match
        if sig_found == 0:
            score -= 500  # Gros malus

    # ═══════════════════════════════════════════════════════
    #  FULL TOKENS — pondération plus faible
    # ═══════════════════════════════════════════════════════

    if g_norm.startswith(q_norm):
        score += 200
    if q_norm in g_norm:
        score += 150
    elif g_norm in q_norm:
        score += 80

    # Couverture all tokens (incluant stopwords) — poids réduit
    found_all = sum(1 for t in q_tokens if any(t in gt for gt in g_tokens))
    cov_all = found_all / len(q_tokens) if q_tokens else 0
    score += cov_all * 80  # Réduit (était 300)

    # ═══════════════════════════════════════════════════════
    #  SET SIMILARITY — sur mots significatifs
    # ═══════════════════════════════════════════════════════

    sa, sb = set(q_sig), set(g_sig)
    if sa and sb:
        inter = sa & sb
        union = sa | sb
        jaccard = len(inter) / len(union) if union else 0
        tcov = len(inter) / len(sa)
        score += (jaccard * 0.3 + tcov * 0.7) * 300

    # ═══════════════════════════════════════════════════════
    #  SEQUENCE MATCHING — sur noms complets
    # ═══════════════════════════════════════════════════════

    score += SequenceMatcher(None, q_norm, g_norm).ratio() * 150

    # Sorted tokens similarity (mots significatifs seulement)
    qs = " ".join(sorted(q_sig))
    gs = " ".join(sorted(g_sig))
    if qs and gs:
        score += SequenceMatcher(None, qs, gs).ratio() * 150

    # Partial ratio
    score += partial_ratio(q_norm, g_norm) * 200

    # ═══════════════════════════════════════════════════════
    #  ACRONYM MATCH
    # ═══════════════════════════════════════════════════════

    if len(q_sig) == 1 and len(q_sig[0]) <= 6:
        acr = "".join(t[0] for t in g_sig if t)
        if q_sig[0] == acr:
            score += 500

    # ═══════════════════════════════════════════════════════
    #  LENGTH PENALTY
    # ═══════════════════════════════════════════════════════

    score -= abs(len(q_norm) - len(g_norm)) * 0.5

    # Bonus si même nombre de mots significatifs
    if len(q_sig) == len(g_sig):
        score += 40

    return score


def _compute_significance_coverage(q_sig: list[str], g_norm: str, g_sig: list[str]) -> float:
    """
    Retourne le % de mots significatifs de la query trouvés dans le titre.
    Utilisé pour le filtre de pertinence.
    """
    if not q_sig:
        return 1.0
    found = sum(1 for t in q_sig if t in g_norm or any(t in gt for gt in g_sig))
    return found / len(q_sig)


def rank_results(query: str, results: list[dict], limit: int = 15) -> list[dict]:
    q_norm = normalize(query)
    q_tokens = tokenize(q_norm)
    q_sig = tokenize_significant(q_norm)

    if not q_norm:
        return results[:limit]

    # ── ÉTAPE 1 : Scorer tous les résultats ──
    for g in results:
        gn = normalize(g["name"])
        gt = tokenize(gn)
        gs = tokenize_significant(gn)

        sc = compute_score(q_norm, q_tokens, q_sig, gn, gt, gs) if gn else 0.0

        # Bonus contexte
        comp   = normalize(g.get("companies", ""))
        genres = normalize(g.get("genres", ""))
        desc   = normalize(g.get("description", ""))
        if q_norm in comp or q_norm in genres:
            sc += 60
        elif any(t in comp or t in genres for t in q_sig):
            sc += 20
        if q_norm in desc:
            sc += 30

        g["score"] = round(sc, 1)
        g["_sig_cov"] = _compute_significance_coverage(q_sig, gn, gs)

    # ── ÉTAPE 2 : Filtrer par pertinence ──
    # Un résultat doit avoir au moins 50% des mots significatifs
    # OU être un match partiel très fort
    min_coverage = 0.5
    if len(q_sig) <= 2:
        min_coverage = 0.8  # Plus strict pour les queries courtes (1-2 mots clés)
    if len(q_sig) >= 4:
        min_coverage = 0.4  # Plus souple pour les longues queries

    filtered = []
    for g in results:
        cov = g.get("_sig_cov", 0)
        sc  = g.get("score", 0)

        # Garder si :
        #  1. Couverture suffisante des mots significatifs
        #  2. OU score très élevé (match séquentiel fort)
        if cov >= min_coverage or sc >= 1500:
            filtered.append(g)

    # ── ÉTAPE 3 : Trier par score ──
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

    # ── ÉTAPE 4 : Détection d'écart (gap detection) ──
    # Si le #1 a un score beaucoup plus élevé que le #2,
    # on coupe les résultats avec un écart trop grand
    if len(filtered) >= 2:
        top_score = filtered[0]["score"]
        if top_score > 0:
            # Calculer le ratio minimum acceptable
            # Ex: si #1 = 2276 et threshold = 25%, tout ce qui est < 569 est coupé
            gap_threshold = 0.25  # 25% du meilleur score

            # Si le 2ème est déjà très loin, être plus agressif
            ratio_2nd = filtered[1]["score"] / top_score if top_score > 0 else 0
            if ratio_2nd < 0.35:
                gap_threshold = 0.30  # Le 2ème est loin → couper plus

            final = [filtered[0]]
            for g in filtered[1:]:
                ratio = g["score"] / top_score
                if ratio >= gap_threshold:
                    final.append(g)
            filtered = final

    # ── ÉTAPE 5 : Nettoyage des champs internes ──
    for g in filtered:
        g.pop("_sig_cov", None)

    return filtered[:limit]


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
        print(f"  {C.DIM}Essayez un autre terme ou --analyze pour debugger.{C.RST}\n")
        return None

    has_scores = any("score" in r for r in results)
    mx = max(r.get("score", 1) for r in results) if has_scores else 1

    print(f"\n  {C.GREEN}{C.BOLD}✔ {len(results)} résultat(s) pour « {query} »{C.RST}")
    print(SEP)

    for i, r in enumerate(results, 1):
        cats = ""
        if r.get("categories"):
            cats = f"  {C.DIM}[{', '.join(r['categories'][:3])}]{C.RST}"
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}{cats}")

        if r.get("url"):
            print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        meta = []
        if r.get("size"):
            meta.append(f"📦 {r['size']}")
        if r.get("version"):
            meta.append(f"🏷  {r['version']}")
        if r.get("date"):
            meta.append(f"📅 {r['date']}")
        if r.get("platforms"):
            meta.append(f"🖥  {r['platforms']}")
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

        if r.get("description"):
            desc = r["description"][:120]
            if len(r["description"]) > 120:
                desc += "…"
            print(f"        {C.DIM}📝 {desc}{C.RST}")

        if has_scores and "score" in r:
            src = f"  [{r.get('source', '')}]" if r.get("source") else ""
            bar = score_bar(r["score"], mx)
            print(f"        {bar}  {C.DIM}score: {r['score']}{src}{C.RST}")

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
#  DEBUG / ANALYZE
# ═══════════════════════════════════════════════════════════════

def _print_debug_help():
    print(f"""
  {C.YEL}{C.BOLD}💡 Aucun résultat :{C.RST}
  {C.WHITE}1.{C.RST} pip install cloudscraper
  {C.WHITE}2.{C.RST} Debug HTML : {C.DIM}{DEBUG_FILE}{C.RST}
  {C.WHITE}3.{C.RST} Debug JSON : {C.DIM}{DEBUG_JSON_FILE}{C.RST}
  {C.WHITE}4.{C.RST} --analyze "query" pour voir la structure
  {C.WHITE}5.{C.RST} --from-file "page.html" "query"
""")


def do_analyze(query: str, verbose=True):
    session = _make_session()
    try:
        session.get(BASE_URL, timeout=(10 if not verbose else 15))
        time.sleep(0.1 if not verbose else 0.3)
    except Exception:
        pass

    html = fetch_search_html(query, session=session, verbose=verbose)
    if not html:
        print(f"  {C.RED}✖ Impossible de récupérer la page.{C.RST}")
        return

    soup = BeautifulSoup(html, "html.parser")

    print(f"\n  {C.WHITE}{C.BOLD}🔬 ANALYSE DE STRUCTURE{C.RST}")
    _analyze_html_structure(soup, verbose=True)

    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        print(f"\n  {C.WHITE}{C.BOLD}📦 __NEXT_DATA__ :{C.RST}")
        try:
            data = json.loads(nd.string)
            print(f"  {C.DIM}  Clés racine : {list(data.keys())}{C.RST}")
            pp = data.get("props", {}).get("pageProps", {})
            if pp:
                print(f"  {C.DIM}  pageProps clés : {list(pp.keys())}{C.RST}")
                for k, v in pp.items():
                    if isinstance(v, list):
                        print(f"  {C.DIM}    .{k}: list[{len(v)}]{C.RST}")
                        if v and isinstance(v[0], dict):
                            print(f"  {C.DIM}      [0] clés: {list(v[0].keys())}{C.RST}")
                            sample = {sk: str(sv)[:60] for sk, sv in list(v[0].items())[:8]}
                            for sk, sv in sample.items():
                                print(f"  {C.CYAN}        .{sk}: {sv}{C.RST}")
                    elif isinstance(v, dict):
                        print(f"  {C.DIM}    .{k}: dict({list(v.keys())[:8]}){C.RST}")
                        for sk, sv in list(v.items())[:5]:
                            if isinstance(sv, list) and sv:
                                print(f"  {C.DIM}      .{sk}: list[{len(sv)}]{C.RST}")
                                if isinstance(sv[0], dict):
                                    print(f"  {C.DIM}        [0] clés: {list(sv[0].keys())}{C.RST}")
                                    sample = {ssk: str(ssv)[:60] for ssk, ssv in list(sv[0].items())[:8]}
                                    for ssk, ssv in sample.items():
                                        print(f"  {C.CYAN}          .{ssk}: {ssv}{C.RST}")
                    elif isinstance(v, str):
                        print(f"  {C.DIM}    .{k}: \"{v[:80]}\"{C.RST}")
                    else:
                        print(f"  {C.DIM}    .{k}: {type(v).__name__}({str(v)[:60]}){C.RST}")

            print(f"\n  {C.WHITE}{C.BOLD}📊 Arrays :{C.RST}")
            all_arrays = _find_all_arrays(data)
            for path, arr in sorted(all_arrays, key=lambda x: -len(x[1]))[:10]:
                sc, nk, uk = _score_array_as_games(arr)
                sample = str(arr[0].get(nk, ""))[:50] if nk and arr else "?"
                print(f"  {C.DIM}  {path}: {len(arr)} items, "
                      f"name={nk}, url={uk}, score={sc:.0f}{C.RST}")
                print(f"  {C.CYAN}    ex: \"{sample}\"{C.RST}")
        except (json.JSONDecodeError, TypeError) as e:
            print(f"  {C.RED}  Erreur: {e}{C.RST}")

    print(f"\n  {C.WHITE}{C.BOLD}📦 Classes CSS fréquentes :{C.RST}")
    class_counts = {}
    for el in soup.find_all(["div", "li", "a", "section", "span"]):
        for cls in el.get("class", []):
            class_counts[cls] = class_counts.get(cls, 0) + 1
    for cls, count in sorted(class_counts.items(), key=lambda x: -x[1])[:30]:
        if count >= 2:
            marker = " ◄" if re.search(r'game|card|item|post|result|title', cls, re.I) else ""
            print(f"  {C.DIM}  .{cls} × {count}{C.CYAN}{marker}{C.RST}")

    print(f"\n  {C.WHITE}{C.BOLD}🔗 Liens internes :{C.RST}")
    count = 0
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href in ("#", "/"):
            continue
        has_img = a.find("img") is not None
        text = a.get_text(strip=True)
        if has_img and text and len(text) > 2:
            classes = " ".join(a.get("class", []))[:30]
            print(f"  {C.DIM}  \"{text}\" → {href[:70]}  [{classes}]{C.RST}")
            count += 1
            if count >= 25:
                break

    print(f"\n  {C.DIM}  Files: {DEBUG_FILE} / {DEBUG_JSON_FILE}{C.RST}")


# ═══════════════════════════════════════════════════════════════
#  MODES
# ═══════════════════════════════════════════════════════════════

def do_search(query: str, limit: int = 15, max_pages: int = MAX_PAGES,
              verbose=True, from_file: str | None = None,
              enrich: bool = False) -> list[dict]:

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

    if enrich and results:
        if verbose:
            print(f"\n  {C.DIM}🔍 Enrichissement…{C.RST}")
        session = _make_session()
        for i, g in enumerate(results[:10]):
            results[i] = enrich_game_page(g, session=session, verbose=verbose)
            time.sleep(0.0 if not verbose else 0.3)

    save_to_history(query, results)
    return results


def interactive_mode(limit: int = 15, max_pages: int = MAX_PAGES,
                     from_file: str | None = None, enrich: bool = False):
    print(BANNER)
    if not HAS_CLOUDSCRAPER:
        print(f"  {C.YEL}💡 pip install cloudscraper  (recommandé){C.RST}")
    print(f"  {C.DIM}Commandes :  <jeu>  │  analyze <jeu>  │  history  │  help  │  q{C.RST}\n")

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
        if cmd.startswith("analyze "):
            do_analyze(raw[8:].strip())
            print()
            continue
        if cmd == "help":
            print(f"""
  {C.WHITE}{C.BOLD}Commandes :{C.RST}
    {C.CYAN}<nom du jeu>{C.RST}        Rechercher un jeu
    {C.CYAN}analyze <jeu>{C.RST}       Debug la structure HTML
    {C.CYAN}history{C.RST}             Historique
    {C.CYAN}q{C.RST}                   Quitter
""")
            continue
        print()
        results = do_search(raw, limit=limit, max_pages=max_pages,
                            from_file=from_file, enrich=enrich)
        displayed = display_results(results, raw)
        prompt_open(displayed or [])
        print()


def single_search(game_name: str, limit: int = 15, max_pages: int = MAX_PAGES,
                  auto_open: bool = False, from_file: str | None = None,
                  json_output: bool = False, enrich: bool = False,
                  analyze: bool = False):
    if analyze:
        if not json_output:
            print(BANNER)
        do_analyze(game_name)
        return
    if not json_output:
        print(BANNER)
    results = do_search(game_name, limit=limit, max_pages=max_pages,
                        verbose=not json_output, from_file=from_file, enrich=enrich)
    if json_output:
        out = {
            "query": game_name, "count": len(results), "source": "gamebounty.world",
            "results": [
                {k: r.get(k, "") for k in
                 ("name", "url", "date", "description", "genres", "companies",
                  "languages", "size", "version", "platforms", "score")}
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
        description="🎮 GameBounty Search CLI v4 — gamebounty.world",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                              # interactif
  python {sys.argv[0]} "Elden Ring"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --json "Stardew Valley"
  python {sys.argv[0]} --from-file page.html "Elden Ring"
  python {sys.argv[0]} --enrich "Cyberpunk 2077"
  python {sys.argv[0]} --analyze "cult of the lamb"
        """,
    )
    parser.add_argument("game", nargs="*", help="Nom du jeu")
    parser.add_argument("-n", "--num", type=int, default=15)
    parser.add_argument("-o", "--open", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("-f", "--from-file", type=str, default=None)
    parser.add_argument("-p", "--pages", type=int, default=MAX_PAGES)
    parser.add_argument("-e", "--enrich", action="store_true")
    parser.add_argument("-a", "--analyze", action="store_true")

    args = parser.parse_args()

    if args.interactive or not args.game:
        interactive_mode(args.num, max_pages=args.pages,
                         from_file=args.from_file, enrich=args.enrich)
    else:
        single_search(
            " ".join(args.game), limit=args.num, max_pages=args.pages,
            auto_open=args.open, from_file=args.from_file,
            json_output=args.json, enrich=args.enrich, analyze=args.analyze,
        )


if __name__ == "__main__":
    main()