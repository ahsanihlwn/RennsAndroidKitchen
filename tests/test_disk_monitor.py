# SPDX-License-Identifier: MPL-2.0
"""An unresponsive volume must not block healthy drives or multiply workers."""
import threading
import time
import unittest
from collections import namedtuple
from unittest.mock import patch

from core import disk_monitor


Usage = namedtuple('Usage', 'total used free')


class DiskMonitorTests(unittest.TestCase):
    def test_hung_drive_is_skipped_and_its_pending_query_is_reused(self):
        release = threading.Event()
        calls = []
        def usage(path):
            calls.append(path)
            if path == 'E:\\':
                release.wait(5)
            return Usage(100, 40, 60)

        with patch.object(disk_monitor, '_windows_drive_keys', return_value=['C:', 'D:', 'E:']), \
             patch.object(disk_monitor, '_VOLUME_QUERIES', {}), \
             patch.object(disk_monitor.shutil, 'disk_usage', side_effect=usage):
            try:
                started = time.monotonic()
                rows = disk_monitor.list_windows_disk_usage(timeout=.1)
                self.assertEqual([row['key'] for row in rows], ['C:', 'D:'])
                self.assertLess(time.monotonic() - started, 1)
                rows = disk_monitor.list_windows_disk_usage(timeout=.1)
                self.assertEqual([row['key'] for row in rows], ['C:', 'D:'])
                self.assertEqual(calls.count('E:\\'), 1)
                release.set()
                disk_monitor._VOLUME_QUERIES['E:'].result(timeout=1)
                self.assertEqual([row['key'] for row in disk_monitor.list_windows_disk_usage()], ['C:', 'D:', 'E:'])
            finally:
                release.set()
                for query in disk_monitor._VOLUME_QUERIES.values():
                    query.result(timeout=1)

    def test_unreadable_and_empty_volumes_do_not_hide_a_healthy_drive(self):
        def usage(path):
            if path == 'D:\\':
                raise OSError('Volume not ready')
            return Usage(0, 0, 0) if path == 'E:\\' else Usage(100, 40, 60)
        with patch.object(disk_monitor, '_windows_drive_keys', return_value=['C:', 'D:', 'E:']), \
             patch.object(disk_monitor, '_VOLUME_QUERIES', {}), \
             patch.object(disk_monitor.shutil, 'disk_usage', side_effect=usage):
            self.assertEqual(disk_monitor.list_windows_disk_usage(), [{'key': 'C:', 'total': 100, 'used': 40}])

    def test_no_windows_drives_never_starts_a_capacity_query(self):
        with patch.object(disk_monitor, '_windows_drive_keys', return_value=[]), \
             patch.object(disk_monitor.shutil, 'disk_usage') as usage:
            self.assertEqual(disk_monitor.list_windows_disk_usage(), [])
            usage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
