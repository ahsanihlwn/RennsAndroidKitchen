# SPDX-License-Identifier: MPL-2.0
"""Measure native blur and actual Automation glass without executing automation."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


def stats(values):
    values = sorted(values)
    return {"samples": len(values), "median_ms": statistics.median(values),
            "p95_ms": values[max(0, math.ceil(len(values) * .95) - 1)], "max_ms": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QTimer, QPoint, QEventLoop, QEvent, QCoreApplication
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QWidget, QLabel
    from PySide6.QtTest import QTest
    from core.skia_gpu import gpu_backend_status, gpu_available, blur_gpu
    from app_ext import class_BlurOverlay as blur_module
    from core.skia_widgets import install_widget_gpu_rendering
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from app_ext.class_BlurOverlay import BlurOverlay, install_window_fade, _qt_blur_pixmap
    from rennsproject.main_ext.class_AutomationLogWindow import AutomationLogWindow
    from main_ext.class_ThemeUi import theme
    from shiboken6 import isValid

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    start = time.perf_counter()
    if not gpu_available():
        raise RuntimeError(gpu_backend_status())
    report = {"gpu_initialization_ms": (time.perf_counter() - start) * 1000}
    install_widget_gpu_rendering(app)
    install_button_press_animations(app)
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = QPixmap(640, 460)
    source.fill(QColor("#daac6e"))
    painter = QPainter(source)
    painter.fillRect(320, 0, 320, 460, QColor("#264987"))
    painter.end()
    start = time.perf_counter()
    first = blur_gpu(source, 18)
    report["first_gpu_blur_ms"] = (time.perf_counter() - start) * 1000
    first.save(str(output / "gaussian.png"))
    for name, function in (("gpu_gaussian", blur_gpu), ("qt_cpu_fallback", _qt_blur_pixmap)):
        times = []
        for _ in range(30):
            start = time.perf_counter()
            function(source, 18)
            times.append((time.perf_counter() - start) * 1000)
        report[name] = stats(times)

    class Host(QWidget):
        phase = 0
        paints = 0

        def paintEvent(self, event):
            self.paints += 1
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(theme.BG))
            for x in range(0, self.width(), 36):
                for y in range(0, self.height(), 36):
                    if ((x + self.phase) // 36 + y // 36) % 2:
                        painter.fillRect(x, y, 36, 36, QColor("#3f685d"))
            painter.end()

    root = Host()
    root.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
    root.setAttribute(Qt.WA_ShowWithoutActivating)
    root.setGeometry(-20000, -20000, 1080, 740)
    root.setStyleSheet(theme.app_stylesheet())
    for index in range(128):
        label = QLabel(f"system_ext / folder_{index:03} — 340.0 MB", root)
        label.setGeometry(24 + (index % 3) * 348, 14 + (index // 3) * 16, 330, 16)
        label.setStyleSheet("font-size:11px; color:#9bb7c3; background:transparent; border:none;")
    root.show()
    QTest.qWait(300)
    report["levels"] = {}
    report["scope"] = "Qt source/Rust Gaussian dispatch cost; outside desktop, not presented FPS"
    report["qt_images_include_blur"] = True
    with patch.object(blur_module, "_capture_widget_area", wraps=blur_module._capture_widget_area) as captures, \
         patch.object(theme, "_save_settings"), patch.object(theme, "_animations_enabled", False), \
         patch.object(theme, "_blur_enabled", True), patch.object(theme, "_effect_level", "medium"):
        try:
            for level in ("low", "medium", "high"):
                theme.set_effect_level(level)
                captures_before = captures.call_count
                paints_before = root.paints
                overlay = BlurOverlay(root, animate=False, capture_without_backdrop=True)
                dialog = AutomationLogWindow(root)
                dialog.setWindowFlag(Qt.Tool)
                dialog.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
                dialog.setAttribute(Qt.WA_ShowWithoutActivating)
                dialog._overlay = overlay
                dialog.append_log("[INFO] UI benchmark only; no script executed")
                dialog.append_log("[RUN] system_ext: testing live backdrop")
                dialog.mark_finished()
                start = time.perf_counter()
                install_window_fade(dialog, freeze_backdrop_immediately=True)
                dialog.show()
                QTest.qWait(80)
                entry = {"show_and_settle_ms": (time.perf_counter() - start) * 1000,
                         "live": overlay._surface_card._live_backdrop is not None if hasattr(overlay, "_surface_card") else False}
                dialog.grab().save(str(output / f"automation_{level}.png"))
                gaps, moves = [], []
                last = [time.perf_counter()]
                origin = dialog.pos()
                loop = QEventLoop()
                timer = QTimer()
                timer.setTimerType(Qt.PreciseTimer)
                timer.setInterval(16)
                count = [0]
                before = gpu_backend_status()["blur_frames"]

                def tick():
                    now = time.perf_counter()
                    gaps.append((now - last[0]) * 1000)
                    last[0] = now
                    count[0] += 1
                    start = time.perf_counter()
                    dialog.move(origin + QPoint(round(48 * math.sin(count[0] / 20)), 0))
                    moves.append((time.perf_counter() - start) * 1000)
                    if count[0] % 8 == 0:
                        root.phase += 36
                        root.update()
                        root.repaint()
                    dialog.repaint()
                    moves[-1] = (time.perf_counter() - start) * 1000
                    if count[0] >= 120:
                        timer.stop()
                        loop.quit()

                timer.timeout.connect(tick)
                timer.start()
                loop.exec()
                entry["dispatch"] = stats(gaps)
                entry["move_handler"] = stats(moves)
                entry["gpu_blur_updates"] = gpu_backend_status()["blur_frames"] - before
                entry["qt_backdrop_captures"] = captures.call_count-captures_before
                entry["root_paints"] = root.paints-paints_before
                scene = getattr(overlay, "_live_backdrop_scene", None)
                if scene is not None:
                    entry["last_backdrop_timings"] = scene._last_timings
                    entry["label_cache_hits"] = scene._label_hits
                    entry["label_cache_entries"] = len(scene._labels)
                QTest.qWait(120)
                idle = gpu_backend_status()["blur_frames"]
                QTest.qWait(300)
                entry["idle_shader_updates"] = gpu_backend_status()["blur_frames"] - idle
                entry["transparent_outer_corner"] = dialog.grab().toImage().pixelColor(0, 0).alpha() == 0
                entry["native_font_family"] = dialog.view.font().family()
                report["levels"][level] = entry
                dialog.close()
                overlay._final_close()
                if isValid(dialog):
                    dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
            report["backend"] = gpu_backend_status()
            (output / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
        finally:
            root.hide()
            root.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    main()
