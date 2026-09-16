# -*- coding: utf-8 -*-
"""Genera il logo e le icone PWA di G & M in static/icons/ (richiede Pillow)."""
import os
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "static", "icons")
os.makedirs(OUT, exist_ok=True)

S = 1024
NAVY, NAVY2, WHITE = (15, 23, 42), (30, 58, 138), (255, 255, 255)


def font(size):
    for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        for d in (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def gradient(size):
    """Sfondo con gradiente diagonale navy -> blu."""
    img = Image.new("RGB", (size, size), NAVY)
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(NAVY[i] + (NAVY2[i] - NAVY[i]) * t) for i in range(3))
    return img


def draw_mark(d, cx, cy, w, color=WHITE):
    """Manubrio: due dischi per lato + barra, in scala rispetto a w (larghezza totale)."""
    u = w / 24
    bar_h = u * 2.2
    d.rounded_rectangle([cx - 8 * u, cy - bar_h / 2, cx + 8 * u, cy + bar_h / 2], radius=bar_h / 2, fill=color)
    for sx in (-1, 1):
        d.rounded_rectangle([cx + sx * 9.5 * u - 1.6 * u, cy - 6.5 * u, cx + sx * 9.5 * u + 1.6 * u, cy + 6.5 * u], radius=u, fill=color)
        d.rounded_rectangle([cx + sx * 12 * u - 1.3 * u, cy - 4.2 * u, cx + sx * 12 * u + 1.3 * u, cy + 4.2 * u], radius=u * .8, fill=color)


def make(size, maskable=False, text=True):
    img = gradient(S).convert("RGBA")
    d = ImageDraw.Draw(img)
    # zona sicura: le icone "maskable" vengono ritagliate dal sistema (cerchio ecc.)
    inset = S * 0.18 if maskable else S * 0.08
    if text:
        draw_mark(d, S / 2, S * 0.40, S - 2 * inset - S * 0.16)
        f = font(int(S * 0.20))
        tw = d.textlength("G&M", font=f)
        d.text(((S - tw) / 2, S * 0.56), "G&M", font=f, fill=WHITE)
    else:
        draw_mark(d, S / 2, S / 2, S - 2 * inset - S * 0.1)
    if not maskable:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
        img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


make(512).save(os.path.join(OUT, "icon-512.png"))
make(192).save(os.path.join(OUT, "icon-192.png"))
make(512, maskable=True).save(os.path.join(OUT, "icon-512-maskable.png"))
make(180).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
make(64, text=False).save(os.path.join(OUT, "favicon-64.png"))
make(32, text=False).save(os.path.join(OUT, "favicon-32.png"))
make(1024).save(os.path.join(OUT, "logo.png"))
print("icone create in", OUT)
