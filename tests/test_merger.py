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
    tiny = Image.new("RGB", (4, 4), "red")
    for i in range(MAX_PHOTOS + 1):
        p = tmp_path / f"p{i:04}.png"
        tiny.save(p)
        paths.append(p)
    calls = []
    res = merge_photos(paths[:MAX_PHOTOS], tmp_path / "ok.tiff",
                       progress=lambda done, total: calls.append((done, total)))
    assert calls[-1] == (MAX_PHOTOS, MAX_PHOTOS)
    with Image.open(res.output) as tif:
        assert tif.n_frames == MAX_PHOTOS
    with pytest.raises(MergeError, match=f"At most {MAX_PHOTOS}"):
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


@pytest.mark.parametrize("compression", ["deflate", "none"])
def test_canvas_compressions_are_lossless(tmp_path, compression):
    originals, paths = [], []
    for i, size in enumerate([(30, 20), (25, 40), (10, 10)]):
        p = tmp_path / f"p{i}.png"
        originals.append(make_photo(p, size, i))
        paths.append(p)
    res = merge_photos(paths, tmp_path / "c.tiff", layout="horizontal",
                       background="black", compression=compression)
    with Image.open(res.output) as tif:
        assert tif.size == (65, 40)
        # second photo fills its whole cell height, starting at x=30
        assert tif.crop((30, 0, 55, 40)).tobytes() == originals[1].tobytes()
        # empty space under the first photo is background
        assert tif.getpixel((0, 39)) == (0, 0, 0)
    assert not list(tmp_path.glob(".photomerge-*")), "scratch files left behind"


def test_folder_uses_natural_order(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    for n, colour in [(10, "blue"), (2, "red"), (1, "green")]:
        Image.new("RGB", (2, 2), colour).save(folder / f"img{n}.png")
    res = merge_photos([folder], tmp_path / "o.tiff", layout="horizontal")
    with Image.open(res.output) as tif:
        assert [tif.getpixel((x, 0)) for x in (0, 2, 4)] == [
            (0, 128, 0), (255, 0, 0), (0, 0, 255)]


def test_exif_rotation_applied(tmp_path):
    img = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees
    img.save(tmp_path / "r.jpg", exif=exif)
    res = merge_photos([tmp_path / "r.jpg"], tmp_path / "r.tiff", layout="grid")
    assert res.size == (20, 40)


def test_disk_space_checked_before_stitching(tmp_path, monkeypatch):
    import collections
    import photomerge.merger as merger

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(merger.shutil, "disk_usage", lambda _: usage(10, 10, 10))
    p = tmp_path / "p.png"
    make_photo(p, (8, 8), 0)
    with pytest.raises(MergeError, match="Not enough free disk space"):
        merge_photos([p, p], tmp_path / "x.tiff", layout="grid")
