# SPDX-License-Identifier: MPL-2.0
"""Measure QWidget painting in the working repo without activating controls.

Native Windows: python tools/benchmark_ui_gpu.py --profile --exercise
Qt reference: add --disable-gpu. Optional --artifacts stores PNGs, layout/font
inventories and timings. This measures grab + event dispatch, not presentation
FPS; the hidden window preserves native GL/font support. No backup is imported.
"""
from __future__ import annotations

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


def _stats(samples, cpu, before, after):
    ordered = sorted(samples)
    return {
        "samples": len(samples), "median_ms": statistics.median(samples),
        "p95_ms": ordered[max(0, math.ceil(len(samples) * .95) - 1)],
        "max_ms": max(samples), "cpu_seconds": cpu,
        "gpu_frames": after.get("frames", 0) - before.get("frames", 0),
        "texture_uploads": after.get("uploads", 0) - before.get("uploads", 0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=45)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--page", choices=("projects", "settings", "create", "all"), default="all")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2")
    root_path = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_path))
    os.chdir(root_path)
    if args.disable_gpu:
        os.environ["RAK_DISABLE_SKIA_GPU"] = "1"

    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QWidget, QLabel, QScrollArea
    from PySide6.QtTest import QTest
    from app import AppUI
    from core.skia_gpu import gpu_backend_status
    from core.skia_button import _source_sheet
    from main_ext.class_ThemeUi import theme

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    ui = AppUI(offscreen_mode=True)
    ui.setAttribute(Qt.WA_DontShowOnScreen)
    ui.show()
    QTest.qWait(1250)
    artifact_dir = args.artifacts.resolve() if args.artifacts else None
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "description": "Hidden native QWidget.grab() plus processEvents; not presentation FPS",
        "size": [ui.width(), ui.height()], "dpr": ui.devicePixelRatioF(),
        "platform": app.platformName(), "profile_enabled": args.profile,
        "animations_enabled": theme.animations_enabled, "blur_enabled": theme.blur_enabled,
        "pages": {},
    }

    def inventory():
        result = []
        def walk(widget, path):
            if widget.isVisible():
                result.append({
                    "path": path, "type": type(widget).__name__, "name": widget.objectName(),
                    "geometry": list(widget.geometry().getRect()),
                    "contents": list(widget.contentsRect().getRect()),
                    "font": widget.font().toString(),
                    "text": widget.text() if isinstance(widget, QLabel) else None,
                    "stylesheet": _source_sheet(widget),
                })
            for index, child in enumerate(widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)):
                walk(child, path + "/" + str(index))
        walk(ui, "root")
        return result

    def measure(change=None):
        samples = []
        before = gpu_backend_status()
        profile = cProfile.Profile() if args.profile else None
        cpu_start = time.process_time()
        if profile:
            profile.enable()
        for index in range(args.iterations):
            started = time.perf_counter()
            if change:
                change(index)
            ui.grab()
            app.processEvents()
            samples.append((time.perf_counter() - started) * 1000)
        if profile:
            profile.disable()
        cpu = time.process_time() - cpu_start
        after = gpu_backend_status()
        result = _stats(samples, cpu, before, after)
        if profile:
            buffer = io.StringIO()
            pstats.Stats(profile, stream=buffer).sort_stats("cumulative").print_stats(20)
            result["profile"] = buffer.getvalue()
        return result

    def paced(change):
        # A 16 ms timer gives Qt time to dispatch between changes. Its observed
        # spacing catches main-thread stalls hidden by a tight grab-only loop.
        loop = QEventLoop()
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(16)
        intervals, work = [], []
        previous = None
        index = 0
        before = gpu_backend_status()
        cpu_started = time.process_time()
        def tick():
            nonlocal previous, index
            started = time.perf_counter()
            if previous is not None:
                intervals.append((started - previous) * 1000)
            previous = started
            change(index)
            ui.grab()
            work.append((time.perf_counter() - started) * 1000)
            index += 1
            if index >= args.iterations:
                timer.stop()
                loop.quit()
        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        result = _stats(work, time.process_time() - cpu_started, before, gpu_backend_status())
        result["timer_spacing_median_ms"] = statistics.median(intervals)
        result["timer_spacing_p95_ms"] = sorted(intervals)[max(0, math.ceil(len(intervals) * .95) - 1)]
        result["timer_spacing_max_ms"] = max(intervals)
        result["profile_enabled"] = False
        return result

    try:
        for name in ("projects", "settings", "create"):
            if args.page not in (name, "all"):
                continue
            page = getattr(ui, "page_" + name)
            ui.stack.setCurrentWidget(page)
            QTest.qWait(1250)
            ui.grab()
            app.processEvents()
            widgets = [ui] + ui.findChildren(QWidget)
            report["pages"][name] = {
                "idle_paint": measure(),
                "visible_graphics_effects": sum(w.isVisible() and w.graphicsEffect() is not None for w in widgets),
                "total_graphics_effects": sum(w.graphicsEffect() is not None for w in widgets),
                "prepared_vector_surfaces": sum(w.isVisible() and hasattr(w, "_skia_surface_original") for w in widgets),
            }
            if artifact_dir:
                ui.grab().save(str(artifact_dir / (name + ".png")))
                (artifact_dir / (name + "_layout.json")).write_text(json.dumps(inventory(), indent=2, ensure_ascii=False), encoding="utf-8")
            if args.exercise and name == "settings":
                areas = [w for w in page.findChildren(QScrollArea) if w.isVisible()]
                if areas:
                    bar = max((area.verticalScrollBar() for area in areas), key=lambda w: w.maximum())
                    value = bar.value()
                    report["pages"][name]["scroll_paint"] = measure(
                        lambda index: bar.setValue(round(bar.maximum() * index / (args.iterations - 1))))
                    bar.setValue(value)
                    app.processEvents()
                    report["pages"][name]["paced_scroll"] = paced(
                        lambda index: bar.setValue(round(bar.maximum() * index / (args.iterations - 1))))
                    bar.setValue(value)
                    app.processEvents()
                button = ui._project_mode_button
                if areas:
                    areas[0].ensureWidgetVisible(ui._language_button)
                    app.processEvents()
                    QTest.qWait(100)
                if artifact_dir:
                    ui.grab().save(str(artifact_dir / "settings_dropdown.png"))
                    button.grab().save(str(artifact_dir / "mode_dropdown.png"))
                    ui._language_button.grab().save(str(artifact_dir / "language_dropdown.png"))
                controller = getattr(button, "_button_press_feedback", None)
                if controller and controller._ensure_effect():
                    try:
                        report["pages"][name]["press_scale_paint"] = measure(
                            lambda index: setattr(controller._effect, "scale", 1.0 - .04 * index / (args.iterations - 1)))
                        report["pages"][name]["paced_press_scale"] = paced(
                            lambda index: setattr(controller._effect, "scale", 1.0 - .04 * index / (args.iterations - 1)))
                        if artifact_dir:
                            button.grab().save(str(artifact_dir / "mode_pressed.png"))
                            ui.grab().save(str(artifact_dir / "settings_pressed.png"))
                    finally:
                        controller.reset()
                        app.processEvents()
                # Exercise the real source capture, blur, opening and closing
                # paths. Closing without picking an option leaves preferences
                # untouched. The timer records dispatch stalls during animation.
                from app_ext.class_ChoiceMorphPopup import ChoiceMorphPopup
                popup = ChoiceMorphPopup(ui, button, (("single_dir", "Single Dir"), ("tree", "Tree")), theme.tree_view_mode)
                popup.setAttribute(Qt.WA_DontShowOnScreen)
                heartbeat = QTimer()
                heartbeat.setTimerType(Qt.PreciseTimer)
                heartbeat.setInterval(16)
                gaps = []
                capture_spans = []
                popup_capture = []
                last_tick = [time.perf_counter()]
                def beat():
                    now = time.perf_counter()
                    gaps.append((last_tick[0], now, (now - last_tick[0]) * 1000))
                    last_tick[0] = now
                heartbeat.timeout.connect(beat)
                def capture_popup():
                    if artifact_dir and popup.isVisible():
                        started = time.perf_counter()
                        popup_capture.append(popup.grab())
                        capture_spans.append((started, time.perf_counter()))
                QTimer.singleShot(400, capture_popup)
                QTimer.singleShot(600, popup.close)
                before = gpu_backend_status()
                heartbeat.start()
                selected = popup.exec()
                heartbeat.stop()
                # Snapshot verification is outside the normal interaction;
                # don't attribute its grab/PNG writing to dispatch latency.
                observed = [gap for start, end, gap in gaps
                            if not any(start < finish and end > begin for begin, finish in capture_spans)]
                report["pages"][name]["popup_dispatch"] = {
                    "selected": selected,
                    "timer_spacing_median_ms": statistics.median(observed) if observed else None,
                    "timer_spacing_p95_ms": sorted(observed)[max(0, math.ceil(len(observed) * .95) - 1)] if observed else None,
                    "timer_spacing_max_ms": max(observed) if observed else None,
                    "excluded_snapshot_intervals": len(gaps) - len(observed),
                    "gpu_frames": gpu_backend_status().get("frames", 0) - before.get("frames", 0),
                }
                if popup_capture:
                    popup_capture[0].save(str(artifact_dir / "mode_popup.png"))
        report["backend"] = gpu_backend_status()
        # Keep useful data even if a native renderer fails a frame.
        if artifact_dir:
            (artifact_dir / "timings.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        ui.hide()
        ui.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
