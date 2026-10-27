"""Генерирует все картинки интерфейса ZeleesLauncher (оригинальная пиксельная графика)."""
import math, os, random, sys
from PIL import Image, ImageDraw, ImageFilter

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
BG = (13, 14, 18)

def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))
def mix(c, tint, t): return lerp(c, tint, t)

# ------------------------------------------------------------------ сцены
W, H = 261, 175

def grad3(y, stops):
    # stops: [(pos, color)]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]; p1, c1 = stops[i + 1]
        if p0 <= y <= p1:
            return lerp(c0, c1, (y - p0) / max(1e-6, p1 - p0))
    return stops[-1][1]

def heightmap(rng, base, amp, rough, block=3):
    n = W // block + 2
    h, v, out = base, 0.0, []
    pts = [rng.uniform(-1, 1) for _ in range(n // 6 + 3)]
    for i in range(n):
        x = i / 6
        k = int(x); f = x - k
        f = f * f * (3 - 2 * f)
        val = pts[k] * (1 - f) + pts[k + 1] * f
        val += rng.uniform(-rough, rough) * 0.15
        hh = base + val * amp
        out.append(int(round(hh / block)) * block)
    return out

def scene(kind, seed=7):
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H))
    px = img.load()
    if kind == "day":
        stops = [(0, (52, 120, 214)), (90, (128, 186, 245)), (175, (200, 228, 252))]
        tint, tint_t = (150, 200, 245), 0.0
    elif kind == "sunset":
        stops = [(0, (34, 26, 72)), (55, (120, 52, 112)), (100, (236, 112, 92)), (130, (252, 178, 108)), (175, (255, 206, 140))]
        tint, tint_t = (90, 40, 90), 0.25
    else:
        stops = [(0, (5, 7, 20)), (90, (14, 20, 48)), (175, (34, 44, 92))]
        tint, tint_t = (18, 24, 60), 0.55
    for y in range(H):
        c = grad3(y, stops)
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(img)
    # звёзды
    if kind != "day":
        for _ in range(170 if kind == "night" else 40):
            x, y = rng.randrange(W), rng.randrange(0, 90 if kind == "night" else 45)
            b = rng.randint(120, 255) if kind == "night" else rng.randint(140, 220)
            px[x, y] = lerp(px[x, y], (b, b, min(255, b + 20)), 0.9)
            if rng.random() < 0.08 and kind == "night":
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if 0 <= x + dx < W and 0 <= y + dy < H:
                        px[x + dx, y + dy] = lerp(px[x + dx, y + dy], (200, 210, 255), 0.4)
    # светило
    if kind == "day":
        cx, cy, r = 205, 30, 9
        for y in range(cy - 34, cy + 34):
            for x in range(cx - 34, cx + 34):
                if 0 <= x < W and 0 <= y < H:
                    a = max(0, 1 - math.hypot(x - cx, y - cy) / 34) ** 2 * 0.45
                    px[x, y] = lerp(px[x, y], (255, 250, 225), a)
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=(255, 244, 196))
        d.rectangle([cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2], fill=(255, 252, 232))
    elif kind == "sunset":
        cx, cy, r = 170, 104, 16
        for y in range(cy - 40, cy + 40):
            for x in range(cx - 60, cx + 60):
                if 0 <= x < W and 0 <= y < H:
                    dist = math.hypot((x - cx) / 1.6, y - cy)
                    a = max(0, 1 - dist / 40) ** 2 * 0.55
                    px[x, y] = lerp(px[x, y], (255, 214, 150), a)
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=(255, 196, 120))
        d.rectangle([cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3], fill=(255, 224, 168))
    else:
        cx, cy, r = 200, 32, 8
        for y in range(cy - 26, cy + 26):
            for x in range(cx - 26, cx + 26):
                if 0 <= x < W and 0 <= y < H:
                    a = max(0, 1 - math.hypot(x - cx, y - cy) / 26) ** 2 * 0.35
                    px[x, y] = lerp(px[x, y], (170, 190, 255), a)
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=(222, 228, 245))
        for (ox, oy, s) in ((-4, -3, 2), (2, 1, 3), (-2, 4, 1)):
            d.rectangle([cx + ox, cy + oy, cx + ox + s, cy + oy + s], fill=(190, 198, 222))
    # облака
    cloud_c = {"day": ((255, 255, 255), 0.85), "sunset": ((255, 170, 150), 0.55), "night": ((60, 70, 110), 0.45)}[kind]
    for _ in range(7):
        cx, cy = rng.randrange(-20, W), rng.randrange(14, 70)
        w = rng.randrange(18, 42)
        for part in range(3):
            pw = w - part * rng.randrange(4, 10)
            ox = rng.randrange(0, max(1, w - pw + 1))
            y0 = cy - part * 3
            for y in range(y0, y0 + 4):
                for x in range(cx + ox, cx + ox + pw):
                    if 0 <= x < W and 0 <= y < H:
                        px[x, y] = lerp(px[x, y], cloud_c[0], cloud_c[1])
    # слои рельефа (дальний -> ближний)
    layers = [
        dict(base=112, amp=26, color=(70, 96, 140), grass=None, fade=0.62),
        dict(base=128, amp=16, color=(46, 92, 60), grass=(76, 140, 72), fade=0.38),
        dict(base=146, amp=10, color=(92, 64, 40), grass=(88, 170, 70), fade=0.0, trees=True),
    ]
    sky_at = lambda y: grad3(y, stops)
    for li, L in enumerate(layers):
        hm = heightmap(rng, L["base"], L["amp"], 1.0)
        for i, top in enumerate(hm):
            x0 = i * 3
            for x in range(x0, min(W, x0 + 3)):
                for y in range(max(0, top), H):
                    depth = y - top
                    if L["grass"] and depth < 3:
                        c = L["grass"]
                    elif L["grass"] and li == 2 and depth >= 15:
                        c = (96, 98, 104) if ((x // 3 + y // 3) % 5) else (80, 82, 88)
                    else:
                        c = L["color"]
                        if L["grass"] and ((x // 3 * 7 + y // 3 * 13) % 9 == 0):
                            c = lerp(c, (0, 0, 0), 0.15)
                    if L["fade"]:
                        c = lerp(c, sky_at(top), L["fade"])
                    if tint_t:
                        c = lerp(c, tint, tint_t * (0.6 if li == 2 else 0.8))
                    px[x, y] = c
        if L.get("trees"):
            for _ in range(9):
                i = rng.randrange(2, len(hm) - 3)
                tx, ty = i * 3, hm[i]
                th = rng.choice((9, 12, 12, 15))
                leaf = lerp((44, 118, 50), tint, tint_t * 0.7) if tint_t else (44, 118, 50)
                leaf2 = lerp(leaf, (0, 0, 0), 0.18)
                trunk = lerp((104, 76, 44), tint, tint_t * 0.6) if tint_t else (104, 76, 44)
                d.rectangle([tx, ty - th, tx + 2, ty - 1], fill=trunk)
                for (lx, ly, lw, lh) in ((-6, -th - 6, 15, 6), (-3, -th - 9, 9, 3)):
                    for yy in range(ty + ly, ty + ly + lh):
                        for xx in range(tx + lx, tx + lx + lw):
                            if 0 <= xx < W and 0 <= yy < H:
                                px[xx, yy] = leaf if ((xx // 3 + yy // 3) % 3) else leaf2
            if kind != "day":
                # домик с горящим окном
                i = len(hm) // 4
                hx, hy = i * 3, hm[i]
                wall = lerp((120, 86, 52), tint, tint_t * 0.6)
                roof = lerp((70, 44, 36), tint, tint_t * 0.6)
                d.rectangle([hx, hy - 12, hx + 17, hy - 1], fill=wall)
                for k in range(4):
                    d.rectangle([hx - 3 + k * 3, hy - 15 - k * 3, hx + 20 - k * 3, hy - 13 - k * 3], fill=roof)
                d.rectangle([hx + 3, hy - 9, hx + 7, hy - 5], fill=(255, 196, 92))
                d.rectangle([hx + 11, hy - 8, hx + 14, hy - 1], fill=lerp(roof, (0, 0, 0), 0.3))
                for y in range(hy - 20, hy + 8):
                    for x in range(hx - 10, hx + 22):
                        if 0 <= x < W and 0 <= y < H:
                            a = max(0, 1 - math.hypot(x - hx - 5, y - hy + 7) / 14) ** 2 * 0.35
                            px[x, y] = lerp(px[x, y], (255, 190, 100), a)
    # затемнение снизу и слева для читаемости
    for y in range(H):
        for x in range(W):
            t_b = max(0.0, (y - H * 0.38) / (H * 0.62))
            a = min(1.0, t_b ** 1.3 * 0.97)
            t_l = max(0.0, 1 - x / (W * 0.55)) * 0.45 * max(0, (y - H * 0.25) / (H * 0.75))
            t_top = max(0.0, 1 - y / (H * 0.18)) * 0.45
            px[x, y] = lerp(px[x, y], BG, min(1.0, a + t_l + t_top))
    return img

for k in ("day", "sunset", "night"):
    scene(k).save(os.path.join(OUT, "hero_%s.png" % k))

# ------------------------------------------------------------------ пиксельные иконки 12x12
ICONS = {
 "skin": [
  "............",
  "..##....##..",
  ".####..####.",
  "############",
  "############",
  "..########..",
  "..########..",
  "..########..",
  "..########..",
  "..########..",
  "..########..",
  "............"],
 "mods": [
  "............",
  ".....##.....",
  "....####....",
  ".##########.",
  ".##########.",
  ".###########",
  ".###########",
  ".##########.",
  ".##########.",
  ".##########.",
  ".##########.",
  "............"],
 "download": [
  ".....##.....",
  ".....##.....",
  ".....##.....",
  ".....##.....",
  "..########..",
  "...######...",
  "....####....",
  ".....##.....",
  "............",
  "#..........#",
  "############",
  "............"],
 "home": [
  "............",
  ".....##.....",
  "....####....",
  "...######...",
  "..########..",
  ".##########.",
  "..########..",
  "..###..###..",
  "..###..###..",
  "..###..###..",
  "..###..###..",
  "............"],
 "play": [
  "............",
  "..##........",
  "..####......",
  "..######....",
  "..########..",
  "..#########.",
  "..#########.",
  "..########..",
  "..######....",
  "..####......",
  "..##........",
  "............"],
 "versions": [
  "............",
  ".##########.",
  ".##########.",
  "............",
  ".##########.",
  ".##########.",
  "............",
  ".##########.",
  ".##########.",
  "............",
  ".######.....",
  ".######....."],
 "java": [
  "...#..#.....",
  "....#..#....",
  "...#..#.....",
  "............",
  ".#########..",
  ".#########.#",
  ".#########.#",
  ".#########.#",
  "..#######.#.",
  "...#####....",
  "............",
  "############"],
 "settings": [
  ".....##.....",
  "..#.####.#..",
  ".##########.",
  "..###..###..",
  ".###....###.",
  "####....####",
  "####....####",
  ".###....###.",
  "..###..###..",
  ".##########.",
  "..#.####.#..",
  ".....##....."],
 "console": [
  "############",
  "#..........#",
  "#.#........#",
  "#..#.......#",
  "#...#......#",
  "#..#.......#",
  "#.#...####.#",
  "#..........#",
  "#..........#",
  "#..........#",
  "############",
  "............"],
 "folder": [
  "............",
  ".####.......",
  "#....#######",
  "#..........#",
  "############",
  "#..........#",
  "#..........#",
  "#..........#",
  "#..........#",
  "############",
  "............",
  "............"],
 "servers": [
  "............",
  ".##########.",
  ".#.#.######.",
  ".##########.",
  "............",
  ".##########.",
  ".#.#.######.",
  ".##########.",
  "............",
  ".....##.....",
  "..########..",
  "............"],
 "user": [
  "....####....",
  "...######...",
  "...######...",
  "...######...",
  "....####....",
  "............",
  "..########..",
  ".##########.",
  ".##########.",
  ".##########.",
  "............",
  "............"],
}
for name, rows in ICONS.items():
    for variant, col in (("", (150, 156, 168, 255)), ("_on", (94, 230, 132, 255)), ("_w", (240, 242, 245, 255))):
        im = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        for y, r in enumerate(rows):
            for x, ch in enumerate(r):
                if ch == "#":
                    im.putpixel((x, y), col)
        im.save(os.path.join(OUT, "ic_%s%s.png" % (name, variant)))

# ------------------------------------------------------------------ кубики (логотип и типы версий) 
def cube(top, left, right, size=16, out=None):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    p = im.load()
    s = size
    cx = s // 2
    for y in range(s):
        for x in range(s):
            # изометрический кубик по пиксельной маске
            yt = y
            if yt < s // 4 + (abs(x - cx + 0.5) // 2):
                pass
    # нарисуем вручную через полигоны на 4x и уменьшим без сглаживания
    big = Image.new("RGBA", (s * 8, s * 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    S = s * 8
    t = [(S / 2, 0.5 * S / 16 * 8 / 8), (S - 0.5, S * 0.25), (S / 2, S * 0.5), (0.5, S * 0.25)]
    l = [(0.5, S * 0.25), (S / 2, S * 0.5), (S / 2, S - 0.5), (0.5, S * 0.75)]
    r = [(S / 2, S * 0.5), (S - 0.5, S * 0.25), (S - 0.5, S * 0.75), (S / 2, S - 0.5)]
    d.polygon(t, fill=top); d.polygon(l, fill=left); d.polygon(r, fill=right)
    im = big.resize((s, s), Image.NEAREST)
    return im

def shade(c, f): return tuple(max(0, min(255, int(v * f))) for v in c[:3]) + (255,)
for name, base in (("release", (78, 214, 118)), ("snapshot", (242, 190, 72)), ("beta", (236, 128, 72)),
                   ("alpha", (150, 158, 172)), ("modded", (168, 120, 255)), ("logo", (80, 224, 128))):
    size = 16
    im = cube(shade(base, 1.08), shade(base, 0.78), shade(base, 0.58), size)
    if name == "logo":
        # «искра» на верхней грани
        im.putpixel((8, 3), (255, 255, 255, 255)); im.putpixel((7, 4), (220, 255, 230, 255)); im.putpixel((9, 4), (220, 255, 230, 255))
    im.save(os.path.join(OUT, "cube_%s.png" % name))

# ------------------------------------------------------------------ скругленные кнопки (3 части: левая, середина 1px, правая)
STYLES = {
    "dark":      dict(fill=(27, 30, 38, 255), border=(42, 46, 56, 255)),
    "dark_h":    dict(fill=(36, 40, 51, 255), border=(56, 61, 74, 255)),
    "accent":    dict(grad=((92, 232, 140), (44, 176, 92)), border=None),
    "accent_h":  dict(grad=((118, 244, 162), (58, 196, 108)), border=None),
    "glass":     dict(fill=(12, 13, 17, 150), border=(255, 255, 255, 30)),
    "glass_h":   dict(fill=(28, 30, 38, 200), border=(255, 255, 255, 60)),
    "danger":    dict(fill=(58, 28, 34, 255), border=(96, 42, 50, 255)),
    "danger_h":  dict(fill=(78, 34, 42, 255), border=(130, 52, 62, 255)),
    "sel":       dict(fill=(28, 58, 40, 255), border=(54, 120, 78, 255)),
    "sel_h":     dict(fill=(34, 70, 48, 255), border=(70, 150, 98, 255)),
    "disabled":  dict(fill=(22, 24, 30, 255), border=(32, 35, 43, 255)),
    "busy":      dict(fill=(40, 42, 52, 235), border=(255, 255, 255, 40)),
    "busy_h":    dict(fill=(58, 34, 40, 240), border=(255, 120, 120, 90)),
}
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
HEIGHTS = {"md": 38, "lg": 60, "sm": 30}

def pill(style, h, radius_ratio=0.5):
    SS = 4
    hh = h * SS
    w = h * 2 + 2  # заготовка, затем режем
    W2 = w * SS
    im = Image.new("RGBA", (W2, hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = int(hh * radius_ratio)
    st = STYLES[style]
    if "grad" in st:
        g = Image.new("RGBA", (W2, hh))
        gd = ImageDraw.Draw(g)
        c0, c1 = st["grad"]
        for y in range(hh):
            gd.line([(0, y), (W2, y)], fill=lerp(c0, c1, y / hh) + (255,))
        mask = Image.new("L", (W2, hh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, W2 - 1, hh - 1], r, fill=255)
        im.paste(g, (0, 0), mask)
        # блик сверху
        hl = Image.new("RGBA", (W2, hh), (0, 0, 0, 0))
        ImageDraw.Draw(hl).rounded_rectangle([SS, SS, W2 - 1 - SS, hh - 1 - SS], r - SS, outline=(255, 255, 255, 60), width=SS)
        hm = Image.new("L", (W2, hh), 0)
        ImageDraw.Draw(hm).rectangle([0, 0, W2, hh // 2], fill=255)
        im.paste(hl, (0, 0), Image.composite(hl.split()[3], Image.new("L", (W2, hh), 0), hm))
    else:
        d.rounded_rectangle([0, 0, W2 - 1, hh - 1], r, fill=st["fill"],
                            outline=st.get("border"), width=SS if st.get("border") else 0)
    im = im.resize((w, h), Image.LANCZOS)
    left = im.crop((0, 0, h // 2, h))
    mid = im.crop((h // 2, 0, h // 2 + 1, h))
    right = im.crop((w - h // 2, 0, w, h))
    return left, mid, right

for sc in SCALES:
    tag = ("%g" % sc).replace(".", "_")
    for hname, hbase in HEIGHTS.items():
        h = int(round(hbase * sc))
        for style in STYLES:
            l, m, r = pill(style, h)
            for part, img in (("l", l), ("m", m), ("r", r)):
                img.save(os.path.join(OUT, "pill_%s_%s_%s@%s.png" % (hname, style, part, tag)))
    # переключатель
    for state in ("on", "off", "on_h", "off_h"):
        SS = 4
        w, h = int(round(42 * sc)), int(round(24 * sc))
        im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        on = state.startswith("on")
        hov = state.endswith("_h")
        track = (52, 196, 104) if on else (44, 48, 58)
        if hov: track = lerp(track, (255, 255, 255), 0.1)
        d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], h * SS // 2, fill=track + (255,))
        k = h * SS - 6 * SS
        x0 = w * SS - 3 * SS - k if on else 3 * SS
        d.ellipse([x0, 3 * SS, x0 + k, 3 * SS + k], fill=(255, 255, 255, 255) if on else (196, 200, 208, 255))
        im.resize((w, h), Image.LANCZOS).save(os.path.join(OUT, "switch_%s@%s.png" % (state, tag)))
    # ручка слайдера
    SS = 4
    k = int(round(18 * sc))
    im = Image.new("RGBA", (k * SS, k * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, k * SS - 1, k * SS - 1], fill=(80, 224, 128, 255))
    d.ellipse([4 * SS, 4 * SS, k * SS - 1 - 4 * SS, k * SS - 1 - 4 * SS], fill=(255, 255, 255, 255))
    im.resize((k, k), Image.LANCZOS).save(os.path.join(OUT, "knob@%s.png" % tag))

# ------------------------------------------------------------------ логотип и сцена для скина
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logo as LG
for sc in SCALES:
    tag = ("%g" % sc).replace(".", "_")
    LG.mark(int(round(34 * sc))).save(os.path.join(OUT, "logo_mark@%s.png" % tag))
    LG.mark(int(round(30 * sc)), badge=LG.GREEN_BADGE, cols=LG.WHITE_COLS).save(os.path.join(OUT, "logo_badge@%s.png" % tag))
    LG.mark(int(round(20 * sc))).save(os.path.join(OUT, "logo_tiny@%s.png" % tag))
    # подиум для 3D-скина
    SS = 2
    w, h = int(round(400 * sc)), int(round(500 * sc))
    im = Image.new("RGBA", (w, h), (20, 22, 28, 255))
    px = im.load()
    for y in range(h):
        for x in range(w):
            dx, dy = (x - w / 2) / (w * 0.55), (y - h * 0.42) / (h * 0.55)
            t = max(0.0, 1 - math.hypot(dx, dy))
            px[x, y] = lerp((16, 17, 22), (36, 52, 44), t ** 1.6) + (255,)
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([w * 0.3, h * 0.86, w * 0.7, h * 0.92], fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(6 * sc))
    im.alpha_composite(sh)
    im.convert("RGB").save(os.path.join(OUT, "stage@%s.png" % tag))
LG.mark(64, badge=LG.GREEN_BADGE, cols=LG.WHITE_COLS).save(os.path.join(OUT, "logo_app_64.png"))
LG.wordmark(px=1, shadow=False).save(os.path.join(OUT, "wordmark.png"))
print("done", len(os.listdir(OUT)))
