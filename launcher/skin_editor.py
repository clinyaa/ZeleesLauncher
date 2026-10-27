"""Редактор скинов: рисование на развёртке 64×64 и прямо на 3D-модели."""
import math
import os
import random
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

from . import skins
from .ui_kit import C, PillButton, make_entry

SKINS_DIR = None  # задаётся из skin_page, чтобы не было циклического импорта

PALETTE = [
    "#ffdbac", "#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#5c3a21",
    "#2b1d14", "#4a3020", "#7a5230", "#c99a4a", "#e8d080", "#d0d0d8",
    "#000000", "#3a3d45", "#6b707c", "#a9aeb8", "#e6e8ec", "#ffffff",
    "#c0392b", "#e67e22", "#f1c40f", "#50e080", "#2cb05c", "#1e8449",
    "#3498db", "#2c3e8f", "#8e44ad", "#e84393", "#16a085", "#7f8c8d",
]
TOOLS = [("pencil", "Карандаш", "B"), ("eraser", "Ластик", "E"), ("picker", "Пипетка", "I"),
         ("fill", "Заливка", "G"), ("lighten", "Светлее", ""), ("darken", "Темнее", ""), ("noise", "Шум", "")]
MANNEQUIN = (201, 206, 216, 255)


def hex_to_rgba(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def rgba_to_hex(c):
    return "#%02x%02x%02x" % tuple(c[:3])


class SkinEditor:
    def __init__(self, app, skin_page):
        self.app, self.sp = app, skin_page
        self.A, self.F, self.px = app.A, app.F, app.px
        self.img = self._blank()
        self.slim = False
        self.tool = "pencil"
        self.color = "#50e080"
        self.recent = []
        self.target = 1            # слой, на котором рисуем в 3D
        self.show_overlay = True
        self.undo, self.redo = [], []
        self.yaw, self.pitch = 0.5, 0.2
        self.item_map = {}
        self.cells = []
        self._stroke = set()
        self._last2d = None
        self._render_job = None
        self._rot = None
        self.regions = skins.layout(False)
        self._build()
        root = app.root
        for seq, fn in (("<Control-z>", self.do_undo), ("<Control-Z>", self.do_undo), ("<Control-y>", self.do_redo),
                        ("<Control-Y>", self.do_redo)):
            root.bind_all(seq, lambda e, f=fn: self._hotkey(f))
        for key, tool in (("b", "pencil"), ("e", "eraser"), ("i", "picker"), ("g", "fill")):
            root.bind_all("<KeyPress-%s>" % key, lambda e, t=tool: self._hotkey(lambda: self.set_tool(t)), add="+")

    # ------------------------------------------------------------------ шаблоны
    def _blank(self):
        px = [[(0, 0, 0, 0)] * 64 for _ in range(64)]
        img = skins.Image(64, 64, px)
        for (x, y, w, h, _n, layer) in skins.layout(False):
            if layer == 1:
                for yy in range(y, y + h):
                    for xx in range(x, x + w):
                        px[yy][xx] = MANNEQUIN
        return img

    # ------------------------------------------------------------------ разметка
    def _build(self):
        app, px = self.app, self.px
        fr = tk.Frame(app.content, bg=C["bg"])
        app.pages["skin_editor"] = fr
        body = tk.Frame(fr, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=px(36), pady=(px(26), px(16)))

        top = tk.Frame(body, bg=C["bg"])
        top.pack(fill="x")
        app._btn(top, "← Назад", self.close, padx=14).pack(side="left")
        tk.Label(top, text="Редактор скина", bg=C["bg"], fg=C["fg"], font=self.F.t(20)).pack(side="left",
                                                                                          padx=px(14))
        self.name_var = tk.StringVar(value="Мой скин")
        ef, self.name_entry = make_entry(top, self.A, self.name_var, width=18, font=self.F.b(10))
        ef.pack(side="left")
        self.wear_btn = app._btn(top, "Сохранить и надеть", self.save_and_wear, style="accent")
        self.wear_btn.pack(side="right")
        app._btn(top, "Сохранить", self.save).pack(side="right", padx=px(8))

        opt = tk.Frame(body, bg=C["bg"])
        opt.pack(fill="x", pady=(px(12), px(10)))
        app._label(opt, "Начать с:", muted=True, size=9).pack(side="left")
        for text, fn in (("Манекена", self.new_blank), ("Текущего скина", self.new_current),
                         ("Случайного", self.new_random), ("Файла PNG…", self.new_file)):
            app._btn(opt, text, fn, size="sm", padx=12).pack(side="left", padx=(px(6), 0))
        self.m_slim = app._btn(opt, "Тонкие руки", lambda: self.set_slim(True), size="sm", padx=12)
        self.m_slim.pack(side="right")
        self.m_classic = app._btn(opt, "Классические руки", lambda: self.set_slim(False), style="sel", size="sm",
                                  padx=12)
        self.m_classic.pack(side="right", padx=px(6))

        main = tk.Frame(body, bg=C["bg"])
        main.pack(fill="both", expand=True)

        # --- инструменты
        tools = tk.Frame(main, bg=C["bg"], width=px(150))
        tools.pack(side="left", fill="y")
        self.tool_btns = {}
        for key, name, hot in TOOLS:
            label = name + ("  " + hot if hot else "")
            b = PillButton(tools, self.A, label, command=lambda k=key: self.set_tool(k),
                           style="sel" if key == self.tool else "dark", size="sm", font=self.F.b(9, "bold"),
                           bg=C["bg"], minw=140, padx=12)
            b.pack(anchor="w", pady=(0, px(5)))
            self.tool_btns[key] = b
        cf = tk.Frame(tools, bg=C["bg"])
        cf.pack(anchor="w", pady=(px(8), px(6)))
        self.swatch = tk.Canvas(cf, width=px(40), height=px(40), bg=self.color, highlightthickness=2,
                                highlightbackground=C["line"], cursor="hand2")
        self.swatch.pack(side="left")
        self.swatch.bind("<Button-1>", lambda e: self.choose_color())
        PillButton(cf, self.A, "Цвет…", command=self.choose_color, size="sm", font=self.F.b(9, "bold"),
                   bg=C["bg"], padx=12).pack(side="left", padx=px(8))
        k = px(22)
        self.pal = tk.Canvas(tools, width=k * 6, height=k * 5, bg=C["bg"], highlightthickness=0)
        self.pal.pack(anchor="w")
        for i, col in enumerate(PALETTE):
            x, y = (i % 6) * k, (i // 6) * k
            it = self.pal.create_rectangle(x + 1, y + 1, x + k - 2, y + k - 2, fill=col, outline=C["line"])
            self.pal.tag_bind(it, "<Button-1>", lambda e, c=col: self.set_color(c))
        self.app._label(tools, "Недавние", muted=True, size=8).pack(anchor="w", pady=(px(6), px(2)))
        self.recent_cv = tk.Canvas(tools, width=k * 6, height=k, bg=C["bg"], highlightthickness=0)
        self.recent_cv.pack(anchor="w")

        # --- развёртка 2D
        mid = tk.Frame(main, bg=C["bg"])
        mid.pack(side="left", padx=px(16))
        self.cs = max(4, px(6))
        size = self.cs * 64
        self.cv2 = tk.Canvas(mid, width=size, height=size, bg="#0b0c10", highlightthickness=1,
                             highlightbackground=C["line"], cursor="crosshair")
        self.cv2.pack()
        self.info = tk.Label(mid, text="", bg=C["bg"], fg=C["muted"], font=self.F.b(9), anchor="w")
        self.info.pack(fill="x", pady=(px(6), 0))
        self.cv2.bind("<ButtonPress-1>", self._press2d)
        self.cv2.bind("<B1-Motion>", self._drag2d)
        self.cv2.bind("<ButtonRelease-1>", self._release)
        self.cv2.bind("<Motion>", self._hover2d)
        self.cv2.bind("<Leave>", lambda e: self.info.configure(text=""))
        self._draw_grid()

        # --- 3D
        right = tk.Frame(main, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.w3, self.h3 = px(300), size
        self.cv3 = tk.Canvas(right, width=self.w3, height=self.h3, bg=C["panel"], highlightthickness=1,
                             highlightbackground=C["line"], cursor="crosshair")
        self.cv3.pack(anchor="n")
        self.cv3.create_text(self.w3 // 2, self.h3 - px(14), text="ЛКМ — рисовать · ПКМ — вращать",
                             fill=C["dim"], font=self.F.b(8), tags="hint")
        self.cv3.bind("<ButtonPress-1>", self._press3d)
        self.cv3.bind("<B1-Motion>", self._drag3d)
        self.cv3.bind("<ButtonRelease-1>", self._release)
        for b in ("<ButtonPress-3>", "<ButtonPress-2>"):
            self.cv3.bind(b, lambda e: setattr(self, "_rot", (e.x, e.y)))
        for b in ("<B3-Motion>", "<B2-Motion>"):
            self.cv3.bind(b, self._rotate)
        lay = tk.Frame(right, bg=C["bg"])
        lay.pack(fill="x", pady=(px(6), 0))
        self.l1 = PillButton(lay, self.A, "Слой 1", command=lambda: self.set_target(1), style="sel", size="sm",
                             font=self.F.b(9, "bold"), bg=C["bg"], padx=10)
        self.l1.pack(side="left")
        self.l2 = PillButton(lay, self.A, "Слой 2 · одежда", command=lambda: self.set_target(2), size="sm",
                             font=self.F.b(9, "bold"), bg=C["bg"], padx=10)
        self.l2.pack(side="left", padx=px(6))
        vw = tk.Frame(right, bg=C["bg"])
        vw.pack(fill="x", pady=(px(6), 0))
        for text, yaw in (("Спереди", 0.0), ("Сбоку", math.pi / 2), ("Сзади", math.pi)):
            PillButton(vw, self.A, text, command=lambda y=yaw: self._view(y), size="sm", font=self.F.b(9, "bold"),
                       bg=C["bg"], padx=10).pack(side="left", padx=(0, px(6)))

        bottom = tk.Frame(body, bg=C["bg"])
        bottom.pack(fill="x", pady=(px(8), 0))
        app._btn(bottom, "Отменить  Ctrl+Z", self.do_undo, size="sm", padx=12).pack(side="left")
        app._btn(bottom, "Вернуть  Ctrl+Y", self.do_redo, size="sm", padx=12).pack(side="left", padx=px(6))
        self.status = app._label(bottom, "", muted=True, size=9)
        self.status.pack(side="left", padx=px(12))

    # ------------------------------------------------------------------ открытие/закрытие
    def open(self, img=None, name=None):
        self.img = skins.copy_image(img) if img else self._blank()
        self.slim = skins.guess_slim(self.img) if img else False
        self.undo, self.redo = [], []
        self.name_var.set(name or self._free_name())
        is_ms = bool(self.sp.acc and self.sp.acc.get("type") == "microsoft")
        self.wear_btn.config_pill(enabled=is_ms)
        self._apply_slim_buttons()
        self._model_changed()
        self.app.show_page("skin_editor")
        self._set_status("Выберите цвет и рисуйте на развёртке или прямо на модели")

    def close(self):
        if self.undo and not messagebox.askyesno("Редактор", "Выйти без сохранения? Изменения пропадут.",
                                                 parent=self.app.root):
            return
        self.app.show_page("skin")
        self.sp._draw_right()

    def _free_name(self):
        i = 1
        while os.path.exists(os.path.join(SKINS_DIR, "Мой скин %d.png" % i)):
            i += 1
        return "Мой скин %d" % i

    def _set_status(self, text, color=None):
        self.status.configure(text=text, fg=color or C["muted"])

    def _hotkey(self, fn):
        if self.app.page != "skin_editor":
            return
        w = self.app.root.focus_get()
        if isinstance(w, tk.Entry):
            return
        fn()

    # ------------------------------------------------------------------ шаблоны (кнопки)
    def _replace(self, img, slim=None):
        self._push_undo()
        self.img = skins.copy_image(img)
        if slim is not None:
            self.slim = slim
            self._apply_slim_buttons()
        self._model_changed()

    def new_blank(self):
        self._replace(self._blank(), False)

    def new_current(self):
        src = self.sp.skin if self.sp.skin is not None else self._blank()
        self._replace(src, self.sp.slim)

    def new_random(self):
        self._replace(skins.procedural_skin("z%06d" % random.randrange(10 ** 6)), False)

    def new_file(self):
        p = filedialog.askopenfilename(title="Открыть скин (PNG 64×64)", filetypes=[("PNG", "*.png")])
        if not p:
            return
        try:
            with open(p, "rb") as f:
                img = skins.validate_skin(f.read())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Редактор", str(e), parent=self.app.root)
            return
        self._replace(img, skins.guess_slim(img))
        self.name_var.set(os.path.splitext(os.path.basename(p))[0])

    def set_slim(self, slim):
        if slim == self.slim:
            return
        self.slim = slim
        self._apply_slim_buttons()
        self._model_changed()

    def _apply_slim_buttons(self):
        self.m_slim.config_pill(style="sel" if self.slim else "dark")
        self.m_classic.config_pill(style="dark" if self.slim else "sel")

    def _model_changed(self):
        self.regions = skins.layout(self.slim)
        self._draw_grid()
        self._render3d()

    # ------------------------------------------------------------------ инструменты и цвет
    def set_tool(self, t):
        self.tool = t
        for k, b in self.tool_btns.items():
            b.config_pill(style="sel" if k == t else "dark")

    def set_color(self, c):
        self.color = c
        self.swatch.configure(bg=c)
        if c in self.recent:
            self.recent.remove(c)
        self.recent.insert(0, c)
        self.recent = self.recent[:6]
        k = self.px(22)
        self.recent_cv.delete("all")
        for i, col in enumerate(self.recent):
            it = self.recent_cv.create_rectangle(i * k + 1, 1, i * k + k - 2, k - 2, fill=col, outline=C["line"])
            self.recent_cv.tag_bind(it, "<Button-1>", lambda e, cc=col: self.set_color(cc))
        if self.tool in ("eraser", "picker"):
            self.set_tool("pencil")

    def choose_color(self):
        res = colorchooser.askcolor(color=self.color, parent=self.app.root, title="Цвет")
        if res and res[1]:
            self.set_color(res[1])

    def set_target(self, layer):
        self.target = layer
        self.l1.config_pill(style="sel" if layer == 1 else "dark")
        self.l2.config_pill(style="sel" if layer == 2 else "dark")
        self._render3d()

    # ------------------------------------------------------------------ история
    def _snapshot(self):
        return [list(r) for r in self.img.px], self.slim

    def _push_undo(self):
        self.undo.append(self._snapshot())
        if len(self.undo) > 60:
            self.undo.pop(0)
        self.redo.clear()

    def _restore(self, snap):
        px, slim = snap
        self.img = skins.Image(64, 64, [list(r) for r in px])
        if slim != self.slim:
            self.slim = slim
            self._apply_slim_buttons()
        self._model_changed()

    def do_undo(self):
        if self.undo:
            self.redo.append(self._snapshot())
            self._restore(self.undo.pop())

    def do_redo(self):
        if self.redo:
            self.undo.append(self._snapshot())
            self._restore(self.redo.pop())

    # ------------------------------------------------------------------ 2D развёртка
    def _region_at(self, x, y):
        for r in self.regions:
            if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
                return r
        return None

    def _cell_fill(self, x, y):
        c = self.img.px[y][x]
        if c[3] > 0:
            return rgba_to_hex(c)
        if self._used[y][x]:
            return "#2b2f39" if (x + y) % 2 else "#23262e"
        return "#0e0f13"

    def _draw_grid(self):
        cv, cs = self.cv2, self.cs
        cv.delete("all")
        self._used = [[False] * 64 for _ in range(64)]
        for (x, y, w, h, _n, _l) in self.regions:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self._used[yy][xx] = True
        self.cells = []
        for y in range(64):
            row = []
            for x in range(64):
                row.append(cv.create_rectangle(x * cs, y * cs, x * cs + cs, y * cs + cs, fill=self._cell_fill(x, y),
                                               width=0))
            self.cells.append(row)
        for (x, y, w, h, _n, layer) in self.regions:
            cv.create_rectangle(x * cs, y * cs, (x + w) * cs, (y + h) * cs,
                                outline="#3a4050" if layer == 1 else "#2f6b45", width=1)

    def _update_cell(self, x, y):
        self.cv2.itemconfigure(self.cells[y][x], fill=self._cell_fill(x, y))

    def _xy2d(self, e):
        x, y = int(e.x // self.cs), int(e.y // self.cs)
        return (x, y) if 0 <= x < 64 and 0 <= y < 64 else None

    def _hover2d(self, e):
        p = self._xy2d(e)
        if not p:
            return
        r = self._region_at(*p)
        self.info.configure(text=("%s · слой %d   (%d, %d)" % (r[4], r[5], p[0], p[1])) if r else
                            "не используется игрой   (%d, %d)" % p)

    def _press2d(self, e):
        p = self._xy2d(e)
        if not p:
            return
        if self.tool != "picker":
            self._push_undo()
        self._stroke = set()
        self._last2d = p
        self._apply(*p)

    def _drag2d(self, e):
        p = self._xy2d(e)
        if not p or p == self._last2d:
            return
        if self.tool in ("pencil", "eraser", "lighten", "darken", "noise") and self._last2d:
            for q in self._line(self._last2d, p):
                self._apply(*q)
        self._last2d = p
        self._hover2d(e)

    def _release(self, _e=None):
        self._stroke = set()
        self._last2d = None

    @staticmethod
    def _line(a, b):
        (x0, y0), (x1, y1) = a, b
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        pts = []
        while True:
            pts.append((x0, y0))
            if (x0, y0) == (x1, y1):
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
        return pts[1:]

    # ------------------------------------------------------------------ применение инструмента
    def _apply(self, x, y):
        if (x, y) in self._stroke and self.tool in ("lighten", "darken", "noise"):
            return
        self._stroke.add((x, y))
        px = self.img.px
        cur = px[y][x]
        t = self.tool
        if t == "picker":
            if cur[3] > 0:
                self.set_color(rgba_to_hex(cur))
                self.set_tool("pencil")
                self._set_status("Цвет взят: %s" % rgba_to_hex(cur))
            return
        if t == "fill":
            self._flood(x, y, hex_to_rgba(self.color))
            self._schedule3d()
            return
        if t == "pencil":
            new = hex_to_rgba(self.color)
        elif t == "eraser":
            new = (0, 0, 0, 0)
        elif cur[3] == 0:
            return
        elif t == "lighten":
            new = tuple(min(255, int(c + (255 - c) * 0.12)) for c in cur[:3]) + (cur[3],)
        elif t == "darken":
            new = tuple(int(c * 0.88) for c in cur[:3]) + (cur[3],)
        else:  # noise
            k = random.uniform(0.9, 1.1)
            new = tuple(max(0, min(255, int(c * k))) for c in cur[:3]) + (cur[3],)
        if new != cur:
            px[y][x] = new
            self._update_cell(x, y)
            self._schedule3d()

    def _flood(self, x, y, new):
        r = self._region_at(x, y)
        if not r:
            return
        rx, ry, rw, rh = r[0], r[1], r[2], r[3]
        px = self.img.px
        old = px[y][x]
        if old == new:
            return
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if not (rx <= cx < rx + rw and ry <= cy < ry + rh) or px[cy][cx] != old:
                continue
            px[cy][cx] = new
            self._update_cell(cx, cy)
            stack += [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]

    # ------------------------------------------------------------------ 3D
    def _faces(self):
        return skins.build_model(self.img, self.slim, None, overlay=(self.show_overlay or self.target == 2))

    def _schedule3d(self):
        if self._render_job is None:
            self._render_job = self.app.root.after(30, self._render3d)

    def _render3d(self):
        self._render_job = None
        cv = self.cv3
        cv.delete("m")
        self.item_map = {}
        faces = self._faces()
        scale = self.h3 / 44.0
        polys = skins.project(faces, self.yaw, self.pitch, scale, self.w3 / 2, self.h3 * 0.48,
                              ghost_overlay=(self.target == 2))
        cp = cv.create_polygon
        for _d, pts, col, (tx, ty, f) in polys:
            if col is None:
                it = cp(pts, fill="#8b919c", stipple="gray12", outline="", tags="m")
            else:
                it = cp(pts, fill=col, outline=col, width=1, tags="m")
            self.item_map[it] = (tx, ty, f.overlay)
        cv.tag_raise("hint")

    def _pick3d(self, e):
        for it in reversed(self.cv3.find_overlapping(e.x, e.y, e.x, e.y)):
            m = self.item_map.get(it)
            if m and m[2] == (self.target == 2):
                return m[0], m[1]
        return None

    def _press3d(self, e):
        p = self._pick3d(e)
        if not p:
            return
        if self.tool != "picker":
            self._push_undo()
        self._stroke = set()
        self._apply(*p)

    def _drag3d(self, e):
        p = self._pick3d(e)
        if p and self.tool not in ("fill", "picker"):
            self._apply(*p)

    def _rotate(self, e):
        if not self._rot:
            self._rot = (e.x, e.y)
            return
        dx, dy = e.x - self._rot[0], e.y - self._rot[1]
        self._rot = (e.x, e.y)
        self.yaw = (self.yaw + dx * 0.012) % (2 * math.pi)
        self.pitch = max(-0.6, min(0.8, self.pitch + dy * 0.008))
        self._render3d()

    def _view(self, yaw):
        self.yaw, self.pitch = yaw, 0.2
        self._render3d()

    # ------------------------------------------------------------------ сохранение
    def _write(self):
        name = "".join(ch for ch in self.name_var.get().strip() if ch.isalnum() or ch in " -_") or "Мой скин"
        os.makedirs(SKINS_DIR, exist_ok=True)
        path = os.path.join(SKINS_DIR, name + ".png")
        data = skins.encode_png(self.img)
        with open(path, "wb") as f:
            f.write(data)
        self.undo.clear()
        return path, data

    def save(self):
        path, _ = self._write()
        self._set_status("✓  Сохранено в «Мои скины»: %s" % os.path.basename(path), C["accent"])

    def save_and_wear(self):
        path, data = self._write()
        img = skins.decode_png(data)
        self.app.show_page("skin")
        self.sp._preview(path, data, img)
        self.sp.preview_slim = self.slim
        self.sp._apply_preview()
