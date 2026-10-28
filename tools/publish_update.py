"""Публикация обновления ZeleesLauncher.

    py tools\\publish_update.py 1.0.1 "Что нового"            — собрать пакет обновления
    py tools\\publish_update.py 1.0.1 "Что нового" --upload   — и сразу выложить на GitHub (нужен gh)

Что делает:
  1. ставит версию в launcher/config.py (и адрес обновлений, если он ещё пустой);
  2. собирает release/zelees-app-<версия>.zip — только код и картинки лаунчера (~1 МБ);
  3. если рядом лежит release/ZeleesLauncher-Setup-<версия>.exe — добавляет и его;
  4. пишет release/update.json — его читают установленные лаунчеры — и подписывает его
     ключом с этого ПК (tools/update_key.py; без ключа публикация не пройдёт);
  5. с --upload создаёт GitHub Release v<версия> и прикладывает файлы.

Куда выкладывать, задаёт tools/update_config.json:
  * свой сайт:  {"site_url": "https://example.com/update", "site_ssh": "root@your-server-ip"}
                --upload копирует файлы через scp в /opt/zelees/site/download (манифест — последним);
  * GitHub:     {"github_repo": "ваш-логин/zelees-launcher"}
Если задан site_url, используется он.
"""
import argparse
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import update_key  # noqa: E402
from launcher import updater  # noqa: E402

CFG =os.path.join(ROOT, "tools", "update_config.json")
CONFIG_PY = os.path.join(ROOT, "launcher", "config.py")
REL = os.path.join(ROOT, "release")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_or_none():
    """Репозиторий из update_config.json или None, если он ещё не настроен."""
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            repo = json.load(f).get("github_repo", "")
    except (OSError, ValueError):
        return None
    return repo if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo) else None


def read_cfg():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def site_or_none():
    """(адрес папки обновлений на сайте, ssh-цель) из update_config.json или None."""
    cfg = read_cfg()
    url = str(cfg.get("site_url", "")).rstrip("/")
    return (url, str(cfg.get("site_ssh", ""))) if url.startswith("https://") else None


def load_cfg():
    repo = repo_or_none()
    if not repo:
        sys.exit("Укажите site_url или свой репозиторий GitHub в %s (например \"zelees/zelees-launcher\")" % CFG)
    return repo


def set_version(version, update_url):
    with open(CONFIG_PY, "r", encoding="utf-8") as f:
        s = f.read()
    s = re.sub(r'APP_VERSION = "[^"]*"', 'APP_VERSION = "%s"' % version, s, count=1)
    s = re.sub(r'UPDATE_URL = "[^"]*"', 'UPDATE_URL = "%s"' % update_url, s, count=1)
    with open(CONFIG_PY, "w", encoding="utf-8") as f:
        f.write(s)


def build_package(version):
    src = os.path.join(ROOT, "launcher")
    for fn in os.listdir(src):
        if fn.endswith(".py"):
            py_compile.compile(os.path.join(src, fn), doraise=True)  # не публикуем код с ошибками
    os.makedirs(REL, exist_ok=True)
    out = os.path.join(REL, "zelees-app-%s.zip" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(src)):
            if fn.endswith(".py") or fn == "assets.zip":
                z.write(os.path.join(src, fn), "launcher/" + fn)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("notes", nargs="?", default="")
    ap.add_argument("--min-bootstrap", type=int, default=1,
                    help="минимальная версия exe; увеличьте, если пакет не заработает в старом exe")
    ap.add_argument("--upload", action="store_true", help="создать GitHub Release через gh")
    ap.add_argument("--skip-version", action="store_true", help="не менять версию в config.py")
    ap.add_argument("--beta", action="store_true",
                    help="бета-канал: update-beta.json вместо update.json (получат только те, кто включил бета-версии)")
    a = ap.parse_args()

    site = site_or_none()
    if a.beta and not site:
        sys.exit("Бета-канал работает только с сайтом (site_url в update_config.json)")
    if site:
        repo = None
        update_url = site[0] + "/update.json"
        base = site[0] + "/"
    else:
        repo = load_cfg()
        update_url = "https://github.com/%s/releases/latest/download/update.json" % repo
        base = "https://github.com/%s/releases/download/v%s/" % (repo, a.version)
    if not a.skip_version:
        set_version(a.version, update_url)

    pkg = build_package(a.version)
    manifest = {
        "version": a.version,
        **({"channel": "beta"} if a.beta else {}),  # канал входит в подпись — бету не подложить вместо обычного
        "notes": a.notes,
        "min_bootstrap": a.min_bootstrap,
        "package": {"url": base + os.path.basename(pkg), "sha256": sha256(pkg), "size": os.path.getsize(pkg)},
    }
    files = [pkg]
    inst = os.path.join(REL, "ZeleesLauncher-Setup-%s.exe" % a.version)
    if os.path.isfile(inst):
        manifest["installer"] = {"url": base + os.path.basename(inst), "sha256": sha256(inst),
                                 "size": os.path.getsize(inst)}
        files.append(inst)
    manifest["signature"] = update_key.sign(updater.signed_bytes(manifest))
    man_path = os.path.join(REL, "update-beta.json" if a.beta else "update.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    files.append(man_path)

    print("\nГотово: версия %s" % a.version)
    for p in files:
        print("  %-40s %8.1f КБ" % (os.path.basename(p), os.path.getsize(p) / 1024))

    if a.upload and site:
        if not site[1] or not shutil.which("scp"):
            sys.exit("Для --upload нужны site_ssh в update_config.json и scp (OpenSSH) в системе")
        dest = "%s:/opt/zelees/site/download/" % site[1]
        for p in files:  # update.json в списке последний: лаунчеры увидят версию, только когда всё уже загружено
            subprocess.run(["scp", p, dest], check=True)
        print("\nОпубликовано на сайте. Установленные лаунчеры увидят обновление при следующем запуске.")
    elif a.upload:
        if not shutil.which("gh"):
            sys.exit("Не найден GitHub CLI (gh). Установите: winget install GitHub.cli, затем gh auth login")
        cmd = ["gh", "release", "create", "v" + a.version, *files, "--repo", repo,
               "--title", "ZeleesLauncher %s" % a.version, "--notes", a.notes or "Обновление"]
        subprocess.run(cmd, check=True)
        print("\nОпубликовано. Установленные лаунчеры увидят обновление при следующем запуске.")
    elif not site:
        print("\nЗагрузите эти файлы в новый GitHub Release с тегом v%s (или запустите с --upload)." % a.version)


if __name__ == "__main__":
    main()
