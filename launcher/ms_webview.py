"""Необязательное встроенное окно входа Microsoft (если установлен pywebview).
Запускается отдельным процессом и записывает итоговый URL с кодом в файл."""
import threading
import time


def available():
    try:
        import importlib.util
        return importlib.util.find_spec("webview") is not None
    except Exception:  # noqa: BLE001
        return False


def run(url, out_file, redirect_prefix="https://login.live.com/oauth20_desktop.srf"):
    import webview  # type: ignore

    done = threading.Event()

    def check(window):
        try:
            cur = window.get_current_url() or ""
        except Exception:  # noqa: BLE001
            return
        if cur.startswith(redirect_prefix) and not done.is_set():
            done.set()
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(cur)
            window.destroy()

    window = webview.create_window("Вход в Microsoft", url, width=520, height=700)
    window.events.loaded += lambda *a: check(window)
    window.events.closed += lambda *a: done.set()

    def poll(w):
        while not done.is_set():
            time.sleep(0.5)
            check(w)

    webview.start(poll, window, private_mode=True)
