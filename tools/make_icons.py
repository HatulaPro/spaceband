"""Draw the spaceband launcher icon and splash screens into the Android project.

A small constellation: coloured stars of different sizes joined by faint lines,
on the app's night-sky background. Run after `npx cap add android`:

    python tools/make_icons.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "android", "app", "src", "main", "res")
BG = (6, 8, 24)
NIGHT = (14, 18, 48)

# (x, y, radius, colour) in a unit square, kept inside the adaptive-icon safe zone
STARS = [
    (0.50, 0.47, 0.085, (245, 215, 142)),   # the selected star, gold
    (0.30, 0.33, 0.050, (110, 168, 255)),
    (0.70, 0.30, 0.042, (236, 120, 196)),
    (0.27, 0.64, 0.040, (84, 214, 190)),
    (0.69, 0.66, 0.055, (176, 132, 255)),
    (0.50, 0.75, 0.030, (255, 160, 90)),
    (0.42, 0.22, 0.024, (150, 220, 120)),
]
LINKS = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 6), (2, 4)]


def draw(size, background, scale=1.0):
    """Render the constellation at `size` px; `scale` < 1 shrinks it toward the centre."""
    ss = 4  # supersample for smooth edges
    S = size * ss
    img = Image.new("RGBA", (S, S), background + (255,) if background else (0, 0, 0, 0))
    if background:
        # soft radial glow in the middle of the sky
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        g.ellipse([S * 0.1, S * 0.1, S * 0.9, S * 0.9], fill=NIGHT + (255,))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(S * 0.12)))

    def P(x, y):
        return ((0.5 + (x - 0.5) * scale) * S, (0.5 + (y - 0.5) * scale) * S)

    lines = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lines)
    for a, b in LINKS:
        d.line([P(*STARS[a][:2]), P(*STARS[b][:2])], fill=(233, 231, 247, 110), width=max(1, int(S * 0.008 * scale)))
    img = Image.alpha_composite(img, lines)

    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    h = ImageDraw.Draw(halo)
    for x, y, r, c in STARS:
        cx, cy = P(x, y)
        R = r * S * scale * 1.9
        h.ellipse([cx - R, cy - R, cx + R, cy + R], fill=c + (120,))
    img = Image.alpha_composite(img, halo.filter(ImageFilter.GaussianBlur(S * 0.03 * scale)))

    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    k = ImageDraw.Draw(core)
    for x, y, r, c in STARS:
        cx, cy = P(x, y)
        R = r * S * scale
        k.ellipse([cx - R, cy - R, cx + R, cy + R], fill=c + (255,))
        R2 = R * 0.45
        k.ellipse([cx - R2, cy - R2, cx + R2, cy + R2], fill=tuple(min(255, v + 60) for v in c) + (255,))
    img = Image.alpha_composite(img, core)
    return img.resize((size, size), Image.LANCZOS)


def main():
    legacy = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for dpi, px in legacy.items():
        folder = os.path.join(RES, f"mipmap-{dpi}")
        os.makedirs(folder, exist_ok=True)
        sq = draw(px, BG, scale=0.95)
        sq.save(os.path.join(folder, "ic_launcher.png"))
        # round legacy icon: same art clipped to a circle
        mask = Image.new("L", (px * 4, px * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, px * 4 - 1, px * 4 - 1], fill=255)
        rnd = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        rnd.paste(sq, (0, 0), mask.resize((px, px), Image.LANCZOS))
        rnd.save(os.path.join(folder, "ic_launcher_round.png"))
        # adaptive foreground: 108dp canvas, art inside the 66dp safe zone
        fg_px = int(px * 108 / 48)
        draw(fg_px, None, scale=0.62).save(os.path.join(folder, "ic_launcher_foreground.png"))

    with open(os.path.join(RES, "values", "ic_launcher_background.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                '    <color name="ic_launcher_background">#060818</color>\n</resources>\n')

    # splash screens: night background with the constellation in the middle
    for name in os.listdir(RES):
        if not name.startswith("drawable"):
            continue
        path = os.path.join(RES, name, "splash.png")
        if not os.path.exists(path):
            continue
        w, h = Image.open(path).size
        canvas = Image.new("RGBA", (w, h), BG + (255,))
        m = int(min(w, h) * 0.42)
        art = draw(m, None, scale=0.9)
        canvas.alpha_composite(art, ((w - m) // 2, (h - m) // 2))
        canvas.convert("RGB").save(path)
    print("icons and splash screens written to", RES)


if __name__ == "__main__":
    main()
