import tempfile
import unittest
from compression import zstd
from pathlib import Path
from unittest import mock

from core import magiskboot_tree as mbt


class RamdiskZstandardTests(unittest.TestCase):
    def test_concatenated_vendor_ramdisk_frames_are_all_preserved(self):
        first, second = b"070701normal-fragment", b"070701recovery-fragment"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ramdisk.cpio"
            target = Path(directory) / "ramdisk.dec"
            source.write_bytes(zstd.compress(first) + zstd.compress(second))
            with mock.patch.object(mbt, "run", side_effect=AssertionError("unsupported external decoder")):
                compression = mbt.decompress_ramdisk_component(source, target, Path("unused"))
            self.assertEqual(compression, "zstd")
            self.assertEqual(target.read_bytes(), first + second)

    def test_raw_cpio_is_copied_without_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "entry.comp"
            target = Path(directory) / "entry.cpio"
            source.write_bytes(b"070701raw-cpio")
            with mock.patch.object(mbt, "run", side_effect=AssertionError("raw input needs no decoder")):
                self.assertEqual(mbt.decompress_ramdisk_component(source, target, Path("unused")), "raw")
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_corrupt_zstandard_is_reported_without_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "entry.comp"
            target = Path(directory) / "entry.cpio"
            source.write_bytes(b"\x28\xb5\x2f\xfdbroken")
            with mock.patch("logging.exception") as report:
                with self.assertRaises(mbt.ToolError):
                    mbt.decompress_ramdisk_component(source, target, Path("unused"))
            report.assert_called_once()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
