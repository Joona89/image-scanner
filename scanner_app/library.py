"""Scanned photos on disk, their year and tags, and the sorted output.

Every cut-out photo starts in the to-do queue. Once it has a year it can be
marked done, which writes it into the sorted folder by year::

    <library folder>/
        library.json        year, tags and state for every photo
        raw/                full, uncropped scans
        photos/             working copy of each cut-out photo
    <sorted folder>/        (default: <library folder>/Sorted)
        1985/1985_scan_20260926_131500_01.jpg
        Unknown year/...

Year and tags are embedded in every JPEG (see metadata.py), so PhotoPrism
and Windows pick them up. Editing a done photo updates its sorted copy,
moving it to another year folder when the year changes.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .metadata import write_metadata

UNKNOWN_YEAR = 0
MIN_YEAR, MAX_YEAR = 1800, 2100


@dataclass
class Photo:
    id: str
    file: str               # working copy, relative to the library folder
    scan: str               # raw scan it was cut from, relative
    created: str
    tags: list[str] = field(default_factory=list)
    year: int | None = None  # None = not set yet, 0 = explicitly unknown
    dpi: int | None = None
    exported: str | None = None  # absolute path of the sorted copy once done

    @property
    def done(self) -> bool:
        return self.exported is not None


def parse_year(text: str) -> int | None:
    """'1985' -> 1985, '85' -> 1985, '?' / 'unknown' -> 0. None if invalid."""
    t = text.strip().lower()
    if t in ("?", "unknown", "0", "tuntematon"):
        return UNKNOWN_YEAR
    if t.isdigit() and len(t) == 2:
        return 1900 + int(t)
    if t.isdigit() and len(t) == 4 and MIN_YEAR <= int(t) <= MAX_YEAR:
        return int(t)
    return None


def year_label(year: int | None) -> str:
    if year is None:
        return "no year"
    return "Unknown year" if year == UNKNOWN_YEAR else str(year)


def _imwrite(path: Path, img: np.ndarray, params=()) -> None:
    # cv2.imwrite cannot handle non-ASCII paths on Windows; encode instead.
    ok, buf = cv2.imencode(path.suffix, img, list(params))
    if not ok:
        raise IOError(f"Could not encode {path}")
    path.write_bytes(buf.tobytes())


class Library:
    def __init__(self, folder: str | Path, sorted_folder: str | Path | None = None, jpeg_quality: int = 95):
        self.folder = Path(folder)
        self.sorted_folder = Path(sorted_folder) if sorted_folder else self.folder / "Sorted"
        self.jpeg_quality = jpeg_quality
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
        known = {f.name for f in fields(Photo)}
        for d in data.get("photos", []):
            p = Photo(**{k: v for k, v in d.items() if k in known})
            if (self.folder / p.file).exists():
                self.photos[p.id] = p

    def save(self) -> None:
        with self._lock:
            data = {"version": 2, "photos": [asdict(p) for p in self.photos.values()]}
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

    def add_scan(self, scan: np.ndarray, crops: list[np.ndarray], tags: list[str] = (),
                 year: int | None = None, dpi: int | None = None) -> list[Photo]:
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
                photo = Photo(id=pid, file=rel, scan=raw_rel, created=now.isoformat(timespec="seconds"),
                              tags=sorted(set(tags), key=str.lower), year=year, dpi=dpi)
                self.photos[pid] = photo
                self._write_metadata(photo)
                added.append(photo)
            self.save()
            return added

    # -- queries --

    def todo(self) -> list[Photo]:
        return [p for p in self.ordered() if not p.done]

    def all_tags(self) -> list[str]:
        with self._lock:
            return sorted({t for p in self.photos.values() for t in p.tags}, key=str.lower)

    def all_years(self) -> list[int]:
        with self._lock:
            return sorted({p.year for p in self.photos.values() if p.year})

    def path(self, pid: str) -> Path:
        return self.folder / self.photos[pid].file

    def ordered(self) -> list[Photo]:
        with self._lock:
            return sorted(self.photos.values(), key=lambda p: p.id)

    # -- tags and year --

    def add_tag(self, ids, tag: str) -> None:
        self._change(ids, lambda p: setattr(p, "tags", sorted(set(p.tags) | {tag}, key=str.lower)))

    def remove_tag(self, ids, tag: str) -> None:
        self._change(ids, lambda p: setattr(p, "tags", sorted(set(p.tags) - {tag}, key=str.lower)))

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

    def set_year(self, ids, year: int | None) -> None:
        self._change(ids, lambda p: setattr(p, "year", year))

    def _change(self, ids, fn) -> None:
        with self._lock:
            for i in ids:
                p = self.photos[i]
                before = (list(p.tags), p.year)
                fn(p)
                if (p.tags, p.year) != before:
                    self._write_metadata(p)
                    if p.done:
                        self._export(p)
            self.save()

    def _write_metadata(self, photo: Photo) -> None:
        path = self.folder / photo.file
        year = photo.year or None  # unknown (0) gets no date
        path.write_bytes(write_metadata(path.read_bytes(), photo.tags, year, photo.dpi))

    # -- done / sorted output --

    def sorted_path(self, photo: Photo) -> Path:
        folder = self.sorted_folder / year_label(photo.year)
        prefix = f"{photo.year}_" if photo.year else ""
        return folder / f"{prefix}{photo.id}.jpg"

    def _export(self, photo: Photo) -> None:
        target = self.sorted_path(photo)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".jpg.tmp")
        tmp.write_bytes((self.folder / photo.file).read_bytes())
        os.replace(tmp, target)
        if photo.exported and Path(photo.exported) != target:
            Path(photo.exported).unlink(missing_ok=True)
        photo.exported = str(target)

    def finish(self, ids) -> tuple[list[str], list[str]]:
        """Write photos into the sorted folder. Photos without a year are
        skipped. Returns (done ids, ids that still need a year)."""
        done, missing = [], []
        with self._lock:
            for i in ids:
                p = self.photos[i]
                if p.year is None:
                    missing.append(i)
                    continue
                self._export(p)
                done.append(i)
            self.save()
        return done, missing

    def reopen(self, ids) -> None:
        """Move photos back to the to-do queue and remove their sorted copy."""
        with self._lock:
            for i in ids:
                p = self.photos[i]
                if p.exported:
                    Path(p.exported).unlink(missing_ok=True)
                    p.exported = None
            self.save()

    # -- other edits --

    def delete(self, ids) -> None:
        with self._lock:
            for i in list(ids):
                p = self.photos.pop(i, None)
                if p:
                    (self.folder / p.file).unlink(missing_ok=True)
                    if p.exported:
                        Path(p.exported).unlink(missing_ok=True)
            self.save()

    def rotate(self, ids, clockwise: bool = True) -> None:
        with self._lock:
            for i in ids:
                p = self.photos[i]
                path = self.folder / p.file
                img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE if clockwise else cv2.ROTATE_90_COUNTERCLOCKWISE)
                _imwrite(path, img, (cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality))
                self._write_metadata(p)
                if p.done:
                    self._export(p)
            self.save()
