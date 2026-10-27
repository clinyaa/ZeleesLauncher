"""ZeleesLauncher — лаунчер Minecraft: вход только через Microsoft (лицензия), все версии, автоподбор Java.

Этот файл — «загрузчик». В собранном exe он подхватывает обновлённый код лаунчера
из %APPDATA%\\ZeleesLauncher\\app\\app-<версия>.zip, если такой скачан (см. launcher/updater.py).
"""
import importlib.abc
import json
import os
import subprocess
import sys
import traceback
import zipimport

# Увеличивайте, только если меняется сам exe (новая версия Python, новые модули стандартной библиотеки).
BOOTSTRAP_VERSION = 1


def _vt(v):
    return tuple(int("".join(c for c in p if c.isdigit()) or 0) for p in str(v).split("."))


class _ZipOverlay(importlib.abc.MetaPathFinder):
    """Отдаёт пакет launcher из zip-обновления раньше встроенного в exe."""

    def __init__(self, zp):
        self.zp = zp

    def find_spec(self, name, path=None, target=None):
        if name != "launcher" and not name.startswith("launcher."):
            return None
        parts = name.split(".")
        base = os.path.join(self.zp, *parts[:-1]) if len(parts) > 1 else self.zp
        try:
            return zipimport.zipimporter(base).find_spec(name)
        except (zipimport.ZipImportError, OSError):
            return None


def _activate_update():
    """Включает код из скачанного обновления. Возвращает путь к state.json или None."""
    if not getattr(sys, "frozen", False):
        return None
    os.environ["ZELEES_BOOTSTRAP"] = str(BOOTSTRAP_VERSION)
    try:
        from launcher import config as cfg  # встроенная версия: пути и номер версии
        state_path = os.path.join(cfg.DATA_DIR, "app", "state.json")
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        zp = st["zip"]
        if not os.path.isfile(zp) or int(st.get("min_bootstrap", 1)) > BOOTSTRAP_VERSION:
            return None
        if _vt(st["version"]) <= _vt(cfg.APP_VERSION):
            return None  # установлена версия новее или такая же — пакет не нужен
    except Exception:  # noqa: BLE001
        return None
    for k in [k for k in sys.modules if k == "launcher" or k.startswith("launcher.")]:
        del sys.modules[k]
    sys.meta_path.insert(0, _ZipOverlay(zp))
    return state_path


def _write_crash(text):
    try:
        from launcher.config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "crash.log"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:  # noqa: BLE001
        pass


_FAULT_FILE = None


def _enable_diagnostics():
    """Пишет нативные падения (access violation и т.п.) и ошибки в потоках в DATA_DIR,
    иначе exe без консоли закрывается молча и причину не узнать."""
    global _FAULT_FILE
    if sys.platform.startswith("win"):
        try:  # при нативном падении процесс завершается сразу, а не висит под окном отчёта Windows
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
        except Exception:  # noqa: BLE001
            pass
    try:
        import faulthandler
        import threading
        import time
        from launcher.config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        _FAULT_FILE = open(os.path.join(DATA_DIR, "fault.log"), "a", encoding="utf-8")
        _FAULT_FILE.write("\n=== запуск %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        _FAULT_FILE.flush()
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)

        def hook(args):
            try:
                _FAULT_FILE.write("[поток %s] %s\n" % (
                    getattr(args.thread, "name", "?"),
                    "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))
                _FAULT_FILE.flush()
            except Exception:  # noqa: BLE001
                pass
        threading.excepthook = hook
    except Exception:  # noqa: BLE001
        pass


def _hard_exit(code):
    """Завершает процесс сразу. Обычный выход ждёт фоновые потоки, а они могут висеть
    в сетевых запросах или в вызовах Tk уже уничтоженного окна — тогда процесс остаётся
    в диспетчере задач. Запущенная игра — отдельный процесс, её это не закрывает."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        if _FAULT_FILE:
            _FAULT_FILE.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)


def main():
    _enable_diagnostics()
    if len(sys.argv) >= 4 and sys.argv[1] == "--ms-webview":
        from launcher import ms_webview
        try:
            ms_webview.run(sys.argv[2], sys.argv[3])
        finally:
            os._exit(0)
        return
    overlay = _activate_update()
    try:
        from launcher.gui import run
        run()
        _hard_exit(0)
    except Exception:  # noqa: BLE001
        text = traceback.format_exc()
        _write_crash(text)
        if overlay and not getattr(sys, "_zelees_started", False):
            # обновление не запустилось — откатываемся на встроенную версию
            try:
                os.replace(overlay, overlay + ".bad")
                subprocess.Popen([sys.executable], close_fds=True)
                os._exit(0)
            except Exception:  # noqa: BLE001
                pass
        try:
            import tkinter.messagebox as mb
            mb.showerror("ZeleesLauncher", "Лаунчер упал:\n\n" + text[-1500:])
        except Exception:  # noqa: BLE001
            print(text)
        _hard_exit(1)


if __name__ == "__main__":
    main()
