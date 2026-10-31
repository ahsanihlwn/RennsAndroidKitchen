# SPDX-License-Identifier: MPL-2.0
"""Hidden picker construction must never probe unavailable drives on the UI."""
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QObject,QTimer
from PySide6.QtTest import QTest
from ui.file_picker import RenxecPicker


class FilePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate():
            self.assertLess(time.monotonic(),deadline,'Picker job did not finish')
            QTest.qWait(5)
            time.sleep(.001)

    def test_hidden_getter_does_not_access_disks(self):
        owner=QObject()
        with patch('ui.file_picker._quick_access',side_effect=AssertionError('Filesystem probe in hidden picker')), \
             patch('ui.file_picker._get_drives',side_effect=AssertionError('Drive probe in hidden picker')):
            picker=RenxecPicker(owner)
            self.assertEqual(picker.locations,[])
            self.assertEqual(picker.property('locations'),[])
            picker.close()
        owner.deleteLater()

    def test_slow_location_discovery_keeps_gui_responsive(self):
        owner=QObject();picker=RenxecPicker(owner)
        entered=threading.Event();release=threading.Event();threads=[];ticks=[]
        def slow_locations():
            threads.append(threading.get_ident());entered.set()
            if not release.wait(5):raise TimeoutError('Fixture drive remained blocked')
            return [('Home','C:/fixture')]
        timer=QTimer(owner);timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(True))
        try:
            with patch('ui.file_picker._quick_access',slow_locations),patch('ui.file_picker._get_drives',return_value=['C:\\']):
                picker.refreshLocations();timer.start()
                self.wait(entered.is_set);self.wait(lambda:len(ticks)>=5)
                self.assertEqual(picker.locations,[])
                self.assertNotEqual(threads,[threading.get_ident()])
                release.set();self.wait(lambda:not picker._jobs.busy())
                self.assertEqual(picker.locations[1]['name'],'Home')
                self.assertEqual(picker.locations[-1]['path'],'C:\\')
        finally:
            release.set();self.wait(lambda:not picker._jobs.busy())
            timer.stop();picker.close();owner.deleteLater()

    def test_windows_drive_list_does_not_stat_any_volume(self):
        from core.file_picker import _get_drives
        with patch('core.file_picker.sys.platform','win32'), \
             patch('core.file_picker.os.listdrives',return_value=['C:\\','Z:\\']), \
             patch('core.file_picker.os.path.exists',side_effect=AssertionError('Unavailable drive probe')):
            self.assertEqual(_get_drives(),['C:\\','Z:\\'])


if __name__=='__main__':unittest.main()
