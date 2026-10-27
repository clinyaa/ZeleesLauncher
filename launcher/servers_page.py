"""Вкладка «Сервера» (сервера игроков с Premium: пинг, онлайн, быстрый вход) и окно Premium."""
import base64
import threading
import time
import webbrowser
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox

from . import mc_status
from .ui_kit import C, PillButton, ScrollFrame, make_entry

LETTER_COLORS = ["#2f6b45", "#6b4a2f", "#2f4f6b", "#5a2f6b", "#6b2f3f", "#4f6b2f"]
DEFAULT_PRICE = "100 ₽ в месяц"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]


def fmt_until(ts):
    t = time.localtime(ts)
    return "%d %s %d" % (t.tm_mday, MONTHS[t.tm_mon - 1], t.tm_year)


def ping_color(ms):
    if ms < 80:
        return C["accent"]
    if ms < 160:
        return "#c8e050"
    if ms < 300:
        return C["warn"]
    return C["err"]


class ServersPage:
    def __init__(self, app):
        self.app = app
        self.A, self.F, self.px = app.A, app.F, app.px
        self.entries = []
        self.rows = {}
        self.results = {}
        self.icons = {}
        self.loaded_at = 0
        self.seq = 0
        self.pool = ThreadPoolExecutor(max_workers=8)
        self._build_ui()

    # ------------------------------------------------------------------ разметка
    def _build_ui(self):
        app, px = self.app, self.px
        body = app._page("servers", "Сервера", "Сервера игроков ZeleesLauncher — пинг, онлайн и вход в игру в один клик")
        top = tk.Frame(body, bg=C["bg"])
        top.pack(fill="x", pady=(0, px(12)))
        self.add_btn = app._btn(top, "+ Добавить свой сервер", self._add_clicked, style="sel")
        self.add_btn.pack(side="left")
        app._btn(top, "Обновить", self.reload, padx=14).pack(side="right")
        self.summary = app._label(top, "", muted=True)
        self.summary.pack(side="right", padx=px(12))
        self.list = ScrollFrame(body, bg=C["bg"])
        self.list.pack(fill="both", expand=True)
        self.status = app._label(body, "", muted=True, size=9, anchor="w")
        self.status.pack(fill="x", pady=(px(8), 0))

    def on_show(self):
        self.on_premium_changed()
        if time.time() - self.loaded_at > 60:
            self.reload()

    def on_premium_changed(self):
        acc = self.app.current_ms_account()
        mine = acc and self.app.premium.is_premium(acc["id"])
        self.add_btn.config_pill(style="accent" if mine else "sel")

    def _set_status(self, text, color=None):
        self.status.configure(text=text, fg=color or C["muted"])

    # ------------------------------------------------------------------ список
    def reload(self):
        self.seq += 1
        seq = self.seq
        self.loaded_at = time.time()
        self._set_status("Загружаю список серверов...")

        def work():
            try:
                servers = self.app.premium.servers()
            except Exception as e:  # noqa: BLE001
                self.app.ui(self._load_failed, seq, e)
                return
            self.app.ui(self._show, seq, servers)
        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, seq, e):
        if seq != self.seq:
            return
        self.loaded_at = 0
        self.list.clear()
        self.rows = {}
        self._set_status("Не удалось загрузить список: %s" % e, C["err"])

    def _show(self, seq, servers):
        if seq != self.seq:
            return
        self.list.clear()
        self.entries = servers
        self.rows = {}
        self.results = {}
        if not servers:
            self._empty_state()
            self.summary.configure(text="")
            self._set_status("")
            return
        for e in servers:
            self._server_row(e)
            self.pool.submit(self._ping, seq, e)
        self._update_summary()
        self._set_status("Пинг считается от вашего компьютера до сервера")

    def _empty_state(self):
        px = self.px
        card = tk.Frame(self.list.inner, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x", pady=px(20), padx=(0, px(8)))
        tk.Label(card, image=self.A.icon("servers", "_on"), bg=C["panel"]).pack(pady=(px(28), px(10)))
        tk.Label(card, text="Здесь пока пусто", bg=C["panel"], fg=C["fg"], font=self.F.t(15)).pack()
        tk.Label(card, text="Владельцы Premium могут добавить сюда свой сервер — его увидят все игроки лаунчера",
                 bg=C["panel"], fg=C["muted"], font=self.F.b(10)).pack(pady=(px(4), px(16)))
        PillButton(card, self.A, "Добавить свой сервер", command=self._add_clicked, style="accent",
                   font=self.F.b(10, "bold"), bg=C["panel"]).pack(pady=(0, px(28)))

    def _icon(self, parent, e, bg):
        size = self.px(48)
        cv = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        col = LETTER_COLORS[sum(map(ord, e["name"])) % len(LETTER_COLORS)]
        cv.create_rectangle(0, 0, size, size, fill=col, width=0)
        cv.create_text(size // 2, size // 2, text=e["name"][:1].upper(), fill="white", font=self.F.t(17))
        return cv

    def _server_row(self, e):
        px = self.px
        acc = self.app.current_ms_account()
        mine = bool(acc and acc["id"] == e["owner"])
        row = tk.Frame(self.list.inner, bg=C["panel"], highlightthickness=1,
                       highlightbackground="#5a4a1c" if mine else C["line"])
        row.pack(fill="x", pady=(0, px(8)), padx=(0, px(8)))
        r = tk.Frame(row, bg=C["panel"])
        r.pack(fill="x", padx=px(14), pady=px(12))
        icon = self._icon(r, e, C["panel"])
        icon.pack(side="left", anchor="n")

        right = tk.Frame(r, bg=C["panel"])
        right.pack(side="right")
        stats = tk.Frame(right, bg=C["panel"])
        stats.pack(side="left", padx=(0, px(14)))
        online = tk.Label(stats, text="…", bg=C["panel"], fg=C["muted"], font=self.F.b(10, "bold"), anchor="e")
        online.pack(anchor="e")
        ping = tk.Label(stats, text="пинг…", bg=C["panel"], fg=C["dim"], font=self.F.b(9), anchor="e")
        ping.pack(anchor="e")
        PillButton(right, self.A, "Играть", command=lambda: self._play(e), style="accent",
                   font=self.F.b(10, "bold"), bg=C["panel"], minw=110).pack(side="left")

        mid = tk.Frame(r, bg=C["panel"])
        mid.pack(side="left", fill="x", expand=True, padx=px(14))
        t = tk.Frame(mid, bg=C["panel"])
        t.pack(fill="x")
        tk.Label(t, text=e["name"], bg=C["panel"], fg=C["fg"], font=self.F.b(12, "bold")).pack(side="left")
        if mine:
            tk.Label(t, text="  ★ ваш сервер", bg=C["panel"], fg=C["gold"], font=self.F.b(9, "bold")).pack(side="left")
            for text, cmd in (("Удалить", lambda: self._delete(e)), ("Изменить", lambda: ServerDialog(self, e))):
                lb = tk.Label(t, text=text, bg=C["panel"], fg=C["muted"], font=self.F.b(9, "underline"),
                              cursor="hand2")
                lb.pack(side="right", padx=(px(10), 0))
                lb.bind("<Button-1>", lambda ev, c=cmd: c())
        desc = tk.Label(mid, text=e.get("description") or "", bg=C["panel"], fg="#b5bac4", font=self.F.b(9),
                        anchor="w", justify="left", wraplength=px(520))
        desc.pack(fill="x")
        meta = tk.Label(mid, text=mc_status.display_address(e["address"], e["port"]), bg=C["panel"], fg=C["dim"],
                        font=self.F.b(8), anchor="w")
        meta.pack(fill="x", pady=(px(2), 0))
        self.rows[e["id"]] = dict(icon=icon, online=online, ping=ping, desc=desc, meta=meta)

    def _ping(self, seq, e):
        try:
            res = mc_status.ping(e["address"], e["port"])
        except mc_status.PingError as ex:
            res = {"error": str(ex)}
        except Exception as ex:  # noqa: BLE001
            res = {"error": "ошибка: %s" % ex}
        self.app.ui(self._apply, seq, e, res)

    def _apply(self, seq, e, res):
        w = self.rows.get(e["id"])
        if seq != self.seq or not w or not w["online"].winfo_exists():
            return
        self.results[e["id"]] = res
        addr = mc_status.display_address(e["address"], e["port"])
        if "error" in res:
            w["online"].configure(text="● оффлайн", fg=C["err"])
            w["ping"].configure(text=res["error"], fg=C["dim"])
            w["meta"].configure(text=addr)
        else:
            w["online"].configure(text="● %d / %d" % (res["online"], res["max"]), fg=C["accent"])
            w["ping"].configure(text="%d мс" % res["ping"], fg=ping_color(res["ping"]))
            w["meta"].configure(text=addr + ("   ·   " + res["version"] if res["version"] else ""))
            if not e.get("description") and res["motd"]:
                w["desc"].configure(text=res["motd"])
            if res.get("favicon"):
                self._set_favicon(w["icon"], res["favicon"])
        self._update_summary()

    def _set_favicon(self, cv, data):
        size = int(cv.cget("width"))
        try:
            img = tk.PhotoImage(data=base64.b64encode(data))
            if img.width() > size:
                img = img.zoom(2).subsample(max(1, -(-img.width() * 2 // size)))
        except tk.TclError:
            return
        self.icons[id(cv)] = img
        cv.delete("all")
        cv.create_image(size // 2, size // 2, image=img)

    def _update_summary(self):
        ok = [r for r in self.results.values() if "error" not in r]
        text = "Серверов: %d" % len(self.entries)
        if ok:
            text += "   ·   игроков онлайн: %d" % sum(r["online"] for r in ok)
        self.summary.configure(text=text)

    # ------------------------------------------------------------------ действия
    def _play(self, e):
        self.app.play_on_server(e, self.results.get(e["id"]))

    def _add_clicked(self):
        acc = self.app.current_ms_account()
        if not acc or not self.app.premium.is_premium(acc["id"]):
            PremiumDialog(self.app, reason="Добавлять свой сервер можно с Premium")
            return
        ServerDialog(self)

    def _delete(self, e):
        if not messagebox.askyesno("Сервера", "Удалить «%s» из списка серверов?" % e["name"], parent=self.app.root):
            return
        self._set_status("Удаляю «%s»..." % e["name"])

        def done(_res, err):
            if err:
                self._set_status("Не удалось удалить: %s" % err, C["err"])
                return
            self.app.status("Сервер «%s» удалён из списка" % e["name"], C["accent"])
            self.reload()
        self.app.ms_call(lambda acc: self.app.premium.delete_server(acc, e["id"]), done)


# =====================================================================================
class ServerDialog:
    """Добавление или изменение своего сервера."""

    def __init__(self, page, entry=None):
        from .dialogs import Dialog
        self.page, self.app, self.entry = page, page.app, entry
        app, px = self.app, self.app.px
        d = Dialog(app, "Сервер", "Изменить сервер" if entry else "Новый сервер",
                   "его увидят все игроки ZeleesLauncher")
        self.d = d
        f = d.f
        self.name_var = tk.StringVar(value=entry["name"] if entry else "")
        self.addr_var = tk.StringVar(value=mc_status.display_address(entry["address"], entry["port"]) if entry else "")
        self.desc_var = tk.StringVar(value=entry.get("description", "") if entry else "")
        for label, var, hint in (("Название", self.name_var, "2–32 символа"),
                                 ("Адрес сервера", self.addr_var, "play.example.com или IP, порт через двоеточие"),
                                 ("Описание", self.desc_var, "необязательно, до 100 символов")):
            row = tk.Frame(f, bg=C["panel"])
            row.pack(fill="x", pady=(0, px(10)))
            tk.Label(row, text=label, bg=C["panel"], fg=C["fg"], font=app.F.b(10, "bold")).pack(side="left")
            tk.Label(row, text="   " + hint, bg=C["panel"], fg=C["dim"], font=app.F.b(8)).pack(side="left")
            ef, e = make_entry(f, app.A, var, width=44, font=app.F.b(11))
            ef.pack(fill="x", pady=(0, px(12)))
            if label == "Название":
                e.focus_set()
        self.info = tk.Label(f, text="", bg=C["panel"], fg=C["muted"], font=app.F.b(9), wraplength=px(440),
                             justify="left")
        self.info.pack(anchor="w")
        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", pady=(px(14), 0))
        self.ok = d.btn(bf, "Сохранить", self._save, "accent")
        self.ok.pack(side="right")
        d.btn(bf, "Отмена", d.destroy).pack(side="right", padx=px(8))
        d.bind("<Return>", lambda ev: self._save())
        d.bind("<Escape>", lambda ev: d.destroy())
        self.busy = False
        d.center()
        d.grab_set()

    def _state(self, text, color=None):
        if self.d.winfo_exists():
            self.info.configure(text=text, fg=color or C["muted"])

    def _save(self):
        if self.busy:
            return
        name, desc = self.name_var.get().strip(), self.desc_var.get().strip()
        if not 2 <= len(name) <= 32:
            self._state("Название: от 2 до 32 символов", C["err"])
            return
        if len(desc) > 100:
            self._state("Описание: не длиннее 100 символов", C["err"])
            return
        try:
            host, port = mc_status.parse_address(self.addr_var.get())
        except ValueError as e:
            self._state(str(e), C["err"])
            return
        if not host:
            self._state("Укажите адрес сервера", C["err"])
            return
        fields = {"name": name, "address": host, "port": port, "description": desc}
        self.busy = True
        self._state("Проверяю, отвечает ли сервер...")

        def work():
            try:
                res = mc_status.ping(host, port)
                err = None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.app.ui(self._checked, fields, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _checked(self, fields, res, err):
        if not self.d.winfo_exists():
            return
        if err is not None:
            if not messagebox.askyesno("Сервер", "Сервер %s не ответил (%s).\n\nВсё равно сохранить?" % (
                    mc_status.display_address(fields["address"], fields["port"]), err), parent=self.d):
                self.busy = False
                self._state("")
                return
        else:
            self._state("Сервер отвечает: %d мс, онлайн %d / %d. Сохраняю..." % (res["ping"], res["online"], res["max"]))
        prem = self.app.premium
        if self.entry:
            fn = lambda acc: prem.update_server(acc, self.entry["id"], fields)  # noqa: E731
        else:
            fn = lambda acc: prem.add_server(acc, fields)  # noqa: E731
        self.app.ms_call(fn, self._done)

    def _done(self, _res, err):
        self.busy = False
        if err:
            self._state(str(err), C["err"])
            return
        self.app.status("Сервер «%s» сохранён — он уже виден в списке" % self.name_var.get().strip(), C["accent"])
        try:
            self.d.destroy()
        except tk.TclError:
            pass
        self.page.reload()


# =====================================================================================
class PremiumDialog:
    """Статус Premium, что он даёт и как купить: автоматически через Boosty (код в личные сообщения автору),
    а если Boosty на сайте не настроен — вручную через админку."""

    def __init__(self, app, reason=None):
        from .dialogs import Dialog
        self.app = app
        px = app.px
        info = app.premium.info or {}
        d = Dialog(app, "Premium", "ZeleesLauncher Premium", info.get("price") or DEFAULT_PRICE, width=500)
        self.d = d
        f = d.f
        self.acc = app.current_ms_account()
        if reason:
            tk.Label(f, text=reason, bg=C["panel"], fg=C["warn"], font=app.F.b(10, "bold")).pack(anchor="w",
                                                                                               pady=(0, px(10)))
        self.state = tk.Label(f, text="", bg=C["panel"], fg=C["fg"], font=app.F.b(11, "bold"), justify="left",
                              wraplength=px(470))
        self.state.pack(anchor="w", pady=(0, px(12)))

        for line in ("Свой сервер во вкладке «Сервера» — его увидят все игроки лаунчера",
                     "Золотой значок Premium рядом с ником",
                     "Поддержка развития ZeleesLauncher"):
            r = tk.Frame(f, bg=C["panel"])
            r.pack(fill="x", pady=px(2))
            tk.Label(r, text="★", bg=C["panel"], fg=C["gold"], font=app.F.b(11, "bold")).pack(side="left")
            tk.Label(r, text=line, bg=C["panel"], fg=C["fg"], font=app.F.b(10)).pack(side="left", padx=px(8))

        tk.Label(f, text="КАК КУПИТЬ", bg=C["panel"], fg=C["muted"], font=app.F.b(8, "bold")).pack(
            anchor="w", pady=(px(16), px(4)))
        self.how = tk.Label(f, text=info.get("contact_text") or "Загрузка...", bg=C["panel"], fg="#c9ced8",
                            font=app.F.b(10), justify="left", wraplength=px(470))
        self.how.pack(anchor="w")
        self.boosty_url = info.get("boosty_url") or ""
        self.code_box = tk.Frame(f, bg=C["panel2"])  # код для Boosty, показывается после «Оплатить через Boosty»
        tk.Label(self.code_box, text="Ваш код:", bg=C["panel2"], fg=C["muted"], font=app.F.b(10)).pack(
            side="left", padx=(px(12), px(8)), pady=px(10))
        self.code_lbl = tk.Label(self.code_box, text="", bg=C["panel2"], fg=C["gold"], font=(app.F.mono, 16, "bold"))
        self.code_lbl.pack(side="left")
        cc = tk.Label(self.code_box, text="Скопировать", bg=C["panel2"], fg=C["accent"],
                      font=app.F.b(9, "underline"), cursor="hand2")
        cc.pack(side="right", padx=px(12))
        cc.bind("<Button-1>", lambda e: self._copy_code())
        if self.acc:
            idf = tk.Frame(f, bg=C["panel2"])
            idf.pack(fill="x", pady=(px(12), 0))
            tk.Label(idf, text="Ваш аккаунт:  %s" % self.acc["name"], bg=C["panel2"], fg=C["fg"],
                     font=app.F.b(10, "bold")).pack(side="left", padx=px(12), pady=px(8))
            cp = tk.Label(idf, text="Скопировать ник и UUID", bg=C["panel2"], fg=C["accent"],
                          font=app.F.b(9, "underline"), cursor="hand2")
            cp.pack(side="right", padx=px(12))
            cp.bind("<Button-1>", lambda e: self._copy())

        bf = tk.Frame(f, bg=C["panel"])
        bf.pack(fill="x", pady=(px(18), 0))
        self.buy = d.btn(bf, "Купить", self._buy, "accent")
        self.buy.pack(side="right")
        if self.acc:
            self.check = d.btn(bf, "Проверить оплату", self._check)
            self.check.pack(side="right", padx=px(8))
        else:
            d.btn(bf, "Войти через Microsoft", self._login, "sel").pack(side="right", padx=px(8))
        d.btn(bf, "Закрыть", d.destroy).pack(side="left")
        d.bind("<Escape>", lambda ev: d.destroy())
        self._apply_info(info)
        d.center()
        d.grab_set()
        threading.Thread(target=self._load_info, daemon=True).start()
        if self.acc:
            self._check(quiet=True)

    def _render(self, note=None, color=None):
        if not self.d.winfo_exists():
            return
        if not self.acc:
            text, col = ("Premium привязывается к лицензионному аккаунту Minecraft. "
                         "Войдите через Microsoft, чтобы купить и использовать Premium."), C["warn"]
        elif self.app.premium.is_premium(self.acc["id"]):
            bo = self.app.premium.boosty(self.acc["id"])
            if bo.get("active"):
                text = "★ Premium активен — подписка Boosty%s" % (" (%s)" % bo["name"] if bo.get("name") else "")
            else:
                text = "★ Premium активен до %s" % fmt_until(self.app.premium.until(self.acc["id"]))
            col = C["gold"]
        else:
            until = self.app.premium.until(self.acc["id"])
            text = ("Premium закончился %s" % fmt_until(until)) if until else "Premium не активен"
            col = C["fg"]
        if note:
            text += "\n" + note
        self.state.configure(text=text, fg=color or col)
        active = self.acc and self.app.premium.is_premium(self.acc["id"])
        if self.boosty_url and self.acc:
            label = "Открыть Boosty" if self.app.premium.boosty(self.acc["id"]).get("active") else "Оплатить через Boosty"
        else:
            label = "Продлить" if active else "Купить"
        self.buy.config_pill(text=label)

    def _apply_info(self, info):
        self.boosty_url = info.get("boosty_url") or ""
        if self.boosty_url:
            self.how.configure(text="1. Нажмите «Оплатить через Boosty» — лаунчер покажет и скопирует ваш код.\n"
                                    "2. Оформите на Boosty подписку уровня «Premium».\n"
                                    "3. Отправьте код автору в личные сообщения на Boosty.\n"
                                    "Premium включится сам в течение нескольких минут и будет продлеваться, "
                                    "пока активна подписка.", fg="#c9ced8")
        elif info:
            self.how.configure(text=info.get("contact_text") or "", fg="#c9ced8")
        self._render()

    def _load_info(self):
        try:
            info = self.app.premium.load_info()
        except Exception as e:  # noqa: BLE001
            self.app.ui(self._info_failed, e)
            return
        self.app.ui(self._info_loaded, info)

    def _info_loaded(self, info):
        if self.d.winfo_exists():
            self._apply_info(info)

    def _info_failed(self, e):
        if self.d.winfo_exists() and not self.app.premium.info:
            self.how.configure(text="Не удалось связаться с сайтом лаунчера: %s" % e, fg=C["err"])

    def _copy(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append("Ник: %s\nUUID: %s" % (self.acc["name"], self.acc["id"]))
        self.app.status("Ник и UUID скопированы — отправьте их вместе с оплатой", C["accent"])

    def _buy(self):
        if not self.acc:
            self._login()
            return
        if self.boosty_url:
            self._boosty()
            return
        url =(self.app.premium.info or {}).get("contact_url") or ""
        if url.startswith(("https://", "http://", "tg://")):
            self._copy()
            webbrowser.open(url)
        else:
            self._copy()
            self._render("Ник и UUID скопированы. Отправьте их администратору вместе с оплатой.", C["fg"])

    def _boosty(self):
        if self.app.premium.boosty(self.acc["id"]).get("active"):
            webbrowser.open(self.boosty_url)
            return
        self._render("Получаю код...", C["muted"])

        def done(res, err):
            if not self.d.winfo_exists():
                return
            if err:
                self._render("Не удалось получить код: %s" % err, C["err"])
                return
            self.code_lbl.configure(text=res["code"])
            if not self.code_box.winfo_ismapped():
                self.code_box.pack(fill="x", pady=(self.app.px(12), 0), after=self.how)
            self._copy_code()
            webbrowser.open(res.get("boosty_url") or self.boosty_url)
            self._render("Код скопирован. Оформите подписку «Premium» на Boosty и отправьте этот код автору "
                         "в личные сообщения.", C["fg"])
            self.d.center()
        self.app.ms_call(self.app.premium.boosty_code, done)

    def _copy_code(self):
        code = self.code_lbl.cget("text")
        if code:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(code)
            self.app.status("Код %s скопирован — отправьте его в личные сообщения на Boosty" % code, C["accent"])

    def _login(self):
        self.d.destroy()
        self.app._add_microsoft()

    def _check(self, quiet=False):
        if not quiet:
            self._render("Проверяю...", C["muted"])

        def done(_data, err):
            if not self.d.winfo_exists():
                return
            if err:
                self._render("Не удалось проверить: %s" % err, C["err"])
            elif not quiet and not self.app.premium.is_premium(self.acc["id"]):
                if self.boosty_url:
                    self._render("Premium ещё не включился. Проверьте, что подписка «Premium» оформлена и код "
                                 "отправлен автору в личные сообщения на Boosty. Сайт проверяет Boosty раз в 3 минуты.")
                else:
                    self._render("Оплата пока не отмечена. Если вы уже оплатили — подождите, Premium выдают вручную.")
            else:
                self._render()
        self.app.sync_premium(done)
