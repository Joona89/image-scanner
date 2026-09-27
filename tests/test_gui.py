import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from scanner_app.gui import MainWindow  # noqa: E402
from scanner_app.library import Library  # noqa: E402
from scanner_app.scanner import DemoScanner  # noqa: E402


@pytest.fixture
def win(tmp_path):
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    w = MainWindow(Library(tmp_path / "out"), DemoScanner(delay=0.3), settings)
    w.show()
    QTest.qWaitForWindowExposed(w)
    yield w
    w.close()


def wait_scan(w, timeout=20000):
    # QTest.qWait keeps the GIL, which starves the scan thread; a real
    # event loop releases it like the app does.
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: w.is_scanning() or loop.quit())
    poll.start(50)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    poll.stop()
    assert not w.is_scanning()


def test_space_scans_and_selects_new_batch(win):
    win.grid.setFocus()
    QTest.keyClick(win.grid, Qt.Key_Space)
    assert win.is_scanning()
    wait_scan(win)
    assert win.grid.count() == 4          # first demo layout has 4 photos
    assert len(win.selected_ids()) == 4


def test_tag_while_scanning_and_number_hotkeys(win):
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    first = win.selected_ids()

    # Type a tag with T, apply with Enter.
    QTest.keyClick(win.grid, Qt.Key_T)
    assert win.tag_edit.hasFocus()
    QTest.keyClicks(win.tag_edit, "Mummo, 1985")
    QTest.keyClick(win.tag_edit, Qt.Key_Return)
    assert all(win.library.photos[i].tags == ["1985", "Mummo"] for i in first)
    assert win.quick_tags == ["Mummo", "1985"]

    # Start the next scan and keep tagging while it runs.
    QTest.keyClick(win.grid, Qt.Key_Space)
    assert win.is_scanning()
    win.grid.clearSelection()
    win.grid.item(0).setSelected(True)
    QTest.keyClick(win.grid, Qt.Key_1)    # toggles "Mummo" off photo 0
    assert win.library.photos[first[0]].tags == ["1985"]
    wait_scan(win)
    # The running selection was not stolen by the finished scan.
    assert win.selected_ids() == [first[0]]
    assert win.grid.count() == 6

    QTest.keyClick(win.grid, Qt.Key_N)
    assert len(win.selected_ids()) == 2
    QTest.keyClick(win.grid, Qt.Key_2)
    assert all("1985" in win.library.photos[i].tags for i in win.selected_ids())


def test_filter_untagged(win):
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    win.library.add_tag([win.grid.item(0).data(Qt.UserRole)], "x")
    win.filter_edit.setText("untagged")
    assert sum(not win.grid.item(i).isHidden() for i in range(win.grid.count())) == 3


def test_year_then_done_moves_photos_to_year_folder(win):
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    batch = win.selected_ids()
    assert len(batch) == 4

    # Done without a year: nothing is saved and the photos stay selected.
    QTest.keyClick(win.grid, Qt.Key_D)
    assert not any(win.library.photos[i].done for i in batch)
    assert sorted(win.selected_ids()) == sorted(batch)

    QTest.keyClick(win.grid, Qt.Key_Y)
    assert win.year_edit.hasFocus()
    QTest.keyClicks(win.year_edit, "87")
    QTest.keyClick(win.year_edit, Qt.Key_Return)
    assert all(win.library.photos[i].year == 1987 for i in batch)

    QTest.keyClick(win.grid, Qt.Key_D)
    assert all(win.library.photos[i].done for i in batch)
    assert len(list((win.library.sorted_folder / "1987").glob("*.jpg"))) == 4
    # They leave the to-do view.
    assert all(win.grid.item(i).isHidden() for i in range(win.grid.count()))
    win.view_combo.setCurrentIndex(1)
    assert not any(win.grid.item(i).isHidden() for i in range(win.grid.count()))


def test_year_for_new_scans_and_450_dpi(win):
    assert win.dpi_combo.findData(450) >= 0
    win.dpi_combo.setCurrentIndex(win.dpi_combo.findData(450))
    win.new_year_edit.setText("1992")
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    photos = [win.library.photos[i] for i in win.selected_ids()]
    assert photos and all(p.year == 1992 and p.dpi == 450 for p in photos)


def test_progress_bar(win, tmp_path):
    from scanner_app.synthetic import DEMO_LAYOUTS, make_scan
    import cv2
    from scanner_app.scanner import FolderScanner

    QTest.keyClick(win.grid, Qt.Key_F5)
    assert win.progress_bar.isVisible() and win.progress_bar.maximum() == 100
    wait_scan(win)
    assert not win.progress_bar.isVisible()

    # A scanner without progress reports: busy first, then an estimate.
    src = tmp_path / "src"
    src.mkdir()
    cv2.imwrite(str(src / "a.png"), make_scan(DEMO_LAYOUTS[1]))
    win.scanner = FolderScanner(src)
    QTest.keyClick(win.grid, Qt.Key_F5)
    assert win.progress_bar.maximum() == 0
    wait_scan(win)
    QTest.keyClick(win.grid, Qt.Key_F5)
    assert win.progress_bar.maximum() == 100 and "estimate" in win.progress_bar.format()
    wait_scan(win)


def test_done_button_uses_typed_date_and_caption(win):
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    batch = win.selected_ids()

    # Type a date and caption but press the Done button instead of Enter.
    QTest.keyClick(win.grid, Qt.Key_Y)
    QTest.keyClicks(win.year_edit, "14.6.1985")
    win._focus_caption_edit()
    QTest.keyClicks(win.caption_edit, "Juhannus")
    QTest.mouseClick(win.done_button, Qt.LeftButton)

    for i in batch:
        p = win.library.photos[i]
        assert p.date == (1985, 6, 14) and p.caption == "Juhannus" and p.done
    assert len(list((win.library.sorted_folder / "1985").glob("1985-06-14_*.jpg"))) == 4


def test_fields_show_selection_values(win):
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    ids = win.selected_ids()
    win.library.set_date(ids[:1], (1990, 5, None))
    win.library.set_caption(ids[:1], "x")
    win.grid.clearSelection()
    win.grid.item(0).setSelected(True)
    assert win.year_edit.text() == "1990-05" and win.caption_edit.text() == "x"
    # Mixed selection: blank, and Done does not overwrite anything with blanks.
    for i in range(win.grid.count()):
        win.grid.item(i).setSelected(True)
    assert win.year_edit.text() == "" and win.caption_edit.text() == ""
    win.library.set_year(ids[1:], 1991)
    win.grid.setFocus()
    QTest.keyClick(win.grid, Qt.Key_D)
    assert win.library.photos[ids[0]].date == (1990, 5, None)
    assert win.library.photos[ids[0]].caption == "x"


def test_more_dpi_choices_and_rotate_refreshes_preview(win):
    for dpi in (75, 200, 400, 450, 800, 2400):
        assert win.dpi_combo.findData(dpi) >= 0
    QTest.keyClick(win.grid, Qt.Key_F5)
    wait_scan(win)
    win.grid.clearSelection()
    win.grid.setCurrentItem(win.grid.item(0))
    before = win.preview.pixmap().size()
    QTest.keyClick(win.grid, Qt.Key_R)
    after = win.preview.pixmap().size()
    # The preview now shows the rotated photo (portrait instead of landscape).
    assert (before.width() > before.height()) != (after.width() > after.height())
