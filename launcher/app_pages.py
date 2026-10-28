"""Страницы окна: главная, версии, Java, настройки, консоль (вёрстка)."""
import os
import tkinter as tk
from tkinter import ttk

from .app_util import fmt_date, hero_kind, open_path, spaced, total_ram_mb
from .config import APP_NAME, APP_VERSION, DATA_DIR
from .ui_kit import C, CPill, GoldBadge, Slider, Switch, make_entry
from .versions import TYPE_NAMES, is_installed

class PagesMixin:
    """Страницы окна: главная, версии, Java, настройки, консоль (вёрстка)."""

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
        self.s_game_dir.trace_add("write", lambda *a: self._game_root())  # обновляет значение для фоновых потоков
        self._game_root()
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
        self.s_reports = tk.BooleanVar(value=self.settings.get("crash_reports") is True)
        Switch(cf, self.A, self.s_reports).pack(side="left", padx=(px(22), px(10)))  # поле шире переключателя
        self._label(cf, "Отчёты об ошибках").pack(side="left")
        self._label(cf, " · анонимно, без ника и путей", muted=True, size=9).pack(side="left")

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
        self.s_beta = tk.BooleanVar(value=bool(self.settings.get("beta_updates")))
        Switch(cf, self.A, self.s_beta).pack(side="left", padx=(px(40), px(10)))
        self._label(cf, "Бета-версии лаунчера").pack(side="left")
        self._label(cf, " · новое раньше всех", muted=True, size=9).pack(side="left")

        cf = row("Скрывать лаунчер", "пока запущена игра")
        self.s_hide = tk.BooleanVar(value=bool(self.settings.get("hide_on_launch")))
        Switch(cf, self.A, self.s_hide).pack(side="left")
        self.s_discord = tk.BooleanVar(value=bool(self.settings.get("discord_rpc")))
        Switch(cf, self.A, self.s_discord).pack(side="left", padx=(px(40), px(10)))
        self._label(cf, "Статус в Discord").pack(side="left")
        self._label(cf, " · версия игры и сервер в профиле", muted=True, size=9).pack(side="left")

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
