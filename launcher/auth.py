"""Аккаунты Microsoft (Xbox Live -> XSTS -> Minecraft Services). Играть можно только с лицензией."""
import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse

from . import net

# ---------------------------------------------------------------- Microsoft

# Собственное приложение ZeleesLauncher (Azure), одобренное Mojang для Minecraft API.
# Вход через него полностью автоматический: браузер -> localhost.
AZURE_CLIENT_ID = "1d70f811-138a-4f5d-ba8b-6e39114563ac"

# Запасной вариант (вход по ссылке): публичный client_id официального лаунчера Minecraft на login.live.com.
# Старые аккаунты, вошедшие этим способом, продолжают обновляться через него.
LIVE_CLIENT_ID = "00000000402b5328"
LIVE_REDIRECT = "https://login.live.com/oauth20_desktop.srf"
LIVE_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
LIVE_AUTH = "https://login.live.com/oauth20_authorize.srf"
LIVE_TOKEN = "https://login.live.com/oauth20_token.srf"

# Вход через приложение Azure (встроенное или своё из Настройки -> Microsoft Client ID).
AZ_AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
AZ_TOKEN = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
AZ_SCOPE = "XboxLive.signin offline_access"

XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENT_URL = "https://api.minecraftservices.com/entitlements/mcstore"

XSTS_ERRORS = {
    2148916233: "У этого аккаунта Microsoft нет профиля Xbox. Зайдите на xbox.com, создайте профиль и повторите.",
    2148916235: "Xbox Live недоступен в вашей стране/регионе.",
    2148916236: "Аккаунту нужна верификация возраста (Южная Корея).",
    2148916237: "Аккаунту нужна верификация возраста (Южная Корея).",
    2148916238: "Это детский аккаунт: его нужно добавить в семью Microsoft взрослым.",
}


class AuthError(Exception):
    pass


def live_auth_url():
    return LIVE_AUTH + "?" + urllib.parse.urlencode({
        "client_id": LIVE_CLIENT_ID,
        "response_type": "code",
        "scope": LIVE_SCOPE,
        "redirect_uri": LIVE_REDIRECT,
    })


def extract_code(text):
    """Достаёт code из вставленного URL (или принимает сам код)."""
    text = (text or "").strip()
    if not text:
        raise AuthError("Пустая ссылка")
    if "://" in text or "?" in text:
        q = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        params = urllib.parse.parse_qs(q)
        if "error" in params:
            raise AuthError("Microsoft вернул ошибку: %s" % (params.get("error_description") or params["error"])[0])
        if "code" in params:
            return params["code"][0]
        raise AuthError("В ссылке нет параметра code")
    return text


def live_exchange_code(code):
    return _token_call(LIVE_TOKEN, {
        "client_id": LIVE_CLIENT_ID, "code": code, "grant_type": "authorization_code",
        "redirect_uri": LIVE_REDIRECT, "scope": LIVE_SCOPE,
    })


def live_refresh(refresh_token):
    return _token_call(LIVE_TOKEN, {
        "client_id": LIVE_CLIENT_ID, "refresh_token": refresh_token, "grant_type": "refresh_token",
        "redirect_uri": LIVE_REDIRECT, "scope": LIVE_SCOPE,
    })


def _token_call(url, fields):
    try:
        r = net.post_form(url, fields)
    except net.HttpError as e:
        j = e.json()
        raise AuthError("Ошибка входа Microsoft: %s" % (j.get("error_description") or j.get("error") or e.text()[:300]))
    if "access_token" not in r:
        raise AuthError("Microsoft не вернул access_token")
    return r


# ---- свой Azure client id: полностью автоматический вход через localhost ----

class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            type(self).result = q
            body = ("<html><meta charset='utf-8'><body style='font-family:sans-serif;background:#1e1f22;color:#eee;"
                    "text-align:center;padding-top:80px'><h2>Готово!</h2><p>Можно закрыть вкладку и вернуться "
                    "в лаунчер.</p></body></html>").encode("utf-8")
        else:
            body = b"waiting"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class AzureLogin:
    def __init__(self, client_id):
        self.client_id = client_id.strip()
        self.verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        handler = type("Catcher", (_CodeCatcher,), {"result": None})
        self.handler = handler
        self.server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.redirect = "http://localhost:%d" % self.port
        self.state = secrets.token_urlsafe(16)
        self.url = AZ_AUTH + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect,
            "scope": AZ_SCOPE, "state": self.state, "prompt": "select_account",
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        self._stop = False

    def wait(self, timeout=300, cancel=None):
        self.server.timeout = 1
        end = time.time() + timeout
        try:
            while time.time() < end and not self._stop and not (cancel and cancel.is_set()):
                self.server.handle_request()
                q = self.handler.result
                if q:
                    if "error" in q:
                        raise AuthError((q.get("error_description") or q["error"])[0])
                    if q.get("state", [""])[0] != self.state:
                        raise AuthError("Неверный state в ответе")
                    tok = _token_call(AZ_TOKEN, {
                        "client_id": self.client_id, "code": q["code"][0], "grant_type": "authorization_code",
                        "redirect_uri": self.redirect, "scope": AZ_SCOPE, "code_verifier": self.verifier,
                    })
                    return tok
        finally:
            self.server.server_close()
        raise AuthError("Время ожидания входа истекло")

    def stop(self):
        self._stop = True


def azure_refresh(client_id, refresh_token):
    return _token_call(AZ_TOKEN, {
        "client_id": client_id, "refresh_token": refresh_token, "grant_type": "refresh_token", "scope": AZ_SCOPE,
    })


# ---- Xbox Live / Minecraft ----

def _xbl(ms_token, mode):
    prefixes = ["d="] if mode == "azure" else ["", "d=", "t="]
    last = None
    for p in prefixes:
        try:
            r = net.post_json(XBL_URL, {
                "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": p + ms_token},
                "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
            }, headers={"x-xbl-contract-version": "1"})
            return r["Token"], r["DisplayClaims"]["xui"][0]["uhs"]
        except net.HttpError as e:
            last = e
    raise AuthError("Xbox Live отклонил вход (HTTP %s)" % (last.status if last else "?"))


def _xsts(xbl_token):
    try:
        r = net.post_json(XSTS_URL, {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT",
        })
    except net.HttpError as e:
        xerr = e.json().get("XErr")
        if xerr in XSTS_ERRORS:
            raise AuthError(XSTS_ERRORS[xerr])
        raise AuthError("XSTS ошибка (HTTP %s, XErr %s)" % (e.status, xerr))
    return r["Token"], r["DisplayClaims"]["xui"][0]["uhs"]


def minecraft_login(ms_access_token, mode):
    xbl_token, _uhs = _xbl(ms_access_token, mode)
    xsts_token, uhs = _xsts(xbl_token)
    try:
        r = net.post_json(MC_LOGIN_URL, {"identityToken": "XBL3.0 x=%s;%s" % (uhs, xsts_token)})
    except net.HttpError as e:
        if e.status == 403:
            raise AuthError("Minecraft Services отклонил вход (403). Если используете свой Azure Client ID — "
                            "его нужно одобрить у Mojang; либо очистите поле Client ID в настройках.")
        raise AuthError("Ошибка входа в Minecraft (HTTP %s): %s" % (e.status, e.text()[:200]))
    mc_token = r["access_token"]
    expires = time.time() + int(r.get("expires_in", 86400))
    try:
        _, body = net.request(MC_PROFILE_URL, headers={"Authorization": "Bearer " + mc_token})
        import json
        prof = json.loads(body.decode("utf-8"))
    except net.HttpError as e:
        if e.status == 404:
            raise AuthError("На этом аккаунте Microsoft нет Minecraft: Java Edition "
                            "(или не создан ник на minecraft.net).")
        raise AuthError("Не удалось получить профиль Minecraft (HTTP %s)" % e.status)
    return {
        "type": "microsoft",
        "id": prof["id"],
        "name": prof["name"],
        "mc_token": mc_token,
        "mc_expires": expires,
        "xuid": "0",
    }


def complete_login(ms_tok, mode, client_id=None):
    acc = minecraft_login(ms_tok["access_token"], mode)
    acc["ms_refresh"] = ms_tok.get("refresh_token", "")
    acc["ms_mode"] = mode
    if client_id:
        acc["client_id"] = client_id
    return acc


def refresh_account(acc):
    """Обновляет токен Minecraft, если он истёк. Возвращает обновлённый аккаунт."""
    if acc.get("type") != "microsoft":
        return acc
    if acc.get("mc_token") and acc.get("mc_expires", 0) - 600 > time.time():
        return acc
    rt = acc.get("ms_refresh")
    if not rt:
        raise AuthError("Сессия истекла — войдите в аккаунт Microsoft заново.")
    if acc.get("ms_mode") == "azure":
        tok = azure_refresh(acc.get("client_id", ""), rt)
    else:
        tok = live_refresh(rt)
    new = complete_login(tok, acc.get("ms_mode", "live"), acc.get("client_id"))
    if not new.get("ms_refresh"):
        new["ms_refresh"] = rt
    return new
