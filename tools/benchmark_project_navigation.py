# SPDX-License-Identifier: MPL-2.0
"""Measure actual App project scrolling and profile/page navigation, without writes."""
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
    parser.add_argument("--rows", type=int, default=128)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--exposed-probe", action="store_true")
    parser.add_argument("--capture-animation", action="store_true", help="Visual probe only; captures add work and invalidate timing comparisons")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject, QEvent, QEventLoop, QTimer, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from app import AppUI
    from core.skia_gpu import gpu_backend_status
    from main_ext.class_ThemeUi import theme
    from unittest.mock import patch

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    ui = AppUI(offscreen_mode=True)
    if args.exposed_probe:
        ui.setWindowFlag(Qt.Tool)
        ui.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        ui.setAttribute(Qt.WA_ShowWithoutActivating)
    else:
        ui.setAttribute(Qt.WA_DontShowOnScreen)
    ui.show()
    QTest.qWait(1000)
    ui._trash_entries = []
    ui._trash_view = False
    ui._storage_available = lambda _: True
    rows = [{"project_name": f"Firmware project {n:04d}",
             "path": f"C:/__rak_navigation_fixture__/project_{n:04d}",
             "port1": "system", "port2": "vendor" if n % 2 else "", "pinned": n < 3}
            for n in range(args.rows)]
    ui._all_projects = rows
    ui._render_projects(rows)
    with patch.object(theme, "project_drive_enabled", return_value=True):
        ui._filter_projects("")
    ui.stack.setCurrentWidget(ui.page_projects)
    ui._settings_back_target = ui.page_projects
    QTest.qWait(1500)

    class Recorder(QObject):
        def __init__(self):
            super().__init__()
            self.enabled = False
            self.counts = {}
        def eventFilter(self, obj, event):
            if self.enabled and event.type() == QEvent.Paint and isinstance(obj, QWidget):
                key = type(obj).__name__
                self.counts[key] = self.counts.get(key, 0) + 1
            return False
    recorder = Recorder()
    app.installEventFilter(recorder)
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    report = {"rows": args.rows, "description": "Actual App event loop; native fonts and controls; no per-frame grabs. Gaps are GUI dispatch, not screen FPS.", "cases": {}}

    def measure(name, action, duration=1200):
        loop = QEventLoop()
        heartbeat = QTimer()
        heartbeat.setTimerType(Qt.PreciseTimer)
        heartbeat.setInterval(16)
        ticks = [time.perf_counter()]
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        finish = QTimer()
        finish.setSingleShot(True)
        finish.timeout.connect(loop.quit)
        profiler = cProfile.Profile() if args.profile else None
        recorder.counts = {}
        recorder.enabled = True
        before = gpu_backend_status().get("frames", 0)
        cpu = time.process_time()
        started = time.perf_counter()
        heartbeat.start()
        finish.start(duration)
        if profiler:
            profiler.enable()
        action()
        if args.capture_animation and name.startswith("to_"):
            for delay in (100, 220, 360):
                QTimer.singleShot(delay, ui, lambda at=delay: ui.grab().save(str(artifacts / f"{name}_{at}.png")))
        loop.exec()
        if profiler:
            profiler.disable()
        recorder.enabled = False
        heartbeat.stop()
        elapsed = (time.perf_counter() - started) * 1000
        gaps = [(b-a)*1000 for a,b in zip(ticks,ticks[1:])]
        result = {"elapsed_ms": elapsed, "cpu_ms": (time.process_time()-cpu)*1000,
                  "ticks": len(gaps), "median_ms": statistics.median(gaps),
                  "p95_ms": sorted(gaps)[math.ceil(len(gaps)*.95)-1], "max_ms": max(gaps),
                  "readback_gpu_frames": gpu_backend_status().get("frames", 0)-before,
                  "paints": recorder.counts}
        if profiler:
            output = io.StringIO()
            pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(35)
            (artifacts / (name+"_profile.txt")).write_text(output.getvalue(), encoding="utf-8")
        report["cases"][name] = result
        ui.grab().save(str(artifacts / (name+".png")))
        QTest.qWait(150)

    try:
        bar = ui.list_widget.verticalScrollBar()
        report["scroll_maximum"] = bar.maximum()
        def scroll():
            viewport = ui.list_widget.viewport()
            position = QPointF(viewport.rect().center())
            global_position = QPointF(viewport.mapToGlobal(position.toPoint()))
            for index in range(12):
                def wheel():
                    event = QWheelEvent(position, global_position, QPoint(), QPoint(0,-120),
                                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
                    QApplication.sendEvent(viewport, event)
                QTimer.singleShot(index*50, ui, wheel)
        measure("scroll", scroll)
        report["scroll_after"] = bar.value()
        bar.setValue(0)
        QTest.qWait(100)
        first = ui.list_widget.item(0)
        ui._project_hover_effect.set_hovered_item(first)
        QTest.qWait(200)
        measure("scroll_hovered", scroll)
        ui._project_hover_effect.reset()
        bar.setValue(0)
        QTest.qWait(100)
        measure("to_settings", lambda: ui._show_page(ui.page_settings))
        measure("to_projects", lambda: ui._show_page(ui.page_projects))
        report["backend"] = gpu_backend_status()
        (artifacts / "timings.json").write_text(json.dumps(report,indent=2), encoding="utf-8")
        print(json.dumps(report,indent=2))
    finally:
        recorder.enabled = False
        app.removeEventFilter(recorder)
        ui.hide()
        ui.deleteLater()
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
