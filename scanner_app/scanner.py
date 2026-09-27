"""Scanner back ends.

``WiaScanner`` talks to real flatbed scanners through Windows Image
Acquisition (the same API Windows Fax and Scan uses), so any scanner with a
normal Windows driver works without vendor software.

``FolderScanner`` and ``DemoScanner`` stand in for a scanner during
development or when re-processing old scans: they return images from a
folder or generate synthetic scans.
"""

from __future__ import annotations

import itertools
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .synthetic import DEMO_LAYOUTS, make_scan


class ScanError(RuntimeError):
    pass


@dataclass
class ScanOptions:
    dpi: int = 300
    color: bool = True


ProgressFn = Callable[[float], None]


class Scanner:
    name = "scanner"
    # True when scan() calls ``progress`` with real values. Otherwise the
    # app shows an estimate based on earlier scans.
    reports_progress = False

    def scan(self, options: ScanOptions, progress: ProgressFn | None = None) -> np.ndarray:
        """Scan one page at ``options.dpi`` and return it as a BGR image."""
        raise NotImplementedError


# --- Windows Image Acquisition -------------------------------------------

WIA_DEVICE_TYPE_SCANNER = 1
WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

# Property ids from wiadef.h
WIA_DIP_DEV_ID = 2
WIA_DIP_DEV_NAME = 7
WIA_DPS_HORIZONTAL_BED_SIZE = 3074  # thousandths of an inch
WIA_DPS_VERTICAL_BED_SIZE = 3075
WIA_IPA_DATATYPE = 4103             # 0 = B/W, 2 = grey, 3 = colour
WIA_IPS_CUR_INTENT = 6146           # 1 = colour, 2 = greyscale
WIA_IPS_XRES = 6147
WIA_IPS_YRES = 6148
WIA_IPS_XPOS = 6149
WIA_IPS_YPOS = 6150
WIA_IPS_XEXTENT = 6151
WIA_IPS_YEXTENT = 6152


def _prop(props, prop_id: int):
    for p in props:
        if p.PropertyID == prop_id:
            return p
    return None


def _set_prop(props, prop_id: int, value) -> bool:
    p = _prop(props, prop_id)
    if p is None:
        return False
    try:
        p.Value = value
        return True
    except Exception:  # driver rejected the value; keep its default
        return False


def pick_resolution(prop, wanted: int) -> int:
    """The resolution to ask the driver for: ``wanted`` if supported, else the
    next higher supported value (the image is then scaled down), else the
    highest available."""
    if prop is None:
        return wanted
    try:
        sub = prop.SubType
        if sub == 2:  # list of values
            vec = prop.SubTypeValues  # WIA Vector, 1-based
            values = sorted(int(vec.Item(i)) for i in range(1, vec.Count + 1))
        elif sub == 1:  # range
            lo, hi, step = int(prop.SubTypeMin), int(prop.SubTypeMax), max(1, int(prop.SubTypeStep))
            if lo <= wanted <= hi and (wanted - lo) % step == 0:
                return wanted
            values = list(range(lo, hi + 1, step))
        else:
            return wanted
    except Exception:
        return wanted
    if wanted in values:
        return wanted
    higher = [v for v in values if v > wanted]
    return higher[0] if higher else values[-1]


def resample(img: np.ndarray, from_dpi: int, to_dpi: int) -> np.ndarray:
    if from_dpi == to_dpi:
        return img
    f = to_dpi / from_dpi
    interp = cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, None, fx=f, fy=f, interpolation=interp)


def list_wia_scanners() -> list[tuple[str, str]]:
    """(device id, display name) for each WIA scanner. Empty off Windows."""
    if sys.platform != "win32":
        return []
    import win32com.client

    dm = win32com.client.Dispatch("WIA.DeviceManager")
    out = []
    for info in dm.DeviceInfos:
        if info.Type != WIA_DEVICE_TYPE_SCANNER:
            continue
        out.append((info.DeviceID, _prop(info.Properties, WIA_DIP_DEV_NAME).Value))
    return out


class WiaScanner(Scanner):
    """Flatbed scanning through WIA automation (wiaaut.dll, part of Windows).

    Safe to call from a worker thread: COM is initialised per call.
    """

    def __init__(self, device_id: str | None = None):
        if sys.platform != "win32":
            raise ScanError("WIA scanning is only available on Windows.")
        self.device_id = device_id
        self.name = "WIA scanner"

    def _connect(self):
        import win32com.client

        dm = win32com.client.Dispatch("WIA.DeviceManager")
        for info in dm.DeviceInfos:
            if info.Type != WIA_DEVICE_TYPE_SCANNER:
                continue
            if self.device_id and info.DeviceID != self.device_id:
                continue
            self.name = _prop(info.Properties, WIA_DIP_DEV_NAME).Value
            return info.Connect()
        raise ScanError("No scanner found. Check that it is switched on and "
                        "shows up in Windows 'Printers & scanners'.")

    def scan(self, options: ScanOptions, progress: ProgressFn | None = None) -> np.ndarray:
        # WIA automation's Transfer() blocks without progress callbacks, so
        # the app estimates progress from earlier scans (reports_progress=False).
        import pythoncom

        pythoncom.CoInitialize()
        try:
            device = self._connect()
            item = device.Items[1]  # the flatbed
            props = item.Properties
            _set_prop(props, WIA_IPS_CUR_INTENT, 1 if options.color else 2)
            _set_prop(props, WIA_IPA_DATATYPE, 3 if options.color else 2)
            # Not every driver offers every resolution (450 is often missing):
            # scan at the next one up and scale down afterwards.
            dpi = pick_resolution(_prop(props, WIA_IPS_XRES), options.dpi)
            if not (_set_prop(props, WIA_IPS_XRES, dpi) and _set_prop(props, WIA_IPS_YRES, dpi)):
                raise ScanError(f"The scanner does not accept {dpi} dpi.")
            # Scan the whole bed: several photos are usually laid out on it.
            bed_w = _prop(device.Properties, WIA_DPS_HORIZONTAL_BED_SIZE)
            bed_h = _prop(device.Properties, WIA_DPS_VERTICAL_BED_SIZE)
            _set_prop(props, WIA_IPS_XPOS, 0)
            _set_prop(props, WIA_IPS_YPOS, 0)
            if bed_w is not None and bed_h is not None:
                _set_prop(props, WIA_IPS_XEXTENT, int(bed_w.Value * dpi / 1000))
                _set_prop(props, WIA_IPS_YEXTENT, int(bed_h.Value * dpi / 1000))

            try:
                wia_image = item.Transfer(WIA_FORMAT_PNG)
            except Exception as e:  # pywintypes.com_error
                raise ScanError(f"Scan failed: {e}") from e

            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            os.remove(tmp)  # WIA refuses to overwrite an existing file
            try:
                wia_image.SaveFile(tmp)
                img = cv2.imread(tmp, cv2.IMREAD_COLOR)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            if img is None:
                raise ScanError("Scanner returned an unreadable image.")
            return resample(img, dpi, options.dpi)
        finally:
            pythoncom.CoUninitialize()


# --- Stand-ins -----------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


class FolderScanner(Scanner):
    """Returns the images in a folder one by one, looping forever."""

    def __init__(self, folder: str | Path):
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if not files:
            raise ScanError(f"No images in {folder}")
        self._files = itertools.cycle(files)
        self.name = f"Folder: {Path(folder).name}"

    def scan(self, options: ScanOptions, progress: ProgressFn | None = None) -> np.ndarray:
        path = next(self._files)
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ScanError(f"Could not read {path}")
        return img


class DemoScanner(Scanner):
    """Generates fake scans with a few photos on them. Slow on purpose so
    you can try tagging while a scan runs."""

    name = "Demo scanner"

    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self._n = 0

    reports_progress = True

    def scan(self, options: ScanOptions, progress: ProgressFn | None = None) -> np.ndarray:
        layout = DEMO_LAYOUTS[self._n % len(DEMO_LAYOUTS)]
        img = make_scan(layout, seed=self._n)
        self._n += 1
        steps = 20
        for i in range(1, steps + 1):
            time.sleep(self.delay / steps)
            if progress:
                progress(i / steps)
        return img if options.color else cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def default_scanner() -> Scanner:
    if sys.platform == "win32":
        try:
            if list_wia_scanners():
                return WiaScanner()
        except Exception:
            pass
    return DemoScanner()
