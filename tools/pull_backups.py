"""Копии базы сайта на этом ПК: скачивает новые ежедневные бэкапы с сервера и проверяет их.

Сервер раз в сутки кладёт копию базы в /var/lib/zelees/backups (хранит 14 последних). Если сервер
пропадёт, пропадут и они — поэтому их нужно держать ещё где-то. Этот скрипт:
  * скачивает по scp бэкапы, которых ещё нет локально (вход по SSH-ключу, как у publish_update);
  * проверяет каждый: PRAGMA integrity_check и наличие таблиц users/servers/packs;
  * хранит KEEP последних в %USERPROFILE%\\.zelees\\server-backups.

    py tools\\pull_backups.py            скачать новые
    py tools\\pull_backups.py --check    только проверить, насколько свежая последняя копия
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
CFG = os.path.join(ROOT, "tools", "update_config.json")
REMOTE_DIR = "/var/lib/zelees/backups"
LOCAL_DIR = os.path.join(os.path.expanduser("~"), ".zelees", "server-backups")
KEEP = 60
NAME_RE = re.compile(r"^zelees-(\d{8})\.db$")
TABLES = {"users", "servers", "settings"}  # packs появилась в 1.0.13 — в старых копиях её нет
STALE_DAYS = 2


def ssh_target():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            target = json.load(f).get("site_ssh", "")
    except (OSError, ValueError):
        target = ""
    if not target:
        sys.exit("Укажите site_ssh в %s (например root@1.2.3.4)" % CFG)
    return target


def remote_list(target):
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", target, "ls -1 %s" % REMOTE_DIR],
                         check=True, capture_output=True, text=True).stdout
    return sorted(n for n in out.split() if NAME_RE.match(n))


def verify(path):
    """Пустая строка, если копия целая, иначе — что с ней не так."""
    try:
        c = sqlite3.connect("file:%s?mode=ro&immutable=1" % path.replace("\\", "/"), uri=True)
        try:
            res = c.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
    except sqlite3.Error as e:
        return "не открывается: %s" % e
    if res != "ok":
        return "integrity_check: %s" % res
    missing = TABLES - tables
    return "нет таблиц: %s" % ", ".join(sorted(missing)) if missing else ""


def local_list():
    if not os.path.isdir(LOCAL_DIR):
        return []
    return sorted(n for n in os.listdir(LOCAL_DIR) if NAME_RE.match(n))


def age_days(name):
    day = time.mktime(time.strptime(NAME_RE.match(name).group(1), "%Y%m%d"))
    return (time.time() - day) / 86400


def main():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    if "--check" not in sys.argv:
        target = ssh_target()
        have = set(local_list())
        new = [n for n in remote_list(target) if n not in have]
        for n in new:
            tmp = os.path.join(LOCAL_DIR, n + ".part")
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "%s:%s/%s" % (target, REMOTE_DIR, n), tmp], check=True)
            problem = verify(tmp)
            if problem:
                os.replace(tmp, os.path.join(LOCAL_DIR, n + ".broken"))
                print("ПОВРЕЖДЕНА: %s — %s" % (n, problem))
                continue
            os.replace(tmp, os.path.join(LOCAL_DIR, n))
            print("Скачана: %s (%d КБ)" % (n, os.path.getsize(os.path.join(LOCAL_DIR, n)) // 1024))
        if not new:
            print("Новых копий нет.")
        for n in local_list()[:-KEEP]:
            os.remove(os.path.join(LOCAL_DIR, n))

    names = local_list()
    if not names:
        sys.exit("Локальных копий нет: %s" % LOCAL_DIR)
    last = names[-1]
    print("Копий на этом ПК: %d, последняя: %s (%.1f дн. назад) — %s" % (len(names), last, age_days(last), LOCAL_DIR))
    if age_days(last) > STALE_DAYS:
        sys.exit("ВНИМАНИЕ: последней копии больше %d дней — проверьте, работает ли сервер и бэкапы" % STALE_DAYS)


if __name__ == "__main__":
    main()
