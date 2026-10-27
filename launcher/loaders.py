"""Загрузчики модов: определение и установка Fabric, Quilt, Forge, NeoForge."""
import json
import os
import subprocess

from . import net
from .versions import version_dir, version_json_path

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_PROMOS = "https://maven.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"

LOADERS = [("fabric", "Fabric"), ("quilt", "Quilt"), ("forge", "Forge"), ("neoforge", "NeoForge")]
LOADER_NAMES = dict(LOADERS)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def detect(vdata):
    """По собранному version.json -> (loader | None, версия_загрузчика, версия_minecraft)."""
    chain = vdata.get("_chain") or [vdata.get("id")]
    mc = chain[-1]
    gargs = [str(x) for x in (vdata.get("arguments") or {}).get("game", []) if isinstance(x, str)]
    for i, a in enumerate(gargs[:-1]):
        if a == "--fml.neoForgeVersion":
            return "neoforge", gargs[i + 1], mc
    for i, a in enumerate(gargs[:-1]):
        if a == "--fml.forgeVersion":
            return "forge", gargs[i + 1], mc
    for lib in vdata.get("libraries", []):
        name = lib.get("name", "")
        parts = name.split(":")
        if len(parts) < 3:
            continue
        g, a, v = parts[0], parts[1], parts[2]
        if g == "net.fabricmc" and a == "fabric-loader":
            return "fabric", v, mc
        if g == "org.quiltmc" and a == "quilt-loader":
            return "quilt", v, mc
        if g == "net.neoforged" and a in ("neoforge", "forge"):
            return "neoforge", v, mc
        if g == "net.minecraftforge" and a in ("forge", "fmlloader"):
            return "forge", v.split("-", 1)[-1], mc
    args = " ".join(str(x) for x in (vdata.get("arguments") or {}).get("game", []) if isinstance(x, str))
    args += " " + (vdata.get("minecraftArguments") or "")
    if "neoforge" in args.lower():
        return "neoforge", "", mc
    if "fml." in args or "FMLTweaker" in args:
        return "forge", "", mc
    return None, "", mc


def modrinth_loaders(loader):
    """Какие загрузчики модов подходят: Quilt умеет запускать и Fabric-моды."""
    if loader == "quilt":
        return ["quilt", "fabric"]
    return [loader] if loader else []


# ----------------------------------------------------------------- версии загрузчиков

def loader_versions(loader, mc):
    """Список версий загрузчика для данной версии Minecraft, лучшая первая."""
    if loader == "fabric":
        data = net.get_json("%s/versions/loader/%s" % (FABRIC_META, mc))
        vs = [d["loader"] for d in data]
        vs.sort(key=lambda l: not l.get("stable", False))
        return [l["version"] for l in vs]
    if loader == "quilt":
        data = net.get_json("%s/versions/loader/%s" % (QUILT_META, mc))
        vs = [d["loader"]["version"] for d in data]
        return [v for v in vs if "beta" not in v] + [v for v in vs if "beta" in v]
    if loader == "forge":
        promos = net.get_json(FORGE_PROMOS).get("promos", {})
        res = []
        for key in ("%s-recommended" % mc, "%s-latest" % mc):
            if key in promos and promos[key] not in res:
                res.append(promos[key])
        return res
    if loader == "neoforge":
        vs = net.get_json(NEOFORGE_VERSIONS).get("versions", [])
        good = [v for v in vs if neoforge_mc(v) == mc]
        good.reverse()
        stable = [v for v in good if "beta" not in v]
        return stable + [v for v in good if "beta" in v]
    return []


def neoforge_mc(v):
    """21.1.77 -> 1.21.1, 20.4.1 -> 1.20.4, 21.0.1 -> 1.21; 26.1.0.5 -> 26.1 (новая схема)."""
    p = v.split("-")[0].split(".")
    try:
        a, b = int(p[0]), int(p[1])
    except (IndexError, ValueError):
        return None
    if a >= 25:
        return "%d.%d" % (a, b) if b else str(a)
    return "1.%d.%d" % (a, b) if b else "1.%d" % a


# ----------------------------------------------------------------- установка

def install(loader, mc, loader_version, root, java_exe=None, log=None):
    """Ставит загрузчик. Возвращает id новой версии (папка в versions/)."""
    log = log or (lambda s: None)
    if loader in ("fabric", "quilt"):
        meta = FABRIC_META if loader == "fabric" else QUILT_META
        prof = net.get_json("%s/versions/loader/%s/%s/profile/json" % (meta, mc, loader_version))
        vid = prof["id"]
        path = version_json_path(root, vid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prof, f, indent=2)
        log("Профиль %s сохранён" % vid)
        return vid
    if loader in ("forge", "neoforge"):
        if not java_exe:
            raise RuntimeError("Для установки %s нужна Java" % LOADER_NAMES[loader])
        if loader == "forge":
            full = "%s-%s" % (mc, loader_version)
            url = "%s/%s/forge-%s-installer.jar" % (FORGE_MAVEN, full, full)
        else:
            url = "%s/%s/neoforge-%s-installer.jar" % (NEOFORGE_MAVEN, loader_version, loader_version)
        inst = os.path.join(root, ".installers", os.path.basename(url))
        log("Скачиваю установщик %s" % os.path.basename(url))
        net.download(url, inst)
        _ensure_profiles(root)
        before = set(_versions(root))
        log("Запускаю установщик (может занять пару минут)...")
        p = subprocess.run([java_exe, "-jar", inst, "--installClient", root], cwd=os.path.dirname(inst),
                           capture_output=True, timeout=1800, creationflags=NO_WINDOW)
        out = (p.stdout + p.stderr).decode("utf-8", "replace")
        for line in out.splitlines()[-15:]:
            log(line)
        new = [v for v in _versions(root) if v not in before]
        if not new:
            # переустановка той же версии: ищем по имени
            key = loader_version
            new = [v for v in _versions(root) if key in v and loader.replace("neo", "") in v.lower()]
        if p.returncode != 0 or not new:
            raise RuntimeError("Установщик %s завершился с ошибкой (код %s). Подробности — в консоли."
                               % (LOADER_NAMES[loader], p.returncode))
        return sorted(new, key=len)[0]
    raise ValueError("Неизвестный загрузчик: %s" % loader)


def _versions(root):
    d = os.path.join(root, "versions")
    if not os.path.isdir(d):
        return []
    return [v for v in os.listdir(d) if os.path.isfile(version_json_path(root, v))]


def _ensure_profiles(root):
    lp = os.path.join(root, "launcher_profiles.json")
    if not os.path.isfile(lp):
        os.makedirs(root, exist_ok=True)
        with open(lp, "w", encoding="utf-8") as f:
            f.write('{"profiles": {}, "selectedProfile": "", "authenticationDatabase": {}}')


def remove_version(root, vid):
    import shutil
    shutil.rmtree(version_dir(root, vid), ignore_errors=True)
