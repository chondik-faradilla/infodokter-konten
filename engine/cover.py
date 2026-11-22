"""Cover Reels 1080x1920. Teks hook di area tengah (aman untuk crop grid 4:5 / 1:1)."""
import sys
from PIL import Image, ImageDraw
sys.path.insert(0, __import__("os").path.dirname(__file__))
from motion import (W, H, NAVY, BLUE, TURQ, RED, WHITE, background_base, blob_layer, font, layout_lines,
                    mascot_img, logo_img, shadow_card)


def make_cover(out, kicker, lines, pose, accent=RED, badge=None):
    base = background_base().copy()
    for r, col, x, y in [(380, (22, 179, 163, 60), 160, 380), (330, (31, 111, 209, 50), 950, 1500)]:
        b = blob_layer(r, col)
        base.alpha_composite(b, (x - b.width // 2, y - b.height // 2))
    # kicker pill
    f = font("body", 46, 900)
    bw = f.getbbox(kicker)[2] + 70
    pill = Image.new("RGBA", (bw, 84), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle([0, 0, bw - 1, 83], radius=42, fill=accent + (255,))
    ImageDraw.Draw(pill).text((35, 14), kicker, font=f, fill=WHITE)
    base.alpha_composite(pill, ((W - bw) // 2, 420))
    # judul hook besar
    placed, th = layout_lines(lines, 104, 800, NAVY, max_w=1000)
    cy = 640 + th // 2
    for im, x, yo in placed:
        base.alpha_composite(im, (int(x), int(cy + yo)))
    # maskot kanan bawah
    m = mascot_img(pose, 620)
    base.alpha_composite(m, (W - m.width + 150, 980))
    if badge:
        card = shadow_card(500, 150, radius=40, accent=TURQ).copy()
        d = ImageDraw.Draw(card)
        d.text((80, 58), badge[0], font=font("head", 70, 800), fill=accent)
        d.text((80, 140), badge[1], font=font("body", 34, 800), fill=NAVY)
        base.alpha_composite(card, (40, 1150))
    lg = logo_img(560)
    base.alpha_composite(lg, (60, 1600))
    base.convert("RGB").save(out, quality=90)


