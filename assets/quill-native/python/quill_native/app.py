from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Avoid a Python paint callback competing with the GUI thread for the GIL.
# This keeps Qt Quick native rendering; it does not force software rendering.
os.environ.setdefault("QSG_RENDER_LOOP", "basic")


def create_window(app, path=""):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from .editor import NativeEditor

    qmlRegisterType(NativeEditor, "Quill.Native", 1, 0, "NativeEditor")
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Main.qml"))))
    if not engine.rootObjects():
        raise RuntimeError("Qt Quick interface could not be loaded")
    window = engine.rootObjects()[0]
    editor = window.findChild(NativeEditor, "nativeEditor")
    if path:
        editor.loadPath(path)
    return engine, window, editor


def main(argv=None):
    start = time.perf_counter()
    parser = argparse.ArgumentParser(description="Quill Native — Rust + PySide6 Qt Quick editor")
    parser.add_argument("file", nargs="?", default="")
    parser.add_argument("--probe", action="store_true", help="Print process diagnostics and exit after first frames")
    parser.add_argument("--capture", default="", help="Save a local UI image for development QA")
    args = parser.parse_args(argv)
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Quill Native")
    app.setOrganizationName("Quill")
    app.setQuitOnLastWindowClosed(True)
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle("Basic")
    engine, window, editor = create_window(app, args.file)
    first_frame = []
    def frame_ready():
        if not first_frame:
            first_frame.append(round((time.perf_counter() - start) * 1000, 1))
    window.frameSwapped.connect(frame_ready)
    if args.probe or args.capture:
        def probe():
            metrics = editor.diagnostics()
            metrics["first_frame_ms"] = first_frame[0] if first_frame else None
            metrics["observation_ms"] = round((time.perf_counter() - start) * 1000, 1)
            metrics["backend"] = os.environ.get("QT_QUICK_BACKEND", "platform default")
            try:
                import psutil
                process = psutil.Process()
                metrics["rss_mib"] = round(process.memory_info().rss / 1024**2, 2)
                metrics["child_processes"] = len(process.children(recursive=True))
            except ImportError:
                pass
            if args.capture:
                window.grabWindow().save(args.capture)
            print(json.dumps(metrics))
            if args.probe:
                app.quit()
        QTimer.singleShot(800, probe)
    result = app.exec()
    del engine
    return result
