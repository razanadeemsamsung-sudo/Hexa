"""Command-line interface: ``python -m photomerge photo1.jpg photo2.jpg -o out.tiff``."""

from __future__ import annotations

import argparse
import sys

from .merger import COMPRESSIONS, LAYOUTS, MAX_PHOTOS, MergeError, merge_photos


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="photomerge",
        description=(
            f"Merge up to {MAX_PHOTOS} photos into one TIFF file at full resolution "
            "(photos are never scaled down)."
        ),
    )
    p.add_argument("inputs", nargs="*", help="photo files or folders containing photos")
    p.add_argument("-o", "--output", default="merged.tiff", help="output TIFF path")
    p.add_argument(
        "-l", "--layout", choices=LAYOUTS, default="pages",
        help="pages = multi-page TIFF (one photo per page); "
             "grid/horizontal/vertical = one big image with photos side by side",
    )
    p.add_argument("-c", "--columns", type=int, help="number of columns for the grid layout")
    p.add_argument("-s", "--spacing", type=int, default=0, help="gap in pixels between photos")
    p.add_argument("-b", "--background", default="white", help="background colour for gaps")
    p.add_argument(
        "--compression", choices=COMPRESSIONS, default="deflate",
        help="lossless compression to use (default: deflate)",
    )
    p.add_argument("--dpi", type=float, help="DPI to store in the file (default: from photos)")
    p.add_argument("--gui", action="store_true", help="open the graphical interface")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui or not args.inputs:
        if not args.inputs:
            print("No photos given on the command line; opening the GUI...", file=sys.stderr)
        from .gui import run_gui

        return run_gui()

    def show_progress(done: int, total: int) -> None:
        if sys.stderr is not None:
            end = "\n" if done == total else ""
            print(f"\rMerging photo {done}/{total}", end=end, file=sys.stderr, flush=True)

    try:
        result = merge_photos(
            args.inputs,
            args.output,
            layout=args.layout,
            columns=args.columns,
            spacing=args.spacing,
            background=args.background,
            compression=args.compression,
            dpi=(args.dpi, args.dpi) if args.dpi else None,
            progress=show_progress,
        )
    except (MergeError, OSError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    detail = (
        f"{result.photo_count} pages" if result.size is None
        else f"{result.size[0]} x {result.size[1]} px"
    )
    print(f"Saved {result.output} ({result.layout}, {detail}"
          f"{', BigTIFF' if result.bigtiff else ''})")
    return 0
