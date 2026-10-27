"""Диалоги лаунчера: общий каркас окна и вход через Microsoft."""
import os
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
import tkinter as tk

from . import auth
from .app_util import log_to_file
from .ui_kit import C, PillButton, make_entry

class Dialog(tk.Toplevel):
    def __init__(self, app, title, heading, sub=None, width=460):
        super().__init__(app.root)
        self.app = app
        px = app.px
        self.title(title)
        self.configure(bg=C["panel"])
        self.resizable(False, False)
        self.transient(app.root)
        self.f = tk.Frame(self, bg=C["panel"], width=px(width))
        self.f.pack(fill="both", expand=True, padx=px(28), pady=px(24))
        top = tk.Frame(self.f, bg=C["panel"])
        top.pack(fill="x")
        tk.Label(top, image=app.A.logo_badge(), bg=C["panel"]).pack(side="left")
        tx = tk.Frame(top, bg=C["panel"])
        tx.pack(side="left", padx=px(14))
        tk.Label(tx, text=heading, bg=C["panel"], fg=C["fg"], font=app.F.t(15)).pack(anchor="w")
        if sub:
            tk.Label(tx, text=sub, bg=C["panel"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        tk.Frame(self.f, bg=C["panel"], height=px(16)).pack()

    def center(self):
        self.update_idletasks()
        p = self.app.root
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def btn(self, parent, text, cmd, style="dark"):
        return PillButton(parent, self.app.A, text, command=cmd, style=style, font=self.app.F.b(10, "bold"),
                          bg=C["panel"])


class MsLoginDialog(Dialog):
    """Вход Microsoft: встроенное окно (pywebview), браузер + ссылка или свой Azure Client ID."""

    def __init__(self, app, client_id):
        super().__init__(app, "Вход через Microsoft", "Вход через Microsoft", "лицензионный аккаунт Minecraft")
        self.client_id = client_id
        self.done = False
        self.working = False
        self.cancel = threading.Event()
        if client_id:
            self._build_azure()
        else:
            self._build_live()
        self.state_lbl = tk.Label(self.f, text="", bg=C["panel"], fg=C["muted"], font=app.F.b(9),
                                  wraplength=app.px(470), justify="left")
        self.state_lbl.pack(anchor="w", pady=(app.px(12), 0))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.center()

    def _state(self, text, color=None):
        self.state_lbl.configure(text=text, fg=color or C["muted"])

    def _steps(self, steps):
        px = self.app.px
        for i, s in enumerate(steps, 1):
            r = tk.Frame(self.f, bg=C["panel"])
            r.pack(fill="x", pady=px(3))
            tk.Label(r, text=str(i), bg=C["sel"], fg=C["accent"], font=self.app.F.b(9, "bold"), width=2).pack(
                side="left", anchor="n")
            tk.Label(r, text=s, bg=C["panel"], fg=C["fg"], font=self.app.F.b(10), justify="left",
                     wraplength=px(440)).pack(side="left", padx=px(10))

    def _build_azure(self):
        self._steps(["Откроется браузер — войдите в аккаунт Microsoft.",
                     "После входа лаунчер сам получит данные и проверит лицензию."])
        bf = tk.Frame(self.f, bg=C["panel"])
        bf.pack(fill="x", pady=(self.app.px(14), 0))
        self.btn(bf, "Отмена", self._close).pack(side="right")
        self.btn(bf, "Войти по ссылке", self._open_link_login).pack(side="right", padx=self.app.px(8))
        login = auth.AzureLogin(self.client_id)
        webbrowser.open(login.url)
        self.after(50, lambda: self._state("Ожидание входа в браузере..."))

        def work():
            try:
                tok = login.wait(300, self.cancel)
                self.app.ui(self._state, "Проверяю лицензию Minecraft...")
                acc = auth.complete_login(tok, "azure", self.client_id)
                self.app.ui(self._success, acc)
            except Exception as e:  # noqa: BLE001
                log_to_file(traceback.format_exc())
                if not self.cancel.is_set():
                    self.app.ui(self._fail, e)
        threading.Thread(target=work, daemon=True).start()

    def _open_link_login(self):
        """Запасной вход (если не открывается браузер или localhost занят): копирование ссылки."""
        self._close()
        MsLoginDialog(self.app, "")

    def _build_live(self):
        from . import ms_webview
        px = self.app.px
        if ms_webview.available():
            self.btn(self.f, "Войти во встроенном окне", self._webview_login, "accent").pack(anchor="w")
            tk.Label(self.f, text="или через браузер:", bg=C["panel"], fg=C["muted"], font=self.app.F.b(9)).pack(
                anchor="w", pady=(px(14), px(6)))
        self._steps(["Нажмите «Открыть страницу входа» и войдите в аккаунт Microsoft.",
                     "После входа браузер покажет пустую страницу — так и должно быть.",
                     "Скопируйте её адрес (Ctrl+L, Ctrl+C) — лаунчер сам заметит ссылку. "
                     "Или вставьте её в поле ниже."])
        self.btn(self.f, "Открыть страницу входа", lambda: webbrowser.open(auth.live_auth_url()), "sel").pack(
            anchor="w", pady=(px(12), px(10)))
        self.url_var = tk.StringVar()
        ef, _ = make_entry(self.f, self.app.A, self.url_var, width=50, font=self.app.F.b(9))
        ef.pack(fill="x")
        bf = tk.Frame(self.f, bg=C["panel"])
        bf.pack(fill="x", pady=(px(14), 0))
        self.btn(bf, "Войти", self._submit_url, "accent").pack(side="right")
        self.btn(bf, "Отмена", self._close).pack(side="right", padx=px(8))
        self.bind("<Return>", lambda ev: self._submit_url())
        try:
            self._last_clip = self.clipboard_get()
        except tk.TclError:
            self._last_clip = None
        self.after(800, self._watch_clipboard)

    def _watch_clipboard(self):
        if self.done or not self.winfo_exists():
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = None
        if clip and clip != self._last_clip and "oauth20_desktop.srf" in clip and "code=" in clip \
                and not self.working:
            self._last_clip = clip
            self.url_var.set(clip.strip())
            self._submit_url()
        self.after(800, self._watch_clipboard)

    def _webview_login(self):
        if self.working:
            return
        self._state("Открываю окно входа...")
        out = os.path.join(tempfile.gettempdir(), "zelees_ms_%d.txt" % os.getpid())
        try:
            os.remove(out)
        except OSError:
            pass
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--ms-webview", auth.live_auth_url(), out]
        else:
            main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
            cmd = [sys.executable, main_py, "--ms-webview", auth.live_auth_url(), out]

        def work():
            try:
                subprocess.run(cmd, timeout=900)
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._fail, e)
                return
            url = None
            try:
                with open(out, "r", encoding="utf-8") as fh:
                    url = fh.read().strip()
                os.remove(out)
            except OSError:
                pass
            if url:
                self.app.ui(self.url_var.set, url)
                self.app.ui(self._submit_url)
            else:
                self.app.ui(self._state, "Окно входа закрыто без результата — можно войти через браузер.")
        threading.Thread(target=work, daemon=True).start()

    def _submit_url(self):
        if self.working:
            return
        try:
            code = auth.extract_code(self.url_var.get())
        except auth.AuthError as e:
            self._state(str(e), C["err"])
            return
        self.working = True
        self._state("Вход в Xbox Live и проверка лицензии Minecraft...")

        def work():
            try:
                tok = auth.live_exchange_code(code)
                acc = auth.complete_login(tok, "live")
                self.app.ui(self._success, acc)
            except Exception as e:  # noqa: BLE001
                log_to_file(traceback.format_exc())
                self.app.ui(self._fail, e)
        threading.Thread(target=work, daemon=True).start()

    def _success(self, acc):
        self.done = True
        self.app.ms_account_added(acc)
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _fail(self, e):
        self.working = False
        if self.winfo_exists():
            self._state("Ошибка: %s" % e, C["err"])

    def _close(self):
        self.cancel.set()
        self.done = True
        self.destroy()
