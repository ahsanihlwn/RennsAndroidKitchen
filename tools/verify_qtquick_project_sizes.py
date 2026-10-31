# SPDX-License-Identifier: MPL-2.0
"""Check production project cards without scanning their contents."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from app import AppUI,_end_startup_import_window
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,Slot
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from core.project_trash import _write
    from ui.project_store import ProjectStore
    import main_ext.class_ThemeUi as theme_module
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix='rak-project-card-sizes-') as temporary:
        root=Path(temporary)
        store=ProjectStore(root/'metadata');store.set_location(str(root/'projects'))
        uncached=store.create({'name':'No cache','port1':'system'})
        cached=store.create({'name':'Cached','port1':'system'})
        cached['cached_size']='3.5 GB'
        _write(store.projects_path,[uncached,cached])
        before=store.projects_path.read_bytes()
        with patch.object(theme_module,'_THEME_FILE',str(root/'theme.json')), \
             patch.object(theme_module,'_SETTINGS_FILE',str(root/'settings.json')), \
             patch.object(ui_lang,'_LANG_FILE',str(root/'language.json')), \
             patch('core.rak_cli.ensure_rak_bat'),patch('core.rak_cli.maybe_autostart'), \
             patch('core.project_metrics.folder_size_str',return_value='unexpected') as scan:
            from ui.application import LauncherApplication
            class IsolatedLauncher(LauncherApplication):
                @Slot()
                def _start_services(self):pass
            # Leave services enabled as in production, but isolate unrelated
            # account/network/prewarm workers from this disk-I/O regression.
            runtime=IsolatedLauncher(app,AppUI,store=store,start_services=True)
            try:
                runtime.model.setStartupActive(False)
                QTest.qWait(250)
                for _ in range(3):
                    runtime._reload();runtime.hide();runtime.show()
                    QTest.qWait(50)
                rows={row['path']:row for row in runtime.model._projects}
                assert rows[uncached['path']]['sizeLabel']==''
                assert rows[cached['path']]['sizeLabel']==ui_lang.t('lbl_size')+'3.5 GB'
                assert store.projects_path.read_bytes()==before,'Reload rewrote project metadata'
                scan.assert_not_called()
                assert not any(thread.name=='rak-quick-project-sizes' for thread in threading.enumerate())
                assert not runtime.engine._diagnostics,runtime.engine._diagnostics
                print(json.dumps({'result':'passed','servicesEnabled':True,'sizeScans':scan.call_count,
                                  'refreshAndReopen':True,'cachedSizesOnly':True,'noCalculatingLabel':True}),flush=True)
            finally:
                QTimer.singleShot(0,runtime.quit);app.exec()
                _end_startup_import_window()
    theme_module.theme.load();ui_lang.init()


if __name__=='__main__':main()
