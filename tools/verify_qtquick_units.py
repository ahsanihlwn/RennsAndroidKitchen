# SPDX-License-Identifier: MPL-2.0
"""Run migration units and collect Qt-owned objects before shutting Qt down."""
import gc
from pathlib import Path
import sys
import unittest


def main():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication,QEvent
    app=QApplication([])
    modules=['project_metrics','path_index','quick_directory','quick_cli','quick_mounts','kitchen_conversions',
        'workspace_files','workspace_transfer','workspace_context','workspace_search','workspace_tasks',
        'editor_document','hex_document','partition_viewer','so_document',
        'qtquick_presentation','qtquick_sessions','tree_entries','import_gate',
        'quick_project_store','quick_rows','quick_file_picker']
    suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_'+name for name in modules])
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    del suite
    for _ in range(3):
        app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);gc.collect()
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
