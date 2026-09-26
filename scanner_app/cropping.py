"""Find and cut out separate photos from a single flatbed scan.

The scanner lid gives a fairly uniform background (usually white or light
grey). We estimate that background colour from the outer border of the scan,
mark every pixel that differs from it (plus strong edges, to catch photos
with pale borders), merge the result into solid blobs and fit a rotated
rectangle to each blob. Each rectangle is then warped upright.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CropSettings:
    # Blobs smaller than this fraction of the scan area are ignored (dust, lint).
    min_area_fraction: float = 0.01
    # Pixels are "not background" when their colour distance exceeds this.
    # None = pick automatically with Otsu.
    threshold: float | None = None
    # Pixels trimmed from every side of a found photo to drop the
    # background halo left by the rotated fit.
    inset_px: int = 3
    # Width of the border strip (fraction of the short side) used to
    # estimate the background colour.
    border_fraction: float = 0.02


@dataclass
class DetectedPhoto:
    image: np.ndarray
    # Rotated rectangle ((cx, cy), (w, h), angle) in scan coordinates.
    rect: tuple
    # Four corners in scan coordinates (for drawing an overlay).
    box: np.ndarray


def _background_color(img: np.ndarray, border_fraction: float) -> np.ndarray:
    h, w = img.shape[:2]
    b = max(2, int(min(h, w) * border_fraction))
    strips = [img[:b].reshape(-1, 3), img[-b:].reshape(-1, 3),
              img[:, :b].reshape(-1, 3), img[:, -b:].reshape(-1, 3)]
    return np.median(np.concatenate(strips), axis=0)


def _foreground_mask(img: np.ndarray, settings: CropSettings) -> np.ndarray:
    small_side = min(img.shape[:2])
    blurred = cv2.GaussianBlur(img, (5, 5), 0)

    bg = _background_color(blurred, settings.border_fraction)
    dist = np.linalg.norm(blurred.astype(np.float32) - bg.astype(np.float32), axis=2)
    dist = np.clip(dist, 0, 255).astype(np.uint8)
    if settings.threshold is None:
        otsu, _ = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Otsu can land very low on an almost empty scan; keep a floor so
        # scanner noise is never picked up as a photo.
        thr = max(float(otsu), 18.0)
    else:
        thr = settings.threshold
    mask = (dist > thr).astype(np.uint8) * 255

    # Edges catch photo borders that are nearly the same colour as the lid.
    gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    mask = cv2.bitwise_or(mask, edges)

    # Remove speckles, then close gaps inside photos (light skies etc.).
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    k = max(5, small_side // 150) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))

    # Fill each outer contour so holes inside a photo don't matter.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    # Shave off edge-dilation growth so the fit hugs the real photo.
    return cv2.erode(filled, np.ones((3, 3), np.uint8))


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _warp(img: np.ndarray, box: np.ndarray, inset: int) -> np.ndarray:
    tl, tr, br, bl = _order_corners(box)
    w = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    h = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl]), dst)
    out = cv2.warpPerspective(img, m, (w, h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    if inset > 0 and w > 4 * inset and h > 4 * inset:
        out = out[inset:h - inset, inset:w - inset]
    return out


def detect_photos(img: np.ndarray, settings: CropSettings | None = None) -> list[DetectedPhoto]:
    """Return every separate photo found on the scan, top-to-bottom, left-to-right.

    ``img`` is a BGR image as loaded by OpenCV. When nothing that looks like
    a photo is found the list is empty; callers usually fall back to keeping
    the whole scan.
    """
    settings = settings or CropSettings()
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    mask = _foreground_mask(img, settings)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = settings.min_area_fraction * img.shape[0] * img.shape[1]

    found: list[DetectedPhoto] = []
    for c in contours:
        if cv2.contourArea(c) < min_area:
            continue
        rect = cv2.minAreaRect(c)
        (_, _), (rw, rh), _ = rect
        if rw < 10 or rh < 10:
            continue
        box = cv2.boxPoints(rect)
        found.append(DetectedPhoto(_warp(img, box, settings.inset_px), rect, box))

    # Reading order: group into rows by centre y, then sort by x.
    row_h = img.shape[0] / 8
    found.sort(key=lambda p: (int(p.rect[0][1] // row_h), p.rect[0][0]))
    return found


def crop_or_whole(img: np.ndarray, settings: CropSettings | None = None) -> list[np.ndarray]:
    """Detected photos, or the whole scan when none were found."""
    photos = detect_photos(img, settings)
    return [p.image for p in photos] if photos else [img]
