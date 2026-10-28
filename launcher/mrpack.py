"""Импорт модпаков Modrinth (.mrpack): версия Minecraft, загрузчик, файлы по ссылкам и папка overrides.

Формат: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
Файлы скачиваются только с доменов, разрешённых форматом, и проверяются по SHA1. Пути внутри пака
не могут выйти за папку сборки.
"""
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
