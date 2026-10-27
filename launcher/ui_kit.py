"""Набор элементов интерфейса: картинки из assets.zip, скруглённые кнопки, переключатели, слайдер, аватары."""
import base64
import io
import hashlib
import os
import zipfile
import tkinter as tk
import tkinter.font as tkfont

ASSETS_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets.zip")


def _assets_bytes():
    """assets.zip читается и с диска, и из пакета обновления (zip), и из собранного exe."""
    try:
        with open(ASSETS_ZIP, "rb") as f:
            return f.read()
    except OSError:
        import pkgutil
        return pkgutil.get_data("launcher", "assets.zip")
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)

C = {
    "bg": "#0d0e12", "side": "#090a0d", "panel": "#14161c", "panel2": "#1b1e26", "line": "#262a33",
    "fg": "#eef0f3", "muted": "#8b919c", "dim": "#5b616c", "accent": "#50e080", "accent_d": "#2fb35c",
    "warn": "#f2b84b", "err": "#ff6b6b", "blue": "#6aa9ff", "sel": "#1d3a2a", "gold": "#f5c542",
}

PILL_H = {"sm": 30, "md": 38, "lg": 60}
PILL_FG = {"accent": "#06210f", "accent_h": "#06210f", "sel": C["accent"], "sel_h": C["accent"],
           "disabled": C["dim"], "danger": "#ffb4b4", "danger_h": "#ffd0d0"}


class Fonts:
    def __init__(self, root):
        fams = set(f.lower() for f in tkfont.families(root))

        def pick(*names):
            for n in names:
                if n.lower() in fams:
                    return n
            return "TkDefaultFont"
        self.title = pick("Bahnschrift", "Segoe UI Semibold", "Inter Display", "Inter", "Segoe UI", "DejaVu Sans")
        self.body = pick("Segoe UI", "Inter", "DejaVu Sans")
        self.mono = pick("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")

    def t(self, size, weight="bold"):
        return (self.title, size, weight)

    def b(self, size, weight="normal"):
        return (self.body, size, weight)


class Assets:
    def __init__(self, scale):
        self.s = scale
        self.tag = ("%g" % min(SCALES, key=lambda x: abs(x - scale))).replace(".", "_")
        self.z = zipfile.ZipFile(io.BytesIO(_assets_bytes()))
        self.cache = {}

    def px(self, n):
        return int(round(n * self.s))

    def raw(self, name):
        if name not in self.cache:
            self.cache[name] = tk.PhotoImage(data=base64.b64encode(self.z.read(name)))
        return self.cache[name]

    def zoomed(self, name, z):
        key = (name, z)
        if key not in self.cache:
            img = self.raw(name)
            self.cache[key] = img.zoom(z) if z > 1 else img
        return self.cache[key]

    def hero(self, kind):
        return self.zoomed("hero_%s.png" % kind, max(1, int(round(4 * self.s))))

    def icon(self, name, variant=""):
        return self.zoomed("ic_%s%s.png" % (name, variant), max(1, int(round(2 * self.s))))

    def cube(self, name, size=16):
        return self.zoomed("cube_%s.png" % name, max(1, int(round(size * self.s / 16.0))))

    def pill(self, size, style):
        return tuple(self.raw("pill_%s_%s_%s@%s.png" % (size, style, p, self.tag)) for p in "lmr")

    def pill_mid(self, size, style, width):
        key = ("mid", size, style, width)
        if key not in self.cache:
            m = self.pill(size, style)[1]
            self.cache[key] = m.zoom(max(1, width), 1)
        return self.cache[key]

    def switch(self, state):
        return self.raw("switch_%s@%s.png" % (state, self.tag))

    def knob(self):
        return self.raw("knob@%s.png" % self.tag)

    def logo_mark(self):
        return self.raw("logo_mark@%s.png" % self.tag)

    def title_mark(self):
        return self.raw("logo_tiny@%s.png" % self.tag)

    def logo_badge(self):
        return self.raw("logo_badge@%s.png" % self.tag)

    def logo_app(self):
        return self.raw("logo_app_64.png")

    def stage(self):
        return self.raw("stage@%s.png" % self.tag)

    def wordmark(self, unit):
        return self.zoomed("wordmark.png", max(1, int(unit)))


# ------------------------------------------------------------------------- кнопка на канвасе
class CPill:
    """Скруглённая кнопка, нарисованная прямо на Canvas (работает поверх картинок)."""
    _n = 0

    def __init__(self, cv, A, x, y, text="", size="md", style="dark", hover=None, command=None, font=None,
                 fg=None, minw=0, image=None, anchor="nw", padx=18, gap=10):
        CPill._n += 1
        self.cv, self.A = cv, A
        self.tag = "cpill%d" % CPill._n
        self.x, self.y, self.anchor = x, y, anchor
        self.text, self.size, self.style = text, size, style
        self.hover_style = hover or (style + "_h" if style in ("dark", "accent", "glass", "danger", "sel", "busy")
                                     else style)
        self.command = command
        self.font = font
        self.fg = fg
        self.minw, self.image, self.padx, self.gap = minw, image, padx, gap
        self.enabled = True
        self.hovered = False
        self.hidden = False
        self.items = []
        self.w = self.h = 0
        self._draw()
        cv.tag_bind(self.tag, "<Enter>", self._enter)
        cv.tag_bind(self.tag, "<Leave>", self._leave)
        cv.tag_bind(self.tag, "<ButtonRelease-1>", self._click)

    def _cur_style(self):
        if not self.enabled:
            return "disabled"
        return self.hover_style if self.hovered else self.style

    def _draw(self):
        cv, A = self.cv, self.A
        for it in self.items:
            cv.delete(it)
        self.items = []
        st = self._cur_style()
        h = A.px(PILL_H[self.size])
        cap = h // 2
        f = tkfont.Font(font=self.font)
        tw = f.measure(self.text) if self.text else 0
        iw = (self.image.width() + (A.px(self.gap) if self.text else 0)) if self.image else 0
        w = max(A.px(self.minw), tw + iw + 2 * A.px(self.padx), h)
        self.w, self.h = w, h
        x, y = self.x, self.y
        if "e" in self.anchor:
            x -= w
        if "s" in self.anchor:
            y -= h
        if self.anchor in ("center", "c"):
            x -= w // 2
            y -= h // 2
        self.bx, self.by = x, y
        l, _m, r = A.pill(self.size, st)
        mid = A.pill_mid(self.size, st, w - 2 * cap)
        t = (self.tag,)
        self.i_l = cv.create_image(x, y, image=l, anchor="nw", tags=t)
        self.i_m = cv.create_image(x + cap, y, image=mid, anchor="nw", tags=t,
                                   state="normal" if w - 2 * cap > 0 else "hidden")
        self.i_r = cv.create_image(x + w - cap, y, image=r, anchor="nw", tags=t)
        self.items += [self.i_l, self.i_m, self.i_r]
        cx = x + w // 2
        content_w = tw + iw
        sx = cx - content_w // 2
        self.i_img = self.i_txt = None
        if self.image:
            self.i_img = cv.create_image(sx, y + h // 2, image=self.image, anchor="w", tags=t)
            self.items.append(self.i_img)
            sx += iw
        if self.text:
            self.i_txt = cv.create_text(sx, y + h // 2, text=self.text, anchor="w", fill=self._fg(st),
                                        font=self.font, tags=t)
            self.items.append(self.i_txt)
        if self.hidden:
            cv.itemconfigure(self.tag, state="hidden")

    def _fg(self, st):
        return self.fg if (self.fg and self.enabled) else PILL_FG.get(st, C["fg"])

    def _restyle(self):
        """Меняет только картинки и цвет — без пересоздания (иначе мигает при наведении)."""
        st = self._cur_style()
        A = self.A
        cap = self.h // 2
        l, _m, r = A.pill(self.size, st)
        self.cv.itemconfigure(self.i_l, image=l)
        self.cv.itemconfigure(self.i_r, image=r)
        if self.w - 2 * cap > 0:
            self.cv.itemconfigure(self.i_m, image=A.pill_mid(self.size, st, self.w - 2 * cap))
        if self.i_txt:
            self.cv.itemconfigure(self.i_txt, fill=self._fg(st))

    def _enter(self, _e):
        if self.enabled:
            self.hovered = True
            self.cv.configure(cursor="hand2")
            self._restyle()

    def _leave(self, _e):
        self.hovered = False
        self.cv.configure(cursor="")
        self._restyle()

    def _click(self, _e):
        if self.enabled and self.command:
            self.command()

    def config(self, text=None, style=None, hover=None, command=None, enabled=None, image=None, fg=None):
        if text is not None:
            self.text = text
        if style is not None:
            self.style = style
            self.hover_style = hover or (style + "_h" if style in ("dark", "accent", "glass", "danger", "sel", "busy")
                                         else style)
        if command is not None:
            self.command = command
        if enabled is not None:
            self.enabled = enabled
        if image is not None:
            self.image = image if image else None
        if fg is not None:
            self.fg = fg
        self._draw()

    def move_to(self, x, y):
        self.x, self.y = x, y
        self._draw()

    def set_visible(self, v):
        self.hidden = not v
        self._draw()


class PillButton(tk.Canvas):
    """Самостоятельная кнопка-виджет (для обычных страниц)."""

    def __init__(self, parent, A, text, command=None, style="dark", size="md", bg=None, minw=0, font=None,
                 image=None, padx=18):
        bg = bg or parent.cget("bg")
        h = A.px(PILL_H[size])
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=h, width=10)
        self.pill = CPill(self, A, 0, 0, text, size=size, style=style, command=command,
                          font=font or ("Segoe UI", 10, "bold"), minw=minw, image=image, padx=padx)
        self.configure(width=self.pill.w)

    def config_pill(self, **kw):
        self.pill.config(**kw)
        self.configure(width=self.pill.w)


class Switch(tk.Canvas):
    def __init__(self, parent, A, variable, command=None, bg=None):
        self.A = A
        img = A.switch("on")
        super().__init__(parent, bg=bg or parent.cget("bg"), highlightthickness=0, bd=0,
                         width=img.width(), height=img.height(), cursor="hand2")
        self.var = variable
        self.command = command
        self.hover = False
        self.item = self.create_image(0, 0, anchor="nw", image=img)
        self.bind("<ButtonRelease-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _set_hover(self, v):
        self.hover = v
        self._draw()

    def _draw(self):
        st = ("on" if self.var.get() else "off") + ("_h" if self.hover else "")
        self.itemconfigure(self.item, image=self.A.switch(st))

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()


class Slider(tk.Canvas):
    def __init__(self, parent, A, variable, from_, to, step=256, width=320, command=None, bg=None):
        self.A = A
        k = A.knob()
        h = k.height() + 4
        super().__init__(parent, bg=bg or parent.cget("bg"), highlightthickness=0, bd=0, width=A.px(width),
                         height=h, cursor="hand2")
        self.var, self.from_, self.to, self.step, self.command = variable, from_, max(to, from_ + step), step, command
        self.kw = k.width()
        self.wpx = A.px(width)
        cy = h // 2
        t = max(2, A.px(4))
        self.track = self.create_rectangle(self.kw // 2, cy - t // 2, self.wpx - self.kw // 2, cy + t - t // 2,
                                           fill=C["line"], width=0)
        self.fill = self.create_rectangle(self.kw // 2, cy - t // 2, self.kw // 2, cy + t - t // 2,
                                          fill=C["accent_d"], width=0)
        self.knob = self.create_image(0, cy, image=k, anchor="center")
        self.cy = cy
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _x_for(self, v):
        frac = (v - self.from_) / float(self.to - self.from_)
        frac = min(1.0, max(0.0, frac))
        return self.kw // 2 + frac * (self.wpx - self.kw)

    def _draw(self):
        try:
            v = int(self.var.get())
        except (tk.TclError, ValueError):
            return
        x = self._x_for(v)
        self.coords(self.knob, x, self.cy)
        x0, y0, _x1, y1 = self.coords(self.track)
        self.coords(self.fill, x0, y0, x, y1)

    def _drag(self, e):
        frac = (e.x - self.kw // 2) / float(max(1, self.wpx - self.kw))
        frac = min(1.0, max(0.0, frac))
        v = self.from_ + frac * (self.to - self.from_)
        v = int(round(v / self.step) * self.step)
        v = max(self.from_, min(self.to, v))
        if v != self.var.get():
            self.var.set(v)
            if self.command:
                self.command(v)


def make_entry(parent, A, var, width=30, font=None, show=None):
    """Плоское поле ввода с подсветкой рамки при фокусе. Возвращает (frame, entry)."""
    fr = tk.Frame(parent, bg=C["panel2"], highlightthickness=1, highlightbackground=C["line"],
                  highlightcolor=C["line"])
    e = tk.Entry(fr, textvariable=var, width=width, relief="flat", bd=0, bg=C["panel2"], fg=C["fg"],
                 insertbackground=C["fg"], font=font, disabledbackground=C["panel"], show=show or "",
                 selectbackground=C["sel"], selectforeground="white", highlightthickness=0)
    e.pack(fill="x", padx=A.px(10), pady=A.px(7))
    e.bind("<FocusIn>", lambda ev: fr.configure(highlightbackground=C["accent_d"]))
    e.bind("<FocusOut>", lambda ev: fr.configure(highlightbackground=C["line"]))
    return fr, e


# ------------------------------------------------------------------------- золотой значок Premium
def png_rgba(w, h, rows):
    """PNG из строк байтов RGBA (без Pillow)."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


GOLD = [(0.0, (255, 236, 150)), (0.45, (247, 200, 70)), (1.0, (214, 150, 30))]


def _gold_at(t):
    for (t0, c0), (t1, c1) in zip(GOLD, GOLD[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            return tuple(int(c0[i] + (c1[i] - c0[i]) * k) for i in range(3))
    return GOLD[-1][1]


def gold_pill_png(w, h):
    """Капсула с золотым градиентом, бликом сверху и тёмной кромкой; края сглажены 4×4 субпикселями."""
    r = h / 2.0
    rows = []
    for y in range(h):
        row = bytearray()
        base = _gold_at(y / max(1.0, h - 1.0))
        for x in range(w):
            cov = edge = 0
            for sy in range(4):
                for sx in range(4):
                    px_, py_ = x + (sx + 0.5) / 4, y + (sy + 0.5) / 4
                    cx = min(max(px_, r), w - r)
                    d = ((px_ - cx) ** 2 + (py_ - r) ** 2) ** 0.5
                    if d <= r:
                        cov += 1
                        if d > r - 1.2:
                            edge += 1
            if not cov:
                row += b"\0\0\0\0"
                continue
            c = base
            if y < h * 0.42:  # мягкий блик в верхней части
                k = 0.35 * (1 - y / (h * 0.42))
                c = tuple(int(v + (255 - v) * k) for v in c)
            if edge:
                k = 0.55 * edge / cov
                c = tuple(int(v * (1 - k) + o * k) for v, o in zip(c, (150, 98, 10)))
            row += bytes((c[0], c[1], c[2], int(255 * cov / 16)))
        rows.append(row)
    return png_rgba(w, h, rows)


class GoldBadge:
    """Значок «★ Premium» на Canvas (рисуется рядом с ником)."""

    def __init__(self, cv, A, font, command=None, text="★  Premium", size="md"):
        self.cv, self.A, self.font, self.command, self.text = cv, A, font, command, text
        self.h = A.px(PILL_H[size])
        f = tkfont.Font(font=font)
        self.w = f.measure(text) + 2 * A.px(16)
        self.img = tk.PhotoImage(data=base64.b64encode(gold_pill_png(self.w, self.h)))
        self.tag = "goldbadge%d" % id(self)
        self.i_img = cv.create_image(0, 0, image=self.img, anchor="nw", tags=(self.tag,), state="hidden")
        self.i_txt = cv.create_text(0, 0, text=text, fill="#3b2500", font=font, anchor="center", tags=(self.tag,),
                                    state="hidden")
        cv.tag_bind(self.tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
        cv.tag_bind(self.tag, "<Leave>", lambda e: cv.configure(cursor=""))
        cv.tag_bind(self.tag, "<ButtonRelease-1>", lambda e: self.command and self.command())

    def place(self, x_right, y):
        x = x_right - self.w
        self.cv.coords(self.i_img, x, y)
        self.cv.coords(self.i_txt, x + self.w // 2, y + self.h // 2)
        self.cv.itemconfigure(self.tag, state="normal")
        self.cv.tag_raise(self.tag)

    def hide(self):
        self.cv.itemconfigure(self.tag, state="hidden")


# ------------------------------------------------------------------------- аватары
SKIN = ["#f1c7a5", "#e0ac86", "#c68a64", "#a26a48", "#7a4c33", "#ffd9bf"]
HAIR = ["#2b1d14", "#4a3020", "#7a5230", "#c99a4a", "#e8d080", "#1a1a1a", "#8a3a2a", "#d0d0d8", "#3a2a5a"]
EYES = ["#3a6ad8", "#2f9a50", "#6b4a2a", "#4a4a5a", "#8a3ad0"]
SHIRT = ["#3aa0e0", "#e05a5a", "#50c080", "#e0b040", "#9060e0", "#e07a30"]


def offline_face(name):
    """Детерминированное пиксельное лицо 8x8 по нику."""
    h = hashlib.md5(name.encode("utf-8")).digest()
    skin, hair, eye = SKIN[h[0] % len(SKIN)], HAIR[h[1] % len(HAIR)], EYES[h[2] % len(EYES)]
    beard = h[3] % 5 == 0
    fringe = h[4] % 3
    dark_skin = _shade(skin, 0.85)
    mouth = _shade(skin, 0.6)
    g = [[skin] * 8 for _ in range(8)]
    for x in range(8):
        g[0][x] = hair
        g[1][x] = hair
    g[2][0] = g[2][7] = hair
    if fringe == 1:
        g[2][1] = g[2][2] = hair
    elif fringe == 2:
        g[2][5] = g[2][6] = hair
    for x in (1, 5):
        g[4][x] = "#ffffff"
        g[4][x + 1] = eye
    g[5][3] = g[5][4] = dark_skin
    g[6][2] = g[6][5] = dark_skin
    g[6][3] = g[6][4] = mouth
    if beard:
        for x in range(1, 7):
            g[7][x] = hair
        g[6][1] = g[6][6] = hair
    return g


def _shade(hexc, f):
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def face_photo(grid, size):
    img = tk.PhotoImage(width=8, height=8)
    img.put(" ".join("{" + " ".join(row) + "}" for row in grid), to=(0, 0))
    z = max(1, size // 8)
    return img.zoom(z) if z > 1 else img


def head_url(uuid, size):
    return "https://mc-heads.net/avatar/%s/%d" % (uuid, size)


class ScrollFrame(tk.Frame):
    """Прокручиваемая область: self.inner — сюда кладутся строки."""

    def __init__(self, parent, bg=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        from tkinter import ttk
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self.win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None):
        self.cv.bind_all("<MouseWheel>", self._wheel)
        self.cv.bind_all("<Button-4>", lambda e: self.cv.yview_scroll(-3, "units"))
        self.cv.bind_all("<Button-5>", lambda e: self.cv.yview_scroll(3, "units"))

    def _unbind_wheel(self, _e=None):
        self.cv.unbind_all("<MouseWheel>")
        self.cv.unbind_all("<Button-4>")
        self.cv.unbind_all("<Button-5>")

    def _wheel(self, e):
        if self.inner.winfo_height() > self.cv.winfo_height():
            self.cv.yview_scroll(int(-e.delta / 40) if abs(e.delta) >= 40 else (-1 if e.delta > 0 else 1), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.cv.yview_moveto(0)
