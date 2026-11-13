# SPDX-License-Identifier: MPL-2.0
"""Exercise the real generated files, sync entry points and user-text boundaries."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from core import rak_agent_docs as docs
from core.cli_contract import PRIMARY
from core.command_context import OperationError
from core.make_symlink import make_symlink_bytes


class AgentDocsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rak agent docs ');self.addCleanup(temporary.cleanup)
        self.project=Path(temporary.name);self.port=self.project/'PORT';self.stock=self.project/'STOCK'
        for root in (self.port,self.stock):(root/'config').mkdir(parents=True)
        tree=SimpleNamespace(base=str(self.port));tree2=SimpleNamespace(base=str(self.stock))
        owner=SimpleNamespace(tree=tree,tree2=tree2,active_tree=tree,project=str(self.project))
        state=patch('core.rak_cli._main_ref',owner);state.start();self.addCleanup(state.stop)

    def test_all_supported_files_get_current_contract_and_keep_manual_instructions(self):
        original={}
        for directory in (self.project,self.port,self.stock):
            for filename in docs.AGENT_FILES:
                path=directory/filename;prefix=f'User rules in {directory.name}/{filename}\n\n';suffix='\nKeep my instructions after the block.\n'
                path.write_text(prefix+docs.START_MARKER+'\nold contract\n'+docs.END_MARKER+'\n'+suffix,encoding='utf-8',newline='\n')
                original[path]=(prefix,suffix)
        updated=docs.update_all_agent_files(str(self.port))
        self.assertEqual(set(updated),{str(path) for path in original})
        snapshots={}
        for path,(prefix,suffix) in original.items():
            text=path.read_text(encoding='utf-8');snapshots[path]=path.read_bytes()
            self.assertTrue(text.startswith(prefix));self.assertTrue(text.endswith(suffix))
            self.assertNotIn('old contract',text);self.assertEqual(text.count(docs.START_MARKER),1)
            self.assertIn('RAK CLI contract v2',text)
            self.assertIn('Agent memory: keep notes, learned facts, and user rules outside this managed block.',text)
            for contract in ('native Windows agent tools','ordinary project files','Kitchen operations','rak inspect PATH --json','rak branch switch','managed block'):
                self.assertIn(contract,text)
            self.assertNotIn('All project-tree file mutations must use managed',text)
        docs.update_all_agent_files(str(self.port))
        self.assertEqual(snapshots,{path:path.read_bytes() for path in snapshots})

    def test_any_supported_terminal_trigger_refreshes_all_four_formats(self):
        for command in ('claude','npx @openai/codex','gemini','aider','cursor','qwen'):
            for filename in docs.AGENT_FILES:(self.port/filename).write_text('My manual rules.\n',encoding='utf-8')
            docs.handle_terminal_input(command,cwd=str(self.port))
            for filename in docs.AGENT_FILES:
                text=(self.port/filename).read_text(encoding='utf-8')
                self.assertIn('RAK CLI contract v2',text);self.assertTrue(text.endswith('My manual rules.\n'))
        with patch.object(docs,'update_all_agent_files') as writer:
            docs.handle_terminal_input('echo hello',cwd=str(self.port));writer.assert_not_called()
            docs.handle_terminal_input('',cwd=str(self.port),extra_line='codex resume');writer.assert_called_once_with(str(self.port))

    def test_duplicate_old_blocks_preserve_bom_crlf_and_user_text_exactly(self):
        path=self.port/'AGENTS.md'
        old=(docs.START_MARKER+'\r\nstale\r\n'+docs.END_MARKER+'\r\n').encode()
        prefix=b'\xef\xbb\xbf# Manual prefix\r\n';middle=b'Manual between blocks\r\n';suffix=b'Manual suffix without newline'
        path.write_bytes(prefix+old+middle+old+suffix)
        self.assertTrue(docs.update_agent_file(str(path),str(self.port)))
        raw=path.read_bytes();self.assertTrue(raw.startswith(prefix));self.assertTrue(raw.endswith(middle+suffix))
        self.assertEqual(raw.count(docs.START_MARKER.encode()),1);self.assertNotIn(b'stale',raw);self.assertNotIn(b'\r\r\n',raw)
        self.assertTrue(docs.update_agent_file(str(path),str(self.port)));self.assertEqual(path.read_bytes(),raw)

    def test_malformed_block_does_not_discard_user_text_and_sync_reports_failure(self):
        path=self.port/'CODEX.md';original=(docs.START_MARKER+'\nUser text that must survive a missing end marker.').encode()
        path.write_bytes(original)
        with self.assertRaisesRegex(OperationError,'CODEX.md'):
            docs.update_all_agent_files(str(self.port))
        self.assertEqual(path.read_bytes(),original)
        self.assertIn('RAK CLI contract v2',(self.port/'AGENTS.md').read_text(encoding='utf-8'))

    def test_failed_atomic_publish_keeps_previous_file_and_cleans_its_staging_file(self):
        path=self.port/'CLAUDE.md';path.write_bytes(b'keep original\r\n')
        with patch.object(docs.os,'replace',side_effect=OSError('disk unavailable')):
            self.assertFalse(docs.update_agent_file(str(path),str(self.port)))
        self.assertEqual(path.read_bytes(),b'keep original\r\n');self.assertFalse(list(self.port.glob('.rak-agent-docs-*')))

    def test_memory_containing_quoted_and_fenced_marker_examples_is_preserved(self):
        path=self.port/'AGENTS.md'
        memory=('Remember how the marker works: '+docs.START_MARKER+'\r\n'+
                '```markdown\r\n'+docs.START_MARKER+'\r\nExample text, not a managed block.\r\n'+docs.END_MARKER+'\r\n```\r\n'+
                '    '+docs.START_MARKER+'\r\nIndented example.\r\n    '+docs.END_MARKER+'\r\nMy latest memory.').encode()
        path.write_bytes(memory)
        self.assertTrue(docs.update_agent_file(str(path),str(self.port)))
        self.assertTrue(path.read_bytes().endswith(memory))
        first=path.read_bytes();self.assertTrue(docs.update_agent_file(str(path),str(self.port)))
        self.assertEqual(path.read_bytes(),first)

    def test_new_agent_memory_written_during_refresh_is_never_overwritten(self):
        path=self.port/'AGENTS.md';path.write_bytes(b'Old memory.\n')
        latest=b'Old memory.\nNew memory added by the agent.\n'
        def concurrent_edit(_descriptor):path.write_bytes(latest)
        with patch.object(docs.os,'fsync',side_effect=concurrent_edit):
            self.assertFalse(docs.update_agent_file(str(path),str(self.port)))
        self.assertEqual(path.read_bytes(),latest);self.assertFalse(list(self.port.glob('.rak-agent-docs-*')))

    def test_inventory_uses_live_identity_for_images_containers_and_symlinks(self):
        (self.port/'renamed archive.zip').write_bytes(b'ANDROID!'+bytes(100))
        (self.port/'system.img.br').write_bytes(b'hint')
        (self.port/'system.new.dat').write_bytes(b'hint')
        (self.port/'payload.bin').write_bytes(b'CrAU'+bytes(24))
        with zipfile.ZipFile(self.port/'update.7z','w') as archive:archive.writestr('inside.txt','data')
        (self.port/'vendor').mkdir();(self.port/'etc').write_bytes(make_symlink_bytes('/vendor'))
        entries={row['name']:row for row in docs.scan_project_partitions(str(self.port))}
        for name,kind in (('renamed archive.zip','BOOT'),('system.img.br','BR'),('system.new.dat','DAT'),('payload.bin','PAYLOAD'),('update.7z','archive'),('etc','SYMLINK')):
            self.assertEqual(entries[name]['format'],kind)
            self.assertTrue(entries[name]['action'].startswith('rak inspect '));self.assertTrue(entries[name]['action'].endswith('--json'))
        self.assertIn('"renamed archive.zip"',entries['renamed archive.zip']['action'])
        self.assertIn('detected_by=header_magic',entries['renamed archive.zip']['desc'])
        self.assertIn('detected_by=filename_hint',entries['system.img.br']['desc'])
        self.assertIn('behaves_as=directory',entries['etc']['desc'])
        self.assertEqual(entries['vendor/']['action'],'rak inspect vendor --json')

    def test_inventory_sees_branch_only_files_and_hides_deleted_source(self):
        folder=self.port/'system';folder.mkdir();(folder/'deleted.img').write_bytes(b'ANDROID!'+bytes(100))
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.port));manager.create_branch('system','work','blue');manager.set_active_branch('system','work')
        manager.record_delete('system','work','system/deleted.img')
        target=Path(manager._branch_fs_dir('system','work'))/'branch-only.img';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'ANDROID!'+bytes(100))
        entries={row['name']:row for row in docs.scan_project_partitions(str(folder))}
        self.assertEqual(entries['branch-only.img']['format'],'BOOT');self.assertNotIn('deleted.img',entries)
        self.assertNotIn('neobranch',entries['branch-only.img']['action'])

    def test_physical_checkout_docs_use_partition_name_and_native_file_tools(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        (self.port/'vendor').mkdir()
        (self.port/'AGENTS.md').write_text('Manual memory stays here.\n',encoding='utf-8')
        NeoBranchManager(str(self.port)).create_checkout_branch('vendor','experiment','blue')
        real_is_physical=NeoBranchManager.is_physical_checkout
        seen=[]
        def is_physical(manager,root):
            seen.append(root)
            self.assertFalse(Path(root).is_absolute())
            return real_is_physical(manager,root)
        with patch.object(NeoBranchManager,'is_physical_checkout',is_physical):
            generated=docs.generate_dynamic_instructions(str(self.port))
            self.assertTrue(docs.update_agent_file(str(self.port/'AGENTS.md'),str(self.port)))
        self.assertIn('Physical NeoBranch: `vendor` on `experiment`',generated)
        self.assertIn('native Windows agent tools',generated)
        self.assertNotIn('sparse overlay or tombstone',generated)
        self.assertNotIn('Unix File Commands on the Active Project View',generated)
        self.assertIn('Manual memory stays here.',(self.port/'AGENTS.md').read_text(encoding='utf-8'))
        self.assertIn('vendor',seen)

    def test_nonstandard_partition_checkout_is_advertised(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        (self.port/'vendor_dlkm').mkdir()
        NeoBranchManager(str(self.port)).create_checkout_branch('vendor_dlkm','modules','blue')
        generated=docs.generate_dynamic_instructions(str(self.port))
        self.assertIn('Physical NeoBranch: `vendor_dlkm` on `modules`',generated)

    def test_clean_agent_file_removes_autogenerated_block_and_preserves_user_rules(self):
        path = self.port / 'AGENTS.md'
        user_header = "# My Custom Agent Guidelines\n- Do not touch vendor folder.\n\n"
        user_footer = "\n# Extra Safeguards\n- Always preserve logs.\n"
        generated_block = docs.generate_dynamic_instructions(str(self.port))
        path.write_text(user_header + generated_block + user_footer, encoding='utf-8')

        self.assertTrue(docs.clean_agent_file(str(path)))
        cleaned_text = path.read_text(encoding='utf-8')

        self.assertNotIn(docs.START_MARKER, cleaned_text)
        self.assertNotIn(docs.END_MARKER, cleaned_text)
        self.assertNotIn('Renns Android Kitchen (RAK)', cleaned_text)
        self.assertIn("# My Custom Agent Guidelines", cleaned_text)
        self.assertIn("- Do not touch vendor folder.", cleaned_text)
        self.assertIn("# Extra Safeguards", cleaned_text)
        self.assertIn("- Always preserve logs.", cleaned_text)

    def test_clean_agent_file_removes_file_if_only_autogenerated_content(self):
        path = self.port / 'CLAUDE.md'
        generated_block = docs.generate_dynamic_instructions(str(self.port))
        path.write_text(generated_block, encoding='utf-8')
        self.assertTrue(path.exists())

        self.assertTrue(docs.clean_agent_file(str(path), remove_empty=True))
        self.assertFalse(path.exists())

    def test_clean_all_agent_files_cleans_workspace_directories(self):
        for filename in docs.AGENT_FILES:
            file_path = self.port / filename
            file_path.write_text(f"User header for {filename}\n" + docs.generate_dynamic_instructions(str(self.port)), encoding='utf-8')

        cleaned = docs.clean_all_agent_files(str(self.port))
        self.assertEqual(len(cleaned), len(docs.AGENT_FILES))
        for filename in docs.AGENT_FILES:
            file_path = self.port / filename
            self.assertTrue(file_path.exists())
            text = file_path.read_text(encoding='utf-8')
            self.assertNotIn(docs.START_MARKER, text)
            self.assertNotIn(docs.END_MARKER, text)
            self.assertIn(f"User header for {filename}", text)


if __name__=='__main__':unittest.main()
