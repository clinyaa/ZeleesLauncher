"""Картинки для установщика Inno Setup (BMP). Нужен Pillow: py -m pip install pillow"""
import os, sys
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logo as LG
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "installer")
os.makedirs(OUT, exist_ok=True)
HERO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_night.png")

def big(w, h):
    im = Image.new("RGB", (w, h), (13, 14, 18))
    if os.path.exists(HERO):
        hero = Image.open(HERO).convert("RGB")
        k = max(1, round(h / hero.height))
        hero = hero.resize((hero.width * k, hero.height * k), Image.NEAREST)
        x0 = max(0, (hero.width - w) // 2 + int(hero.width * 0.12))
        im.paste(hero.crop((x0, 0, x0 + w, h)), (0, 0))
    m = LG.mark(int(w * 0.5))
    im.paste(m, ((w - m.width) // 2 + int(w * 0.03), int(h * 0.16)), m)
    return im

def small(s):
    im = Image.new("RGB", (s, s), (255, 255, 255))
    m = LG.mark(s, badge=LG.GREEN_BADGE, cols=LG.WHITE_COLS)
    im.paste(m, (0, 0), m)
    return im

for k, (w, h) in enumerate([(164, 314), (205, 392), (246, 471), (328, 628)]):
    big(w, h).save(os.path.join(OUT, "wizard_%d.bmp" % w))
for s in (55, 69, 83, 110):
    small(s).save(os.path.join(OUT, "wizard_small_%d.bmp" % s))
print("ok", OUT)
