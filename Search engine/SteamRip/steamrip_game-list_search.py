#!/usr/bin/env python3
"""
🎮 SteamRip Search CLI
Scrape steamrip.com/games-list-page/ puis recherche locale avec scoring avancé.
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
from urllib.parse import urljoin, urlparse

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

BASE_URL       = "https://steamrip.com"
GAMES_LIST_URL = f"{BASE_URL}/games-list-page/"

CACHE_DIR      = Path.home() / ".steamrip_cache"
CACHE_FILE     = CACHE_DIR / "games.json"
DEBUG_FILE     = CACHE_DIR / "debug_page.html"
CACHE_MAX_AGE  = 60 * 60 * 12   # 12h

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

# Slugs / chemins connus qui ne sont PAS des jeux
NON_GAME_PATTERNS = {
    "", "/", "#",
    "/games-list-page/", "/games-list/", "/game-list/",
    "/category/", "/tag/", "/author/", "/page/",
    "/wp-admin/", "/wp-content/", "/wp-includes/",
    "/wp-login.php", "/wp-json/", "/xmlrpc.php",
    "/feed/", "/comments/", "/sitemap/",
    "/contact/", "/about/", "/faq/", "/dmca/",
    "/request/", "/privacy-policy/", "/terms-of-service/",
    "/disclaimer/", "/how-to-download/", "/tutorial/",
    "/guide/", "/news/", "/cart/", "/checkout/",
    "/my-account/", "/search/", "/cdn-cgi/",
}


# ═══════════════════════════════════════════════════════════════
#  ANSI
# ═══════════════════════════════════════════════════════════════

class C:
    RST = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    CYAN = "\033[96m"; GREEN = "\033[92m"; YEL = "\033[93m"
    RED = "\033[91m"; MAG = "\033[95m"; BLUE = "\033[94m"
    WHITE = "\033[97m"

BANNER = f"""{C.CYAN}{C.BOLD}
 ╔═══════════════════════════════════════════════════════╗
 ║            🎮  SteamRip Search CLI  🎮               ║
 ║   Base locale · Recherche fuzzy · steamrip.com        ║
 ╚═══════════════════════════════════════════════════════╝{C.RST}
"""
SEP = f"  {C.DIM}{'─' * 60}{C.RST}"


# ═══════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════

def _make_session():
    if HAS_CLOUDSCRAPER:
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows"}
        )
    else:
        session = requests.Session()

    session.headers.update({
        "User-Agent": USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://steamrip.com/",
    })
    return session


def fetch_page(verbose=True) -> str | None:
    session = _make_session()

    # Visite la home d'abord (cookies)
    try:
        session.get(BASE_URL, timeout=15)
        time.sleep(0.3)
    except Exception:
        pass

    for ua in USER_AGENTS:
        session.headers["User-Agent"] = ua
        try:
            if verbose:
                print(f"  {C.DIM}🌐 Essai : {GAMES_LIST_URL}{C.RST}")

            resp = session.get(GAMES_LIST_URL, timeout=30)

            if resp.status_code == 200 and len(resp.text) > 2000:
                lower = resp.text[:3000].lower()
                if "just a moment" in lower and "cloudflare" in lower:
                    if verbose:
                        print(f"  {C.YEL}⚠ Cloudflare challenge.{C.RST}")
                    continue

                if verbose:
                    print(f"  {C.GREEN}✔ Page récupérée ({len(resp.text):,} octets){C.RST}")

                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                DEBUG_FILE.write_text(resp.text[:800_000], encoding="utf-8")
                return resp.text

            if verbose:
                print(f"  {C.DIM}  → HTTP {resp.status_code}{C.RST}")

        except requests.RequestException as e:
            if verbose:
                print(f"  {C.DIM}  → Erreur : {e}{C.RST}")

    return None


# ═══════════════════════════════════════════════════════════════
#  SCRAPING  –  URLs relatives ET absolues
# ═══════════════════════════════════════════════════════════════

def _normalize_href(href: str) -> str:
    """Convertit toute URL (relative ou absolue) en URL absolue steamrip."""
    href = href.strip()

    # Déjà absolue
    if href.startswith("http://") or href.startswith("https://"):
        return href

    # Relative → absolue
    if href.startswith("/"):
        return BASE_URL + href

    # Relative sans slash
    if href and not href.startswith("#") and not href.startswith("javascript"):
        return BASE_URL + "/" + href

    return href


def _is_game_path(path: str) -> bool:
    """
    Vérifie si un chemin URL ressemble à une page de jeu.
    Accepte : /game-name-free-download/  ou  /game-name/
    Rejette : /category/xxx, /page/2, etc.
    """
    path = path.lower().rstrip("/")

    if not path or path == "":
        return False

    # Chemin dans la blacklist exacte
    if (path + "/") in NON_GAME_PATTERNS or path in NON_GAME_PATTERNS:
        return False

    # Commence par un chemin blacklisté
    for pattern in NON_GAME_PATTERNS:
        if pattern.endswith("/") and path.startswith(pattern.rstrip("/")):
            # /category/action → rejeté
            if len(pattern) > 2:
                return False

    # Doit être un chemin de profondeur 1 : /slug/ (pas /a/b/c/)
    parts = [p for p in path.split("/") if p]
    if len(parts) != 1:
        return False

    slug = parts[0]

    # Le slug doit ressembler à un nom de jeu (alphanum + tirets, >= 3 chars)
    if len(slug) < 3:
        return False
    if not re.match(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$', slug):
        return False

    return True


def _clean_game_name(text: str) -> str:
    """Nettoie le nom du jeu."""
    text = re.sub(
        r'\s*[-–—:]\s*(Free\s+)?Download.*$', '', text,
        flags=re.IGNORECASE
    ).strip()
    text = re.sub(r'\s*\(Free\s+Download[^)]*\)', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _name_from_slug(slug: str) -> str:
    """Déduit un nom lisible à partir du slug URL."""
    slug = re.sub(r'-free-download$', '', slug)
    slug = re.sub(r'-', ' ', slug)
    return slug.title()


def parse_games_from_html(html: str, verbose=True) -> list[dict]:
    """Parse le HTML et extrait les jeux — gère URLs relatives et absolues."""
    soup = BeautifulSoup(html, "html.parser")

    games = []
    seen_slugs = set()

    all_links = soup.find_all("a", href=True)
    if verbose:
        print(f"  {C.DIM}   📄 Liens totaux : {len(all_links)}{C.RST}")

    # ── Collecter tous les <a> qui pointent vers un jeu ──
    for a_tag in all_links:
        raw_href = a_tag["href"].strip()

        # Ignorer les ancres vides et javascript
        if not raw_href or raw_href == "#" or raw_href.startswith("javascript"):
            continue

        # Convertir en URL absolue
        abs_url = _normalize_href(raw_href)

        # Parser l'URL pour extraire le chemin
        parsed = urlparse(abs_url)
        host = parsed.hostname or ""

        # Accepter uniquement steamrip.com (absolu) OU les liens relatifs
        is_steamrip_absolute = "steamrip.com" in host
        is_relative = raw_href.startswith("/") and not raw_href.startswith("//")

        if not is_steamrip_absolute and not is_relative:
            continue

        # Si c'est un lien externe NON steamrip, skip
        if parsed.scheme in ("http", "https") and host and "steamrip.com" not in host:
            continue

        path = parsed.path

        # Vérifier que c'est un chemin de jeu
        if not _is_game_path(path):
            continue

        # Slug pour déduplication
        slug = path.strip("/").lower()
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Nom du jeu
        text = a_tag.get_text(strip=True)
        if text and len(text) >= 2:
            name = _clean_game_name(text)
        else:
            name = _name_from_slug(slug)

        if not name or len(name) < 2:
            name = _name_from_slug(slug)

        full_url = f"{BASE_URL}/{slug}/"
        games.append({"name": name, "url": full_url})

    if verbose:
        print(f"  {C.DIM}   🎯 Liens de jeux trouvés : {len(games)}{C.RST}")

    # ── Fallback : regex brute sur le HTML ──
    if len(games) < 10:
        count_regex = 0
        # Chercher des href relatifs : href="/slug/" ou href="/slug-free-download/"
        pattern = re.compile(
            r'href=["\']'
            r'(/[a-z0-9][a-z0-9\-]+-free-download/?'   # /xxx-free-download/
            r'|/[a-z0-9][a-z0-9\-]*[a-z0-9]/?)'         # /xxx/
            r'["\']',
            re.IGNORECASE
        )
        for m in pattern.finditer(html):
            path = m.group(1)
            if not _is_game_path(path):
                continue
            slug = path.strip("/").lower()
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            name = _name_from_slug(slug)
            full_url = f"{BASE_URL}/{slug}/"
            games.append({"name": name, "url": full_url})
            count_regex += 1

        if verbose and count_regex:
            print(f"  {C.DIM}   🎯 Regex fallback        : +{count_regex}{C.RST}")

    # ── Dédupliquer par nom ──
    seen_names = set()
    unique = []
    for g in games:
        key = g["name"].lower().strip()
        if key not in seen_names:
            seen_names.add(key)
            unique.append(g)

    if verbose:
        print(f"  {C.GREEN}{C.BOLD}  ✔ {len(unique)} jeux uniques indexés.{C.RST}")

    return unique


def scrape_games_list(verbose=True) -> list[dict]:
    if verbose:
        print(f"  {C.DIM}⏳ Téléchargement de la liste…{C.RST}")
        if not HAS_CLOUDSCRAPER:
            print(f"  {C.YEL}💡 pip install cloudscraper (bypass Cloudflare){C.RST}")

    html = fetch_page(verbose)
    if not html:
        print(f"  {C.RED}✖ Impossible de télécharger.{C.RST}")
        _print_debug_help()
        return []

    games = parse_games_from_html(html, verbose)
    if not games:
        _print_debug_info(html)
        _print_debug_help()
    return games


def _print_debug_info(html: str):
    print(f"\n  {C.YEL}── Debug ──────────────────────────────────{C.RST}")
    print(f"  {C.DIM}Taille : {len(html):,} octets{C.RST}")

    soup = BeautifulSoup(html[:5000], "html.parser")
    title = soup.find("title")
    if title:
        print(f"  {C.DIM}Titre  : {title.get_text(strip=True)}{C.RST}")

    lower = html[:5000].lower()
    if "cloudflare" in lower:
        print(f"  {C.RED}⚠ Page Cloudflare !{C.RST}")

    # Montrer les 10 premiers href
    all_hrefs = re.findall(r'href=["\']([^"\']{5,80})["\']', html[:50000])
    print(f"  {C.DIM}Premiers href :{C.RST}")
    shown = set()
    for h in all_hrefs:
        if h in shown or h.startswith("#") or "css" in h or "js" in h:
            continue
        shown.add(h)
        marker = "✓" if ("/free-download" in h.lower() or (
            h.startswith("/") and len(h) > 5 and "wp-" not in h
        )) else " "
        print(f"    {C.BLUE}{marker} {h}{C.RST}")
        if len(shown) >= 15:
            break

    print(f"  {C.DIM}HTML sauvegardé : {DEBUG_FILE}{C.RST}")
    print(f"  {C.YEL}───────────────────────────────────────────{C.RST}\n")


def _print_debug_help():
    print(f"""
  {C.YEL}{C.BOLD}💡 Solutions :{C.RST}
  {C.WHITE}1.{C.RST} pip install cloudscraper
  {C.WHITE}2.{C.RST} Sauvegardez la page manuellement (Ctrl+S) puis :
     python {sys.argv[0]} --from-file "fichier.html"
  {C.WHITE}3.{C.RST} Vérifiez : {GAMES_LIST_URL}
""")


# ═══════════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════════

def save_cache(games):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"ts": time.time(), "count": len(games), "games": games}
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_cache():
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data["ts"] > CACHE_MAX_AGE:
            return None
        return data["games"]
    except (json.JSONDecodeError, KeyError):
        return None


def get_games(force_refresh=False, from_file=None, verbose=True):
    if from_file:
        p = Path(from_file)
        if p.exists():
            if verbose:
                print(f"  {C.DIM}📂 Chargement depuis {p}{C.RST}")
            html = p.read_text(encoding="utf-8", errors="replace")
            games = parse_games_from_html(html, verbose)
            if games:
                save_cache(games)
            return games
        else:
            print(f"  {C.RED}✖ Fichier introuvable : {p}{C.RST}")
            return []

    if not force_refresh:
        cached = load_cache()
        if cached:
            if verbose:
                print(f"  {C.DIM}📦 {len(cached)} jeux depuis le cache.{C.RST}")
            return cached

    games = scrape_games_list(verbose)
    if games:
        save_cache(games)
    return games


# ═══════════════════════════════════════════════════════════════
#  ALGORITHME DE RECHERCHE
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

    # Starts with
    if g_norm.startswith(q_norm):
        score += 600

    # Substring
    if q_norm in g_norm:
        score += 400
    elif g_norm in q_norm:
        score += 200

    # Token coverage
    found = sum(1 for t in q_tokens if any(t in gt for gt in g_tokens))
    cov = found / len(q_tokens) if q_tokens else 0
    score += cov * 300
    if cov == 1.0:
        score += 150

    # Sub-token
    sub = sum(1 for qt in q_tokens if any(qt in gt for gt in g_tokens))
    score += (sub / max(len(q_tokens), 1)) * 100

    # Token set Jaccard
    sa, sb = set(q_tokens), set(g_tokens)
    inter = sa & sb
    union = sa | sb
    if union:
        jaccard = len(inter) / len(union)
        tcov = len(inter) / len(sa) if sa else 0
        score += (jaccard * 0.4 + tcov * 0.6) * 200

    # Fuzzy global
    score += SequenceMatcher(None, q_norm, g_norm).ratio() * 150

    # Fuzzy sorted
    qs = " ".join(sorted(q_tokens))
    gs = " ".join(sorted(g_tokens))
    score += SequenceMatcher(None, qs, gs).ratio() * 120

    # Partial ratio
    score += partial_ratio(q_norm, g_norm) * 250

    # Acronym
    if len(q_tokens) == 1 and len(q_norm) <= 6:
        acr = "".join(t[0] for t in g_tokens if t)
        if q_norm == acr:
            score += 500

    # Prefix match
    pre = sum(1 for qt in q_tokens if any(gt.startswith(qt) for gt in g_tokens))
    score += (pre / max(len(q_tokens), 1)) * 100

    # Length penalty
    score -= abs(len(q_norm) - len(g_norm)) * 0.8

    # Same token count bonus
    if len(q_tokens) == len(g_tokens):
        score += 30

    return score


def search(query, games, limit=15, threshold=50.0):
    q_norm   = normalize(query)
    q_tokens = tokenize(q_norm)
    if not q_norm:
        return []

    scored = []
    for g in games:
        gn = normalize(g["name"])
        gt = tokenize(gn)
        if not gn:
            continue
        sc = compute_score(q_norm, q_tokens, gn, gt)
        if sc >= threshold:
            scored.append({"name": g["name"], "url": g["url"], "score": round(sc, 1)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


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


def display_results(results, query):
    if not results:
        print(f"\n  {C.YEL}⚠  Aucun résultat pour « {query} »{C.RST}")
        print(f"  {C.DIM}Essayez un autre nom ou 'refresh'.{C.RST}\n")
        return None

    mx = results[0]["score"]
    print(f"\n  {C.GREEN}{C.BOLD}✔ {len(results)} résultat(s) pour « {query} »{C.RST}")
    print(SEP)

    for i, r in enumerate(results, 1):
        bar = score_bar(r["score"], mx)
        print(
            f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}\n"
            f"        {C.BLUE}🔗  {r['url']}{C.RST}\n"
            f"        {bar}  {C.DIM}score: {r['score']}{C.RST}"
        )
        print(SEP)
    return results


def prompt_open(results):
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
#  MODES
# ═══════════════════════════════════════════════════════════════

def interactive_mode(limit=15, from_file=None):
    print(BANNER)
    games = get_games(from_file=from_file, verbose=True)
    if not games:
        print(f"  {C.RED}✖ Impossible de charger la liste.{C.RST}")
        return

    print(f"\n  {C.DIM}Commandes :  <jeu>  |  refresh  |  stats  |  list  |  q{C.RST}\n")

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

        if cmd == "refresh":
            print()
            games = get_games(force_refresh=True, verbose=True)
            if games:
                print(f"  {C.GREEN}✔ Rafraîchi.{C.RST}\n")
            continue

        if cmd == "stats":
            print(f"\n  {C.WHITE}{C.BOLD}📊 {len(games)} jeux.{C.RST}")
            if CACHE_FILE.exists():
                age = time.time() - json.loads(CACHE_FILE.read_text())["ts"]
                print(f"  {C.DIM}   Cache : {int(age // 60)} min{C.RST}")
            print()
            continue

        if cmd == "list":
            print(f"\n  {C.WHITE}{C.BOLD}📋 50 premiers jeux :{C.RST}")
            for g in sorted(games, key=lambda x: x["name"].lower())[:50]:
                print(f"    {C.DIM}•{C.RST} {g['name']}")
            if len(games) > 50:
                print(f"  {C.DIM}  … +{len(games) - 50} autres.{C.RST}")
            print()
            continue

        results = search(raw, games, limit=limit)
        displayed = display_results(results, raw)
        prompt_open(displayed or [])
        print()


def single_search(game_name, limit=15, auto_open=False, from_file=None):
    print(BANNER)
    games = get_games(from_file=from_file, verbose=True)
    if not games:
        print(f"  {C.RED}✖ Impossible de charger la liste.{C.RST}")
        return

    results = search(game_name, games, limit=limit)
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
        description="🎮 SteamRip Search CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                            # interactif
  python {sys.argv[0]} "Elden Ring"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --from-file page.html       # depuis HTML local
        """,
    )
    parser.add_argument("game", nargs="*", help="Nom du jeu")
    parser.add_argument("-n", "--num", type=int, default=15)
    parser.add_argument("-o", "--open", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-r", "--refresh", action="store_true")
    parser.add_argument("-f", "--from-file", type=str, default=None,
                        help="HTML local (Ctrl+S depuis navigateur)")

    args = parser.parse_args()

    if args.refresh and CACHE_FILE.exists():
        CACHE_FILE.unlink()

    if args.interactive or not args.game:
        interactive_mode(args.num, from_file=args.from_file)
    else:
        single_search(" ".join(args.game), args.num, args.open, args.from_file)


if __name__ == "__main__":
    main()