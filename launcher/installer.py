"""Установка версии: клиент, библиотеки, нативные библиотеки, ассеты, конфиг логов."""
import json
import os
import shutil
import zipfile

from . import net
from .rules import arch_bits, is_allowed, maven_path, os_name
from .versions import version_dir, version_jar_path

LIBRARIES_URL = "https://libraries.minecraft.net/"
RESOURCES_URL = "https://resources.download.minecraft.net/"


def collect_libraries(vdata, root, features=None):
    """Возвращает (classpath, natives, tasks).
    natives — список (путь_к_jar, exclude) для старого формата (до 1.19)."""
    lib_dir = os.path.join(root, "libraries")
    classpath, natives, tasks = [], [], []
    seen_cp = set()
    for lib in vdata.get("libraries", []):
        if not is_allowed(lib.get("rules"), features):
            continue
        name = lib.get("name")
        if not name:
            continue
        downloads = lib.get("downloads") or {}
        base_url = lib.get("url") or LIBRARIES_URL
        if not base_url.endswith("/"):
            base_url += "/"

        # ---- нативные библиотеки старого формата ----
        nat_map = lib.get("natives")
        if nat_map:
            cls = nat_map.get(os_name())
            if cls:
                cls = cls.replace("${arch}", arch_bits())
                info = (downloads.get("classifiers") or {}).get(cls)
                if info:
                    rel = info.get("path") or maven_path(name, cls)
                    path = os.path.join(lib_dir, rel)
                    if info.get("url"):
                        tasks.append(net.Task(info["url"], path, info.get("sha1"), info.get("size")))
                else:
                    rel = maven_path(name, cls)
                    path = os.path.join(lib_dir, rel)
                    tasks.append(net.Task(base_url + rel, path))
                natives.append((path, (lib.get("extract") or {}).get("exclude", [])))

        # ---- обычный artifact ----
        artifact = downloads.get("artifact")
        if artifact is not None:
            rel = artifact.get("path") or maven_path(name)
            path = os.path.join(lib_dir, rel)
            if artifact.get("url"):
                tasks.append(net.Task(artifact["url"], path, artifact.get("sha1"), artifact.get("size")))
        elif nat_map:
            # старая библиотека, содержащая только natives (lwjgl-platform, jinput-platform)
            continue
        elif "downloads" in lib and not downloads.get("artifact"):
            continue
        else:
            rel = maven_path(name)
            path = os.path.join(lib_dir, rel)
            tasks.append(net.Task(base_url + rel, path, lib.get("sha1"), lib.get("size")))
        norm = os.path.normcase(os.path.abspath(path))
        if norm not in seen_cp:
            seen_cp.add(norm)
            classpath.append(path)
    return classpath, natives, tasks


def client_task(vdata, root):
    jar_id = vdata.get("_jar") or vdata["id"]
    path = version_jar_path(root, jar_id)
    client = (vdata.get("downloads") or {}).get("client")
    if client and client.get("url"):
        return path, net.Task(client["url"], path, client.get("sha1"), client.get("size"))
    return path, None


def asset_info(vdata, root):
    ai = vdata.get("assetIndex") or {}
    index_id = ai.get("id") or vdata.get("assets") or "legacy"
    return index_id, os.path.join(root, "assets", "indexes", index_id + ".json"), ai


def install_assets(vdata, root, game_dir, threads, progress, cancel):
    index_id, index_path, ai = asset_info(vdata, root)
    if ai.get("url"):
        if not net.is_valid(index_path, ai.get("sha1")):
            net.download(ai["url"], index_path, ai.get("sha1"), cancel=cancel)
    if not os.path.isfile(index_path):
        return
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    objects = index.get("objects", {})
    obj_dir = os.path.join(root, "assets", "objects")
    tasks = []
    for _name, o in objects.items():
        h = o["hash"]
        tasks.append(net.Task(RESOURCES_URL + h[:2] + "/" + h, os.path.join(obj_dir, h[:2], h),
                              h, o.get("size"), check_hash=False))
    net.download_many(tasks, threads, progress, cancel, "Ассеты")

    # старые версии: ассеты лежат «как есть» по именам
    targets = []
    if index.get("virtual"):
        targets.append(os.path.join(root, "assets", "virtual", index_id))
    if index.get("map_to_resources"):
        targets.append(os.path.join(game_dir, "resources"))
    for target in targets:
        for name, o in objects.items():
            h = o["hash"]
            src = os.path.join(obj_dir, h[:2], h)
            dst = os.path.join(target, *name.split("/"))
            if not os.path.isfile(dst) or os.path.getsize(dst) != o.get("size", -1):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)


def game_assets_dir(vdata, root, game_dir):
    """Значение ${game_assets} для старых версий."""
    index_id, index_path, _ = asset_info(vdata, root)
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    if index.get("map_to_resources"):
        return os.path.join(game_dir, "resources")
    return os.path.join(root, "assets", "virtual", index_id)


def logging_info(vdata, root):
    """Возвращает (jvm_аргумент, Task) для конфигурации log4j или (None, None)."""
    lg = ((vdata.get("logging") or {}).get("client")) or {}
    fi = lg.get("file") or {}
    if not fi.get("url") or not lg.get("argument"):
        return None, None
    path = os.path.join(root, "assets", "log_configs", fi.get("id", "client.xml"))
    return lg["argument"].replace("${path}", path), net.Task(fi["url"], path, fi.get("sha1"), fi.get("size"))


def install_version(vdata, root, game_dir, threads=16, progress=None, cancel=None):
    """Скачивает всё необходимое для запуска уже собранного vdata."""
    _cp, _natives, lib_tasks = collect_libraries(vdata, root)
    jar_path, ct = client_task(vdata, root)
    tasks = list(lib_tasks)
    if ct:
        tasks.append(ct)
    _arg, lt = logging_info(vdata, root)
    if lt:
        tasks.append(lt)
    net.download_many(tasks, threads, progress, cancel, "Библиотеки")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError("Нет клиента %s (версия не содержит ссылку на скачивание)" % jar_path)
    install_assets(vdata, root, game_dir, threads, progress, cancel)
    for vid in vdata.get("_chain", [vdata["id"]]):
        d = version_dir(root, vid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, ".installed"), "w") as f:
            f.write("ok")


def extract_natives(natives, dest):
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    for jar, exclude in natives:
        if not os.path.isfile(jar):
            continue
        with zipfile.ZipFile(jar) as z:
            for info in z.infolist():
                n = info.filename
                if n.endswith("/") or any(n.startswith(e) for e in exclude):
                    continue
                if n.startswith("META-INF/"):
                    continue
                target = os.path.normpath(os.path.join(dest, n))
                if not target.startswith(os.path.normpath(dest) + os.sep):
                    continue  # защита от путей вида ../
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
