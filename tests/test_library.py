import json

import numpy as np
import piexif

from scanner_app.library import Library


def img(v=100):
    a = np.zeros((60, 80, 3), np.uint8)
    a[:] = v
    return a


def test_add_scan_writes_files_and_index(tmp_path):
    lib = Library(tmp_path)
    photos = lib.add_scan(img(), [img(1), img(2), img(3)], tags=["album 1"])
    assert len(photos) == 3
    assert all((tmp_path / p.file).exists() for p in photos)
    assert (tmp_path / photos[0].scan).exists()
    data = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert [p["tags"] for p in data["photos"]] == [["album 1"]] * 3


def test_two_scans_in_same_second_do_not_collide(tmp_path):
    lib = Library(tmp_path)
    a = lib.add_scan(img(), [img()])
    b = lib.add_scan(img(), [img()])
    assert a[0].id != b[0].id and len(lib.photos) == 2


def test_multiselect_tagging_and_toggle(tmp_path):
    lib = Library(tmp_path)
    ids = [p.id for p in lib.add_scan(img(), [img(), img(), img()])]
    lib.add_tag(ids[:2], "Mummo")
    assert [lib.photos[i].tags for i in ids] == [["Mummo"], ["Mummo"], []]
    # Mixed selection: toggle adds to everyone first.
    assert lib.toggle_tag(ids, "Mummo") is True
    assert all(lib.photos[i].tags == ["Mummo"] for i in ids)
    # Now everyone has it: toggle removes.
    assert lib.toggle_tag(ids, "Mummo") is False
    assert all(lib.photos[i].tags == [] for i in ids)
    assert lib.all_tags() == []


def test_tags_survive_reload_and_are_embedded(tmp_path):
    lib = Library(tmp_path)
    (p,) = lib.add_scan(img(), [img()])
    lib.add_tag([p.id], "Kesä 1985")
    lib.add_tag([p.id], "beach")

    again = Library(tmp_path)
    assert again.photos[p.id].tags == ["beach", "Kesä 1985"]
    exif = piexif.load(str(tmp_path / p.file))
    raw = bytes(exif["0th"][piexif.ImageIFD.XPKeywords])
    assert raw.decode("utf-16-le").rstrip("\x00") == "beach;Kesä 1985"


def test_rotate_and_delete(tmp_path):
    lib = Library(tmp_path)
    (p,) = lib.add_scan(img(), [img()])
    lib.add_tag([p.id], "x")
    lib.rotate([p.id])
    import cv2
    assert cv2.imread(str(tmp_path / p.file)).shape[:2] == (80, 60)
    lib.delete([p.id])
    assert not (tmp_path / p.file).exists()
    assert Library(tmp_path).photos == {}


def test_parse_year():
    from scanner_app.library import parse_year
    assert parse_year("1985") == 1985
    assert parse_year(" 85 ") == 1985
    assert parse_year("?") == 0
    assert parse_year("198") is None
    assert parse_year("abcd") is None
    assert parse_year("3000") is None


def test_done_needs_year_and_writes_year_folder(tmp_path):
    lib = Library(tmp_path / "lib", tmp_path / "sorted")
    a, b, c = (p.id for p in lib.add_scan(img(), [img(), img(), img()], tags=["Mummo"]))
    lib.set_year([a], 1985)
    lib.set_year([b], 0)
    done, missing = lib.finish([a, b, c])
    assert done == [a, b] and missing == [c]
    assert [p.id for p in lib.todo()] == [c]

    target = tmp_path / "sorted" / "1985" / f"1985_{a}.jpg"
    assert target.exists()
    assert (tmp_path / "sorted" / "Unknown year" / f"{b}.jpg").exists()
    exif = piexif.load(str(target))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"1985:01:01 00:00:00"
    assert b"<rdf:li>Mummo</rdf:li>" in target.read_bytes()


def test_editing_done_photo_updates_sorted_copy(tmp_path):
    lib = Library(tmp_path / "lib", tmp_path / "sorted")
    (p,) = lib.add_scan(img(), [img()], year=1985)
    lib.finish([p.id])
    old = tmp_path / "sorted" / "1985" / f"1985_{p.id}.jpg"

    lib.add_tag([p.id], "beach")
    assert b"<rdf:li>beach</rdf:li>" in old.read_bytes()

    lib.set_year([p.id], 1986)
    new = tmp_path / "sorted" / "1986" / f"1986_{p.id}.jpg"
    assert new.exists() and not old.exists()

    lib.reopen([p.id])
    assert not new.exists() and not lib.photos[p.id].done

    lib.finish([p.id])
    lib.delete([p.id])
    assert not new.exists()


def test_loads_version_1_library(tmp_path):
    lib = Library(tmp_path)
    (p,) = lib.add_scan(img(), [img()])
    data = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    for d in data["photos"]:
        for k in ("year", "dpi", "exported"):
            d.pop(k)
    (tmp_path / "library.json").write_text(json.dumps({"version": 1, **data}), encoding="utf-8")
    again = Library(tmp_path)
    assert again.photos[p.id].year is None and not again.photos[p.id].done


def test_parse_date():
    from scanner_app.library import parse_date
    assert parse_date("1985") == (1985, None, None)
    assert parse_date("1985-06") == (1985, 6, None)
    assert parse_date("1985-06-14") == (1985, 6, 14)
    assert parse_date("14.6.1985") == (1985, 6, 14)
    assert parse_date("6.1985") == (1985, 6, None)
    assert parse_date("14/6/85") == (1985, 6, 14)
    assert parse_date("?") == (0, None, None)
    assert parse_date("31.2.1985") is None
    assert parse_date("1985-13") is None
    assert parse_date("june 1985") is None


def test_full_date_and_caption_in_sorted_file(tmp_path):
    lib = Library(tmp_path / "lib", tmp_path / "sorted")
    (p,) = lib.add_scan(img(), [img()])
    lib.set_date([p.id], (1985, 6, 14))
    lib.set_caption([p.id], "  Mummon 60v, Tampere  ")
    lib.finish([p.id])
    target = tmp_path / "sorted" / "1985" / f"1985-06-14_{p.id}.jpg"
    assert target.exists()
    exif = piexif.load(str(target))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"1985:06:14 00:00:00"
    assert exif["Exif"][piexif.ExifIFD.DateTimeDigitized].startswith(p.created[:4].encode())
    assert exif["0th"][piexif.ImageIFD.ImageDescription].decode("utf-8") == "Mummon 60v, Tampere"
    assert "Mummon 60v, Tampere</rdf:li>" in target.read_bytes().decode("utf-8", "ignore")

    # Month-only date: file renamed, old one gone.
    lib.set_date([p.id], (1985, 6, None))
    assert not target.exists()
    assert (tmp_path / "sorted" / "1985" / f"1985-06_{p.id}.jpg").exists()
    assert Library(tmp_path / "lib", tmp_path / "sorted").photos[p.id].caption == "Mummon 60v, Tampere"
