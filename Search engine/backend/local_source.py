import json
from pathlib import Path

from .utils import normalize, similarity


# Le chemin vers la base de données locale (peut ne pas exister sur Vercel)
LOCAL_DB_PATH = Path(__file__).resolve().parent.parent / "local_source" / "games.json"


def load_local_games() -> list[dict]:
    if not LOCAL_DB_PATH.exists():
        return []
    try:
        raw = json.loads(LOCAL_DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    games = raw.get("games") if isinstance(raw, dict) else None
    if not isinstance(games, list):
        return []
    out = []
    for g in games:
        if not isinstance(g, dict):
            continue
        name = (g.get("name") or "").strip()
        if not name:
            continue
        out.append(g)
    return out


def search_local(query: str, limit: int = 10) -> list[dict]:
    try:
        qn = normalize(query)
        if not qn:
            return []
        games = load_local_games()
        scored = []
        for g in games:
            name = (g.get("name") or "").strip()
            sc = similarity(query, name)
            if sc <= 0:
                continue
            scored.append((sc, g))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for sc, g in scored[:limit]:
            gg = dict(g)
            gg["score"] = round(sc * 10000, 1)
            out.append(gg)
        return out
    except Exception:
        return []
