# SPDX-License-Identifier: MPL-2.0
"""Exercise the real launcher page handoff, without projects/services/network."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=60)
    parser.add_argument("--exposed-probe", action="store_true")
    parser.add_argument("--profile-refresh", action="store_true")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, qInstallMessageHandler, QCoreApplication, QEvent, QPoint
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from app import AppUI
    from shiboken6 import isValid

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    errors, warnings = [], []
    sys.excepthook = lambda kind, value, tb: errors.append(str(value))
    previous = qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    ui = AppUI(offscreen_mode=True)
    if args.exposed_probe:
        ui.setWindowFlag(Qt.Tool)
        ui.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        ui.setAttribute(Qt.WA_ShowWithoutActivating)
        ui.move(-20000, -20000)
    else:
        ui.setAttribute(Qt.WA_DontShowOnScreen)
    ui.show()
    QTest.qWait(800)
    ui._trash_entries = []
    ui._storage_available = lambda _: True
    rows = [{"project_name": f"Project {n}", "port1": "system", "port2": "vendor", "path": f"C:/__rak_navigation_fixture__/{n}"} for n in range(8)]
    ui._all_projects = rows
    ui._render_projects(rows)
    QTest.qWait(800)
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    checks = []
    try:
        for index in range(args.cycles * 2):
            page = ui.page_settings if index % 2 == 0 else ui.page_projects
            ui._show_page(page)
            if args.profile_refresh and ui.profile_provider is not None:
                ui.badge_icon_path = "v1" if index % 3 else None
                ui.verified_level = "core" if ui.badge_icon_path else None
                ui.is_verified = ui.verified_level is not None
                ui.profile_provider.update_badge(ui.badge_icon_path, ui.verified_level)
                ui.refresh_profile_mini()
                ui._refresh_settings_badge()
            # Immediate changes, interrupted animations, then fully completed
            # animations cover the ways real users can leave a page.
            QTest.qWait((0, 16, 80, 240, 800)[index % 5])
            checks.append({"index": index, "page": ui.stack.currentIndex(),
                           "updates_enabled": page.updatesEnabled(), "visible": page.isVisible()})
        QTest.qWait(1400)
        ui._show_page(ui.page_settings)
        QTest.qWait(1200)
        ui.grab().save(str(artifacts / "settings.png"))
        back = ui.btn_back_settings
        point = back.mapTo(ui, QPoint())
        back_bounds = [point.x(), point.y(), back.width(), back.height()]
        ui._show_page(ui.page_projects)
        QTest.qWait(1200)
        ui.grab().save(str(artifacts / "projects.png"))
        overlays = [widget.objectName() for widget in ui.findChildren(QWidget)
                    if widget.isVisible() and widget.objectName() in ("appPageTransition", "profileSharedTransition", "footerActionTransition")]
        report = {"cycles": args.cycles, "errors": errors, "warnings": warnings,
                  "visible_overlays": overlays, "scene_active": ui._page_content_reveal is not None,
                  "cached_transition_active": getattr(ui, "_cached_page_transition_active", False),
                  "window_size": [ui.width(), ui.height()], "settings_back_bounds": back_bounds, "checks": checks}
        (artifacts / "checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key:value for key,value in report.items() if key != "checks"}, indent=2), flush=True)
        if errors or overlays or report["scene_active"] or not all(c["updates_enabled"] and c["visible"] for c in checks):
            raise RuntimeError("Page navigation did not restore the live UI")
    finally:
        qInstallMessageHandler(previous)
        ui._clear_page_content_reveal()
        ui.hide()
        ui.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
