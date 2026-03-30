from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .metadata import rawg_search, steam_app_details, steam_store_search
from .providers import list_provider_ids, search_providers
from .utils import normalize


app = FastAPI(title="GameSearch")

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


@app.get("/api/search")
def api_search(q: str, mode: str = "fast", sources: str | None = None, allow_broad_fallback: int = 0):
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Missing query")

    src_list = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]

    steam_hit = steam_store_search(q)
    steam = steam_app_details(steam_hit["appid"]) if steam_hit and "appid" in steam_hit else None
    rawg = rawg_search(q)

    steam_hit_name = steam_hit.get("name") if isinstance(steam_hit, dict) else None
    steam_name = steam.get("name") if isinstance(steam, dict) else None
    provider_query = steam_name or steam_hit_name or q
    links, used_providers = search_providers(provider_query, limit_per_provider=8, mode=mode, sources=src_list)
    has_canonical = bool(steam_name or steam_hit_name)
    if not links and provider_query != q and (allow_broad_fallback == 1) and (not has_canonical):
        provider_query = q
        links, used_providers = search_providers(provider_query, limit_per_provider=8, mode=mode, sources=src_list)

    game_name = None
    if steam and steam.get("name"):
        game_name = steam["name"]
    elif rawg and rawg.get("name"):
        game_name = rawg["name"]
    elif links:
        game_name = links[0].get("name")

    game = {
        "id": normalize(game_name or q) or normalize(q),
        "name": game_name or q,
        "steam_appid": steam.get("appid") if steam else None,
        "release_date": steam.get("release_date") if steam else (rawg.get("released") if rawg else None),
    }

    metadata = {
        "steam": steam,
        "rawg": rawg,
    }

    return {
        "query": q,
        "provider_query": provider_query,
        "game": game,
        "metadata": metadata,
        "links": links,
        "providers": used_providers,
        "available_providers": list_provider_ids(),
    }


@app.get("/api/providers")
def api_providers():
    return {
        "available_providers": list_provider_ids(),
        "default_mode": "fast",
    }


@app.get("/api/provider")
def api_provider_alias():
    return api_providers()


@app.get("/favicon.ico")
def favicon():
    raise HTTPException(status_code=204)


@app.get("/")
def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="frontend/index.html not found")
    return FileResponse(index_file)


app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR)),
    name="static",
)
