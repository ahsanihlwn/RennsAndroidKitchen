import os
import tempfile
import unittest
from unittest import mock

from ui import workspace_tools


class BootRepackDispatchTests(unittest.TestCase):
    def test_boot_repack_uses_cpio_packer_without_filesystem_context_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = os.path.join(directory, "vendor_boot_debug")
            os.mkdir(folder)
            expected = (True, os.path.join(directory, "vendor_boot_debug_new.img"))
            with mock.patch("main_ext.class_NeoBranchManager.NeoBranchManager") as manager, \
                 mock.patch("core.config_engine_pure.ConfigEngine", side_effect=AssertionError("CPIO is not a filesystem partition")), \
                 mock.patch("core.fs_verify.FSVerifier", side_effect=AssertionError("CPIO validation belongs to the boot packer")), \
                 mock.patch("core.boot_image.repack_boot_image", return_value=expected) as pack:
                manager.return_value.get_active_branch.return_value = None
                self.assertEqual(workspace_tools._repack(folder, "repack_boot"), expected)
            pack.assert_called_once_with(folder)


if __name__ == "__main__":
    unittest.main()
