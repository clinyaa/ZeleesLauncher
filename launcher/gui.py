"""Графический интерфейс ZeleesLauncher (tkinter + собственные пиксельные картинки)."""
import base64
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont

from . import auth, java as javamod, net
from .config import APP_NAME, APP_VERSION, CACHE_DIR, DATA_DIR, RUNTIME_DIR, Accounts, Settings, ensure_dirs
from .installer import install_version
from .launch import build_command, instance_dir, mask_cmd, start_process
from .mods_page import ModsPage
from .premium import Premium
from .servers_page import PremiumDialog, ServersPage
from .skin_page import SkinPage
from . import updater
from .window import TB_H, TitleBar, make_borderless, set_app_id
from .ui_kit import (C, Assets, CPill, Fonts, GoldBadge, PillButton, Slider, Switch, face_photo, head_url,
                     make_entry, offline_face)
from .versions import (TYPE_NAMES, Manifest, fetch_manifest, is_installed, local_versions, required_java,
                       resolve_version)

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


# =====================================================================================
class App:
    def __init__(self, root, scale):
        ensure_dirs()
        self.root = root
        self.A = Assets(scale)
        self.F = Fonts(root)
        self.px = self.A.px
        self.settings = Settings()
        self.accounts = Accounts()
        self.premium = Premium()
        self._premium_syncing = set()
        self.q = queue.Queue()
        self.manifest = Manifest(None)
        self.javas = []
        self.req_cache = {}
        self.busy = False
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

    # ------------------------------------------------------------------ главная
    def _build_home(self):
        px, A = self.px, self.A
        fr = tk.Frame(self.content, bg=C["bg"])
        self.pages["home"] = fr
        cv = tk.Canvas(fr, width=self.CW, height=self.H, bg=C["bg"], highlightthickness=0, bd=0)
        cv.place(x=0, y=0)
        self.hc = cv
        cv.create_image(0, 0, image=A.hero(hero_kind()), anchor="nw")
        L, R, Hh = px(48), self.CW - px(48), self.H

        # плашка «доступно обновление» (скрыта, пока его нет)
        self.upd_pill = CPill(cv, A, L, px(24), "Доступно обновление", size="md", style="sel",
                              command=self._update_clicked, font=self.F.b(10, "bold"),
                              image=A.icon("download", "_on"), padx=14)
        self.upd_pill.set_visible(False)

        # аккаунт
        self.acc_pill = CPill(cv, A, R, px(24), "Войти", size="md", style="glass", anchor="ne",
                              command=self._account_menu, font=self.F.b(10, "bold"), fg="#ffffff",
                              image=A.icon("user", "_w"), padx=14)
        self.prem_badge = GoldBadge(cv, A, self.F.b(10, "bold"), command=self.open_premium)

        # информация о версии
        self.h_kicker = cv.create_text(L, Hh - px(238), text=spaced("MINECRAFT") + "   ·   JAVA EDITION",
                                       anchor="w", fill=C["accent"], font=self.F.b(9, "bold"))
        self.h_version = cv.create_text(L - px(3), Hh - px(196), text="—", anchor="w", fill="#ffffff",
                                        font=self.F.t(50, "bold"))
        self.h_meta = cv.create_text(L, Hh - px(148), text="", anchor="w", fill="#c3c8d0", font=self.F.b(11))
        self.h_jdot = cv.create_oval(L, Hh - px(122), L + px(8), Hh - px(114), fill=C["muted"], width=0)
        self.h_java = cv.create_text(L + px(16), Hh - px(118), text="", anchor="w", fill=C["muted"],
                                     font=self.F.b(10))

        # кнопки
        self.play_pill = CPill(cv, A, R, Hh - px(126), "ИГРАТЬ", size="lg", style="accent", anchor="se",
                               command=self._play_clicked, font=self.F.t(15, "bold"), minw=230)
        self.ver_pill = CPill(cv, A, R - px(230) - px(14), Hh - px(137), "Сменить версию", size="md",
                              style="glass", anchor="se", command=lambda: self.show_page("versions"),
                              font=self.F.b(10, "bold"), fg="#ffffff", image=A.icon("versions", "_w"), padx=16)

        # прогресс и статус
        ty = Hh - px(70)
        self.h_track = cv.create_rectangle(L, ty, R, ty + px(3), fill="#262a33", width=0, tags=("bar",))
        self.h_fill = cv.create_rectangle(L, ty, L, ty + px(3), fill=C["accent"], width=0, tags=("bar",))
        self.h_pct = cv.create_text(R, ty + px(20), text="", anchor="e", fill=C["muted"], font=self.F.b(9),
                                    tags=("bar",))
        self.h_status = cv.create_text(L, ty + px(20), text="", anchor="w", fill=C["muted"], font=self.F.b(9))
        self._set_bar(None)

    def _update_home(self):
        vid = self.selected_vid
        cv = self.hc
        if not vid:
            cv.itemconfigure(self.h_version, text="Выберите версию")
            cv.itemconfigure(self.h_meta, text="")
            return
        cv.itemconfigure(self.h_version, text=vid)
        entry = self.manifest.by_id.get(vid)
        root = self._game_root()
        parts = []
        if entry:
            parts.append(TYPE_NAMES.get(entry["type"], entry["type"]))
            parts.append(fmt_date(entry.get("releaseTime", "")))
        else:
            parts.append("Модифицированная / локальная")
        parts.append("установлена" if is_installed(root, vid) else "будет скачана при запуске")
        cv.itemconfigure(self.h_meta, text="   ·   ".join(p for p in parts if p))
        if entry and entry.get("id") == self.manifest.latest.get("release"):
            cv.itemconfigure(self.h_kicker, text=spaced("MINECRAFT") + "   ·   ПОСЛЕДНИЙ РЕЛИЗ")
        else:
            cv.itemconfigure(self.h_kicker, text=spaced("MINECRAFT") + "   ·   JAVA EDITION")

    def _set_java_line(self, text, color):
        self.hc.itemconfigure(self.h_java, text=text, fill=color if color != C["muted"] else "#c3c8d0")
        self.hc.itemconfigure(self.h_jdot, fill=color)
        if hasattr(self, "v_java"):
            self.v_java.configure(text=text, fg=color)

    # ------------------------------------------------------------------ версии
    def _build_versions(self):
        px = self.px
        body = self._page("versions", "Версии", "Все версии Minecraft: Java Edition — от самых первых до свежих снапшотов")
        bar = tk.Frame(body, bg=C["bg"])
        bar.pack(fill="x", pady=(0, px(14)))
        self.search_var = tk.StringVar()
        ef, e = make_entry(bar, self.A, self.search_var, width=22, font=self.F.b(10))
        ef.pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._fill_versions())
        self.flt = {}
        self.flt_btn = {}
        for key, text in (("show_release", "Релизы"), ("show_snapshot", "Снапшоты"), ("show_beta", "Beta"),
                          ("show_alpha", "Alpha"), ("show_installed_only", "Установленные")):
            v = tk.BooleanVar(value=bool(self.settings.get(key)))
            self.flt[key] = v
            b = self._btn(bar, text, lambda k=key: self._toggle_filter(k), style="sel" if v.get() else "dark",
                          size="md", padx=14)
            b.pack(side="left", padx=(px(8), 0))
            self.flt_btn[key] = b
        self._btn(bar, "Обновить", lambda: self.bg(self._initial_load), padx=14).pack(side="right")

        frame = tk.Frame(body, bg=C["bg"])
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("type", "date", "status"), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="  ВЕРСИЯ", anchor="w")
        self.tree.heading("type", text="ТИП", anchor="w")
        self.tree.heading("date", text="ДАТА ВЫХОДА", anchor="w")
        self.tree.heading("status", text="СТАТУС", anchor="w")
        self.tree.column("#0", width=px(300), anchor="w")
        self.tree.column("type", width=px(150), anchor="w")
        self.tree.column("date", width=px(170), anchor="w")
        self.tree.column("status", width=px(220), anchor="w")
        self.tree.tag_configure("installed", foreground=C["accent"])
        self.tree.tag_configure("odd", background="#101116")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=(px(6), 0))
        self.tree.bind("<<TreeviewSelect>>", self._on_version_selected)
        self.tree.bind("<Double-1>", lambda e: (self.show_page("home")))

        foot = tk.Frame(body, bg=C["bg"])
        foot.pack(fill="x", pady=(px(14), 0))
        self.v_sel = self._label(foot, "", size=12, bold=True)
        self.v_sel.pack(side="left")
        self.v_java = self._label(foot, "", muted=True)
        self.v_java.pack(side="left", padx=px(14))
        self._btn(foot, "Выбрать и вернуться", lambda: self.show_page("home"), style="accent").pack(side="right")
        self.install_btn = self._btn(foot, "Установить", self._install_clicked)
        self.install_btn.pack(side="right", padx=px(8))

    def _toggle_filter(self, key):
        v = self.flt[key]
        v.set(not v.get())
        self.flt_btn[key].config_pill(style="sel" if v.get() else "dark")
        self.settings.set(key, bool(v.get()))
        self._fill_versions()

    # ------------------------------------------------------------------ java
    def _build_java(self):
        px = self.px
        body = self._page("java", "Java", "Каждой версии Minecraft нужна своя Java — лаунчер подберёт или скачает её сам")
        card = tk.Frame(body, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x")
        inner = tk.Frame(card, bg=C["panel"])
        inner.pack(fill="x", padx=px(20), pady=px(16))
        self.java_auto = tk.BooleanVar(value=self.settings.get("java_mode") != "custom")
        Switch(inner, self.A, self.java_auto, command=self._java_mode_changed).pack(side="left")
        tx = tk.Frame(inner, bg=C["panel"])
        tx.pack(side="left", padx=px(16), fill="x", expand=True)
        self._label(tx, "Автоподбор Java", size=11, bold=True).pack(anchor="w")
        self._label(tx, "Для каждой версии берётся подходящая Java из найденных, а если её нет — скачивается "
                        "официальная сборка Mojang", muted=True, size=9, wraplength=px(700), justify="left").pack(anchor="w")

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(px(14), 0))
        self._label(row, "Своя Java", bold=True).pack(side="left")
        self.java_path_var = tk.StringVar(value=self.settings.get("java_path"))
        ef, _ = make_entry(row, self.A, self.java_path_var, font=self.F.b(10))
        ef.pack(side="left", fill="x", expand=True, padx=px(12))
        self._btn(row, "Обзор", self._browse_java).pack(side="left")

        self._label(body, "До 1.16.5 — Java 8   ·   1.17 — Java 16   ·   1.18–1.20.4 — Java 17   ·   1.20.5+ — Java 21"
                          "   ·   новейшие — 21/25 (точно — из файла версии)", muted=True, size=9).pack(
            anchor="w", pady=(px(10), px(16)))

        self._label(body, spaced("НАЙДЕННЫЕ"), muted=True, size=8, bold=True).pack(anchor="w", pady=(0, px(4)))
        frame = tk.Frame(body, bg=C["bg"])
        frame.pack(fill="both", expand=True)
        self.jtree = ttk.Treeview(frame, columns=("ver", "arch", "src", "path"), show="headings", height=6)
        for c, txt, w in (("ver", "ВЕРСИЯ", 130), ("arch", "РАЗРЯДНОСТЬ", 110), ("src", "ИСТОЧНИК", 170),
                          ("path", "ПУТЬ", 470)):
            self.jtree.heading(c, text=txt, anchor="w")
            self.jtree.column(c, width=px(w), anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.jtree.yview)
        self.jtree.configure(yscrollcommand=sb.set)
        self.jtree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=(px(6), 0))
        self.jtree.bind("<Double-1>", self._java_use_selected)

        btns = tk.Frame(body, bg=C["bg"])
        btns.pack(fill="x", pady=(px(14), 0))
        self._btn(btns, "Обновить", lambda: self.bg(self._scan_java)).pack(side="left")
        self._btn(btns, "Использовать выбранную", self._java_use_selected).pack(side="left", padx=px(8))
        self._btn(btns, "Скачать", self._download_runtime, style="accent").pack(side="right")
        self.rt_var = tk.StringVar(value="Скачать Java от Mojang…")
        self.rt_box = ttk.Combobox(btns, textvariable=self.rt_var, state="readonly", width=34, font=self.F.b(10))
        self.rt_box.pack(side="right", padx=px(8))
        self.rt_box.bind("<Button-1>", lambda e: self._load_runtimes())
        self._runtimes = []

    # ------------------------------------------------------------------ настройки
    def _build_settings(self):
        px = self.px
        body = self._page("settings", "Настройки", "Память, папки и параметры запуска")
        grid = tk.Frame(body, bg=C["bg"])
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self._srow = 0

        def row(title, hint=None):
            lf = tk.Frame(grid, bg=C["bg"])
            lf.grid(row=self._srow, column=0, sticky="nw", pady=px(5), padx=(0, px(24)))
            self._label(lf, title, bold=True).pack(anchor="w")
            if hint:
                self._label(lf, hint, muted=True, size=8).pack(anchor="w")
            cf = tk.Frame(grid, bg=C["bg"])
            cf.grid(row=self._srow, column=1, sticky="ew", pady=px(5))
            self._srow += 1
            return cf

        ram = total_ram_mb()
        self._ram_total = ram
        cf = row("Оперативная память", "сколько выделить игре")
        self.s_mem = tk.IntVar(value=int(self.settings.get("memory_max")))
        Slider(cf, self.A, self.s_mem, 1024, max(2048, ram - 1024), 256, width=420,
               command=lambda v: self._mem_changed()).pack(side="left")
        self.mem_lbl = self._label(cf, "", bold=True)
        self.mem_lbl.pack(side="left", padx=px(16))
        self._mem_changed()

        cf = row("Папка игры", "сохранения, моды, ресурспаки")
        self.s_game_dir = tk.StringVar(value=self.settings.get("game_dir"))
        ef, _ = make_entry(cf, self.A, self.s_game_dir, font=self.F.b(10))
        ef.pack(side="left", fill="x", expand=True)
        self._btn(cf, "Обзор", self._browse_game_dir).pack(side="left", padx=(px(8), 0))
        self._btn(cf, "Открыть", lambda: open_path(self._game_root())).pack(side="left", padx=(px(8), 0))

        cf = row("Окно игры", "ширина × высота")
        self.s_w = tk.StringVar(value=str(self.settings.get("width")))
        self.s_h = tk.StringVar(value=str(self.settings.get("height")))
        ef, _ = make_entry(cf, self.A, self.s_w, width=6, font=self.F.b(10))
        ef.pack(side="left")
        self._label(cf, "  ×  ", muted=True).pack(side="left")
        ef, _ = make_entry(cf, self.A, self.s_h, width=6, font=self.F.b(10))
        ef.pack(side="left")
        self.s_full = tk.BooleanVar(value=bool(self.settings.get("fullscreen")))
        Switch(cf, self.A, self.s_full).pack(side="left", padx=(px(24), px(10)))
        self._label(cf, "Полный экран").pack(side="left")

        cf = row("Аргументы JVM", "для опытных")
        self.s_jvm = tk.StringVar(value=self.settings.get("jvm_args"))
        ef, _ = make_entry(cf, self.A, self.s_jvm, font=self.F.b(10))
        ef.pack(side="left", fill="x", expand=True)

        cf = row("Загрузка", "одновременных потоков")
        self.s_threads = tk.StringVar(value=str(self.settings.get("download_threads")))
        ef, _ = make_entry(cf, self.A, self.s_threads, width=5, font=self.F.b(10))
        ef.pack(side="left")

        cf = row("Microsoft Client ID", "пусто = встроенный ZeleesLauncher, менять не нужно")
        self.s_client = tk.StringVar(value=self.settings.get("ms_client_id"))
        ef, _ = make_entry(cf, self.A, self.s_client, font=self.F.b(10))
        ef.pack(side="left", fill="x", expand=True)

        cf = row("CurseForge API-ключ", "необязательно, для каталога CurseForge")
        self.s_cf = tk.StringVar(value=self.settings.get("curseforge_key"))
        ef, _ = make_entry(cf, self.A, self.s_cf, font=self.F.b(10), show="•")
        ef.pack(side="left", fill="x", expand=True)

        cf = row("Отдельные папки", "свой мир у каждой версии (сборки с модами — всегда)")
        self.s_sep = tk.BooleanVar(value=bool(self.settings.get("separate_instances")))
        Switch(cf, self.A, self.s_sep).pack(side="left")

        cf = row("Скрывать лаунчер", "пока запущена игра")
        self.s_hide = tk.BooleanVar(value=bool(self.settings.get("hide_on_launch")))
        Switch(cf, self.A, self.s_hide).pack(side="left")

        foot = tk.Frame(body, bg=C["bg"])
        foot.pack(fill="x", side="bottom")
        self._btn(foot, "Сохранить", self._save_settings_clicked, style="accent", minw=150).pack(side="left")
        self._btn(foot, "Папка лаунчера", lambda: open_path(DATA_DIR)).pack(side="left", padx=px(8))
        self._btn(foot, "Проверить обновления", lambda: self._check_updates(manual=True)).pack(side="right")
        self._label(foot, "%s %s" % (APP_NAME, APP_VERSION), muted=True, size=9).pack(side="right", padx=px(12))
        self.s_saved = self._label(foot, "", muted=True)
        self.s_saved.pack(side="left", padx=px(10))

    # ------------------------------------------------------------------ консоль
    def _build_console(self):
        px = self.px
        body = self._page("console", "Консоль", "Журнал лаунчера и игры")
        frame = tk.Frame(body, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        frame.pack(fill="both", expand=True)
        self.console = tk.Text(frame, bg=C["panel"], fg="#c9ced8", insertbackground="white", relief="flat",
                               font=(self.F.mono, 9), wrap="word", state="disabled", padx=px(14), pady=px(10),
                               bd=0, highlightthickness=0, selectbackground=C["sel"])
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=sb.set)
        self.console.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.console.tag_configure("WARN", foreground=C["warn"])
        self.console.tag_configure("ERROR", foreground=C["err"])
        self.console.tag_configure("FATAL", foreground=C["err"])
        self.console.tag_configure("LAUNCHER", foreground=C["accent"])
        bf = tk.Frame(body, bg=C["bg"])
        bf.pack(fill="x", pady=(px(14), 0))
        self._btn(bf, "Очистить", self._console_clear).pack(side="left")
        self._btn(bf, "Копировать всё", self._console_copy).pack(side="left", padx=px(8))
        self._btn(bf, "Папка логов", lambda: open_path(os.path.join(self._current_game_dir(), "logs"))).pack(side="left")
        self.kill_btn = self._btn(bf, "Закрыть игру", self._kill_game, style="danger")
        self.kill_btn.pack(side="right")
        self.kill_btn.config_pill(enabled=False)

    def log(self, text, level="LAUNCHER"):
        self.console.configure(state="normal")
        tag = (level,) if level in ("WARN", "ERROR", "FATAL", "LAUNCHER") else ()
        if level == "LAUNCHER":
            text = "› " + text
        self.console.insert("end", text + "\n", tag)
        lines = int(self.console.index("end-1c").split(".")[0])
        if lines > 6000:
            self.console.delete("1.0", "%d.0" % (lines - 5000))
        self.console.configure(state="disabled")
        self.console.see("end")

    def _console_clear(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _console_copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.console.get("1.0", "end"))
        self.status("Лог скопирован в буфер обмена")

    # ------------------------------------------------------------------ загрузка данных
    def _initial_load(self):
        lp = os.path.join(self._game_root(), "launcher_profiles.json")
        if not os.path.isfile(lp):
            try:
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                with open(lp, "w", encoding="utf-8") as f:
                    f.write('{"profiles": {}, "selectedProfile": "", "authenticationDatabase": {}}')
            except OSError:
                pass
        m = fetch_manifest()
        self.ui(self._manifest_loaded, m)
        self._scan_java()

    def _manifest_loaded(self, m):
        self.manifest = m
        if m.offline:
            self.status("Нет связи с серверами Mojang — показаны сохранённые и установленные версии", C["warn"])
        else:
            lt = m.latest
            self.status("Доступно версий: %d   ·   релиз %s   ·   снапшот %s" % (
                len(m.versions), lt.get("release", "?"), lt.get("snapshot", "?")))
        if not self.selected_vid and m.latest.get("release"):
            self.selected_vid = m.latest["release"]
        self._fill_versions()
        self._update_home()
        if self.selected_vid and self.selected_vid not in self.req_cache:
            self.bg(self._fetch_requirement, self.selected_vid)

    def _scan_java(self):
        js = javamod.detect_javas(self._game_root())
        self.ui(self._javas_loaded, js)

    def _javas_loaded(self, js):
        self.javas = js
        self.jtree.delete(*self.jtree.get_children())
        for j in js:
            self.jtree.insert("", "end", iid=j.path, values=("Java " + j.version, "64 бит" if j.is64 else "32 бит",
                                                             j.source, j.path))
        if not js:
            self.jtree.insert("", "end", values=("—", "", "", "Java не найдена — нужная скачается автоматически"))
        self._update_java_label()

    # ------------------------------------------------------------------ список версий
    def _game_root(self):
        return os.path.abspath(self.s_game_dir.get().strip() or self.settings.get("game_dir"))

    def _current_game_dir(self):
        return instance_dir({"separate_instances": self.s_sep.get()}, self._game_root(), self.selected_vid or "")

    def _fill_versions(self):
        root = self._game_root()
        query = self.search_var.get().strip().lower()
        show = {"release": self.flt["show_release"].get(), "snapshot": self.flt["show_snapshot"].get(),
                "old_beta": self.flt["show_beta"].get(), "old_alpha": self.flt["show_alpha"].get()}
        only_inst = self.flt["show_installed_only"].get()
        rows = []
        for lv in local_versions(root):
            if lv["id"] in self.manifest.by_id:
                continue
            rows.append((lv["id"], "Модиф. / локальная", lv.get("releaseTime", ""), "local", lv.get("inheritsFrom")))
        for v in self.manifest.versions:
            if not show.get(v["type"], False) and not only_inst:
                continue
            rows.append((v["id"], TYPE_NAMES.get(v["type"], v["type"]), v.get("releaseTime", ""), v["type"], None))
        self.tree.delete(*self.tree.get_children())
        n = 0
        latest = self.manifest.latest.get("release")
        for vid, tname, date, vtype, parent in rows:
            if query and query not in vid.lower():
                continue
            inst = is_installed(root, vid)
            if only_inst and not inst:
                continue
            status = "●  Установлена" if inst else ""
            if vtype == "local" and parent:
                status = (status + "   " if status else "") + "на базе " + parent
            if vid == latest:
                tname += "  ·  последний"
            tags = []
            if inst:
                tags.append("installed")
            if n % 2:
                tags.append("odd")
            self.tree.insert("", "end", iid=vid, text="  " + vid, image=self.A.cube(TYPE_CUBE.get(vtype, "modded"), 20),
                             values=(tname, fmt_date(date), status), tags=tuple(tags))
            n += 1
        if self.selected_vid and self.tree.exists(self.selected_vid):
            self.tree.selection_set(self.selected_vid)
            self.tree.see(self.selected_vid)

    def _on_version_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        vid = sel[0]
        self.selected_vid = vid
        self.settings.set("last_version", vid)
        self.v_sel.configure(text=vid)
        self._update_home()
        self._update_java_label()
        if vid not in self.req_cache:
            self.bg(self._fetch_requirement, vid)

    def select_version(self, vid):
        self.selected_vid = vid
        self.settings.set("last_version", vid)
        self._fill_versions()
        self._update_home()
        self._update_java_label()
        if vid not in self.req_cache:
            self.bg(self._fetch_requirement, vid)

    def _build_created(self, vid):
        self.select_version(vid)
        self.status("Сборка %s готова — добавляйте моды и играйте" % vid, C["accent"])
        self.show_page("mods")
        self.mods_page.reload_builds(select=vid)

    def _fetch_requirement(self, vid):
        try:
            vdata = resolve_version(self._game_root(), vid, self.manifest)
            self.req_cache[vid] = required_java(vdata)
        except Exception as e:  # noqa: BLE001
            self.req_cache[vid] = None
            log_to_file("requirement %s: %s" % (vid, e))
        self.ui(self._update_java_label)

    def _update_java_label(self):
        vid = self.selected_vid
        if not vid:
            self._set_java_line("", C["muted"])
            return
        req = self.req_cache.get(vid, "pending")
        if req == "pending":
            self._set_java_line("Проверяю, какая Java нужна...", C["muted"])
            return
        if req is None:
            self._set_java_line("Не удалось получить данные версии — нет интернета?", C["warn"])
            return
        comp, major = req
        if not self.java_auto.get():
            path = self.java_path_var.get().strip()
            j = javamod.probe(path, "custom") if path else None
            if not j:
                self._set_java_line("Нужна Java %d   ·   путь к своей Java неверен" % major, C["err"])
            elif javamod.is_compatible(j, major):
                self._set_java_line("Java %d   ·   ваша Java %s" % (major, j.version), C["accent"])
            else:
                self._set_java_line("Нужна Java %d, выбрана %s — включите автоподбор" % (major, j.version), C["warn"])
            return
        j = javamod.pick_java(self.javas, major)
        if j:
            self._set_java_line("Java %d готова   ·   %s, %s" % (major, j.version, j.source), C["accent"])
        else:
            self._set_java_line("Нужна Java %d   ·   скачается автоматически при запуске" % major, C["warn"])

    # ------------------------------------------------------------------ Java вкладка
    def _java_mode_changed(self):
        self.settings.set("java_mode", "auto" if self.java_auto.get() else "custom")
        self._update_java_label()

    def _browse_java(self):
        p = filedialog.askopenfilename(title="Выберите java.exe / javaw.exe",
                                       filetypes=[("Java", "java.exe javaw.exe java"), ("Все файлы", "*.*")])
        if p:
            if os.path.basename(p).lower() == "javaw.exe":
                alt = os.path.join(os.path.dirname(p), "java.exe")
                if os.path.isfile(alt):
                    p = alt
            self._use_custom_java(os.path.normpath(p))

    def _use_custom_java(self, p):
        self.java_path_var.set(p)
        self.java_auto.set(False)
        self.settings.set("java_path", p, save=False)
        self.settings.set("java_mode", "custom")
        self._update_java_label()

    def _java_use_selected(self, _e=None):
        sel = self.jtree.selection()
        if sel and os.path.isfile(sel[0]):
            self._use_custom_java(sel[0])

    def _load_runtimes(self):
        if self._runtimes:
            return
        self.bg(lambda: self.ui(self._runtimes_loaded, javamod.available_runtimes()))

    def _runtimes_loaded(self, rts):
        self._runtimes = rts
        self.rt_box["values"] = ["Java %d  —  %s" % (m, name) for comp, name, m in rts]
        if rts:
            self.rt_box.current(len(rts) - 1)

    def _download_runtime(self):
        if not self._runtimes:
            self._load_runtimes()
            self.status("Загружаю список Java от Mojang — выберите версию и нажмите «Скачать»")
            return
        i = self.rt_box.current()
        if i < 0 or self.busy:
            return
        comp, _name, major = self._runtimes[i]

        def work():
            self.ui(self._set_busy, True)
            try:
                j = javamod.install_runtime(comp, major, self.settings.get("download_threads"),
                                            self.progress, self.cancel)
                self.ui(self.status, "Java %s установлена" % j.version, C["accent"])
                self._scan_java()
            finally:
                self.ui(self._set_busy, False)
        self.bg(work)

    # ------------------------------------------------------------------ настройки
    def _mem_changed(self):
        v = int(self.s_mem.get())
        self.mem_lbl.configure(text="%.1f ГБ   из %.0f" % (v / 1024.0, self._ram_total / 1024.0))

    def _browse_game_dir(self):
        p = filedialog.askdirectory(title="Папка игры", initialdir=self._game_root())
        if p:
            self.s_game_dir.set(os.path.normpath(p))

    def _collect_settings(self):
        s = self.settings
        try:
            s.set("game_dir", self._game_root(), save=False)
            s.set("memory_max", int(self.s_mem.get()), save=False)
            s.set("memory_min", min(1024, int(self.s_mem.get())), save=False)
            s.set("width", int(self.s_w.get()), save=False)
            s.set("height", int(self.s_h.get()), save=False)
            s.set("download_threads", max(1, min(64, int(self.s_threads.get()))), save=False)
        except (tk.TclError, ValueError):
            raise ValueError("Проверьте числовые поля в настройках")
        s.set("fullscreen", bool(self.s_full.get()), save=False)
        s.set("jvm_args", self.s_jvm.get().strip(), save=False)
        s.set("ms_client_id", self.s_client.get().strip(), save=False)
        s.set("curseforge_key", self.s_cf.get().strip(), save=False)
        s.set("separate_instances", bool(self.s_sep.get()), save=False)
        s.set("hide_on_launch", bool(self.s_hide.get()), save=False)
        s.set("java_mode", "auto" if self.java_auto.get() else "custom", save=False)
        s.set("java_path", self.java_path_var.get().strip(), save=False)
        s.save()
        return dict(s.data)

    def _save_settings_clicked(self):
        try:
            self._collect_settings()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        self.s_saved.configure(text="✓  Сохранено", fg=C["accent"])
        self.root.after(2500, lambda: self.s_saved.configure(text=""))
        self._fill_versions()
        self._update_home()
        self._update_java_label()

    # ------------------------------------------------------------------ аккаунты
    def _refresh_accounts(self, select_key=None):
        key = select_key or self.settings.get("last_account")
        acc = self.accounts.find(key) if key else None
        if not acc and self.accounts.items:
            acc = self.accounts.items[0]
        self.cur_acc_key = self.accounts.key(acc) if acc else None
        if self.cur_acc_key:
            self.settings.set("last_account", self.cur_acc_key)
        self._draw_account_pill()
        self.sync_premium()

    def set_skin_head(self, uuid, grid):
        """Голова для аватара берётся прямо из текущего скина."""
        self._skin_grids[uuid] = grid
        for k in [k for k in self._heads if isinstance(k, tuple) and uuid in k]:
            self._heads.pop(k, None)
        self._draw_account_pill()

    def _avatar(self, acc, size):
        if acc["id"] in self._skin_grids:
            key = ("skin", acc["id"], size)
            if key not in self._heads:
                self._heads[key] = face_photo(self._skin_grids[acc["id"]], size)
            return self._heads[key]
        img = self._heads.get((acc["id"], size))
        if img:
            return img
        self.bg(self._load_head, acc["id"], size)
        # пока голова скина загружается — пиксельное лицо-заглушка по нику
        key = ("off", acc.get("name"), size)
        if key not in self._heads:
            self._heads[key] = face_photo(offline_face(acc.get("name", "?")), size)
        return self._heads[key]

    def _load_head(self, uuid, size):
        if (uuid, size) in self._heads or ("loading", uuid, size) in self._heads:
            return
        self._heads[("loading", uuid, size)] = True
        path = os.path.join(CACHE_DIR, "heads", "%s_%d.png" % (uuid, size))
        data = None
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 86400 * 3:
                with open(path, "rb") as f:
                    data = f.read()
            else:
                _, data = net.request(head_url(uuid, size), timeout=10)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
        except Exception:  # noqa: BLE001
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
        if data:
            self.ui(self._head_loaded, uuid, size, data)

    def _head_loaded(self, uuid, size, data):
        try:
            self._heads[(uuid, size)] = tk.PhotoImage(data=base64.b64encode(data))
        except tk.TclError:
            return
        self._draw_account_pill()

    def _draw_account_pill(self):
        acc = self.accounts.find(self.cur_acc_key) if self.cur_acc_key else None
        if acc:
            img = self._avatar(acc, self.px(22) // 8 * 8 or 16)
            self.acc_pill.config(text="%s   ·   лицензия   ▾" % acc["name"], image=img)
        else:
            self.acc_pill.config(text="Войти через Microsoft   ▾", image=self.A.icon("user", "_w"))
        self._update_premium_badge()

    # ------------------------------------------------------------------ Premium
    def current_ms_account(self):
        """Текущий аккаунт, если он лицензионный (Premium привязан только к ним)."""
        acc = self.accounts.find(self.cur_acc_key) if self.cur_acc_key else None
        return acc if acc and acc.get("type") == "microsoft" else None

    def _update_premium_badge(self):
        acc = self.current_ms_account()
        if acc and self.premium.is_premium(acc["id"]):
            self.prem_badge.place(self.acc_pill.bx - self.px(10), self.acc_pill.by)
        else:
            self.prem_badge.hide()

    def open_premium(self):
        PremiumDialog(self)

    def ms_call(self, fn, done):
        """fn(acc) в фоне со свежим токеном лицензионного аккаунта; затем done(result, error) в окне."""
        acc = self.current_ms_account()
        if not acc:
            done(None, RuntimeError("Нужен лицензионный аккаунт Microsoft"))
            return
        key = self.accounts.key(acc)

        def work():
            res, err = None, None
            try:
                fresh = auth.refresh_account(acc)
                if fresh is not acc:
                    self.accounts.upsert(fresh)
                res = fn(fresh)
            except Exception as e:  # noqa: BLE001
                log_to_file("premium %s: %s" % (key, e))
                err = e
            self.ui(self._ms_call_done, done, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _ms_call_done(self, done, res, err):
        self._update_premium_badge()
        self.servers_page.on_premium_changed()
        done(res, err)

    def sync_premium(self, done=None):
        """Обновляет статус Premium текущего аккаунта с сайта."""
        acc = self.current_ms_account()
        if not acc:
            self._update_premium_badge()
            if done:
                done(None, None)
            return
        if acc["id"] in self._premium_syncing and not done:
            return
        self._premium_syncing.add(acc["id"])

        def finished(res, err, uid=acc["id"]):
            self._premium_syncing.discard(uid)
            if done:
                done(res, err)
        self.ms_call(self.premium.me, finished)

    def play_on_server(self, entry, st=None):
        """«Играть» на вкладке «Сервера»: запуск сразу с подключением к серверу."""
        import re
        if self.busy:
            self.status("Подождите — лаунчер уже занят", C["warn"])
            return
        vid = self.selected_vid
        found = re.findall(r"\b1\.\d+(?:\.\d+)?\b", (st or {}).get("version") or "")
        if len(set(found)) == 1 and found[0] in self.manifest.by_id and found[0] != vid:
            want = found[0]
            if not vid:
                vid = want
            else:
                ans = messagebox.askyesnocancel(
                    APP_NAME, "Сервер «%s» работает на версии %s, а выбрана %s.\n\nЗапустить %s?\n\n"
                              "Да — запустить %s     Нет — играть на %s" % (entry["name"], want, vid, want, want, vid),
                    parent=self.root)
                if ans is None:
                    return
                if ans:
                    vid = want
        if vid and vid != self.selected_vid:
            self.select_version(vid)
        host, port = entry["address"], int(entry.get("port") or 0)
        self.log("Подключение к серверу %s (%s%s)" % (entry["name"], host, ":%d" % port if port else ""))
        self.show_page("home")
        self._start(launch=True, server=(host, port))

    def _account_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=C["panel2"], fg=C["fg"], activebackground=C["sel"],
                    activeforeground="white", bd=0, relief="flat", font=self.F.b(10))
        for a in self.accounts.items:
            k = self.accounts.key(a)
            mark = "✓  " if k == self.cur_acc_key else "     "
            m.add_command(label="%s%s" % (mark, a["name"]), command=lambda kk=k: self._select_account(kk))
        if self.accounts.items:
            m.add_separator()
        m.add_command(label="     Войти через Microsoft…", command=self._add_microsoft)
        m.add_separator()
        m.add_command(label="★   Premium…", command=self.open_premium, foreground=C["gold"],
                      activeforeground=C["gold"])
        if self.cur_acc_key:
            acc = self.accounts.find(self.cur_acc_key)
            m.add_separator()
            m.add_command(label="     Удалить «%s»" % acc["name"], command=self._remove_account)
        p = self.acc_pill
        x = self.hc.winfo_rootx() + p.bx
        y = self.hc.winfo_rooty() + p.by + p.h + self.px(6)
        m.tk_popup(x, y)

    def _select_account(self, key):
        self.settings.set("last_account", key)
        self._refresh_accounts(key)

    def _add_microsoft(self):
        MsLoginDialog(self, self.s_client.get().strip() or auth.AZURE_CLIENT_ID)

    def ms_account_added(self, acc):
        self._refresh_accounts(self.accounts.upsert(acc))
        self.status("Вход выполнен: %s — лицензия подтверждена" % acc["name"], C["accent"])

    def _remove_account(self):
        k = self.cur_acc_key
        a = self.accounts.find(k) if k else None
        if a and messagebox.askyesno(APP_NAME, "Удалить аккаунт %s?" % a.get("name"), parent=self.root):
            self.accounts.remove(k)
            threading.Thread(target=self.premium.forget, args=(a["id"],), daemon=True).start()
            self.settings.set("last_account", "")
            self._refresh_accounts()

    # ------------------------------------------------------------------ установка / запуск
    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self.cancel.clear()
            self.play_pill.config(text="ОТМЕНА", style="busy", command=self._cancel_clicked)
            self.install_btn.config_pill(enabled=False)
            self._set_bar(0)
        else:
            self.play_pill.config(text="ИГРАТЬ", style="accent", command=self._play_clicked)
            self.install_btn.config_pill(enabled=True)
            with self._progress_lock:
                self._progress = None
            self._set_bar(None)

    def _cancel_clicked(self):
        self.cancel.set()
        self.status("Отмена...", C["warn"])

    def _install_clicked(self):
        self._start(launch=False)

    def _play_clicked(self):
        self._start(launch=True)

    def _start(self, launch, server=None):
        if self.busy:
            return
        vid = self.selected_vid
        if not vid:
            self.show_page("versions")
            return
        if launch and not self.cur_acc_key:
            self._account_menu()
            self.status("Чтобы играть, войдите в аккаунт Microsoft с лицензией Minecraft", C["warn"])
            return
        if launch and self.proc and self.proc.poll() is None:
            if not messagebox.askyesno(APP_NAME, "Игра уже запущена. Запустить ещё одну копию?", parent=self.root):
                return
        try:
            settings = self._collect_settings()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        if not launch:
            self.show_page("home")
        self._set_busy(True)
        self.bg(self._run_pipeline, vid, self.cur_acc_key, settings, launch, server)

    def _ensure_java(self, comp, major, settings):
        if settings.get("java_mode") == "custom" and settings.get("java_path"):
            j = javamod.probe(settings["java_path"], "custom")
            if not j:
                raise RuntimeError("Указанная Java не найдена: %s" % settings["java_path"])
            if not javamod.is_compatible(j, major):
                self.ui(self.log, "Внимание: версии нужна Java %d, а выбрана Java %s" % (major, j.version), "WARN")
            return j
        if not self.javas:
            self.javas = javamod.detect_javas(self._game_root())
        j = javamod.pick_java(self.javas, major)
        if j:
            return j
        self.ui(self.log, "Подходящая Java %d не найдена — скачиваю %s от Mojang" % (major, comp))
        j = javamod.install_runtime(comp, major, settings.get("download_threads"), self.progress, self.cancel)
        self.javas = javamod.detect_javas(self._game_root())
        self.ui(self._javas_loaded, self.javas)
        return j

    def _run_pipeline(self, vid, acc_key, settings, launch, server=None):
        try:
            root = settings["game_dir"]
            game_dir = instance_dir(settings, root, vid)
            self.ui(self.status, "Получаю данные версии %s..." % vid)
            vdata = resolve_version(root, vid, self.manifest)
            comp, major = required_java(vdata)
            self.req_cache[vid] = (comp, major)
            self.ui(self.log, "Minecraft %s · нужна Java %d (%s)" % (vid, major, comp))
            self.ui(self.status, "Проверка Java %d..." % major)
            java = self._ensure_java(comp, major, settings)
            self.ui(self.log, "Java %s · %s" % (java.version, java.path))
            self.ui(self._update_java_label)

            install_version(vdata, root, game_dir, settings.get("download_threads", 16), self.progress, self.cancel)
            self.ui(self.log, "Файлы версии %s проверены" % vid)
            self.ui(self._fill_versions)
            self.ui(self._update_home)
            if not launch:
                self.ui(self.status, "Minecraft %s установлен" % vid, C["accent"])
                return

            acc = self.accounts.find(acc_key)
            if not acc or acc.get("type") != "microsoft":
                raise RuntimeError("Для игры нужен аккаунт Microsoft с лицензией Minecraft")
            self.ui(self.status, "Проверка лицензии и сессии Microsoft...")
            new = auth.refresh_account(acc)
            if new is not acc:
                self.accounts.upsert(new)
            acc = new

            self._precheck_mods(vid, vdata, game_dir)
            self.ui(self.status, "Запуск Minecraft %s..." % vid)
            cmd, _ = build_command(vdata, root, game_dir, java.launch_exe, acc, settings, server=server)
            self.ui(self.log, "Команда: " + " ".join(mask_cmd(cmd, acc)))
            self.proc = start_process(cmd, game_dir,
                                      lambda line, lvl: self.ui(self.log, line, lvl),
                                      lambda code: self.ui(self._game_exited, code))
            self.ui(self._game_started, settings)
        except net.Cancelled:
            self.ui(self.status, "Отменено", C["warn"])
        finally:
            self.ui(self._set_busy, False)

    def _precheck_mods(self, vid, vdata, game_dir):
        """Быстрая проверка модов перед запуском (без интернета, по метаданным jar)."""
        from . import loaders, mods
        ld, _lv, mc = loaders.detect(vdata)
        mdir = os.path.join(game_dir, "mods")
        if not ld or not os.path.isdir(mdir):
            return
        try:
            res = mods.check_folder(mods.ModFolder(mdir), mc, ld, online=False)
        except Exception:  # noqa: BLE001
            return
        bad = [r for r in res if r["enabled"] and r["status"] == "bad"]
        miss = [r for r in res if r["enabled"] and r["missing"]]
        for r in bad:
            self.ui(self.log, "Несовместимый мод: %s — %s" % (r["title"], r["reason"]), "WARN")
        for r in miss:
            self.ui(self.log, "Мод %s: не хватает %s" % (r["title"], ", ".join(m["name"] for m in r["missing"])),
                    "WARN")
        if bad or miss:
            self.ui(self.status, "Внимание: проблемы с модами (%d) — игра может не запуститься. См. «Моды»"
                    % (len(bad) + len(miss)), C["warn"])

    def _game_started(self, settings):
        self.status("Игра запущена — приятной игры!", C["accent"])
        self.kill_btn.config_pill(enabled=True)
        if settings.get("hide_on_launch"):
            self.root.withdraw()

    def _game_exited(self, code):
        self.kill_btn.config_pill(enabled=False)
        self.root.deiconify()
        if code == 0:
            self.log("Игра закрыта")
            self.status("Игра закрыта")
        else:
            self.log("Игра завершилась с кодом %s — подробности выше" % code, "ERROR")
            self.status("Игра завершилась с ошибкой (код %s) — смотрите «Консоль»" % code, C["err"])
            self.show_page("console")

    def _kill_game(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

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

        def work():
            try:
                m = updater.check()
            except Exception as e:  # noqa: BLE001
                if manual:
                    self.ui(self.status, "Не удалось проверить обновления: %s" % e, C["warn"])
                return
            self.ui(self._update_found, m, manual)
        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, m, manual):
        self.update_manifest = m
        if m:
            self.upd_pill.config(text="Доступно обновление %s" % m["version"])
            self.upd_pill.set_visible(True)
            if manual:
                self._update_clicked()
        elif manual:
            messagebox.showinfo(APP_NAME, "У вас последняя версия: %s" % APP_VERSION, parent=self.root)

    def _update_clicked(self):
        m = self.update_manifest
        if not m or self.busy:
            return
        notes = (m.get("notes") or "").strip()
        if not messagebox.askyesno(APP_NAME, "Обновить %s до версии %s?%s" % (
                APP_NAME, m["version"], ("\n\nЧто нового:\n" + notes) if notes else ""), parent=self.root):
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
        self.status("Обновление %s готово" % ver, C["accent"])
        if messagebox.askyesno(APP_NAME, "Обновление %s скачано. Перезапустить лаунчер сейчас?" % ver,
                               parent=self.root):
            updater.restart()
            self._on_close()

    def _on_close(self):
        try:
            self._collect_settings()
        except Exception:  # noqa: BLE001
            pass
        self.cancel.set()
        self.root.destroy()


# =====================================================================================
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
    root.mainloop()
