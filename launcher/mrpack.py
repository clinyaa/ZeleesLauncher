"""Импорт и экспорт модпаков Modrinth (.mrpack): версия Minecraft, загрузчик, файлы по ссылкам и папка overrides.

Формат: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
Файлы скачиваются только с доменов, разрешённых форматом, и проверяются по SHA1. Пути внутри пака
не могут выйти за папку сборки.
"""
import hashlib
import json
import os
import posixpath
import shutil
import urllib.parse
import zipfile

from . import net

INDEX = "modrinth.index.json"
# зависимость в modrinth.index.json -> загрузчик лаунчера
LOADER_DEPS = {"fabric-loader": "fabric", "quilt-loader": "quilt", "forge": "forge", "neoforge": "neoforge"}
ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
OVERRIDES = ("overrides/", "client-overrides/")  # client-overrides накладываются поверх overrides
# что экспорт кладёт в пак: папка -> расширения файлов
EXPORT_DIRS = {"mods": (".jar",), "resourcepacks": (".zip",), "shaderpacks": (".zip",)}
CONFIG_DIRS = ("config", "defaultconfigs", "kubejs")  # настройки модов (по желанию)
MODRINTH_API = "https://api.modrinth.com/v2"


class MrpackError(Exception):
    pass


def safe_path(path):
    """Относительный путь внутри сборки ('mods/a.jar') или None, если путь выходит наружу."""
    p = str(path or "").replace("\\", "/")
    if not p or p.startswith("/") or ":" in p:
        return None
    parts = p.split("/")
    if any(x in ("", ".", "..") for x in parts):
        return None
    return posixpath.join(*parts)


def _allowed_url(url):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in ALLOWED_HOSTS


def read(path):
    """Читает .mrpack. Возвращает {"name", "version", "summary", "mc", "loader", "loader_version",
    "files": [{"path", "url", "sha1", "size"}], "skipped": [путь], "overrides": число файлов}."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise MrpackError("Это не файл .mrpack (не zip-архив)")
    with z:
        try:
            idx = json.loads(z.read(INDEX).decode("utf-8"))
        except KeyError:
            raise MrpackError("В архиве нет %s — это не модпак Modrinth" % INDEX)
        except ValueError:
            raise MrpackError("Файл %s повреждён" % INDEX)
        overrides = sum(1 for n in z.namelist() if n.startswith(OVERRIDES) and not n.endswith("/"))
    if not isinstance(idx, dict) or idx.get("formatVersion") != 1 or idx.get("game") != "minecraft":
        raise MrpackError("Неподдерживаемая версия формата .mrpack")
    deps = idx.get("dependencies") or {}
    mc = deps.get("minecraft")
    if not mc:
        raise MrpackError("В модпаке не указана версия Minecraft")
    found = [(LOADER_DEPS[k], str(v)) for k, v in deps.items() if k in LOADER_DEPS]
    if not found:
        raise MrpackError("Модпак без загрузчика модов (Fabric, Quilt, Forge, NeoForge) не поддерживается")
    if len(found) > 1:
        raise MrpackError("В модпаке указано несколько загрузчиков сразу")
    loader, loader_version = found[0]

    files, skipped = [], []
    for f in idx.get("files") or []:
        rel = safe_path(f.get("path"))
        if not rel:
            raise MrpackError("Недопустимый путь файла в модпаке: %s" % f.get("path"))
        if (f.get("env") or {}).get("client") == "unsupported":
            continue  # файл только для сервера
        sha1 = (f.get("hashes") or {}).get("sha1")
        url = next((u for u in f.get("downloads") or [] if _allowed_url(u)), None)
        if not url or not sha1:
            skipped.append(rel)
            continue
        files.append({"path": rel, "url": url, "sha1": sha1.lower(), "size": f.get("fileSize")})
    return {"name": str(idx.get("name") or "Модпак").strip()[:40] or "Модпак",
            "version": str(idx.get("versionId") or ""), "summary": str(idx.get("summary") or ""),
            "mc": str(mc), "loader": loader, "loader_version": loader_version,
            "files": files, "skipped": skipped, "overrides": overrides}


def extract_overrides(path, game_dir):
    """Копирует overrides/ и client-overrides/ в папку сборки. Возвращает число файлов."""
    root = os.path.normpath(game_dir)
    n = 0
    with zipfile.ZipFile(path) as z:
        for prefix in OVERRIDES:
            for info in z.infolist():
                name = info.filename
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                rel = safe_path(name[len(prefix):])
                if not rel:
                    continue
                target = os.path.normpath(os.path.join(root, rel))
                if not target.startswith(root + os.sep):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                n += 1
    return n


def install_files(pack, game_dir, threads=16, progress=None, cancel=None):
    """Скачивает файлы модпака в папку сборки с проверкой SHA1."""
    tasks = [net.Task(f["url"], os.path.join(game_dir, *f["path"].split("/")), f["sha1"], f.get("size"))
             for f in pack["files"]]
    net.download_many(tasks, threads, progress, cancel, label="Файлы модпака")


# ----------------------------------------------------------------- экспорт
def _hashes(path):
    h1, h512 = hashlib.sha1(), hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h1.update(chunk)
            h512.update(chunk)
    return h1.hexdigest(), h512.hexdigest()


def scan(game_dir):
    """Файлы сборки для экспорта. Возвращает (files, disabled):
    files — [{"path": 'mods/a.jar', "abs", "sha1", "sha512", "size"}], disabled — имена отключённых модов."""
    files, disabled = [], []
    for d, exts in EXPORT_DIRS.items():
        folder = os.path.join(game_dir, d)
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder), key=str.lower):
            p = os.path.join(folder, fn)
            if not os.path.isfile(p):
                continue
            if fn.lower().endswith(".jar.disabled") and d == "mods":
                disabled.append(fn)
                continue
            if not fn.lower().endswith(exts):
                continue
            sha1, sha512 = _hashes(p)
            files.append({"path": "%s/%s" % (d, fn), "abs": p, "sha1": sha1, "sha512": sha512,
                          "size": os.path.getsize(p)})
    return files, disabled


def identify(files):
    """Ищет файлы на Modrinth по SHA1. Возвращает sha1 -> ссылка на cdn.modrinth.com."""
    sha1s = sorted({f["sha1"] for f in files})
    found = {}
    for i in range(0, len(sha1s), 500):
        r = net.post_json(MODRINTH_API + "/version_files", {"hashes": sha1s[i:i + 500], "algorithm": "sha1"})
        for h, v in r.items():
            for vf in v.get("files") or []:
                if (vf.get("hashes") or {}).get("sha1") == h and _allowed_url(vf.get("url") or ""):
                    found[h] = vf["url"]
    return found


def build_index(name, version, summary, mc, loader, loader_version, files, urls):
    """modrinth.index.json для файлов, найденных на Modrinth (urls: sha1 -> ссылка)."""
    dep = {v: k for k, v in LOADER_DEPS.items()}[loader]
    idx = {"formatVersion": 1, "game": "minecraft", "versionId": version or "1.0.0", "name": name,
           "files": [{"path": f["path"], "hashes": {"sha1": f["sha1"], "sha512": f["sha512"]},
                      "downloads": [urls[f["sha1"]]], "fileSize": f["size"]}
                     for f in files if f["sha1"] in urls],
           "dependencies": {"minecraft": mc, dep: loader_version}}
    if summary:
        idx["summary"] = summary
    return idx


def config_files(game_dir):
    """Файлы настроек модов (папки CONFIG_DIRS): [(путь в паке, путь на диске)]."""
    res = []
    for d in CONFIG_DIRS:
        top = os.path.join(game_dir, d)
        for dirpath, _dirs, fns in os.walk(top):
            for fn in fns:
                p = os.path.join(dirpath, fn)
                res.append((os.path.relpath(p, game_dir).replace(os.sep, "/"), p))
    return sorted(res)


def write(out_path, index, embed):
    """Пишет .mrpack: индекс и файлы overrides (embed: [(путь в паке, путь на диске)])."""
    tmp = out_path + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(INDEX, json.dumps(index, ensure_ascii=False, indent=1))
            for rel, src in embed:
                z.write(src, "overrides/" + rel)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
