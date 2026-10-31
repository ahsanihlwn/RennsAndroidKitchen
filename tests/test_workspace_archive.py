# SPDX-License-Identifier: MPL-2.0
import bz2
from compression.zstd import ZstdError
import gzip
import io
import lzma
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import zstandard
from PySide6.QtCore import QCoreApplication

from ui.archive import extract_archive, list_archive
from ui.workspace_tasks import WorkspaceTasks


class WorkspaceArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'output'
        self.payload = b'\0' * 4096 + b'ANDROID_IMAGE_CONTENT' * 100000

    def zst(self, name='vendor.img.zst', payload=None):
        path = self.root / name
        compressor = zstandard.ZstdCompressor(write_checksum=True, write_content_size=False)
        path.write_bytes(compressor.compress(self.payload if payload is None else payload))
        return path

    def test_zero_prefixed_compressed_images_are_files_instead_of_empty_tar(self):
        compressors = {'.zst': lambda value: zstandard.ZstdCompressor().compress(value),
                       '.gz': gzip.compress, '.bz2': bz2.compress, '.xz': lzma.compress}
        for extension, compress in compressors.items():
            with self.subTest(extension=extension):
                source = self.root / ('vendor.img' + extension)
                source.write_bytes(compress(self.payload))
                self.assertTrue(tarfile.is_tarfile(source), 'Fixture must reproduce false TAR detection')
                rows = list_archive(source)
                self.assertEqual([row['name'] for row in rows], ['vendor.img'])
                self.assertEqual(extract_archive(source, self.output), (True, str(self.output)))
                self.assertEqual((self.output / 'vendor.img').read_bytes(), self.payload)

    def test_zstd_handles_unknown_content_size_concatenated_frames_and_uppercase_suffix(self):
        source = self.zst('vendor.img.ZSTD')
        source.write_bytes(source.read_bytes() + zstandard.ZstdCompressor().compress(b'SECOND_FRAME'))
        extract_archive(source, self.output)
        self.assertEqual((self.output / 'vendor.img').read_bytes(), self.payload + b'SECOND_FRAME')

    def test_truncated_zst_fails_without_leaving_partial_image_or_replacing_previous_output(self):
        source = self.zst()
        source.write_bytes(source.read_bytes()[:-8])
        self.output.mkdir()
        existing = self.output / 'vendor.img'
        for previous in (None, b'previous image'):
            with self.subTest(previous=previous):
                if previous is not None:
                    existing.write_bytes(previous)
                with self.assertRaises(EOFError):
                    extract_archive(source, self.output)
                self.assertEqual(list(self.output.glob('.rak*extract-*.part')), [])
                if previous is None:
                    self.assertFalse(existing.exists())
                else:
                    self.assertEqual(existing.read_bytes(), previous)

    def test_checksum_failure_is_not_reported_as_a_successful_empty_archive(self):
        source = self.zst()
        compressed = bytearray(source.read_bytes())
        compressed[-1] ^= 0xff
        source.write_bytes(compressed)
        with self.assertRaises(ZstdError):
            extract_archive(source, self.output)
        self.assertFalse((self.output / 'vendor.img').exists())
        self.assertEqual(list(self.output.glob('.rak*extract-*.part')), [])

    def test_staging_file_stays_internal_until_the_completed_image_is_published(self):
        import shutil
        source = self.zst()
        copy = shutil.copyfileobj
        observed = []
        def inspect_copy(stream, target, length):
            temporary = Path(target.name)
            observed.append(temporary)
            self.assertTrue(temporary.name.startswith('.rak_'))
            self.assertTrue(temporary.exists())
            self.assertFalse((self.output / 'vendor.img').exists())
            copy(stream, target, length)
        with patch('ui.archive.shutil.copyfileobj', side_effect=inspect_copy):
            extract_archive(source, self.output)
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())
        self.assertEqual((self.output / 'vendor.img').read_bytes(), self.payload)

    def test_tar_zst_still_extracts_its_members(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            info = tarfile.TarInfo('folder/notes.txt')
            info.size = len(self.payload)
            archive.addfile(info, io.BytesIO(self.payload))
        source = self.zst('firmware.tar.zst', buffer.getvalue())
        self.assertEqual([row['name'] for row in list_archive(source)], ['folder/notes.txt'])
        extract_archive(source, self.output)
        self.assertEqual((self.output / 'folder/notes.txt').read_bytes(), self.payload)

    def test_background_queue_records_corrupt_zst_as_failure_with_reason(self):
        source = self.zst()
        source.write_bytes(source.read_bytes()[:-8])
        tasks = WorkspaceTasks()
        received = []
        try:
            tasks.enqueue('Extract Here', lambda: extract_archive(source, self.output),
                          lambda ok, value: received.append((ok, value)))
            deadline = time.monotonic() + 5
            while not received and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(received)
            self.assertFalse(received[0][0])
            history = tasks.loading.get_history()
            self.assertFalse(history[-1]['ok'])
            self.assertTrue(history[-1]['detail'])
            self.assertEqual(tasks.state, 'error')
            self.assertFalse((self.output / 'vendor.img').exists())
        finally:
            tasks.close()


if __name__ == '__main__':
    unittest.main()
