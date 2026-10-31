"""Отчёты об ошибках разработчику (только с согласия игрока).

В отчёт входят текст ошибки, версия лаунчера и Windows. Перед отправкой из текста убираются путь профиля,
имя пользователя Windows, папки лаунчера и всё, похожее на токены. Ник и аккаунт не отправляются.
Отправляются только неожиданные ошибки (ошибки в коде лаунчера), а не сбои сети или входа.
"""
import os
import platform
import re
import threading
import traceback

from .config import APP_VERSION, DATA_DIR, base_dir

CRASH_LOG = os.path.join(DATA_DIR, "crash.log")
MAX_PER_SESSION = 5
MAX_TRACE = 20000
_TOKEN_RE = re.compile(r"\b(?:eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]+|[A-Za-z0-9_\-]{40,})\b")
_FRAME_RE = re.compile(r'File "([^"]+)", line \d+, in (\S+)')


def _variants(path):
    path = os.path.normpath(path)
    return {path, path.replace("\\", "/"), path.replace("\\", "\\\\")}


def scrub(text, home=None, user=None, dirs=None):
    """Убирает из текста личное: пути (профиль, папки лаунчера), имя пользователя Windows, токены."""
    home = home if home is not None else os.path.expanduser("~")
    user = user if user is not None else (os.environ.get("USERNAME") or os.environ.get("USER") or "")
    dirs = dirs if dirs is not None else [(DATA_DIR, "<data>"), (base_dir(), "<launcher>")]
    for d, label in list(dirs) + [(home, "~")]:
        if d and len(d) > 3:
            for v in sorted(_variants(d), key=len, reverse=True):
                text = re.sub(re.escape(v), lambda m, lb=label: lb, text, flags=re.I)
    if len(user) >= 3:
        text = re.sub(r"(?<![\w])%s(?![\w])" % re.escape(user), "<user>", text, flags=re.I)
    return _TOKEN_RE.sub("<token>", text)


def is_expected(exc):
    """Ошибки, которые случаются не из-за кода лаунчера: сеть, вход, каталоги модов, отмена."""
    from . import auth, cfpack, mc_status, mods, mrpack, net, packs, premium
    return isinstance(exc, (OSError, net.HttpError, net.Cancelled, auth.AuthError, mods.ModsError, premium.ApiError,
                            mrpack.MrpackError, cfpack.CfPackError, packs.PackError, mc_status.PingError))


def signature(trace):
    """Одна и та же ошибка: файлы, функции и тип исключения (без номеров строк)."""
    frames = ["%s:%s" % (re.split(r"[\\/]", f)[-1], fn) for f, fn in _FRAME_RE.findall(trace)]
    last = next((ln for ln in reversed(trace.strip().splitlines()) if ln.strip()), "")
    return "|".join(frames) + "|" + last.split(":", 1)[0]


class Reporter:
    """settings["crash_reports"]: None — спросить при первой ошибке, True — отправлять, False — нет.
    ask(callback) показывает вопрос в окне и вызывает callback(True/False); send(payload) отправляет."""

    def __init__(self, settings, ask, send):
        self.settings = settings
        self.ask = ask
        self.send = send
        self.lock = threading.Lock()
        self.seen = set()
        self.sent = 0
        self.pending = []
        self.asking = False

    def exception(self, exc, kind="error"):
        if is_expected(exc):
            return
        self.report("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), kind)

    def report(self, trace, kind="error"):
        consent = self.settings.get("crash_reports")
        if consent is False:
            return
        trace = scrub(trace)[-MAX_TRACE:]
        with self.lock:
            sig = signature(trace)
            if sig in self.seen or self.sent >= MAX_PER_SESSION:
                return
            self.seen.add(sig)
            self.sent += 1
            payload = {"version": APP_VERSION, "kind": kind, "os": platform.platform()[:80], "trace": trace}
            if consent is True:
                threading.Thread(target=self._send, args=(payload,), daemon=True).start()
                return
            self.pending.append(payload)
            if self.asking:
                return
            self.asking = True
        self.ask(self._answered)

    def _answered(self, yes):
        self.settings.set("crash_reports", bool(yes))
        with self.lock:
            pending, self.pending, self.asking = self.pending, [], False
        if yes:
            for p in pending:
                threading.Thread(target=self._send, args=(p,), daemon=True).start()

    def _send(self, payload):
        try:
            self.send(payload)
        except Exception:  # noqa: BLE001 - отчёт не дошёл — не страшно, игроку об этом знать не нужно
            pass

    def check_startup_crash(self):
        """Падение при прошлом запуске (main.py пишет crash.log) — отправить один раз."""
        try:
            mtime = int(os.path.getmtime(CRASH_LOG))
            if mtime <= int(self.settings.get("crash_reported_at") or 0):
                return
            with open(CRASH_LOG, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return
        self.settings.set("crash_reported_at", mtime)
        if text.strip():
            self.report(text, "startup")
