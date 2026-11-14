# SPDX-License-Identifier: MPL-2.0
"""Main is exclusively Qt Quick; the launcher and direct callers share one owner."""
from ui.workspace_window import QuickWorkspace
from PySide6.QtCore import Slot


class Main(QuickWorkspace):
    instance = None
    _config_service_ready = False
    _config_service_mgr = None

    def __init__(self, project, p1, p2=None, profile_provider=None, *, preferences=None, parent=None, **options):
        if preferences is None:
            from core import ui_lang
            from main_ext.class_ThemeUi import theme
            from ui.presentation import LauncherModel
            theme.load(); ui_lang.init()
            preferences = LauncherModel(palette=theme.name, language=ui_lang._current_lang,
                settings={"effects":theme.effect_level,"animations":theme.animations_enabled,
                          "treeMode":theme.tree_view_mode},parent=parent)
        self._owned_preferences = preferences
        self._enable_cli=options.pop('enable_cli_services',True)
        super().__init__(project,p1,p2,preferences=preferences,
                         profile_provider=profile_provider,parent=parent,**options)
        Main.instance = self
        from ui.workspace_cli import WorkspaceCli
        self.quick_cli=WorkspaceCli(self.session)

    @Slot()
    def _prepared(self):
        if self._enable_cli:
            from core import rak_cli
            rak_cli.attach(self)
            rak_cli.start_ipc_server()
            rak_cli.ensure_rak_bat()
            rak_cli.maybe_autostart()
        super()._prepared()

    @property
    def tree(self):return self.session.tree
    @property
    def tree2(self):return self.session.tree2 if self.p2 else None
    @property
    def active_tree(self):return self.session.active_tree
    @property
    def task_queue(self):return self.session.task_queue
    @property
    def dev_guard(self):return self.session.privateFeatures.guard

    def _run_automation_exec(self,base,tree,name,path):self.session.privateFeatures.runAutomation(path,base,name)

    def push_automation_log(self,text):self.session.privateFeatures.logLine.emit(str(text))

    def _disposed(self):
        from core import rak_cli
        rak_cli.detach(self)
        if Main.instance is self:Main.instance=None
        super()._disposed()

    @classmethod
    def _ensure_config_service(cls,timeout=10.0):
        from services.process_manager import get_manager
        from main_ext.config_engine_client import ensure_connected
        manager=get_manager()
        manager.start_process()
        ensure_connected(manager.wait_ready(timeout=timeout))
        cls._config_service_mgr=manager
        cls._config_service_ready=True

    @classmethod
    def _shutdown_config_service(cls):
        if cls._config_service_mgr is not None:cls._config_service_mgr.stop()
        cls._config_service_mgr=None
        cls._config_service_ready=False

def run_workspace_standalone(argv=None):
    import sys
    import os
    import argparse
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from ui.runtime import initialize_quick_runtime
    from core.fonts import configure_text_rendering_env, apply_app_font
    from core.error_reporting import install_error_reporter

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=True)
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else [sys.argv[0]] + list(argv))
    apply_app_font(app, polish_widgets=False)
    install_error_reporter()

    parser = argparse.ArgumentParser(description="RAK Workspace Editor")
    parser.add_argument("project", help="Path to project directory")
    parser.add_argument("p1", help="Primary partition")
    parser.add_argument("p2", nargs="?", default=None, help="Secondary partition")
    parser.add_argument("--palette", default=None, help="Theme palette")
    parser.add_argument("--lang", default=None, help="Language code")
    args = parser.parse_args(argv)

    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel

    theme.load()
    ui_lang.init()
    palette = args.palette or theme.name
    lang = args.lang or ui_lang._current_lang

    preferences = LauncherModel(
        palette=palette,
        language=lang,
        settings={
            "effects": theme.effect_level,
            "animations": theme.animations_enabled,
            "treeMode": theme.tree_view_mode
        }
    )

    editor = Main(
        args.project,
        args.p1,
        args.p2,
        preferences=preferences,
        enable_private_services=True,
        enable_cli_services=True
    )

    def on_ready():
        print("[RAK_WORKSPACE_READY]", flush=True)
        editor.reveal()

    def on_return():
        print("[RAK_WORKSPACE_RETURN]", flush=True)
        sys.exit(42)

    def on_quit():
        print("[RAK_WORKSPACE_QUIT]", flush=True)
        sys.exit(0)

    def on_failed(err):
        print(f"[RAK_WORKSPACE_FAILED] {err}", flush=True)
        sys.exit(1)

    editor.ready.connect(on_ready)
    editor.return_to_project_signal.connect(on_return)
    editor.preparation_failed.connect(on_failed)
    editor._shutdown_and_quit = on_quit

    sys.exit(app.exec())


if __name__ == "__main__":
    run_workspace_standalone()
