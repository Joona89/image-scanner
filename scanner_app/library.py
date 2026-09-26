"""Scanned photos on disk and their tags.

Layout of an output folder::

    <folder>/
        library.json        tags and metadata for every photo
        raw/                full, uncropped scans
        photos/             one JPEG per cut-out photo

Tags live in library.json and are also written into each JPEG as Windows
keywords (EXIF XPKeywords), so Explorer and the Photos app can search them.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    import piexif
except ImportError:  # tags still work, just not embedded in the files
    piexif = None


@dataclass
class Photo:
    id: str
    file: str               # relative to the library folder
    scan: str               # raw scan it was cut from, relative
    created: str
    tags: list[str] = field(default_factory=list)


def _imwrite(path: Path, img: np.ndarray, params=()) -> None:
    # cv2.imwrite cannot handle non-ASCII paths on Windows; encode instead.
    ok, buf = cv2.imencode(path.suffix, img, list(params))
    if not ok:
        raise IOError(f"Could not encode {path}")
    path.write_bytes(buf.tobytes())


class Library:
    def __init__(self, folder: str | Path, jpeg_quality: int = 95, embed_tags: bool = True):
        self.folder = Path(folder)
        self.jpeg_quality = jpeg_quality
        self.embed_tags = embed_tags and piexif is not None
        self.photos: dict[str, Photo] = {}
        self._lock = threading.RLock()
        (self.folder / "raw").mkdir(parents=True, exist_ok=True)
        (self.folder / "photos").mkdir(parents=True, exist_ok=True)
        self._load()

    # -- persistence --

    @property
    def index_path(self) -> Path:
        return self.folder / "library.json"

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        for d in data.get("photos", []):
            p = Photo(**d)
            if (self.folder / p.file).exists():
                self.photos[p.id] = p

    def save(self) -> None:
        with self._lock:
            data = {"version": 1, "photos": [asdict(p) for p in self.photos.values()]}
            tmp = self.index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.index_path)

    # -- adding --

    def _unique_stem(self, stem: str) -> str:
        n, candidate = 1, stem
        while (self.folder / "raw" / f"{candidate}.png").exists():
            n += 1
            candidate = f"{stem}_{n}"
        return candidate

    def add_scan(self, scan: np.ndarray, crops: list[np.ndarray], tags: list[str] = ()) -> list[Photo]:
        """Store a raw scan and its cut-out photos. Returns the new photos."""
        with self._lock:
            now = datetime.now()
            stem = self._unique_stem(now.strftime("scan_%Y%m%d_%H%M%S"))
            raw_rel = f"raw/{stem}.png"
            _imwrite(self.folder / raw_rel, scan)

            added = []
            for i, crop in enumerate(crops, 1):
                pid = f"{stem}_{i:02d}"
                rel = f"photos/{pid}.jpg"
                _imwrite(self.folder / rel, crop, (cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality))
                photo = Photo(id=pid, file=rel, scan=raw_rel,
                              created=now.isoformat(timespec="seconds"), tags=sorted(set(tags)))
                self.photos[pid] = photo
                if photo.tags:
                    self._embed(photo)
                added.append(photo)
            self.save()
            return added

    # -- tags --

    def all_tags(self) -> list[str]:
        with self._lock:
            return sorted({t for p in self.photos.values() for t in p.tags}, key=str.lower)

    def add_tag(self, ids, tag: str) -> None:
        self._change(ids, lambda tags: tags | {tag})

    def remove_tag(self, ids, tag: str) -> None:
        self._change(ids, lambda tags: tags - {tag})

    def toggle_tag(self, ids, tag: str) -> bool:
        """Add the tag to all ``ids`` unless every one already has it, in
        which case remove it. Returns True when the tag ended up added."""
        ids = list(ids)
        with self._lock:
            have_all = all(tag in self.photos[i].tags for i in ids)
        if have_all:
            self.remove_tag(ids, tag)
            return False
        self.add_tag(ids, tag)
        return True

    def _change(self, ids, fn) -> None:
        with self._lock:
            for i in ids:
                p = self.photos[i]
                new = sorted(fn(set(p.tags)), key=str.lower)
                if new != p.tags:
                    p.tags = new
                    self._embed(p)
            self.save()

    def _embed(self, photo: Photo) -> None:
        if not self.embed_tags:
            return
        path = str(self.folder / photo.file)
        try:
            exif = piexif.load(path)
            exif["0th"][piexif.ImageIFD.XPKeywords] = tuple(
                ";".join(photo.tags).encode("utf-16-le") + b"\x00\x00")
            piexif.insert(piexif.dump(exif), path)
        except Exception:
            # Tags are safe in library.json even if the file can't be updated.
            pass

    # -- other edits --

    def delete(self, ids) -> None:
        with self._lock:
            for i in list(ids):
                p = self.photos.pop(i, None)
                if p:
                    (self.folder / p.file).unlink(missing_ok=True)
            self.save()

    def rotate(self, ids, clockwise: bool = True) -> None:
        with self._lock:
            for i in ids:
                p = self.photos[i]
                path = self.folder / p.file
                img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE if clockwise else cv2.ROTATE_90_COUNTERCLOCKWISE)
                _imwrite(path, img, (cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality))
                if p.tags:
                    self._embed(p)

    def path(self, pid: str) -> Path:
        return self.folder / self.photos[pid].file

    def ordered(self) -> list[Photo]:
        with self._lock:
            return sorted(self.photos.values(), key=lambda p: p.id)
