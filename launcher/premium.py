"""Premium и сервера игроков: связь с сайтом ZeleesLauncher.

Вход — как на обычном сервере Minecraft: лаунчер отправляет join в sessionserver Mojang, а сайт
проверяет его через hasJoined. Токен Minecraft сайту не передаётся. Соединение — HTTPS
с закреплённым корневым сертификатом сайта (подменить сервер нельзя).
"""
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import net
from .config import DATA_DIR, _read_json, _write_json

# Сначала домен (через Cloudflare, обычный сертификат), если недоступен — сам сервер по IP с закреплённым сертификатом.
API_HOSTS = ["https://zelees.link", "https://45.118.248.198"]
PINNED_HOST = "45.118.248.198"
JOIN_URL = "https://sessionserver.mojang.com/session/minecraft/join"
STATE_FILE = os.path.join(DATA_DIR, "premium.json")

# Корневой сертификат сайта (/root/zelees-ca/ca.crt на сервере). Доверяем только ему.
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBpzCCAU2gAwIBAgIUYZqAAxf0WByRIJ6JABjUd6pNX8QwCgYIKoZIzj0EAwIw
ITEfMB0GA1UEAwwWWmVsZWVzTGF1bmNoZXIgUm9vdCBDQTAeFw0yNjEwMjYwNTMx
MjVaFw00NjEwMjEwNTMxMjVaMCExHzAdBgNVBAMMFlplbGVlc0xhdW5jaGVyIFJv
b3QgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARy2ImxLijl8pnik/0JsD2a
/KWEj+4cpXJuIKDvfmg9oSEXq+7sihVBs9jsf9KrRVCebdVmBJI8E5CNj51VmBuM
o2MwYTAfBgNVHSMEGDAWgBR1UZQs9mbB3SkJchX1pQHJlB7f/TAPBgNVHRMBAf8E
BTADAQH/MA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUdVGULPZmwd0pCXIV9aUB
yZQe3/0wCgYIKoZIzj0EAwIDSAAwRQIgSys5NQL06jcRj4AvgGSA3RawmYBMg2Xg
U/o/C7gOW7QCIQCMZxw0qlUs9D0ppnwnWs7hGOwXbqUmDmkpueTFa4YuSQ==
-----END CERTIFICATE-----
"""

_CTX = None
net.pin_host(PINNED_HOST, CA_PEM)  # для обращений к серверу по IP (в том числе обновления лаунчера)


class ApiError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def _ctx(base):
    """Домен проверяется обычным набором сертификатов, IP-адрес сервера — только нашим корневым."""
    global _CTX
    if base.endswith(PINNED_HOST):
        if _CTX is None:
            _CTX = ssl.create_default_context(cadata=CA_PEM)
        return _CTX
    return net.ssl_context()


_FIRST = 0  # какой из адресов отвечал в прошлый раз — с него и начинаем


def _can_fallback(e):
    """Сети нет / Cloudflare не достучался до сервера — пробуем следующий адрес. Ошибки самого сайта — нет."""
    return e.status == 0 or e.status in (502, 503, 504) or 520 <= e.status <= 530


def _request(base, method, path, data, h, timeout):
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(base)) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            msg = None
        raise ApiError(msg or "Ошибка сайта (HTTP %s)" % e.code, e.code)
    except (urllib.error.URLError, OSError) as e:
        raise ApiError("Сайт ZeleesLauncher недоступен: %s" % getattr(e, "reason", e))


def call(method, path, body=None, token=None, timeout=15):
    global _FIRST
    h = {"User-Agent": net.USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    elif method == "POST":
        data = b""
    if token:
        h["Authorization"] = "Bearer " + token
    order = list(range(_FIRST, len(API_HOSTS))) + list(range(_FIRST))
    for n, i in enumerate(order):
        last = n == len(order) - 1
        try:
            raw = _request(API_HOSTS[i], method, path, data, h, timeout if last else min(timeout, 8))
        except ApiError as e:
            if last or not _can_fallback(e):
                raise
            continue
        _FIRST = i
        break
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        raise ApiError("Неверный ответ сайта")


class Premium:
    """Сессии сайта и кэш статуса Premium по UUID лицензионных аккаунтов (data/premium.json)."""

    def __init__(self):
        self._lock = threading.Lock()
        data = _read_json(STATE_FILE, {})
        self.state = data if isinstance(data, dict) else {}
        self.info = {}

    def _save(self):
        with self._lock:
            _write_json(STATE_FILE, self.state)

    def until(self, uuid):
        return int((self.state.get(uuid) or {}).get("premium_until") or 0)

    def is_premium(self, uuid):
        return self.until(uuid) > time.time()

    def forget(self, uuid):
        st = self.state.pop(uuid, None)
        self._save()
        if st and st.get("token"):
            try:
                call("POST", "/api/auth/logout", token=st["token"], timeout=5)
            except ApiError:
                pass

    # ---- вход
    def login(self, acc):
        sid = call("POST", "/api/auth/challenge")["server_id"]
        try:
            net.post_json(JOIN_URL, {"accessToken": acc["mc_token"], "selectedProfile": acc["id"],
                                     "serverId": sid}, timeout=15)
        except net.HttpError as e:
            raise ApiError("Mojang не подтвердил вход (HTTP %s). Попробуйте перезайти в аккаунт Microsoft."
                           % e.status, e.status)
        r = call("POST", "/api/auth/verify", {"name": acc["name"], "server_id": sid})
        self.state[acc["id"]] = {"token": r["token"], "expires": r["expires"], "premium_until": r["premium_until"],
                                 "name": r.get("name")}
        self._save()
        return r["token"]

    def authed(self, acc, method, path, body=None):
        """Запрос от имени аккаунта. Если сессии нет или она истекла — входит заново."""
        uuid = acc["id"]
        for attempt in range(2):
            st = self.state.get(uuid) or {}
            tok = st.get("token") if st.get("expires", 0) > time.time() + 60 else None
            if not tok:
                tok = self.login(acc)
            try:
                r = call(method, path, body, token=tok)
            except ApiError as e:
                if e.status == 401 and attempt == 0:
                    self.state.pop(uuid, None)
                    continue
                raise
            if "premium_until" in r:
                st = self.state.setdefault(uuid, {})
                st["premium_until"] = r["premium_until"]
                if "boosty" in r:
                    st["boosty"] = r["boosty"]
                if "ref" in r:
                    st["ref"] = r["ref"]
                self._save()
            return r
        raise ApiError("Не удалось войти на сайт")

    def me(self, acc):
        return self.authed(acc, "GET", "/api/me")

    # ---- оплата через Boosty
    def boosty(self, uuid):
        """Привязка Boosty из последнего ответа сайта: {"linked", "name", "active"}."""
        return (self.state.get(uuid) or {}).get("boosty") or {}

    def boosty_code(self, acc):
        """Код ZL-…, который игрок отправляет автору в личные сообщения на Boosty."""
        return self.authed(acc, "POST", "/api/boosty/code")

    # ---- партнёрская программа
    def ref(self, uuid):
        """Из последнего ответа сайта: {"invited_by", "can_enter", "percent"}."""
        return (self.state.get(uuid) or {}).get("ref") or {}

    def ref_code(self, acc, code):
        """Код партнёра (до первой покупки Premium): засчитывает приглашение партнёру."""
        return self.authed(acc, "POST", "/api/ref", {"code": code})

    # ---- время в игре
    def playtime(self, acc, sessions):
        """Сессии из playtime.Playtime.pending() — в личный кабинет."""
        return self.authed(acc, "POST", "/api/playtime", {"sessions": [
            {k: s[k] for k in ("version", "server", "seconds")} for s in sessions]})

    # ---- подарочный код
    def gift(self, acc, code):
        return self.authed(acc, "POST", "/api/gift", {"code": code})

    # ---- сервера
    def load_info(self):
        self.info = call("GET", "/api/info", timeout=10)
        return self.info

    def servers(self):
        return call("GET", "/api/servers", timeout=10).get("servers", [])

    def add_server(self, acc, fields):
        return self.authed(acc, "POST", "/api/servers", fields)

    def update_server(self, acc, sid, fields):
        return self.authed(acc, "POST", "/api/servers/%d" % int(sid), fields)

    def delete_server(self, acc, sid):
        return self.authed(acc, "DELETE", "/api/servers/%d" % int(sid))

    # ---- личный кабинет на сайте
    def web_link(self, acc, page="/account"):
        """Одноразовая ссылка (2 минуты), которая открывает личный кабинет в браузере без входа Microsoft."""
        return self.authed(acc, "POST", "/api/web/link", {"next": page})["url"]
