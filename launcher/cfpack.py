"""Импорт модпаков CurseForge (.zip с manifest.json и папкой overrides).

В архиве нет самих модов — только их id (projectID/fileID). Файлы берутся у CurseForge API, поэтому нужен
личный API-ключ («Опции → CurseForge API-ключ»). Если автор мода запретил скачивание из сторонних
лаунчеров, файл не качается в обход — лаунчер перечислит его со ссылкой на страницу.
"""
import json
import zipfile

from . import mods, packs
from .mrpack import safe_path

MANIFEST = "manifest.json"
# modLoaders[].id в manifest.json ('forge-47.2.0') -> загрузчик лаунчера
LOADER_IDS = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}
# classId проекта CurseForge -> вид файла лаунчера
CLASS_KINDS = {mods.CF_CLASS[k]: k for k in ("mod", "resourcepack", "shader")}


class CfPackError(Exception):
    pass


def is_cfpack(path):
    """True, если это zip с manifest.json в корне."""
    try:
        with zipfile.ZipFile(path) as z:
            return MANIFEST in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _loader(mf):
    ls = [x for x in mf.get("modLoaders") or [] if isinstance(x, dict) and x.get("id")]
    if not ls:
        raise CfPackError("Модпак без загрузчика модов (Forge, NeoForge, Fabric, Quilt) не поддерживается")
    main = next((x for x in ls if x.get("primary")), ls[0])
    name, _, version = str(main["id"]).partition("-")
    if name.lower() not in LOADER_IDS or not version:
        raise CfPackError("Неизвестный загрузчик в модпаке: %s" % main["id"])
    return LOADER_IDS[name.lower()], version


def read(path):
    """Читает модпак CurseForge. Возвращает {"name", "version", "author", "mc", "loader", "loader_version",
    "files": [{"project", "file"}], "overrides_dir", "overrides": число файлов}."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise CfPackError("Это не модпак CurseForge (не zip-архив)")
    with z:
        try:
            mf = json.loads(z.read(MANIFEST).decode("utf-8-sig"))
        except KeyError:
            raise CfPackError("В архиве нет %s — это не модпак CurseForge. Если вы распаковали архив, "
                              "запакуйте его обратно так, чтобы %s лежал в корне." % (MANIFEST, MANIFEST))
        except ValueError:
            raise CfPackError("Файл %s повреждён" % MANIFEST)
        if not isinstance(mf, dict) or mf.get("manifestType") != "minecraftModpack":
            raise CfPackError("Неподдерживаемый формат модпака CurseForge")
        ov = safe_path(mf.get("overrides") or "overrides") or "overrides"
        overrides = sum(1 for n in z.namelist() if n.startswith(ov + "/") and not n.endswith("/"))
    mc = str((mf.get("minecraft") or {}).get("version") or "")
    if not mc:
        raise CfPackError("В модпаке не указана версия Minecraft")
    loader, loader_version = _loader(mf.get("minecraft") or {})
    files = []
    for f in mf.get("files") or []:
        try:
            pid, fid = int(f["projectID"]), int(f["fileID"])
        except (KeyError, TypeError, ValueError):
            raise CfPackError("Повреждённая запись файла в %s" % MANIFEST)
        if f.get("required", True) is False:
            continue  # необязательный файл, выключенный автором пака
        files.append({"project": str(pid), "file": str(fid)})
    return {"name": str(mf.get("name") or "Модпак").strip()[:40] or "Модпак",
            "version": str(mf.get("version") or ""), "author": str(mf.get("author") or ""),
            "mc": mc, "loader": loader, "loader_version": loader_version,
            "files": files, "overrides_dir": ov, "overrides": overrides}


def resolve(pack, cf_key):
    """Получает файлы у CurseForge. Возвращает (items, failed) в формате packs.install_files."""
    if not pack["files"]:
        return [], []
    if not cf_key:
        raise CfPackError("Для модпаков CurseForge нужен API-ключ CurseForge. Получите бесплатный ключ на "
                          "console.curseforge.com и вставьте его в «Опции → CurseForge API-ключ».")
    cf = mods.CurseForge(cf_key)
    files = cf.files([f["file"] for f in pack["files"]])
    projects = cf.projects({f["project"] for f in pack["files"]})
    items, failed = [], []
    for f in pack["files"]:
        pr = projects.get(f["project"]) or {}
        title = pr.get("title") or "CurseForge %s" % f["project"]
        mf = files.get(f["file"])
        if not mf or str(mf.get("project_id")) != f["project"]:
            failed.append((title, "файл больше не доступен на CurseForge"))
            continue
        fn = packs._safe_name(mf.get("filename"))
        kind = CLASS_KINDS.get(pr.get("class_id"))
        if kind is None and not pr and fn and fn.lower().endswith(".jar"):
            kind = "mod"  # проект не нашёлся, но это явно мод
        if kind is None:
            failed.append((title, "неподдерживаемый тип файла (не мод, ресурс-пак или шейдер)"))
            continue
        if not mf.get("url"):
            page = pr.get("page_url")
            failed.append((title, "автор запретил скачивание из сторонних лаунчеров"
                           + (" — скачайте вручную: %s" % page if page else "")))
            continue
        if not fn or not fn.lower().endswith(packs.KIND_EXT[kind][0]):
            failed.append((title, "неожиданный тип файла"))
            continue
        mf["filename"] = fn
        items.append({"kind": kind, "off": False, "mf": mf, "title": title, "icon_url": pr.get("icon_url", "")})
    return items, failed
