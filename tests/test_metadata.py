import re

import cv2
import numpy as np
import piexif

from scanner_app.metadata import XMP_HEADER, write_metadata


def jpeg():
    ok, buf = cv2.imencode(".jpg", np.full((40, 50, 3), 120, np.uint8))
    return buf.tobytes()


def xmp_of(data: bytes) -> str:
    i = data.index(XMP_HEADER)
    return data[i + len(XMP_HEADER):].split(b'<?xpacket end="w"?>')[0].decode("utf-8")


def test_keywords_and_year_are_embedded():
    out = write_metadata(jpeg(), ["Mummo", "Kesä & ranta"], 1985, 450)
    xmp = xmp_of(out)
    assert re.findall(r"<rdf:li>(.*?)</rdf:li>", xmp) == ["Mummo", "Kesä &amp; ranta"]
    assert "<photoshop:DateCreated>1985</photoshop:DateCreated>" in xmp
    exif = piexif.load(out)
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"1985:01:01 00:00:00"
    assert exif["0th"][piexif.ImageIFD.XResolution] == (450, 1)
    assert cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR).shape == (40, 50, 3)


def test_rewriting_replaces_old_values():
    once = write_metadata(jpeg(), ["a", "b"], 1985)
    twice = write_metadata(once, ["c"], None)
    assert twice.count(XMP_HEADER) == 1
    assert re.findall(r"<rdf:li>(.*?)</rdf:li>", xmp_of(twice)) == ["c"]
    assert "DateCreated" not in xmp_of(twice)
    assert piexif.ExifIFD.DateTimeOriginal not in piexif.load(twice)["Exif"]
