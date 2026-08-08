#!/usr/bin/env python3
"""Build aligned 320x240 original robot frames and C++ flash assets."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHEET = (
    ROOT / "firmware/assets/source/original-robot-expression-sheet-v1.png"
)
OUTPUT_DIR = ROOT / "firmware/assets/faces"
HEADER = ROOT / "firmware/include/generated/FaceAssets.hpp"
SOURCE_CPP = ROOT / "firmware/src/generated/FaceAssets.cpp"

FRAME_CELLS = {
    "neutral": (0, 0),
    "happy": (1, 0),
    "listening": (2, 0),
    "thinking": (0, 1),
    "speaking_soft": (1, 1),
    "speaking_excited": (2, 1),
    "surprised": (0, 2),
    "sleepy": (1, 2),
    "shy": (2, 2),
    "worried": (0, 3),
    "playful": (1, 3),
    "petted": (2, 3),
    "blink": (1, 2),
}

# The generated source uses three aligned face centers per row. Direct crops
# avoid scaling the round shell into an oval and keep every eye baseline fixed.
CROP_LEFT = (110, 564, 1018)
CROP_TOP = (40, 287, 534, 780)
FRAME_SIZE = (320, 240)


def build_frames() -> dict[str, Image.Image]:
    sheet = Image.open(SOURCE_SHEET).convert("RGB")
    if sheet.size != (1448, 1086):
        raise RuntimeError(f"unexpected source sheet size: {sheet.size}")
    frames = {}
    for name, (column, row) in FRAME_CELLS.items():
        left = CROP_LEFT[column]
        top = CROP_TOP[row]
        frame = sheet.crop((left, top, left + FRAME_SIZE[0], top + FRAME_SIZE[1]))
        if frame.size != FRAME_SIZE:
            raise RuntimeError(f"invalid crop for {name}: {frame.size}")
        frames[name] = frame
    return frames


def cpp_bytes(payload: bytes) -> str:
    rows = []
    for offset in range(0, len(payload), 16):
        rows.append("  " + ", ".join(f"0x{value:02x}" for value in payload[offset : offset + 16]))
    return ",\n".join(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HEADER.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_CPP.parent.mkdir(parents=True, exist_ok=True)
    frames = build_frames()
    assets: list[tuple[str, bytes]] = []
    for name in FRAME_CELLS:
        output = OUTPUT_DIR / f"{name}.png"
        frames[name].save(output, format="PNG", optimize=True)
        assets.append((name, output.read_bytes()))

    declarations = "\n".join(
        f"extern const uint8_t {name}_png[];\nextern const size_t {name}_png_len;"
        for name, _ in assets
    )
    HEADER.write_text(
        "#pragma once\n\n#include <Arduino.h>\n\nnamespace stackchan::faces {\n"
        f"{declarations}\n"
        "}\n",
        encoding="utf-8",
    )
    definitions = []
    for name, payload in assets:
        definitions.append(
            f"const uint8_t {name}_png[] PROGMEM = {{\n{cpp_bytes(payload)}\n}};\n"
            f"const size_t {name}_png_len = sizeof({name}_png);"
        )
    SOURCE_CPP.write_text(
        '#include "generated/FaceAssets.hpp"\n\nnamespace stackchan::faces {\n'
        + "\n\n".join(definitions)
        + "\n}\n",
        encoding="utf-8",
    )
    total = sum(len(payload) for _, payload in assets)
    print(f"generated {len(assets)} face frames ({total} PNG bytes)")


if __name__ == "__main__":
    main()
