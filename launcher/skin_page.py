"""Вкладка «Скин»: 3D-превью, смена скина и модели, библиотека скинов, плащи (для лицензии)."""
import hashlib
import math
import os
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

from . import auth, skins
from .config import CACHE_DIR, DATA_DIR
from .ui_kit import C, PillButton, face_photo

SKINS_DIR = os.path.join(DATA_DIR, "skins")
TEX_CACHE = os.path.join(CACHE_DIR, "textures")


class SkinPage:
    def __init__(self, app):
        self.app = app
        self.A, self.F, self.px = app.A, app.F, app.px
        self.acc = None
        self.profile = None
        self.skin = None          # skins.Image
        self.slim = False
        self.cape = None          # skins.Image | None
        self.capes = []           # [(id, alias, Image|None, active)]
        self.preview = None       # (path, bytes, Image) — скин из библиотеки до применения
        self.preview_slim = False
        self.faces = []
        self.yaw, self.pitch = 0.45, 0.2
        self.auto = True
        self.drag = None
        self.busy = False
        self._tiles = []
        self._anim = None
        self._loaded_for = False  # не None: «нет аккаунта» (None) тоже нужно отрисовать
        self._build()
        from . import skin_editor
        skin_editor.SKINS_DIR = SKINS_DIR
        self.editor = skin_editor.SkinEditor(app, self)

    # ------------------------------------------------------------------ разметка
    def _build(self):
        app, px = self.app, self.px
        body = app._page("skin", "Скин", "Скин и плащ лицензионного аккаунта — их видят все игроки")
        wrap = tk.Frame(body, bg=C["bg"])
        wrap.pack(fill="both", expand=True)

        left = tk.Frame(wrap, bg=C["bg"])
        left.pack(side="left", fill="y")
        st = self.A.stage()
        self.sw, self.sh = st.width(), st.height()
        self.cv = tk.Canvas(left, width=self.sw, height=self.sh, bg=C["panel"], highlightthickness=0, bd=0,
                            cursor="fleur")
        self.cv.pack()
        self.cv.create_image(0, 0, image=st, anchor="nw")
        self.cv.create_text(self.sw // 2, self.sh - px(18), text="Потяните мышью, чтобы повернуть",
                            fill=C["dim"], font=self.F.b(8))
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.cv.bind("<Double-1>", lambda e: self._set_view(0.45))
        vb = tk.Frame(left, bg=C["bg"])
        vb.pack(fill="x", pady=(px(10), 0))
        app._btn(vb, "Спереди", lambda: self._set_view(0.0), size="sm", padx=12).pack(side="left")
        app._btn(vb, "Сзади", lambda: self._set_view(math.pi), size="sm", padx=12).pack(side="left", padx=px(6))
        self.auto_btn = app._btn(vb, "Вращение", self._toggle_auto, style="sel", size="sm", padx=12)
        self.auto_btn.pack(side="right")

        col = tk.Frame(wrap, bg=C["bg"])
        col.pack(side="left", fill="both", expand=True, padx=(px(28), 0))
        self.status = app._label(col, "", muted=True, size=9, anchor="w", wraplength=px(480), justify="left")
        self.status.pack(side="bottom", fill="x")
        self.right = tk.Frame(col, bg=C["bg"])
        self.right.pack(side="top", fill="both", expand=True)

    def _set_status(self, text, color=None):
        self.status.configure(text=text, fg=color or C["muted"])

    def _section(self, parent, title):
        self.app._label(parent, " ".join(title), muted=True, size=8, bold=True).pack(anchor="w", pady=(self.px(16), self.px(6)))

    # ------------------------------------------------------------------ показ вкладки
    def on_show(self):
        acc = self.app.accounts.find(self.app.cur_acc_key) if self.app.cur_acc_key else None
        key = self.app.cur_acc_key
        if key != self._loaded_for:
            self._loaded_for = key
            self._set_status("")
            self.acc = acc
            self.preview = None
            self.profile = None
            self.capes = []
            self.cape = None
            if acc and acc.get("type") == "microsoft":
                self.skin = skins.procedural_skin(acc["name"])
                self._rebuild_model()
                self._draw_right()
                self._load_profile()
            else:
                self.skin = skins.procedural_skin(acc["name"] if acc else "Player")
                self.slim = False
                self._rebuild_model()
                self._draw_right()
        self._start_anim()

    def on_hide(self):
        if self._anim:
            self.app.root.after_cancel(self._anim)
            self._anim = None

    # ------------------------------------------------------------------ 3D
    def _rebuild_model(self):
        img = self.preview[2] if self.preview else self.skin
        slim = self.slim
        if self.preview:
            slim = self.preview_slim
        self.faces = skins.build_model(img, slim, self.cape) if img else []
        self._render()

    def _render(self):
        cv = self.cv
        cv.delete("m")
        if not self.faces:
            return
        scale = self.sh / 46.0
        polys = skins.project(self.faces, self.yaw, self.pitch, scale, self.sw / 2, self.sh * 0.47)
        cp = cv.create_polygon
        for _d, pts, col, _t in polys:
            cp(pts, fill=col, outline=col, width=1, tags="m")

    def _start_anim(self):
        if self._anim:
            return

        def tick():
            self._anim = None
            if self.app.page != "skin":
                return
            if self.auto and self.drag is None:
                self.yaw = (self.yaw + 0.035) % (2 * math.pi)
                self._render()
            self._anim = self.app.root.after(70, tick)
        tick()

    def _press(self, e):
        self.drag = (e.x, e.y)

    def _move(self, e):
        if self.drag is None:
            return
        dx, dy = e.x - self.drag[0], e.y - self.drag[1]
        self.drag = (e.x, e.y)
        self.yaw = (self.yaw + dx * 0.012) % (2 * math.pi)
        self.pitch = max(-0.5, min(0.7, self.pitch + dy * 0.008))
        self._render()

    def _set_view(self, yaw):
        self.yaw, self.pitch = yaw, 0.2
        self._render()

    def _toggle_auto(self):
        self.auto = not self.auto
        self.auto_btn.config_pill(style="sel" if self.auto else "dark")

    # ------------------------------------------------------------------ правая колонка
    def _draw_right(self):
        for w in self.right.winfo_children():
            w.destroy()
        app, px, r = self.app, self.px, self.right
        acc = self.acc
        head = tk.Frame(r, bg=C["bg"])
        head.pack(fill="x")
        if acc:
            self._avatar_lbl = tk.Label(head, image=app._avatar(acc, px(40) // 8 * 8), bg=C["bg"])
            self._avatar_lbl.pack(side="left")
        tx = tk.Frame(head, bg=C["bg"])
        tx.pack(side="left", padx=px(12))
        app._label(tx, acc["name"] if acc else "Нет аккаунта", size=15, bold=True).pack(anchor="w")
        is_ms = bool(acc and acc.get("type") == "microsoft")
        app._label(tx, "лицензия Minecraft" if is_ms else "войдите, чтобы менять скин", muted=True,
                   size=9).pack(anchor="w")

        if not is_ms:
            card = tk.Frame(r, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
            card.pack(fill="x", pady=(px(20), 0))
            tk.Label(card, text="Скины и плащи доступны для лицензии", bg=C["panel"], fg=C["fg"],
                     font=self.F.t(13), anchor="w").pack(fill="x", padx=px(18), pady=(px(16), px(4)))
            tk.Label(card, text="Mojang хранит скин в профиле лицензионного аккаунта — так его видят все игроки. "
                                "Войдите через Microsoft, чтобы менять скин и плащ. Пока слева — "
                                "сгенерированный персонаж только для красоты.", bg=C["panel"], fg=C["muted"],
                     font=self.F.b(9),
                     wraplength=px(480), justify="left", anchor="w").pack(fill="x", padx=px(18))
            PillButton(card, self.A, "Войти через Microsoft", command=app._add_microsoft, style="accent",
                       font=self.F.b(10, "bold"), bg=C["panel"]).pack(anchor="w", padx=px(18), pady=px(16))
            row = tk.Frame(r, bg=C["bg"])
            row.pack(fill="x", pady=(px(16), 0))
            app._btn(row, "Создать скин", lambda: self.editor.open(None)).pack(side="left")
            app._label(row, "  можно нарисовать скин заранее — он сохранится в библиотеке", muted=True,
                       size=9).pack(side="left")
            self._section(r, "МОИ СКИНЫ")
            lib = tk.Frame(r, bg=C["bg"])
            lib.pack(fill="x")
            self._draw_library(lib)
            return

        # ---- скин
        self._section(r, "СКИН")
        row = tk.Frame(r, bg=C["bg"])
        row.pack(fill="x")
        slim = self.preview_slim if self.preview else self.slim
        self.m_classic = app._btn(row, "Классическая модель", lambda: self._set_variant(False),
                                  style="sel" if not slim else "dark", padx=14)
        self.m_classic.pack(side="left")
        self.m_slim = app._btn(row, "Тонкие руки", lambda: self._set_variant(True),
                               style="sel" if slim else "dark", padx=14)
        self.m_slim.pack(side="left", padx=px(8))
        row2 = tk.Frame(r, bg=C["bg"])
        row2.pack(fill="x", pady=(px(10), 0))
        if self.preview:
            app._btn(row2, "Применить этот скин", self._apply_preview, style="accent").pack(side="left")
            app._btn(row2, "Редактировать", lambda: self.editor.open(
                self.preview[2], os.path.splitext(os.path.basename(self.preview[0]))[0])).pack(side="left", padx=px(8))
            app._btn(row2, "Отмена", self._cancel_preview).pack(side="left")
            app._label(row2, "предпросмотр: " + os.path.basename(self.preview[0]), muted=True, size=9).pack(
                side="left", padx=px(6))
        else:
            app._btn(row2, "Загрузить PNG…", self._choose_file, style="accent").pack(side="left")
            app._btn(row2, "Стандартный скин", self._reset).pack(side="left", padx=px(8))
            row3 = tk.Frame(r, bg=C["bg"])
            row3.pack(fill="x", pady=(px(8), 0))
            app._btn(row3, "Создать скин", lambda: self.editor.open(None), style="sel").pack(side="left")
            app._btn(row3, "Редактировать текущий", lambda: self.editor.open(self.skin, "Мой скин")).pack(
                side="left", padx=px(8))

        # ---- библиотека
        self._section(r, "МОИ СКИНЫ")
        lib = tk.Frame(r, bg=C["bg"])
        lib.pack(fill="x")
        self._draw_library(lib)

        # ---- плащи
        self._section(r, "ПЛАЩ")
        cf = tk.Frame(r, bg=C["bg"])
        cf.pack(fill="x")
        if self.profile is None:
            app._label(cf, "Загрузка...", muted=True, size=9).pack(anchor="w")
        elif not self.capes:
            app._label(cf, "На аккаунте пока нет плащей (их выдают за события и покупки, например Minecon "
                           "или Migrator).", muted=True, size=9, wraplength=px(480), justify="left").pack(anchor="w")
        else:
            any_active = any(c[3] for c in self.capes)
            self._cape_tile(cf, None, "Без плаща", None, not any_active)
            for cid, alias, img, act in self.capes:
                self._cape_tile(cf, cid, alias, img, act)

    def _draw_library(self, parent):
        px = self.px
        files = []
        if os.path.isdir(SKINS_DIR):
            files = sorted((os.path.join(SKINS_DIR, f) for f in os.listdir(SKINS_DIR) if f.lower().endswith(".png")),
                           key=os.path.getmtime, reverse=True)[:9]
        add = tk.Canvas(parent, width=px(52), height=px(52), bg=C["panel2"], highlightthickness=1,
                        highlightbackground=C["line"], cursor="hand2")
        add.create_text(px(26), px(26), text="+", fill=C["muted"], font=self.F.t(18))
        add.pack(side="left", padx=(0, px(8)))
        add.bind("<Button-1>", lambda e: self._choose_file())
        self._tiles = []
        for path in files:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                img = skins.validate_skin(data)
            except Exception:  # noqa: BLE001
                continue
            photo = face_photo(skins.head_pixels(img), px(40) // 8 * 8)
            self._tiles.append(photo)
            sel = self.preview and self.preview[0] == path
            t = tk.Label(parent, image=photo, bg=C["panel2"], highlightthickness=2,
                         highlightbackground=C["accent"] if sel else C["line"], cursor="hand2", padx=px(4), pady=px(4))
            t.pack(side="left", padx=(0, px(8)))
            t.bind("<Button-1>", lambda e, p=path, d=data, i=img: self._preview(p, d, i))
            t.bind("<Button-3>", lambda e, p=path: self._delete_lib(p))
            t.bind("<Double-Button-1>", lambda e, p=path, i=img: self.editor.open(
                i, os.path.splitext(os.path.basename(p))[0]))
        if not files:
            self.app._label(parent, "Здесь будут ваши скины — загруженные и прошлые", muted=True, size=9).pack(
                side="left")

    def _cape_tile(self, parent, cid, alias, img, active):
        px = self.px
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(side="left", padx=(0, px(10)))
        size_w, size_h = px(40), px(64)
        cv = tk.Canvas(f, width=size_w, height=size_h, bg=C["panel2"], highlightthickness=2,
                       highlightbackground=C["accent"] if active else C["line"], cursor="hand2")
        cv.pack()
        if img is not None:
            grid = skins.cape_front_pixels(img)
            k = max(1, min(size_w // 10, size_h // 16))
            ox, oy = (size_w - 10 * k) // 2, (size_h - 16 * k) // 2
            for y, row in enumerate(grid):
                for x, col in enumerate(row):
                    cv.create_rectangle(ox + x * k, oy + y * k, ox + x * k + k, oy + y * k + k, fill=col, width=0)
        else:
            cv.create_line(px(8), px(8), size_w - px(8), size_h - px(8), fill=C["dim"], width=2)
        tk.Label(f, text=(alias or "")[:12], bg=C["bg"], fg=C["fg"] if active else C["muted"],
                 font=self.F.b(8)).pack()
        cv.bind("<Button-1>", lambda e: self._select_cape(cid))

    # ------------------------------------------------------------------ работа с API
    def _token(self, force=False):
        acc = self.app.accounts.find(self.app.cur_acc_key)
        if force:
            acc = dict(acc, mc_expires=0)
        new = auth.refresh_account(acc)
        if new is not acc:
            self.app.accounts.upsert(new)
        self.acc = new
        return new["mc_token"]

    def _api(self, fn, *a):
        try:
            return fn(self._token(), *a)
        except PermissionError:
            return fn(self._token(force=True), *a)

    def _run(self, label, work):
        if self.busy:
            return
        self.busy = True
        self._set_status(label)

        def run():
            try:
                work()
            except Exception as e:  # noqa: BLE001
                self.app.ui(self.app.log, traceback.format_exc(), "ERROR")
                self.app.ui(self._set_status, "Ошибка: %s" % e, C["err"])
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def _load_profile(self):
        def work():
            prof = self._api(skins.get_profile)
            self._apply_profile(prof)
            self.app.ui(self._set_status, "")
        self._run("Загружаю скин и плащи...", work)

    def _apply_profile(self, prof):
        """Вызывается из фонового потока: качает текстуры и обновляет интерфейс."""
        if not prof or "skins" not in prof:
            prof = self._api(skins.get_profile)
        sk = skins.active(prof.get("skins"))
        skin_img, slim = None, False
        if sk and sk.get("url"):
            data = skins.fetch_texture(sk["url"], TEX_CACHE)
            skin_img = skins.decode_png(data)
            slim = (sk.get("variant", "CLASSIC").upper() == "SLIM")
            self._save_to_library(data, "%s_%s" % (prof.get("name", "skin"), hashlib.sha1(data).hexdigest()[:8]))
        capes = []
        cape_img = None
        for c in prof.get("capes") or []:
            img = None
            try:
                img = skins.decode_png(skins.fetch_texture(c["url"], TEX_CACHE)) if c.get("url") else None
            except Exception:  # noqa: BLE001
                img = None
            act = c.get("state") == "ACTIVE"
            capes.append((c["id"], c.get("alias", "Плащ"), img, act))
            if act:
                cape_img = img
        self.app.ui(self._profile_ready, prof, skin_img, slim, capes, cape_img)

    def _profile_ready(self, prof, skin_img, slim, capes, cape_img):
        self.profile = prof
        if skin_img is not None:
            self.skin = skin_img
            self.slim = slim
            self.app.set_skin_head(self.acc["id"], skins.head_pixels(skin_img))
        self.capes = capes
        self.cape = cape_img
        self.preview = None
        self._rebuild_model()
        self._draw_right()

    def _save_to_library(self, data, name):
        os.makedirs(SKINS_DIR, exist_ok=True)
        h = hashlib.sha1(data).hexdigest()
        for f in os.listdir(SKINS_DIR):
            p = os.path.join(SKINS_DIR, f)
            try:
                with open(p, "rb") as fh:
                    if hashlib.sha1(fh.read()).hexdigest() == h:
                        return p
            except OSError:
                pass
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ") or "skin"
        p = os.path.join(SKINS_DIR, safe + ".png")
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def _choose_file(self):
        p = filedialog.askopenfilename(title="Выберите скин (PNG 64×64)", filetypes=[("PNG", "*.png")])
        if not p:
            return
        try:
            with open(p, "rb") as f:
                data = f.read()
            img = skins.validate_skin(data)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Скин", str(e), parent=self.app.root)
            return
        lp = self._save_to_library(data, os.path.splitext(os.path.basename(p))[0])
        self._preview(lp, data, img)

    def _preview(self, path, data, img):
        self.preview = (path, data, img)
        self.preview_slim = skins.guess_slim(img)
        self._rebuild_model()
        self._draw_right()
        if self.acc and self.acc.get("type") == "microsoft":
            self._set_status("Предпросмотр — нажмите «Применить этот скин», чтобы поставить его на аккаунт")
        else:
            self._set_status("Предпросмотр скина из библиотеки")

    def _cancel_preview(self):
        self.preview = None
        self._rebuild_model()
        self._draw_right()
        self._set_status("")

    def _delete_lib(self, path):
        if messagebox.askyesno("Скин", "Убрать «%s» из библиотеки?" % os.path.basename(path), parent=self.app.root):
            try:
                os.remove(path)
            except OSError:
                pass
            if self.preview and self.preview[0] == path:
                self.preview = None
                self._rebuild_model()
            self._draw_right()

    def _set_variant(self, slim):
        if self.preview:
            self.preview_slim = slim
            self._rebuild_model()
            self._draw_right()
            return
        if slim == self.slim or not self.profile:
            return
        sk = skins.active(self.profile.get("skins"))
        if not sk:
            return
        variant = "slim" if slim else "classic"

        def work():
            prof = self._api(skins.set_skin_url, sk["url"], variant)
            self._apply_profile(prof)
            self.app.ui(self._set_status, "✓  Модель изменена", C["accent"])
        self._run("Меняю модель...", work)

    def _apply_preview(self):
        path, data, _img = self.preview
        variant = "slim" if self.preview_slim else "classic"

        def work():
            prof = self._api(skins.upload_skin, data, variant)
            self._apply_profile(prof)
            self.app.ui(self._set_status, "✓  Скин установлен — в игре он появится после перезахода", C["accent"])
        self._run("Загружаю скин на аккаунт...", work)

    def _reset(self):
        if not messagebox.askyesno("Скин", "Вернуть стандартный скин? Текущий останется в «Моих скинах».",
                                   parent=self.app.root):
            return

        def work():
            prof = self._api(skins.reset_skin)
            self._apply_profile(prof)
            self.app.ui(self._set_status, "✓  Установлен стандартный скин", C["accent"])
        self._run("Сбрасываю скин...", work)

    def _select_cape(self, cid):
        cur = next((c[0] for c in self.capes if c[3]), None)
        if cid == cur:
            return

        def work():
            prof = self._api(skins.show_cape, cid) if cid else self._api(skins.hide_cape)
            self._apply_profile(prof)
            self.app.ui(self._set_status, "✓  Плащ %s" % ("надет" if cid else "снят"), C["accent"])
        self._run("Меняю плащ...", work)
