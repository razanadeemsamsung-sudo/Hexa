"""Core logic for merging up to 2000 photos into a single TIFF without resampling.

Two layouts are supported:

* ``pages``  - a multi-page TIFF. Every photo becomes its own page, stored
  pixel-for-pixel at its original resolution and bit depth.
* ``grid`` / ``horizontal`` / ``vertical`` - one large image. Photos are
  pasted side by side at their native size (never scaled), so the canvas
  grows to fit them instead of the photos shrinking to fit the canvas.

All output uses lossless compression, so no detail is lost.

Photos are processed one at a time so memory use stays low no matter how
many are merged: pages are streamed straight into the TIFF, and stitched
canvases are assembled in a memory-mapped scratch file on disk rather than
in RAM.
"""

from __future__ import annotations

import gc
import math
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import tifffile
from PIL import Image, ImageColor, ImageOps

MAX_PHOTOS = 2000
LAYOUTS = ("pages", "grid", "horizontal", "vertical")
COMPRESSIONS = ("deflate", "none")
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp",
}

# Classic TIFF uses 32-bit offsets; above ~4 GB we must write BigTIFF.
_CLASSIC_TIFF_LIMIT = 4 * 1024**3 - 64 * 1024**2

# Merged canvases can legitimately exceed Pillow's decompression-bomb guard.
Image.MAX_IMAGE_PIXELS = None

# Pillow mode -> (mode to convert to or None, tifffile photometric, extrasamples)
_PAGE_MODES = {
    "L": (None, "minisblack", None),
    "RGB": (None, "rgb", None),
    "RGBA": (None, "rgb", ("unassalpha",)),
    "CMYK": (None, "separated", None),
    "I;16": (None, "minisblack", None),
    "I;16L": (None, "minisblack", None),
    "I;16B": (None, "minisblack", None),
    "I": (None, "minisblack", None),
    "F": (None, "minisblack", None),
    "1": ("L", "minisblack", None),
    "LA": ("RGBA", "rgb", ("unassalpha",)),
}

# EXIF orientations that rotate the photo by 90 degrees (width/height swap).
_SWAPPING_ORIENTATIONS = {5, 6, 7, 8}

ProgressCallback = Callable[[int, int], None]


class MergeError(Exception):
    """Raised for invalid input to the merger."""


@dataclass
class MergeResult:
    output: Path
    layout: str
    photo_count: int
    size: tuple[int, int] | None  # canvas size; None for multi-page output
    bigtiff: bool


@dataclass
class _PhotoInfo:
    path: Path
    size: tuple[int, int]  # after EXIF rotation
    mode: str
    has_alpha: bool
    dpi: tuple[float, float] | None


def _natural_key(path: Path):
    # "img2.jpg" sorts before "img10.jpg".
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def collect_images(paths: Iterable[str | Path]) -> list[Path]:
    """Expand directories and return the image files in a stable order."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(
                sorted(
                    (f for f in p.iterdir()
                     if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS),
                    key=_natural_key,
                )
            )
        elif p.is_file():
            files.append(p)
        else:
            raise MergeError(f"File not found: {p}")
    return files


def _scan(path: Path) -> _PhotoInfo:
    """Read just the header of a photo (fast; pixels are not decoded)."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            if img.getexif().get(0x0112) in _SWAPPING_ORIENTATIONS:
                w, h = h, w
            has_alpha = "A" in img.getbands() or (
                img.mode == "P" and "transparency" in img.info
            )
            dpi = img.info.get("dpi")
            return _PhotoInfo(
                path, (w, h), img.mode, has_alpha,
                (float(dpi[0]), float(dpi[1])) if dpi else None,
            )
    except Exception as exc:  # Pillow raises a variety of errors
        raise MergeError(f"Cannot open image {path}: {exc}") from exc


def _load(path: Path) -> Image.Image:
    try:
        with Image.open(path) as img:
            img.load()
            # Apply the camera's EXIF rotation so photos are the right way up.
            # This only rotates/flips pixels; it never resamples.
            out = ImageOps.exif_transpose(img)
            return out if out is not None else img.copy()
    except Exception as exc:
        raise MergeError(f"Cannot open image {path}: {exc}") from exc


def _page_array(img: Image.Image) -> tuple[np.ndarray, str, tuple | None]:
    target, photometric, extra = _PAGE_MODES.get(img.mode, (None, None, None))
    if photometric is None:  # P, YCbCr, LAB, ... -> lossless RGB(A)
        has_alpha = "A" in img.getbands() or "transparency" in img.info
        target = "RGBA" if has_alpha else "RGB"
        photometric, extra = "rgb", (("unassalpha",) if has_alpha else None)
    if target:
        img = img.convert(target)
    return np.asarray(img), photometric, extra


def _grid_shape(count: int, layout: str, columns: int | None) -> tuple[int, int]:
    if layout == "horizontal":
        return count, 1
    if layout == "vertical":
        return 1, count
    cols = columns or math.ceil(math.sqrt(count))
    cols = max(1, min(cols, count))
    return cols, math.ceil(count / cols)


def _bytes_per_pixel(mode: str) -> int:
    return {"I;16": 2, "I;16L": 2, "I;16B": 2, "I": 4, "F": 4, "CMYK": 4,
            "RGBA": 4, "LA": 4, "P": 4}.get(mode, 3)


def _write_kwargs(compression: str, dpi: tuple[float, float] | None) -> dict:
    kw: dict = {"metadata": None, "software": "PhotoMerge"}
    if compression == "deflate":
        # Horizontal differencing makes deflate far more effective on photos.
        kw.update(compression="zlib", predictor=True)
    if dpi:
        kw.update(resolution=dpi, resolutionunit="INCH")
    return kw


def merge_photos(
    inputs: Sequence[str | Path],
    output: str | Path,
    layout: str = "pages",
    columns: int | None = None,
    spacing: int = 0,
    background: str = "white",
    compression: str = "deflate",
    dpi: tuple[float, float] | None = None,
    progress: ProgressCallback | None = None,
) -> MergeResult:
    """Merge 1-2000 photos into a single TIFF file without reducing resolution.

    ``progress`` is called as ``progress(done, total)`` after each photo.
    """
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

    # Validate every photo before writing anything, so a bad file found at
    # photo 1999 doesn't waste a long merge.
    infos = [_scan(f) for f in files]
    report = progress or (lambda done, total: None)

    if layout == "pages":
        return _merge_pages(infos, output, compression, dpi, report)
    return _merge_canvas(infos, output, layout, columns, spacing, background,
                         compression, dpi, report)


def _merge_pages(infos, output, compression, dpi, report) -> MergeResult:
    total = sum(i.size[0] * i.size[1] * _bytes_per_pixel(i.mode) for i in infos)
    bigtiff = total > _CLASSIC_TIFF_LIMIT
    with tifffile.TiffWriter(output, bigtiff=bigtiff) as tif:
        for n, info in enumerate(infos, 1):
            img = _load(info.path)
            try:
                arr, photometric, extra = _page_array(img)
                kw = _write_kwargs(compression, dpi or info.dpi)
                if arr.dtype.kind == "f":
                    kw.pop("predictor", None)  # float predictor needs extra codecs
                icc = img.info.get("icc_profile")
                if icc and photometric != "separated":
                    kw["iccprofile"] = icc
                tif.write(arr, photometric=photometric, extrasamples=extra, **kw)
            finally:
                img.close()
                del img
            report(n, len(infos))
    return MergeResult(output, "pages", len(infos), None, bigtiff)


def _merge_canvas(infos, output, layout, columns, spacing, background,
                  compression, dpi, report) -> MergeResult:
    cols, rows = _grid_shape(len(infos), layout, columns)
    col_widths = [0] * cols
    row_heights = [0] * rows
    for i, info in enumerate(infos):
        c, r = i % cols, i // cols
        col_widths[c] = max(col_widths[c], info.size[0])
        row_heights[r] = max(row_heights[r], info.size[1])

    width = sum(col_widths) + spacing * (cols + 1)
    height = sum(row_heights) + spacing * (rows + 1)
    mode = "RGBA" if any(i.has_alpha for i in infos) else "RGB"
    channels = len(mode)
    try:
        bg = ImageColor.getcolor(background, mode)
    except ValueError as exc:
        raise MergeError(f"Unknown background colour {background!r}") from exc

    if dpi is None:
        dpis = [i.dpi for i in infos if i.dpi]
        dpi = max(dpis, key=lambda d: d[0]) if dpis else None

    bigtiff = width * height * channels > _CLASSIC_TIFF_LIMIT
    write_kw = _write_kwargs(compression, dpi)
    extra = ("unassalpha",) if mode == "RGBA" else None
    shape = (height, width, channels)

    needed = width * height * channels
    free = shutil.disk_usage(output.parent).free
    if needed > free:
        raise MergeError(
            f"Not enough free disk space: the {width} x {height} px canvas needs about "
            f"{needed / 1024**3:.1f} GB of scratch space next to the output file, but only "
            f"{free / 1024**3:.1f} GB is free. Choose another drive or use the 'pages' layout."
        )

    # The canvas lives in a memory-mapped file next to the output, so its
    # size is limited by free disk space rather than RAM.
    with tempfile.TemporaryDirectory(
        prefix=".photomerge-", dir=output.parent, ignore_cleanup_errors=True
    ) as tmp:
        if compression == "none":
            # Write straight into the final (uncompressed) TIFF.
            canvas = tifffile.memmap(
                output, shape=shape, dtype=np.uint8, photometric="rgb",
                extrasamples=extra, bigtiff=bigtiff, **write_kw,
            )
        else:
            canvas = np.memmap(Path(tmp) / "canvas.raw", dtype=np.uint8,
                               mode="w+", shape=shape)
        try:
            # Paint the background in bands to keep memory use bounded.
            band = max(1, (64 * 1024**2) // (width * channels))
            for y0 in range(0, height, band):
                canvas[y0:y0 + band] = bg

            y = spacing
            n = 0
            for r in range(rows):
                x = spacing
                for c in range(cols):
                    i = r * cols + c
                    if i >= len(infos):
                        break
                    img = _load(infos[i].path)
                    try:
                        if img.mode != mode:
                            img = img.convert(mode)
                        # Centre each photo in its cell; copied 1:1, never resized.
                        ox = x + (col_widths[c] - img.width) // 2
                        oy = y + (row_heights[r] - img.height) // 2
                        region = canvas[oy:oy + img.height, ox:ox + img.width]
                        if mode == "RGBA":
                            under = Image.fromarray(np.array(region), "RGBA")
                            under.alpha_composite(img)
                            region[...] = np.asarray(under)
                        else:
                            region[...] = np.asarray(img)
                    finally:
                        img.close()
                        del img
                        region = under = None
                    n += 1
                    report(n, len(infos))
                    x += col_widths[c] + spacing
                y += row_heights[r] + spacing

            canvas.flush()
            if compression != "none":
                tifffile.imwrite(
                    output, canvas, photometric="rgb", extrasamples=extra,
                    bigtiff=bigtiff, **write_kw,
                )
        finally:
            # Release the mapping before the scratch file is deleted (Windows).
            del canvas
            gc.collect()

    return MergeResult(output, layout, len(infos), (width, height), bigtiff)
