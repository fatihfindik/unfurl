"""Draw the extension's toolbar icons from the logo's shapes (app/static/img/logo.svg).

Run once after changing the logo:  uv run python scripts/make_extension_icons.py
Writes extension/icons/icon-{16,32,48,128}.png. Each icon is drawn large and scaled
down, so the edges are smooth (Pillow draws shapes without anti-aliasing).
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 32, 48, 128)
BIG = 34 * 16  # the logo's 34×34 box, drawn 16 times larger
INDIGO = "#4F46E5"
WHITE = "#FFFFFF"
AMBER = "#FDB022"
OUT = Path(__file__).parent.parent / "extension" / "icons"


def scale(x: float, y: float) -> tuple[float, float]:
    """A point inside the logo's flag group (translate 7, scale 0.8333) on the big canvas."""
    return ((7 + x * 0.8333) * 16, (7 + y * 0.8333) * 16)


def draw_logo() -> Image.Image:
    image = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, BIG - 1, BIG - 1), radius=10 * 16, fill=INDIGO)
    # The pole.
    (x, top), (_, bottom) = scale(6.5, 5), scale(6.5, 21)
    half = 2.4 * 0.8333 * 16 / 2
    draw.rounded_rectangle((x - half, top, x + half, bottom), radius=half, fill=WHITE)
    # The flag, its wavy edges simplified to straight lines.
    flag = [
        (6.5, 5.5),
        (10, 5),
        (13.7, 5.5),
        (19, 4.9),
        (19, 13.2),
        (13.7, 14.4),
        (10, 13.4),
        (6.5, 13.5),
    ]
    draw.polygon([scale(px, py) for px, py in flag], fill=WHITE)
    # The dot on top of the pole.
    cx, cy = scale(6.5, 3.6)
    r = 1.9 * 0.8333 * 16
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=AMBER)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo = draw_logo()
    for size in SIZES:
        logo.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png")
        print(f"wrote {OUT / f'icon-{size}.png'}")


if __name__ == "__main__":
    main()
