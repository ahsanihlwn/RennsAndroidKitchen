# SPDX-License-Identifier: MPL-2.0
"""Document behavior must survive moving its view from QWidget to Qt Quick."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.editor_document import EditorBridge


class EditorDocumentTests(unittest.TestCase):
    def test_physical_branch_and_metadata_tabs_save_live_paths_and_reject_stale_branch(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);(base/'vendor').mkdir();(base/'config').mkdir()
            file=base/'vendor/build.prop';file.write_text('source')
            config=base/'config/vendor_file_contexts';config.write_text('source label')
            manager=NeoBranchManager(folder);manager.create_checkout_branch('vendor','port','blue')
            bridge=EditorBridge();failures=[]
            bridge.documentSaveFailed.connect(lambda *args:failures.append(args))
            for path in (file,config):
                with patch.object(bridge,'_resolve_icon_b64',return_value=''):self.assertTrue(bridge.open_file(str(path)))
                bridge.bind_neobranch_tab(str(path),folder,'vendor',str(path),'port')
                bridge.save_file(str(path),'branch '+path.name)
                self.assertEqual(path.read_text(),'branch '+path.name)
            manager.checkout_branch('vendor',None)
            for path in (file,config):bridge.save_file(str(path),'stale buffer')
            self.assertEqual(len(failures),2)
            self.assertEqual(file.read_text(),'source');self.assertEqual(config.read_text(),'source label')
    def test_save_preserves_bom_and_original_line_endings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "build.prop"
            path.write_bytes(b"\xef\xbb\xbfro.device=old\r\nro.product=fixture\r\n")
            bridge = EditorBridge()
            with patch.object(bridge, "_resolve_icon_b64", return_value=""):
                self.assertTrue(bridge.open_file(str(path)))
            bridge.on_content_changed(str(path), "ro.device=new\nro.product=fixture\n")
            self.assertTrue(bridge.has_unsaved())
            bridge.save_file(str(path), bridge._tabs[str(path)]["content"])
            self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfro.device=new\r\nro.product=fixture\r\n")
            self.assertFalse(bridge.has_unsaved())

    def test_virtual_read_only_tab_never_runs_save_callback(self):
        callbacks = []
        bridge = EditorBridge()
        bridge.open_virtual("mount://system/build.prop", "original", read_only=True,
                            on_save=lambda *args: callbacks.append(args), icon_b64="fixture")
        bridge.save_file("mount://system/build.prop", "changed")
        self.assertEqual(callbacks, [])
        self.assertEqual(bridge._tabs["mount://system/build.prop"]["content"], "original")

    def test_pending_documents_flush_once_after_webchannel_is_ready(self):
        bridge = EditorBridge()
        loaded = []
        bridge.fileLoaded.connect(lambda *args: loaded.append(args))
        bridge.open_virtual("fixture.prop", "first", icon_b64="fixture")
        bridge.open_virtual("fixture.prop", "latest", icon_b64="fixture")
        self.assertEqual(loaded, [])
        bridge.on_editor_ready()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0][1], "latest")

    def test_large_file_warning_is_a_signal_without_creating_a_dialog(self):
        bridge = EditorBridge()
        warnings = []
        bridge.warningRaised.connect(lambda *args: warnings.append(args))
        with patch("core.editor_document.os.path.isfile", return_value=True), \
             patch("core.editor_document.os.path.getsize", return_value=400*1024*1024), \
             patch.object(bridge, "_resolve_icon_b64", return_value=""):
            self.assertFalse(bridge.open_file("oversize.img"))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(bridge.tab_count(), 0)


if __name__ == "__main__":
    unittest.main()
