"""Скины и плащи: API Minecraft Services, чтение PNG без сторонних библиотек и 3D-модель персонажа."""
import hashlib
import json
import math
import os
import struct
import uuid as uuidlib
import zlib

from . import net

API = "https://api.minecraftservices.com/minecraft/profile"


# =====================================================================================
#  API (только для лицензионных аккаунтов)
# =====================================================================================
def _auth(token):
    return {"Authorization": "Bearer " + token, "Accept": "application/json"}


def _call(method, url, token, body=None, ctype="application/json"):
    h = _auth(token)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        h["Content-Type"] = ctype
    try:
        _, resp = net.request(url, data=data, headers=h, method=method, timeout=30)
    except net.HttpError as e:
        if e.status == 401:
            raise PermissionError("Сессия истекла")
        if e.status == 429:
            raise RuntimeError("Слишком много изменений подряд — Mojang просит подождать минуту.")
        msg = e.json().get("errorMessage") or e.json().get("error") or e.text()[:200]
        raise RuntimeError("Ошибка Minecraft Services (HTTP %s): %s" % (e.status, msg))
    return json.loads(resp.decode("utf-8")) if resp else {}


def get_profile(token):
    return _call("GET", API, token)


def upload_skin(token, png_bytes, variant):
    boundary = "----ZeleesLauncher" + uuidlib.uuid4().hex
    parts = []
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"variant\"\r\n\r\n%s\r\n"
                  % (boundary, variant)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"skin.png\"\r\n"
                  "Content-Type: image/png\r\n\r\n" % boundary).encode("utf-8") + png_bytes + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return _call("POST", API + "/skins", token, b"".join(parts), "multipart/form-data; boundary=" + boundary)


def set_skin_url(token, url, variant):
    return _call("POST", API + "/skins", token, {"variant": variant, "url": url})


def reset_skin(token):
    return _call("DELETE", API + "/skins/active", token)


def show_cape(token, cape_id):
    return _call("PUT", API + "/capes/active", token, {"capeId": cape_id})


def hide_cape(token):
    return _call("DELETE", API + "/capes/active", token)


def active(items):
    return next((x for x in items or [] if x.get("state") == "ACTIVE"), None)


def fetch_texture(url, cache_dir):
    """Качает текстуру с textures.minecraft.net (с кэшем по имени файла)."""
    if url.startswith("http://textures.minecraft.net/"):
        url = "https://" + url[len("http://"):]
    name = hashlib.sha1(url.encode()).hexdigest()[:20] + ".png"
    path = os.path.join(cache_dir, name)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    _, data = net.request(url, timeout=20)
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


# =====================================================================================
#  PNG-декодер (8/16 бит, палитра 1–8 бит, без interlace)
# =====================================================================================
class Image:
    def __init__(self, w, h, px):
        self.w, self.h, self.px = w, h, px   # px[y][x] = (r, g, b, a)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return (0, 0, 0, 0)


def decode_png(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Это не PNG-файл")
    pos, idat, plte, trns = 8, [], None, None
    w = h = bd = ct = inter = None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct, _c, _f, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
    if inter:
        raise ValueError("Чересстрочный PNG не поддерживается — пересохраните файл")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(b"".join(idat))
    bpp = max(1, ch * bd // 8)
    stride = (w * ch * bd + 7) // 8
    rows, prev, i = [], bytearray(stride), 0
    for _y in range(h):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        rows.append(line)
        prev = line

    def samples(line):
        if bd == 8:
            return list(line)
        if bd == 16:
            return [line[k] for k in range(0, len(line), 2)]
        res, mask = [], (1 << bd) - 1
        for byte in line:
            for sh in range(8 - bd, -1, -bd):
                res.append((byte >> sh) & mask)
        return res

    px = []
    for line in rows:
        s = samples(line)
        row = []
        for x in range(w):
            if ct == 6:
                row.append(tuple(s[x * 4:x * 4 + 4]))
            elif ct == 2:
                r, g, b = s[x * 3:x * 3 + 3]
                row.append((r, g, b, 255))
            elif ct == 3:
                k = s[x]
                r, g, b = plte[k * 3:k * 3 + 3]
                a = trns[k] if trns and k < len(trns) else 255
                row.append((r, g, b, a))
            elif ct == 0:
                v = s[x] * (255 // ((1 << bd) - 1)) if bd < 8 else s[x]
                row.append((v, v, v, 255))
            else:  # 4: серый + альфа
                v, a = s[x * 2:x * 2 + 2]
                row.append((v, v, v, a))
        px.append(row)
    return Image(w, h, px)


def validate_skin(data):
    img = decode_png(data)
    if (img.w, img.h) not in ((64, 64), (64, 32)):
        raise ValueError("Скин должен быть PNG 64×64 (или старый формат 64×32), а этот %d×%d" % (img.w, img.h))
    return img


def guess_slim(img):
    """Тонкие руки (Alex): столбец 54–55 в области руки прозрачен."""
    if img.h < 64:
        return False
    return img.get(54, 20)[3] == 0 and img.get(55, 20)[3] == 0


# =====================================================================================
#  Процедурный скин для оффлайн-аккаунтов (оригинальный, по нику)
# =====================================================================================
def procedural_skin(name):
    from .ui_kit import offline_face, SHIRT
    h = hashlib.md5(name.encode("utf-8")).digest()

    def hx(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)

    face = offline_face(name)
    skin_c = hx(face[3][3])
    hair_c = hx(face[0][0])
    shirt = hx(SHIRT[h[5] % len(SHIRT)])
    pants = (40 + h[6] % 40, 46 + h[7] % 40, 90 + h[8] % 60, 255)
    shoes = (60, 60, 66, 255)
    px = [[(0, 0, 0, 0)] * 64 for _ in range(64)]

    def fill(x0, y0, w, hgt, col):
        for y in range(y0, y0 + hgt):
            for x in range(x0, x0 + w):
                px[y][x] = col

    def dark(c, f=0.85):
        return (int(c[0] * f), int(c[1] * f), int(c[2] * f), 255)
    # голова (0,0) 8x8x8
    fill(0, 0, 32, 16, skin_c)
    fill(8, 0, 8, 8, hair_c)            # макушка
    fill(0, 8, 8, 3, hair_c)            # бока/затылок — волосы сверху
    fill(16, 8, 8, 3, hair_c)
    fill(24, 8, 8, 8, hair_c)           # затылок
    for y in range(8):
        for x in range(8):
            px[8 + y][8 + x] = hx(face[y][x])
    # тело и руки
    fill(16, 16, 24, 16, shirt)
    fill(20, 16, 8, 4, dark(shirt))
    stripe = (240, 242, 245, 255) if sum(shirt[:3]) < 420 else (30, 32, 40, 255)
    fill(16, 24, 24, 2, stripe)      # полоса на кофте
    fill(40, 23, 16, 1, stripe)
    fill(32, 55, 16, 1, stripe)
    fill(16, 28, 24, 1, dark(pants, 0.7))  # ремень
    fill(40, 16, 16, 16, skin_c)
    fill(40, 20, 16, 5, shirt)
    fill(32, 48, 16, 16, skin_c)
    fill(32, 52, 16, 5, shirt)
    # ноги
    fill(0, 16, 16, 16, pants)
    fill(0, 29, 16, 3, shoes)
    fill(16, 48, 16, 16, pants)
    fill(16, 61, 16, 3, shoes)
    return Image(64, 64, px)


# =====================================================================================
#  3D-модель
# =====================================================================================
def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v_mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


class Face:
    __slots__ = ("o", "u", "v", "n", "tw", "th", "tex", "img", "overlay", "part", "side")

    def __init__(self, o, u, v, n, tw, th, tex, img, overlay, part="", side=""):
        self.o, self.u, self.v, self.n, self.tw, self.th = o, u, v, n, tw, th
        self.tex, self.img, self.overlay, self.part, self.side = tex, img, overlay, part, side


def _box(img, x0, y0, z0, w, h, d, tu, tv, inflate=0.0, overlay=False, mirror=False, cape=False, part=""):
    """Грани коробки с текстурой в стандартной раскладке Minecraft."""
    x0, y0, z0 = x0 - inflate, y0 - inflate, z0 - inflate
    W, H, D = w + 2 * inflate, h + 2 * inflate, d + 2 * inflate
    regions = {
        "top": (tu + d, tv, w, d), "bottom": (tu + d + w, tv, w, d),
        "right": (tu, tv + d, d, h), "front": (tu + d, tv + d, w, h),
        "left": (tu + d + w, tv + d, d, h), "back": (tu + d + w + d, tv + d, w, h),
    }
    if mirror:
        regions["right"], regions["left"] = regions["left"], regions["right"]
    if cape:  # у плаща «лицевая» текстура смотрит назад
        regions["front"], regions["back"] = regions["back"], regions["front"]
    sx, sy, sz = W / w, H / h, D / d
    geo = {
        "front": ((x0, y0 + H, z0 + D), (sx, 0, 0), (0, -sy, 0), (0, 0, 1)),
        "back": ((x0 + W, y0 + H, z0), (-sx, 0, 0), (0, -sy, 0), (0, 0, -1)),
        "right": ((x0, y0 + H, z0), (0, 0, sz), (0, -sy, 0), (-1, 0, 0)),
        "left": ((x0 + W, y0 + H, z0 + D), (0, 0, -sz), (0, -sy, 0), (1, 0, 0)),
        "top": ((x0, y0 + H, z0), (sx, 0, 0), (0, 0, sz), (0, 1, 0)),
        "bottom": ((x0, y0, z0 + D), (sx, 0, 0), (0, 0, -sz), (0, -1, 0)),
    }
    faces = []
    for name, (o, u, v, n) in geo.items():
        rx, ry, rw, rh = regions[name]
        tw, th = (w, h) if name in ("front", "back") else ((d, h) if name in ("left", "right") else (w, d))
        tex = [[(rx + ((tw - 1 - i) if mirror else i), ry + j) for i in range(tw)] for j in range(th)]
        faces.append(Face(o, u, v, n, tw, th, tex, img, overlay, part, name))
    return faces


def _hat_is_opaque(img):
    for y in range(0, 16):
        for x in range(32, 64):
            if img.get(x, y)[3] < 128:
                return False
    return True


def build_model(img, slim=False, cape=None, overlay=True):
    faces = []
    legacy = img.h < 64
    aw = 3 if slim else 4
    faces += _box(img, -4, 0, -2, 4, 12, 4, 0, 16, part="Правая нога")
    faces += (_box(img, 0, 0, -2, 4, 12, 4, 0, 16, mirror=True, part="Левая нога") if legacy
              else _box(img, 0, 0, -2, 4, 12, 4, 16, 48, part="Левая нога"))
    faces += _box(img, -4, 12, -2, 8, 12, 4, 16, 16, part="Тело")
    faces += _box(img, -4 - aw, 12, -2, aw, 12, 4, 40, 16, part="Правая рука")
    faces += (_box(img, 4, 12, -2, aw, 12, 4, 40, 16, mirror=True, part="Левая рука") if legacy
              else _box(img, 4, 12, -2, aw, 12, 4, 32, 48, part="Левая рука"))
    faces += _box(img, -4, 24, -4, 8, 8, 8, 0, 0, part="Голова")
    if overlay and not (legacy and _hat_is_opaque(img)):
        faces += _box(img, -4, 24, -4, 8, 8, 8, 32, 0, inflate=0.5, overlay=True, part="Голова")
    if overlay and not legacy:
        faces += _box(img, -4, 0, -2, 4, 12, 4, 0, 32, 0.25, True, part="Правая нога")
        faces += _box(img, 0, 0, -2, 4, 12, 4, 0, 48, 0.25, True, part="Левая нога")
        faces += _box(img, -4, 12, -2, 8, 12, 4, 16, 32, 0.25, True, part="Тело")
        faces += _box(img, -4 - aw, 12, -2, aw, 12, 4, 40, 32, 0.25, True, part="Правая рука")
        faces += _box(img, 4, 12, -2, aw, 12, 4, 48, 48, 0.25, True, part="Левая рука")
    if cape is not None:
        cf = _box(cape, -5, 8, -3.2, 10, 16, 1, 0, 0, cape=True, part="Плащ")
        ang = math.radians(-10)  # плащ чуть отходит от спины
        piv = (0, 24, -2.2)
        ca, sa = math.cos(ang), math.sin(ang)

        def rot(p, vec=False):
            y, z = (p[1], p[2]) if vec else (p[1] - piv[1], p[2] - piv[2])
            y2, z2 = y * ca + z * sa, -y * sa + z * ca
            return (p[0], y2, z2) if vec else (p[0], y2 + piv[1], z2 + piv[2])
        for f in cf:
            f.o, f.u, f.v, f.n = rot(f.o), rot(f.u, True), rot(f.v, True), rot(f.n, True)
        faces += cf
    return faces


SIDE_NAMES = {"front": "спереди", "back": "сзади", "left": "левый бок", "right": "правый бок",
              "top": "сверху", "bottom": "снизу"}


def layout(slim=False):
    """Все области текстуры скина 64x64: [(x, y, w, h, 'Голова · спереди', layer)]."""
    probe = Image(64, 64, [[(0, 0, 0, 0)] * 64 for _ in range(64)])
    res = []
    for f in build_model(probe, slim):
        xs = [t[0] for row in f.tex for t in row]
        ys = [t[1] for row in f.tex for t in row]
        res.append((min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1,
                    "%s · %s" % (f.part, SIDE_NAMES.get(f.side, f.side)), 2 if f.overlay else 1))
    return res


LIGHT = (-0.35, 0.55, 0.76)


def project(faces, yaw, pitch, scale, cx, cy, ghost_overlay=False):
    """Возвращает список (глубина, [x0,y0,...], '#rrggbb' | None, (tx, ty, face)) от дальних к ближним.
    ghost_overlay=True — пустые пиксели второго слоя тоже возвращаются (цвет None), чтобы на них можно было рисовать."""
    cyw, syw = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    def rot(p, center=True):
        x, y, z = p
        if center:
            y -= 16
        x2 = x * cyw + z * syw
        z2 = -x * syw + z * cyw
        y2 = y * cp - z2 * sp
        z3 = y * sp + z2 * cp
        return x2, y2, z3
    polys = []
    lx, ly, lz = LIGHT
    for f in faces:
        n = rot(f.n, False)
        if n[2] <= 1e-4:
            continue
        shade = 0.62 + 0.38 * max(0.0, n[0] * lx + n[1] * ly + n[2] * lz)
        o, u, v = rot(f.o), rot(f.u, False), rot(f.v, False)
        px = f.img.px
        for j in range(f.th):
            trow = f.tex[j]
            for i in range(f.tw):
                tx, ty = trow[i]
                r, g, b, a = px[ty][tx] if 0 <= ty < f.img.h else (0, 0, 0, 0)
                if f.overlay and a < 128:
                    if not ghost_overlay:
                        continue
                    col = None
                else:
                    col = "#%02x%02x%02x" % (min(255, int(r * shade)), min(255, int(g * shade)),
                                             min(255, int(b * shade)))
                p0 = (o[0] + u[0] * i + v[0] * j, o[1] + u[1] * i + v[1] * j, o[2] + u[2] * i + v[2] * j)
                p1 = (p0[0] + u[0], p0[1] + u[1])
                p2 = (p0[0] + u[0] + v[0], p0[1] + u[1] + v[1], p0[2] + u[2] + v[2])
                p3 = (p0[0] + v[0], p0[1] + v[1])
                depth = (p0[2] + p2[2]) * 0.5
                polys.append((depth, (cx + p0[0] * scale, cy - p0[1] * scale, cx + p1[0] * scale,
                                      cy - p1[1] * scale, cx + p2[0] * scale, cy - p2[1] * scale,
                                      cx + p3[0] * scale, cy - p3[1] * scale), col, (tx, ty, f)))
    polys.sort(key=lambda t: t[0])
    return polys


def encode_png(img):
    """RGBA 8 бит, без фильтров — для сохранения своих скинов."""
    raw = bytearray()
    for y in range(img.h):
        raw.append(0)
        for x in range(img.w):
            raw.extend(img.px[y][x])

    def chunk(t, body):
        c = struct.pack(">I", len(body)) + t + body
        return c + struct.pack(">I", zlib.crc32(t + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def copy_image(img):
    """Копия в формате 64x64 (старые 64x32 переводятся в новый формат)."""
    px = [[(0, 0, 0, 0)] * 64 for _ in range(64)]
    for y in range(min(64, img.h)):
        for x in range(64):
            px[y][x] = tuple(img.get(x, y))
    res = Image(64, 64, px)
    if img.h < 64:  # зеркалим правые руку/ногу в левые, как делает игра
        for (sx, sy, dx, dy) in ((0, 16, 16, 48), (40, 16, 32, 48)):
            for y in range(16):
                for x in range(16):
                    px[dy + y][dx + x] = px[sy + y][sx + x]
    return res


def head_pixels(img):
    """8x8 лицо + слой шапки — для миниатюр в библиотеке."""
    res = []
    for y in range(8):
        row = []
        for x in range(8):
            r, g, b, _a = img.get(8 + x, 8 + y)
            hr, hg, hb, ha = img.get(40 + x, 8 + y)
            if ha >= 128 and not (img.h < 64 and _hat_is_opaque(img)):
                r, g, b = hr, hg, hb
            row.append("#%02x%02x%02x" % (r, g, b))
        res.append(row)
    return res


def cape_front_pixels(cape):
    return [["#%02x%02x%02x" % cape.get(1 + x, 1 + y)[:3] for x in range(10)] for y in range(16)]
