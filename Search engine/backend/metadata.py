import os

import requests

from .utils import TTLCache, normalize, similarity


_cache = TTLCache(ttl_seconds=1800)


def _get_json(url: str, params: dict | None = None, timeout: int = 12):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def steam_store_search(query: str) -> dict | None:
    qn = normalize(query)
    if not qn:
        return None

    cache_key = f"steam_search:{qn}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        data = _get_json(
            "https://store.steampowered.com/api/storesearch",
            params={"term": query, "l": "english", "cc": "us"},
            timeout=12,
        )
    except Exception:
        _cache.set(cache_key, None)
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        _cache.set(cache_key, None)
        return None

    best = None
    best_sc = 0.0
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        appid = it.get("id")
        if not name or not appid:
            continue
        sc = similarity(query, name)
        if normalize(name) == qn:
            sc += 0.5
        if sc > best_sc:
            best_sc = sc
            best = {"appid": int(appid), "name": name}

    _cache.set(cache_key, best)
    return best


def steam_app_details(appid: int) -> dict | None:
    cache_key = f"steam_details:{appid}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        data = _get_json(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(appid), "l": "english", "cc": "us"},
            timeout=12,
        )
    except Exception:
        _cache.set(cache_key, None)
        return None

    block = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(block, dict) or not block.get("success"):
        _cache.set(cache_key, None)
        return None

    d = block.get("data") or {}
    platforms = d.get("platforms") or {}
    out = {
        "appid": appid,
        "name": d.get("name"),
        "release_date": (d.get("release_date") or {}).get("date"),
        "header_image": d.get("header_image"),
        "website": d.get("website"),
        "short_description": d.get("short_description"),
        "developers": d.get("developers") or [],
        "publishers": d.get("publishers") or [],
        "genres": [g.get("description") for g in (d.get("genres") or []) if isinstance(g, dict) and g.get("description")],
        "platforms": {
            "windows": bool(platforms.get("windows")),
            "mac": bool(platforms.get("mac")),
            "linux": bool(platforms.get("linux")),
        },
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "price_overview": d.get("price_overview"),
        "steam_url": f"https://store.steampowered.com/app/{appid}",
        "steamdb_url": f"https://steamdb.info/app/{appid}",
    }

    _cache.set(cache_key, out)
    return out


def rawg_search(query: str) -> dict | None:
    key = (os.getenv("RAWG_API_KEY") or "").strip()
    if not key:
        return None

    qn = normalize(query)
    cache_key = f"rawg_search:{qn}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        data = _get_json(
            "https://api.rawg.io/api/games",
            params={"key": key, "search": query, "page_size": 10},
            timeout=12,
        )
    except Exception:
        _cache.set(cache_key, None)
        return None

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        _cache.set(cache_key, None)
        return None

    best = None
    best_sc = 0.0
    for it in results[:10]:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if not name:
            continue
        sc = similarity(query, name)
        if sc > best_sc:
            best_sc = sc
            best = {
                "id": it.get("id"),
                "name": name,
                "slug": it.get("slug"),
                "released": it.get("released"),
                "genres": it.get("genres") or [],
                "rawg_url": f"https://rawg.io/games/{it.get('slug')}" if it.get("slug") else None,
            }

    _cache.set(cache_key, best)
    return best
