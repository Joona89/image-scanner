import numpy as np

from scanner_app.scanner import pick_resolution, resample


class Vec:
    def __init__(self, values):
        self.values = values
        self.Count = len(values)

    def Item(self, i):
        return self.values[i - 1]


class ListProp:
    SubType = 2

    def __init__(self, values):
        self.SubTypeValues = Vec(values)


class RangeProp:
    SubType = 1
    SubTypeMin, SubTypeMax, SubTypeStep = 75, 1200, 1


def test_pick_resolution_uses_next_higher_when_missing():
    prop = ListProp([75, 150, 300, 600, 1200])
    assert pick_resolution(prop, 300) == 300
    assert pick_resolution(prop, 450) == 600
    assert pick_resolution(prop, 2400) == 1200


def test_pick_resolution_range_and_unknown():
    assert pick_resolution(RangeProp(), 450) == 450
    assert pick_resolution(None, 450) == 450


def test_resample_scales_to_requested_dpi():
    img = np.zeros((600, 800, 3), np.uint8)
    assert resample(img, 600, 450).shape[:2] == (450, 600)
    assert resample(img, 300, 300) is img
