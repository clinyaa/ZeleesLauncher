"""Список версий Minecraft и чтение/слияние version.json (включая inheritsFrom для Fabric/Forge)."""
import copy
import json
import os

from . import net
from .config import CACHE_DIR

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_CACHE = os.path.join(CACHE_DIR, "version_manifest_v2.json")

TYPE_NAMES = {
    "release": "Релиз",
    "snapshot": "Снапшот",
    "old_beta": "Beta",
    "old_alpha": "Alpha",
}


class Manifest:
    def __init__(self, data, offline=False):
        self.data = data or {"latest": {}, "versions": []}
        self.offline = offline
        self.by_id = {v["id"]: v for v in self.data.get("versions", [])}

    @property
    def versions(self):
        return self.data.get("versions", [])

    @property
    def latest(self):
        return self.data.get("latest", {})


def fetch_manifest():
    """Скачивает манифест; если сети нет — берёт кэш."""
    try:
        data = net.get_json(MANIFEST_URL, timeout=20)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(MANIFEST_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return Manifest(data)
    except Exception:  # noqa: BLE001
        try:
            with open(MANIFEST_CACHE, "r", encoding="utf-8") as f:
                return Manifest(json.load(f), offline=True)
        except (OSError, ValueError):
            return Manifest(None, offline=True)


def version_dir(root, vid):
    return os.path.join(root, "versions", vid)


def version_json_path(root, vid):
    return os.path.join(version_dir(root, vid), vid + ".json")


def version_jar_path(root, vid):
    return os.path.join(version_dir(root, vid), vid + ".jar")


def local_versions(root):
    """Версии, у которых есть json в папке versions (в т.ч. Fabric/Forge, поставленные установщиками)."""
    res = []
    vroot = os.path.join(root, "versions")
    if not os.path.isdir(vroot):
        return res
    for name in os.listdir(vroot):
        p = version_json_path(root, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                res.append({
                    "id": d.get("id", name),
                    "type": d.get("type", "release"),
                    "releaseTime": d.get("releaseTime", ""),
                    "inheritsFrom": d.get("inheritsFrom"),
                    "name": d.get("zeleesName"),  # сборка, поставленная по коду
                    "pack": d.get("zeleesPack"),
                })
            except (OSError, ValueError):
                pass
    return res


def is_installed(root, vid):
    jp = version_json_path(root, vid)
    if not os.path.isfile(jp):
        return False
    try:
        with open(jp, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    if d.get("inheritsFrom"):
        return is_installed(root, d["inheritsFrom"])
    jar = d.get("jar") or vid
    return os.path.isfile(version_jar_path(root, jar)) and os.path.isfile(
        os.path.join(version_dir(root, vid), ".installed"))


def load_version_json(root, vid, manifest, refresh=False):
    path = version_json_path(root, vid)
    entry = manifest.by_id.get(vid) if manifest else None
    if entry and (refresh or not net.is_valid(path, entry.get("sha1"))):
        try:
            net.download(entry["url"], path, entry.get("sha1"))
        except Exception:  # noqa: BLE001
            if not os.path.isfile(path):
                raise
    if not os.path.isfile(path):
        raise FileNotFoundError("Версия %s не найдена ни локально, ни в манифесте Mojang" % vid)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge(parent, child):
    res = copy.deepcopy(parent)
    for k, v in child.items():
        if k == "libraries":
            from .rules import library_key
            seen = set()
            libs = []
            for lib in list(v) + list(parent.get("libraries", [])):
                key = library_key(lib.get("name", "")) if lib.get("name") else id(lib)
                # одинаковые библиотеки с разными правилами (natives) не схлопываем
                if lib.get("rules") or lib.get("natives"):
                    key = (key, json.dumps(lib.get("rules"), sort_keys=True), json.dumps(lib.get("natives"), sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                libs.append(lib)
            res["libraries"] = libs
        elif k == "arguments":
            args = copy.deepcopy(parent.get("arguments", {}))
            for kind in ("game", "jvm"):
                if kind in v:
                    args[kind] = list(args.get(kind, [])) + list(v[kind])
            res["arguments"] = args
        elif k == "inheritsFrom":
            continue
        else:
            res[k] = copy.deepcopy(v)
    return res


def resolve_version(root, vid, manifest, refresh=False, _depth=0):
    """Возвращает полностью собранный version.json. Поле _jar — какой jar использовать как клиент."""
    if _depth > 10:
        raise RuntimeError("Слишком глубокая цепочка inheritsFrom")
    data = load_version_json(root, vid, manifest, refresh)
    parent_id = data.get("inheritsFrom")
    if parent_id:
        parent = resolve_version(root, parent_id, manifest, refresh, _depth + 1)
        merged = _merge(parent, data)
        merged["_jar"] = data.get("jar") or parent.get("_jar") or parent_id
        merged["_chain"] = [vid] + parent.get("_chain", [parent_id])
        return merged
    data["_jar"] = data.get("jar") or vid
    data["_chain"] = [vid]
    return data


def required_java(vdata):
    jv = vdata.get("javaVersion") or {}
    return jv.get("component") or "jre-legacy", int(jv.get("majorVersion") or 8)
