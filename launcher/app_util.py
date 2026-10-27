"""Общие функции и константы окна лаунчера."""
import os
import subprocess
import sys
import time

from .config import DATA_DIR

BASE_W, BASE_H = 1120, 700
SIDE_W = 76
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
TYPE_CUBE = {"release": "release", "snapshot": "snapshot", "old_beta": "beta", "old_alpha": "alpha",
             "local": "modded"}


def total_ram_mb():
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys // (1024 * 1024))
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
    except Exception:  # noqa: BLE001
        return 8192


def open_path(p):
    os.makedirs(p, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def log_to_file(text):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "launcher.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text + "\n")
    except OSError:
        pass


def fmt_date(iso):
    try:
        y, m, d = iso[:10].split("-")
        return "%d %s %s" % (int(d), MONTHS[int(m) - 1], y)
    except (ValueError, IndexError):
        return ""


def spaced(text):
    return " ".join(text)


def hero_kind():
    h = time.localtime().tm_hour
    if 7 <= h < 17:
        return "day"
    if 17 <= h < 21 or 5 <= h < 7:
        return "sunset"
    return "night"
