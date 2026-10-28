"""Пути, настройки и хранилище аккаунтов лаунчера."""
import json
import os
import sys
import threading

APP_NAME = "ZeleesLauncher"
APP_VERSION = "1.1.1"

# Откуда лаунчер берёт обновления. Заполняется один раз (см. tools/publish_update.py):
# "https://github.com/<владелец>/<репозиторий>/releases/latest/download/update.json"
UPDATE_URL = "https://zelees.link/update/update.json"


def base_dir():
    """Папка, где лежит лаунчер (рядом с .exe, если собран через PyInstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """Исходники и портативный exe — папка data рядом; установленная версия — %APPDATA%\\ZeleesLauncher."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(exe_dir, "portable.txt")) or os.path.isdir(os.path.join(exe_dir, "data")):
            return os.path.join(exe_dir, "data")
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_NAME)
    return os.path.join(base_dir(), "data")


DATA_DIR = data_dir()
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
RUNTIME_DIR = os.path.join(DATA_DIR, "runtime")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
LOG_FILE = os.path.join(DATA_DIR, "launcher.log")

DEFAULT_SETTINGS = {
    "game_dir": os.path.join(DATA_DIR, "minecraft"),
    "memory_max": 4096,
    "memory_min": 1024,
    "java_mode": "auto",          # auto | custom
    "java_path": "",
    "jvm_args": "",
    "width": 925,
    "height": 530,
    "fullscreen": False,
    "separate_instances": False,  # отдельная папка игры для каждой версии
    "hide_on_launch": False,
    "discord_rpc": True,          # статус «Играет в Minecraft …» в профиле Discord
    "beta_updates": False,        # получать бета-версии лаунчера (update-beta.json)
    "crash_reports": None,        # отчёты об ошибках разработчику: None — ещё не спрашивали
    "show_release": True,
    "show_snapshot": False,
    "show_beta": False,
    "show_alpha": False,
    "show_installed_only": False,
    "ms_client_id": "",           # пусто = встроенный вход через login.live.com
    "curseforge_key": "",         # личный API-ключ CurseForge (необязательно)
    "download_threads": 16,
    "last_version": "",
    "last_account": "",
    "lan_servers": [],            # адреса, добавленные вручную на вкладке «Сервера → Локальная сеть»
}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        data = _read_json(SETTINGS_FILE, {})
        self.data = dict(DEFAULT_SETTINGS)
        if isinstance(data, dict):
            self.data.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})

    def get(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def save(self):
        with self._lock:
            _write_json(SETTINGS_FILE, self.data)


_DPAPI_ENTROPY = b"ZeleesLauncher accounts v1"


def _dpapi(data, protect):
    """Шифрование Windows DPAPI: расшифровать может только этот же пользователь Windows на этом ПК."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, _k1 = blob(data)
    ent, _k2 = blob(_DPAPI_ENTROPY)
    out = Blob()
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):  # UI_FORBIDDEN
        raise OSError("DPAPI: ошибка %d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _read_accounts():
    """(список аккаунтов, хранился ли файл в открытом виде)."""
    data = _read_json(ACCOUNTS_FILE, [])
    if isinstance(data, dict) and "dpapi" in data:
        import base64
        try:
            return json.loads(_dpapi(base64.b64decode(data["dpapi"]), False).decode("utf-8")), False
        except (OSError, ValueError, AttributeError):  # другой ПК/пользователь Windows — войти заново
            return [], False
    return data, True


class Accounts:
    """Список аккаунтов Microsoft. Хранится refresh-токен; на Windows файл зашифрован DPAPI."""

    def __init__(self):
        self._lock = threading.Lock()
        data, plain = _read_accounts()
        data = data if isinstance(data, list) else []
        # оффлайн-аккаунты из старых версий лаунчера больше не поддерживаются
        self.items = [a for a in data if isinstance(a, dict) and a.get("type") == "microsoft"]
        if len(self.items) != len(data) or (plain and data and os.name == "nt"):
            self.save()  # заодно шифруем файл, оставшийся от старых версий

    def save(self):
        with self._lock:
            data = self.items
            if os.name == "nt":
                import base64
                try:
                    raw = json.dumps(self.items, ensure_ascii=False).encode("utf-8")
                    data = {"dpapi": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
                except (OSError, AttributeError):
                    pass  # DPAPI недоступен — сохраняем как раньше
            _write_json(ACCOUNTS_FILE, data)

    def key(self, acc):
        return "%s:%s" % (acc.get("type"), acc.get("id"))

    def find(self, key):
        for a in self.items:
            if self.key(a) == key:
                return a
        return None

    def upsert(self, acc):
        k = self.key(acc)
        for i, a in enumerate(self.items):
            if self.key(a) == k:
                self.items[i] = acc
                break
        else:
            self.items.append(acc)
        self.save()
        return k

    def remove(self, key):
        self.items = [a for a in self.items if self.key(a) != key]
        self.save()


def ensure_dirs():
    for d in (DATA_DIR, RUNTIME_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)
