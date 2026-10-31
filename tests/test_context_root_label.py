from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.context_patcher import ContextPatcher


class RootContextTests(unittest.TestCase):
    def patcher(self, base, root_context):
        cp = ContextPatcher.__new__(ContextPatcher)
        cp.base = str(base)
        cp.cfgdir = str(base / "config")
        cp.ctx_map = {"/system_debug": root_context} if root_context else {}
        cp._load_all_neocat_cache = lambda: {}
        cp.detect_domain_by_majority = lambda root: "system"
        cp._has_priv_app_ctx = lambda root: True
        return cp

    def test_scan_preserves_sar_root_despite_system_children(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "system_debug").mkdir()
            cp = self.patcher(base, "u:object_r:rootfs:s0")
            result = cp.scan_tree("system_debug", mode="ai", debug=False)
        self.assertEqual(result["system_debug_file_contexts"][0][1], "u:object_r:rootfs:s0")

    def test_old_cached_prediction_cannot_replace_explicit_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "config").mkdir()
            config = base / "config/system_debug_file_contexts"
            config.write_text("/system_debug u:object_r:rootfs:s0\n", encoding="utf-8")
            cp = self.patcher(base, "u:object_r:rootfs:s0")
            with mock.patch.object(cp, "is_context_locked", return_value=False):
                cp.apply_results({"system_debug_file_contexts": [
                    ("/system_debug", "u:object_r:system_file:s0", "cached", "old-cache")
                ]})
            self.assertEqual(config.read_text(), "/system_debug u:object_r:rootfs:s0\n")

    def test_missing_root_still_receives_inferred_label(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "system_debug").mkdir()
            cp = self.patcher(base, None)
            result = cp.scan_tree("system_debug", mode="ai", debug=False)
        self.assertEqual(result["system_debug_file_contexts"][0][1], "u:object_r:system_file:s0")


if __name__ == "__main__":
    unittest.main()
