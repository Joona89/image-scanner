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
