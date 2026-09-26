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
