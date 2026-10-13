# SPDX-License-Identifier: MPL-2.0
"""Cached page composition, scroll input, clipped marquees and style reuse."""
import os
import time
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, QPointF, QObject, QCoreApplication, QRectF, qInstallMessageHandler
from PySide6.QtGui import QWheelEvent, QImage, QPainter, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QStackedWidget, QVBoxLayout, QLabel, QPushButton, QListWidgetItem
from shiboken6 import isValid

from app_ext.class_AppPageTransition import AppPageTransition
from app_ext.class_FooterActionTransition import FooterActionTransition
from app_ext.class_ProjectHoverCanvas import ProjectHoverCanvas
from app_ext.class_SmoothListWidget import SmoothListWidget
from core.skia_gpu import gpu_backend_status
from core.skia_button import ButtonSurface
from core.skia_patch import SurfacePatch
from core.theme_binding import bind_style, refresh_theme
from core.skia_widgets import _WidgetGpuManager
from core.visible_label_clock import VisibleLabelClock


class Recorder(QObject):
    def __init__(self, kind=QEvent.Paint):
        super().__init__()
        self.kind, self.count = kind, 0
    def eventFilter(self, obj, event):
        self.count += event.type() == self.kind
        return False


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(500, 400)
        layout = QVBoxLayout(self.root)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.old, self.page = QWidget(), QWidget()
        self.stack.addWidget(self.old)
        self.stack.addWidget(self.page)
        self.page.setStyleSheet("background:#20232b; color:white;")
        self.caption = QLabel("Native Qt caption cafe", self.page)
        self.caption.setGeometry(25, 30, 250, 35)
        self.source = QPushButton("Settings", self.old)
        self.target = QPushButton("Back", self.page)
        for button in (self.source, self.target):
            button.setGeometry(320, 300, 105, 32)
            button.setStyleSheet("background:#303540;border:1px solid #666;border-radius:6px;color:white;")
        self.root.show()
        self.app.processEvents()
        self.scene = None

    def tearDown(self):
        if self.scene is not None:
            self.scene.stop()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def make_scene(self):
        self.stack.setCurrentWidget(self.page)
        self.app.processEvents()
        footer = FooterActionTransition(self.stack, (self.source,), (self.target,))
        self.scene = AppPageTransition(self.page, footer=footer)
        return self.scene

    def test_scene_uses_cached_qt_pixels_without_live_caption_paints_or_gpu_readbacks(self):
        scene = self.make_scene()
        recorder = Recorder()
        self.caption.installEventFilter(recorder)
        before = gpu_backend_status().get("frames", 0)
        scene.start()
        QTest.qWait(250)
        self.assertEqual(recorder.count, 0)
        self.assertEqual(gpu_backend_status().get("frames", 0), before)
        self.assertTrue(self.root._cached_page_transition_active)
        QTest.qWait(500)
        self.assertFalse(self.root._cached_page_transition_active)
        self.assertFalse(isValid(scene._footer))

    def test_completed_page_retains_exact_native_caption_pixels(self):
        scene = self.make_scene()
        expected = self.page.grab().toImage().copy(25, 30, 250, 35)
        scene.start()
        scene._timer.stop()
        scene._started_at = time.monotonic() - 2
        scene._footer._group.setCurrentTime(scene._footer._DURATION)
        rendered = scene.grab().toImage().copy(25, 30, 250, 35)
        self.assertEqual(expected.convertToFormat(QImage.Format_ARGB32_Premultiplied),
                         rendered.convertToFormat(QImage.Format_ARGB32_Premultiplied))

    def test_scene_cancel_and_resize_release_models_once(self):
        scene = self.make_scene()
        finished = []
        scene.finished.connect(lambda: finished.append(True))
        scene.start()
        self.page.resize(self.page.width()-10, self.page.height())
        self.assertTrue(scene._stopped)
        scene.stop()
        self.assertEqual(finished, [True])
        self.assertFalse(self.root._cached_page_transition_active)

    def test_completion_deadline_restores_page_if_frame_timer_is_interrupted(self):
        scene = self.make_scene()
        finished = []
        scene.finished.connect(lambda: finished.append(True))
        scene.start()
        scene._timer.stop()
        QTest.qWait(round(scene._end_at*1000) + 150)
        self.assertEqual(finished, [True])
        self.assertTrue(scene._stopped)
        self.assertTrue(not isValid(scene) or not scene.isVisible())
        self.assertFalse(self.root._cached_page_transition_active)
        self.assertTrue(self.page.isVisible())

    def test_caption_replica_styles_remain_valid_for_scoped_and_raw_source_rules(self):
        from app_ext.class_FooterActionTransition import FooterActionTransition
        warnings = []
        previous = qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
        model = None
        try:
            scene, model, _ = self.make_profile_scene()
            source = QLabel("Qt source caption", self.page)
            source.setObjectName("captionSource")
            source.setStyleSheet("QLabel#captionSource {background:#304050;color:#dbe7ff;font-size:18px;}")
            source.setGeometry(15, 100, 200, 35)
            source.show()
            manager = _WidgetGpuManager(self.app)
            manager._prepare(source)
            effective = source.styleSheet()
            clone = model._label(source, source.geometry())
            clone.ensurePolished()
            self.assertNotIn("_rakSkiaSurface", clone.styleSheet())
            self.assertEqual(clone.font().pixelSize(), source.font().pixelSize())
            self.assertEqual(source.styleSheet(), effective)
            manager.deleteLater()
            footer = FooterActionTransition(self.stack, (self.source,), (self.target,))
            footer._views[-1].ensurePolished()
            footer.stop()
            self.app.processEvents()
            self.assertFalse([message for message in warnings if "Could not parse stylesheet" in message])
        finally:
            qInstallMessageHandler(previous)

    def make_profile_scene(self):
        try:
            from rennsproject.app_ext.class_ProfileCardTransition import ProfileCardTransition
        except ImportError:
            self.skipTest("optional profile feature bundle unavailable")
        from PySide6.QtGui import QPixmap, QFont
        def endpoint(page, card):
            widget = QWidget(page)
            widget.setGeometry(5, 5 if card else 280, 430 if card else 180, 65)
            avatar = QLabel(widget)
            avatar.setGeometry(5, 10, 28, 28)
            pixmap = QPixmap(28, 28)
            pixmap.fill(QColor("#66aabb"))
            avatar.setPixmap(pixmap)
            name = QLabel("Qt profile caption", widget)
            name.setGeometry(40, 5, 170, 25)
            status = QLabel("Native status", widget)
            status.setGeometry(40, 32, 170, 20)
            for label in (name, status):
                label.setStyleSheet("background:transparent;border:none;color:white;")
            badge = QLabel(widget)
            badge.hide()
            widget.show()
            return dict(widget=widget, page=page, card=card, avatar=avatar, name=name, status=status, badge=badge)
        source = endpoint(self.old, False)
        target = endpoint(self.page, True)
        self.stack.setCurrentWidget(self.page)
        self.app.processEvents()
        model = ProfileCardTransition.create(self.stack, source, target)
        self.assertIsNotNone(model)
        self.scene = AppPageTransition(self.page, profile=model)
        return self.scene, model, target

    def test_shared_profile_caption_assets_are_nonempty_and_keep_native_qt_font_pixels(self):
        scene, model, target = self.make_profile_scene()
        expected = target["name"].grab().toImage()
        names = [(view, image) for view,image in self.scene._profile_layers
                 if isinstance(view, QLabel) and view.text() == "Qt profile caption"]
        self.assertEqual(len(names), 2)
        for view, image in names:
            self.assertFalse(image.isNull())
            self.assertEqual(image.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied),
                             expected.convertToFormat(QImage.Format_ARGB32_Premultiplied))
        self.scene.start()
        self.scene.stop()
        self.assertIsNone(target["widget"].graphicsEffect())

    def test_external_profile_stop_cancels_canvas_before_next_paint(self):
        scene, model, target = self.make_profile_scene()
        finished = []
        scene.finished.connect(lambda: finished.append(True))
        scene.start()
        model.stop()
        scene._tick()
        self.assertTrue(scene._stopped)
        scene.paintEvent(QEvent(QEvent.Paint))
        self.assertEqual(finished, [True])
        self.assertIsNone(target["widget"].graphicsEffect())
        self.assertFalse(self.root._cached_page_transition_active)

    def test_deleted_profile_surface_cancels_and_restores_target(self):
        scene, model, target = self.make_profile_scene()
        scene.start()
        model._surface.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(scene._stopped)
        self.assertIsNone(target["widget"].graphicsEffect())
        scene.paintEvent(QEvent(QEvent.Paint))

    def test_deleted_profile_model_restores_owned_target_effect(self):
        scene, model, target = self.make_profile_scene()
        scene.start()
        model.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(scene._stopped)
        self.assertIsNone(target["widget"].graphicsEffect())

    def test_deleted_profile_target_does_not_break_cleanup(self):
        scene, model, target = self.make_profile_scene()
        scene.start()
        target["widget"].deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(scene._stopped)
        self.assertFalse(self.root._cached_page_transition_active)

    def test_skia_surface_asset_reuses_image_for_resize_and_preserves_corner_alpha(self):
        shape = ButtonSurface(QColor("#334455"), QColor("#aabbcc"), 1.5, 6)
        asset = SurfacePatch(shape, self.root.devicePixelRatioF())
        before = gpu_backend_status().get("frames", 0)
        for width in (100, 190, 270):
            image = QImage(width, 40, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            asset.draw(painter, QRectF(0, 0, width, 40))
            painter.end()
            self.assertEqual(image.pixelColor(width//2,20), shape.background)
            self.assertLess(image.pixelColor(0,0).alpha(), 255)
        self.assertEqual(gpu_backend_status().get("frames", 0), before)
        again = SurfacePatch(shape, self.root.devicePixelRatioF())
        self.assertEqual(again.image.cacheKey(), asset.image.cacheKey())

    def test_theme_refresh_does_not_remove_skia_override_and_repolish_children(self):
        card = QWidget(self.page)
        child = QLabel("Native caption", card)
        factory = lambda: "background:#303540;border:1px solid white;border-radius:8px;"
        bind_style(card, factory)
        manager = _WidgetGpuManager(self.app)
        manager._prepare(card)
        effective = card.styleSheet()
        recorder = Recorder(QEvent.StyleChange)
        child.installEventFilter(recorder)
        refresh_theme(card)
        self.assertEqual(card.styleSheet(), effective)
        self.assertEqual(recorder.count, 0)
        manager.deleteLater()

    def test_scrolling_pauses_hover_capture_and_trackpad_delta_is_not_dropped(self):
        view = SmoothListWidget(self.page)
        view.setGeometry(0, 70, 450, 190)
        for n in range(30):
            view.addItem(QListWidgetItem(f"Project {n}"))
        view.set_viewport_background("#20232b")
        view.show()
        self.stack.setCurrentWidget(self.page)
        self.app.processEvents()
        hover = ProjectHoverCanvas(view, "#20232b")
        view.scrollingChanged.connect(hover.set_scrolling)
        hover.set_hovered_item(view.item(0))
        point = QPointF(view.viewport().rect().center())
        wheel = QWheelEvent(point, QPointF(view.viewport().mapToGlobal(point.toPoint())),
                            QPoint(0,-25), QPoint(), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(view.viewport(), wheel)
        self.assertEqual(view.verticalScrollBar().value(), 25)
        self.assertFalse(hover.isVisible())
        self.assertIsNone(view.viewport().graphicsEffect())
        deadline = time.monotonic() + 1
        while view._scrolling and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(view._scrolling)
        hover.reset()

    def test_marquee_clock_ignores_clipped_rows_and_wakes_after_scroll(self):
        view = SmoothListWidget(self.page)
        view.setGeometry(0, 0, 450, 150)
        view.show()
        self.stack.setCurrentWidget(self.page)
        self.app.processEvents()
        class Marquee(QLabel):
            ticks = 0
            def _max_offset(self): return 50
            def _advance_scroll(self): self.ticks += 1
        live = Marquee("visible", view.viewport())
        live.setGeometry(5, 5, 100, 25)
        clipped = Marquee("clipped", view.viewport())
        clipped.setGeometry(5, 500, 100, 25)
        live.show()
        clipped.show()
        clock = VisibleLabelClock.instance()
        clock.track(live)
        clock.track(clipped)
        clock._tick()
        self.assertEqual(live.ticks, 1)
        self.assertEqual(clipped.ticks, 0)
        clipped.move(5, 80)
        clock._tick()
        self.assertEqual(clipped.ticks, 1)

    def test_project_hover_does_no_capture_and_press_still_animates(self):
        view = SmoothListWidget(self.page)
        view.setGeometry(0, 0, 450, 150)
        view.addItem(QListWidgetItem("Press this project"))
        view.show()
        self.stack.setCurrentWidget(self.page)
        self.app.processEvents()
        canvas = ProjectHoverCanvas(view, "#20232b")
        item = view.item(0)
        canvas.set_hovered_item(item)
        QTest.qWait(40)
        self.assertFalse(canvas._timer.isActive())
        self.assertFalse(canvas.isVisible())
        self.assertFalse(canvas._states)
        canvas.set_pressed_item(item, True)
        QTest.qWait(40)
        self.assertTrue(canvas.isVisible())
        self.assertLess(canvas._states[id(item)]["scale"], 1)
        canvas.set_pressed_item(item, False)
        QTest.qWait(250)
        self.assertFalse(canvas.isVisible())
        self.assertFalse(canvas._timer.isActive())


if __name__ == "__main__":
    unittest.main()
