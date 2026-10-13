# SPDX-License-Identifier: MPL-2.0
"""Measure the real launcher splash/reveal using synthetic projects and no services."""
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
from contextlib import ExitStack
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=128)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--cold-start", action="store_true", help="Construct the actual splash-first AppUI; bypass external IO only")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject, QEvent, QEventLoop, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from app import AppUI
    import app as launcher
    launcher._queue_crash_dialog = lambda text: None
    sys.excepthook = sys.__excepthook__
    from core.skia_gpu import gpu_backend_status
    from main_ext.class_ThemeUi import theme

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    rows = [{"project_name": f"Firmware project {n:04d}",
             "path": f"C:/__rak_startup_fixture__/project_{n:04d}",
             "port1": "system", "port2": "vendor" if n % 2 else "", "pinned": n < 3}
            for n in range(args.rows)]

    from app_ext.class_StartupVisual import StartupVisual
    isolation = ExitStack()
    slow_grabs = []
    if args.profile:
        original_grab = QWidget.grab
        def tracked_grab(widget, *params):
            began = time.perf_counter()
            result = original_grab(widget, *params)
            elapsed = (time.perf_counter()-began)*1000
            if elapsed > 25:
                slow_grabs.append({"type":type(widget).__name__, "name":widget.objectName(),
                                   "text":widget.text()[:80] if hasattr(widget, "text") else "",
                                   "font":widget.font().families(), "ms":elapsed})
            return result
        isolation.enter_context(patch.object(QWidget, "grab", new=tracked_grab))
    if args.cold_start:
        # Plain functions avoid changing QObject's dynamic slot metadata with
        # unittest Mock objects. No worker, account request or project IO runs.
        for name in ("_refresh_wsl_disks", "_refresh_windows_disks", "_start_startup_cache"):
            isolation.enter_context(patch.object(AppUI, name, new=lambda self: None))
        def empty_trash(self):
            self._trash_read_error = False
            return []
        isolation.enter_context(patch.object(AppUI, "_read_trash_entries", new=empty_trash))
        original_build = AppUI._build_ui_steps
        slow_steps = []
        def fixture_build(self):
            steps = original_build(self)
            while True:
                step_started = time.perf_counter()
                try:
                    next(steps)
                except StopIteration:
                    break
                step_ms = (time.perf_counter() - step_started) * 1000
                if step_ms > 25:
                    slow_steps.append({"line": steps.gi_frame.f_lineno, "ms": step_ms})
                yield
            self._storage_available = lambda _: True
            self._all_projects = rows
            self._render_projects(rows)
            self.stack.setCurrentWidget(self.page_projects if rows else self.page_create)
        isolation.enter_context(patch.object(AppUI, "_build_ui_steps", new=fixture_build))
    build_started = time.perf_counter()
    ui = AppUI(startup_splash=True) if args.cold_start else AppUI(offscreen_mode=True)
    build_ms = (time.perf_counter() - build_started) * 1000
    ui._offscreen_mode = True
    fixture_disks = [{"key":"C:", "total":237*1024**3, "used":211*1024**3},
                     {"key":"D:", "total":1863*1024**3, "used":1456*1024**3}]
    ui._windows_disk_usage = fixture_disks
    if not args.cold_start:
        ui._startup_splash = StartupVisual(ui)
        ui._startup_splash.setGeometry(ui.rect())
        ui._startup_splash.show()
    ui.setAttribute(Qt.WA_DontShowOnScreen)
    ui.show()
    if args.cold_start:
        QTest.qWait(120)
    ui._disk_poll_timer.stop()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    class Recorder(QObject):
        def __init__(self):
            super().__init__()
            self.counts = {}
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Paint and isinstance(obj, QWidget):
                key = type(obj).__name__
                self.counts[key] = self.counts.get(key, 0) + 1
            return False
    recorder = Recorder()
    app.installEventFilter(recorder)
    report = {"rows": args.rows, "build_ms": build_ms, "description": "Actual AppUI with synthetic projects; no service/network/project writes. Intervals measure GUI dispatch, not display FPS.", "cases": {}}

    def measure(name, action, duration):
        loop = QEventLoop()
        ticks = [time.perf_counter()]
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(16)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        recorder.counts = {}
        before = gpu_backend_status().get("frames", 0)
        cpu = time.process_time()
        profiler = cProfile.Profile() if args.profile else None
        timer.start()
        started = time.perf_counter()
        if profiler:
            profiler.enable()
        if name == "build":
            ui.startup_built.connect(loop.quit)
            ui._startup_ui_built = True
        animation_start = []
        if name == "reveal":
            ui.startup_animation_started.connect(lambda: animation_start.append((time.perf_counter(), gpu_backend_status().get("frames",0))))
        action()
        action_ms = (time.perf_counter() - started) * 1000
        frames_after_prepare = gpu_backend_status().get("frames", 0)
        clock_started = time.perf_counter()
        active_end = []
        if name == "reveal":
            ui.startup_revealed.connect(loop.quit)
            ui.startup_revealed.connect(lambda: active_end.append((time.perf_counter(), gpu_backend_status().get("frames",0))))
        QTimer.singleShot(30000 if name == "build" else 10000 if name == "reveal" else duration, loop.quit)
        loop.exec()
        if name == "build":
            ui.startup_built.disconnect(loop.quit)
        if profiler:
            profiler.disable()
            output = io.StringIO()
            pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(45)
            (artifacts / f"{name}_profile.txt").write_text(output.getvalue(), encoding="utf-8")
        timer.stop()
        gaps = [(b-a)*1000 for a,b in zip(ticks,ticks[1:])]
        report["cases"][name] = {"action_ms": action_ms, "cpu_ms": (time.process_time()-cpu)*1000,
                                 "ticks": len(gaps), "median_ms": statistics.median(gaps),
                                 "p95_ms": sorted(gaps)[math.ceil(len(gaps)*.95)-1], "max_ms": max(gaps),
                                 "elapsed_ms": (time.perf_counter() - started)*1000,
                                 "preparation_readbacks": frames_after_prepare-before,
                                 "post_preparation_readbacks": gpu_backend_status().get("frames",0)-frames_after_prepare,
                                 "readback_gpu_frames": gpu_backend_status().get("frames",0)-before,
                                 "paints": recorder.counts}
        if active_end:
            if animation_start:
                clock_started, frames_after_prepare = animation_start[0]
                report["cases"][name]["preparation_ms"] = (clock_started-started)*1000
            active_gaps = [(b-a)*1000 for a,b in zip(ticks,ticks[1:]) if a >= clock_started and b <= active_end[0][0]]
            report["cases"][name]["animation"] = {"p95_ms": sorted(active_gaps)[math.ceil(len(active_gaps)*.95)-1],
                                                   "max_ms": max(active_gaps), "ticks": len(active_gaps),
                                                   "readbacks": active_end[0][1] - frames_after_prepare}

    try:
        # The cold build includes the production deferred popup/settings warmup.
        def build():
            if args.cold_start:
                ui._finish_startup_build()
                return
            ui._sync_project_disk_cards(fixture_disks, special=False)
            ui._storage_available = lambda _: True
            ui._all_projects = rows
            ui._render_projects(rows)
            with patch.object(theme, "project_drive_enabled", return_value=True):
                ui._filter_projects("")
            ui.stack.setCurrentWidget(ui.page_projects if rows else ui.page_create)
        measure("build", build, 1)
        measure("reveal", ui.reveal_startup, 1250)
        ui.grab().save(str(artifacts / "completed.png"))
        report["reveal_finished"] = ui._startup_revealed_emitted
        report["backend"] = gpu_backend_status()
        if args.cold_start:
            report["slow_build_steps"] = slow_steps
        if args.profile:
            report["slow_grabs"] = slow_grabs
        (artifacts / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        app.removeEventFilter(recorder)
        for name in ("_startup_content_reveal", "_startup_transition"):
            reveal = getattr(ui, name, None)
            if reveal is not None:
                reveal.stop()
        ui.hide()
        ui.deleteLater()
        app.processEvents()
        isolation.close()


if __name__ == "__main__":
    main()
