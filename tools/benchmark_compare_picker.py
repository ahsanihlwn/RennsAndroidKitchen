# SPDX-License-Identifier: MPL-2.0
"""Measure the real compare picker using in-memory partitions; no compare runs."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=128)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QEvent, QObject, QTimer, QPoint, QPointF, QEventLoop
    from PySide6.QtGui import QWheelEvent, QEnterEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from core.fonts import apply_app_font
    from core.skia_gpu import gpu_backend_status
    from core.skia_widgets import install_widget_gpu_rendering
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from app_ext.class_BlurOverlay import BlurOverlay, install_window_fade
    from main_ext.class_ThemeUi import theme
    from rennsproject.main_ext.class_CompareFilesystem import FolderPickerDialog, _FolderColumn

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    install_button_press_animations(app)
    install_widget_gpu_rendering(app)
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        bases = [Path(directory, name) for name in ("bedah", "target")]
        for base in bases:
            base.mkdir()
        root = QWidget()
        root.setAttribute(Qt.WA_DontShowOnScreen)
        root.setGeometry(100, 50, 1000, 760)
        root.setStyleSheet(theme.app_stylesheet())
        root.show()
        entries = [(f"system_{index:03}", "EROFS" if index % 2 else "") for index in range(args.rows)]
        with patch.object(_FolderColumn, "_collect_partitions", return_value=entries):
            dialog = FolderPickerDialog(*(str(base) for base in bases), parent=root)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        overlay = BlurOverlay(root)
        dialog._overlay = overlay
        install_window_fade(dialog)
        dialog.show()
        QTest.qWait(700)
        dialog.grab().save(str(output / "top.png"))

        class Recorder(QObject):
            def __init__(self):
                super().__init__()
                self.styles = self.paints = 0
            def eventFilter(self, obj, event):
                if event.type() == QEvent.StyleChange:
                    self.styles += 1
                elif event.type() == QEvent.Paint:
                    self.paints += 1
                return False
        recorder = Recorder()
        app.installEventFilter(recorder)
        area = dialog._col_a._scroll
        position = QPointF(area.viewport().rect().center())
        hovered = [None]
        ticks = [time.perf_counter()]
        heartbeat = QTimer()
        heartbeat.setTimerType(Qt.PreciseTimer)
        heartbeat.setInterval(16)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        loop = QEventLoop()
        wheel_ticks = [0]
        def scroll():
            wheel_ticks[0] += 1
            if wheel_ticks[0] % 4 == 0:
                event = QWheelEvent(position, QPointF(area.viewport().mapToGlobal(position.toPoint())),
                                    QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
                QApplication.sendEvent(area.viewport(), event)
            column = dialog._col_a
            point = column._list_w.mapFrom(area.viewport(), position.toPoint())
            card = next((item for item in column._cards if item.geometry().contains(point)), None)
            if card is not hovered[0]:
                if hovered[0] is not None:
                    QApplication.sendEvent(hovered[0], QEvent(QEvent.Leave))
                if card is not None:
                    p = QPointF(card.rect().center())
                    QApplication.sendEvent(card, QEnterEvent(p, p, QPointF(card.mapToGlobal(p.toPoint()))))
                hovered[0] = card
        wheel = QTimer()
        wheel.setInterval(16)
        wheel.timeout.connect(scroll)
        before = gpu_backend_status().get("frames", 0)
        cpu = time.process_time()
        heartbeat.start()
        wheel.start()
        QTimer.singleShot(1500, loop.quit)
        loop.exec()
        wheel.stop()
        heartbeat.stop()
        gaps = [(b - a) * 1000 for a, b in zip(ticks, ticks[1:])]
        report = {
            "rows_per_column": args.rows,
            "description": "GUI dispatch intervals, not presentation FPS; actual dialog with frozen glass; no filesystem comparison.",
            "median_ms": statistics.median(gaps),
            "p95_ms": sorted(gaps)[math.ceil(len(gaps) * .95) - 1],
            "max_ms": max(gaps), "cpu_ms": (time.process_time() - cpu) * 1000,
            "style_changes": recorder.styles, "paints": recorder.paints,
            "gpu_frames": gpu_backend_status().get("frames", 0) - before,
            "scroll_value": area.verticalScrollBar().value(),
            "scroll_maximum": area.verticalScrollBar().maximum(),
            "labels": [dialog._col_a._tab1.text(), dialog._col_a._tab2.text()],
        }
        app.removeEventFilter(recorder)
        dialog.grab().save(str(output / "scrolled.png"))
        (output / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        dialog.hide()
        overlay._final_close()
        dialog.deleteLater()
        root.hide()
        root.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
