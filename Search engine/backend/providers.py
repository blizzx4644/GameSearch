import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from pathlib import Path

import requests

from .utils import normalize, similarity, TTLCache
from .local_source import search_local


ROOT = Path(__file__).resolve().parent.parent

# Debug: Afficher les chemins des providers au démarrage
print(f"[DEBUG] ROOT path: {ROOT}")
for provider in PROVIDERS:
    print(f"[DEBUG] Provider {provider['id']}: {provider['path']} (exists: {provider['path'].exists()})")


MIN_ACCEPT_SCORE = 2000.0

DEFAULT_MODE = "fast"  # fast | all

PROVIDER_MAX_WORKERS = 6
PROVIDER_TIME_BUDGET_SECONDS = 6.5


_module_cache: dict[str, object] = {}


def _load_module(name: str, path: Path):
    cache_key = f"{name}:{str(path)}"
    cached = _module_cache.get(cache_key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache[cache_key] = mod
    return mod


_cache = TTLCache(ttl_seconds=900)
_byxatab_check_cache = TTLCache(ttl_seconds=60 * 60 * 6)


PROVIDERS: list[dict] = [
    {"id": "goggames", "path": ROOT / "GogGames" / "goggames_game-list_search.py", "kind": "goggames", "tier": "fast"},
    {"id": "steamrip", "path": ROOT / "SteamRip" / "steamrip_game-list_search.py", "kind": "steamrip", "tier": "fast"},

    {"id": "fitgirl", "path": ROOT / "FitGirl" / "fitgirl_search.py", "kind": "do_search", "tier": "slow"},
    {"id": "ankergames", "path": ROOT / "AnkerGames" / "ankergames_search.py", "kind": "do_search", "tier": "slow"},
    {"id": "byxatab", "path": ROOT / "ByXatab" / "byxatab_search.py", "kind": "do_search", "tier": "slow"},
    {"id": "cpgrepacks", "path": ROOT / "CPGRepacks" / "cpgrepacks_search.py", "kind": "do_search", "tier": "slow"},
    {"id": "crackedgames", "path": ROOT / "CrackedGames" / "crackedgames_search.py", "kind": "do_search", "tier": "slow"},
    {"id": "gamebounty", "path": ROOT / "GameBounty" / "gamebounty_search.py", "kind": "do_search", "tier": "slow"},
]


def list_provider_ids() -> list[str]:
    return [p["id"] for p in PROVIDERS]


def _select_providers(mode: str, sources: list[str] | None) -> list[dict]:
    mode = (mode or DEFAULT_MODE).strip().lower()
    wanted = None
    if sources:
        wanted = {s.strip().lower() for s in sources if (s or "").strip()}

    out: list[dict] = []
    for p in PROVIDERS:
        pid = p["id"]
        if wanted is not None:
            if pid in wanted:
                out.append(p)
            continue
        if mode == "all":
            out.append(p)
        else:
            if p.get("tier") == "fast":
                out.append(p)
    return out


def _byxatab_accessible(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False

    cache_key = f"byxatab_ok:{url.rstrip('/').lower()}"
    cached = _byxatab_check_cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    try:
        r = requests.get(
            url,
            timeout=10,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
            },
        )
        text = (r.text or "")[:120_000]
        blocked = (
            "Внимание! Обнаружена ошибка" in text
            or "Гости не имеют доступа" in text
            or "Guests do not have access" in text
        )
        ok = (r.status_code == 200) and (not blocked)
    except Exception:
        ok = False

    _byxatab_check_cache.set(cache_key, ok)
    return ok


def _call_do_search(mod, query: str, limit: int) -> list[dict]:
    fn = getattr(mod, "do_search", None)
    if not callable(fn):
        return []
    try:
        return fn(query, limit=limit, verbose=False)
    except TypeError:
        return fn(query)
    except Exception:
        return []


def _call_steamrip(mod, query: str, limit: int) -> list[dict]:
    try:
        games = mod.get_games(force_refresh=False, verbose=False)
        return mod.search(query, games, limit=limit)
    except Exception:
        return []


def _call_goggames(mod, query: str, limit: int) -> list[dict]:
    try:
        games = mod.get_games(force_refresh=False, verbose=False)
        return mod.search(query, games, limit=limit)
    except Exception:
        return []


def _coerce_link(provider: str, item: dict) -> dict | None:
    url = (item.get("url") or "").strip()
    name = (item.get("name") or "").strip()
    if not url or not name:
        return None
    out = {
        "source": provider,
        "name": name,
        "url": url,
    }
    if "score" in item:
        try:
            out["score"] = float(item["score"])
        except Exception:
            pass
    meta = {k: v for k, v in item.items() if k not in {"name", "url", "score"}}
    if meta:
        out["meta"] = meta
    return out


def _rank_links(query: str, links: list[dict]) -> list[dict]:
    qn = normalize(query)
    out = []
    for l in links:
        nm = l.get("name") or ""
        score = l.get("score")
        if isinstance(score, (int, float)):
            effective = float(score)
        else:
            effective = similarity(query, nm) * 10000.0

        if qn and normalize(nm) == qn:
            effective += 500.0

        if effective < MIN_ACCEPT_SCORE:
            continue

        s = effective
        ll = dict(l)
        ll["_rank"] = s
        ll.setdefault("score", round(effective, 1))
        out.append(ll)
    out.sort(key=lambda x: x.get("_rank", 0), reverse=True)
    for l in out:
        l.pop("_rank", None)
    return out


def search_providers(
    query: str,
    limit_per_provider: int = 8,
    mode: str = DEFAULT_MODE,
    sources: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    sel = ",".join(sorted([s.strip().lower() for s in (sources or []) if (s or "").strip()]))
    cache_key = f"providers:{normalize(query)}:{limit_per_provider}:{(mode or '').lower()}:{sel}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    selected = _select_providers(mode=mode, sources=sources)

    links: list[dict] = []
    used: list[str] = []

    def _run_provider(pconf: dict) -> tuple[str, list[dict]]:
        provider = pconf["id"]
        path: Path = pconf["path"]
        kind = pconf["kind"]

        # Debug: Vérifier si le fichier existe
        if not path.exists():
            print(f"[DEBUG] Provider {provider}: file not found at {path}")
            return provider, []
        
        try:
            mod = _load_module(f"provider_{provider}", path)
            print(f"[DEBUG] Provider {provider}: module loaded successfully")
        except Exception as e:
            print(f"[DEBUG] Provider {provider}: failed to load module - {e}")
            return provider, []

        if kind == "steamrip":
            results = _call_steamrip(mod, query, limit_per_provider)
        elif kind == "goggames":
            results = _call_goggames(mod, query, limit_per_provider)
        else:
            results = _call_do_search(mod, query, limit_per_provider)

        if not isinstance(results, list) or not results:
            print(f"[DEBUG] Provider {provider}: no results returned")
            return provider, []

        print(f"[DEBUG] Provider {provider}: found {len(results)} raw results")
        out: list[dict] = []
        for it in results:
            if not isinstance(it, dict):
                continue
            l = _coerce_link(provider, it)
            if l and provider == "byxatab":
                if not _byxatab_accessible(l.get("url") or ""):
                    continue
            if l:
                out.append(l)
        
        print(f"[DEBUG] Provider {provider}: returning {len(out)} processed links")
        return provider, out

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=PROVIDER_MAX_WORKERS) as ex:
        futures = [ex.submit(_run_provider, p) for p in selected]
        timeout = max(0.05, PROVIDER_TIME_BUDGET_SECONDS)
        try:
            for fut in as_completed(futures, timeout=timeout):
                prov, out_links = fut.result()
                if out_links:
                    used.append(prov)
                    links.extend(out_links)
                if (time.monotonic() - start) >= PROVIDER_TIME_BUDGET_SECONDS:
                    break
        except FuturesTimeoutError:
            pass

    local = search_local(query, limit=limit_per_provider)
    if local:
        used.append("local")
        for it in local:
            url = (it.get("url") or "").strip()
            if not url:
                continue
            links.append({
                "source": "local",
                "name": (it.get("name") or "").strip(),
                "url": url,
                "score": float(it.get("score") or 0),
                "meta": {k: v for k, v in it.items() if k not in {"name", "url", "score"}},
            })

    links = _rank_links(query, links)

    seen = set()
    deduped = []
    for l in links:
        k = (l.get("source"), (l.get("url") or "").rstrip("/").lower())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(l)

    res = (deduped, used)
    _cache.set(cache_key, res)
    return res
