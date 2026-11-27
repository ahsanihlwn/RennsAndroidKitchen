# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from core import ui_lang
from core.tree_entries import IS_FOLDER_ROLE,APKTOOL_LINK_ROLE
from ui.directory import DirectoryEntry
from ui.workspace_actions import context_actions,dispatch
from ui.workspace_tools import dispatch_tool
from ui.menu_metrics import menu_metrics
from ui.workspace_tasks import WorkspaceTasks


class ContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QGuiApplication.instance() or QGuiApplication([])
        from core.fonts import apply_app_font
        apply_app_font(cls.app,polish_widgets=False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.calls=[];self.messages=[]
        tasks=WorkspaceTasks(runner=lambda *args,**kwargs:None);self.addCleanup(tasks.close)
        directory=SimpleNamespace(base=str(self.base),location=str(self.base),_nodes={},_history=[],engine=SimpleNamespace(root_map_ctx={},root_map_fs={}))
        self.session=SimpleNamespace(active_tree=directory,_selection=[],_clipboard=None,mounts=SimpleNamespace(reference=lambda *args,**kwargs:None),
            transfers=SimpleNamespace(start=lambda *args,**kwargs:self.calls.append((args,kwargs))),
            _tasks=tasks,statusChanged=SimpleNamespace(emit=lambda:None),message=lambda *args:self.messages.append(args))
        current=patch.object(ui_lang,'_current_lang','id');current.start();self.addCleanup(current.stop)
    def entry(self,name,badge='',folder=False,display_folder=None):
        path=self.base/name
        if folder:path.mkdir()
        else:path.write_bytes(b'fixture')
        node=DirectoryEntry([name,'','']);node.setData(0,Qt.UserRole,str(path));node.setData(0,Qt.UserRole+1,badge)
        node.setData(0,IS_FOLDER_ROLE,folder if display_folder is None else display_folder)
        self.session.active_tree._nodes[str(path)]=node
        return path,node
    def actions(self,path,paths=None):
        with patch('core.binary_handler.is_available',lambda *args:True):return context_actions(self.session,str(path),[str(path)] if paths is None else paths)
    def test_basic_file_matches_reference_size_and_action_order(self):
        path,_=self.entry('notes.txt');rows=self.actions(path)
        self.assertEqual([row.get('key') for row in rows],['rename','delete',None,'copy','cut','paste',None,'newFile','newFolder','symlink',None,'explorer'])
        self.assertEqual(menu_metrics(rows)['width'],144);self.assertEqual(menu_metrics(rows)['height'],310)

    def test_apk_folder_paste_targets_its_decompiled_directory(self):
        from ui.workspace_actions import _directory
        path,node=self.entry('folder.apk')
        mirror=str(self.base/'apktool_dec/folder.apk')
        node.setData(0,APKTOOL_LINK_ROLE,mirror)
        self.assertEqual(_directory(self.session,str(path)),mirror)

    def test_boot_menu_offers_unpack_and_combined_action_in_order(self):
        path, _ = self.entry('boot_2.img', 'BOOT')
        keys = [row.get('key') for row in self.actions(path)]
        unpack = keys.index('unpack_boot')
        self.assertEqual(keys[unpack + 1], 'unpack_boot_decompile')
        self.assertNotIn('compile_kernel_sources', keys)
        kernel, _ = self.entry('kernel')
        kernel_keys = [row.get('key') for row in self.actions(kernel)]
        self.assertIn('decompile_kernel', kernel_keys)
        self.assertIn('decompile_kernel_dwarf', kernel_keys)
        vendor, _ = self.entry('vendor_boot.img', 'VNDRBOOT')
        self.assertNotIn('unpack_boot_decompile', [row.get('key') for row in self.actions(vendor)])

    def test_compile_action_only_from_sources_folder(self):
        folder, _ = self.entry('kernel_src', folder=True)
        self.assertNotIn('compile_kernel_sources', [row.get('key') for row in self.actions(folder)])
        manifest = folder / 'reconstruction_manifest.json'
        manifest.write_text('{"kernel_info":null}')
        self.assertNotIn('compile_kernel_sources', [row.get('key') for row in self.actions(folder)])
        manifest.write_text('{"kernel_info":{"is_kernel":true}}')
        self.assertIn('compile_kernel_sources', [row.get('key') for row in self.actions(folder)])

    def test_dwarf_decompile_form_dispatches_reference_and_unlimited_selection(self):
        path, _ = self.entry("kernel")
        self.session._operation_serial = 0
        dialogs = []
        self.session.presentationRequested = SimpleNamespace(emit=lambda *args: dialogs.append(args))
        results = []
        reference = str(self.base / "vmlinux")
        def run_operation(_session, _label, worker, **_options):
            results.append(worker())
        with patch("ui.workspace_actions.operation", side_effect=run_operation), \
                patch("core.kernel_workspace.decompile", return_value="finished") as decompile:
            dispatch_tool(self.session, "decompile_kernel_dwarf", str(path), {})
            kind, dialog = dialogs[0]
            self.assertEqual(kind, "actionDialog")
            self.session._forms.pop(dialog["token"])({"reference": reference, "limit": "0"})
        self.assertEqual(results, [(True, "finished")])
        self.assertEqual(Path(decompile.call_args.args[0]).resolve(), path.resolve())
        self.assertEqual(decompile.call_args.kwargs, {"dwarf_reference": reference, "max_funcs_per_file": None})

    def test_gki_badge_is_scoped_to_real_boot_folder_and_direct_sources(self):
        from core.boot_image import META_DIR, _write_bootconf
        from core.directory_scan import scan_tree_directory
        from core.kernel_workspace import SOURCE_DIR, gki_boot_folder, source_destination
        from core.magiskboot_tree import META_NAME

        boot = self.base / 'boot_itel'
        metadata = boot / META_DIR
        src = boot / SOURCE_DIR
        other = self.base / SOURCE_DIR
        metadata.mkdir(parents=True)
        src.mkdir()
        other.mkdir()
        (metadata / META_NAME).write_text('{"boot_kind":"boot","visible_components":["kernel"]}')
        kernel = boot / 'kernel'
        kernel.write_bytes(b'kernel fixture')
        for folder in (src, other):
            (folder / 'reconstruction_manifest.json').write_text('{"kernel_info":{"is_kernel":true}}')
        _write_bootconf(self.base, boot.name, 'boot', gki='11279209-' + 'a' * 40)
        self.assertTrue(gki_boot_folder(boot))
        self.assertEqual(source_destination(kernel), src)
        for root, name, expected in ((self.base, boot.name, True), (self.base, SOURCE_DIR, False), (boot, SOURCE_DIR, True)):
            ok, payload = scan_tree_directory({'base':str(self.base),'path':str(root),'show_apktool_dec':False})
            self.assertTrue(ok)
            row = next(row for row in payload['entries'] if row['name'] == name)
            self.assertIs(row['gki'], expected)
        _write_bootconf(self.base, boot.name, 'boot')
        self.assertFalse(gki_boot_folder(boot))

    def test_kernel_setup_offers_download_only_for_a_matched_release(self):
        from core import gki_checkout, kernel_version

        def run_operation(_session, _label, worker, done, **_kwargs):
            done(worker())

        path, _ = self.entry('boot.img', 'BOOT')
        info = kernel_version.KernelVersionInfo(str(path), version_string='6.12.38')
        self.session.message = lambda *args, **kwargs: self.messages.append((args, kwargs))
        release = gki_checkout.GkiRelease('123', 'gki-fixture', 'a' * 40, '', b'', 2)
        with patch('ui.workspace_actions.operation', side_effect=run_operation), \
             patch.object(gki_checkout, 'inspect_release', side_effect=[(info, release, ''), (info, release, ''), (info, None, 'Vendor build')]), \
             patch.object(gki_checkout, 'cache_checkout', side_effect=[{}, {'state': 'ready', 'path': '/home/test/gki'}]), \
             patch.object(gki_checkout, 'require_linux_tools'), \
             patch.object(gki_checkout, 'sync_release', return_value='/home/test/gki') as sync:
            dispatch_tool(self.session, 'prepare_kernel_build', str(path), {})
            confirm = self.messages[-1][1]['confirm']
            sync.assert_not_called()
            confirm()
            sync.assert_called_once_with(release, distro=None)
            self.assertIn('/home/test/gki/common', self.messages[-1][0][0])
            dispatch_tool(self.session, 'prepare_kernel_build', str(path), {})
            self.assertNotIn('confirm', self.messages[-1][1])
            sync.assert_called_once()
            dispatch_tool(self.session, 'prepare_kernel_build', str(path), {})
            self.assertNotIn('confirm', self.messages[-1][1])
            self.assertIn('compile_commands.json', self.messages[-1][0][0])
    def test_image_group_order_and_mounted_root_keep_basic_actions(self):
        path,_=self.entry('system.img','EXT4');rows=self.actions(path)
        self.assertEqual([row.get('key') for row in rows[12:]], [None,'unpack_ext','convert_img',None,'mount',None,'create_flashable'])
        self.assertEqual((menu_metrics(rows)['width'],menu_metrics(rows)['height']),(146,448))
        self.session.mounts.reference=lambda *args,**kwargs:(str(path),'')
        keys=[row.get('key') for row in self.actions(path)]
        self.assertIn('rename',keys);self.assertIn('unmount',keys);self.assertIn('usage',keys);self.assertNotIn('mount',keys)
    def test_batch_header_and_tools_keep_their_member_subset(self):
        image,_=self.entry('system.img','EXT4',display_folder=True);text,_=self.entry('notes.txt')
        rows=self.actions(image,[str(image),str(text)])
        self.assertEqual(rows[0],{'header':True,'label':'2 item dipilih'})
        command=next(row for row in rows if row.get('key')=='batchTool')
        self.assertEqual(command['tool'],'unpack_ext');self.assertEqual(command['paths'],[str(image)])
        self.assertNotIn('rename',[row.get('key') for row in rows])
    def test_apk_build_variants_use_original_destination_and_stop_before_sign_on_failure(self):
        path,node=self.entry('fixture.apk')
        from core import apktool_tool
        with patch.object(apktool_tool,'is_decompiled',return_value=True),patch.object(apktool_tool,'is_apktool_available',return_value=True),patch.object(apktool_tool,'is_signer_available',return_value=True):
            keys=[row.get('key') for row in self.actions(path)]
            self.assertTrue({'apktool_build','apktool_build_sign','apktool_build_keeporig','apktool_switch_mode'}<=set(keys))
        with patch('ui.workspace_actions.operation',side_effect=lambda session,label,worker,*args,**kwargs:worker()), \
             patch.object(apktool_tool,'build_apk',return_value=(True,'built')) as build,patch.object(apktool_tool,'sign_apk',return_value=(True,'signed')) as sign:
            dispatch_tool(self.session,'apktool_build_keeporig',str(path),{})
            self.assertEqual(build.call_args.args[1],str(path));self.assertTrue(build.call_args.kwargs['copy_original'])
            dispatch_tool(self.session,'apktool_build_sign',str(path),{});sign.assert_called_once_with(str(path))
            sign.reset_mock();build.return_value=(False,'failed')
            with self.assertRaises(RuntimeError):dispatch_tool(self.session,'apktool_build_sign',str(path),{})
            sign.assert_not_called()
    def test_cut_same_directory_is_consumed_without_transfer(self):
        path,_=self.entry('notes.txt');dispatch(self.session,'cut',{'path':str(path)})
        self.assertTrue(self.session._clipboard);dispatch(self.session,'paste',{'path':str(self.base)})
        self.assertIsNone(self.session._clipboard);self.assertFalse(self.calls);self.assertTrue(path.exists())
    def test_copy_same_directory_requests_numbered_copy(self):
        path,_=self.entry('notes.txt');dispatch(self.session,'copy',{'path':str(path)});dispatch(self.session,'paste',{'path':str(self.base)})
        self.assertTrue(self.calls[0][1]['duplicate_in_place']);self.assertFalse(self.calls[0][1]['move'])
    def test_clipboard_feedback_uses_source_fade_and_keeps_active_task_progress(self):
        import time
        path,_=self.entry('notes.txt');tasks=self.session._tasks
        dispatch(self.session,'copy',{'path':str(path)})
        self.assertEqual(tasks.state,'done');self.assertEqual(tasks.text,'Salin: 1 item')
        deadline=time.monotonic()+tasks.loading.FADE_DONE_MS/1000+1
        while tasks.state!='idle' and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertEqual(tasks.state,'idle');self.assertEqual(tasks.text,'')
        tasks.enqueue('Extracting fixture',lambda:True,lambda *args:None)
        progress=tasks.text
        dispatch(self.session,'cut',{'path':str(path)})
        self.assertEqual(tasks.state,'active');self.assertEqual(tasks.text,progress)
    def test_cut_into_own_descendant_retains_clipboard(self):
        path,_=self.entry('folder',folder=True);child=path/'child';child.mkdir()
        dispatch(self.session,'cut',{'path':str(path)});dispatch(self.session,'paste',{'path':str(child)})
        self.assertTrue(self.session._clipboard);self.assertTrue(self.messages);self.assertFalse(self.calls)
    def test_removed_clipboard_source_disables_paste(self):
        path,_=self.entry('notes.txt');other,_=self.entry('other.txt');dispatch(self.session,'copy',{'path':str(path)});path.unlink()
        rows=self.actions(other);self.assertFalse(next(row for row in rows if row.get('key')=='paste')['enabled'])
        self.assertIsNone(self.session._clipboard)


if __name__=='__main__':unittest.main()
