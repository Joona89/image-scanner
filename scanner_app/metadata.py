"""Write year and tags into JPEG metadata.

Photo managers look in different places, so the same information goes into
several standard fields:

* XMP ``dc:subject`` (keywords): what PhotoPrism, Lightroom, digiKam and
  most other tools read as tags.
* XMP ``photoshop:DateCreated`` and EXIF ``DateTimeOriginal``: when the
  photo was taken. Only the year is known, so the date is 1 January of that
  year. Without this PhotoPrism would date every photo to the day it was
  scanned.
* EXIF ``XPKeywords``: tags shown in Windows Explorer.
* EXIF resolution: the scan resolution, so prints come out at the original size.
"""

from __future__ import annotations

import io
import struct
from xml.sax.saxutils import escape

import piexif

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def _xmp_packet(tags: list[str], year: int | None) -> bytes:
    items = "".join(f"<rdf:li>{escape(t)}</rdf:li>" for t in tags)
    subject = f"<dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>" if tags else ""
    date = ""
    if year is not None:
        date = (f"<photoshop:DateCreated>{year:04d}</photoshop:DateCreated>"
                f"<exif:DateTimeOriginal>{year:04d}-01-01T00:00:00</exif:DateTimeOriginal>")
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
        ' xmlns:exif="http://ns.adobe.com/exif/1.0/">'
        f"{subject}{date}</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return xml.encode("utf-8")


def _segments(jpeg: bytes):
    """Split a JPEG into (marker, payload) header segments and the rest."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    pos, segs = 2, []
    while pos < len(jpeg):
        marker = jpeg[pos:pos + 2]
        if marker[0] != 0xFF or marker[1] == 0xDA:  # start of scan: image data follows
            break
        length = struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
        segs.append((marker, jpeg[pos + 4:pos + 2 + length]))
        pos += 2 + length
    return segs, jpeg[pos:]


def _set_xmp(jpeg: bytes, packet: bytes) -> bytes:
    segs, rest = _segments(jpeg)
    segs = [s for s in segs if not (s[0] == b"\xff\xe1" and s[1].startswith(XMP_HEADER))]
    # Place XMP right after APP0/EXIF, where readers expect it.
    i = 0
    while i < len(segs) and segs[i][0] in (b"\xff\xe0", b"\xff\xe1"):
        i += 1
    payload = XMP_HEADER + packet
    segs.insert(i, (b"\xff\xe1", payload))
    out = bytearray(b"\xff\xd8")
    for marker, data in segs:
        out += marker + struct.pack(">H", len(data) + 2) + data
    return bytes(out + rest)


def write_metadata(jpeg: bytes, tags: list[str], year: int | None, dpi: int | None = None) -> bytes:
    """Return ``jpeg`` with year, tags and resolution embedded."""
    try:
        exif = piexif.load(jpeg)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    zeroth, ex = exif.setdefault("0th", {}), exif.setdefault("Exif", {})

    if tags:
        zeroth[piexif.ImageIFD.XPKeywords] = tuple(";".join(tags).encode("utf-16-le") + b"\x00\x00")
    else:
        zeroth.pop(piexif.ImageIFD.XPKeywords, None)
    if year is not None:
        ex[piexif.ExifIFD.DateTimeOriginal] = f"{year:04d}:01:01 00:00:00".encode()
    else:
        ex.pop(piexif.ExifIFD.DateTimeOriginal, None)
    if dpi:
        zeroth[piexif.ImageIFD.XResolution] = (int(dpi), 1)
        zeroth[piexif.ImageIFD.YResolution] = (int(dpi), 1)
        zeroth[piexif.ImageIFD.ResolutionUnit] = 2  # inches
    zeroth[piexif.ImageIFD.Software] = b"Image Scanner"
    ex.setdefault(piexif.ExifIFD.ExifVersion, b"0231")
    ex.setdefault(piexif.ExifIFD.ColorSpace, 1)  # sRGB

    buf = io.BytesIO()
    piexif.insert(piexif.dump(exif), jpeg, buf)
    return _set_xmp(buf.getvalue(), _xmp_packet(tags, year))
