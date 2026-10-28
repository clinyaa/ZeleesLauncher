"""Вкладка «Моды»: сборки с загрузчиком, каталог Modrinth/CurseForge, установленные моды и проверка совместимости."""
import base64
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, ttk, messagebox

from . import loaders, mods, mrpack, net, packs
from .installer import install_version
from .launch import instance_dir
from .app_util import open_path
from .ui_kit import C, PillButton, ScrollFrame, Switch, make_entry
from .versions import local_versions, resolve_version, required_java

KIND_WHAT = {"mod": "моды", "resourcepack": "ресурс-паки", "shader": "шейдеры"}
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
        self.kind = "mod"         # что ищем в каталоге: mod | resourcepack | shader
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
        self.build_box = ttk.Combobox(top, textvariable=self.build_var, state="readonly", width=34,
                                      font=self.F.b(10))
        self.build_box.pack(side="left", padx=px(12))
        self.build_box.bind("<<ComboboxSelected>>", lambda e: self._build_selected())
        app._btn(top, "+ Новая", self._new_build, style="sel", padx=14).pack(side="left")
        app._btn(top, "Сборка по коду", lambda: PackCodeDialog(self), padx=14).pack(side="left", padx=(px(8), 0))
        app._btn(top, "Импорт .mrpack", self.import_mrpack, padx=14).pack(side="left", padx=(px(8), 0))
        app._btn(top, "Играть", self._play_build, style="accent", padx=16).pack(side="right")
        self.share_btn = app._btn(top, "Поделиться ▾", self._share_menu, padx=14)
        self.share_btn.pack(side="right", padx=px(8))

        bar = tk.Frame(body, bg=C["bg"])
        bar.pack(fill="x", pady=(px(14), px(10)))
        self.tabs = {}
        for i, (key, name) in enumerate((("mod", "Моды"), ("resourcepack", "Ресурс-паки"), ("shader", "Шейдеры"),
                                         ("installed", "Установленные"))):
            b = app._btn(bar, name, lambda k=key: self._set_mode(k), style="sel" if key == "mod" else "dark",
                         padx=14)
            b.pack(side="left", padx=(px(8) if i else 0, 0))
            self.tabs[key] = b

        self.cat_tools = tk.Frame(bar, bg=C["bg"])
        self.cat_tools.pack(side="right")
        ef, self.search_entry = make_entry(self.cat_tools, self.A, self.query_var, width=18, font=self.F.b(10))
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
        app._btn(self.inst_tools, "Папка mods", self._open_mods, padx=14).pack(side="left", padx=(px(8), 0))

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
                res.append((lv["id"], ld, lver, mc, lv.get("name")))
        res.sort(key=lambda b: (not b[4], (b[4] or b[0]).lower()))
        self.builds = res
        self.build_box["values"] = [("%s  ·  %s %s" % (b[4], loaders.LOADER_NAMES[b[1]], b[3]) if b[4] else
                                     "%s %s  ·  Minecraft %s" % (loaders.LOADER_NAMES[b[1]], b[2], b[3])) for b in res]
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

    def content_folder(self):
        """Папка сборки для текущего вида каталога (mods, resourcepacks или shaderpacks)."""
        d = os.path.join(os.path.dirname(self.mods_dir()), packs.KIND_DIRS[self.kind])
        return mods.ModFolder(d, mods.KIND_EXT[self.kind])

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

    def _share_menu(self):
        if not self.build:
            messagebox.showinfo("Поделиться", "Сначала создайте сборку с загрузчиком и добавьте в неё моды.",
                                parent=self.app.root)
            return
        m = tk.Menu(self.app.root, tearoff=0, bg=C["panel2"], fg=C["fg"], activebackground=C["sel"],
                    activeforeground="white", bd=0, relief="flat", font=self.F.b(10))
        m.add_command(label="Кодом — короткий код и ссылка zelees.link…", command=self._share_build)
        m.add_command(label="Файлом .mrpack — для друга или Modrinth…", command=self._export_build)
        b = self.share_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + self.px(4))

    def _export_build(self):
        if not self.build[2]:
            messagebox.showinfo("Экспорт .mrpack", "Не удалось определить версию загрузчика этой сборки.",
                                parent=self.app.root)
            return
        ExportDialog(self)

    def _share_build(self):
        if not self.build[2]:
            messagebox.showinfo("Поделиться", "Не удалось определить версию загрузчика этой сборки — "
                                "ей нельзя поделиться кодом.", parent=self.app.root)
            return
        if not self.app.current_ms_account():
            self.app._account_menu()
            self.app.status("Чтобы делиться сборками, войдите в аккаунт Microsoft", C["warn"])
            return
        ShareDialog(self)

    def import_mrpack(self, path=None):
        """Ставит модпак из файла .mrpack (без path — спрашивает файл)."""
        app = self.app
        if not path:
            path = filedialog.askopenfilename(title="Модпак Modrinth", parent=app.root,
                                              filetypes=[("Модпак Modrinth", "*.mrpack"), ("Все файлы", "*.*")])
        if not path:
            return
        if app.busy:
            app.status("Подождите — лаунчер уже занят", C["warn"])
            return
        try:
            pack = mrpack.read(path)
        except mrpack.MrpackError as e:
            messagebox.showerror("Импорт .mrpack", str(e), parent=app.root)
            return
        title = pack["name"] + (" " + pack["version"] if pack["version"] else "")
        text = "%s\nMinecraft %s  ·  %s %s\nФайлов для скачивания: %d, файлов настроек: %d" % (
            title, pack["mc"], loaders.LOADER_NAMES[pack["loader"]], pack["loader_version"],
            len(pack["files"]), pack["overrides"])
        if pack["skipped"]:
            text += "\nНе скачаются (нет ссылки Modrinth/GitHub): %d" % len(pack["skipped"])
        text += "\n\nБудет создана отдельная сборка со своей папкой — ваши другие сборки и миры не изменятся."
        if not messagebox.askyesno("Импорт .mrpack", text + "\n\nУстановить?", parent=app.root):
            return
        try:
            settings = app._collect_settings()
        except ValueError as e:
            messagebox.showerror("Импорт .mrpack", str(e), parent=app.root)
            return
        app.show_page("home")
        app._set_busy(True)

        def work():
            root = settings["game_dir"]
            vid = None
            try:
                ld, lv, mc = pack["loader"], pack["loader_version"], pack["mc"]
                parent = packs.find_loader_version(root, app.manifest, ld, lv, mc)
                if not parent:
                    parent = install_loader(app, settings, ld, mc, lv)
                vid = packs.create_pack_version(root, app.manifest, pack["name"], "mrpack", parent)
                game_dir = instance_dir(settings, root, vid)
                app.ui(app.status, "Модпак «%s»: копирую настройки..." % pack["name"])
                mrpack.extract_overrides(path, game_dir)
                mrpack.install_files(pack, game_dir, settings.get("download_threads", 16), app.progress, app.cancel)
                app.ui(app.log, "Модпак «%s» (%s) установлен: %s, файлов: %d" % (
                    title, os.path.basename(path), vid, len(pack["files"])))
                failed = [(p, "нет ссылки для скачивания") for p in pack["skipped"]]
                for what, why in failed:
                    app.ui(app.log, "Не скачано: %s — %s" % (what, why), "WARN")
                app.ui(_build_done, app, vid, pack["name"], failed, "Импорт .mrpack")
            except net.Cancelled:
                _remove_build(root, vid)
                app.ui(app.status, "Отменено", C["warn"])
            except Exception as e:  # noqa: BLE001
                _remove_build(root, vid)
                app.ui(app.log, traceback.format_exc(), "ERROR")
                app.ui(app._error, e)
            finally:
                app.ui(app._set_busy, False)
        app.bg(work)

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
    def _set_mode(self, key):
        """key: вид контента каталога (mod | resourcepack | shader) или installed."""
        if key != "installed":
            if self.kind != key:
                self.query_var.set("")
            self.kind = key
        mode = "installed" if key == "installed" else "catalog"
        self.mode = mode
        for k, b in self.tabs.items():
            b.config_pill(style="sel" if k == key else "dark")
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
        _vid, ld, _lv, mc = self.build[:4]
        sort = {"Популярные": "downloads", "По запросу": "relevance", "Обновлённые": "updated",
                "Новые": "newest"}[self.sort_var.get()]
        q = self.query_var.get().strip()
        prov = self.provider()
        kind = self.kind
        self._set_status("Поиск в %s: %s для %s..." % (prov.name, KIND_WHAT[kind], self._for_build(kind)))
        offset = self.offset

        def work():
            try:
                hits, total = prov.search(q, mc, ld, offset=offset, sort=sort, kind=kind)
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._set_status, "Не удалось загрузить каталог: %s" % e, C["err"])
                return
            self.app.ui(self._show_hits, seq, hits, total)
        threading.Thread(target=work, daemon=True).start()

    def _for_build(self, kind):
        _vid, ld, _lv, mc = self.build[:4]
        return "%s %s" % (loaders.LOADER_NAMES[ld], mc) if kind == "mod" else "Minecraft " + mc

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
        if not hits and self.offset == 0:
            self._set_status("Ничего не найдено для %s" % self._for_build(self.kind), C["warn"])
        else:
            self._set_status("Показано %d из %s · только %s для %s" % (
                self.offset, fmt_num(total), KIND_WHAT[self.kind], self._for_build(self.kind)))
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
        _vid, ld, _lv, mc = self.build[:4]
        prov = self.provider()
        kind = self.kind
        folder = self.content_folder()

        def work():
            try:
                mf = prov.best_file(h["id"], mc, ld, kind=kind)
                if not mf:
                    self.app.ui(self._install_done, btn, False, "Нет версии «%s» для %s" % (
                        h["title"], self._for_build(kind)))
                    return
                mf.setdefault("page_url", h.get("page_url"))
                keys = set(self.installed_keys) - {(h["source"], h["id"])}
                # у ресурс-паков и шейдеров зависимостей-модов не бывает
                plan = mods.resolve_install(prov, mf, mc, ld, keys, log=self.app.log) if kind == "mod" else [mf]
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
                    self.app.ui(self.app.log, "Установлено: %s (%s)" % (title, item["filename"]))
                msg = "«%s» установлен" % h["title"]
                if names:
                    msg += " + зависимости: " + ", ".join(names)
                if mf.get("other_mc"):
                    msg += " (для Minecraft %s версии нет — поставлена последняя, игра может предупредить)" % mc
                if kind == "shader" and ld in ("fabric", "quilt") and not self._has_iris():
                    msg += " · для шейдеров нужен мод Iris"
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
        keys = set()
        if self.mods_dir():
            keys |= set(self.content_folder().installed_projects().keys())
        if self.kind == "mod":
            for r in self.checked:
                if r.get("project_id") and r.get("source"):
                    keys.add((r["source"], r["project_id"]))
        self.installed_keys = keys

    def _has_iris(self):
        """Стоит ли в сборке Iris (без него Fabric/Quilt шейдеры не загрузит)."""
        folder = mods.ModFolder(self.mods_dir())
        if any(m.get("project_id") == "YL57xq9U" for m in folder.index.values()):  # Iris на Modrinth
            return True
        return any(e["base"].lower().startswith("iris") for e in folder.entries() if e["enabled"])

    # ------------------------------------------------------------------ установленные
    def refresh_installed(self):
        if not self.build:
            return
        self.list.clear()
        _vid, ld, _lv, mc = self.build[:4]
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
        _vid, ld, _lv, mc = self.build[:4]
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
        _vid, ld, _lv, mc = self.build[:4]
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


def _remove_build(root, vid):
    """Недоустановленная сборка не должна остаться в списке."""
    if not vid:
        return
    import shutil
    loaders.remove_version(root, vid)
    shutil.rmtree(os.path.join(root, "instances", vid), ignore_errors=True)


def _build_done(app, vid, name, failed, title):
    app._build_created(vid)
    app.status("Сборка «%s» готова — нажмите «Играть»" % name, C["accent"])
    if failed:
        lines = "\n".join("• %s — %s" % fw for fw in failed[:12])
        if len(failed) > 12:
            lines += "\n… и ещё %d" % (len(failed) - 12)
        messagebox.showwarning(title, "Сборка установлена, но часть файлов скачать не удалось:\n\n"
                               + lines, parent=app.root)


def install_loader(app, settings, ld, mc, lv):
    """Ставит загрузчик (для Forge/NeoForge — сначала Java и сам Minecraft). Возвращает id версии. В фоне."""
    root = settings["game_dir"]
    app.ui(app.status, "Создаю сборку %s %s для Minecraft %s..." % (loaders.LOADER_NAMES[ld], lv, mc))
    java = None
    if ld in ("forge", "neoforge"):
        vdata = resolve_version(root, mc, app.manifest)
        comp, major = required_java(vdata)
        java = app._ensure_java(comp, major, settings)
        install_version(vdata, root, root, settings.get("download_threads", 16), app.progress, app.cancel)
    return loaders.install(ld, mc, lv, root, java.path if java else None, log=lambda s: app.ui(app.log, s))


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
                vid = install_loader(app, settings, ld, mc, lv)
                app.ui(app.log, "Сборка создана: %s" % vid)
                app.ui(app._build_created, vid)
            except Exception as e:  # noqa: BLE001
                app.ui(app.log, traceback.format_exc(), "ERROR")
                app.ui(app._error, e)
            finally:
                app.ui(app._set_busy, False)
        app.bg(work)


# =====================================================================================
def _pack_summary(files):
    n = {"mod": 0, "resourcepack": 0, "shader": 0}
    for f in files:
        n[f.get("kind", "mod")] = n.get(f.get("kind", "mod"), 0) + 1
    parts = [t % n[k] for k, t in (("mod", "модов: %d"), ("resourcepack", "ресурс-паков: %d"),
                                    ("shader", "шейдеров: %d")) if n[k]]
    return ", ".join(parts) or "без модов"


class ExportDialog:
    """Сохраняет сборку в файл .mrpack: моды с Modrinth — ссылками, остальное и настройки — внутрь файла."""

    def __init__(self, page):
        from .dialogs import Dialog
        self.page, self.app = page, page.app
        app, px = self.app, self.app.px
        _vid, ld, lv, mc, name = page.build
        self.build = page.build
        self.game_dir = os.path.dirname(page.mods_dir())
        self.scan = None
        d = Dialog(app, "Экспорт .mrpack", "Экспорт в .mrpack", "файл откроется в любом лаунчере с Modrinth-паками")
        self.d = d
        f = d.f
        row = tk.Frame(f, bg=C["panel"])
        row.pack(fill="x")
        col = tk.Frame(row, bg=C["panel"])
        col.pack(side="left", fill="x", expand=True)
        tk.Label(col, text="Название", bg=C["panel"], fg=C["fg"], font=app.F.b(10, "bold")).pack(anchor="w")
        self.name_var = tk.StringVar(value=name or "%s %s" % (loaders.LOADER_NAMES[ld], mc))
        ef, _e = make_entry(col, app.A, self.name_var, width=30, font=app.F.b(11))
        ef.pack(fill="x", pady=(px(4), 0))
        col = tk.Frame(row, bg=C["panel"])
        col.pack(side="left", padx=(px(10), 0))
        tk.Label(col, text="Версия", bg=C["panel"], fg=C["fg"], font=app.F.b(10, "bold")).pack(anchor="w")
        self.ver_var = tk.StringVar(value="1.0.0")
        ef, _e = make_entry(col, app.A, self.ver_var, width=10, font=app.F.b(11))
        ef.pack(pady=(px(4), 0))
        tk.Label(f, text="Minecraft %s  ·  %s %s" % (mc, loaders.LOADER_NAMES[ld], lv), bg=C["panel"],
                 fg=C["muted"], font=app.F.b(9)).pack(anchor="w", pady=(px(10), px(6)))
        self.embed_var = tk.BooleanVar(value=True)
        self.config_var = tk.BooleanVar(value=True)
        for var, text in ((self.embed_var, "Вложить файлы, которых нет на Modrinth"),
                          (self.config_var, "Вложить настройки модов (папка config)")):
            r = tk.Frame(f, bg=C["panel"])
            r.pack(fill="x", pady=px(3))
            Switch(r, app.A, var, command=self._update_info, bg=C["panel"]).pack(side="left")
            tk.Label(r, text=text, bg=C["panel"], fg=C["fg"], font=app.F.b(10)).pack(side="left", padx=px(10))
        self.info = tk.Label(f, text="Проверяю файлы сборки на Modrinth...", bg=C["panel"], fg=C["muted"],
                             font=app.F.b(9), wraplength=px(440), justify="left")
        self.info.pack(anchor="w", pady=(px(8), 0))
        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", side="bottom", pady=(px(16), 0))
        self.ok = d.btn(bf, "Сохранить…", self._save, "accent")
        self.ok.config_pill(enabled=False)
        self.ok.pack(side="right")
        self.cancel_btn = d.btn(bf, "Отмена", d.destroy)
        self.cancel_btn.pack(side="right", padx=px(8))
        d.bind("<Escape>", lambda ev: d.destroy())
        d.center()
        d.grab_set()

        def work():
            try:
                files, disabled = mrpack.scan(self.game_dir)
                urls = mrpack.identify(files) if files else {}
                res, err = (files, disabled, urls, mrpack.config_files(self.game_dir)), None
            except Exception as ex:  # noqa: BLE001
                res, err = None, ex
            app.ui(self._scanned, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _state(self, text, color=None):
        if self.d.winfo_exists():
            self.info.configure(text=text, fg=color or C["muted"])

    def _scanned(self, res, err):
        if not self.d.winfo_exists():
            return
        if err:
            self._state("Не удалось проверить файлы на Modrinth: %s" % err, C["err"])
            return
        self.scan = res
        self.ok.config_pill(enabled=True)
        self._update_info()

    def _update_info(self):
        if not self.scan:
            return
        files, disabled, urls, configs = self.scan
        linked = [f for f in files if f["sha1"] in urls]
        other = [f for f in files if f["sha1"] not in urls]
        text = "Ссылками на Modrinth: %d из %d файлов." % (len(linked), len(files))
        if other:
            names = ", ".join(os.path.basename(f["path"]) for f in other[:5])
            names += " и ещё %d" % (len(other) - 5) if len(other) > 5 else ""
            text += ("\nВнутрь файла: " if self.embed_var.get() else "\nНе войдут: ") + names
            if self.embed_var.get():
                text += "\nНа Modrinth такой пак не загрузить, если у этих файлов нет разрешения на распространение."
        if self.config_var.get():
            text += "\nФайлов настроек: %d." % len(configs)
        if disabled:
            text += "\nОтключённые моды не войдут: %d." % len(disabled)
        self._state(text, C["warn"] if other else C["muted"])

    def _save(self):
        if not self.scan:
            return
        name, ver = self.name_var.get().strip(), self.ver_var.get().strip()
        if not name or len(name) > 40:
            self._state("Название: от 1 до 40 символов", C["err"])
            return
        path = filedialog.asksaveasfilename(
            parent=self.d, title="Сохранить модпак", defaultextension=".mrpack",
            initialfile="%s-%s.mrpack" % (packs.slug(name), packs.slug(ver or "1.0.0")),
            filetypes=[("Модпак Modrinth", "*.mrpack")])
        if not path:
            return
        files, _disabled, urls, configs = self.scan
        _vid, ld, lv, mc, _n = self.build
        index = mrpack.build_index(name, ver, "", mc, ld, lv, files, urls)
        embed = [(f["path"], f["abs"]) for f in files if f["sha1"] not in urls] if self.embed_var.get() else []
        if self.config_var.get():
            embed += configs
        self.ok.config_pill(enabled=False)
        self._state("Сохраняю...")
        app = self.app

        def work():
            try:
                mrpack.write(path, index, embed)
                err = None
            except Exception as ex:  # noqa: BLE001
                err = ex
            app.ui(self._saved, path, err)
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, path, err):
        if not self.d.winfo_exists():
            return
        if err:
            self.ok.config_pill(enabled=True)
            self._state("Не удалось сохранить: %s" % err, C["err"])
            return
        self.app.log("Сборка экспортирована: %s" % path)
        self._state("Сохранено: %s" % path, C["accent"])
        self.ok.config_pill(text="Показать файл", command=lambda: _show_in_folder(path), enabled=True)
        self.cancel_btn.config_pill(text="Готово")


def _show_in_folder(path):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


class ShareDialog:
    """Код для текущей сборки: друг вставит его в «Сборка по коду» и получит такую же сборку."""

    def __init__(self, page):
        from .dialogs import Dialog
        self.page, self.app = page, page.app
        app, px = self.app, self.app.px
        _vid, ld, lv, mc, name = page.build
        self.build = page.build
        self.files = None
        d = Dialog(app, "Поделиться сборкой", "Поделиться сборкой", "друг вставит код и получит такую же сборку")
        self.d = d
        f = d.f
        tk.Label(f, text="Название", bg=C["panel"], fg=C["fg"], font=app.F.b(10, "bold")).pack(anchor="w")
        self.name_var = tk.StringVar(value=name or "%s %s" % (loaders.LOADER_NAMES[ld], mc))
        ef, _e = make_entry(f, app.A, self.name_var, width=44, font=app.F.b(11))
        ef.pack(fill="x", pady=(px(4), px(12)))
        tk.Label(f, text="Minecraft %s  ·  %s %s" % (mc, loaders.LOADER_NAMES[ld], lv), bg=C["panel"],
                 fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        self.info = tk.Label(f, text="Проверяю моды, ресурс-паки и шейдеры...", bg=C["panel"], fg=C["muted"],
                             font=app.F.b(9), wraplength=px(440), justify="left")
        self.info.pack(anchor="w", pady=(px(8), 0))
        self.result = tk.Frame(f, bg=C["panel"])
        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", side="bottom", pady=(px(16), 0))
        self.ok = d.btn(bf, "Получить код", self._publish, "accent")
        self.ok.config_pill(enabled=False)
        self.ok.pack(side="right")
        self.cancel_btn = d.btn(bf, "Отмена", d.destroy)
        self.cancel_btn.pack(side="right", padx=px(8))
        d.bind("<Escape>", lambda ev: d.destroy())
        self.busy = True
        d.center()
        d.grab_set()
        game_dir = os.path.dirname(page.mods_dir())

        def work():
            try:
                res, err = packs.collect(game_dir), None
            except Exception as ex:  # noqa: BLE001
                res, err = None, ex
            app.ui(self._collected, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _state(self, text, color=None):
        if self.d.winfo_exists():
            self.info.configure(text=text, fg=color or C["muted"])

    def _collected(self, res, err):
        if not self.d.winfo_exists():
            return
        self.busy = False
        if err:
            self._state("Не удалось проверить файлы сборки: %s" % err, C["err"])
            return
        self.files, skipped = res
        text = "В код войдут: %s." % _pack_summary(self.files)
        if skipped:
            names = ", ".join(skipped[:6]) + (" и ещё %d" % (len(skipped) - 6) if len(skipped) > 6 else "")
            text += "\nНе войдут (их нет на Modrinth, друг поставит их сам): " + names
        text += "\nНастройки и миры в код не входят."
        self._state(text, C["warn"] if skipped else C["muted"])
        self.ok.config_pill(enabled=True)

    def _publish(self):
        if self.busy or self.files is None:
            return
        name = self.name_var.get().strip()
        if not name or len(name) > 40:
            self._state("Название: от 1 до 40 символов", C["err"])
            return
        _vid, ld, lv, mc, _n = self.build
        pack = packs.make_pack(name, mc, ld, lv, self.files)
        self.busy = True
        self.ok.config_pill(enabled=False)
        self._state("Получаю код на сайте ZeleesLauncher...")
        self.app.ms_call(lambda acc: packs.publish(self.app.premium, acc, pack), self._published)

    def _published(self, res, err):
        if not self.d.winfo_exists():
            return
        self.busy = False
        if err:
            self.ok.config_pill(enabled=True)
            self._state("Не удалось получить код: %s" % err, C["err"])
            return
        app, px = self.app, self.app.px
        self.code, self.url = res["code"], res.get("url") or ""
        self.ok.pack_forget()
        self.cancel_btn.config_pill(text="Готово")
        self.result.pack(fill="x", pady=(px(14), 0))
        tk.Label(self.result, text=self.code, bg=C["panel"], fg=C["accent"], font=app.F.t(26)).pack(anchor="w")
        tk.Label(self.result, text="Друг открывает «Моды» → «Сборка по коду» и вставляет код или ссылку",
                 bg=C["panel"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w", pady=(px(2), px(8)))
        row = tk.Frame(self.result, bg=C["panel"])
        row.pack(anchor="w")
        self.d.btn(row, "Копировать код", lambda: self._copy(self.code), "accent").pack(side="left")
        if self.url:
            self.d.btn(row, "Копировать ссылку", lambda: self._copy(self.url)).pack(side="left", padx=px(8))
        self._copy(self.code)
        self.app.log("Код сборки «%s»: %s %s" % (self.name_var.get().strip(), self.code, self.url))

    def _copy(self, text):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)
        self._state("Скопировано: %s" % text, C["accent"])


# =====================================================================================
class PackCodeDialog:
    """Ввод кода сборки: показывает, что в ней, и ставит версию, загрузчик и все файлы."""

    def __init__(self, page):
        from .dialogs import Dialog
        self.page, self.app = page, page.app
        app, px = self.app, self.app.px
        self.data = None
        d = Dialog(app, "Сборка по коду", "Сборка по коду", "версия, загрузчик и моды поставятся сами")
        self.d = d
        f = d.f
        tk.Label(f, text="Код или ссылка", bg=C["panel"], fg=C["fg"], font=app.F.b(10, "bold")).pack(anchor="w")
        self.code_var = tk.StringVar()
        try:
            clip = packs.parse_code(app.root.clipboard_get())
        except tk.TclError:
            clip = None
        if clip:
            self.code_var.set(clip)
        row = tk.Frame(f, bg=C["panel"])
        row.pack(fill="x", pady=(px(4), px(10)))
        ef, self.entry = make_entry(row, app.A, self.code_var, width=24, font=app.F.b(13))
        ef.pack(side="left", fill="x", expand=True)
        d.btn(row, "Найти", self._lookup, "sel").pack(side="left", padx=(px(8), 0))
        self.entry.focus_set()
        self.info = tk.Label(f, text="Например: K7QF-M2XA или https://zelees.link/p/K7QF-M2XA", bg=C["panel"],
                             fg=C["dim"], font=app.F.b(9), wraplength=px(440), justify="left")
        self.info.pack(anchor="w")
        self.preview = tk.Frame(f, bg=C["panel2"], highlightthickness=1, highlightbackground=C["line"])
        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", side="bottom", pady=(px(16), 0))
        self.ok = d.btn(bf, "Установить", self._install, "accent")
        self.ok.config_pill(enabled=False)
        self.ok.pack(side="right")
        d.btn(bf, "Отмена", d.destroy).pack(side="right", padx=px(8))
        d.bind("<Return>", lambda ev: self._install() if self.data else self._lookup())
        d.bind("<Escape>", lambda ev: d.destroy())
        self.code_var.trace_add("write", lambda *a: self._code_changed())
        d.center()
        d.grab_set()
        if clip:
            self._lookup()

    def _state(self, text, color=None):
        if self.d.winfo_exists():
            self.info.configure(text=text, fg=color or C["muted"])

    def _code_changed(self):
        if self.data and packs.parse_code(self.code_var.get()) != self.data["code"]:
            self.data = None
            self.ok.config_pill(enabled=False)
            self.preview.pack_forget()

    def _lookup(self):
        code = packs.parse_code(self.code_var.get())
        if not code:
            self._state("Код — это 8 букв и цифр, например K7QF-M2XA", C["err"])
            return
        self._state("Ищу сборку %s..." % code)

        def work():
            try:
                res, err = packs.fetch(code), None
            except Exception as ex:  # noqa: BLE001
                res, err = None, ex
            self.app.ui(self._found, code, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _found(self, code, res, err):
        if not self.d.winfo_exists() or packs.parse_code(self.code_var.get()) != code:
            return
        if err:
            self._state(str(err), C["err"])
            return
        self.data = res
        pk = res["pack"]
        app, px = self.app, self.app.px
        for w in self.preview.winfo_children():
            w.destroy()
        self.preview.pack(fill="x", pady=(px(12), 0))
        inner = tk.Frame(self.preview, bg=C["panel2"])
        inner.pack(fill="x", padx=px(14), pady=px(12))
        tk.Label(inner, text=res["name"], bg=C["panel2"], fg=C["fg"], font=app.F.b(13, "bold")).pack(anchor="w")
        tk.Label(inner, text="Minecraft %s  ·  %s %s" % (pk["mc"], loaders.LOADER_NAMES.get(pk["loader"], pk["loader"]),
                                                         pk["loader_version"]),
                 bg=C["panel2"], fg=C["muted"], font=app.F.b(10)).pack(anchor="w", pady=(px(2), 0))
        meta = _pack_summary(pk["files"])
        if res.get("author"):
            meta += "   ·   автор: " + res["author"]
        tk.Label(inner, text=meta, bg=C["panel2"], fg=C["muted"], font=app.F.b(9)).pack(anchor="w")
        note = "Будет создана отдельная сборка со своей папкой — ваши другие сборки и миры не изменятся."
        if any(f["source"] == "curseforge" for f in pk["files"]) and not (
                app.settings.get("curseforge_key") or "").strip():
            note += "\nВ сборке есть моды с CurseForge: без API-ключа CurseForge («Опции») они не скачаются."
        self._state(note)
        self.ok.config_pill(enabled=True)

    def _install(self):
        if not self.data:
            return
        app = self.app
        if app.busy:
            self._state("Подождите — лаунчер уже занят", C["warn"])
            return
        data = self.data
        try:
            settings = app._collect_settings()
        except ValueError as e:
            self._state(str(e), C["err"])
            return
        self.d.destroy()
        app.show_page("home")
        app._set_busy(True)

        def work():
            pk = data["pack"]
            root = settings["game_dir"]
            vid = None
            try:
                ld, lv, mc = pk["loader"], pk["loader_version"], pk["mc"]
                parent = packs.find_loader_version(root, app.manifest, ld, lv, mc)
                if not parent:
                    parent = install_loader(app, settings, ld, mc, lv)
                app.ui(app.status, "Сборка «%s»: получаю список файлов..." % data["name"])
                items, failed = packs.resolve_files(pk, (settings.get("curseforge_key") or "").strip())
                vid = packs.create_pack_version(root, app.manifest, data["name"], data["code"], parent)
                game_dir = instance_dir(settings, root, vid)
                packs.install_files(game_dir, items, settings.get("download_threads", 16), app.progress, app.cancel)
                app.ui(app.log, "Сборка «%s» (%s) установлена: %s, файлов: %d" % (
                    data["name"], data["code"], vid, len(items)))
                for what, why in failed:
                    app.ui(app.log, "Не скачано: %s — %s" % (what, why), "WARN")
                app.ui(_build_done, app, vid, data["name"], failed, "Сборка по коду")
            except net.Cancelled:
                _remove_build(root, vid)
                app.ui(app.status, "Отменено", C["warn"])
            except Exception as e:  # noqa: BLE001
                _remove_build(root, vid)
                app.ui(app.log, traceback.format_exc(), "ERROR")
                app.ui(app._error, e)
            finally:
                app.ui(app._set_busy, False)
        app.bg(work)
