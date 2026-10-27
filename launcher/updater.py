"""Система обновлений.

Два вида обновлений, их описывает файл update.json (публикуется tools/publish_update.py):
  * package   — zip с кодом лаунчера (~1 МБ). Скачивается в %APPDATA%\\ZeleesLauncher\\app,
                при следующем запуске main.py подгружает код из него. Переустановка не нужна.
  * installer — полный установщик. Нужен, только если изменился сам exe (min_bootstrap).

Манифест подписан Ed25519 (поле signature). Секретный ключ есть только на ПК автора
(tools/update_key.py), здесь — открытый. Без верной подписи обновление не ставится, даже если
сервер, Cloudflare или DNS скомпрометированы: sha256 файлов берётся из подписанной части.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

from . import ed25519, net, premium  # noqa: F401 - premium закрепляет сертификат сайта, с которого идут обновления
from .config import APP_VERSION, DATA_DIR, UPDATE_URL

UPD_DIR = os.path.join(DATA_DIR, "app")
STATE_FILE = os.path.join(UPD_DIR, "state.json")

# Открытый ключ подписи обновлений (ставит tools/update_key.py genkey).
UPDATE_PUBKEY = "fd2c4a32182c06db33af27c2664ba83546d0dc90b25cab8bdc1d6ad8ee8dd70a"


def signed_bytes(manifest):
    """Что именно подписывается: манифест без поля signature в каноническом JSON."""
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_manifest(manifest):
    """Возвращает манифест, только если подпись верна. Иначе — исключение."""
    if not isinstance(manifest, dict):
        raise IOError("Неверный формат описания обновления")
    try:
        sig = bytes.fromhex(str(manifest.get("signature", "")))
        pub = bytes.fromhex(UPDATE_PUBKEY)
    except ValueError:
        sig = pub = b""
    if not pub or not ed25519.verify(pub, signed_bytes(manifest), sig):
        raise IOError("Обновление отклонено: неверная подпись")
    return manifest


def vtuple(v):
    out = []
    for p in str(v).strip().lstrip("v").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def newer(a, b):
    return vtuple(a) > vtuple(b)


def bootstrap_version():
    """Версия exe-«загрузчика» (задаётся в main.py). 0 — запуск из исходников."""
    try:
        return int(os.environ.get("ZELEES_BOOTSTRAP", "0"))
    except ValueError:
        return 0


def enabled():
    return bool(UPDATE_URL) and getattr(sys, "frozen", False)


def _mirrors(url):
    """Тот же файл на всех адресах сайта: сначала указанный, потом остальные (домен -> IP сервера)."""
    hosts = premium.API_HOSTS
    for i, base in enumerate(hosts):
        if url.startswith(base + "/"):
            return [b + url[len(base):] for b in hosts[i:] + hosts[:i]]
    return [url]


def check(url=None):
    """Возвращает манифест, если есть версия новее текущей, иначе None."""
    url = url or UPDATE_URL
    if not url:
        return None
    urls = _mirrors(url)
    for n, u in enumerate(urls):
        try:
            m = net.get_json(u, timeout=15 if n == len(urls) - 1 else 8)
            break
        except Exception:  # noqa: BLE001 - пробуем следующий адрес, у последнего ошибка уходит наружу
            if n == len(urls) - 1:
                raise
    verify_manifest(m)
    if m.get("version") and newer(m["version"], APP_VERSION):
        return m
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(info, dest, progress=None, label="Обновление"):
    if progress:
        progress(0, 1, "%s: загрузка..." % label)
    urls = _mirrors(info["url"])
    for n, u in enumerate(urls):
        try:
            net.download(u, dest, retries=4 if n == len(urls) - 1 else 2)
            break
        except Exception:  # noqa: BLE001
            if n == len(urls) - 1:
                raise
    if not info.get("sha256") or _sha256(dest).lower() != str(info["sha256"]).lower():
        os.remove(dest)
        raise IOError("Файл обновления повреждён (не совпала контрольная сумма)")
    if progress:
        progress(1, 1, "%s: загружено" % label)


def install(manifest, progress=None):
    """Скачивает обновление. Возвращает ('restart', None) или ('installer', путь_к_установщику)."""
    verify_manifest(manifest)
    ver = manifest["version"]
    pkg, inst = manifest.get("package"), manifest.get("installer")
    if pkg and bootstrap_version() >= int(manifest.get("min_bootstrap", 1)):
        os.makedirs(UPD_DIR, exist_ok=True)
        dest = os.path.join(UPD_DIR, "app-%s.zip" % ver)
        _fetch(pkg, dest, progress, "Обновление %s" % ver)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            if "launcher/__init__.py" not in names or "launcher/gui.py" not in names:
                raise IOError("В пакете обновления нет кода лаунчера")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": ver, "zip": dest, "min_bootstrap": int(manifest.get("min_bootstrap", 1))}, f)
        for fn in os.listdir(UPD_DIR):  # старые пакеты больше не нужны
            if fn.startswith("app-") and fn.endswith(".zip") and fn != os.path.basename(dest):
                try:
                    os.remove(os.path.join(UPD_DIR, fn))
                except OSError:
                    pass
        return "restart", None
    if inst:
        dest = os.path.join(tempfile.gettempdir(), "ZeleesLauncher-Setup-%s.exe" % ver)
        _fetch(inst, dest, progress, "Установщик %s" % ver)
        return "installer", dest
    raise RuntimeError("Для этой версии лаунчера обновление нужно скачать вручную")


DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def restart():
    """Запускает лаунчер заново (новый процесс) — вызывающий код должен сразу завершиться."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")]
    subprocess.Popen(cmd, close_fds=True, creationflags=DETACHED)


def run_installer(path):
    """Тихая установка поверх: установщик сам перезапустит лаунчер."""
    subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True, creationflags=DETACHED)
