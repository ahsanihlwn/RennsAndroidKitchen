# SPDX-License-Identifier: MPL-2.0
"""Probe actual OpenWithDialog clipping with repeated in-memory option fixtures."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, default=70)
    parser.add_argument("--parent-height", type=int, default=760)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    if args.options < 1 or args.parent_height < 300:
        parser.error("options must be positive and parent-height at least 300")
    root_path = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_path))
    os.chdir(root_path)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QObject, QEvent, QPoint, QPointF, QCoreApplication
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget, QLabel
    from main_ext.class_OpenWithDialog import OpenWithDialog
    from app_ext.class_BlurOverlay import BlurOverlay, install_window_fade
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from core.skia_widgets import install_widget_gpu_rendering
    from core.skia_gpu import gpu_backend_status
    from main_ext.class_ThemeUi import theme

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    install_button_press_animations(app)
    install_widget_gpu_rendering(app)
    root = QWidget()
    root.setAttribute(Qt.WA_DontShowOnScreen)
    root.setGeometry(100, 30, 1000, args.parent_height)
    root.setStyleSheet(theme.app_stylesheet())
    for index in range(30):
        label = QLabel(f"system_ext_hwservice_contexts_{index:03}", root)
        label.setGeometry(20, 15+index*24, 600, 24)
    root.show()
    options = (OpenWithDialog._OPTIONS * ((args.options+6)//7))[:args.options]
    with patch.object(OpenWithDialog, "_OPTIONS", options):
        dialog = OpenWithDialog(root, "system_ext_hwservice_contexts")
    dialog.setAttribute(Qt.WA_DontShowOnScreen)
    for index, card in enumerate(dialog._cards):
        caption = card.findChildren(QLabel)[-1]
        caption.setText(f"{caption.text()} · {index+1}")
    overlay = BlurOverlay(root)
    dialog._overlay = overlay
    install_window_fade(dialog)
    dialog.show()
    QTest.qWait(450)
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    top = dialog.grab().toImage()
    top.save(str(output / "top.png"))
    report = {
        "options": len(dialog._cards), "max_visible": dialog._MAX_VISIBLE_OPTIONS,
        "dialog_size": [dialog.width(), dialog.height()],
        "viewport_size": [dialog._scroll.viewport().width(), dialog._scroll.viewport().height()],
        "scroll_maximum": dialog._scroll.verticalScrollBar().maximum(),
        "transparent_outer_corner": top.pixelColor(0, 0).alpha() == 0,
        "blur_matches_card": dialog._blur_surface.geometry() == dialog._card.geometry(),
        "window_within_bounds": dialog._available_bounds().contains(dialog.geometry()),
        "description": "Repeated known app options in memory; no preferences/file operations; no FPS measurement.",
    }
    class Counter(QObject):
        def __init__(self):
            super().__init__()
            self.painted = set()
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Paint:
                self.painted.add(dialog._cards.index(obj))
            return False
    counter = Counter()
    for card in dialog._cards:
        card.installEventFilter(counter)
    before = gpu_backend_status().get("frames", 0)
    pos = QPointF(dialog._scroll.viewport().rect().center())
    for _ in range(8):
        wheel = QWheelEvent(pos, QPointF(dialog._scroll.viewport().mapToGlobal(pos.toPoint())),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.ScrollUpdate, False)
        QApplication.sendEvent(dialog._scroll.viewport(), wheel)
        QTest.qWait(50)
    QTest.qWait(260)
    report["wheel_painted_unique_options"] = len(counter.painted)
    report["wheel_gpu_frames"] = gpu_backend_status().get("frames", 0)-before
    dialog.grab().save(str(output / "scrolled.png"))
    dialog._scroll.verticalScrollBar().setValue(dialog._scroll.verticalScrollBar().maximum())
    QTest.qWait(50)
    dialog.grab().save(str(output / "bottom.png"))
    report["backend"] = gpu_backend_status()
    (output / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    dialog.hide()
    overlay._final_close()
    dialog.deleteLater()
    root.hide()
    root.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return 0 if all(report[key] for key in ("transparent_outer_corner", "blur_matches_card", "window_within_bounds")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
