# SPDX-License-Identifier: MPL-2.0
"""Capture the current UI and its geometry before the Qt Quick migration."""
import argparse
import ast
from collections import Counter
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def source_inventory(repo):
    files = [repo / "app.py", repo / "main.py"]
    for folder in ("app_ext", "main_ext", "windows", "core", "rennsproject"):
        files.extend((repo / folder).rglob("*.py"))
    sources, classes, types = [], [], Counter()
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        module = ast.parse(text)
        widgets = [alias.name for node in ast.walk(module)
                   if isinstance(node, ast.ImportFrom) and node.module == "PySide6.QtWidgets"
                   for alias in node.names]
        if not widgets:
            continue
        relative = path.relative_to(repo).as_posix()
        sources.append({"path": relative, "lines": len(text.splitlines()), "widgets": widgets})
        types.update(widgets)
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(base) for base in node.bases]
                if any(base in widgets for base in bases):
                    classes.append({"path": relative, "class": node.name,
                                    "line": node.lineno, "bases": bases})
    return {"sources": sources, "classes": classes, "widget_types": dict(types)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("build/qtquick_migration/baseline"))
    parser.add_argument("--workspace", action="store_true", help="Also capture the real editor shell with synthetic file rows")
    parser.add_argument("--disks", action="store_true", help="Include local volumes and cached WSL usage in the appearance reference")
    parser.add_argument("--source-root", type=Path, help="Capture a separate original checkout without modifying its sources")
    parser.add_argument("--size", default="1080x700")
    parser.add_argument("--palette", help="Use a palette for capture without changing saved settings")
    parser.add_argument("--language", help="Use a language for capture without changing saved settings")
    parser.add_argument("--exposed", action="store_true", help="Capture visible composited windows and the production popup backdrop")
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    repo = args.source_root.resolve() if args.source_root else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    sys.dont_write_bytecode = True
    os.chdir(repo)
    artifacts.mkdir(parents=True, exist_ok=True)
    inventory = source_inventory(repo)
    (artifacts / "sources.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QPoint, QEvent, QCoreApplication, QUrl
    from PySide6.QtWidgets import QApplication, QWidget, QAbstractButton, QLabel, QLineEdit, QScrollArea
    from PySide6.QtTest import QTest
    try:
        from core.skia_button import _source_sheet
    except ImportError:
        _source_sheet = lambda widget: widget.styleSheet()
    from app import AppUI
    from main_ext.class_ThemeUi import theme

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    from main_ext.class_ThemeUi import _PALETTES
    from core import ui_lang
    # Inject captured preferences into loads; never call the persistence setters.
    original_load=theme.load
    original_language_init=ui_lang.init
    def load_theme():
        original_load()
        if args.palette:
            theme._name=args.palette
            theme._apply_palette(_PALETTES[args.palette])
    def load_language():
        original_language_init()
        if args.language:ui_lang._current_lang=args.language
    with patch.object(theme,'load',load_theme),patch.object(ui_lang,'init',load_language):
        ui = AppUI(offscreen_mode=True)
    ui.resize(*map(int, args.size.lower().split("x")))
    ui.setAttribute(Qt.WA_DontShowOnScreen, not args.exposed)
    if args.exposed:
        ui.setWindowFlag(Qt.WindowStaysOnTopHint)
        ui.move(app.primaryScreen().availableGeometry().topLeft())
    ui.show()
    QTest.qWait(700)
    ui._trash_entries = []
    ui._trash_view = False
    ui._storage_available = lambda _: True
    rows = [{"project_name": f"Firmware project {index:02d}",
             "path": f"C:/__rak_visual_fixture__/project_{index:02d}", "port1": "system",
             "port2": "vendor" if index % 2 else "", "pinned": index < 2}
            for index in range(12)]
    ui._all_projects = rows
    ui._render_projects(rows)
    with patch.object(theme, "project_drive_enabled", return_value=True):
        ui._filter_projects("")

    drive_rows = []
    if args.disks:
        from core.disk_monitor import list_windows_disk_usage
        from core.wsl_manager import load_disk_usage_cache
        windows, wsl = list_windows_disk_usage(), load_disk_usage_cache()
        with patch.object(theme, "_animations_enabled", False):
            ui._sync_project_disk_cards(windows, special=False)
            ui._sync_project_disk_cards(wsl, special=True)
            ui._update_project_drive_layout(animate=False)
        for row in windows + wsl:
            entry = ui._project_drive_cards[row["key"]]
            special = bool(row.get("distro"))
            percent = row.get("percent", int(100 * row["used"] / row["total"]))
            drive_rows.append({**row, "distro": row.get("distro", ""), "active": theme.project_drive_enabled(row["key"]),
                               "name": "WSL · " + row["distro"] if special else row["key"],
                               "usage": f"{row['used'] / 1024**3:.1f} / {row['total'] / 1024**3:.1f} GB",
                               "fraction": min(1., max(0., percent / 100)), "percent": percent,
                               "fill": entry["fill"], "barColor": entry["bar_color"]})

    from core import ui_lang
    profile = {}
    if getattr(ui, "account", None):
        profile = {"name": ui.account.get("username", ""),
                   "subtitle": ui.account.get("employee_status", "")}
        from rennsproject.avatar.handler import avatar_circular_pixmap
        avatar = artifacts / "avatar.png"
        avatar_circular_pixmap(ui.account.get("avatar", "000"), ui.account, 100).save(str(avatar))
        profile["avatar"] = QUrl.fromLocalFile(str(avatar)).toString()
    fixture = {"palette": theme.name, "language": ui_lang._current_lang,
               "profile": profile, "location": ui.path_label.text(),
               "settings": {"effects": getattr(theme, "effect_level", "medium" if theme.blur_enabled else "low"), "animations": theme.animations_enabled, "treeMode": theme.tree_view_mode},
               "projects": [{**row, "parentPath": os.path.dirname(row["path"]),
                             "sizeLabel": ui_lang.t("lbl_size") + "Calculating..."} for row in rows]}
    fixture["drives"] = drive_rows
    (artifacts / "fixture.json").write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")

    def widgets(root):
        result = []
        def walk(widget, route):
            if widget.isVisibleTo(root):
                point = widget.mapTo(root, QPoint())
                item = {"path": route, "class": type(widget).__name__, "name": widget.objectName(),
                        "rect": [point.x(), point.y(), widget.width(), widget.height()],
                        "local_rect": list(widget.geometry().getRect()),
                        "contents": list(widget.contentsRect().getRect()),
                        "font": widget.font().toString(), "stylesheet": _source_sheet(widget),
                        "enabled": widget.isEnabled(), "tooltip": widget.toolTip()}
                if isinstance(widget, (QAbstractButton, QLabel, QLineEdit)):
                    item["text"] = widget.text()
                if isinstance(widget, QLineEdit):
                    item["placeholder"] = widget.placeholderText()
                    item["cursor_rect"] = list(widget.cursorRect().getRect())
                if isinstance(widget, QAbstractButton):
                    item.update(checked=widget.isChecked(), checkable=widget.isCheckable(),
                                icon_size=[widget.iconSize().width(), widget.iconSize().height()])
                if widget.layout() is not None:
                    layout = widget.layout()
                    margins = layout.contentsMargins()
                    item["layout"] = {"class": type(layout).__name__, "spacing": layout.spacing(),
                                      "margins": [margins.left(), margins.top(), margins.right(), margins.bottom()]}
                result.append(item)
            for index, child in enumerate(widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)):
                walk(child, route + "/" + str(index))
        walk(root, "root")
        return result

    captures = []
    def capture(name, root):
        root.ensurePolished()
        app.processEvents()
        root.grab().save(str(artifacts / f"{name}.png"))
        if args.exposed:
            origin=root.mapToGlobal(QPoint())
            root.screen().grabWindow(0,origin.x(),origin.y(),root.width(),root.height()).save(str(artifacts/f"{name}.screen.png"))
        tree = widgets(root)
        (artifacts / f"{name}.json").write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
        captures.append({"name": name, "size": [root.width(), root.height()], "widgets": len(tree)})

    try:
        with patch.object(theme, "_animations_enabled", False):
            for name, page in (("projects", ui.page_projects), ("create", ui.page_create), ("settings", ui.page_settings)):
                ui.stack.setCurrentWidget(page)
                QTest.qWait(200)
                capture(name, ui)
                scroll = page.findChild(QScrollArea)
                if scroll is not None and scroll.verticalScrollBar().maximum():
                    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                    QTest.qWait(100)
                    capture(name + "_bottom", ui)
                    scroll.verticalScrollBar().setValue(0)
            ui.stack.setCurrentWidget(ui.page_create)
            for button in ui.page_create.findChildren(QAbstractButton):
                if button.text().lstrip("\u2b24 ") == ui_lang.t("btn_dual_rom").lstrip("\u2b24 "):
                    button.click()
                    break
            QTest.qWait(100)
            capture("create_dual", ui)
            from windows.theme_picker import ThemePickerDialog
            dialog = ThemePickerDialog(ui)
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            dialog.show()
            QTest.qWait(150)
            capture("theme_picker", dialog)
            dialog.hide()
            dialog.deleteLater()
            ui.stack.setCurrentWidget(ui.page_projects)
            from app_ext.class_CreateProjectDialog import CreateProjectDialog
            if args.exposed:
                CreateProjectDialog.show_popup(ui)
                popup=ui._create_project_dialog
                popup.setWindowFlag(Qt.WindowStaysOnTopHint)
                popup.show()
            else:
                popup = CreateProjectDialog(ui, as_popup=True)
                popup.setAttribute(Qt.WA_DontShowOnScreen)
                popup._position_near_button()
                popup.show()
            QTest.qWait(150)
            capture("create_popup", popup)
            popup.input_name.setText("Redmi Note 12")
            QTest.qWait(50)
            capture("create_popup_typed", popup)
            popup.input_name.clear()
            popup._port_mode = 2
            popup._apply_port_mode()
            QTest.qWait(100)
            capture("create_popup_dual", popup)
            if args.exposed:
                popup.reject()
                QTest.qWait(200)
            else:
                popup.hide()
                popup.deleteLater()
            if args.workspace:
                from types import SimpleNamespace
                from main import Main
                from PySide6.QtWidgets import QTreeWidgetItem
                from main_ext.class_NameBadgeDelegate import SIZE_LABEL_ROLE, IS_FOLDER_ROLE
                profile_provider = SimpleNamespace(build_profile_mini=lambda: QWidget())
                # Keep service startup patched until the queued constructor
                # callback has run. The real toolbar/splitters/trees remain.
                with patch.object(Main, "_build_after_init"), patch.object(Main, "_start_private_features"):
                    workspace = Main(profile_provider=profile_provider)
                    workspace.resize(*map(int,args.size.lower().split("x")))
                    workspace.tree.base = "C:/__rak_visual_fixture__"
                    for index in range(20):
                        item = QTreeWidgetItem([f"partition_{index:02d}.img", "u:object_r:system_file:s0", "rw-r--r--"])
                        item.setData(0, Qt.UserRole, workspace.tree.base + f"/{index}.img")
                        item.setData(0, IS_FOLDER_ROLE, False)
                        item.setData(0, SIZE_LABEL_ROLE, "340.0 MB")
                        item.setIcon(0, workspace.tree.icon_file)
                        workspace.tree.addTopLevelItem(item)
                    workspace.show()
                    QTest.qWait(150)
                    capture("workspace", workspace)
                    workspace.hide()
                    workspace.abort_project_preparation()
                    workspace.deleteLater()
        summary = {"source_root": str(repo), "theme": theme.name, "effect_level": getattr(theme, "effect_level", "medium" if theme.blur_enabled else "low"),
                   "dpr": ui.devicePixelRatioF(), "captures": captures,
                   "source_files": len(inventory["sources"]), "classes": len(inventory["classes"]),
                   "source_lines": sum(item["lines"] for item in inventory["sources"])}
        (artifacts / "manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        ui.hide()
        ui.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
