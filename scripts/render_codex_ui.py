"""Render the firmware Codex layout at native resolution for visual QA."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 320, 240
COLUMNS = (28, 94, 160, 226)
SLOT_X = (94, 160, 28, 94, 160, 226)
SLOT_Y = (16, 16, 64, 64, 64, 64)


def rounded_key(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: str,
    border: str,
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0 + 3, x1, y1 + 3), 13, fill="#090a0e")
    draw.rounded_rectangle(box, 13, fill=border)
    draw.rounded_rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), 11, fill=fill)
    draw.line((x0 + 9, y0 + 3, x1 - 9, y0 + 3), fill="#4a4d58")


def render(scale: int = 3) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#17181d")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 7, 302, 233), 18, fill="#0c0d12", outline="#30323b")
    draw.rounded_rectangle((20, 9, 300, 231), 16, outline="#22242d")

    for index, (x, y) in enumerate(zip(SLOT_X, SLOT_Y, strict=True)):
        selected = index == 0
        rounded_key(
            draw,
            (x, y, x + 58, y + 42),
            "#4856b6" if selected else "#b9bbc1",
            "#6975d6" if selected else "#d7d8dc",
        )
        draw.ellipse((x + 23, y + 15, x + 35, y + 27), fill="#7b6cdc")
        if selected:
            draw.ellipse((x + 21, y + 13, x + 37, y + 29), outline="#9a91f0")

    draw.ellipse((32, 12, 82, 62), fill="#20222b", outline="#393c47")
    draw.polygon(((34, 38), (57, 14), (72, 18)), fill="#30333d")
    draw.rounded_rectangle((226, 16, 284, 58), 13, fill="#292b32")
    draw.ellipse((239, 21, 271, 53), fill="#020304")

    for x in COLUMNS:
        rounded_key(draw, (x, 112, x + 58, 158), "#181920", "#343640")

    # Lightning, new chat, fork, and steer glyphs mirror the firmware bitmaps.
    draw.line((49, 124, 42, 137, 51, 137, 46, 148, 67, 131, 57, 131, 62, 122),
              fill="#f7f4ee", width=2, joint="curve")
    draw.ellipse((109, 122, 137, 148), outline="#f7f4ee", width=2)
    draw.ellipse((116, 133, 119, 136), fill="#f7f4ee")
    draw.ellipse((123, 133, 126, 136), fill="#f7f4ee")
    draw.ellipse((130, 133, 133, 136), fill="#f7f4ee")
    draw.line((189, 125, 189, 142, 177, 142, 177, 130), fill="#f7f4ee", width=2)
    draw.ellipse((173, 124, 181, 132), outline="#f7f4ee", width=2)
    draw.ellipse((185, 142, 193, 150), outline="#f7f4ee", width=2)
    draw.line((241, 140, 264, 122, 263, 132), fill="#777b87", width=2)
    draw.line((264, 122, 254, 123), fill="#777b87", width=2)

    rounded_key(draw, (28, 164, 86, 226), "#15161d", "#282a33")
    for index, color in enumerate(("#a9c9ff", "#d8d590", "#8e92a2")):
        radius = 3 if index == 0 else 2
        draw.ellipse((37 - radius, 183 + index * 7 - radius,
                      37 + radius, 183 + index * 7 + radius), fill=color)
    draw.ellipse((46, 180, 76, 210), fill="#111219")

    rounded_key(draw, (94, 164, 218, 226), "#181920", "#373944")
    draw.rounded_rectangle((147, 177, 165, 203), 9, outline="#f7f4ee", width=2)
    draw.arc((140, 184, 172, 212), 0, 180, fill="#f7f4ee", width=2)
    draw.line((156, 211, 156, 218), fill="#f7f4ee", width=2)
    draw.line((149, 218, 163, 218), fill="#f7f4ee", width=2)

    rounded_key(draw, (226, 164, 284, 226), "#181920", "#343640")
    draw.rectangle((244, 185, 266, 207), outline="#f7f4ee", width=2)
    draw.line((241, 182, 269, 182), fill="#f7f4ee", width=2)
    draw.line((249, 190, 261, 190), fill="#f7f4ee", width=2)

    return image.resize((WIDTH * scale, HEIGHT * scale), Image.Resampling.NEAREST)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render(max(1, args.scale)).save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
