# SPDX-License-Identifier: MPL-2.0
"""Measure real reveal timer/paint events with a large FileTree (no project writes)."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=12000)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--window-opacity", action="store_true")
    parser.add_argument("--actual-main", action="store_true", help="Construct the real Main UI; bypass project/service IO only")
    parser.add_argument("--profile-prepare", action="store_true", help="Profile preparation only; use separate runs for timing comparisons")
    parser.add_argument("--pause-icons", action="store_true")
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--verify-webengine", action="store_true")
    parser.add_argument("--hidden-entry", action="store_true")
    parser.add_argument("--mapped-offscreen", action="store_true", help="Expose Qt's RHI window outside the desktop, without taking focus")
    parser.add_argument("--exposed-probe", action="store_true", help="Use a native tool window without activation for WebEngine/RHI checks")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    if args.disable_gpu:
        os.environ["RAK_DISABLE_SKIA_GPU"] = "1"
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QTimer, QEventLoop, QEvent, QCoreApplication, QObject, QSignalBlocker, QPersistentModelIndex
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTreeWidgetItem, QPushButton
    from PySide6.QtTest import QTest
    from app_ext.class_StartupVisual import StartupContentReveal
    from app_ext.class_WindowContentReveal import WindowContentReveal
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from core.skia_widgets import install_widget_gpu_rendering
    from core.skia_gpu import gpu_backend_status
    from main_ext.class_ThemeUi import theme
    from main_ext.class_FileTree import FileTree
    from main_ext.class_NameBadgeDelegate import SIZE_LABEL_ROLE, IS_FOLDER_ROLE
    from main_ext.FileTree_ext.DirectoryLoading import ICON_PENDING_ROLE, ENTRY_SOURCE_ROLE, ENTRY_FINGERPRINT_ROLE

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    theme.load()
    install_button_press_animations(app)
    install_widget_gpu_rendering(app)
    if args.actual_main:
        from unittest.mock import patch
        from main import Main
        from types import SimpleNamespace
        profile = SimpleNamespace(build_profile_mini=lambda: QWidget())
        # Keep the production toolbar, splitters, tabs, trees and editor. Only
        # service startup/project IO is bypassed; all fixture rows are synthetic.
        with patch.object(Main, "_build_after_init"), patch.object(Main, "_start_private_features"):
            root = Main(profile_provider=profile)
        root.setAttribute(Qt.WA_DontShowOnScreen, False)
    else:
        root = QWidget()
    if args.mapped_offscreen or args.exposed_probe:
        root.setWindowFlag(Qt.Tool)
        root.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        root.setAttribute(Qt.WA_ShowWithoutActivating)
        if args.exposed_probe:
            root.move(app.primaryScreen().availableGeometry().topLeft())
        else:
            root.move(-20000, -20000)
    else:
        root.setAttribute(Qt.WA_DontShowOnScreen)
    root.resize(1080, 700)
    root.setStyleSheet(f"background-color:{theme.BG};")
    layout = root.layout() if args.actual_main else QVBoxLayout(root)
    header = QHBoxLayout()
    for label in (() if args.actual_main else ("Return", "Project", "Tools", "Settings")):
        button = QPushButton(label)
        button.setStyleSheet(f"QPushButton {{background:{theme.BG3};color:{theme.FG};border:1px solid #59606b;border-radius:6px;padding:7px 14px;}}")
        header.addWidget(button)
    if not args.actual_main:
        layout.addLayout(header)
    tree = root.tree if args.actual_main else FileTree()
    tree.base = "mount://entry-benchmark"
    if not args.actual_main:
        layout.addWidget(tree)
    rows = []
    with QSignalBlocker(tree):
        for number in range(args.rows):
            item = QTreeWidgetItem([f"partition_{number:05d}_firmware.img", "u:object_r:system_file:s0", "rw-r--r--"])
            path = tree.base + f"/{number}.img"
            item.setData(0, Qt.UserRole, path)
            item.setData(0, IS_FOLDER_ROLE, False)
            item.setData(0, SIZE_LABEL_ROLE, "340.0 MB")
            item.setData(0, ENTRY_SOURCE_ROLE, path)
            item.setData(0, ENTRY_FINGERPRINT_ROLE, str(number))
            item.setData(0, Qt.UserRole + 1, "EROFS" if number % 3 == 0 else "")
            item.setIcon(0, tree.icon_file)
            rows.append(item)
        tree.addTopLevelItems(rows)
    if not args.hidden_entry:
        root.show()
        QTest.qWait(250)
    editor = None
    if args.verify_webengine:
        from main_ext.class_WebEditorPanel import WebEditorPanel
        editor = root.unified_tabs._editor_panel if args.actual_main else WebEditorPanel()
        if not args.actual_main:
            layout.addWidget(editor)
        editor.hide()
        editor.prepare()
        deadline = time.monotonic() + 15
        while not editor.is_ready() and time.monotonic() < deadline:
            QTest.qWait(25)
        if not editor.is_ready():
            raise RuntimeError("The native Monaco editor did not become ready")

    class Recorder(QObject):
        def __init__(self):
            super().__init__()
            self.tree_paints = 0
            self.reveal_paints = 0
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Paint:
                if obj is tree.viewport():
                    self.tree_paints += 1
                else:
                    self.reveal_paints += 1
            return False

    recorder = Recorder()
    tree.viewport().installEventFilter(recorder)
    reports = []
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        for run in range(args.runs):
            tree._reset_icon_work()
            with QSignalBlocker(tree):
                for number, item in enumerate(rows):
                    item.setData(0, ICON_PENDING_ROLE, True)
                    path = item.data(0, Qt.UserRole)
                    task = (QPersistentModelIndex(tree.indexFromItem(item)), tree._icon_generation,
                            tree.base, path, str(number), {"path": path, "symlink": None})
                    tree._icon_queue.append(task)
            tree._start_icon_drain(1)
            if args.pause_icons:
                tree.set_entry_transition_active(True)
            options = {"cover_until_start": True}
            if args.snapshot:
                options["freeze_content"] = True
            reveal = WindowContentReveal(root) if args.window_opacity else StartupContentReveal(root, **options)
            prepare_started = time.perf_counter()
            profiler = None
            if args.profile_prepare:
                import cProfile
                profiler = cProfile.Profile()
                profiler.enable()
            if args.window_opacity:
                preparation = QEventLoop()
                reveal.prepared.connect(preparation.quit)
                reveal.prepare()
                preparation.exec()
            elif args.snapshot:
                reveal.prepare_snapshot()
            prepare_ms = (time.perf_counter() - prepare_started) * 1000
            if profiler is not None:
                profiler.disable()
                import pstats
                import io
                output = io.StringIO()
                pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(40)
                (artifacts / f"prepare_{run}.txt").write_text(output.getvalue(), encoding="utf-8")
            recorder.tree_paints = recorder.reveal_paints = 0
            reveal.installEventFilter(recorder)
            ticks = []
            animation_started = []
            if args.window_opacity:
                reveal._animation.valueChanged.connect(lambda _: ticks.append(time.perf_counter()))
            else:
                original_clock = reveal._begin_clock
                def begin_clock():
                    original_clock()
                    if reveal._started_at is not None and not animation_started:
                        animation_started.append(time.perf_counter())
                reveal._begin_clock = begin_clock
                original_tick = reveal._tick
                reveal._timer.timeout.disconnect()
                def tick():
                    ticks.append(time.perf_counter())
                    original_tick()
                reveal._timer.timeout.connect(tick)
            loop = QEventLoop()
            reveal.finished.connect(loop.quit)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            timeout.start(10000)
            cpu = time.process_time()
            direct_frame = getattr(reveal, "_gpu_frame", None)
            gpu_before = gpu_backend_status().get("frames", 0)
            show_started = time.perf_counter()
            root.show()
            show_ms = (time.perf_counter() - show_started) * 1000
            started = time.perf_counter()
            reveal.start()
            loop.exec()
            elapsed = (time.perf_counter() - started) * 1000
            clock_started = animation_started[0] if animation_started else started
            gaps = [(b - a) * 1000 for a, b in zip([clock_started] + ticks, ticks)]
            reports.append({"run": run, "prepare_ms": prepare_ms, "show_ms": show_ms, "elapsed_ms": elapsed,
                "cpu_ms": (time.process_time() - cpu) * 1000,
                "presentation_wait_ms": (clock_started - started) * 1000,
                "ticks": len(ticks), "spacing_median_ms": statistics.median(gaps),
                "spacing_p95_ms": sorted(gaps)[math.ceil(len(gaps) * .95)-1],
                "spacing_max_ms": max(gaps), "tree_paints": recorder.tree_paints,
                "reveal_paints": recorder.reveal_paints,
                "direct_gpu_frames": direct_frame.frames if direct_frame is not None else 0,
                "direct_gpu_error": direct_frame.error if direct_frame is not None else getattr(reveal, "_snapshot_gpu_error", None),
                "readback_gpu_frames": gpu_backend_status().get("frames", 0) - gpu_before,
                "icons_remaining": len(tree._icon_queue)})
            timeout.stop()
            if args.pause_icons:
                tree.set_entry_transition_active(False)
            tree._reset_icon_work()
            QTest.qWait(100)
        root.grab().save(str(artifacts / "tree.png"))
        report = {"description": "Real event-loop reveal: 1080x700, cached Qt rows plus pending file icon work; no forced grabs during animation",
                  "rows": args.rows, "freeze_content": args.snapshot, "window_opacity": args.window_opacity, "actual_main": args.actual_main,
                  "pause_icons": args.pause_icons, "runs": reports, "backend": gpu_backend_status()}
        report["native_window_exposed"] = bool(root.windowHandle() and root.windowHandle().isExposed())
        if editor is not None:
            if args.actual_main:
                root.right_wrap.show()
            tree.hide()
            editor.show()
            text = "# Native Monaco after Skia entry\nprint('Qt WebEngine is still alive')\n"
            editor.bridge.open_virtual("entry-probe.py", text, "python", True)
            QTest.qWait(500)
            values = []
            editor.view.page().runJavaScript("JSON.stringify({value:editor.getValue(),lines:document.querySelectorAll('.view-line').length})", values.append)
            deadline = time.monotonic() + 5
            while not values and time.monotonic() < deadline:
                QTest.qWait(25)
            state = json.loads(values[0]) if values else {}
            if state.get("value") != text or not state.get("lines"):
                raise RuntimeError(f"Monaco did not render the virtual test file: {state}")
            editor.view.grab().save(str(artifacts / "webeditor.png"))
            report["webengine_after_entry"] = state
        (artifacts / "timings.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if editor is not None:
            editor.view.stop()
        tree.stop_background_work()
        root.hide()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        if editor is not None:
            QTest.qWait(100)
        print("Entry probe cleanup complete", flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        import tempfile
        detail = traceback.format_exc()
        Path(tempfile.gettempdir(), "rak_entry_probe_error.txt").write_text(detail, encoding="utf-8")
        print(detail, file=sys.__stderr__, flush=True)
        raise
