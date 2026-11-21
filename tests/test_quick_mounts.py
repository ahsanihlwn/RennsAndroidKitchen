# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import QCoreApplication,QEvent,QUrl
from core import imgfs
from ui.workspace_cli import headless_workspace
from ui.mounts import WorkspaceMounts
from tools.mount_fixture import build_fixture


class QuickMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rak-mount-native-');self.root=Path(self.tmp.name)
        self.image=build_fixture(self.root);self.owner=headless_workspace(str(self.root))
        self.mounts=WorkspaceMounts(self.owner);self.owner.mounts=self.mounts
        self.wait(lambda:not self.owner.tree.busy)
    def tearDown(self):
        self.mounts.close();self.owner.stop();self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.tmp.cleanup()
    def wait(self,predicate):
        deadline=time.monotonic()+15
        while not predicate():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.003)
    def test_real_mount_navigation_metadata_extract_and_unmount(self):
        image=str(self.image);tree=self.owner.tree
        self.mounts.mount(image);self.wait(lambda:self.mounts.reference(image) is not None and not tree.busy)
        self.assertTrue(self.mounts.writable(image));self.assertTrue(tree._nodes[image].folder)
        from core.rak_img_cli import cmd_mounted
        ok,listing=cmd_mounted(self.owner);self.assertTrue(ok);self.assertIn(image,listing)
        tree.activate(image)
        etc='mount://'+image+'//etc';document=etc+'/native.txt'
        self.assertIn(etc,tree._nodes);tree.activate(etc);self.assertIn(document,tree._nodes)
        self.mounts.metadata(document,2,'0600')
        self.wait(lambda:not self.owner.task_queue.is_busy() and not self.mounts._jobs.busy())
        raw,_=imgfs.read_path(image,'etc/native.txt');self.assertEqual(raw,b'Original mounted document\n')
        nodes=imgfs.list_image(image);node=next(row for row in nodes if row['path']=='etc/native.txt')
        self.assertEqual(node['perm'][-9:],'rw-------')
        output=self.root/'extracted';output.mkdir()
        self.mounts.extract(document,QUrl.fromLocalFile(str(output)))
        self.wait(lambda:not self.owner.task_queue.is_busy())
        self.assertEqual((output/'native.txt').read_bytes(),raw)
        self.mounts.unmount(image);self.wait(lambda:not tree.busy)
        self.assertIsNone(self.mounts.reference(image));self.assertEqual(tree.location,str(self.root))
        self.assertEqual(cmd_mounted(self.owner),(True,'No images are currently mounted in the Kitchen GUI.'))

    def test_real_mounted_folder_drag_extracts_once_and_retains_image(self):
        from ui.transfers import WorkspaceTransfers
        from ui.workspace import WorkspaceSession
        import json
        image=str(self.image);tree=self.owner.tree
        self.mounts.mount(image);self.wait(lambda:self.mounts.reference(image) is not None and not tree.busy)
        self.owner.secondTreeModel=None
        transfers=WorkspaceTransfers(self.owner)
        self.addCleanup(transfers.close)
        etc='mount://'+image+'//etc';document=etc+'/native.txt'
        mime=WorkspaceSession.treeDragMimeData(self.owner,0,[etc,document])
        self.assertNotIn('text/uri-list',mime)
        output=self.root/'drag-output';output.mkdir()
        transfers.dropInto(0,str(output),{'mounted':json.loads(mime['application/x-rdt-mount'])},True)
        self.wait(lambda:(output/'etc'/'native.txt').exists())
        self.wait(lambda:not transfers._jobs.busy() and not self.owner.task_queue.is_busy())
        self.assertEqual((output/'etc'/'native.txt').read_bytes(),b'Original mounted document\n')
        self.assertFalse((output/'native.txt').exists())
        self.assertEqual(imgfs.read_path(image,'etc/native.txt')[0],b'Original mounted document\n')
        tree.activate(image);tree.activate(etc)
        self.assertFalse(tree.dragTarget(tree.indexForPath(document),'',False)['accepted'])

    def test_mounted_directory_symlinks_sort_with_folders_before_files(self):
        from core.mount_index import _build_child_index
        image=str(self.image);tree=self.owner.tree
        entries=[
            {'path':'usr','is_dir':True,'is_link':False,'size':0,'selinux':'','perm':'rwxr-xr-x'},
            {'path':'xbin','is_dir':True,'is_link':False,'size':0,'selinux':'','perm':'rwxr-xr-x'},
            {'path':'build.prop','is_dir':False,'is_link':False,'size':100,'selinux':'','perm':'rw-r--r--'},
            {'path':'product','is_dir':False,'is_link':True,'target':'/product','size':8,'selinux':'','perm':'rwxrwxrwx'},
            {'path':'vendor','is_dir':False,'is_link':True,'target':'/vendor','size':7,'selinux':'','perm':'rwxrwxrwx'},
            {'path':'alias.prop','is_dir':False,'is_link':True,'target':'build.prop','size':10,'selinux':'','perm':'rwxrwxrwx'},
        ]
        reg=self.mounts.registry(str(self.root))
        reg.indexes[image]=_build_child_index(entries)
        reg.metadata[image]={'fs':'EROFS','sparse':False,'writable':True,'physical':image}
        self.mounts._image_base[image]=str(self.root)
        self.mounts._reload_views()
        self.wait(lambda:not tree.busy)
        tree.activate(image)
        names=[child.texts[0] for child in tree._nodes[image].children if not child.up]
        self.assertEqual(names,['product','usr','vendor','xbin','alias.prop','build.prop'])


if __name__=='__main__':unittest.main()
