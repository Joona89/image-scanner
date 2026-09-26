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

import cv2
import numpy as np

from .synthetic import DEMO_LAYOUTS, make_scan


class ScanError(RuntimeError):
    pass


@dataclass
class ScanOptions:
    dpi: int = 300
    color: bool = True


class Scanner:
    name = "scanner"

    def scan(self, options: ScanOptions) -> np.ndarray:
        """Scan one page and return it as a BGR image."""
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

    def scan(self, options: ScanOptions) -> np.ndarray:
        import pythoncom

        pythoncom.CoInitialize()
        try:
            device = self._connect()
            item = device.Items[1]  # the flatbed
            props = item.Properties
            _set_prop(props, WIA_IPS_CUR_INTENT, 1 if options.color else 2)
            _set_prop(props, WIA_IPA_DATATYPE, 3 if options.color else 2)
            _set_prop(props, WIA_IPS_XRES, options.dpi)
            _set_prop(props, WIA_IPS_YRES, options.dpi)
            # Scan the whole bed: several photos are usually laid out on it.
            bed_w = _prop(device.Properties, WIA_DPS_HORIZONTAL_BED_SIZE)
            bed_h = _prop(device.Properties, WIA_DPS_VERTICAL_BED_SIZE)
            _set_prop(props, WIA_IPS_XPOS, 0)
            _set_prop(props, WIA_IPS_YPOS, 0)
            if bed_w is not None and bed_h is not None:
                _set_prop(props, WIA_IPS_XEXTENT, int(bed_w.Value * options.dpi / 1000))
                _set_prop(props, WIA_IPS_YEXTENT, int(bed_h.Value * options.dpi / 1000))

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
            return img
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

    def scan(self, options: ScanOptions) -> np.ndarray:
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

    def scan(self, options: ScanOptions) -> np.ndarray:
        layout = DEMO_LAYOUTS[self._n % len(DEMO_LAYOUTS)]
        img = make_scan(layout, seed=self._n)
        self._n += 1
        time.sleep(self.delay)
        return img if options.color else cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def default_scanner() -> Scanner:
    if sys.platform == "win32":
        try:
            if list_wia_scanners():
                return WiaScanner()
        except Exception:
            pass
    return DemoScanner()
