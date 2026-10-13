# SPDX-License-Identifier: MPL-2.0
"""Startup scene pixels, paint isolation, build ordering and cancellation."""
import ast
from collections import deque
import os
from pathlib import Path
import time
import types
import unittest

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QTimer, QEvent, QCoreApplication, QPoint, Signal, Slot, QEventLoop, QRect
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QStackedWidget, QLabel, QProgressBar, QAbstractButton, QLineEdit, QPushButton
from shiboken6 import isValid

from app_ext.class_StartupVisual import StartupVisual
from app_ext.class_StartupTransition import StartupTransition, DriveUsageTiming
from core.skia_gpu import gpu_backend_status, gpu_available


class CountingLabel(QLabel):
    paints = 0
    def paintEvent(self, event):
        self.paints += 1
        super().paintEvent(event)


class Harness(QWidget):
    startup_built = Signal()
    startup_revealed = Signal()
    startup_animation_started = Signal()


def production_method(name):
    path = Path(__file__).resolve().parent.parent / "app.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "AppUI")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = dict(StartupTransition=StartupTransition, Slot=Slot, QTimer=QTimer, QApplication=QApplication,
                     QEventLoop=QEventLoop, time=time, _global_excepthook=lambda *args: None,
                     QLabel=QLabel, QWidget=QWidget, QAbstractButton=QAbstractButton, QLineEdit=QLineEdit,
                     QProgressBar=QProgressBar, QRect=QRect,
                     QPoint=QPoint, isValid=isValid)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class StartupTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from core.fonts import apply_app_font
        apply_app_font(cls.app)

    def setUp(self):
        self.root = Harness()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(480, 330)
        self.root.setStyleSheet("background:#1e2430;color:#cdd6e2;")
        self.root.stack = QStackedWidget(self.root)
        self.root.stack.setGeometry(10, 10, 460, 310)
        self.root.page_projects = QWidget()
        self.root.page_create = QWidget()
        self.root.stack.addWidget(self.root.page_projects)
        self.root.stack.addWidget(self.root.page_create)
        self.label = CountingLabel("Native Qt cafe العربية", self.root.page_projects)
        self.label.setGeometry(20, 20, 290, 30)
        self.label.setStyleSheet("font-size:17px;color:#cdd6e2;background:transparent;")
        footer = QWidget(self.root.page_projects)
        footer.setGeometry(0, 270, 460, 40)
        self.root._page_footers = {self.root.page_projects: footer}
        self.root.list_widget = None
        self.root._project_drive_cards = {}
        self.root._startup_transition = self.root._startup_content_reveal = None
        self.root._startup_rows = None
        self.root._startup_builder = None
        self.root._startup_paint_widgets = None
        self.root._startup_ui_built = False
        self.root._startup_reveal_started = self.root._startup_revealed_emitted = False
        self.root._startup_fade = None
        self.root._create_project_dialog = None
        for name in ("_settings_warm_timer", "_create_popup_warm_timer", "_startup_rows_timer", "_startup_prepare_timer"):
            timer = QTimer(self.root)
            timer.setSingleShot(True)
            setattr(self.root, name, timer)
        for name in ("reveal_startup", "_on_startup_scene_finished", "_complete_startup_reveal",
                     "_emit_startup_revealed_if_ready", "_emit_startup_built_if_ready", "_build_startup_project_rows",
                     "_prepare_startup_paints", "_start_prepared_startup_scene"):
            setattr(self.root, name, types.MethodType(production_method(name), self.root))
        self.root._startup_prepare_timer.timeout.connect(self.root._prepare_startup_paints)
        self.root.show()
        self.app.processEvents()
        self.root._startup_splash = StartupVisual(self.root)
        self.root._startup_splash.setGeometry(self.root.rect())
        self.root._startup_splash.show()
        self.app.processEvents()
        self.scene = None
        self.manager = None

    def tearDown(self):
        if self.manager is not None:
            self.app.removeEventFilter(self.manager)
            self.manager._timer.stop()
            self.manager._pending.clear()
            self.manager.deleteLater()
        if self.scene is not None and isValid(self.scene):
            self.scene.stop()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def reveal(self):
        self.root.reveal_startup()
        deadline = time.monotonic() + 2
        while self.root._startup_transition is None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.scene = self.root._startup_transition
        self.assertIsNotNone(self.scene)
        return self.scene

    def test_opaque_animation_reuses_qt_content_without_live_caption_paints_or_gpu_readbacks(self):
        scene = self.reveal()
        self.label.paints = 0
        frames = gpu_backend_status().get("frames", 0)
        QTest.qWait(180)
        self.assertTrue(scene.testAttribute(Qt.WA_OpaquePaintEvent))
        self.assertEqual(self.label.paints, 0)
        self.assertEqual(gpu_backend_status().get("frames", 0), frames)
        self.assertIsNone(self.root._startup_splash.graphicsEffect())
        self.assertFalse(self.root._startup_splash.isVisible())

    def test_completed_scene_preserves_exact_native_qt_font_pixels(self):
        scene = self.reveal()
        scene._timer.stop()
        scene._started_at = time.monotonic() - 2
        expected = QImage(scene._content_frame)
        self.assertEqual(scene.grab().toImage().convertToFormat(expected.format()), expected)
        finished = []
        self.root.startup_revealed.connect(lambda: finished.append(True))
        scene.stop()
        scene.stop()
        self.assertEqual(finished, [True])
        self.assertIsNone(self.root._startup_splash)
        self.assertIsNone(self.root._startup_content_reveal)
        self.assertFalse(self.root._cached_page_transition_active)

    def test_resize_hide_and_dpr_cancel_instead_of_leaving_an_opaque_cover(self):
        scene = self.reveal()
        finished = []
        self.root.startup_revealed.connect(lambda: finished.append(True))
        QApplication.sendEvent(self.root, QEvent(QEvent.DevicePixelRatioChange))
        self.assertTrue(scene._stopped)
        self.assertFalse(scene.isVisible())
        self.root.resize(500, 350)
        self.root.hide()
        self.assertEqual(finished, [True])
        self.assertFalse(self.root._cached_page_transition_active)

    def test_reveal_waits_for_all_project_rows_and_built_signal_emits_once(self):
        self.root._startup_rows = deque(enumerate([{"name":"a"}, {"name":"b"}, {"name":"c"}]))
        self.root._startup_ui_built = True
        self.root.reveal_startup()
        self.assertFalse(self.root._startup_reveal_started)
        built, rows = [], []
        self.root.startup_built.connect(lambda: built.append(True))
        self.root._add_project_row = lambda project, order: rows.append((project["name"], order))
        self.root.search_input = QLabel("")
        self.root._filter_projects = lambda _: None
        self.root._update_trash_hint = lambda: None
        self.root._build_startup_project_rows()
        self.root._emit_startup_built_if_ready()
        self.assertEqual(rows, [("a",0),("b",1),("c",2)])
        self.assertEqual(built, [True])
        self.assertIsNone(self.root._startup_rows)
        self.reveal()

    def test_login_destination_uses_full_cached_crossfade_without_page_bands(self):
        self.root._login_overlay = object()
        scene = self.reveal()
        self.assertIsNone(scene._model)
        self.assertAlmostEqual(scene._end_at, .260)
        QTest.qWait(360)
        self.assertTrue(self.root._startup_revealed_emitted)

    def test_drive_timing_retains_stagger_and_smooth_extended_tail(self):
        self.assertEqual(DriveUsageTiming.progress(-50, 0), 0)
        self.assertEqual(DriveUsageTiming.progress(0, 0), 0)
        self.assertAlmostEqual(DriveUsageTiming.progress(420, 0), .936)
        self.assertEqual(DriveUsageTiming.progress(840, 0), 1)
        self.assertLess(DriveUsageTiming.progress(500, 1), DriveUsageTiming.progress(500, 0))
        self.assertEqual(DriveUsageTiming.total(2), 882)

    def test_rom_dots_do_not_require_a_missing_glyph_and_follow_mode_and_translation(self):
        from app_ext.class_RomModeButton import bind_rom_mode_button
        from core.language_binding import refresh_language
        from main_ext.class_ThemeUi import theme
        mode = [1]
        captions = {"btn_single_rom":"⬤  ROM Tunggal", "btn_dual_rom":"⬤⬤  ROM Ganda"}
        buttons = [bind_rom_mode_button(QPushButton(self.root), count,
                   lambda count=count: mode[0] == count, captions.__getitem__) for count in (1, 2)]
        self.assertEqual([button.text() for button in buttons], ["ROM Tunggal", "ROM Ganda"])
        self.assertTrue(all(not button.icon().isNull() for button in buttons))
        self.assertGreater(buttons[1].iconSize().width(), buttons[0].iconSize().width())
        old_icon = buttons[0].icon().cacheKey()
        mode[0] = 2
        buttons[0].setStyleSheet(f"color:{theme.FG_MUTED};")
        buttons[1].setStyleSheet(f"color:{theme.FG_BLUE};")
        self.app.processEvents()
        self.assertNotEqual(buttons[0].icon().cacheKey(), old_icon)
        captions["btn_single_rom"] = "⬤  Single ROM"
        captions["btn_dual_rom"] = "⬤⬤  Dual ROM"
        refresh_language(self.root)
        self.assertEqual([button.text() for button in buttons], ["Single ROM", "Dual ROM"])

    def test_drive_counter_uses_qt_fonts_and_cached_surfaces_without_mutating_live_widgets(self):
        card = QWidget(self.root.page_projects)
        card.setGeometry(290, 70, 145, 50)
        card.setStyleSheet("background:#293747;border:1px solid #485260;border-radius:6px;")
        usage = CountingLabel("50/100 GB", card)
        usage.setGeometry(8, 5, 120, 18)
        usage.setStyleSheet("font-size:11px;color:#93aabc;background:transparent;border:none;")
        usage._used_gb, usage._total_gb = 50, 100
        usage._final_variants = ("50/100 GB", "50/100")
        percent = CountingLabel("50%", card)
        percent.setGeometry(103, 25, 30, 18)
        percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        percent.setStyleSheet("font-size:10px;color:#93aabc;background:transparent;border:none;")
        bar = QProgressBar(card)
        bar.setGeometry(8, 33, 87, 4)
        bar.setTextVisible(False)
        bar.setRange(0, 10000)
        bar.setValue(5000)
        bar.setStyleSheet("QProgressBar {background:#425060;border:none;border-radius:2px;} QProgressBar::chunk {background:#f25d91;border-radius:2px;}")
        from core.skia_widgets import _WidgetGpuManager
        if gpu_available():
            self.manager = _WidgetGpuManager(self.app)
            self.app.installEventFilter(self.manager)
            self.manager._prepare(bar)
        card.show()
        self.root._startup_splash.hide()
        self.app.processEvents()
        expected = self.root.grab().toImage()
        self.root._project_drive_cards = {"C:":dict(widget=card, usage=usage, percent=percent,
                                                     bar=bar, usage_fraction=.5, usage_percent=None)}
        scene = self.reveal()
        point = bar.mapTo(self.root, QPoint(12, 2))
        ratio = expected.devicePixelRatioF()
        self.assertNotEqual(scene._content_frame.pixelColor(round(point.x()*ratio), round(point.y()*ratio)),
                            expected.pixelColor(round(point.x()*ratio), round(point.y()*ratio)))
        scene._timer.stop()
        scene._started_at = time.monotonic() - 2
        rendered = scene.grab().toImage().convertToFormat(expected.format())
        ratio = expected.devicePixelRatioF()
        for label in (usage, percent):
            point = label.mapTo(self.root, QPoint())
            coords = (round(point.x()*ratio), round(point.y()*ratio), round(label.width()*ratio), round(label.height()*ratio))
            self.assertEqual(rendered.copy(*coords), expected.copy(*coords))
        scene._started_at = time.monotonic()
        usage.paints = percent.paints = 0
        frames = gpu_backend_status().get("frames", 0)
        scene._timer.start()
        QTest.qWait(180)
        self.assertEqual(usage.paints + percent.paints, 0)
        self.assertEqual(bar.value(), 5000)
        self.assertEqual(usage.text(), "50/100 GB")
        self.assertEqual(percent.text(), "50%")
        self.assertEqual(gpu_backend_status().get("frames", 0), frames)


if __name__ == "__main__":
    unittest.main()
