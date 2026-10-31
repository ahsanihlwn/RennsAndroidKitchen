# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from core.project_metrics import folder_size_str,format_size,trash_remaining_text,project_drive_fill


class ProjectMetricsTests(unittest.TestCase):
    def test_size_format_and_cancellable_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'child').mkdir();(root/'child'/'a').write_bytes(b'a'*2048)
            self.assertEqual(folder_size_str(root),'2.0 KB')
            self.assertIsNone(folder_size_str(root,lambda:True))
            self.assertEqual(format_size(1024**3),'1.00 GB')

    def test_trash_retains_source_pending_expired_hours_days(self):
        with patch('core.ui_lang.t',side_effect=lambda key,**args:(key,args)):
            self.assertEqual(trash_remaining_text({})[0],'trash_recovery_pending')
            self.assertEqual(trash_remaining_text({'handle':True,'expires_at':0},1)[0],'trash_expired')
            self.assertEqual(trash_remaining_text({'handle':True,'expires_at':3601},0),('trash_hours_left',{'n':2}))
            self.assertEqual(trash_remaining_text({'handle':True,'expires_at':86401},0),('trash_days_left',{'n':2}))

    def test_drive_fill_matches_reference_tint(self):
        theme=SimpleNamespace(BG2='#eeeeee',BG4='#202020',accent_hex='#00ff00',is_light=True)
        self.assertEqual(project_drive_fill(theme),'#eeeeee')
        self.assertEqual(project_drive_fill(theme,True),'#d6f0d6')


if __name__=='__main__':unittest.main()
