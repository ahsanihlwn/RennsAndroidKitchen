# SPDX-License-Identifier: MPL-2.0
"""Agents verify bytes, metadata and differences using the same branch as edits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from PySide6.QtCore import QCoreApplication
from tests import test_kitchen_cli as fixtures
from core.workspace_files import WorkspaceFiles
from core.make_symlink import make_symlink_bytes


class UnixVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=fixtures.KitchenCliTests.setUp
    tearDown=fixtures.KitchenCliTests.tearDown
    wait=fixtures.KitchenCliTests.wait
    command=fixtures.KitchenCliTests.command

    def branch_fixture(self):
        folder=self.root/'vendor';folder.mkdir();path=folder/'build.prop';path.write_bytes(b'old\n')
        config=self.root/'config';config.mkdir(exist_ok=True)
        (config/'vendor_fs_config').write_text('vendor 0 0 0755\nvendor/build.prop 1000 1001 0644\n')
        (config/'vendor_file_contexts').write_text('/vendor/build\\.prop u:object_r:vendor_file:s0\n')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('vendor','verify','blue');manager.set_active_branch('vendor','verify')
        self.assertTrue(self.command("sed -i 's/old/new/' vendor/build.prop")['ok'])
        (self.root/'alias').write_bytes(make_symlink_bytes('/vendor'))
        return path,manager

    def test_stat_and_hash_follow_branch_and_report_android_metadata_without_seeding(self):
        path,manager=self.branch_fixture();self.assertTrue(self.command('chmod 0600 alias/build.prop')['ok'])
        description=json.loads(self.command('stat alias/build.prop --json')['msg'])
        self.assertEqual(description['branch'],'verify');self.assertEqual(description['origin'],'branch')
        self.assertEqual(description['android']['mode'],'0600');self.assertEqual(description['android']['uid'],'1000')
        self.assertEqual(description['android']['selinux'],'u:object_r:vendor_file:s0')
        self.assertEqual(description['symlinks'][0]['target'],'/vendor')
        digest=self.command('sha256sum alias/build.prop');self.assertTrue(digest['ok'],digest)
        self.assertEqual(digest['msg'],hashlib.sha256(b'new\n').hexdigest()+'  alias/build.prop\n')
        self.assertEqual(path.read_bytes(),b'old\n')
        self.assertIn('0644',(self.root/'config/vendor_fs_config').read_text())

    def test_stat_keeps_symlink_identity_and_dereference_is_explicit(self):
        (self.root/'target.txt').write_bytes(b'content')
        (self.root/'link').write_bytes(make_symlink_bytes('target.txt'))
        entry=json.loads(self.command('stat link --json')['msg']);self.assertEqual(entry['kind'],'symlink')
        entry=json.loads(self.command('stat -L link --json')['msg']);self.assertEqual(entry['kind'],'file');self.assertEqual(entry['size'],7)
        (self.root/'broken').write_bytes(make_symlink_bytes('absent'))
        entry=json.loads(self.command('stat broken --json')['msg']);self.assertEqual(entry['symlink']['status'],'missing')
        result=self.command('stat -L broken --json');self.assertFalse(result['ok']);self.assertFalse(result['application_error'])

    def test_cmp_and_diff_differences_are_exit_one_without_operation_error_log(self):
        (self.root/'a.txt').write_bytes(b'first\nold\n');(self.root/'b.txt').write_bytes(b'first\nnew\n')
        for command in ('cmp a.txt b.txt','cmp -s a.txt b.txt','diff -u a.txt b.txt','diff -q a.txt b.txt'):
            result=self.command(command);self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'different');self.assertNotIn('log',result)
        self.assertEqual(self.command('cmp -s a.txt b.txt')['msg'],'')
        diff=self.command('diff -u a.txt b.txt')['msg'];self.assertIn('-old',diff);self.assertIn('+new',diff)
        self.assertIn('--- a.txt',diff);self.assertNotIn('neobranch',diff)
        self.assertTrue(self.command('cmp a.txt a.txt')['ok']);self.assertTrue(self.command('diff a.txt a.txt')['ok'])

    def test_recursive_diff_uses_merged_branch_and_keeps_source_unchanged(self):
        source,_=self.branch_fixture();(self.root/'expected').mkdir();(self.root/'expected/build.prop').write_bytes(b'new\n')
        result=self.command('diff -ru vendor expected');self.assertTrue(result['ok'],result)
        self.assertEqual(source.read_bytes(),b'old\n')
        (self.root/'expected/extra.txt').write_bytes(b'added')
        result=self.command('diff -r vendor expected');self.assertEqual(result['error_code'],'different')
        self.assertIn('Only in ',result['msg']);self.assertIn('expected',result['msg']);self.assertNotIn('rak-diff-view-',result['msg'])

    def test_large_binary_hash_and_cmp_really_read_after_first_megabyte(self):
        payload=bytes(range(256))*24000
        (self.root/'a.bin').write_bytes(payload);(self.root/'b.bin').write_bytes(payload[:-1]+b'!')
        self.assertEqual(self.command('sha256sum a.bin')['msg'].split()[0],hashlib.sha256(payload).hexdigest())
        result=self.command('cmp a.bin b.bin');self.assertEqual(result['error_code'],'different');self.assertIn(str(len(payload)),result['msg'])

    def test_checksum_manifest_verifies_branch_and_mismatch_is_normal(self):
        self.branch_fixture();digest=hashlib.sha256(b'new\n').hexdigest()
        (self.root/'SHA256SUMS').write_text(digest+'  alias/build.prop\n')
        self.assertEqual(self.command('sha256sum -c SHA256SUMS')['msg'],'alias/build.prop: OK\n')
        (self.root/'SHA256SUMS').write_text('0'*64+'  alias/build.prop\n')
        result=self.command('sha256sum -c SHA256SUMS');self.assertEqual(result['error_code'],'different');self.assertNotIn('log',result)

    def test_find_reports_links_and_branch_only_paths_without_storage_paths(self):
        self.branch_fixture();self.assertTrue(self.command('touch vendor/only.txt')['ok'])
        self.assertEqual(self.command('find vendor -name "*.txt" -type f')['msg'],'vendor/only.txt\n')
        self.assertEqual(self.command('find alias -type l')['msg'],'alias\n')
        self.assertIn('alias/build.prop',self.command('find -L alias -maxdepth 1 -type f')['msg'])
        result=self.command('find vendor -maxdepth 0');self.assertEqual(result['msg'],'vendor\n')
        (self.root/'vendor/back').write_bytes(make_symlink_bytes('/vendor'))
        result=self.command('find -L vendor');self.assertFalse(result['ok']);self.assertFalse(result['application_error']);self.assertIn('cycle',result['msg'])

    def test_head_tail_wc_use_gnu_and_cat_is_not_silently_truncated(self):
        data=''.join(f'{index}: test words\n' for index in range(30000))+'FINAL_MARKER\n'
        (self.root/'large text.txt').write_text(data,encoding='utf-8',newline='\n')
        self.assertEqual(self.command('head -n 2 "large text.txt"')['msg'],'0: test words\n1: test words\n')
        self.assertEqual(self.command('tail -n 1 "large text.txt"')['msg'],'FINAL_MARKER\n')
        result=self.command('wc -lc "large text.txt"');self.assertTrue(result['ok'],result)
        self.assertEqual(result['msg'].split()[:2],['30001',str(len(data.encode()))]);self.assertNotIn(str(self.root),result['msg'])
        result=self.command('cat "large text.txt"');self.assertEqual(result['msg'],data);self.assertNotIn('[truncated',result['msg'])

    def test_standalone_output_status_and_shutdown_are_clean(self):
        (self.root/'a.txt').write_bytes(b'old\n');(self.root/'b.txt').write_bytes(b'new\n')
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        for args,code in ((['cmp','-s','a.txt','b.txt'],1),(['diff','-u','a.txt','b.txt'],1),(['diff','absent','b.txt'],2),(['stat','a.txt','--json'],0),(['inspect','a.txt','--json'],0)):
            with self.subTest(args=args):
                result=subprocess.run([sys.executable,'-c',script,*args],cwd=self.root,env={**__import__('os').environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])},capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,code,result.stderr);self.assertNotIn('Signal source has been deleted',result.stderr)
                self.assertNotIn('[RAK_CLI',result.stdout)
                if '--json' in args:json.loads(result.stdout)

    def test_wc_files0_manifest_handles_more_than_windows_command_line_limit(self):
        folder=self.root/('long directory name '*4).rstrip();folder.mkdir()
        names=[]
        for index in range(260):
            path=folder/f'file {index:04}.txt';path.write_bytes(b'two words\n')
            names.append(str(path.relative_to(self.root)))
        (self.root/'files.list0').write_bytes(b''.join(name.encode('utf-8')+b'\0' for name in names))
        self.assertGreater(sum(len(str(self.root/name)) for name in names),32767)
        result=self.command('wc -l --files0-from files.list0');self.assertTrue(result['ok'],result)
        self.assertEqual(len(result['msg'].splitlines()),261)
        self.assertEqual(result['msg'].splitlines()[-1].split(),['260','total'])

    def test_find_print0_can_pipe_to_wc_without_bypassing_branch_view(self):
        import os
        self.branch_fixture();self.assertTrue(self.command('touch vendor/only.txt')['ok'])
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        source=subprocess.Popen([sys.executable,'-c',script,'find','vendor','-type','f','-print0'],cwd=self.root,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            result=subprocess.run([sys.executable,'-c',script,'wc','-l','--files0-from=-'],cwd=self.root,env=environment,stdin=source.stdout,capture_output=True,text=True,timeout=25)
            source.stdout.close();diagnostics=source.stderr.read();source.wait(timeout=10)
        finally:
            if source.poll() is None:source.terminate();source.wait(timeout=10)
            source.stderr.close()
        self.assertEqual(source.returncode,0,diagnostics);self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('vendor/build.prop',result.stdout);self.assertIn('vendor/only.txt',result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1].split(),['1','total'])

    def test_large_sed_in_branch_preserves_source_and_bom_eol(self):
        source,manager=self.branch_fixture()
        data=b'\xef\xbb\xbf'+b'keep\r\n'*90000+b'FINAL_MARKER\r\n'
        source.write_bytes(data)
        overlay=Path(manager._branch_fs_dir('vendor','verify'))/'build.prop'
        if overlay.exists():overlay.unlink()
        result=self.command("sed -i 's/FINAL_MARKER/LARGE_EDIT_VERIFIED/' vendor/build.prop")
        self.assertTrue(result['ok'],result);self.assertEqual(source.read_bytes(),data)
        self.assertTrue(overlay.read_bytes().startswith(b'\xef\xbb\xbf'));self.assertTrue(overlay.read_bytes().endswith(b'LARGE_EDIT_VERIFIED\r\n'))

    def test_doctor_and_standalone_capabilities_report_actual_scope(self):
        self.assertEqual(self.owner.project,str(self.root))
        from core.rak_agent_docs import get_all_workspace_dirs
        self.assertEqual(get_all_workspace_dirs(str(self.root)),[str(self.root)])
        result=self.command('doctor --json');self.assertTrue(result['ok'],result)
        value=json.loads(result['msg']);self.assertFalse(value['live_session']);self.assertEqual(value['mode'],'standalone')
        self.assertIn('stat [-L] PATH... [--json]',value['primary_commands']['metadata'])
        self.assertIn('native agent tools',value['shell']['project_reads_and_edits'])
        self.assertFalse(value['shell']['arbitrary_host_commands_isolated'])
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        environment={**__import__('os').environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        for command in ('doctor','capabilities'):
            result=subprocess.run([sys.executable,'-c',script,command,'--json'],cwd=self.root,env=environment,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr);value=json.loads(result.stdout)
            self.assertEqual(value['mode'],'standalone');self.assertIn('context',value['requires_live_session'])

    def test_json_replacement_keeps_multiline_bom_eol_and_branch_boundary(self):
        source,manager=self.branch_fixture();original=b'\xef\xbb\xbfvalue=old\r\n# keep\r\n';source.write_bytes(original)
        overlay=Path(manager._branch_fs_dir('vendor','verify'))/'build.prop';overlay.unlink()
        (self.root/'replacement.json').write_text(json.dumps({'old_text':'# keep','new_text':'# keep\n# multiline verified'}),encoding='utf-8')
        result=self.command('fs replace alias/build.prop --from replacement.json');self.assertTrue(result['ok'],result)
        self.assertEqual(overlay.read_bytes(),b'\xef\xbb\xbfvalue=old\r\n# keep\r\n# multiline verified\r\n');self.assertEqual(source.read_bytes(),original)
        saved=overlay.read_bytes();(self.root/'replacement.json').write_text('{bad json')
        result=self.command('fs replace alias/build.prop --from replacement.json')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error']);self.assertEqual(overlay.read_bytes(),saved)

    def test_legacy_fs_actual_argv_preserves_newlines_and_literal_quotes(self):
        import threading
        from core import rak_cli
        (self.root/'text.txt').write_bytes(b'"old"\n')
        argv=['fs','replace','text.txt','"old"','"new"\nsecond line']
        results=[]
        worker=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command(subprocess.list2cmdline(argv),str(self.root),argv=argv)))
        worker.start();self.wait(lambda:not worker.is_alive());worker.join()
        self.assertTrue(results[0]['ok'],results);self.assertEqual((self.root/'text.txt').read_bytes(),b'"new"\nsecond line\n')

    def test_recursive_diff_uses_gnu_ignore_flags_for_changed_content(self):
        for folder,text in (('left','a b\n'),('right','a   b\n')):
            (self.root/folder).mkdir();(self.root/folder/'same.txt').write_bytes(b'identical\n');(self.root/folder/'changed.txt').write_text(text,newline='\n')
        result=self.command('diff -ru left right');self.assertEqual(result['error_code'],'different');self.assertIn('-a b',result['msg'])
        self.assertNotIn('same.txt',result['msg'])
        result=self.command('diff -rub left right');self.assertTrue(result['ok'],result)

    @unittest.skipUnless(os.name=='nt','Windows case-insensitive filesystem contract')
    def test_case_variant_overlay_cannot_leak_source_or_revive_a_deleted_file(self):
        source,manager=self.branch_fixture()
        overlay=Path(manager._branch_fs_dir('vendor','verify'))/'build.prop'
        overlay.rename(overlay.with_name('BUILD.PROP'))
        rows=WorkspaceFiles(str(self.root)).view.list_dir(str(self.root/'vendor'))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['origin'],'branch')
        self.assertEqual(self.command('cat vendor/BUILD.PROP')['msg'],'new\n')
        self.assertTrue(self.command('rm vendor/BUILD.PROP')['ok'])
        self.assertFalse(WorkspaceFiles(str(self.root)).view.resolve(str(source))[0])
        self.assertTrue(self.command('touch vendor/build.prop')['ok'])
        self.assertFalse(manager.deleted_set_for_branch('vendor','verify'))
        self.assertEqual(source.read_bytes(),b'old\n')


if __name__=='__main__':unittest.main()
