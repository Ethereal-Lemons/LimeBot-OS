"""Build bounded timestamped contact sheets for multimodal ingestion."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .constants import MAX_SHEET_BYTES
from .frames import Frame
from .time_utils import format_timestamp


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _render(frames: list[Frame], target: Path, tile_width: int, quality: int) -> None:
    tile_height = round(tile_width * 9 / 16) + 34
    sheet = Image.new("RGB", (tile_width * 4, tile_height * 4), "#111111")
    draw = ImageDraw.Draw(sheet)
    font = _font(max(11, tile_width // 25))
    for index, frame in enumerate(frames):
        column, row = index % 4, index // 4
        x, y = column * tile_width, row * tile_height
        image_area = (x, y, x + tile_width, y + tile_height - 34)
        with Image.open(frame.path) as source:
            image = source.convert("RGB")
            image.thumbnail((tile_width, tile_height - 34), Image.Resampling.LANCZOS)
            offset = (x + (tile_width - image.width) // 2, y + (tile_height - 34 - image.height) // 2)
            sheet.paste(image, offset)
        label = f"{format_timestamp(frame.timestamp)} | {frame.reason}"
        draw.text((x + 6, image_area[3] + 8), label, fill="white", font=font)
    sheet.save(target, "JPEG", quality=quality, optimize=True)


def create_contact_sheets(frames: list[Frame], output_dir: Path, resolution: int) -> list[Path]:
    targets = []
    for sheet_index in range(0, len(frames), 16):
        group = frames[sheet_index : sheet_index + 16]
        target = output_dir / f"contact-sheet-{sheet_index // 16 + 1:02d}.jpg"
        width = resolution
        quality = 85
        while True:
            _render(group, target, width, quality)
            if target.stat().st_size < MAX_SHEET_BYTES:
                break
            if quality > 55:
                quality -= 10
            elif width > 320:
                width = max(320, int(width * 0.8))
            else:
                raise ValueError("contact sheet could not fit the 4 MiB image limit")
        targets.append(target)
    return targets[:3]
