"""Core logic for merging up to 30 photos into a single TIFF without resampling.

Two layouts are supported:

* ``pages``  - a multi-page TIFF. Every photo becomes its own page, stored
  pixel-for-pixel at its original resolution and bit depth.
* ``grid`` / ``horizontal`` / ``vertical`` - one large image. Photos are
  pasted side by side at their native size (never scaled), so the canvas
  grows to fit them instead of the photos shrinking to fit the canvas.

All output uses lossless compression, so no detail is lost.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageOps

MAX_PHOTOS = 30
LAYOUTS = ("pages", "grid", "horizontal", "vertical")
COMPRESSIONS = ("tiff_lzw", "tiff_adobe_deflate", "raw")
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp",
}

# Classic TIFF uses 32-bit offsets; above ~4 GB we must write BigTIFF.
_CLASSIC_TIFF_LIMIT = 4 * 1024**3 - 64 * 1024**2

# Merged canvases can legitimately exceed Pillow's decompression-bomb guard.
Image.MAX_IMAGE_PIXELS = None

# Pixel modes that the TIFF writer can store as-is for multi-page output.
_TIFF_NATIVE_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16", "I", "F"}


class MergeError(Exception):
    """Raised for invalid input to the merger."""


@dataclass
class MergeResult:
    output: Path
    layout: str
    photo_count: int
    size: tuple[int, int] | None  # canvas size; None for multi-page output
    bigtiff: bool


def collect_images(paths: Iterable[str | Path]) -> list[Path]:
    """Expand directories and return the image files in a stable order."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(
                sorted(
                    f for f in p.iterdir()
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
                )
            )
        elif p.is_file():
            files.append(p)
        else:
            raise MergeError(f"File not found: {p}")
    return files


def _load(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:  # Pillow raises a variety of errors
        raise MergeError(f"Cannot open image {path}: {exc}") from exc
    # Apply the camera's EXIF rotation so photos are the right way up.
    # This only rotates/flips pixels; it never resamples.
    return ImageOps.exif_transpose(img) or img


def _for_tiff_page(img: Image.Image) -> Image.Image:
    if img.mode in _TIFF_NATIVE_MODES:
        return img
    return img.convert("RGBA" if "A" in img.getbands() else "RGB")


def _canvas_mode(images: Sequence[Image.Image]) -> str:
    has_alpha = any(
        "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
        for im in images
    )
    return "RGBA" if has_alpha else "RGB"


def _grid_shape(count: int, layout: str, columns: int | None) -> tuple[int, int]:
    if layout == "horizontal":
        return count, 1
    if layout == "vertical":
        return 1, count
    cols = columns or math.ceil(math.sqrt(count))
    cols = max(1, min(cols, count))
    return cols, math.ceil(count / cols)


def _enable_bigtiff(save_opts: dict) -> None:
    # Pillow only writes BigTIFF through its built-in (uncompressed) encoder;
    # the libtiff compressors ignore the flag. Uncompressed is still lossless.
    save_opts["big_tiff"] = True
    save_opts["compression"] = None


def _estimate_bytes(width: int, height: int, mode: str) -> int:
    return width * height * len(mode)


def merge_photos(
    inputs: Sequence[str | Path],
    output: str | Path,
    layout: str = "pages",
    columns: int | None = None,
    spacing: int = 0,
    background: str = "white",
    compression: str = "tiff_lzw",
    dpi: tuple[float, float] | None = None,
) -> MergeResult:
    """Merge 1-30 photos into a single TIFF file without reducing resolution."""
    if layout not in LAYOUTS:
        raise MergeError(f"Unknown layout {layout!r}; choose from {', '.join(LAYOUTS)}")
    if compression not in COMPRESSIONS:
        raise MergeError(
            f"Unknown compression {compression!r}; choose from {', '.join(COMPRESSIONS)}"
        )
    if spacing < 0:
        raise MergeError("Spacing cannot be negative")

    files = collect_images(inputs)
    if not files:
        raise MergeError("No input photos given")
    if len(files) > MAX_PHOTOS:
        raise MergeError(f"At most {MAX_PHOTOS} photos can be merged (got {len(files)})")

    output = Path(output)
    if output.suffix.lower() not in (".tif", ".tiff"):
        output = output.with_suffix(".tiff")
    output.parent.mkdir(parents=True, exist_ok=True)

    images = [_load(f) for f in files]
    try:
        # Keep the highest source DPI so print size metadata stays sensible.
        if dpi is None:
            dpis = [im.info.get("dpi") for im in images if im.info.get("dpi")]
            if dpis:
                dpi = max(dpis, key=lambda d: float(d[0]))
        save_opts: dict = {"compression": None if compression == "raw" else compression}
        if dpi:
            save_opts["dpi"] = tuple(float(v) for v in dpi)

        if layout == "pages":
            pages = [_for_tiff_page(im) for im in images]
            total = sum(_estimate_bytes(*p.size, p.mode) for p in pages)
            bigtiff = total > _CLASSIC_TIFF_LIMIT
            if bigtiff:
                _enable_bigtiff(save_opts)
            icc = images[0].info.get("icc_profile")
            if icc:
                save_opts["icc_profile"] = icc
            pages[0].save(
                output, format="TIFF", save_all=True, append_images=pages[1:], **save_opts
            )
            return MergeResult(output, layout, len(images), None, bigtiff)

        cols, rows = _grid_shape(len(images), layout, columns)
        col_widths = [0] * cols
        row_heights = [0] * rows
        for i, im in enumerate(images):
            c, r = i % cols, i // cols
            col_widths[c] = max(col_widths[c], im.width)
            row_heights[r] = max(row_heights[r], im.height)

        width = sum(col_widths) + spacing * (cols + 1)
        height = sum(row_heights) + spacing * (rows + 1)
        mode = _canvas_mode(images)
        canvas = Image.new(mode, (width, height), background)

        y = spacing
        for r in range(rows):
            x = spacing
            for c in range(cols):
                i = r * cols + c
                if i >= len(images):
                    break
                im = images[i]
                src = im.convert(mode) if im.mode != mode else im
                # Centre each photo in its cell; pasted 1:1, never resized.
                ox = x + (col_widths[c] - im.width) // 2
                oy = y + (row_heights[r] - im.height) // 2
                if mode == "RGBA":
                    canvas.alpha_composite(src, (ox, oy))
                else:
                    canvas.paste(src, (ox, oy))
                x += col_widths[c] + spacing
            y += row_heights[r] + spacing

        bigtiff = _estimate_bytes(width, height, mode) > _CLASSIC_TIFF_LIMIT
        if bigtiff:
            _enable_bigtiff(save_opts)
        canvas.save(output, format="TIFF", **save_opts)
        return MergeResult(output, layout, len(images), (width, height), bigtiff)
    finally:
        for im in images:
            im.close()
