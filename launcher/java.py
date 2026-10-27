"""Поиск установленных Java, проверка версии под нужную версию Minecraft и скачивание рантайма Mojang."""
import os
import re
import shutil
import subprocess
import sys
import threading

from . import net
from .config import RUNTIME_DIR
from .rules import os_arch, os_name

RUNTIME_ALL_URL = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                   "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")

IS_WIN = os_name() == "windows"
JAVA_EXE = "java.exe" if IS_WIN else "java"
JAVAW_EXE = "javaw.exe" if IS_WIN else "java"
NO_WINDOW = 0x08000000 if IS_WIN else 0


class JavaInstall:
    def __init__(self, path, version, major, is64, source):
        self.path = path          # путь к java(.exe)
        self.version = version
        self.major = major
        self.is64 = is64
        self.source = source

    @property
    def home(self):
        return os.path.dirname(os.path.dirname(self.path))

    @property
    def launch_exe(self):
        """javaw.exe — без чёрного окна консоли (вывод всё равно читается через pipe)."""
        w = os.path.join(os.path.dirname(self.path), JAVAW_EXE)
        return w if os.path.isfile(w) else self.path

    def __repr__(self):
        return "Java %s (%s) %s" % (self.version, "64" if self.is64 else "32", self.path)


def parse_major(version):
    v = (version or "").strip().strip('"')
    if v.startswith("1."):
        try:
            return int(v.split(".")[1])
        except (IndexError, ValueError):
            return 0
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else 0


_probe_cache = {}
_probe_lock = threading.Lock()


def probe(java_exe, source="?"):
    """Определяет версию и разрядность java. Сначала читает файл release, иначе запускает java -version."""
    java_exe = os.path.abspath(java_exe)
    if not os.path.isfile(java_exe):
        return None
    try:
        key = (os.path.normcase(java_exe), os.path.getmtime(java_exe))
    except OSError:
        return None
    with _probe_lock:
        if key in _probe_cache:
            c = _probe_cache[key]
            return JavaInstall(c.path, c.version, c.major, c.is64, source) if c else None

    result = None
    home = os.path.dirname(os.path.dirname(java_exe))
    release = os.path.join(home, "release")
    version, arch = None, None
    if os.path.isfile(release):
        try:
            with open(release, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("JAVA_VERSION="):
                        version = line.split("=", 1)[1].strip().strip('"')
                    elif line.startswith("OS_ARCH="):
                        arch = line.split("=", 1)[1].strip().strip('"').lower()
        except OSError:
            pass
    if version and arch:
        is64 = arch in ("amd64", "x86_64", "x64", "aarch64", "arm64")
        result = JavaInstall(java_exe, version, parse_major(version), is64, source)
    else:
        try:
            p = subprocess.run([java_exe, "-version"], capture_output=True, timeout=15,
                               creationflags=NO_WINDOW)
            out = (p.stderr + p.stdout).decode("utf-8", "replace")
            m = re.search(r'version "([^"]+)"', out)
            if m:
                version = m.group(1)
                is64 = "64-Bit" in out or "64-bit" in out
                result = JavaInstall(java_exe, version, parse_major(version), is64, source)
        except (OSError, subprocess.SubprocessError):
            result = None
    with _probe_lock:
        _probe_cache[key] = result
    return result


def _find_java_under(root, max_depth=4):
    """Ищет bin/java(.exe) внутри root на глубину max_depth."""
    found = []
    if not root or not os.path.isdir(root):
        return found
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _files in os.walk(root):
        depth = dirpath.count(os.sep) - base_depth
        exe = os.path.join(dirpath, "bin", JAVA_EXE)
        if os.path.isfile(exe):
            found.append(exe)
            dirnames[:] = []
            continue
        if depth >= max_depth:
            dirnames[:] = []
        else:
            # не лезем в тяжёлые папки
            dirnames[:] = [d for d in dirnames if d.lower() not in ("lib", "legal", "include", "jmods", "conf")]
    return found


def _registry_javas():
    res = []
    if not IS_WIN:
        return res
    try:
        import winreg
    except ImportError:
        return res
    keys = [
        r"SOFTWARE\JavaSoft\Java Runtime Environment",
        r"SOFTWARE\JavaSoft\Java Development Kit",
        r"SOFTWARE\JavaSoft\JRE",
        r"SOFTWARE\JavaSoft\JDK",
        r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment",
        r"SOFTWARE\WOW6432Node\JavaSoft\Java Development Kit",
    ]
    for k in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k) as h:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(h, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(h, sub) as sh:
                            home, _ = winreg.QueryValueEx(sh, "JavaHome")
                            exe = os.path.join(home, "bin", JAVA_EXE)
                            if os.path.isfile(exe):
                                res.append(exe)
                    except OSError:
                        pass
        except OSError:
            pass
    return res


def detect_javas(game_root=None):
    """Возвращает список JavaInstall, найденных в системе."""
    candidates = []  # (exe, source)

    def add(exe, src):
        if exe and os.path.isfile(exe):
            candidates.append((exe, src))

    # 1. рантаймы, скачанные этим лаунчером
    for exe in _find_java_under(RUNTIME_DIR, 2):
        add(exe, "ZeleesLauncher")
    # 2. JAVA_HOME и PATH
    jh = os.environ.get("JAVA_HOME")
    if jh:
        add(os.path.join(jh, "bin", JAVA_EXE), "JAVA_HOME")
    w = shutil.which("java")
    if w:
        add(os.path.realpath(w), "PATH")
    # 3. реестр
    for exe in _registry_javas():
        add(exe, "Реестр")
    # 4. стандартные папки
    if IS_WIN:
        bases = {os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")}
        local = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        if local:
            bases.add(os.path.join(local, "Programs"))
        vendors = ["Java", "Eclipse Adoptium", "Eclipse Foundation", "AdoptOpenJDK", "Zulu", "Microsoft",
                   "BellSoft", "Amazon Corretto", "Semeru", "IBM", "Oracle", "Temurin", "Liberica", "GraalVM"]
        for b in filter(None, bases):
            for v in vendors:
                for exe in _find_java_under(os.path.join(b, v), 2):
                    add(exe, v)
        home = os.path.expanduser("~")
        for exe in _find_java_under(os.path.join(home, ".jdks"), 1):
            add(exe, ".jdks")
        # Java из официального лаунчера Minecraft
        mc_runtimes = [
            os.path.join(local, "Packages", "Microsoft.4297127D64EC6_8wekyb3d8bbwe", "LocalCache", "Local", "runtime"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Minecraft Launcher", "runtime"),
            os.path.join(appdata, ".minecraft", "runtime"),
        ]
        for r in mc_runtimes:
            for exe in _find_java_under(r, 4):
                add(exe, "Minecraft Launcher")
    else:
        for r in ("/usr/lib/jvm", "/usr/java", "/opt", "/Library/Java/JavaVirtualMachines"):
            for exe in _find_java_under(r, 4):
                add(exe, "система")
    if game_root:
        for exe in _find_java_under(os.path.join(game_root, "runtime"), 4):
            add(exe, "runtime")

    seen, res = set(), []
    for exe, src in candidates:
        k = os.path.normcase(os.path.abspath(exe))
        if k in seen:
            continue
        seen.add(k)
        j = probe(exe, src)
        if j and j.major > 0:
            res.append(j)
    res.sort(key=lambda j: (-j.major, not j.is64, j.path))
    return res


def is_compatible(java, required_major):
    """Java 8 нужна строго для старых версий (на 9+ они падают). Для 16+ годится такая же или новее."""
    if required_major <= 8:
        return java.major == 8
    return java.major >= required_major


def pick_java(installs, required_major):
    good = [j for j in installs if is_compatible(j, required_major)]
    if not good:
        return None
    # точное совпадение лучше, 64-бит лучше, свой рантайм лучше
    good.sort(key=lambda j: (j.major != required_major, not j.is64, j.source != "ZeleesLauncher", j.major))
    return good[0]


# ------------------------- рантайм Mojang -------------------------

def runtime_platform():
    if IS_WIN:
        a = os_arch()
        return {"x86_64": "windows-x64", "x86": "windows-x86", "arm64": "windows-arm64"}[a]
    if sys.platform == "darwin":
        return "mac-os-arm64" if os_arch() == "arm64" else "mac-os"
    return "linux" if os_arch() != "x86" else "linux-i386"


_all_cache = {}


def runtime_index():
    if "all" not in _all_cache:
        _all_cache["all"] = net.get_json(RUNTIME_ALL_URL)
    return _all_cache["all"]


def available_runtimes():
    """[(component, version_name, major)] для текущей платформы."""
    allj = runtime_index()
    plat = allj.get(runtime_platform()) or {}
    res = []
    for comp, lst in plat.items():
        if lst:
            name = (lst[0].get("version") or {}).get("name", "")
            res.append((comp, name, parse_major(name)))
    res.sort(key=lambda x: (x[2], x[0]))
    return res


def _find_component(component, major):
    allj = runtime_index()
    plats = [runtime_platform()]
    if runtime_platform() == "windows-arm64":
        plats.append("windows-x64")  # Windows on ARM умеет эмулировать x64
    for plat in plats:
        comps = allj.get(plat) or {}
        if comps.get(component):
            return component, comps[component][0]
    # компонента нет — ищем любой с подходящей версией
    for plat in plats:
        comps = allj.get(plat) or {}
        for comp, lst in comps.items():
            if lst and parse_major((lst[0].get("version") or {}).get("name", "")) == major:
                return comp, lst[0]
    return None, None


def install_runtime(component, major, threads=16, progress=None, cancel=None):
    """Скачивает Java от Mojang в data/runtime/<component>. Возвращает JavaInstall."""
    comp, entry = _find_component(component, major)
    if not entry:
        raise RuntimeError("Mojang не предоставляет Java %s для платформы %s" % (major, runtime_platform()))
    target = os.path.join(RUNTIME_DIR, comp)
    manifest = net.get_json(entry["manifest"]["url"])
    files = manifest.get("files", {})
    tasks, links, execs = [], [], []
    for rel, info in files.items():
        p = os.path.join(target, *rel.split("/"))
        t = info.get("type")
        if t == "directory":
            os.makedirs(p, exist_ok=True)
        elif t == "file":
            raw = (info.get("downloads") or {}).get("raw")
            if raw:
                tasks.append(net.Task(raw["url"], p, raw.get("sha1"), raw.get("size")))
            if info.get("executable"):
                execs.append(p)
        elif t == "link":
            links.append((p, info.get("target")))
    net.download_many(tasks, threads, progress, cancel, "Java %s" % major)
    if not IS_WIN:
        for p in execs:
            try:
                os.chmod(p, 0o755)
            except OSError:
                pass
        for p, tgt in links:
            try:
                if not os.path.lexists(p) and tgt:
                    os.symlink(tgt, p)
            except OSError:
                pass
    with open(os.path.join(target, ".version"), "w", encoding="utf-8") as f:
        f.write((entry.get("version") or {}).get("name", ""))
    exe = None
    for e in _find_java_under(target, 3):
        exe = e
        break
    if not exe:
        raise RuntimeError("Java скачана, но java%s не найден в %s" % (".exe" if IS_WIN else "", target))
    j = probe(exe, "ZeleesLauncher")
    if not j:
        ver = (entry.get("version") or {}).get("name", str(major))
        j = JavaInstall(exe, ver, parse_major(ver) or major, True, "ZeleesLauncher")
    return j
