import random

import pytest
from PIL import Image, ImageSequence

from photomerge import MAX_PHOTOS, MergeError, merge_photos
from photomerge.cli import main


def make_photo(path, size, seed, mode="RGB"):
    rnd = random.Random(seed)
    img = Image.new(mode, size)
    img.putdata([tuple(rnd.randrange(256) for _ in mode) for _ in range(size[0] * size[1])])
    img.save(path)
    return img


def test_pages_are_pixel_identical(tmp_path):
    sizes = [(64, 48), (40, 90), (120, 30)]
    originals = []
    paths = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"p{i}.png"
        originals.append(make_photo(p, size, i))
        paths.append(p)

    res = merge_photos(paths, tmp_path / "out.tiff", layout="pages")
    with Image.open(res.output) as tif:
        pages = [page.copy() for page in ImageSequence.Iterator(tif)]
    assert len(pages) == 3
    for orig, page in zip(originals, pages):
        assert page.size == orig.size
        assert page.tobytes() == orig.tobytes()


@pytest.mark.parametrize("layout,expected", [
    ("horizontal", (64 + 40 + 120, 90)),
    ("vertical", (120, 48 + 90 + 30)),
])
def test_stitched_layouts_keep_full_resolution(tmp_path, layout, expected):
    paths = []
    for i, size in enumerate([(64, 48), (40, 90), (120, 30)]):
        p = tmp_path / f"p{i}.png"
        make_photo(p, size, i)
        paths.append(p)
    res = merge_photos(paths, tmp_path / "out.tif", layout=layout)
    assert res.size == expected
    with Image.open(res.output) as tif:
        assert tif.size == expected


def test_grid_pastes_photos_unscaled(tmp_path):
    paths = []
    originals = []
    for i in range(4):
        p = tmp_path / f"p{i}.png"
        originals.append(make_photo(p, (20, 10), i))
        paths.append(p)
    res = merge_photos(paths, tmp_path / "grid.tiff", layout="grid", spacing=2)
    assert res.size == (2 + 20 + 2 + 20 + 2, 2 + 10 + 2 + 10 + 2)
    with Image.open(res.output) as tif:
        crop = tif.crop((24, 14, 44, 24))  # bottom-right cell
        assert crop.tobytes() == originals[3].tobytes()


def test_limit_of_thirty(tmp_path):
    paths = []
    for i in range(MAX_PHOTOS + 1):
        p = tmp_path / f"p{i:02}.png"
        make_photo(p, (4, 4), i)
        paths.append(p)
    merge_photos(paths[:MAX_PHOTOS], tmp_path / "ok.tiff")
    with pytest.raises(MergeError, match="At most 30"):
        merge_photos(paths, tmp_path / "too_many.tiff")


def test_folder_input_and_cli(tmp_path, capsys):
    folder = tmp_path / "photos"
    folder.mkdir()
    for i in range(3):
        make_photo(folder / f"{i}.jpg", (16, 16), i)
    out = tmp_path / "cli.tiff"
    assert main([str(folder), "-o", str(out), "-l", "grid"]) == 0
    assert out.exists()
    assert "Saved" in capsys.readouterr().out


def test_alpha_and_missing_file(tmp_path):
    p = tmp_path / "a.png"
    make_photo(p, (8, 8), 1, mode="RGBA")
    res = merge_photos([p], tmp_path / "a.tiff", layout="grid")
    with Image.open(res.output) as tif:
        assert tif.mode == "RGBA"
    with pytest.raises(MergeError, match="not found"):
        merge_photos([tmp_path / "nope.jpg"], tmp_path / "x.tiff")


def test_bigtiff_used_for_huge_output(tmp_path, monkeypatch):
    import photomerge.merger as merger

    monkeypatch.setattr(merger, "_CLASSIC_TIFF_LIMIT", 10)
    p = tmp_path / "p.png"
    make_photo(p, (8, 8), 0)
    for layout in ("pages", "grid"):
        res = merge_photos([p, p], tmp_path / f"{layout}.tiff", layout=layout)
        assert res.bigtiff
        assert res.output.read_bytes()[:4] == b"II+\x00"
        with Image.open(res.output) as tif:
            assert tif.size in ((8, 8), (16, 8))
