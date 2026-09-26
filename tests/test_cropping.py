import cv2
import numpy as np
import pytest

from scanner_app.cropping import crop_or_whole, detect_photos
from scanner_app.synthetic import DEMO_LAYOUTS, _photo, make_scan


@pytest.mark.parametrize("i", range(len(DEMO_LAYOUTS)))
def test_finds_every_photo_with_right_size(i):
    layout = DEMO_LAYOUTS[i]
    found = detect_photos(make_scan(layout, seed=i))
    assert len(found) == len(layout)
    got = sorted(sorted(p.image.shape[:2]) for p in found)
    # White-bordered prints may lose their pale border, so compare against the inner photo.
    want = sorted(sorted((s["h"], s["w"])) for s in layout)
    for g, w in zip(got, want):
        assert abs(g[0] - w[0]) <= 12 and abs(g[1] - w[1]) <= 12, (g, w)


def test_crop_is_deskewed_and_matches_original():
    spec = dict(w=500, h=350, cx=700, cy=800, angle=6)
    scan = make_scan([spec], seed=7, noise=0)
    (found,) = detect_photos(scan)
    original = _photo(500, 350, 7)
    crop = cv2.resize(found.image, (500, 350))
    diff = np.abs(crop.astype(int) - original.astype(int))[20:-20, 20:-20].mean()
    assert diff < 20, diff


def test_portrait_photo_stays_portrait():
    scan = make_scan([dict(w=400, h=600, cx=800, cy=900, angle=-4)], seed=3)
    (found,) = detect_photos(scan)
    h, w = found.image.shape[:2]
    assert h > w


def test_reading_order_is_top_to_bottom_left_to_right():
    layout = DEMO_LAYOUTS[0]
    found = detect_photos(make_scan(layout, seed=0))
    centers = [p.rect[0] for p in found]
    assert centers[0][0] < centers[1][0] and centers[0][1] < centers[2][1]


def test_empty_scan_keeps_whole_image():
    blank = make_scan([], seed=1)
    assert detect_photos(blank) == []
    (whole,) = crop_or_whole(blank)
    assert whole.shape == blank.shape


def test_dark_background():
    scan = make_scan([dict(w=500, h=400, cx=500, cy=500, angle=2, white_border=20),
                      dict(w=500, h=400, cx=1100, cy=1500, angle=-3)],
                     background=(20, 20, 20), seed=4)
    found = detect_photos(scan)
    assert len(found) == 2
    # On a dark lid even the white print border is kept.
    assert max(max(p.image.shape[:2]) for p in found) >= 530
