"""Write year and tags into JPEG metadata.

Photo managers look in different places, so the same information goes into
several standard fields:

* XMP ``dc:subject`` (keywords): what PhotoPrism, Lightroom, digiKam and
  most other tools read as tags.
* XMP ``photoshop:DateCreated`` and EXIF ``DateTimeOriginal``: when the
  photo was taken. XMP can say "1985" or "1985-06"; EXIF needs a full date,
  so missing month/day become 1. Without this PhotoPrism would date every
  photo to the day it was scanned.
* XMP ``dc:description`` and EXIF ``ImageDescription``: the caption
  (PhotoPrism's description).
* EXIF ``DateTimeDigitized`` and XMP ``xmp:CreateDate``: when it was scanned.
* EXIF ``XPKeywords``: tags shown in Windows Explorer.
* EXIF resolution: the scan resolution, so prints come out at the original size.
"""

from __future__ import annotations

import io
import struct
from xml.sax.saxutils import escape

import piexif

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


Date = tuple  # (year, month or None, day or None)


def _norm_date(date) -> Date | None:
    if date is None:
        return None
    if isinstance(date, int):
        return (date, None, None)
    return tuple(date)


def _xmp_packet(tags: list[str], date: Date | None, caption: str, scanned: str | None) -> bytes:
    items = "".join(f"<rdf:li>{escape(t)}</rdf:li>" for t in tags)
    subject = f"<dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>" if tags else ""
    extra = ""
    if date is not None:
        y, m, d = date
        partial = f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{d:02d}" if m and d else "")
        extra += (f"<photoshop:DateCreated>{partial}</photoshop:DateCreated>"
                  f"<exif:DateTimeOriginal>{y:04d}-{m or 1:02d}-{d or 1:02d}T00:00:00</exif:DateTimeOriginal>")
    if caption:
        extra += (f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{escape(caption)}</rdf:li>'
                  "</rdf:Alt></dc:description>")
    if scanned:
        extra += f"<xmp:CreateDate>{escape(scanned)}</xmp:CreateDate>"
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
        ' xmlns:exif="http://ns.adobe.com/exif/1.0/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        f"{subject}{extra}</rdf:Description></rdf:RDF></x:xmpmeta>"
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


def write_metadata(jpeg: bytes, tags: list[str], date, dpi: int | None = None,
                   caption: str = "", scanned: str | None = None) -> bytes:
    """Return ``jpeg`` with date, tags, caption and resolution embedded.

    ``date`` is a year or a (year, month, day) tuple with month/day
    optional; ``scanned`` an ISO timestamp of the scan."""
    date = _norm_date(date)
    try:
        exif = piexif.load(jpeg)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    zeroth, ex = exif.setdefault("0th", {}), exif.setdefault("Exif", {})

    if tags:
        zeroth[piexif.ImageIFD.XPKeywords] = tuple(";".join(tags).encode("utf-16-le") + b"\x00\x00")
    else:
        zeroth.pop(piexif.ImageIFD.XPKeywords, None)
    if date is not None:
        y, m, d = date
        ex[piexif.ExifIFD.DateTimeOriginal] = f"{y:04d}:{m or 1:02d}:{d or 1:02d} 00:00:00".encode()
    else:
        ex.pop(piexif.ExifIFD.DateTimeOriginal, None)
    if caption:
        zeroth[piexif.ImageIFD.ImageDescription] = caption.encode("utf-8")
    else:
        zeroth.pop(piexif.ImageIFD.ImageDescription, None)
    if scanned:
        ex[piexif.ExifIFD.DateTimeDigitized] = scanned.replace("-", ":").replace("T", " ")[:19].encode()
    if dpi:
        zeroth[piexif.ImageIFD.XResolution] = (int(dpi), 1)
        zeroth[piexif.ImageIFD.YResolution] = (int(dpi), 1)
        zeroth[piexif.ImageIFD.ResolutionUnit] = 2  # inches
    zeroth[piexif.ImageIFD.Software] = b"Image Scanner"
    ex.setdefault(piexif.ExifIFD.ExifVersion, b"0231")
    ex.setdefault(piexif.ExifIFD.ColorSpace, 1)  # sRGB

    buf = io.BytesIO()
    piexif.insert(piexif.dump(exif), jpeg, buf)
    return _set_xmp(buf.getvalue(), _xmp_packet(tags, date, caption, scanned))
