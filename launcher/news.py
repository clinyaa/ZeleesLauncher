"""Новости для главного экрана: пишутся в админке сайта, лаунчер берёт последние через /api/news."""
import os
import time

from . import premium
from .config import DATA_DIR, _read_json, _write_json

CACHE = os.path.join(DATA_DIR, "cache", "news.json")


def _clean(items):
    """Только записи нужного вида: сайт может быть старым или ответить чем-то не тем."""
    out = []
    for n in items if isinstance(items, list) else []:
        if isinstance(n, dict) and isinstance(n.get("id"), int) and n.get("title"):
            url = str(n.get("url") or "")
            out.append({"id": n["id"], "at": int(n.get("at") or 0), "title": str(n["title"])[:80],
                        "body": str(n.get("body") or "")[:600], "url": url if url.startswith("https://") else ""})
    return sorted(out, key=lambda n: n["id"], reverse=True)


def cached():
    return _clean(_read_json(CACHE, []))


def fetch():
    """Свежие новости с сайта (и в кэш). При ошибке сети — исключение."""
    items = _clean(premium.call("GET", "/api/news", timeout=10).get("news"))
    _write_json(CACHE, items)
    return items


def unread(items, seen_id):
    return [n for n in items if n["id"] > int(seen_id or 0)]


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else ""
