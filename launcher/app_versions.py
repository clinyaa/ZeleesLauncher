"""Список версий, поиск Java и сохранение настроек."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox

from . import java as javamod
from .app_util import TYPE_CUBE, fmt_date, log_to_file
from .config import APP_NAME
from .launch import instance_dir
from .ui_kit import C
from .versions import TYPE_NAMES, fetch_manifest, is_installed, local_versions, required_java, resolve_version

class VersionsMixin:
    """Список версий, поиск Java и сохранение настроек."""

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
            tname = "Сборка «%s»" % lv["name"] if lv.get("name") else "Модиф. / локальная"
            rows.append((lv["id"], tname, lv.get("releaseTime", ""), "local", lv.get("inheritsFrom")))
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
