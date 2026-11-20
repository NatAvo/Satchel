"""Draws Satchel's app icon (satchel/assets/satchel.ico): an original leather satchel.

    python tools/make_icon.py

Needs Pillow. Drawn large and scaled down, so every size in the .ico is smooth.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "satchel" / "assets" / "satchel.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
S = 1024  # drawing size

OUTLINE = (58, 32, 14, 255)
STRAP = (110, 66, 30, 255)
BODY = (139, 86, 40, 255)
BODY_SHADE = (118, 71, 32, 255)
FLAP = (166, 106, 52, 255)
FLAP_LIGHT = (188, 128, 66, 255)
STITCH = (226, 186, 120, 255)
GOLD = (222, 174, 58, 255)
GOLD_DARK = (150, 108, 24, 255)


def draw() -> Image.Image:
    image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    w = 26  # outline width

    # Handle: a thick arc above the bag.
    d.arc((250, 70, 774, 560), 180, 360, fill=OUTLINE, width=110)
    d.arc((272, 92, 752, 538), 180, 360, fill=STRAP, width=66)

    # Body.
    body = (110, 300, 914, 940)
    d.rounded_rectangle(body, radius=120, fill=OUTLINE)
    d.rounded_rectangle((body[0] + w, body[1] + w, body[2] - w, body[3] - w), radius=96, fill=BODY)
    d.rounded_rectangle((body[0] + w, 760, body[2] - w, body[3] - w), radius=96, fill=BODY_SHADE)
    d.rectangle((body[0] + w, 760, body[2] - w, 820), fill=BODY_SHADE)
    # Side stitching.
    for x in (190, 834):
        for y in range(640, 880, 56):
            d.line((x, y, x, y + 30), fill=STITCH, width=14)

    # Flap: covers the top, with a rounded point at the front.
    flap = [(110, 380), (110, 560), (512, 700), (914, 560), (914, 380)]
    d.rounded_rectangle((110, 300, 914, 520), radius=120, fill=OUTLINE)
    d.polygon(flap, fill=OUTLINE)
    inner = [(136, 400), (136, 545), (512, 672), (888, 545), (888, 400)]
    d.rounded_rectangle((136, 326, 888, 500), radius=96, fill=FLAP)
    d.polygon(inner, fill=FLAP)
    d.rounded_rectangle((190, 350, 834, 400), radius=25, fill=FLAP_LIGHT)
    # Flap stitching along its edge.
    for (x1, y1), (x2, y2) in (((180, 530), (512, 642)), ((512, 642), (844, 530))):
        steps = 7
        for i in range(steps):
            a, b = i / steps, (i + 0.5) / steps
            d.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b),
                   fill=STITCH, width=14)

    # Buckle strap and buckle.
    d.rectangle((452, 600, 572, 830), fill=OUTLINE)
    d.rectangle((474, 600, 550, 808), fill=STRAP)
    d.rounded_rectangle((400, 620, 624, 780), radius=34, fill=OUTLINE)
    d.rounded_rectangle((424, 644, 600, 756), radius=22, fill=GOLD)
    d.rounded_rectangle((462, 674, 562, 726), radius=12, fill=GOLD_DARK)
    d.rectangle((500, 660, 524, 740), fill=GOLD)
    return image


def main() -> None:
    big = draw()
    images = [big.resize((size, size), Image.LANCZOS) for size in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(size, size) for size in SIZES], append_images=images[:-1])
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
