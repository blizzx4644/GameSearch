#!/usr/bin/env python3
"""
🎮 GOG-Games Search CLI
Récupère https://gog-games.to/api/web/all-games puis recherche locale avec scoring avancé.
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

try:
    import requests
except ImportError:
    print("❌ Dépendance manquante :")
    print("   pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

BASE_URL       = "https://www.gog-games.to"
API_ALL_GAMES  = f"{BASE_URL}/api/web/all-games"
GAME_PAGE_URL  = f"{BASE_URL}/game/"                 # + slug

CACHE_DIR      = Path.home() / ".goggames_cache"
CACHE_FILE     = CACHE_DIR / "games.json"
CACHE_MAX_AGE  = 60 * 60 * 12   # 12 h

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


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
 ║          🎮  GOG-Games Search CLI  🎮                ║
 ║   Base locale · Recherche fuzzy · gog-games.to       ║
 ╚═══════════════════════════════════════════════════════╝{C.RST}
"""
SEP = f"  {C.DIM}{'─' * 60}{C.RST}"


# ═══════════════════════════════════════════════════════════════
#  HTTP  –  Récupération de l'API
# ═══════════════════════════════════════════════════════════════

def fetch_all_games(verbose=True) -> list[dict] | None:
    """GET /api/web/all-games → liste JSON brute."""
    session = requests.Session()
    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept":          "application/json",
        "Accept-Language":  "en-US,en;q=0.9",
        "Referer":          f"{BASE_URL}/",
    })

    try:
        if verbose:
            print(f"  {C.DIM}🌐 GET {API_ALL_GAMES}{C.RST}")

        resp = session.get(API_ALL_GAMES, timeout=30)

        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", 10))
            if verbose:
                print(f"  {C.YEL}⏳ Rate-limit, attente {retry}s…{C.RST}")
            time.sleep(retry)
            resp = session.get(API_ALL_GAMES, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                if verbose:
                    print(f"  {C.GREEN}✔ {len(data)} jeux récupérés depuis l'API{C.RST}")
                return data
            else:
                if verbose:
                    print(f"  {C.YEL}⚠ Réponse inattendue (pas une liste){C.RST}")
                return None

        if verbose:
            print(f"  {C.RED}✖ HTTP {resp.status_code}{C.RST}")
        return None

    except requests.exceptions.JSONDecodeError:
        if verbose:
            print(f"  {C.RED}✖ Réponse non-JSON (Cloudflare ?){C.RST}")
        return None
    except requests.RequestException as e:
        if verbose:
            print(f"  {C.RED}✖ Erreur réseau : {e}{C.RST}")
        return None


# ═══════════════════════════════════════════════════════════════
#  TRANSFORMATION  –  JSON API → format interne
# ═══════════════════════════════════════════════════════════════

def transform_games(raw: list[dict], verbose=True) -> list[dict]:
    """
    Transforme la réponse API en liste de dicts uniformes.
    Chaque item API :
      { id, slug, title, developer, publisher, image, background,
        gog_url, is_indev, last_update, infohash }
    → format interne :
      { name, url, slug, developer, publisher, gog_url, is_indev, last_update }
    """
    games = []
    seen_slugs = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        slug  = (item.get("slug") or "").strip()
        title = (item.get("title") or "").strip()

        if not title and not slug:
            continue
        if not title:
            title = slug.replace("_", " ").replace("-", " ").title()

        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        url = f"{GAME_PAGE_URL}{slug}" if slug else ""

        game = {
            "name":        title,
            "url":         url,
            "slug":        slug,
            "developer":   (item.get("developer") or "").strip(),
            "publisher":   (item.get("publisher") or "").strip(),
            "gog_url":     (item.get("gog_url") or "").strip(),
            "is_indev":    item.get("is_indev", False),
            "last_update": (item.get("last_update") or "").strip(),
        }
        games.append(game)

    if verbose:
        indev = sum(1 for g in games if g["is_indev"])
        print(f"  {C.GREEN}{C.BOLD}  ✔ {len(games)} jeux indexés"
              f" ({indev} en early-access){C.RST}")

    return games


# ═══════════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════════

def save_cache(games: list[dict]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"ts": time.time(), "count": len(games), "games": games}
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_cache() -> list[dict] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data["ts"] > CACHE_MAX_AGE:
            return None
        return data["games"]
    except (json.JSONDecodeError, KeyError):
        return None


def get_games(force_refresh=False, from_file=None, verbose=True) -> list[dict]:
    # ── Fichier local (JSON brut ou cache) ──
    if from_file:
        p = Path(from_file)
        if p.exists():
            if verbose:
                print(f"  {C.DIM}📂 Chargement depuis {p}{C.RST}")
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                games = transform_games(raw, verbose)
            elif isinstance(raw, dict) and "games" in raw:
                games = raw["games"]           # c'est déjà un cache
            else:
                games = []
            if games:
                save_cache(games)
            return games
        else:
            print(f"  {C.RED}✖ Fichier introuvable : {p}{C.RST}")
            return []

    # ── Cache ──
    if not force_refresh:
        cached = load_cache()
        if cached:
            if verbose:
                print(f"  {C.DIM}📦 {len(cached)} jeux depuis le cache{C.RST}")
            return cached

    # ── API ──
    if verbose:
        print(f"  {C.DIM}⏳ Téléchargement de la liste complète…{C.RST}")

    raw = fetch_all_games(verbose)
    if not raw:
        print(f"  {C.RED}✖ Impossible de récupérer la liste.{C.RST}")
        _print_help()
        return []

    games = transform_games(raw, verbose)
    if games:
        save_cache(games)
    return games


def _print_help():
    print(f"""
  {C.YEL}{C.BOLD}💡 Solutions :{C.RST}
  {C.WHITE}1.{C.RST} Vérifiez l'accès : curl {API_ALL_GAMES} | head
  {C.WHITE}2.{C.RST} Sauvez le JSON manuellement puis :
     python {sys.argv[0]} --from-file all-games.json
  {C.WHITE}3.{C.RST} Rate-limit ? Attendez quelques minutes.
""")


# ═══════════════════════════════════════════════════════════════
#  ALGORITHME DE RECHERCHE  (identique à steamrip_game-list.py)
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

        # Bonus : chercher aussi dans developer / publisher
        dev = normalize(g.get("developer", ""))
        pub = normalize(g.get("publisher", ""))
        if q_norm in dev or q_norm in pub:
            sc += 80
        elif any(t in dev or t in pub for t in q_tokens):
            sc += 30

        if sc >= threshold:
            scored.append({**g, "score": round(sc, 1)})

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

        # Ligne titre
        indev = f"  {C.YEL}[Early Access]{C.RST}" if r.get("is_indev") else ""
        print(f"\n  {C.CYAN}{C.BOLD} [{i:>2}] {C.WHITE}{r['name']}{C.RST}{indev}")

        # URL gog-games
        print(f"        {C.BLUE}🔗  {r['url']}{C.RST}")

        # Metadata
        meta = []
        if r.get("developer"):
            meta.append(f"🛠  {r['developer']}")
        if r.get("publisher") and r["publisher"] != r.get("developer"):
            meta.append(f"📦 {r['publisher']}")
        if r.get("last_update"):
            date = r["last_update"][:10]
            meta.append(f"📅 {date}")
        if meta:
            print(f"        {C.DIM}{' │ '.join(meta)}{C.RST}")

        # GOG.com link
        if r.get("gog_url"):
            print(f"        {C.DIM}🏪  {r['gog_url']}{C.RST}")

        # Score bar
        print(f"        {bar}  {C.DIM}score: {r['score']}{C.RST}")
        print(SEP)

    return results


def prompt_open(results):
    if not results:
        return
    print(f"\n  {C.MAG}Ouvrir ? numéro │ g<n> = page GOG.com │ Entrée = passer{C.RST}")
    try:
        ch = input(f"  {C.MAG}{C.BOLD}▸ {C.RST}").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if not ch:
        return

    # "g3" → ouvrir la page GOG.com du résultat 3
    if ch.lower().startswith("g") and ch[1:].strip().isdigit():
        idx = int(ch[1:].strip()) - 1
        if 0 <= idx < len(results):
            gog = results[idx].get("gog_url", "")
            if gog:
                print(f"  {C.GREEN}🏪 {gog}{C.RST}\n")
                webbrowser.open(gog)
            else:
                print(f"  {C.YEL}Pas de lien GOG.com pour ce jeu.{C.RST}")
        else:
            print(f"  {C.RED}Numéro invalide.{C.RST}")
        return

    # Numéro simple → ouvrir la page gog-games.to
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

    print(f"\n  {C.DIM}Commandes :  <jeu>  │  refresh  │  stats  │  list  │  q{C.RST}\n")

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
            print(f"\n  {C.WHITE}{C.BOLD}📊 {len(games)} jeux dans la base.{C.RST}")
            indev = sum(1 for g in games if g.get("is_indev"))
            devs  = len({g["developer"] for g in games if g.get("developer")})
            print(f"  {C.DIM}   Early-access : {indev}{C.RST}")
            print(f"  {C.DIM}   Développeurs  : {devs}{C.RST}")
            if CACHE_FILE.exists():
                age = time.time() - json.loads(CACHE_FILE.read_text())["ts"]
                h, m = divmod(int(age), 3600)
                print(f"  {C.DIM}   Cache         : {h}h{m // 60:02d}m{C.RST}")
            print()
            continue

        if cmd == "list":
            print(f"\n  {C.WHITE}{C.BOLD}📋 50 premiers jeux (A-Z) :{C.RST}")
            for g in sorted(games, key=lambda x: x["name"].lower())[:50]:
                dev = f"  {C.DIM}({g['developer']}){C.RST}" if g.get("developer") else ""
                print(f"    {C.DIM}•{C.RST} {g['name']}{dev}")
            if len(games) > 50:
                print(f"  {C.DIM}  … +{len(games) - 50} autres.{C.RST}")
            print()
            continue

        if cmd.startswith("dev "):
            dev_q = normalize(raw[4:])
            found = [g for g in games if dev_q in normalize(g.get("developer", ""))]
            if found:
                print(f"\n  {C.WHITE}{C.BOLD}🛠  {len(found)} jeux (dev ≈ « {raw[4:]} ») :{C.RST}")
                for g in sorted(found, key=lambda x: x["name"].lower())[:30]:
                    print(f"    {C.DIM}•{C.RST} {g['name']}  {C.DIM}({g['developer']}){C.RST}")
                if len(found) > 30:
                    print(f"  {C.DIM}  … +{len(found) - 30} autres.{C.RST}")
            else:
                print(f"\n  {C.YEL}⚠ Aucun dev trouvé pour « {raw[4:]} »{C.RST}")
            print()
            continue

        results = search(raw, games, limit=limit)
        displayed = display_results(results, raw)
        prompt_open(displayed or [])
        print()


def single_search(game_name, limit=15, auto_open=False, from_file=None,
                  json_output=False):
    if not json_output:
        print(BANNER)

    games = get_games(from_file=from_file, verbose=not json_output)
    if not games:
        if not json_output:
            print(f"  {C.RED}✖ Impossible de charger la liste.{C.RST}")
        return

    results = search(game_name, games, limit=limit)

    # ── Sortie JSON ──
    if json_output:
        out = {
            "query":   game_name,
            "count":   len(results),
            "results": [
                {
                    "name":        r["name"],
                    "url":         r["url"],
                    "slug":        r.get("slug", ""),
                    "developer":   r.get("developer", ""),
                    "publisher":   r.get("publisher", ""),
                    "gog_url":     r.get("gog_url", ""),
                    "is_indev":    r.get("is_indev", False),
                    "last_update": r.get("last_update", ""),
                    "score":       r["score"],
                }
                for r in results
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # ── Sortie interactive ──
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
        description="🎮 GOG-Games Search CLI — gog-games.to",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
exemples :
  python {sys.argv[0]}                              # interactif
  python {sys.argv[0]} "Elden Ring"
  python {sys.argv[0]} --open "Hollow Knight"
  python {sys.argv[0]} --json "Stardew Valley"       # sortie JSON
  python {sys.argv[0]} --from-file all-games.json     # depuis JSON local

mode interactif :
  <jeu>          rechercher un jeu
  dev <nom>      lister les jeux d'un développeur
  stats          infos sur la base
  list           50 premiers jeux A-Z
  refresh        re-télécharger depuis l'API
  q              quitter
        """,
    )
    parser.add_argument("game", nargs="*", help="Nom du jeu")
    parser.add_argument("-n", "--num", type=int, default=15)
    parser.add_argument("-o", "--open", action="store_true",
                        help="Ouvrir le 1er résultat dans le navigateur")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-r", "--refresh", action="store_true",
                        help="Forcer le re-téléchargement")
    parser.add_argument("-j", "--json", action="store_true",
                        help="Sortie JSON (pour piping)")
    parser.add_argument("-f", "--from-file", type=str, default=None,
                        help="JSON local (curl … > all-games.json)")

    args = parser.parse_args()

    if args.refresh and CACHE_FILE.exists():
        CACHE_FILE.unlink()

    if args.interactive or not args.game:
        interactive_mode(args.num, from_file=args.from_file)
    else:
        single_search(
            " ".join(args.game), args.num, args.open,
            args.from_file, args.json,
        )


if __name__ == "__main__":
    main()