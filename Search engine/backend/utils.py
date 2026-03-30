import re
import time
import unicodedata
from difflib import SequenceMatcher


def normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def token_set(text: str) -> set[str]:
    n = normalize(text)
    return {t for t in n.split() if t}


def similarity(a: str, b: str) -> float:
    an = normalize(a)
    bn = normalize(b)
    if not an or not bn:
        return 0.0
    if an == bn:
        return 1.0
    return SequenceMatcher(None, an, bn).ratio()


class TTLCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        v = self._data.get(key)
        if not v:
            return None
        ts, value = v
        if time.time() - ts > self.ttl_seconds:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value):
        self._data[key] = (time.time(), value)
