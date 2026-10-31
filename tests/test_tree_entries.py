# SPDX-License-Identifier: MPL-2.0
"""Regressions for the shared directory row builder used at project startup."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem
from core.tree_entries import build_tree_entry,SIZE_LABEL_ROLE,IS_FOLDER_ROLE,APKTOOL_LINK_ROLE


class TreeEntriesTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.tree=SimpleNamespace(base=str(self.base),superdetect_cache={},NON_FS_BADGE={'SUPER','SPARSE'},
            _unpack_watcher=SimpleNamespace(get_fs_type=lambda _: 'EXT4'),
            _resolve_folder_icon=lambda *_:None,_resolve_file_icon=lambda _:None,
            _apply_ctx_color=Mock(),add_loading=Mock(),_schedule_file_thumbnail=Mock(),
            _dtgen_collapse_target=lambda _:None,get_meta=lambda path:(os.path.basename(path),'u:object_r:system_file:s0','rw-r--r--','config'))
        # QTreeWidgetItem.setIcon needs a QIcon; there is still no QWidget owner.
        from PySide6.QtGui import QIcon
        self.tree._resolve_folder_icon=lambda *_:QIcon()
        self.tree._resolve_file_icon=lambda _:QIcon()
        self.tree.icon_file=QIcon();self.tree.icon_symlink=QIcon();self.tree.icon_symlink_file=QIcon()
        self.tree.icon_apk=QIcon();self.tree.icon_apk_linked=QIcon()

    def build(self,name,folder=False,info=True,symlink=None,**kwargs):
        path=str(self.base/name)
        entry=(0,name,path,folder,symlink,path,None)
        return build_tree_entry(self.tree,entry,True,False,None,
            {path:'1.0 KB'},entry_info={'fs_type':'','collapse':None,'badge':'','apk_mirror':None} if info else None,
            item_factory=QTreeWidgetItem,**kwargs)

    def test_regular_file_builds_with_metadata_without_optional_tree_attributes(self):
        item=self.build('build.prop')
        self.assertEqual(item.text(0),'build.prop')
        self.assertEqual(item.text(1),'u:object_r:system_file:s0')
        self.assertEqual(item.data(0,SIZE_LABEL_ROLE),'1.0 KB')
        self.assertFalse(item.data(0,IS_FOLDER_ROLE))

    def test_directory_from_scan_keeps_lazy_child_indicator(self):
        item=self.build('system',folder=True)
        self.assertTrue(item.data(0,IS_FOLDER_ROLE))
        self.assertEqual(item.childIndicatorPolicy(),QTreeWidgetItem.ShowIndicator)

    def test_directory_without_scan_info_uses_filesystem_fallback(self):
        with patch('core.tree_entries.detect_fs_root',return_value=''):
            item=self.build('system',folder=True,info=False)
        self.assertEqual(item.data(0,Qt.UserRole+1),'EXT4')
        self.tree.add_loading.assert_called_once_with(item)

    def test_symlink_retains_target_and_broken_state(self):
        item=self.build('link',symlink={'resolved':'/missing/target','is_dir':False,'exists':False})
        self.assertEqual(item.data(0,Qt.UserRole+50),'/missing/target')
        self.assertFalse(item.data(0,Qt.UserRole+51))

    def test_mount_reattaches_after_file_row_build(self):
        self.tree._mounts={'fixture':True};self.tree.reattach_mount_item=Mock()
        item=self.build('system.img')
        self.tree.reattach_mount_item.assert_called_once_with(item,str(self.base/'system.img'))

    def test_dropped_file_badge_fallback_uses_tree_cache(self):
        path=str(self.base/'filesystem.bin')
        self.tree._dropped_badge_paths={os.path.normpath(path)}
        self.tree._detect_magic_once=lambda _:'SUPER'
        item=self.build('filesystem.bin',info=False)
        self.assertEqual(item.data(0,Qt.UserRole+1),'SUPER')


if __name__=='__main__':unittest.main()
