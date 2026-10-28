"""Графический интерфейс ZeleesLauncher (tkinter + собственные пиксельные картинки).

Окно App собрано из частей: страницы (app_pages), версии/Java/настройки (app_versions),
аккаунты и Premium (app_accounts), запуск игры (app_launch), автообновление (app_update).
Здесь — ядро: очередь вызовов в окне, стили, каркас и навигация.
"""
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from .app_accounts import AccountsMixin
from .app_launch import LaunchMixin
from .app_pages import PagesMixin
from .app_update import UpdateMixin
from .app_util import BASE_H, BASE_W, SIDE_W, log_to_file, open_path
from .app_versions import VersionsMixin
from . import discord_rpc, fileopen
from .config import APP_NAME, Accounts, Settings, ensure_dirs
from .dialogs import Dialog  # noqa: F401 - для старого кода, импортировавшего Dialog из gui
from .mods_page import ModsPage
from .premium import Premium
from .servers_page import ServersPage
from .skin_page import SkinPage
from .versions import Manifest
from .window import TB_H, TitleBar, make_borderless, set_app_id
from .ui_kit import C, Assets, Fonts, PillButton

__all__ = ["App", "Dialog", "run"]

# =====================================================================================
class App(PagesMixin, VersionsMixin, AccountsMixin, LaunchMixin, UpdateMixin):
    def __init__(self, root, scale):
        ensure_dirs()
        self.root = root
        self.A = Assets(scale)
        self.F = Fonts(root)
        self.px = self.A.px
        self.settings = Settings()
        self.accounts = Accounts()
        self.premium = Premium()
        self.discord = discord_rpc.Presence(log=log_to_file)
        self._premium_syncing = set()
        self.q = queue.Queue()
        self.manifest = Manifest(None)
        self.javas = []
        self.req_cache = {}
        self.busy = False
        self.closed = False
        self.cancel = threading.Event()
        self.proc = None
        self._progress = None
        self._progress_lock = threading.Lock()
        self._heads = {}
        self._skin_grids = {}
        self.selected_vid = self.settings.get("last_version") or None
        self.page = None

        W, H = self.px(BASE_W), self.px(BASE_H)
        self.W, self.H = W, H
        self.TB = self.px(TB_H)
        self.CW = W - self.px(SIDE_W)
        self.borderless = False
        self.update_manifest = None
        root.title(APP_NAME)
        root.geometry("%dx%d" % (W, H + self.TB))
        root.resizable(False, False)
        root.configure(bg=C["bg"])
        try:
            root.iconphoto(True, self.A.logo_app())
        except tk.TclError:
            pass
        self._style()
        self._build()
        self._refresh_accounts()
        self.show_page("home")
        root.after(60, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.status("Загрузка списка версий...")
        self.bg(self._initial_load)

    # ------------------------------------------------------------------ инфраструктура
    def ui(self, fn, *a):
        self.q.put((fn, a))

    def bg(self, fn, *a):
        def run():
            try:
                fn(*a)
            except Exception as e:  # noqa: BLE001
                log_to_file(traceback.format_exc())
                self.ui(self._error, e)
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            for _ in range(500):
                fn, a = self.q.get_nowait()
                try:
                    fn(*a)
                except Exception:  # noqa: BLE001
                    log_to_file(traceback.format_exc())
                if self.closed:
                    return  # окно закрыто (например, перезапуск после обновления) — остальные вызовы не нужны
        except queue.Empty:
            pass
        with self._progress_lock:
            p, self._progress = self._progress, None
        if p:
            done, total, text = p
            self._set_bar(done / float(max(1, total)))
            self.status(text)
        self.root.after(60, self._poll)

    def progress(self, done, total, text):
        with self._progress_lock:
            self._progress = (done, total, text)

    def status(self, text, color=None):
        self.hc.itemconfigure(self.h_status, text=text, fill=color or C["muted"])

    def _set_bar(self, frac):
        if frac is None:
            self.hc.itemconfigure("bar", state="hidden")
            return
        self.hc.itemconfigure("bar", state="normal")
        x0, y0, x1, y1 = self.hc.coords(self.h_track)
        self.hc.coords(self.h_fill, x0, y0, x0 + (x1 - x0) * max(0.0, min(1.0, frac)), y1)
        self.hc.itemconfigure(self.h_pct, text="%d%%" % int(frac * 100))

    def open_file(self, path):
        """Файл, открытый двойным кликом (или переданный из второго запуска лаунчера)."""
        if self.closed:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.mods_page.import_mrpack(path)

    def _error(self, e):
        self.status("Ошибка: %s" % e, C["err"])
        messagebox.showerror(APP_NAME, str(e), parent=self.root)

    # ------------------------------------------------------------------ стили ttk
    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        px = self.px
        body = self.F.b(10)
        self.root.option_add("*Font", body)
        self.root.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["sel"])
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        s.configure(".", background=C["bg"], foreground=C["fg"], fieldbackground=C["panel2"],
                    bordercolor=C["line"], lightcolor=C["panel2"], darkcolor=C["panel2"],
                    troughcolor=C["bg"], focuscolor=C["accent"], font=body)
        s.configure("Treeview", background=C["bg"], fieldbackground=C["bg"], foreground=C["fg"],
                    rowheight=px(36), borderwidth=0, font=self.F.b(10))
        s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", "white")])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Treeview.Heading", background=C["bg"], foreground=C["dim"], borderwidth=0, relief="flat",
                    font=self.F.b(9, "bold"), padding=(px(6), px(8)))
        s.map("Treeview.Heading", background=[("active", C["bg"])], foreground=[("active", C["muted"])])
        s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["bg"], bordercolor=C["bg"],
                    arrowcolor=C["bg"], lightcolor=C["panel2"], darkcolor=C["panel2"], gripcount=0,
                    arrowsize=px(4), width=px(8))
        s.map("Vertical.TScrollbar", background=[("active", C["line"])])
        s.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"], foreground=C["fg"],
                    arrowcolor=C["muted"], bordercolor=C["line"], padding=px(6))
        s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["fg"])],
              selectbackground=[("readonly", C["panel2"])], selectforeground=[("readonly", C["fg"])],
              bordercolor=[("focus", C["accent_d"])])

    # ------------------------------------------------------------------ каркас
    def _build(self):
        px = self.px
        # боковая панель
        self.titlebar = TitleBar(self, self.W)
        self.side = tk.Canvas(self.root, width=px(SIDE_W), height=self.H, bg=C["side"], highlightthickness=0, bd=0)
        self.side.place(x=0, y=self.TB)
        self.side.create_line(px(SIDE_W) - 1, 0, px(SIDE_W) - 1, self.H, fill="#16181e")
        self.nav = {}
        items = [("home", "play", "Играть"), ("versions", "versions", "Версии"), ("mods", "mods", "Моды"),
                 ("servers", "servers", "Сервера"), ("skin", "skin", "Скин"), ("java", "java", "Java"),
                 ("settings", "settings", "Опции"), ("console", "console", "Консоль")]
        y = px(28)
        for key, icon, label in items:
            self._nav_item(key, icon, label, y)
            y += px(66)
        self._nav_item("folder", "folder", "Папка", self.H - px(70), command=lambda: open_path(self._game_root()))

        # страницы
        self.pages = {}
        self.content = tk.Frame(self.root, bg=C["bg"], width=self.CW, height=self.H)
        self.content.place(x=px(SIDE_W), y=self.TB)
        self._build_home()
        self._build_versions()
        self.mods_page = ModsPage(self)
        self.servers_page = ServersPage(self)
        self.skin_page = SkinPage(self)
        self._build_java()
        self._build_settings()
        self._build_console()

    def _nav_item(self, key, icon, label, y, command=None):
        px, cv = self.px, self.side
        tag = "nav_" + key
        cx = px(SIDE_W) // 2
        bar = cv.create_rectangle(0, y - px(4), px(3), y + px(46), fill=C["accent"], width=0, state="hidden",
                                  tags=(tag,))
        img = cv.create_image(cx, y + px(14), image=self.A.icon(icon), anchor="center", tags=(tag,))
        txt = cv.create_text(cx, y + px(38), text=label, fill=C["dim"], font=self.F.b(8, "bold"), tags=(tag,))
        hit = cv.create_rectangle(0, y - px(6), px(SIDE_W) - 2, y + px(50), fill="", outline="", tags=(tag,))
        cv.tag_raise(hit)
        self.nav[key] = dict(bar=bar, img=img, txt=txt, icon=icon)
        cmd = command or (lambda k=key: self.show_page(k))
        cv.tag_bind(tag, "<ButtonRelease-1>", lambda e: cmd())
        cv.tag_bind(tag, "<Enter>", lambda e: self._nav_hover(key, True))
        cv.tag_bind(tag, "<Leave>", lambda e: self._nav_hover(key, False))

    def _nav_hover(self, key, on):
        self.side.configure(cursor="hand2" if on else "")
        if key == self.page:
            return
        n = self.nav[key]
        self.side.itemconfigure(n["img"], image=self.A.icon(n["icon"], "_w" if on else ""))
        self.side.itemconfigure(n["txt"], fill=C["fg"] if on else C["dim"])

    def show_page(self, key):
        if self.page == "skin" and key != "skin":
            self.skin_page.on_hide()
        self.page = key
        for k, n in self.nav.items():
            active = k == key
            self.side.itemconfigure(n["bar"], state="normal" if active else "hidden")
            self.side.itemconfigure(n["img"], image=self.A.icon(n["icon"], "_on" if active else ""))
            self.side.itemconfigure(n["txt"], fill=C["fg"] if active else C["dim"])
        for k, fr in self.pages.items():
            if k == key:
                fr.place(x=0, y=0, width=self.CW, height=self.H)
                fr.tkraise()
            else:
                fr.place_forget()
        if key == "versions":
            self.tree.focus_set()
        if key == "mods":
            self.mods_page.on_show()
        if key == "servers":
            self.servers_page.on_show()
        if key == "skin":
            self.skin_page.on_show()

    def _page(self, key, title, subtitle):
        px = self.px
        fr = tk.Frame(self.content, bg=C["bg"])
        self.pages[key] = fr
        head = tk.Frame(fr, bg=C["bg"])
        head.pack(fill="x", padx=px(44), pady=(px(38), px(18)))
        tk.Label(head, text=title, bg=C["bg"], fg=C["fg"], font=self.F.t(24)).pack(anchor="w")
        tk.Label(head, text=subtitle, bg=C["bg"], fg=C["muted"], font=self.F.b(10)).pack(anchor="w", pady=(px(2), 0))
        body = tk.Frame(fr, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=px(44), pady=(0, px(32)))
        return body

    def _btn(self, parent, text, cmd, style="dark", size="md", **kw):
        return PillButton(parent, self.A, text, command=cmd, style=style, size=size,
                          font=self.F.b(10, "bold"), **kw)

    def _label(self, parent, text, muted=False, size=10, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=C["muted"] if muted else C["fg"],
                        font=self.F.b(size, "bold" if bold else "normal"), **kw)

    def _on_close(self):
        try:
            self._collect_settings()
        except Exception:  # noqa: BLE001
            pass
        self.cancel.set()
        self.closed = True
        self.root.destroy()

# =====================================================================================
def pick_scale(root):
    """Масштаб интерфейса под DPI экрана (шаг 0.25), чтобы пиксельная графика оставалась чёткой."""
    try:
        dpi = root.winfo_fpixels("1i")
    except tk.TclError:
        dpi = 96.0
    s = max(1.0, min(2.0, round(dpi / 96.0 * 4) / 4.0))
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    while s > 1.0 and (BASE_W * s > sw * 0.96 or (BASE_H + TB_H) * s > sh * 0.90):
        s -= 0.25
    return s


def run():
    path = fileopen.file_arg()
    if path and fileopen.send(path):
        return  # лаунчер уже открыт — модпак откроется в нём
    set_app_id()
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.withdraw()
    from . import tksafe
    tksafe.install(root)  # объекты Tk удаляются только в главном потоке (иначе вылет при кликах во время загрузки)
    scale = float(os.environ.get("ZELEES_SCALE", "0")) or pick_scale(root)
    root.tk.call("tk", "scaling", scale * 96.0 / 72.0)
    try:
        app = App(root, scale)
    except Exception:  # noqa: BLE001
        log_to_file(traceback.format_exc())
        raise
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = max(0, (root.winfo_screenheight() - root.winfo_height()) // 2 - 20)
    root.geometry("+%d+%d" % (x, y))
    root.deiconify()
    root.update()
    app.borderless = make_borderless(root)
    sys._zelees_started = True  # окно открылось — обновление (если было) рабочее
    root.after(3000, app._check_updates)
    server = None
    try:
        server = fileopen.Server(lambda p: app.ui(app.open_file, p))
    except OSError:
        log_to_file(traceback.format_exc())
    app.bg(fileopen.register)
    if path:
        root.after(1500, app.open_file, path)
    try:
        root.mainloop()
    finally:
        if server:
            server.close()
