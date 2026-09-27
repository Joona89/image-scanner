"""Main window: scan with one key, keep tagging while the scanner runs."""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .cropping import crop_or_whole
from .library import Library, parse_year, year_label
from .scanner import DemoScanner, Scanner, ScanOptions, WiaScanner, list_wia_scanners

THUMB = 180
ID_ROLE = Qt.UserRole
TAG_ROLE = Qt.UserRole + 1

HELP = """<b>Keys</b><br>
<b>Space</b> or <b>F5</b> &nbsp; scan (keep tagging while it runs)<br>
<b>1</b>–<b>9</b> &nbsp; toggle quick tag on selected photos<br>
<b>T</b> &nbsp; type a tag for the selected photos (Enter adds it)<br>
<b>Y</b> &nbsp; type the year for the selected photos (85 = 1985, ? = unknown)<br>
<b>D</b> &nbsp; done: write selected photos to their year folder<br>
<b>Shift+D</b> &nbsp; move selected photos back to the to-do list<br>
<b>N</b> &nbsp; select the photos from the latest scan<br>
<b>Ctrl+A</b> / <b>Esc</b> &nbsp; select all / none<br>
<b>R</b> / <b>Shift+R</b> &nbsp; rotate right / left<br>
<b>Del</b> &nbsp; delete selected photos<br>
<b>Enter</b> &nbsp; open photo in the default viewer"""


def split_tags(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class ScanWorker(QObject):
    """Runs scan + crop + save off the UI thread."""

    done = Signal(list)       # new Photo objects
    failed = Signal(str)
    progress = Signal(float)  # 0..1, only from scanners that report it
    scanned = Signal(float)   # seconds the scanner took
    stage = Signal(str)

    def __init__(self, scanner: Scanner, library: Library, options: ScanOptions,
                 auto_crop: bool, tags: list[str], year: int | None):
        super().__init__()
        self.scanner, self.library, self.options = scanner, library, options
        self.auto_crop, self.tags, self.year = auto_crop, tags, year

    @Slot()
    def run(self):
        try:
            t0 = time.monotonic()
            scan = self.scanner.scan(self.options, self.progress.emit)
            self.scanned.emit(time.monotonic() - t0)
            self.stage.emit("Cutting out photos…")
            crops = crop_or_whole(scan) if self.auto_crop else [scan]
            self.done.emit(self.library.add_scan(scan, crops, self.tags, self.year, self.options.dpi))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e) or type(e).__name__)


def load_thumb(path: Path, size: int = THUMB) -> QPixmap:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    s = reader.size()
    if s.isValid() and max(s.width(), s.height()) > size:
        reader.setScaledSize(s.scaled(size, size, Qt.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


class MainWindow(QMainWindow):
    def __init__(self, library: Library, scanner: Scanner | None = None, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("image-scanner", "image-scanner")
        self.library = library
        self.scanner = scanner
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._last_scan_ids: list[str] = []
        self._scan_started = 0.0
        self._expected_seconds: float | None = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(200)
        self._progress_timer.timeout.connect(self._estimate_progress)
        self.setWindowTitle("Image Scanner")
        self.resize(1300, 850)

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()
        self._reload_grid()
        self._update_status()

    # ---------------------------------------------------------------- UI

    def _build_toolbar(self):
        tb = QToolBar("Scan")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.scan_action = QAction("Scan (Space)", self)
        self.scan_action.triggered.connect(self.start_scan)
        tb.addAction(self.scan_action)
        tb.addSeparator()

        self.scanner_combo = QComboBox()
        self.scanner_combo.setMinimumWidth(180)
        self._fill_scanners()
        self.scanner_combo.currentIndexChanged.connect(self._scanner_changed)
        tb.addWidget(QLabel(" Scanner "))
        tb.addWidget(self.scanner_combo)

        self.dpi_combo = QComboBox()
        for dpi in (150, 300, 450, 600, 1200):
            self.dpi_combo.addItem(f"{dpi} dpi", dpi)
        self.dpi_combo.setCurrentIndex(max(0, self.dpi_combo.findData(int(self.settings.value("dpi", 300)))))
        self.dpi_combo.currentIndexChanged.connect(lambda: self.settings.setValue("dpi", self.dpi_combo.currentData()))
        tb.addWidget(self.dpi_combo)

        self.color_check = QCheckBox("Colour")
        self.color_check.setChecked(self.settings.value("color", True, bool))
        self.color_check.toggled.connect(lambda v: self.settings.setValue("color", v))
        tb.addWidget(self.color_check)

        self.crop_check = QCheckBox("Auto-crop")
        self.crop_check.setToolTip("Cut each photo on the scanner glass into its own file")
        self.crop_check.setChecked(self.settings.value("auto_crop", True, bool))
        self.crop_check.toggled.connect(lambda v: self.settings.setValue("auto_crop", v))
        tb.addWidget(self.crop_check)

        # Second row: what new scans get, and where photos go.
        self.addToolBarBreak()
        tb = QToolBar("Output")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" Year for new scans "))
        self.new_year_edit = QLineEdit(self.settings.value("new_scan_year", ""))
        self.new_year_edit.setPlaceholderText("optional")
        self.new_year_edit.setMaximumWidth(80)
        self.new_year_edit.textChanged.connect(lambda t: self.settings.setValue("new_scan_year", t))
        tb.addWidget(self.new_year_edit)

        tb.addWidget(QLabel(" Tags for new scans "))
        self.new_tags_edit = QLineEdit(self.settings.value("new_scan_tags", ""))
        self.new_tags_edit.setPlaceholderText("e.g. album 3, 1980s")
        self.new_tags_edit.setMinimumWidth(200)
        self.new_tags_edit.textChanged.connect(lambda t: self.settings.setValue("new_scan_tags", t))
        tb.addWidget(self.new_tags_edit)
        tb.addSeparator()

        folder_action = QAction("Scan folder…", self)
        folder_action.setToolTip("Where raw scans and the to-do list are kept")
        folder_action.triggered.connect(self.choose_folder)
        tb.addAction(folder_action)
        sorted_action = QAction("Sorted folder…", self)
        sorted_action.setToolTip("Where finished photos are written, one folder per year")
        sorted_action.triggered.connect(self.choose_sorted_folder)
        tb.addAction(sorted_action)
        open_action = QAction("Open sorted", self)
        open_action.triggered.connect(self._open_sorted)
        tb.addAction(open_action)

    def _fill_scanners(self):
        self.scanner_combo.blockSignals(True)
        self.scanner_combo.clear()
        if self.scanner is not None and not isinstance(self.scanner, (WiaScanner, DemoScanner)):
            self.scanner_combo.addItem(self.scanner.name, ("custom", None))
        try:
            for dev_id, name in list_wia_scanners():
                self.scanner_combo.addItem(name, ("wia", dev_id))
        except Exception as e:
            self.statusBar().showMessage(f"Could not list scanners: {e}")
        self.scanner_combo.addItem("Demo (fake scans)", ("demo", None))
        if isinstance(self.scanner, DemoScanner):
            self.scanner_combo.setCurrentIndex(self.scanner_combo.count() - 1)
        self.scanner_combo.blockSignals(False)
        if self.scanner is None:
            self._scanner_changed()

    def _scanner_changed(self):
        kind, dev_id = self.scanner_combo.currentData()
        if kind == "wia":
            self.scanner = WiaScanner(dev_id)
        elif kind == "demo":
            self.scanner = DemoScanner()

    def _build_body(self):
        splitter = QSplitter()

        # Left: filter + photo grid
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.view_combo = QComboBox()
        self.view_combo.addItem("To do", "todo")
        self.view_combo.addItem("Done", "done")
        self.view_combo.addItem("All", "all")
        self.view_combo.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.view_combo)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by tag or year…  ('untagged' or 'no year' also work)")
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, 1)
        lv.addLayout(top)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 24, THUMB + 60))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.itemActivated.connect(self._open_item)
        lv.addWidget(self.grid)
        splitter.addWidget(left)

        # Right: preview + tagging
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 4, 4, 4)
        self.preview = QLabel("No photo selected")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(260)
        self.preview.setStyleSheet("background:#222;color:#aaa;")
        rv.addWidget(self.preview, 3)

        self.sel_label = QLabel()
        rv.addWidget(self.sel_label)

        year_row = QHBoxLayout()
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText("Year (Y), required: 1985, 85 or ? for unknown")
        self.year_edit.returnPressed.connect(self._year_entered)
        self.year_completer = QCompleter([])
        self.year_edit.setCompleter(self.year_completer)
        year_row.addWidget(self.year_edit, 1)
        self.done_button = QPushButton("Done → year folder (D)")
        self.done_button.clicked.connect(self.finish_selected)
        year_row.addWidget(self.done_button)
        rv.addLayout(year_row)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Add tag to selected (T), Enter to apply, commas for several")
        self.tag_edit.returnPressed.connect(self._tag_entered)
        self.completer = QCompleter([])
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.tag_edit.setCompleter(self.completer)
        rv.addWidget(self.tag_edit)

        rv.addWidget(QLabel("Quick tags (keys 1–9). Click to toggle on the selection; right-click to forget."))
        self.quick_list = QListWidget()
        self.quick_list.itemClicked.connect(self._quick_clicked)
        self.quick_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.quick_list.customContextMenuRequested.connect(self._quick_forget)
        rv.addWidget(self.quick_list, 2)

        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color:#666;")
        rv.addWidget(help_label)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([850, 400])
        self.setCentralWidget(splitter)

        self.quick_tags: list[str] = list(self.settings.value("quick_tags", []) or [])
        if isinstance(self.quick_tags, str):
            self.quick_tags = [self.quick_tags]
        self._refresh_tags()

    def _build_shortcuts(self):
        def sc(keys, fn):
            for k in keys:
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WindowShortcut)
                s.activated.connect(fn)

        sc(["Space", "F5"], self.start_scan)
        for n in range(1, 10):
            sc([str(n)], lambda n=n: self.toggle_quick(n - 1))
        sc(["T"], self._focus_tag_edit)
        sc(["Y"], self._focus_year_edit)
        sc(["D"], self.finish_selected)
        sc(["Shift+D"], self.reopen_selected)
        sc(["N"], self.select_last_scan)
        sc(["Esc"], self._escape)
        sc(["R"], lambda: self.rotate(True))
        sc(["Shift+R"], lambda: self.rotate(False))
        sc(["Del"], self.delete_selected)

    # ------------------------------------------------------------- grid

    def _add_item(self, photo) -> QListWidgetItem:
        item = QListWidgetItem(QIcon(load_thumb(self.library.path(photo.id))), "")
        item.setData(ID_ROLE, photo.id)
        self._update_item_text(item)
        self.grid.addItem(item)
        return item

    def _update_item_text(self, item: QListWidgetItem):
        p = self.library.photos[item.data(ID_ROLE)]
        mark = "✓ " if p.done else ""
        item.setText(f"{mark}{year_label(p.year)} · {', '.join(p.tags) or '—'}")
        where = f"\nSaved to {p.exported}" if p.done else "\nTo do"
        item.setToolTip(f"{p.id}\nYear: {year_label(p.year)}\nTags: {', '.join(p.tags) or 'none'}{where}")

    def _reload_grid(self):
        self.grid.clear()
        for p in self.library.ordered():
            self._add_item(p)
        self._apply_filter()

    def _items(self):
        return [self.grid.item(i) for i in range(self.grid.count())]

    def selected_ids(self) -> list[str]:
        return [it.data(ID_ROLE) for it in self.grid.selectedItems()]

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        view = self.view_combo.currentData()
        for it in self._items():
            p = self.library.photos[it.data(ID_ROLE)]
            if (view == "todo" and p.done) or (view == "done" and not p.done):
                hide = True
            elif not text:
                hide = False
            elif text == "untagged":
                hide = bool(p.tags)
            elif text == "no year":
                hide = p.year is not None
            else:
                hide = not (any(text in t.lower() for t in p.tags) or text in year_label(p.year).lower())
            it.setHidden(hide)
            if hide and it.isSelected():
                it.setSelected(False)
        todo = sum(not p.done for p in self.library.photos.values())
        self.view_combo.setItemText(0, f"To do ({todo})")
        self.view_combo.setItemText(1, f"Done ({len(self.library.photos) - todo})")

    def _selection_changed(self):
        ids = self.selected_ids()
        self.sel_label.setText(f"{len(ids)} selected" if ids else "Nothing selected")
        self._refresh_quick_states()
        self._show_preview()

    def _show_preview(self):
        cur = self.grid.currentItem()
        if cur is None or not cur.isSelected():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("No photo selected")
            return
        pm = QPixmap(str(self.library.path(cur.data(ID_ROLE))))
        self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._show_preview)

    def _open_item(self, item):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.path(item.data(ID_ROLE)))))

    # ---------------------------------------------------------- tagging

    def _refresh_tags(self):
        known = set(self.quick_tags)
        self.completer.model().setStringList(sorted(set(self.library.all_tags()) | known, key=str.lower))
        self.year_completer.model().setStringList([str(y) for y in self.library.all_years()])
        self.quick_list.clear()
        for i, t in enumerate(self.quick_tags):
            label = f"{i + 1}  {t}" if i < 9 else f"    {t}"
            it = QListWidgetItem(label)
            it.setData(TAG_ROLE, t)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            self.quick_list.addItem(it)
        self._refresh_quick_states()

    def _refresh_quick_states(self):
        ids = self.selected_ids()
        for i in range(self.quick_list.count()):
            it = self.quick_list.item(i)
            t = it.data(TAG_ROLE)
            n = sum(1 for pid in ids if t in self.library.photos[pid].tags)
            state = Qt.Unchecked if n == 0 else Qt.Checked if n == len(ids) else Qt.PartiallyChecked
            it.setCheckState(state)

    def _remember_tag(self, tag: str):
        if tag not in self.quick_tags:
            self.quick_tags.append(tag)
            self.settings.setValue("quick_tags", self.quick_tags)

    def _after_tag_change(self, ids):
        by_id = {it.data(ID_ROLE): it for it in self._items()}
        for pid in ids:
            self._update_item_text(by_id[pid])
        self._refresh_tags()
        self._apply_filter()

    def _tag_entered(self):
        tags = split_tags(self.tag_edit.text())
        ids = self.selected_ids()
        self.tag_edit.clear()
        if not tags:
            self.grid.setFocus()
            return
        for t in tags:
            self._remember_tag(t)
            if ids:
                self.library.add_tag(ids, t)
        self._after_tag_change(ids)
        if not ids:
            self.statusBar().showMessage("Saved as quick tag. Select photos to apply it.", 4000)
        self.grid.setFocus()

    def toggle_quick(self, index: int):
        ids = self.selected_ids()
        if index >= len(self.quick_tags) or not ids:
            return
        tag = self.quick_tags[index]
        added = self.library.toggle_tag(ids, tag)
        self.statusBar().showMessage(f"{'Added' if added else 'Removed'} '{tag}' on {len(ids)} photo(s)", 3000)
        self._after_tag_change(ids)

    def _quick_clicked(self, item):
        self.toggle_quick(self.quick_list.row(item))
        self.grid.setFocus()

    def _quick_forget(self, pos):
        item = self.quick_list.itemAt(pos)
        if item:
            self.quick_tags.remove(item.data(TAG_ROLE))
            self.settings.setValue("quick_tags", self.quick_tags)
            self._refresh_tags()

    def _year_entered(self):
        text = self.year_edit.text()
        ids = self.selected_ids()
        if not text.strip():
            self.grid.setFocus()
            return
        year = parse_year(text)
        if year is None:
            self.statusBar().showMessage(f"'{text}' is not a year. Use e.g. 1985, 85, or ? for unknown.", 5000)
            return
        self.year_edit.clear()
        if not ids:
            self.statusBar().showMessage("Select photos first.", 3000)
        else:
            self.library.set_year(ids, year)
            self.statusBar().showMessage(f"Year {year_label(year)} on {len(ids)} photo(s). Press D when done.", 4000)
            self._after_tag_change(ids)
        self.grid.setFocus()

    def _focus_year_edit(self):
        self.year_edit.setFocus()
        self.year_edit.selectAll()

    def finish_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        done, missing = self.library.finish(ids)
        self._after_tag_change(ids)
        if missing:
            # Keep the ones that still need a year selected, ready for Y.
            for it in self._items():
                it.setSelected(it.data(ID_ROLE) in missing)
            self.statusBar().showMessage(
                f"{len(done)} photo(s) saved. {len(missing)} still need a year: press Y to set it.", 6000)
        else:
            self.statusBar().showMessage(f"{len(done)} photo(s) saved to {self.library.sorted_folder}", 4000)
        self._update_status()

    def reopen_selected(self):
        ids = [i for i in self.selected_ids() if self.library.photos[i].done]
        if not ids:
            return
        self.library.reopen(ids)
        self._after_tag_change(ids)
        self.statusBar().showMessage(f"{len(ids)} photo(s) moved back to the to-do list.", 4000)
        self._update_status()

    def _focus_tag_edit(self):
        self.tag_edit.setFocus()
        self.tag_edit.selectAll()

    def _escape(self):
        if self.tag_edit.hasFocus() or self.filter_edit.hasFocus() or self.year_edit.hasFocus():
            self.grid.setFocus()
        else:
            self.grid.clearSelection()

    def select_last_scan(self):
        if not self._last_scan_ids:
            return
        wanted = set(self._last_scan_ids)
        self.grid.clearSelection()
        last = None
        for it in self._items():
            if it.data(ID_ROLE) in wanted:
                it.setSelected(True)
                last = it
        if last:
            self.grid.setCurrentItem(last, QItemSelectionModel.NoUpdate)
            self.grid.scrollToItem(last)
        self.grid.setFocus()

    # ------------------------------------------------------------ edits

    def rotate(self, clockwise: bool):
        ids = self.selected_ids()
        if not ids:
            return
        self.library.rotate(ids, clockwise)
        for it in self.grid.selectedItems():
            it.setIcon(QIcon(load_thumb(self.library.path(it.data(ID_ROLE)))))
        self._show_preview()

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, "Delete photos", f"Delete {len(ids)} photo(s)? "
                                "The raw scans are kept.") != QMessageBox.Yes:
            return
        for it in self.grid.selectedItems():
            self.grid.takeItem(self.grid.row(it))
        self.library.delete(ids)
        self._refresh_tags()
        self._update_status()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Where should scans be saved?", str(self.library.folder))
        if not folder or self.is_scanning():
            return
        self.library = Library(folder, self.settings.value("sorted_folder") or None)
        self.settings.setValue("output_folder", folder)
        self._last_scan_ids = []
        self._reload_grid()
        self._refresh_tags()
        self._update_status()

    def choose_sorted_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Where should finished photos go (one folder per year)?",
                                                  str(self.library.sorted_folder))
        if not folder:
            return
        self.library.sorted_folder = Path(folder)
        self.settings.setValue("sorted_folder", folder)
        self._update_status()
        self.statusBar().showMessage("Photos marked done from now on go to the new folder.", 5000)

    def _open_sorted(self):
        self.library.sorted_folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.sorted_folder)))

    # ------------------------------------------------------------ scans

    def is_scanning(self) -> bool:
        return self._thread is not None

    def start_scan(self):
        if self.is_scanning():
            self.statusBar().showMessage("Already scanning. Keep tagging, it will show up shortly.", 3000)
            return
        if self.scanner is None:
            QMessageBox.warning(self, "No scanner", "No scanner selected.")
            return
        options = ScanOptions(dpi=self.dpi_combo.currentData(), color=self.color_check.isChecked())
        tags = split_tags(self.new_tags_edit.text())
        year = None
        if self.new_year_edit.text().strip():
            year = parse_year(self.new_year_edit.text())
            if year is None:
                QMessageBox.warning(self, "Year for new scans", "That is not a year. Use e.g. 1985 or leave it empty.")
                return
        for t in tags:
            self._remember_tag(t)

        self._thread = QThread(self)
        self._worker = ScanWorker(self.scanner, self.library, options, self.crop_check.isChecked(), tags, year)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._real_progress)
        self._worker.scanned.connect(self._remember_scan_time)
        self._worker.stage.connect(self._scan_stage)
        self._worker.done.connect(self._scan_done)
        self._worker.failed.connect(self._scan_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._scan_thread_finished)
        self.scan_action.setEnabled(False)
        self.scan_action.setText("Scanning…")
        self._start_progress(options)
        self._update_status()
        self._thread.start()

    # Progress: real values when the scanner reports them, otherwise an
    # estimate from how long the last scan with the same settings took.

    def _timing_key(self, options: ScanOptions) -> str:
        return f"scan_seconds/{self.scanner.name}/{options.dpi}/{'color' if options.color else 'gray'}"

    def _start_progress(self, options: ScanOptions):
        self._scan_started = time.monotonic()
        self._timing = self._timing_key(options)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        if self.scanner.reports_progress:
            self._expected_seconds = None
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setFormat("Scanning %p%")
            return
        expected = self.settings.value(self._timing, None)
        self._expected_seconds = float(expected) if expected else None
        if self._expected_seconds:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setFormat("Scanning ~%p% (estimate)")
            self._progress_timer.start()
        else:
            self.progress_bar.setRange(0, 0)  # busy animation until we know how long it takes
            self.progress_bar.setFormat("Scanning…")

    def _estimate_progress(self):
        elapsed = time.monotonic() - self._scan_started
        self.progress_bar.setValue(int(min(0.97, elapsed / self._expected_seconds) * 100))

    def _real_progress(self, fraction: float):
        self.progress_bar.setValue(int(fraction * 100))

    def _remember_scan_time(self, seconds: float):
        self.settings.setValue(self._timing, max(0.1, round(seconds, 1)))
        self._progress_timer.stop()

    def _scan_stage(self, text: str):
        self._progress_timer.stop()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(text)

    def _scan_thread_finished(self):
        self._thread.deleteLater()
        self._worker.deleteLater()
        self._thread = self._worker = None
        self.scan_action.setEnabled(True)
        self.scan_action.setText("Scan (Space)")
        self._progress_timer.stop()
        self.progress_bar.hide()
        self._update_status()

    def _scan_done(self, photos):
        self._last_scan_ids = [p.id for p in photos]
        item = None
        for p in photos:
            item = self._add_item(p)
        self._apply_filter()
        self._refresh_tags()
        if item:
            self.grid.scrollToItem(item)
        # If nothing is being tagged right now, select the new batch so it
        # can be tagged straight away. Never steal an existing selection.
        if not self.grid.selectedItems():
            self.select_last_scan()
        self.statusBar().showMessage(f"Scan finished: {len(photos)} photo(s). Press N to select them.", 5000)

    def _scan_failed(self, message: str):
        QMessageBox.warning(self, "Scan failed", message)

    def _update_status(self):
        if getattr(self, "status_label", None) is None:
            self.progress_bar = QProgressBar()
            self.progress_bar.setMaximumWidth(260)
            self.progress_bar.setTextVisible(True)
            self.progress_bar.hide()
            self.status_label = QLabel()
            self.statusBar().addPermanentWidget(self.progress_bar)
            self.statusBar().addPermanentWidget(self.status_label)
        todo = sum(not p.done for p in self.library.photos.values())
        state = "Scanning, keep tagging" if self.is_scanning() else "Ready"
        self.status_label.setText(f"{state}  |  {todo} to do, {len(self.library.photos) - todo} done"
                                  f"  |  sorted into {self.library.sorted_folder}")

    def closeEvent(self, e):
        if self.is_scanning():
            self._thread.quit()
            self._thread.wait(30000)
        super().closeEvent(e)
