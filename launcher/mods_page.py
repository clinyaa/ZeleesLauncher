"""Вкладка «Моды»: сборки с загрузчиком, каталог Modrinth/CurseForge, установленные моды и проверка совместимости."""
import base64
import os
import threading
import traceback
import webbrowser
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, messagebox

from . import loaders, mods, net
from .installer import install_version
from .launch import instance_dir
from .ui_kit import C, PillButton, ScrollFrame, Switch, make_entry
from .versions import local_versions, resolve_version, required_java

STATUS_COLOR = {"ok": C["accent"], "bad": C["err"], "unknown": C["muted"]}
LETTER_COLORS = ["#2f6b45", "#6b4a2f", "#2f4f6b", "#5a2f6b", "#6b2f3f", "#4f6b2f"]


def fmt_num(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%.0fK" % (n / 1e3)
    return str(n)


class ModsPage:
    def __init__(self, app):
        self.app = app
        self.A, self.F, self.px = app.A, app.F, app.px
        self.builds = []          # [(vid, loader, loader_ver, mc)]
        self.build = None
        self.mode = "catalog"
        self.source = "modrinth"
        self.offset = 0
        self.total = 0
        self.query_var = tk.StringVar()
        self.sort_var = tk.StringVar(value="Популярные")
        self.icons = {}
        self.icon_pool = ThreadPoolExecutor(max_workers=4)
        self.installed_keys = set()
        self.checked = []
        self._search_job = None
        self._search_seq = 0
        self._build_ui()

    # ------------------------------------------------------------------ разметка
    def _build_ui(self):
        app, px = self.app, self.px
        body = app._page("mods", "Моды", "Каталог Modrinth — моды скачиваются прямо в сборку вместе с зависимостями")
        self.body = body

        top = tk.Frame(body, bg=C["bg"])
        top.pack(fill="x")
        app._label(top, "Сборка", bold=True).pack(side="left")
        self.build_var = tk.StringVar()
        self.build_box = ttk.Combobox(top, textvariable=self.build_var, state="readonly", width=46,
                                      font=self.F.b(10))
        self.build_box.pack(side="left", padx=px(12))
        self.build_box.bind("<<ComboboxSelected>>", lambda e: self._build_selected())
        app._btn(top, "+ Новая сборка", self._new_build, style="sel").pack(side="left")
        app._btn(top, "Папка mods", self._open_mods).pack(side="right")
        app._btn(top, "Играть в сборку", self._play_build, style="accent").pack(side="right", padx=px(8))

        bar = tk.Frame(body, bg=C["bg"])
        bar.pack(fill="x", pady=(px(14), px(10)))
        self.tab_cat = app._btn(bar, "Каталог", lambda: self._set_mode("catalog"), style="sel", padx=16)
        self.tab_cat.pack(side="left")
        self.tab_inst = app._btn(bar, "Установленные", lambda: self._set_mode("installed"), padx=16)
        self.tab_inst.pack(side="left", padx=(px(8), 0))

        self.cat_tools = tk.Frame(bar, bg=C["bg"])
        self.cat_tools.pack(side="right")
        ef, self.search_entry = make_entry(self.cat_tools, self.A, self.query_var, width=24, font=self.F.b(10))
        ef.pack(side="left")
        self.query_var.trace_add("write", lambda *a: self._schedule_search())
        sort = ttk.Combobox(self.cat_tools, textvariable=self.sort_var, state="readonly", width=12,
                            values=["Популярные", "По запросу", "Обновлённые", "Новые"], font=self.F.b(10))
        sort.pack(side="left", padx=px(8))
        sort.bind("<<ComboboxSelected>>", lambda e: self.search(reset=True))
        self.src_btns = {}
        for key, name in (("modrinth", "Modrinth"), ("curseforge", "CurseForge")):
            b = app._btn(self.cat_tools, name, lambda k=key: self._set_source(k),
                         style="sel" if key == self.source else "dark", padx=12)
            b.pack(side="left", padx=(px(6), 0))
            self.src_btns[key] = b

        self.inst_tools = tk.Frame(bar, bg=C["bg"])
        self.inst_summary = app._label(self.inst_tools, "", muted=True)
        self.inst_summary.pack(side="left", padx=px(10))
        app._btn(self.inst_tools, "Проверить снова", self.refresh_installed, padx=14).pack(side="left")

        self.list = ScrollFrame(body, bg=C["bg"])
        self.list.pack(fill="both", expand=True)
        self.status = app._label(body, "", muted=True, size=9, anchor="w")
        self.status.pack(fill="x", pady=(px(8), 0))

    # ------------------------------------------------------------------ сборки
    def on_show(self):
        self.reload_builds()

    def reload_builds(self, select=None):
        root = self.app._game_root()
        res = []
        for lv in local_versions(root):
            if not lv.get("inheritsFrom"):
                continue
            try:
                vd = resolve_version(root, lv["id"], self.app.manifest)
            except Exception:  # noqa: BLE001
                continue
            ld, lver, mc = loaders.detect(vd)
            if ld:
                res.append((lv["id"], ld, lver, mc))
        res.sort(key=lambda b: b[0])
        self.builds = res
        self.build_box["values"] = ["%s %s  ·  Minecraft %s" % (loaders.LOADER_NAMES[b[1]], b[2], b[3])
                                    for b in res]
        want = select or (self.build[0] if self.build else None) or self.app.selected_vid
        idx = next((i for i, b in enumerate(res) if b[0] == want), 0 if res else -1)
        if idx >= 0:
            self.build_box.current(idx)
            self.build = res[idx]
        else:
            self.build = None
            self.build_var.set("— нет сборок с загрузчиком —")
        self._refresh_view()

    def _build_selected(self):
        i = self.build_box.current()
        if 0 <= i < len(self.builds):
            self.build = self.builds[i]
            self._refresh_view()

    def mods_dir(self):
        if not self.build:
            return None
        root = self.app._game_root()
        return os.path.join(instance_dir(self.app.settings.data, root, self.build[0]), "mods")

    def _open_mods(self):
        d = self.mods_dir()
        if d:
            from .app_util import open_path
            open_path(d)

    def _play_build(self):
        if not self.build:
            return
        self.app.select_version(self.build[0])
        self.app.show_page("home")
        self.app._play_clicked()

    def _refresh_view(self):
        self.list.clear()
        if not self.build:
            self._empty_state()
            return
        self._update_installed_keys()
        if self.mode == "catalog":
            self.search(reset=True)
        else:
            self.refresh_installed()

    def _empty_state(self):
        px = self.px
        card = tk.Frame(self.list.inner, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x", pady=px(20))
        tk.Label(card, image=self.A.cube("modded", 48), bg=C["panel"]).pack(pady=(px(28), px(10)))
        tk.Label(card, text="Чтобы ставить моды, нужна сборка с загрузчиком", bg=C["panel"], fg=C["fg"],
                 font=self.F.t(15)).pack()
        tk.Label(card, text="Выберите версию Minecraft и загрузчик (Fabric, Quilt, Forge или NeoForge) — "
                            "лаунчер всё установит сам", bg=C["panel"], fg=C["muted"], font=self.F.b(10)).pack(
            pady=(px(4), px(16)))
        PillButton(card, self.A, "Создать сборку", command=self._new_build, style="accent",
                   font=self.F.b(10, "bold"), bg=C["panel"]).pack(pady=(0, px(28)))

    def _new_build(self):
        NewBuildDialog(self)

    # ------------------------------------------------------------------ режимы
    def _set_mode(self, mode):
        self.mode = mode
        self.tab_cat.config_pill(style="sel" if mode == "catalog" else "dark")
        self.tab_inst.config_pill(style="sel" if mode == "installed" else "dark")
        if mode == "catalog":
            self.inst_tools.pack_forget()
            self.cat_tools.pack(side="right")
        else:
            self.cat_tools.pack_forget()
            self.inst_tools.pack(side="right")
        self._refresh_view()

    def _set_source(self, key):
        if key == "curseforge" and not (self.app.settings.get("curseforge_key") or "").strip():
            messagebox.showinfo("CurseForge", "Каталог CurseForge работает только с личным API-ключом.\n\n"
                                "Получите бесплатный ключ на console.curseforge.com и вставьте его "
                                "в «Опции → CurseForge API-ключ».", parent=self.app.root)
            return
        self.source = key
        for k, b in self.src_btns.items():
            b.config_pill(style="sel" if k == key else "dark")
        self.search(reset=True)

    def provider(self):
        for p in mods.providers(self.app.settings.data):
            if p.key == self.source:
                return p
        return mods.Modrinth()

    def _set_status(self, text, color=None):
        self.status.configure(text=text, fg=color or C["muted"])

    # ------------------------------------------------------------------ каталог
    def _schedule_search(self):
        if self._search_job:
            self.app.root.after_cancel(self._search_job)
        self._search_job = self.app.root.after(450, lambda: self.search(reset=True))

    def search(self, reset=False):
        if not self.build or self.mode != "catalog":
            return
        if reset:
            self.offset = 0
            self.list.clear()
        self._search_seq += 1
        seq = self._search_seq
        _vid, ld, _lv, mc = self.build
        sort = {"Популярные": "downloads", "По запросу": "relevance", "Обновлённые": "updated",
                "Новые": "newest"}[self.sort_var.get()]
        q = self.query_var.get().strip()
        prov = self.provider()
        self._set_status("Поиск в %s: моды для %s %s..." % (prov.name, loaders.LOADER_NAMES[ld], mc))
        offset = self.offset

        def work():
            try:
                hits, total = prov.search(q, mc, ld, offset=offset, sort=sort)
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._set_status, "Не удалось загрузить каталог: %s" % e, C["err"])
                return
            self.app.ui(self._show_hits, seq, hits, total)
        threading.Thread(target=work, daemon=True).start()

    def _show_hits(self, seq, hits, total):
        if seq != self._search_seq or self.mode != "catalog":
            return
        for w in self.list.inner.winfo_children():
            if getattr(w, "_more", False):
                w.destroy()
        self.total = total
        for h in hits:
            self._hit_row(h)
        self.offset += len(hits)
        _vid, ld, _lv, mc = self.build
        if not hits and self.offset == 0:
            self._set_status("Ничего не найдено для %s %s" % (loaders.LOADER_NAMES[ld], mc), C["warn"])
        else:
            self._set_status("Показано %d из %s · только моды, совместимые с %s %s" % (
                self.offset, fmt_num(total), loaders.LOADER_NAMES[ld], mc))
        if self.offset < total and hits:
            f = tk.Frame(self.list.inner, bg=C["bg"])
            f._more = True
            f.pack(fill="x", pady=self.px(12))
            PillButton(f, self.A, "Показать ещё", command=lambda: self.search(), font=self.F.b(10, "bold"),
                       bg=C["bg"]).pack()

    def _icon_widget(self, parent, title, url, bg):
        px = self.px
        size = px(44)
        cv = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        col = LETTER_COLORS[sum(map(ord, title or "?")) % len(LETTER_COLORS)]
        cv.create_rectangle(0, 0, size, size, fill=col, width=0)
        cv.create_text(size // 2, size // 2, text=(title or "?")[:1].upper(), fill="white", font=self.F.t(16))
        if url:
            if url in self.icons:
                if self.icons[url]:
                    cv.create_image(size // 2, size // 2, image=self.icons[url])
            else:
                self.icon_pool.submit(self._load_icon, url, cv, size)
        return cv

    def _load_icon(self, url, cv, size):
        try:
            _, data = net.request(url, timeout=10)
        except Exception:  # noqa: BLE001
            return
        if not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:6] in (b"GIF87a", b"GIF89a")):
            self.icons[url] = None  # webp/jpeg tkinter не умеет — остаётся буква
            return
        self.app.ui(self._apply_icon, url, data, cv, size)

    def _apply_icon(self, url, data, cv, size):
        try:
            img = tk.PhotoImage(data=base64.b64encode(data))
            k = max(1, -(-max(img.width(), img.height()) // size))
            if k > 1:
                img = img.subsample(k)
            elif img.width() * 2 <= size:
                img = img.zoom(max(1, size // img.width()))
        except tk.TclError:
            self.icons[url] = None
            return
        self.icons[url] = img
        if cv.winfo_exists():
            cv.create_image(size // 2, size // 2, image=img)

    def _row(self):
        px = self.px
        row = tk.Frame(self.list.inner, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        row.pack(fill="x", pady=(0, px(8)), padx=(0, px(8)))
        inner = tk.Frame(row, bg=C["panel"])
        inner.pack(fill="x", padx=px(14), pady=px(12))
        return inner

    def _hit_row(self, h):
        px = self.px
        r = self._row()
        self._icon_widget(r, h["title"], h.get("icon_url"), C["panel"]).pack(side="left", anchor="n")
        mid = tk.Frame(r, bg=C["panel"])
        mid.pack(side="left", fill="x", expand=True, padx=px(14))
        t = tk.Frame(mid, bg=C["panel"])
        t.pack(fill="x")
        title = tk.Label(t, text=h["title"], bg=C["panel"], fg=C["fg"], font=self.F.b(11, "bold"), cursor="hand2")
        title.pack(side="left")
        title.bind("<Button-1>", lambda e: webbrowser.open(h["page_url"]))
        if h.get("author"):
            tk.Label(t, text="  от " + h["author"], bg=C["panel"], fg=C["muted"], font=self.F.b(9)).pack(side="left")
        desc = h.get("description", "")
        if len(desc) > 150:
            desc = desc[:147].rstrip() + "…"
        tk.Label(mid, text=desc, bg=C["panel"], fg="#b5bac4", font=self.F.b(9), anchor="w", justify="left",
                 wraplength=px(620)).pack(fill="x")
        meta = "↓ %s" % fmt_num(h.get("downloads"))
        if h.get("categories"):
            meta += "   ·   " + ", ".join(h["categories"][:3])
        tk.Label(mid, text=meta, bg=C["panel"], fg=C["dim"], font=self.F.b(8)).pack(anchor="w", pady=(px(2), 0))
        key = (h["source"], h["id"])
        installed = key in self.installed_keys
        btn = PillButton(r, self.A, "Установлен" if installed else "Скачать", style="sel" if installed else "accent",
                         font=self.F.b(10, "bold"), bg=C["panel"], minw=120,
                         image=None if installed else self.A.icon("download", "_w"))
        btn.config_pill(command=lambda: self._install_hit(h, btn))
        btn.pack(side="right")

    def _install_hit(self, h, btn):
        if not self.build or getattr(btn, "_busy", False):
            return
        if (h["source"], h["id"]) in self.installed_keys:
            if not messagebox.askyesno("Моды", "«%s» уже установлен. Переустановить свежую версию?" % h["title"],
                                       parent=self.app.root):
                return
        btn._busy = True
        btn.config_pill(text="Загрузка…", style="disabled", image=self.A.icon("download"))
        _vid, ld, _lv, mc = self.build
        prov = self.provider()
        folder = mods.ModFolder(self.mods_dir())

        def work():
            try:
                mf = prov.best_file(h["id"], mc, ld)
                if not mf:
                    self.app.ui(self._install_done, btn, False, "Нет версии «%s» для %s %s" % (
                        h["title"], loaders.LOADER_NAMES[ld], mc))
                    return
                mf.setdefault("page_url", h.get("page_url"))
                keys = set(self.installed_keys) - {(h["source"], h["id"])}
                plan = mods.resolve_install(prov, mf, mc, ld, keys, log=self.app.log)
                names = []
                for item in plan:
                    if item is mf:
                        title, icon = h["title"], h.get("icon_url")
                    else:
                        try:
                            pr = prov.project(item["project_id"])
                            title, icon = pr.get("title"), pr.get("icon_url")
                        except Exception:  # noqa: BLE001
                            title, icon = item["filename"], ""
                        names.append(title)
                    item.setdefault("page_url", h.get("page_url"))
                    folder.install(item, title, icon)
                    self.app.ui(self.app.log, "Мод установлен: %s (%s)" % (title, item["filename"]))
                msg = "«%s» установлен" % h["title"]
                if names:
                    msg += " + зависимости: " + ", ".join(names)
                self.app.ui(self._install_done, btn, True, msg)
            except mods.ModsError as e:
                self.app.ui(self._install_done, btn, False, str(e), e.url)
            except Exception as e:  # noqa: BLE001
                self.app.ui(self.app.log, traceback.format_exc(), "ERROR")
                self.app.ui(self._install_done, btn, False, "Ошибка: %s" % e)
        threading.Thread(target=work, daemon=True).start()

    def _install_done(self, btn, ok, msg, url=None):
        btn._busy = False
        if btn.winfo_exists():
            if ok:
                btn.config_pill(text="Установлен", style="sel", image=False)
            else:
                btn.config_pill(text="Скачать", style="accent", image=self.A.icon("download", "_w"))
        self._set_status(("✓  " if ok else "") + msg, C["accent"] if ok else C["err"])
        if url and messagebox.askyesno("Моды", msg + "\n\nОткрыть страницу мода?", parent=self.app.root):
            webbrowser.open(url)
        self._update_installed_keys()

    def _update_installed_keys(self):
        d = self.mods_dir()
        keys = set()
        if d:
            keys |= set(mods.ModFolder(d).installed_projects().keys())
        for r in self.checked:
            if r.get("project_id") and r.get("source"):
                keys.add((r["source"], r["project_id"]))
        self.installed_keys = keys

    # ------------------------------------------------------------------ установленные
    def refresh_installed(self):
        if not self.build:
            return
        self.list.clear()
        _vid, ld, _lv, mc = self.build
        self._set_status("Проверяю моды на совместимость с %s %s..." % (loaders.LOADER_NAMES[ld], mc))
        folder = mods.ModFolder(self.mods_dir())

        def work():
            try:
                res = mods.check_folder(folder, mc, ld, online=True)
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._set_status, "Ошибка проверки: %s" % e, C["err"])
                return
            self.app.ui(self._show_installed, res)
        threading.Thread(target=work, daemon=True).start()

    def _show_installed(self, res):
        if self.mode != "installed":
            self.checked = res
            self._update_installed_keys()
            return
        self.list.clear()
        self.checked = res
        self._update_installed_keys()
        _vid, ld, _lv, mc = self.build
        if not res:
            tk.Label(self.list.inner, text="В этой сборке пока нет модов — загляните в «Каталог»", bg=C["bg"],
                     fg=C["muted"], font=self.F.b(11)).pack(pady=self.px(40))
            self.inst_summary.configure(text="")
            self._set_status("")
            return
        on = [r for r in res if r["enabled"]]
        ok = sum(1 for r in on if r["status"] == "ok")
        bad = sum(1 for r in on if r["status"] == "bad")
        miss = sum(1 for r in on if r["missing"])
        upd = sum(1 for r in res if r["update"])
        problems = bad + miss
        parts = ["Модов: %d" % len(res), "совместимы: %d" % ok]
        if problems:
            parts.append("проблемы: %d" % problems)
        if upd:
            parts.append("обновления: %d" % upd)
        self.inst_summary.configure(text="  ·  ".join(parts), fg=C["err"] if problems else C["muted"])
        order = {"bad": 0, "unknown": 2, "ok": 3}
        res = sorted(res, key=lambda r: (not r["enabled"], 1 if r["missing"] else order[r["status"]],
                                         r["title"].lower()))
        for r in res:
            self._installed_row(r)
        self._set_status("Проверено для %s %s. Отключённые моды не загружаются игрой." % (
            loaders.LOADER_NAMES[ld], mc))

    def _installed_row(self, r):
        px = self.px
        row = self._row()
        self._icon_widget(row, r["title"], r.get("icon_url"), C["panel"]).pack(side="left", anchor="n")
        mid = tk.Frame(row, bg=C["panel"])
        mid.pack(side="left", fill="x", expand=True, padx=px(14))
        t = tk.Frame(mid, bg=C["panel"])
        t.pack(fill="x")
        fg = C["fg"] if r["enabled"] else C["dim"]
        tk.Label(t, text=r["title"], bg=C["panel"], fg=fg, font=self.F.b(11, "bold")).pack(side="left")
        if r["version"]:
            tk.Label(t, text="  " + str(r["version"])[:40], bg=C["panel"], fg=C["muted"], font=self.F.b(9)).pack(
                side="left")
        st = tk.Frame(mid, bg=C["panel"])
        st.pack(fill="x", pady=(px(3), 0))
        if r["enabled"]:
            color = STATUS_COLOR[r["status"]]
            label = {"ok": "Совместим", "bad": "Несовместим", "unknown": "Не удалось проверить"}[r["status"]]
            text = label if r["status"] == "ok" else "%s — %s" % (label, r["reason"])
        else:
            color, text = C["dim"], "Отключён"
        dot = tk.Canvas(st, width=px(8), height=px(8), bg=C["panel"], highlightthickness=0)
        dot.create_oval(0, 0, px(8), px(8), fill=color, width=0)
        dot.pack(side="left")
        tk.Label(st, text=" " + text, bg=C["panel"], fg=color, font=self.F.b(9)).pack(side="left")
        tk.Label(st, text="   " + r["file"], bg=C["panel"], fg=C["dim"], font=self.F.b(8)).pack(side="left")
        if r["missing"]:
            mf = tk.Frame(mid, bg=C["panel"])
            mf.pack(fill="x", pady=(px(4), 0))
            names = ", ".join(m["name"] for m in r["missing"][:4])
            tk.Label(mf, text="Не хватает зависимостей: " + names, bg=C["panel"], fg=C["warn"],
                     font=self.F.b(9)).pack(side="left")
            PillButton(mf, self.A, "Доустановить", command=lambda: self._install_missing(r["missing"]),
                       style="sel", size="sm", font=self.F.b(9, "bold"), bg=C["panel"], padx=12).pack(
                side="left", padx=px(10))

        right = tk.Frame(row, bg=C["panel"])
        right.pack(side="right")
        PillButton(right, self.A, "Удалить", command=lambda: self._delete(r), style="danger", size="sm",
                   font=self.F.b(9, "bold"), bg=C["panel"], padx=12).pack(side="right")
        if r["update"]:
            PillButton(right, self.A, "Обновить", command=lambda: self._update(r), style="accent", size="sm",
                       font=self.F.b(9, "bold"), bg=C["panel"], padx=12).pack(side="right", padx=px(8))
        v = tk.BooleanVar(value=r["enabled"])
        Switch(right, self.A, v, command=lambda: self._toggle(r), bg=C["panel"]).pack(side="right", padx=px(10))

    def _toggle(self, r):
        mods.ModFolder(self.mods_dir()).toggle(r["file"])
        self.refresh_installed()

    def _delete(self, r):
        if messagebox.askyesno("Моды", "Удалить мод «%s»?" % r["title"], parent=self.app.root):
            mods.ModFolder(self.mods_dir()).delete(r["file"])
            self.refresh_installed()

    def _update(self, r):
        mf = r["update"]
        folder = mods.ModFolder(self.mods_dir())
        self._set_status("Обновляю «%s»..." % r["title"])

        def work():
            try:
                folder.install(mf, r["title"], r.get("icon_url"))
                old = os.path.join(folder.path, r["file"])
                if os.path.basename(old) != mf["filename"] and os.path.exists(old):
                    folder.delete(r["file"])
                self.app.ui(self.app.log, "Мод обновлён: %s → %s" % (r["title"], mf["version_number"]))
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._set_status, "Ошибка обновления: %s" % e, C["err"])
                return
            self.app.ui(self.refresh_installed)
        threading.Thread(target=work, daemon=True).start()

    def _install_missing(self, missing):
        _vid, ld, _lv, mc = self.build
        folder = mods.ModFolder(self.mods_dir())
        self._set_status("Ищу и ставлю недостающие зависимости...")

        def work():
            mr = mods.Modrinth()
            done, failed = [], []
            for m in missing:
                try:
                    pid = m["pid"]
                    if not pid:
                        hits, _ = mr.search(m["name"], mc, ld, limit=5, sort="relevance")
                        hit = next((x for x in hits if x.get("slug") == m["name"] or
                                    x["title"].lower().replace(" ", "-") == m["name"].lower()), hits[0] if hits else None)
                        pid = hit["id"] if hit else None
                    mf = mr.best_file(pid, mc, ld) if pid else None
                    if not mf:
                        failed.append(m["name"])
                        continue
                    plan = mods.resolve_install(mr, mf, mc, ld, self.installed_keys)
                    for item in plan:
                        try:
                            pr = mr.project(item["project_id"])
                            title, icon = pr.get("title"), pr.get("icon_url")
                        except Exception:  # noqa: BLE001
                            title, icon = item["filename"], ""
                        folder.install(item, title, icon)
                        done.append(title)
                except Exception:  # noqa: BLE001
                    failed.append(m["name"])
            msg = ("Установлено: " + ", ".join(done)) if done else ""
            if failed:
                msg += ("   ·   " if msg else "") + "Не найдено на Modrinth: " + ", ".join(failed)
            self.app.ui(self._set_status, msg, C["warn"] if failed else C["accent"])
            self.app.ui(self.refresh_installed)
        threading.Thread(target=work, daemon=True).start()


# =====================================================================================
class NewBuildDialog:
    """Создание сборки: версия Minecraft + загрузчик + его версия."""

    def __init__(self, page):
        from .dialogs import Dialog
        self.page = page
        app = page.app
        self.app = app
        d = Dialog(app, "Новая сборка", "Новая сборка", "версия Minecraft + загрузчик модов")
        self.d = d
        px = app.px
        f = d.f
        releases = [v["id"] for v in app.manifest.versions if v["type"] == "release"]
        tk.Label(f, text="Версия Minecraft", bg=C["panel"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        self.mc_var = tk.StringVar(value=releases[0] if releases else "")
        mc_box = ttk.Combobox(f, textvariable=self.mc_var, values=releases, state="readonly", font=app.F.b(11))
        mc_box.pack(fill="x", pady=(px(4), px(14)))
        mc_box.bind("<<ComboboxSelected>>", lambda e: self._load_versions())

        tk.Label(f, text="Загрузчик", bg=C["panel"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        lf = tk.Frame(f, bg=C["panel"])
        lf.pack(fill="x", pady=(px(4), px(14)))
        self.loader = "fabric"
        self.lbtns = {}
        for key, name in loaders.LOADERS:
            b = PillButton(lf, app.A, name, command=lambda k=key: self._pick(k), style="sel" if key == self.loader
                           else "dark", font=app.F.b(10, "bold"), bg=C["panel"], padx=14)
            b.pack(side="left", padx=(0, px(6)))
            self.lbtns[key] = b

        tk.Label(f, text="Версия загрузчика", bg=C["panel"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        self.lv_var = tk.StringVar()
        self.lv_box = ttk.Combobox(f, textvariable=self.lv_var, state="readonly", font=app.F.b(11))
        self.lv_box.pack(fill="x", pady=(px(4), 0))
        self.info = tk.Label(f, text="", bg=C["panel"], fg=C["muted"], font=app.F.b(9), wraplength=px(440),
                             justify="left")
        self.info.pack(anchor="w", pady=(px(10), 0))
        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", pady=(px(16), 0))
        self.ok_btn = d.btn(bf, "Создать", self._create, "accent")
        self.ok_btn.pack(side="right")
        d.btn(bf, "Отмена", d.destroy).pack(side="right", padx=px(8))
        d.center()
        d.grab_set()
        self._load_versions()

    def _pick(self, key):
        self.loader = key
        for k, b in self.lbtns.items():
            b.config_pill(style="sel" if k == key else "dark")
        self._load_versions()

    def _load_versions(self):
        mc, ld = self.mc_var.get(), self.loader
        self.lv_box["values"] = []
        self.lv_var.set("загрузка...")
        self.info.configure(text="", fg=C["muted"])

        def work():
            try:
                vs = loaders.loader_versions(ld, mc)
            except Exception as e:  # noqa: BLE001
                vs, err = [], str(e)
            else:
                err = None
            self.app.ui(self._versions_loaded, mc, ld, vs, err)
        threading.Thread(target=work, daemon=True).start()

    def _versions_loaded(self, mc, ld, vs, err):
        if mc != self.mc_var.get() or ld != self.loader or not self.d.winfo_exists():
            return
        self.lv_box["values"] = vs
        if vs:
            self.lv_box.current(0)
            note = "Рекомендуемая версия выбрана автоматически."
            if ld in ("forge", "neoforge"):
                note += " Установщик %s запустится сам и займёт 1–3 минуты." % loaders.LOADER_NAMES[ld]
            self.info.configure(text=note, fg=C["muted"])
        else:
            self.lv_var.set("")
            self.info.configure(text=err or "%s пока не поддерживает Minecraft %s" % (
                loaders.LOADER_NAMES[ld], mc), fg=C["warn"])

    def _create(self):
        mc, ld, lv = self.mc_var.get(), self.loader, self.lv_var.get()
        if not mc or not lv or lv == "загрузка...":
            return
        app = self.app
        self.d.destroy()
        app.show_page("home")
        app._set_busy(True)
        settings = app._collect_settings()

        def work():
            try:
                root = settings["game_dir"]
                app.ui(app.status, "Создаю сборку %s %s для Minecraft %s..." % (loaders.LOADER_NAMES[ld], lv, mc))
                java = None
                if ld in ("forge", "neoforge"):
                    vdata = resolve_version(root, mc, app.manifest)
                    comp, major = required_java(vdata)
                    java = app._ensure_java(comp, major, settings)
                    install_version(vdata, root, root, settings.get("download_threads", 16), app.progress, app.cancel)
                vid = loaders.install(ld, mc, lv, root, java.path if java else None,
                                      log=lambda s: app.ui(app.log, s))
                app.ui(app.log, "Сборка создана: %s" % vid)
                app.ui(app._build_created, vid)
            except Exception as e:  # noqa: BLE001
                app.ui(app.log, traceback.format_exc(), "ERROR")
                app.ui(app._error, e)
            finally:
                app.ui(app._set_busy, False)
        app.bg(work)
