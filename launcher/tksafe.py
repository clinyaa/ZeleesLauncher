"""Защита Tkinter от удаления объектов из фоновых потоков.

Проблема: PhotoImage / StringVar / BooleanVar и т.п. при удалении вызывают Tcl
(«image delete», «unset»). Сборщик мусора Python запускается в том потоке,
который в этот момент выделяет память, — во время загрузки это поток скачивания.
Вызов Tcl не из главного потока вешает и роняет лаунчер (особенно если в это время
кликать по окну или перетаскивать его).

Решение:
1. автоматический сборщик мусора выключен, мусор собирается периодически в главном потоке;
2. если объект Tk всё-таки удаляется в фоновом потоке, удаление откладывается
   и выполняется в главном потоке.
"""
import collections
import gc
import threading
import tkinter as tk

_MAIN = threading.main_thread()
_pending = collections.deque()  # потокобезопасна для append/popleft
_installed = False


def _on_main():
    return threading.current_thread() is _MAIN


def _patch(cls, attr="__del__"):
    orig = cls.__dict__.get(attr)
    if orig is None or getattr(orig, "_tksafe", False):
        return

    def safe_del(self, _orig=orig):
        if _on_main():
            try:
                _orig(self)
            except Exception:  # noqa: BLE001
                pass
        else:
            # объект оживает в очереди и будет удалён в главном потоке
            _pending.append((_orig, self))

    safe_del._tksafe = True
    setattr(cls, attr, safe_del)


def _drain():
    n = 0
    while _pending and n < 2000:
        fn, obj = _pending.popleft()
        try:
            fn(obj)
        except Exception:  # noqa: BLE001
            pass
        n += 1


def install(root, interval_ms=1500):
    """Вызывать один раз из главного потока сразу после создания tk.Tk()."""
    global _installed
    if _installed:
        return
    _installed = True
    _patch(tk.Image)
    _patch(tk.Variable)
    gc.disable()

    def tick():
        try:
            gc.collect()
            _drain()
        finally:
            try:
                root.after(interval_ms, tick)
            except tk.TclError:
                pass

    root.after(interval_ms, tick)
