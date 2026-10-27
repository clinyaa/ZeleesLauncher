"""Полная сборка ZeleesLauncher: exe → установщик → пакет обновления.

Проще всего — дважды щёлкнуть build_release.bat в папке лаунчера.
Или из командной строки:
    py tools\\build_release.py 1.0.0 "Первый релиз"
    py tools\\build_release.py 1.1.0 "Что нового" --upload

Нужно: Python 3.10+ (Windows). PyInstaller и Inno Setup скрипт поставит сам.
Результат — папка release:
    ZeleesLauncher-Setup-<версия>.exe   установщик для пользователей
    zelees-app-<версия>.zip             лёгкий пакет обновления
    update.json                         описание обновления
"""
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import publish_update as pub  # noqa: E402

# Модули стандартной библиотеки, которые кладём в exe «про запас»,
# чтобы лёгкие обновления могли ими пользоваться без переустановки.
HIDDEN = ["tomllib", "tkinter.colorchooser", "tkinter.filedialog", "tkinter.messagebox", "tkinter.simpledialog",
          "tkinter.ttk", "tkinter.font", "http.server", "http.cookiejar", "concurrent.futures", "zipimport",
          "importlib.abc", "importlib.resources", "pkgutil", "uuid", "secrets", "shlex", "struct", "zlib",
          "hashlib", "ssl", "ctypes.wintypes", "winreg", "webbrowser", "platform", "colorsys", "random",
          "statistics", "csv", "html", "html.parser", "xml.etree.ElementTree", "base64", "gzip", "bz2", "lzma",
          "tarfile", "socket", "selectors", "queue", "datetime", "calendar", "difflib", "textwrap", "fnmatch",
          "glob", "tempfile", "shutil", "email", "email.utils", "urllib.request", "urllib.parse", "json"]
EXCLUDE = ["idlelib", "turtledemo", "turtle", "tkinter.test", "test"]  # только то, что точно не мешает PyInstaller


def run(cmd, **kw):
    print("> " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def build_exe():
    run([sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "pyinstaller"])
    for d in ("build", os.path.join("dist", "ZeleesLauncher")):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
           "--name", "ZeleesLauncher", "--icon", os.path.join(ROOT, "ZeleesLauncher.ico"),
           "--add-data", "%s%slauncher" % (os.path.join(ROOT, "launcher", "assets.zip"), os.pathsep),
           "--collect-submodules", "launcher",
           "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build"),
           "--specpath", os.path.join(ROOT, "build")]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join(ROOT, "main.py"))
    run(cmd, cwd=ROOT)
    prune(os.path.join(ROOT, "dist", "ZeleesLauncher"))


def prune(app):
    """Убираем то, что лаунчеру точно не нужно: часовые пояса Tcl, демо Tk и т. п."""
    removed = 0
    for pattern in ("_internal/tcl*/tzdata", "_internal/tcl*/msgs", "_internal/_tcl_data/tzdata",
                    "_internal/_tcl_data/msgs", "_internal/tk*/demos", "_internal/tk*/images",
                    "_internal/_tk_data/demos", "_internal/_tk_data/images"):
        for p in glob.glob(os.path.join(app, pattern)):
            removed += sum(os.path.getsize(os.path.join(dp, f)) for dp, _d, fs in os.walk(p) for f in fs)
            shutil.rmtree(p, ignore_errors=True)
    print("Удалено лишнего: %.1f МБ" % (removed / 1e6))


def find_iscc():
    cands = [shutil.which("iscc"), shutil.which("ISCC")]
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        if base:
            cands += glob.glob(os.path.join(base, "Programs", "Inno Setup *", "ISCC.exe"))
            cands += glob.glob(os.path.join(base, "Inno Setup *", "ISCC.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def build_installer(version):
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup не найден — ставлю через winget...")
        run(["winget", "install", "-e", "--id", "JRSoftware.InnoSetup", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"])
        iscc = find_iscc()
        if not iscc:
            sys.exit("Не удалось найти ISCC.exe. Установите Inno Setup 6 вручную: https://jrsoftware.org/isdl.php")
    run([iscc, "/Q", "/DAppVersion=%s" % version, os.path.join(ROOT, "installer", "ZeleesLauncher.iss")])


def current_version():
    import re
    with open(pub.CONFIG_PY, "r", encoding="utf-8") as f:
        m = re.search(r'APP_VERSION = "([^"]*)"', f.read())
    return m.group(1) if m else "1.0.0"


def ask(prompt, default=""):
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def main():
    print("=" * 60)
    print("  Сборка ZeleesLauncher: программа + установщик")
    print("=" * 60)
    args = sys.argv[1:]
    if args:
        version, rest = args[0], args[1:]
    else:  # запуск двойным щелчком — спрашиваем
        cur = current_version()
        version = ask("Номер версии [%s]: " % cur, cur)
        notes = ask("Что нового (можно оставить пустым): ")
        rest = [notes] if notes else []
    upload = "--upload" in rest

    site = pub.site_or_none()
    repo = None if site else pub.repo_or_none()
    if site:
        url = site[0] + "/update.json"
        print("Обновления: через сайт %s" % site[0])
    elif repo:
        url = "https://github.com/%s/releases/latest/download/update.json" % repo
        print("Обновления: через GitHub %s" % repo)
    else:
        url = ""
        print("Обновления не настроены (tools\\update_config.json) —")
        print("собираю установщик без автообновлений. Это можно настроить позже.")
    if site or repo:
        pub.update_key.sign(b"check")  # нет ключа подписи — останавливаемся до долгой сборки
    pub.set_version(version, url)

    build_exe()
    build_installer(version)
    setup = os.path.join(ROOT, "release", "ZeleesLauncher-Setup-%s.exe" % version)

    if site or repo:
        if not upload and shutil.which("scp" if site else "gh"):
            upload = ask("Выложить версию %s %s сейчас? (д/н) [н]: " % (version, "на сайт" if site else "на GitHub"),
                         "н").lower() in ("д", "y", "да", "yes")
        sys.argv = ["publish_update.py", version] + [a for a in rest if a != "--upload"] + ["--skip-version"] + \
                   (["--upload"] if upload else [])
        pub.main()

    print()
    print("ГОТОВО! Установщик: %s" % setup)
    if os.name == "nt" and os.path.isfile(setup):
        os.startfile(os.path.dirname(setup))  # открываем папку release


if __name__ == "__main__":
    main()
