"""Автообновление самого лаунчера."""
import sys
import threading
import traceback
from tkinter import messagebox

from . import updater
from .app_util import log_to_file
from .config import APP_NAME, APP_VERSION
from .ui_kit import C

class UpdateMixin:
    """Автообновление самого лаунчера."""

    # ------------------------------------------------------------------ обновления лаунчера
    def _check_updates(self, manual=False):
        if not updater.enabled():
            if manual:
                messagebox.showinfo(APP_NAME, "Обновления работают в установленной версии лаунчера.\n\n"
                                    "Запуск из исходников обновлять не нужно — вы и так запускаете свежий код."
                                    if not getattr(sys, "frozen", False) else
                                    "Адрес обновлений не задан (UPDATE_URL в launcher/config.py).",
                                    parent=self.root)
            return

        beta = bool(self.s_beta.get())  # переключатель в «Опциях», даже если ещё не нажали «Сохранить»

        def work():
            try:
                m = updater.check(beta=beta)
            except Exception as e:  # noqa: BLE001
                if manual:
                    self.ui(self.status, "Не удалось проверить обновления: %s" % e, C["warn"])
                return
            self.ui(self._update_found, m, manual)
        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, m, manual):
        self.update_manifest = m
        if m:
            beta = " (бета)" if m.get("channel") == "beta" else ""
            self.upd_pill.config(text="Доступно обновление %s%s" % (m["version"], beta))
            self.upd_pill.set_visible(True)
            self._layout_top()
            if manual:
                self._update_clicked()
        elif manual:
            messagebox.showinfo(APP_NAME, "У вас последняя версия: %s" % APP_VERSION, parent=self.root)

    def _update_clicked(self):
        m = self.update_manifest
        if not m or self.busy:
            return
        notes = (m.get("notes") or "").strip()
        beta = " (бета-версия)" if m.get("channel") == "beta" else ""
        if not messagebox.askyesno(APP_NAME, "Обновить %s до версии %s%s?%s" % (
                APP_NAME, m["version"], beta, ("\n\nЧто нового:\n" + notes) if notes else ""), parent=self.root):
            return
        self.show_page("home")
        self._set_busy(True)

        def work():
            try:
                kind, path = updater.install(m, self.progress)
                self.ui(self._update_ready, kind, path, m["version"])
            except Exception as e:  # noqa: BLE001
                log_to_file(traceback.format_exc())
                self.ui(self._error, e)
            finally:
                self.ui(self._set_busy, False)
        threading.Thread(target=work, daemon=True).start()

    def _update_ready(self, kind, path, ver):
        if kind == "installer":
            self.status("Устанавливаю версию %s..." % ver, C["accent"])
            updater.run_installer(path)
            self._on_close()
            return
        self.upd_pill.set_visible(False)
        self._layout_top()
        self.status("Обновление %s готово" % ver, C["accent"])
        if messagebox.askyesno(APP_NAME, "Обновление %s скачано. Перезапустить лаунчер сейчас?" % ver,
                               parent=self.root):
            updater.restart()
            self._on_close()
