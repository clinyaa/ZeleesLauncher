"""Время в игре по версиям (сборкам) и серверам: data/playtime.json.

После каждой игры сессия добавляется к итогам и в очередь на сайт (личный кабинет, видит только игрок).
Если сайт недоступен, сессии ждут следующей игры или запуска лаунчера.
"""
import os
import threading
import time

from .config import DATA_DIR, _read_json, _write_json

PLAYTIME_FILE = os.path.join(DATA_DIR, "playtime.json")
MIN_SESSION = 30          # короче — скорее всего вылет при запуске, не считаем
MAX_SESSION = 24 * 3600
MAX_PENDING = 200


def fmt_hours(sec):
    if sec < 3600:
        return "%d мин" % max(1, sec // 60)
    h = round(sec / 3600.0, 1)
    if h >= 10 or h == int(h):
        return "%d ч" % round(h)
    return ("%.1f ч" % h).replace(".", ",")


class Playtime:
    def __init__(self, path=PLAYTIME_FILE):
        self.path = path
        self._lock = threading.Lock()
        data = _read_json(path, {})
        self.data = data if isinstance(data, dict) else {}
        for k in ("versions", "servers"):
            if not isinstance(self.data.get(k), dict):
                self.data[k] = {}
        if not isinstance(self.data.get("pending"), list):
            self.data["pending"] = []
        self.current = None

    def _save(self):
        try:
            _write_json(self.path, self.data)
        except OSError:
            pass

    def start(self, version, server=None, account=None):
        """server — (host, port) или None."""
        srv = ""
        if server:
            host, port = server
            srv = host + (":%d" % port if port and port != 25565 else "")
        self.current = {"version": version or "", "server": srv, "account": account or "", "start": time.time()}

    def stop(self):
        """Игра закрылась: -> сессия {"version", "server", "seconds", "account"} или None (слишком короткая)."""
        cur, self.current = self.current, None
        if not cur:
            return None
        sec = int(time.time() - cur["start"])
        if sec < MIN_SESSION:
            return None
        sec = min(sec, MAX_SESSION)
        sess = {"version": cur["version"], "server": cur["server"], "seconds": sec, "account": cur["account"],
                "end": int(time.time())}
        with self._lock:
            for kind, key in (("versions", cur["version"]), ("servers", cur["server"])):
                if key:
                    t = self.data[kind].setdefault(key, {"seconds": 0, "sessions": 0, "last": 0})
                    t["seconds"] += sec
                    t["sessions"] += 1
                    t["last"] = sess["end"]
            if cur["account"]:
                self.data["pending"] = (self.data["pending"] + [sess])[-MAX_PENDING:]
            self._save()
        return sess

    def seconds(self, kind, key):
        return int(((self.data.get(kind) or {}).get(key) or {}).get("seconds") or 0)

    def pending(self, account):
        with self._lock:
            return [dict(s) for s in self.data["pending"] if s.get("account") == account][:20]

    def sent(self, sessions):
        ends = {(s["account"], s["end"], s["seconds"]) for s in sessions}
        with self._lock:
            self.data["pending"] = [s for s in self.data["pending"]
                                    if (s.get("account"), s.get("end"), s.get("seconds")) not in ends]
            self._save()
