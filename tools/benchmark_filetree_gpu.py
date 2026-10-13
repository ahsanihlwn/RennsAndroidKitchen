# SPDX-License-Identifier: MPL-2.0
"""Reproducible FileTree repaint/scroll/hover and rounded dialog probe.

Uses synthetic populated items with cached metadata: no project/editor is
opened and no user files/preferences are changed. Native Qt fonts stay active.
"""
import argparse
import cProfile
import io
import json
import math
import os
from pathlib import Path
import pstats
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=4000)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    root_path = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_path))
    os.chdir(root_path)
    if args.disable_gpu:
        os.environ["RAK_DISABLE_SKIA_GPU"] = "1"
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QSignalBlocker, QCoreApplication, QEvent, QTimer, QEventLoop
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTreeWidgetItem
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QPainter, QImage, QColor
    from core.skia_gpu import gpu_backend_status
    from core.skia_widgets import install_widget_gpu_rendering
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from main_ext.class_ThemeUi import theme
    from main_ext.class_FileTree import FileTree
    from main_ext.class_NameBadgeDelegate import IS_FOLDER_ROLE, SIZE_LABEL_ROLE
    from main_ext.class_AppDialog import _Dlg

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    theme.load()
    install_button_press_animations(app)
    install_widget_gpu_rendering(app)
    root = QWidget()
    root.setAttribute(Qt.WA_DontShowOnScreen)
    root.resize(960, 700)
    # Main uses a declaration-only background inherited by its dialogs.
    root.setStyleSheet(f"background-color: {theme.BG};")
    tree = FileTree()
    QVBoxLayout(root).addWidget(tree)
    tree.setColumnWidth(0, 360)
    blocker = QSignalBlocker(tree)
    rows = []
    badges = ("EROFS", "EXT4", "SPARSE", "", "BOOT", "PAYLOAD")
    for index in range(args.rows):
        item = QTreeWidgetItem([f"partition_{index:04d}_firmware.img", "u:object_r:system_file:s0", "rw-r--r--"])
        item.setData(0, Qt.UserRole, "mount://benchmark/" + str(index))
        item.setData(0, IS_FOLDER_ROLE, False)
        item.setData(0, SIZE_LABEL_ROLE, f"{34 + index % 300}.0 MB")
        item.setData(0, Qt.UserRole + 1, badges[index % len(badges)])
        item.setData(0, Qt.UserRole + 51, index % 7 == 0)
        item.setData(0, Qt.UserRole + 10, "blue" if index % 9 == 0 else None)
        item.setIcon(0, tree.icon_file)
        rows.append(item)
    tree.addTopLevelItems(rows)
    del blocker
    root.show()
    QTest.qWait(300)
    root.grab()
    app.processEvents()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    def measure(change=None):
        samples = []
        profile = cProfile.Profile()
        before = gpu_backend_status()
        cpu = time.process_time()
        profile.enable()
        for index in range(args.samples):
            started = time.perf_counter()
            if change:
                change(index)
            root.grab()
            app.processEvents()
            samples.append((time.perf_counter() - started) * 1000)
        profile.disable()
        elapsed_cpu = time.process_time() - cpu
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(25)
        ordered = sorted(samples)
        return {"median_ms": statistics.median(samples),
                "p95_ms": ordered[math.ceil(len(samples) * .95) - 1],
                "max_ms": max(samples), "cpu_seconds": elapsed_cpu,
                "gpu_frames": gpu_backend_status().get("frames", 0) - before.get("frames", 0),
                "profile": stream.getvalue()}

    try:
        report = {"description": "FileTree with synthetic cached metadata; grab + dispatch, not FPS",
                  "rows": args.rows, "samples": args.samples, "idle": measure()}
        bar = tree.verticalScrollBar()
        from PySide6.QtWidgets import QAbstractItemView
        row_height = tree.visualItemRect(rows[0]).height()
        step = 19 * row_height if tree.verticalScrollMode() == QAbstractItemView.ScrollPerPixel else 19
        report["scroll_distance_rows_per_sample"] = 19
        report["scroll"] = measure(lambda index: bar.setValue(index * step))
        report["small_scroll"] = measure(lambda index: bar.setValue(index * max(1, row_height // 4)))
        bar.setValue(0)
        QTest.qWait(150)
        loop = QEventLoop()
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(16)
        paced_work, gaps = [], []
        previous = [None]
        position = [0]
        def tick():
            started = time.perf_counter()
            if previous[0] is not None:
                gaps.append((started - previous[0]) * 1000)
            previous[0] = started
            bar.setValue(position[0] * max(1, row_height // 4))
            root.grab()
            paced_work.append((time.perf_counter() - started) * 1000)
            position[0] += 1
            if position[0] >= args.samples:
                timer.stop()
                loop.quit()
        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        report["paced_small_scroll"] = {
            "profile_enabled": False, "median_ms": statistics.median(paced_work),
            "p95_ms": sorted(paced_work)[math.ceil(len(paced_work)*.95)-1],
            "timer_spacing_median_ms": statistics.median(gaps),
            "timer_spacing_p95_ms": sorted(gaps)[math.ceil(len(gaps)*.95)-1],
            "timer_spacing_max_ms": max(gaps)}
        report["cell_cache"] = {"hits": tree._badge_delegate._cell_cache_hits,
                                "misses": tree._badge_delegate._cell_cache_misses,
                                "bytes": tree._badge_delegate._cell_cache_bytes,
                                "entries": len(tree._badge_delegate._cell_cache)}
        bar.setValue(0)
        app.processEvents()
        root.grab().save(str(artifacts / "tree.png"))
        # Observe actual hover damage through viewport paint events, without
        # adding a full-window grab that would override the damage region.
        from PySide6.QtCore import QObject, QPoint, QPointF
        from PySide6.QtGui import QMouseEvent
        from unittest.mock import patch
        class PaintRecorder(QObject):
            def __init__(self):
                super().__init__()
                self.regions = []
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Paint:
                    self.regions.append(event.region().boundingRect().getRect())
                return False
        recorder = PaintRecorder()
        tree.viewport().installEventFilter(recorder)
        tree.viewport().repaint()
        damage = []
        with patch.object(tree.viewport(), "update", wraps=tree.viewport().update) as update:
            for index in range(min(16, len(rows))):
                point = tree.visualItemRect(rows[index]).center()
                event = QMouseEvent(QEvent.MouseMove, QPointF(point), QPointF(tree.viewport().mapToGlobal(point)), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
                QApplication.sendEvent(tree.viewport(), event)
                app.processEvents()
            damage = [list(call.args[0].getRect()) for call in update.call_args_list if call.args]
        report["hover_paint_regions"] = recorder.regions
        report["hover_requested_damage"] = damage
        tree.viewport().removeEventFilter(recorder)

        dialog = _Dlg(root, "Return", "Are you sure you want to return to the project picker?<br><br>All open editors and sessions will be closed.")
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog._btn("Cancel", "neutral", 0)
        dialog._btn("Return", "primary", 1)
        dialog.adjustSize()
        dialog.show()
        QTest.qWait(350)
        # Render on transparent pixels so inherited root backgrounds / stale
        # alpha at the rounded corners can be detected quantitatively.
        image = QImage(dialog.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        dialog.render(image)
        image.save(str(artifacts / "dialog.png"))
        report["dialog"] = {"size": [image.width(), image.height()],
            "corner_alpha": [image.pixelColor(x, y).alpha() for x, y in (
                (0, 0), (image.width()-1, 0), (0, image.height()-1), (image.width()-1, image.height()-1))],
            "root_gpu_surface": hasattr(dialog, "_skia_surface_original"),
            "box_gpu_surface": hasattr(dialog._box, "_skia_surface_original")}
        dialog.hide()
        dialog.deleteLater()
        report["backend"] = gpu_backend_status()
        (artifacts / "timings.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: {k:v for k,v in value.items() if k != "profile"} if isinstance(value,dict) else value for key,value in report.items()},indent=2))
    finally:
        tree.stop_background_work()
        root.hide()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
