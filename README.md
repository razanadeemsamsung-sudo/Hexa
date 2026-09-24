# PhotoMerge

Merge **up to 30 photos into a single TIFF file** without reducing their resolution.
Photos are never resized: every pixel of every photo ends up in the output, stored
with lossless compression.

## Windows .exe (no Python needed)

Every push is built into Windows executables by GitHub Actions
(`.github/workflows/build-exe.yml`):

* **`PhotoMerge.exe`** – double-click to open the graphical app.
* **`photomerge-cli.exe`** – the same command-line options as below, e.g.
  `photomerge-cli.exe C:\Photos -o merged.tiff --layout grid`.

Download them from the repository's **Actions** tab → latest *Build Windows EXE* run →
**Artifacts → PhotoMerge-windows** (a zip). Pushing a tag such as `v1.0.0` also
attaches both files to a GitHub Release.

To build locally on Windows:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name PhotoMerge --paths . packaging\photomerge_gui.py
pyinstaller --onefile --console --name photomerge-cli --paths . packaging\photomerge_cli.py
```

The executables appear in `dist\`.

## Install (Python)

```bash
pip install -r requirements.txt      # just Pillow
# or install the `photomerge` command:
pip install .
```

Python 3.9+ is required. The optional GUI uses Tkinter, which ships with most
Python installers (on Debian/Ubuntu: `sudo apt install python3-tk`).

## Usage

### Command line

```bash
# Multi-page TIFF: each photo is its own page, untouched (default)
python -m photomerge photo1.jpg photo2.jpg photo3.png -o merged.tiff

# A whole folder of photos (sorted by name)
python -m photomerge ./holiday_photos -o holiday.tiff

# One big image: photos side by side in a grid at full size, 20 px gaps
python -m photomerge ./holiday_photos -o collage.tiff --layout grid --spacing 20

# Single row / single column
python -m photomerge a.jpg b.jpg c.jpg -o strip.tiff --layout horizontal
python -m photomerge a.jpg b.jpg c.jpg -o strip.tiff --layout vertical
```

| Option | Meaning |
| --- | --- |
| `-o, --output` | Output file (default `merged.tiff`) |
| `-l, --layout` | `pages` (default), `grid`, `horizontal`, `vertical` |
| `-c, --columns` | Columns for `grid` (default: square-ish) |
| `-s, --spacing` | Gap in pixels between photos in stitched layouts |
| `-b, --background` | Colour of gaps / empty space, e.g. `white`, `black`, `#202020` |
| `--compression` | `tiff_lzw` (default), `tiff_adobe_deflate`, or `raw` — all lossless |
| `--dpi` | DPI to record in the file (default: highest DPI among the photos) |
| `--gui` | Open the graphical interface |

### Graphical interface

```bash
python -m photomerge --gui     # or run with no arguments
```

Add photos, reorder them, pick a layout and click **Merge to TIFF…**.

### From Python

```python
from photomerge import merge_photos

result = merge_photos(["a.jpg", "b.jpg"], "out.tiff", layout="grid", spacing=10)
print(result.size)
```

## How resolution is preserved

* **No resampling anywhere.** In `pages` mode each photo is written as its own TIFF
  page at its original size. In stitched modes the canvas grows to fit the photos
  (each grid cell is as large as the biggest photo in its row/column) and photos are
  pasted 1:1, centred in their cell.
* **Lossless output.** LZW/Deflate compression or raw storage — no JPEG re-encoding.
* **Correct orientation.** EXIF rotation from phone/camera photos is applied by
  rotating pixels (not resampling).
* **Metadata.** DPI is preserved; in `pages` mode the first photo's ICC colour profile
  is embedded.
* **Huge outputs.** If the result would exceed the 4 GB limit of classic TIFF, a
  BigTIFF file is written automatically (uncompressed, since Pillow only supports
  BigTIFF without compression).

Supported inputs: JPEG, PNG, TIFF, BMP, GIF, WebP.

Memory note: stitched layouts hold the full canvas in RAM. 30 × 24-megapixel photos
in a grid is about 2.2 GB of pixels, so use `pages` mode on low-memory machines.

## Tests

```bash
pip install pytest
python -m pytest
```
