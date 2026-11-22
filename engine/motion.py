"""INFODOKTER motion engine: Pillow + numpy + ffmpeg, deterministik per frame.

Satu spec (dict) = satu video. Timing di spec dipakai visual DAN audio (cues).
"""
import json
import math
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
MASCOT_DIR = HERE / "assets" / "mascot"
LOGO = HERE / "assets" / "logo-tagline.webp"

NAVY = (18, 48, 94)
BLUE = (31, 111, 209)
TURQ = (22, 179, 163)
LIGHT = (234, 244, 251)
WHITE = (255, 255, 255)
RED = (226, 76, 75)

SAFE_TOP, SAFE_BOTTOM = 230, 1520  # area aman konten (UI platform di atas & bawah)


# ---------- easing ----------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_cubic(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_cubic(t):
    t = clamp(t)
    return t ** 3


def ease_out_back(t, s=1.70158):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1


def ease_in_out(t):
    t = clamp(t)
    return 3 * t * t - 2 * t * t * t


# ---------- font & teks ----------
@lru_cache(maxsize=64)
def font(kind, size, weight):
    path = FONTS / ("Baloo2-var.ttf" if kind == "head" else "Nunito-var.ttf")
    f = ImageFont.truetype(str(path), size)
    f.set_variation_by_axes([weight])
    return f


def parse_words(text):
    """'Nyamuk *Aedes* suka air' -> [(kata, highlight)]"""
    out = []
    for w in text.split(" "):
        hl = w.startswith("*") and w.rstrip(".,!?:").endswith("*")
        out.append((w.replace("*", "").replace("_", " "), hl))
    return out


@lru_cache(maxsize=512)
def word_img(word, size, weight, color, hl, kind="head", hl_color=TURQ):
    f = font(kind, size, weight)
    bbox = f.getbbox(word)
    tw, th = bbox[2] - bbox[0], size * 1.18
    pad_x = int(size * 0.22) if hl else 0
    img = Image.new("RGBA", (int(tw + pad_x * 2 + 4), int(th + 8)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if hl:
        d.rounded_rectangle([0, int(size * 0.12), img.width - 1, img.height - 1], radius=int(size * 0.28), fill=hl_color + (255,))
        d.text((pad_x - bbox[0], int(size * 0.08)), word, font=f, fill=WHITE)
    else:
        d.text((-bbox[0], int(size * 0.08)), word, font=f, fill=color)
    return img


def layout_lines(lines, size, weight, color, kind="head", max_w=960):
    """Kembalikan daftar (img, x, y_offset) relatif ke pusat blok, plus tinggi total."""
    placed = []
    gap = int(size * 0.26)
    line_h = int(size * 1.22)
    rows = []
    for line in lines:
        words = parse_words(line)
        imgs = [word_img(w, size, weight, color, hl, kind) for w, hl in words]
        # bungkus otomatis kalau kepanjangan
        row, row_w = [], 0
        for im in imgs:
            if row and row_w + gap + im.width > max_w:
                rows.append(row)
                row, row_w = [], 0
            row_w += (gap if row else 0) + im.width
            row.append(im)
        rows.append(row)
    total_h = line_h * len(rows)
    for r_i, row in enumerate(rows):
        row_w = sum(im.width for im in row) + gap * (len(row) - 1)
        x = (W - row_w) // 2
        y = r_i * line_h - total_h // 2
        for im in row:
            placed.append((im, x, y))
            x += im.width + gap
    return placed, total_h


# ---------- gambar bantu ----------
def paste_scaled(base, img, cx, cy, scale=1.0, alpha=1.0, rot=0.0):
    if alpha <= 0.01 or scale <= 0.01:
        return
    im = img
    if abs(scale - 1) > 0.01:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if rot:
        im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alpha < 0.999:
        a = im.getchannel("A").point(lambda v: int(v * alpha))
        im = im.copy()
        im.putalpha(a)
    base.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))


@lru_cache(maxsize=32)
def shadow_card(w, h, radius=48, fill=WHITE, accent=None):
    pad = 40
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 14, pad + w, pad + h + 14], radius=radius, fill=(18, 48, 94, 60))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(sh)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([pad, pad, pad + w, pad + h], radius=radius, fill=fill + (255,))
    if accent:
        d.rounded_rectangle([pad, pad, pad + 22, pad + h], radius=11, fill=accent + (255,))
    return img


@lru_cache(maxsize=32)
def icon(name, size):
    """Ikon 'clay' sederhana: bentuk bulat dengan gradasi & highlight."""
    s = size * 4  # supersampling
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def blob(box, col, hi=True):
        d.ellipse(box, fill=col + (255,))
        if hi:
            x0, y0, x1, y1 = box
            w_, h_ = x1 - x0, y1 - y0
            d.ellipse([x0 + w_ * 0.2, y0 + h_ * 0.12, x0 + w_ * 0.5, y0 + h_ * 0.35], fill=(255, 255, 255, 110))

    if name == "drop":
        d.polygon([(s * .5, s * .06), (s * .2, s * .55), (s * .8, s * .55)], fill=BLUE + (255,))
        blob([s * .2, s * .32, s * .8, s * .92], BLUE)
    elif name == "mosquito":
        for a in (-35, -10, 15):  # kaki
            for side in (-1, 1):
                x0, y0 = s * .5, s * .58
                x1 = x0 + side * s * .36 * math.cos(math.radians(a)); y1 = y0 + s * .36 * math.sin(math.radians(a + 50))
                d.line([(x0, y0), (x1, y1)], fill=NAVY + (255,), width=int(s * .025))
        d.ellipse([s * .16, s * .18, s * .46, s * .46], fill=(200, 225, 245, 200))  # sayap
        d.ellipse([s * .54, s * .18, s * .84, s * .46], fill=(200, 225, 245, 200))
        blob([s * .38, s * .36, s * .62, s * .82], NAVY)
        for k in range(3):  # belang putih Aedes
            yy = s * (.48 + k * .09)
            d.line([(s * .4, yy), (s * .6, yy)], fill=WHITE + (255,), width=int(s * .025))
        blob([s * .41, s * .22, s * .59, s * .40], NAVY)
        d.line([(s * .5, s * .25), (s * .5, s * .04)], fill=NAVY + (255,), width=int(s * .02))
    elif name == "fridge":
        d.rounded_rectangle([s * .22, s * .06, s * .78, s * .94], radius=s * .08, fill=(214, 228, 240, 255))
        d.line([(s * .22, s * .38), (s * .78, s * .38)], fill=(170, 190, 210, 255), width=int(s * .02))
        d.rounded_rectangle([s * .66, s * .14, s * .7, s * .3], radius=s * .02, fill=NAVY + (255,))
        d.rounded_rectangle([s * .66, s * .46, s * .7, s * .66], radius=s * .02, fill=NAVY + (255,))
        d.ellipse([s * .1, s * .82, s * .9, s * .98], fill=BLUE + (200,))
    elif name == "dispenser":
        d.ellipse([s * .3, s * .02, s * .7, s * .36], fill=(120, 190, 240, 255))
        d.rounded_rectangle([s * .24, s * .32, s * .76, s * .94], radius=s * .06, fill=(214, 228, 240, 255))
        d.rectangle([s * .44, s * .5, s * .56, s * .58], fill=NAVY + (255,))
        d.rounded_rectangle([s * .32, s * .74, s * .68, s * .82], radius=s * .03, fill=BLUE + (255,))
    elif name == "tub":
        d.rounded_rectangle([s * .1, s * .36, s * .9, s * .9], radius=s * .1, fill=(214, 228, 240, 255))
        d.rounded_rectangle([s * .16, s * .42, s * .84, s * .6], radius=s * .06, fill=(120, 190, 240, 255))
        blob([s * .62, s * .08, s * .82, s * .3], BLUE)
    elif name in ("check", "bucket", "lid", "recycle", "shield", "meter", "food", "walk", "scale", "clinic", "calendar"):
        col = {"check": TURQ, "bucket": BLUE, "lid": BLUE, "recycle": TURQ, "shield": NAVY, "meter": BLUE,
               "food": (240, 150, 60), "walk": TURQ, "scale": BLUE, "clinic": RED, "calendar": NAVY}[name]
        blob([s * .04, s * .04, s * .96, s * .96], col)
        w_ = int(s * .07)
        if name == "check":
            d.line([(s * .28, s * .52), (s * .44, s * .68), (s * .74, s * .36)], fill=WHITE + (255,), width=w_, joint="curve")
        elif name == "bucket":
            d.polygon([(s * .3, s * .38), (s * .7, s * .38), (s * .64, s * .74), (s * .36, s * .74)], fill=WHITE + (255,))
            d.arc([s * .3, s * .2, s * .7, s * .56], 180, 360, fill=WHITE + (255,), width=int(s * .04))
        elif name == "lid":
            d.rounded_rectangle([s * .26, s * .34, s * .74, s * .44], radius=s * .03, fill=WHITE + (255,))
            d.rounded_rectangle([s * .32, s * .48, s * .68, s * .74], radius=s * .05, fill=WHITE + (255,))
            d.rounded_rectangle([s * .44, s * .26, s * .56, s * .34], radius=s * .02, fill=WHITE + (255,))
        elif name == "recycle":
            d.arc([s * .26, s * .26, s * .74, s * .74], 20, 300, fill=WHITE + (255,), width=w_)
            d.polygon([(s * .68, s * .22), (s * .8, s * .42), (s * .58, s * .4)], fill=WHITE + (255,))
        elif name == "shield":
            d.polygon([(s * .5, s * .2), (s * .76, s * .3), (s * .72, s * .6), (s * .5, s * .8), (s * .28, s * .6), (s * .24, s * .3)], fill=WHITE + (255,))
            d.line([(s * .38, s * .5), (s * .47, s * .6), (s * .63, s * .4)], fill=col + (255,), width=int(s * .05))
        elif name == "meter":
            d.rounded_rectangle([s * .3, s * .22, s * .7, s * .78], radius=s * .08, fill=WHITE + (255,))
            d.rounded_rectangle([s * .36, s * .3, s * .64, s * .48], radius=s * .03, fill=col + (255,))
            d.ellipse([s * .45, s * .58, s * .55, s * .68], fill=col + (255,))
        elif name == "food":
            d.ellipse([s * .22, s * .3, s * .78, s * .74], fill=WHITE + (255,))
            d.ellipse([s * .34, s * .4, s * .66, s * .64], fill=(255, 215, 170, 255))
        elif name == "walk":
            d.ellipse([s * .46, s * .18, s * .6, s * .32], fill=WHITE + (255,))
            for seg in [((.52, .34), (.46, .58)), ((.46, .58), (.36, .78)), ((.46, .58), (.6, .78)), ((.5, .4), (.34, .5)), ((.5, .4), (.66, .48))]:
                d.line([(s * seg[0][0], s * seg[0][1]), (s * seg[1][0], s * seg[1][1])], fill=WHITE + (255,), width=w_)
        elif name == "scale":
            d.rounded_rectangle([s * .24, s * .3, s * .76, s * .74], radius=s * .08, fill=WHITE + (255,))
            d.arc([s * .36, s * .38, s * .64, s * .62], 200, 340, fill=col + (255,), width=int(s * .04))
        elif name == "clinic":
            d.rectangle([s * .42, s * .24, s * .58, s * .76], fill=WHITE + (255,))
            d.rectangle([s * .24, s * .42, s * .76, s * .58], fill=WHITE + (255,))
        elif name == "calendar":
            d.rounded_rectangle([s * .24, s * .28, s * .76, s * .76], radius=s * .05, fill=WHITE + (255,))
            d.rectangle([s * .24, s * .28, s * .76, s * .4], fill=TURQ + (255,))
    return im.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=16)
def mascot_img(pose, size):
    im = Image.open(MASCOT_DIR / f"{pose}.webp").convert("RGBA")
    im = im.resize((size, size), Image.LANCZOS)
    # glow putih lembut di belakang maskot
    pad = 150
    out = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    glow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([pad + size * .04, pad + size * .08, pad + size * .96, pad + size * 1.0], fill=(255, 255, 255, 190))
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(45)))
    out.alpha_composite(im, (pad, pad))
    return out


@lru_cache(maxsize=4)
def logo_img(width):
    im = Image.open(LOGO).convert("RGBA")
    h = int(im.height * width / im.width)
    return im.resize((width, h), Image.LANCZOS)


# ---------- latar ----------
@lru_cache(maxsize=1)
def background_base():
    y = np.linspace(0, 1, H)[:, None]
    top, bot = np.array(LIGHT), np.array((250, 253, 255))
    grad = (top * (1 - y) + bot * y)[:, :, None].repeat(1, axis=2)
    arr = np.broadcast_to(grad.reshape(H, 1, 3), (H, W, 3)).astype(np.uint8)
    im = Image.fromarray(arr, "RGB").convert("RGBA")
    d = ImageDraw.Draw(im)
    for yy in range(80, H, 64):  # pola titik halus
        for xx in range(40 + (yy // 64 % 2) * 32, W, 64):
            d.ellipse([xx - 3, yy - 3, xx + 3, yy + 3], fill=(31, 111, 209, 22))
    return im


@lru_cache(maxsize=4)
def blob_layer(r, col):
    pad = 160
    im = Image.new("RGBA", (r * 2 + pad * 2, r * 2 + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([pad, pad, pad + r * 2, pad + r * 2], fill=col)
    return im.filter(ImageFilter.GaussianBlur(50))


def draw_background(frame_t, accent):
    base = background_base().copy()
    blobs = [(380, (22, 179, 163, 55), 0.15, 200, 420), (320, (31, 111, 209, 45), 0.11, 880, 1300), (240, accent + (30,), 0.2, 140, 1700)]
    for r, col, sp, x, y in blobs:
        bx = x + 60 * math.sin(frame_t * sp * 2 * math.pi / 6)
        by = y + 50 * math.cos(frame_t * sp * 2 * math.pi / 7)
        b = blob_layer(r, col)
        base.alpha_composite(b, (int(bx - b.width / 2), int(by - b.height / 2)))
    return base


# ---------- elemen ----------
def env(t, start, end, t_in=0.45, t_out=0.3):
    """Kembalikan (masuk 0..1, keluar 0..1) atau None kalau tidak aktif."""
    if t < start or t > end:
        return None
    return clamp((t - start) / t_in), clamp((t - (end - t_out)) / t_out)


def draw_text(base, el, t):
    e = env(t, el["start"], el["end"])
    if not e:
        return
    p_in, p_out = e
    placed, _ = layout_lines(el["lines"], el.get("size", 92), el.get("weight", 800), tuple(el.get("color", NAVY)), el.get("kind", "head"))
    cy = el["y"]
    for i, (im, x, yo) in enumerate(placed):
        local = clamp((t - el["start"] - i * el.get("stagger", 0.06)) / 0.4)
        sc = 0.6 + 0.4 * ease_out_back(local)
        a = ease_out_cubic(local) * (1 - ease_in_cubic(p_out))
        dy = (1 - ease_out_cubic(local)) * 40 - ease_in_cubic(p_out) * 40
        paste_scaled(base, im, x + im.width / 2, cy + yo + im.height / 2 + dy, sc, a)


def fmt_num(n):
    return f"{int(n):,}".replace(",", ".")


def draw_number(base, el, t):
    e = env(t, el["start"], el["end"])
    if not e:
        return
    p_in, p_out = e
    dur = el.get("count", 1.6)
    k = ease_out_cubic((t - el["start"]) / dur)
    val = el["value"] * k
    txt = (fmt_num(val) if el.get("dots", True) else str(int(round(val)))) + el.get("suffix", "")
    f = font("head", el.get("size", 260), 800)
    bbox = f.getbbox(txt)
    im = Image.new("RGBA", (bbox[2] - bbox[0] + 40, int(el.get("size", 260) * 1.25)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20 - bbox[0], int(el.get("size", 260) * 0.1)), txt, font=f, fill=tuple(el.get("color", BLUE)))
    sc = 0.7 + 0.3 * ease_out_back(clamp((t - el["start"]) / 0.5))
    if t - el["start"] > dur and t - el["start"] < dur + 0.25:  # denyut saat angka selesai
        sc *= 1 + 0.06 * math.sin((t - el["start"] - dur) / 0.25 * math.pi)
    paste_scaled(base, im, W / 2, el["y"], sc, ease_out_cubic(p_in * 2) * (1 - ease_in_cubic(p_out)))


def draw_card(base, el, t):
    e = env(t, el["start"], el["end"], t_in=0.5)
    if not e:
        return
    p_in, p_out = e
    cw, ch = el.get("w", 880), el.get("h", 170)
    card = shadow_card(cw, ch, accent=tuple(el.get("accent", TURQ))).copy()
    d = ImageDraw.Draw(card)
    if el.get("icon"):
        ic = icon(el["icon"], int(ch * 0.72))
        card.alpha_composite(ic, (40 + 50, 40 + (ch - ic.height) // 2))
        tx = 40 + 50 + ic.width + 34
    else:
        tx = 40 + 60
    f = font("body", el.get("size", 54), 800)
    lines = el["text"].split("\n")
    lh = el.get("size", 54) * 1.2
    ty = 40 + (ch - lh * len(lines)) / 2
    for ln in lines:
        d.text((tx, ty), ln, font=f, fill=NAVY)
        ty += lh
    side = el.get("from", -1)
    k = ease_out_back(p_in, 1.2)
    x = W / 2 + side * (1 - k) * 700
    a = clamp(p_in * 2) * (1 - ease_in_cubic(p_out))
    paste_scaled(base, card, x, el["y"] - ease_in_cubic(p_out) * 30, 1.0, a)


def draw_icon(base, el, t):
    e = env(t, el["start"], el["end"])
    if not e:
        return
    p_in, p_out = e
    ic = icon(el["icon"], el.get("size", 260))
    bob = math.sin((t - el["start"]) * 2.2) * 12
    sc = ease_out_back(p_in) * (1 - 0.3 * ease_in_cubic(p_out))
    paste_scaled(base, ic, el["x"], el["y"] + bob, sc, 1 - ease_in_cubic(p_out), el.get("rot", 0) * math.sin(t * 3))


def draw_mascot(base, el, t):
    e = env(t, el["start"], el["end"], t_in=0.55, t_out=0.4)
    if not e:
        return
    p_in, p_out = e
    size = el.get("size", 820)
    m = mascot_img(el["pose"], size)
    k_in, k_out = ease_out_back(p_in, 1.4), ease_in_cubic(p_out)
    y_rest = el.get("y", 1080)
    y = y_rest + (1 - k_in) * 1000 + k_out * 1100
    bob = math.sin((t - el["start"]) * 2.6) * 10
    paste_scaled(base, m, el.get("x", W / 2), y + bob, 1.0, 1.0)
    if el.get("bubble"):
        bp = clamp((t - el["start"] - 0.4) / 0.4)
        lines = el["bubble"].split("\n")
        f = font("head", 66, 800)
        bw = max(f.getbbox(l)[2] for l in lines) + 100
        bh = int(len(lines) * 80 + 70)
        card = shadow_card(bw, bh, radius=60).copy()
        d = ImageDraw.Draw(card)
        tail_x = card.width // 2
        d.polygon([(tail_x - 34, 40 + bh - 2), (tail_x + 34, 40 + bh - 2), (tail_x, 40 + bh + 46)], fill=WHITE + (255,))
        ty = 40 + 30
        for l in lines:
            lw = f.getbbox(l)[2]
            d.text(((card.width - lw) / 2, ty), l, font=f, fill=NAVY)
            ty += 80
        by = y_rest - size / 2 - bh / 2 + 10
        paste_scaled(base, card, W / 2, by + (1 - ease_out_back(bp)) * 40, 0.8 + 0.2 * ease_out_back(bp), bp * (1 - k_out))


def draw_source(base, el, t):
    e = env(t, el["start"], el["end"], 0.3, 0.3)
    if not e:
        return
    f = font("body", 30, 700)
    txt = el["text"]
    bb = f.getbbox(txt)
    im = Image.new("RGBA", (bb[2] + 48, 58), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, 57], radius=29, fill=(255, 255, 255, 220))
    d.text((24, 11), txt, font=f, fill=(70, 90, 120))
    paste_scaled(base, im, W / 2, el.get("y", 1470), 1, clamp(e[0] * 2) * (1 - e[1]))


def draw_logo(base, el, t):
    e = env(t, el["start"], el["end"], 0.6, 0.01)
    if not e:
        return
    lg = logo_img(el.get("w", 760))
    paste_scaled(base, lg, W / 2, el["y"], 0.85 + 0.15 * ease_out_back(e[0]), ease_out_cubic(e[0]))


DRAW = {"text": draw_text, "number": draw_number, "card": draw_card, "icon": draw_icon,
        "mascot": draw_mascot, "source": draw_source, "logo": draw_logo}


def draw_progress(base, t, total, scenes):
    d = ImageDraw.Draw(base)
    n = len(scenes)
    x0, x1, y = 70, W - 70, 150
    gap = 12
    seg = (x1 - x0 - gap * (n - 1)) / n
    for i, (s0, s1) in enumerate(scenes):
        xa = x0 + i * (seg + gap)
        d.rounded_rectangle([xa, y, xa + seg, y + 10], radius=5, fill=(31, 111, 209, 50))
        fill = clamp((t - s0) / (s1 - s0))
        if fill > 0:
            d.rounded_rectangle([xa, y, xa + seg * fill, y + 10], radius=5, fill=TURQ + (255,))


def render_frame(spec, t):
    base = draw_background(t, tuple(spec.get("accent", TURQ)))
    for el in spec["elements"]:
        DRAW[el["type"]](base, el, t)
    draw_progress(base, t, spec["duration"], spec["scenes"])
    # gerak kamera halus: zoom pelan per scene
    z = 1.0
    for s0, s1 in spec["scenes"]:
        if s0 <= t < s1:
            z = 1.0 + 0.025 * ease_in_out((t - s0) / (s1 - s0))
    if z > 1.001:
        cw, ch = W / z, H / z
        base = base.crop(((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2)).resize((W, H), Image.BILINEAR)
    # kilatan transisi singkat di awal scene
    for s0, _ in spec["scenes"][1:]:
        if 0 <= t - s0 < 0.18:
            a = int(110 * (1 - (t - s0) / 0.18))
            base.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, a)))
    return base.convert("RGB")


# ---------- audio sintetis ----------
SR = 44100


def load_vo(path):
    with wave.open(str(path)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        x_old = np.arange(len(data)) / sr
        x_new = np.arange(int(len(data) * SR / sr)) / SR
        data = np.interp(x_new, x_old, data)
    return data


def synth_audio(spec, path):
    n = int(spec["duration"] * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    # musik latar: pad akor lembut 92 bpm
    chords = [[261.6, 329.6, 392.0, 493.9], [220.0, 261.6, 329.6, 392.0], [174.6, 220.0, 261.6, 329.6], [196.0, 246.9, 293.7, 392.0]]
    bar = 60 / 92 * 4
    for i in range(int(spec["duration"] / bar) + 1):
        s, e = int(i * bar * SR), min(n, int((i + 1) * bar * SR))
        if s >= n:
            break
        tt = t[s:e] - t[s]
        envl = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.6)
        for f in chords[i % 4]:
            out[s:e] += 0.035 * envl * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2.003 * tt))
        # pluck arpeggio
        for k in range(8):
            ps = s + int(k * bar / 8 * SR)
            if ps >= n:
                break
            f = chords[i % 4][k % 4] * 2
            L = min(n - ps, int(0.5 * SR))
            tp = np.arange(L) / SR
            out[ps:ps + L] += 0.04 * np.exp(-tp * 9) * np.sin(2 * np.pi * f * tp)
    rng = np.random.default_rng(7)

    def add(at, sig):
        s = int(at * SR)
        if s >= n:
            return
        L = min(len(sig), n - s)
        out[s:s + L] += sig[:L]

    for cue in spec["cues"]:
        kind, at = cue["sfx"], cue["t"]
        if kind == "whoosh":
            L = int(0.5 * SR); tp = np.arange(L) / SR
            noise = rng.normal(0, 1, L)
            noise = np.convolve(noise, np.ones(30) / 30, mode="same")
            add(at - 0.15, 0.5 * noise * np.sin(np.pi * tp / 0.5) ** 2)
        elif kind == "pop":
            L = int(0.18 * SR); tp = np.arange(L) / SR
            add(at, 0.35 * np.exp(-tp * 25) * np.sin(2 * np.pi * (900 - 2500 * tp) * tp))
        elif kind == "hit":
            L = int(0.6 * SR); tp = np.arange(L) / SR
            add(at, 0.45 * np.exp(-tp * 6) * np.sin(2 * np.pi * 70 * tp) + 0.1 * np.exp(-tp * 30) * rng.normal(0, 1, L))
        elif kind == "ticks":
            for k in range(int(cue.get("dur", 1.5) / 0.07)):
                L = int(0.02 * SR); tp = np.arange(L) / SR
                add(at + k * 0.07, 0.12 * np.exp(-tp * 300) * np.sin(2 * np.pi * 2400 * tp))
        elif kind == "ding":
            L = int(1.2 * SR); tp = np.arange(L) / SR
            add(at, 0.25 * np.exp(-tp * 3) * (np.sin(2 * np.pi * 1318 * tp) + 0.5 * np.sin(2 * np.pi * 1975 * tp)))
    if spec.get("vo_path"):
        vo = load_vo(spec["vo_path"])
        vo = vo / (np.max(np.abs(vo)) + 1e-9) * 0.9
        music = out / (np.max(np.abs(out)) + 1e-9) * 0.16  # musik & sfx di bawah suara
        out = music
        s0 = int(spec.get("vo_offset", 0.3) * SR)
        L = min(len(vo), n - s0)
        out[s0:s0 + L] += vo[:L]
    # fade akhir, normalisasi puncak
    fade = int(1.5 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.89
    pcm = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def render(spec, out_dir, stills=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cues.json").write_text(json.dumps({"duration": spec["duration"], "scenes": spec["scenes"], "cues": spec["cues"]}, indent=1))
    if stills is not None:
        for st in stills:
            render_frame(spec, st).save(out_dir / f"still_{st:05.2f}.jpg", quality=85)
        return
    wav = out_dir / "audio.wav"
    synth_audio(spec, wav)
    nframes = int(spec["duration"] * FPS)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", "-shortest", str(out_dir / "video.mp4")],
        stdin=subprocess.PIPE)
    for i in range(nframes):
        proc.stdin.write(render_frame(spec, i / FPS).tobytes())
        if i % 150 == 0:
            print(f"frame {i}/{nframes}", flush=True)
    proc.stdin.close()
    proc.wait()


if __name__ == "__main__":
    import importlib.util
    spec_path, out = sys.argv[1], sys.argv[2]
    sp = importlib.util.spec_from_file_location("spec", spec_path)
    mod = importlib.util.module_from_spec(sp); sp.loader.exec_module(mod)
    stills = [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
    render(mod.SPEC, out, stills)
