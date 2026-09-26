"""Synthetic flatbed scans for tests and the demo scanner."""

from __future__ import annotations

import cv2
import numpy as np


def _photo(w: int, h: int, seed: int, white_border: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Smooth coloured gradient plus a few shapes, like a faded print.
    y, x = np.mgrid[0:h, 0:w]
    base = rng.integers(40, 200, size=3)
    img = np.stack([(base[i] + 50 * np.sin(x / (30 + 10 * i) + y / 45)).clip(0, 255)
                    for i in range(3)], axis=2).astype(np.uint8)
    for _ in range(6):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                   int(rng.integers(10, max(11, min(w, h) // 3))), c, -1)
    if white_border:
        img = cv2.copyMakeBorder(img, white_border, white_border, white_border, white_border,
                                 cv2.BORDER_CONSTANT, value=(245, 245, 245))
    return img


def _paste_rotated(canvas: np.ndarray, photo: np.ndarray, center: tuple[int, int], angle: float) -> None:
    h, w = photo.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    rot = cv2.warpAffine(photo, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    alpha = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, (nw, nh), flags=cv2.INTER_NEAREST)
    x0, y0 = center[0] - nw // 2, center[1] - nh // 2
    roi = canvas[y0:y0 + nh, x0:x0 + nw]
    roi[alpha > 0] = rot[alpha > 0]


def make_scan(specs: list[dict], size: tuple[int, int] = (1700, 2300),
              background: tuple[int, int, int] = (238, 238, 236), noise: float = 3.0,
              seed: int = 0) -> np.ndarray:
    """Build a fake scan. Each spec: w, h, cx, cy, angle, optional white_border."""
    rng = np.random.default_rng(seed)
    w, h = size
    canvas = np.empty((h, w, 3), np.uint8)
    canvas[:] = background
    for i, s in enumerate(specs):
        _paste_rotated(canvas, _photo(s["w"], s["h"], seed + i, s.get("white_border", 0)),
                       (s["cx"], s["cy"]), s.get("angle", 0.0))
    if noise:
        canvas = np.clip(canvas.astype(np.float32) + rng.normal(0, noise, canvas.shape), 0, 255).astype(np.uint8)
    return canvas


DEMO_LAYOUTS = [
    [dict(w=600, h=420, cx=450, cy=400, angle=3),
     dict(w=420, h=600, cx=1250, cy=450, angle=-5),
     dict(w=620, h=440, cx=500, cy=1200, angle=-2, white_border=25),
     dict(w=560, h=400, cx=1200, cy=1300, angle=8)],
    [dict(w=900, h=640, cx=850, cy=600, angle=1),
     dict(w=900, h=640, cx=850, cy=1550, angle=-1.5)],
    [dict(w=500, h=500, cx=500, cy=500, angle=12),
     dict(w=620, h=420, cx=1150, cy=1100, angle=-7),
     dict(w=420, h=300, cx=500, cy=1800, angle=0)],
]
