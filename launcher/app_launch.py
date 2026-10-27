"""Установка версии и запуск игры (в том числе сразу на сервер)."""
import os
from tkinter import messagebox

from . import auth, java as javamod, net
from .config import APP_NAME
from .installer import install_version
from .launch import build_command, instance_dir, mask_cmd, start_process
from .ui_kit import C
from .versions import required_java, resolve_version

class LaunchMixin:
    """Установка версии и запуск игры (в том числе сразу на сервер)."""

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
