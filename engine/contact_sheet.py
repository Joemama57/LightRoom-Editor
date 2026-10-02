"""A labelled grid of previews so a person (or Claude Code) can judge the set at a glance."""

import math

from PIL import Image, ImageDraw, ImageFont

TILE = 360
LABEL_H = 44
PAD = 8
COLUMNS = 4


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def build(tiles, path):
    """tiles: list of {"path", "title", "subtitle", "highlight"}; the first is the reference."""
    cols = min(COLUMNS, len(tiles))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * (TILE + PAD) + PAD, rows * (TILE + LABEL_H + PAD) + PAD), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    title_font, sub_font = _font(16), _font(13)

    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        x = PAD + c * (TILE + PAD)
        y = PAD + r * (TILE + LABEL_H + PAD)
        with Image.open(tile["path"]) as im:
            im = im.convert("RGB")
            im.thumbnail((TILE, TILE))
            sheet.paste(im, (x + (TILE - im.width) // 2, y + (TILE - im.height) // 2))
        if tile.get("highlight"):
            draw.rectangle([x - 3, y - 3, x + TILE + 2, y + TILE + 2], outline=(230, 190, 40), width=3)
        draw.text((x, y + TILE + 4), tile["title"], fill=(240, 240, 240), font=title_font)
        draw.text((x, y + TILE + 24), tile.get("subtitle", ""), fill=(170, 170, 170), font=sub_font)

    sheet.save(path, quality=90)
    return path
