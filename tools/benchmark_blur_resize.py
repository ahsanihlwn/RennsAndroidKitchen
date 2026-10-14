# SPDX-License-Identifier: MPL-2.0
"""Measure blur source/paint cost during resize, without presented-FPS claims."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


def stats(values):
    values = sorted(values)
    return {"median_ms": statistics.median(values),
            "p95_ms": values[max(0, math.ceil(len(values)*.95)-1)], "max_ms": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtCore import Qt, QTimer, QEventLoop, QCoreApplication, QEvent
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget, QLabel
    from PySide6.QtTest import QTest
    from main_ext.class_ThemeUi import theme
    from core.skia_gpu import gpu_available, gpu_backend_status
    from app_ext.class_BlurOverlay import BlurOverlay, BlurCard
    from core.fonts import apply_app_font
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    if not gpu_available():
        raise RuntimeError(gpu_backend_status())
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    class Root(QWidget):
        def paintEvent(self, _):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#345c80"))
            painter.fillRect(0, 0, self.width()//2, self.height(), QColor("#976d35"))
            painter.end()

    def run(widget, root, scene, name):
        QTest.qWait(250)
        before = gpu_backend_status()
        begin = time.perf_counter()
        calls, gaps, texture_sizes, steps = [], [], [], []
        last = [begin]
        count = [0]
        loop = QEventLoop()
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(16)
        def tick():
            now = time.perf_counter()
            gaps.append((now-last[0])*1000)
            last[0] = now
            i = count[0]
            start = time.perf_counter()
            root.resize(930+round(230*math.sin(i/18)), 610+round(90*math.sin(i/20)))
            resized = time.perf_counter()
            if root is scene.root:
                # Outside the desktop, force the source paint that a visible
                # resized window would receive from its native backing store.
                root.repaint()
            painted = time.perf_counter()
            frame = widget.grab()
            end = time.perf_counter()
            calls.append((end-start)*1000)
            steps.append({"step": i, "resize_ms": (resized-start)*1000,
                          "source_paint_ms": (painted-resized)*1000,
                          "blur_paint_ms": (end-painted)*1000})
            texture_sizes.append([scene.image.width(), scene.image.height()])
            count[0] += 1
            if count[0] == 90:
                timer.stop()
                frame.save(str(artifacts/f"{name}_during_drag.png"))
                loop.quit()
        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        during = gpu_backend_status()
        # Wait for final capture/handoff and check the timer is then idle.
        QTest.qWait(700)
        widget.grab().save(str(artifacts/f"{name}.png"))
        final = gpu_backend_status()
        return {"resize_and_blur_paint": stats(calls), "dispatch": stats(gaps),
                "slowest_step": steps[calls.index(max(calls))],
                "blur_updates_during_drag": during["blur_frames"]-before["blur_frames"],
                "blur_updates_after_drag": final["blur_frames"]-during["blur_frames"],
                "gpu_composite_frames_during_drag": during["frames"]-before["frames"],
                "distinct_backdrop_sizes_during_drag": len({tuple(size) for size in texture_sizes}),
                "capture_finished": scene._capture_jobs is None,
                "final_texture_logical_size": [scene.image.deviceIndependentSize().width(), scene.image.deviceIndependentSize().height()],
                "final_root_size": [scene.root.width(), scene.root.height()]}

    report = {"scope": "Qt resize/blur work in an off-desktop fixture, not presented FPS"}
    with patch.object(theme, "_effect_level", "high"), patch.object(theme, "_blur_enabled", True):
        root = Root()
        root.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        root.setAttribute(Qt.WA_ShowWithoutActivating)
        root.setGeometry(-20000, -20000, 1000, 720)
        for i in range(100):
            label = QLabel(f"folder_{i:03} / system_ext / native Qt font", root)
            label.setGeometry(12+(i%3)*310, 12+(i//3)*18, 300, 18)
            label.setStyleSheet("background:transparent; color:white; font-size:11px;")
        root.show()
        overlay = BlurOverlay(root, animate=False, source_widget=root, backdrop="blur", sync_blur=True)
        report["source_root"] = run(overlay, root, overlay._live_backdrop, "root_resized")
        overlay._final_close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        root.resize(1000, 720)
        # A card-only resize leaves its source root unchanged.
        shell = QWidget(root, Qt.Tool | Qt.FramelessWindowHint)
        shell.setAttribute(Qt.WA_TranslucentBackground)
        shell.setGeometry(-19800, -19800, 500, 350)
        card = BlurCard(shell, corner=14)
        card.setGeometry(shell.rect())
        shell.show()
        card.show()
        card.freeze_from_widget(root, card.mapToGlobal(card.rect().topLeft()))
        # Keep card geometry synchronized as the shell changes size.
        original_resize = shell.resizeEvent
        shell.resizeEvent = lambda event: (original_resize(event), card.setGeometry(shell.rect()))
        report["card_only"] = run(card, shell, card._live_backdrop, "card_resized")
        shell.hide()
        shell.deleteLater()
        root.hide()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        report["backend"] = gpu_backend_status()
    (artifacts/"checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
