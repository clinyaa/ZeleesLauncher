"""Логотип ZeleesLauncher: кнопка ▶, собранная из объёмных блоков."""
import os, sys
from PIL import Image, ImageDraw, ImageFont
S=4
def lerp(a,b,t): return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(len(a)))
ROWS=7
BLOCKS=[(c,r) for c in range(4) for r in range(c, ROWS-c)]
GREEN=(80,224,128)
def mark(size, base=GREEN, ext=0.30, gap=0.07, badge=None, cols=None, pad_ratio=None):
    W=size*S; im=Image.new("RGBA",(W,W),(0,0,0,0)); d=ImageDraw.Draw(im)
    if badge:
        r=int(W*0.23)
        g=Image.new("RGBA",(W,W)); gd=ImageDraw.Draw(g)
        for y in range(W): gd.line([(0,y),(W,y)],fill=lerp(badge[0],badge[1],y/W)+(255,))
        m=Image.new("L",(W,W),0); ImageDraw.Draw(m).rounded_rectangle([0,0,W-1,W-1],r,fill=255)
        im.paste(g,(0,0),m)
    pad=W*(pad_ratio if pad_ratio is not None else (0.19 if badge else 0.04))
    cell=(W-2*pad)/(ROWS+ext)
    e=cell*ext
    bw=cell*4+e
    ox=(W-bw)/2 + cell*0.18   # оптический центр треугольника правее геометрического
    oy=pad
    top=lerp(base,(255,255,255),0.38); side=lerp(base,(0,0,0),0.40); face=base
    if cols: top,face,side=cols
    for (c,r) in sorted(BLOCKS,key=lambda b:(-b[1],b[0])):
        x=ox+c*cell; y=oy+r*cell+e; g=cell*gap
        x0,y0,x1,y1=x+g/2,y+g/2,x+cell-g/2,y+cell-g/2
        d.polygon([(x0,y0),(x0+e,y0-e),(x1+e,y0-e),(x1,y0)],fill=top)
        d.polygon([(x1,y0),(x1+e,y0-e),(x1+e,y1-e),(x1,y1)],fill=side)
        d.rectangle([x0,y0,x1,y1],fill=face)
    return im.resize((size,size),Image.LANCZOS)

DARK_BADGE=((44,48,60),(16,18,23))
GREEN_BADGE=((98,236,146),(34,158,82))
WHITE_COLS=((255,255,255),(236,252,242),(178,226,196))

# ---------- пиксельный шрифт для надписи (5x7)
GLYPHS={
"M":["#...#","##.##","#.#.#","#.#.#","#...#","#...#","#...#"],
"Y":["#...#","#...#",".#.#.","..#..","..#..","..#..","..#.."],
"L":["#....","#....","#....","#....","#....","#....","#####"],
"A":[".###.","#...#","#...#","#####","#...#","#...#","#...#"],
"U":["#...#","#...#","#...#","#...#","#...#","#...#",".###."],
"N":["#...#","##..#","#.#.#","#..##","#...#","#...#","#...#"],
"C":[".####","#....","#....","#....","#....","#....",".####"],
"H":["#...#","#...#","#...#","#####","#...#","#...#","#...#"],
"E":["#####","#....","#....","####.","#....","#....","#####"],
"R":["####.","#...#","#...#","####.","#.#..","#..#.","#...#"],
"Z":["#####","....#","...#.","..#..",".#...","#....","#####"],
"S":[".####","#....","#....",".###.","....#","....#","####."],
}
def wordmark(px=6, c1=GREEN, c2=(240,242,245), shadow=True):
    text="ZELEESLAUNCHER"; gw=5; sp=1
    w=len(text)*(gw+sp)-sp; h=7
    im=Image.new("RGBA",((w+1)*px,(h+1)*px),(0,0,0,0)); d=ImageDraw.Draw(im)
    x=0
    for i,ch in enumerate(text):
        col=c1 if i<6 else c2
        for yy,row in enumerate(GLYPHS[ch]):
            for xx,v in enumerate(row):
                if v=="#":
                    X=(x+xx)*px; Y=yy*px
                    if shadow: d.rectangle([X+px*0.0+px//3,Y+px//3,X+px-1+px//3,Y+px-1+px//3],fill=lerp(col,(0,0,0),0.65)+(255,))
                    d.rectangle([X,Y,X+px-1,Y+px-1],fill=col+(255,))
        x+=gw+sp
    return im

def lockup(height=120, bg=None, light=False):
    m=mark(height)
    wm=wordmark(px=max(2,height//14), c2=(20,22,28) if light else (240,242,245))
    W=m.width+int(height*0.28)+wm.width
    im=Image.new("RGBA",(W,height),bg or (0,0,0,0))
    im.alpha_composite(m,(0,0))
    im.alpha_composite(wm,(m.width+int(height*0.28),(height-wm.height)//2+int(height*0.02)))
    return im

if __name__=="__main__":
    out=sys.argv[1]; os.makedirs(out,exist_ok=True)
    # ассеты
    mark(512,badge=DARK_BADGE).save(os.path.join(out,"logo_app_dark.png"))
    mark(512,badge=GREEN_BADGE,cols=WHITE_COLS).save(os.path.join(out,"logo_app_green.png"))
    mark(512).save(os.path.join(out,"logo_mark.png"))
    lockup(160).save(os.path.join(out,"logo_horizontal.png"))
    lockup(160,light=True).save(os.path.join(out,"logo_horizontal_light.png"))
    ico_src=mark(256,badge=GREEN_BADGE,cols=WHITE_COLS)
    ico_src.save(os.path.join(out,"ZeleesLauncher.ico"),sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    # лист-презентация
    sheet=Image.new("RGBA",(1600,900),(13,14,18,255)); d=ImageDraw.Draw(sheet)
    try:
        f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",22); fs=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",17)
    except OSError:
        f=fs=ImageFont.load_default()
    d.text((60,50),"ZeleesLauncher — логотип",font=f,fill=(240,242,245))
    d.text((60,84),"Кнопка «Играть», собранная из блоков: запуск + мир из кубов",font=fs,fill=(139,145,156))
    sheet.alpha_composite(lockup(110),(60,140))
    light=Image.new("RGBA",(820,150),(244,245,247,255)); lk=lockup(80,light=True); light.alpha_composite(lk,(40,(150-lk.height)//2)); sheet.alpha_composite(light,(60,290))
    for i,(name,img) in enumerate([("Значок приложения",mark(200,badge=GREEN_BADGE,cols=WHITE_COLS)),("Тёмный значок",mark(200,badge=DARK_BADGE)),("Знак",mark(200))]):
        sheet.alpha_composite(img,(60+i*270,500)); d.text((60+i*270,715),name,font=fs,fill=(139,145,156))
    x=960
    for sz in (16,24,32,48,64,96):
        sheet.alpha_composite(mark(sz,badge=GREEN_BADGE,cols=WHITE_COLS),(x,420-sz)); x+=sz+22
    d.text((960,440),"Читается от 16 px",font=fs,fill=(139,145,156))
    for i,(c,n) in enumerate([((80,224,128),"#50E080"),((44,176,92),"#2CB05C"),((13,14,18),"#0D0E12"),((240,242,245),"#F0F2F5")]):
        d.rounded_rectangle([960+(i%2)*280,520+(i//2)*150,1200+(i%2)*280,610+(i//2)*150],14,fill=c,outline=(50,54,64))
        d.text((960+(i%2)*280,618+(i//2)*150),n,font=fs,fill=(200,204,212))
    d.text((60,800),"Шрифт надписи — собственный пиксельный 5×7, «ZELEES» выделено акцентным зелёным.",font=fs,fill=(139,145,156))
    d.text((60,830),"Знак: кнопка «Играть», собранная из блоков — запуск игры + мир из кубов.",font=fs,fill=(139,145,156))
    sheet.save(os.path.join(out,"logo_sheet.png"))
    print("ok")
