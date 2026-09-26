import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from scanner_app.gui import MainWindow
from scanner_app.library import Library
from scanner_app.scanner import DemoScanner, FolderScanner


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scanner_app", description="Scan old photos quickly.")
    ap.add_argument("--output", help="folder to save scans in (remembered for next time)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--demo", action="store_true", help="use fake scans instead of a scanner")
    src.add_argument("--from-folder", metavar="DIR",
                     help="'scan' existing images from a folder, e.g. to auto-crop old flatbed scans")
    args = ap.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Image Scanner")
    settings = QSettings("image-scanner", "image-scanner")
    folder = args.output or settings.value("output_folder") or str(Path.home() / "Pictures" / "Scans")
    settings.setValue("output_folder", folder)

    scanner = None
    if args.demo:
        scanner = DemoScanner()
    elif args.from_folder:
        scanner = FolderScanner(args.from_folder)

    win = MainWindow(Library(folder), scanner)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
