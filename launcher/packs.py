"""Коды сборок: поделиться сборкой (версия, загрузчик, моды, ресурс-паки, шейдеры) и поставить сборку по коду.

В коде хранятся только id проектов и версий Modrinth/CurseForge — сами файлы, их адреса и хэши лаунчер
получает у Modrinth/CurseForge и проверяет при скачивании. Код выдаёт сайт ZeleesLauncher (premium.call).
"""
import json
import os
import re
import time

from . import loaders, mods, net, premium
from .versions import local_versions, resolve_version, version_json_path

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
# папка в каталоге игры для каждого вида файлов
KIND_DIRS = {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks"}
KIND_EXT = {"mod": (".jar", ".jar.disabled"), "resourcepack": (".zip",), "shader": (".zip",)}
_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


class PackError(Exception):
    pass


def parse_code(text):
    """«K7QF-M2XA», «k7qf m2xa» или ссылка zelees.link/p/K7QF-M2XA -> «K7QF-M2XA» (или None)."""
    text = (text or "").strip()
    m = re.search(r"/p/([0-9A-Za-z-]+)", text)
    if m:
        text = m.group(1)
    s = re.sub(r"[^0-9A-Za-z]", "", text).upper()
    if len(s) != 8 or any(c not in CODE_ALPHABET for c in s):
        return None
    return s[:4] + "-" + s[4:]


def fetch(code):
    """Сборка по коду: {"code", "name", "author", "created", "uses", "pack": {...}}."""
    try:
        return premium.call("GET", "/api/packs/" + code.replace("-", ""), timeout=15)
    except premium.ApiError as e:
        raise PackError(str(e))


def publish(prem, acc, pack):
    """Отправляет сборку на сайт, возвращает {"code", "url"}."""
    return prem.authed(acc, "POST", "/api/packs", pack)


# ----------------------------------------------------------------- сборка -> код
def build_info(root, vid, manifest):
    """(loader, версия загрузчика, версия Minecraft, название сборки) для сборки vid."""
    vd = resolve_version(root, vid, manifest)
    ld, lv, mc = loaders.detect(vd)
    if not ld or not lv:
        raise PackError("Поделиться можно только сборкой с загрузчиком модов (Fabric, Quilt, Forge, NeoForge)")
    return ld, lv, mc, vd.get("zeleesName") or ""


def collect(game_dir):
    """Файлы сборки, которые можно передать кодом. Возвращает (files, skipped):
    files — [{"kind", "source", "project", "version", "off"?, "title"}], skipped — имена файлов без источника."""
    files, unknown = [], []  # unknown: (kind, имя файла, путь, отключён)
    folder = mods.ModFolder(os.path.join(game_dir, "mods"))
    for e in folder.entries():
        m = e["meta"]
        if m.get("source") in ("modrinth", "curseforge") and m.get("project_id") and m.get("version_id"):
            files.append({"kind": "mod", "source": m["source"], "project": str(m["project_id"]),
                          "version": str(m["version_id"]), "off": not e["enabled"], "title": m.get("title") or e["base"]})
        else:
            unknown.append(("mod", e["file"], e["path"], not e["enabled"]))
    for kind in ("resourcepack", "shader"):
        d = os.path.join(game_dir, KIND_DIRS[kind])
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d), key=str.lower):
                p = os.path.join(d, fn)
                if fn.lower().endswith(".zip") and os.path.isfile(p):
                    unknown.append((kind, fn, p, False))

    # файлы, скачанные не через лаунчер, ищем на Modrinth по хэшу
    sha = {}
    for kind, fn, path, off in unknown:
        try:
            sha[fn, kind] = net.sha1_file(path)
        except OSError:
            pass
    ident = {}
    if sha:
        try:
            ident = mods.Modrinth().identify(set(sha.values()))
        except Exception:  # noqa: BLE001
            ident = {}
    skipped = []
    for kind, fn, _path, off in unknown:
        v = ident.get(sha.get((fn, kind)))
        if v:
            files.append({"kind": kind, "source": "modrinth", "project": v["project_id"], "version": v["version_id"],
                          "off": off, "title": fn})
        elif kind == "mod" or fn.lower().endswith(".zip"):
            skipped.append(fn)
    return files, skipped


def make_pack(name, mc, loader, loader_version, files):
    out = []
    for f in files:
        item = {k: f[k] for k in ("kind", "source", "project", "version")}
        if f.get("off"):
            item["off"] = True
        out.append(item)
    return {"name": name.strip()[:40], "mc": mc, "loader": loader, "loader_version": loader_version, "files": out}


# ----------------------------------------------------------------- код -> сборка
def find_loader_version(root, manifest, loader, lv, mc):
    """Уже установленная версия с этим загрузчиком (не сборка по коду) или None."""
    for v in local_versions(root):
        if not v.get("inheritsFrom") or v.get("pack"):
            continue
        try:
            vd = resolve_version(root, v["id"], manifest)
        except Exception:  # noqa: BLE001
            continue
        if loaders.detect(vd) == (loader, lv, mc):
            return v["id"]
    return None


def slug(name):
    s = "".join(_TRANSLIT.get(c, c) for c in name.lower())
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-.")[:32]
    return s or "sborka"


def create_pack_version(root, manifest, name, code, parent):
    """Своя версия-сборка поверх версии с загрузчиком: у неё своя папка игры (instances/<id>)."""
    base = slug(name)
    taken = {v["id"].lower() for v in local_versions(root)} | {v.lower() for v in manifest.by_id}
    vid, n = base, 2
    while vid.lower() in taken or os.path.exists(os.path.join(root, "instances", vid)):
        vid, n = "%s-%d" % (base, n), n + 1
    t = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    data = {"id": vid, "inheritsFrom": parent, "type": "release", "time": t, "releaseTime": t,
            "zeleesName": name, "zeleesPack": code}
    path = version_json_path(root, vid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return vid


def _safe_name(fn):
    fn = os.path.basename(str(fn or "").replace("\\", "/"))
    if not fn or fn.startswith(".") or fn in ("..",):
        return None
    return fn


def resolve_files(pack, cf_key):
    """Получает у Modrinth/CurseForge настоящие файлы. Возвращает (items, failed):
    items — [{"kind", "off", "mf"}], failed — [(что, почему)]."""
    files = pack.get("files") or []
    mr_ids = [f["version"] for f in files if f["source"] == "modrinth"]
    cf_ids = [f["version"] for f in files if f["source"] == "curseforge"]
    mr = mods.Modrinth().versions(mr_ids) if mr_ids else {}
    cf, cf_err = {}, None
    if cf_ids:
        if not cf_key:
            cf_err = "нужен API-ключ CurseForge («Опции»)"
        else:
            try:
                cf = mods.CurseForge(cf_key).files(cf_ids)
            except Exception as e:  # noqa: BLE001
                cf_err = str(e)
    titles = {}
    pids = {f["project"] for f in files if f["source"] == "modrinth"}
    if pids:
        try:
            pr = mods.Modrinth().projects(pids)
            titles = {k: (p.get("title"), p.get("icon_url") or "") for k, p in pr.items()}
        except Exception:  # noqa: BLE001
            pass
    items, failed = [], []
    for f in files:
        title = titles.get(f["project"], (None, ""))[0] or "%s %s" % (f["source"], f["project"])
        mf = (mr if f["source"] == "modrinth" else cf).get(f["version"])
        if f["source"] == "curseforge" and cf_err:
            failed.append((title, cf_err))
            continue
        if not mf or str(mf.get("project_id")) != f["project"]:
            failed.append((title, "файл больше не доступен на %s" % ("Modrinth" if f["source"] == "modrinth"
                                                                       else "CurseForge")))
            continue
        if not mf.get("url"):
            failed.append((title, "автор запретил скачивание из сторонних лаунчеров"))
            continue
        fn = _safe_name(mf.get("filename"))
        if not fn or not fn.lower().endswith(KIND_EXT[f["kind"]][0]):
            failed.append((title, "неожиданный тип файла"))
            continue
        mf["filename"] = fn
        items.append({"kind": f["kind"], "off": bool(f.get("off")), "mf": mf, "title": title,
                      "icon_url": titles.get(f["project"], (None, ""))[1]})
    return items, failed


def install_files(game_dir, items, threads=16, progress=None, cancel=None):
    """Скачивает файлы сборки в папку игры (с проверкой SHA1) и записывает индекс модов."""
    tasks = []
    for it in items:
        d = os.path.join(game_dir, KIND_DIRS[it["kind"]])
        os.makedirs(d, exist_ok=True)
        mf = it["mf"]
        tasks.append(net.Task(mf["url"], os.path.join(d, mf["filename"]), mf.get("sha1"), mf.get("size")))
    net.download_many(tasks, threads, progress, cancel, label="Файлы сборки")
    folder = mods.ModFolder(os.path.join(game_dir, "mods"))
    for it in items:
        mf = it["mf"]
        if it["kind"] != "mod":
            continue
        folder.index[mf["filename"]] = {
            "source": mf["source"], "project_id": str(mf["project_id"]), "version_id": str(mf["version_id"]),
            "version_number": mf.get("version_number", ""), "title": it["title"], "icon_url": it.get("icon_url", ""),
            "game_versions": mf.get("game_versions", []), "loaders": mf.get("loaders", []),
        }
        if it["off"]:
            src = os.path.join(folder.path, mf["filename"])
            os.replace(src, src + ".disabled")
    folder.save_index()
