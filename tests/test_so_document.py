# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from core import so_core
from ui.so_viewer import SoDocument
from tools.audit_so_viewer import fixture_bytes


class DemangleToolTests(unittest.TestCase):
    def tearDown(self):so_core._cxxfilt_bin.cache_clear()

    def test_import_and_plain_symbols_do_not_search_path(self):
        import importlib
        with patch('shutil.which',side_effect=AssertionError('Unexpected PATH scan')):
            importlib.reload(so_core)
            self.assertEqual(so_core.demangle('printf'),'printf')
            self.assertEqual(so_core.demangle_batch(['printf','native_export']),{'printf':'printf','native_export':'native_export'})

    def test_mangled_symbols_discover_tool_once_and_keep_plain_names(self):
        from types import SimpleNamespace
        so_core._cxxfilt_bin.cache_clear()
        with patch('shutil.which',return_value='fixture-cxxfilt') as which, \
             patch('core.kitchen_process.run',return_value=SimpleNamespace(stdout='foo()\n')) as run:
            self.assertEqual(so_core.demangle('_Z3foov'),'foo()')
            self.assertEqual(so_core.demangle_batch(['printf','_Z3foov']),{'printf':'printf','_Z3foov':'foo()'})
            which.assert_called_once_with('c++filt')
            self.assertEqual(run.call_args.kwargs['input'],'_Z3foov')


class SoDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.path=Path(self.temporary.name)/'fixture.so';self.path.write_bytes(fixture_bytes())
        self.document=SoDocument();self.messages=[];self.document.messageRequested.connect(lambda *args:self.messages.append(args))

    def tearDown(self):
        self.document.close();self.app.processEvents();self.temporary.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate():
            self.assertLess(time.monotonic(),deadline,(self.document.status,self.messages))
            self.app.processEvents();time.sleep(.002)

    def load(self):
        self.document.load(str(self.path));self.wait(lambda:not self.document.busy)
        self.assertTrue(self.document.ready,self.messages);self.assertFalse(self.messages)

    def test_real_elf_symbols_filters_sections_security_and_relocations(self):
        self.load();self.assertEqual(self.document.symbols.rowCount(),3)
        self.assertEqual(self.document.security['nx_stack'],'Yes');self.assertEqual(self.document.security['stack_canary'],'Yes')
        self.document.filterSymbols('native','export',True)
        self.assertEqual(self.document.symbols.snapshot()[0]['cells'],['export','native_export','native_export','0x1000','6'])
        self.document.filterSymbols('','local',False);self.assertEqual(self.document.symbols.rowCount(),1)
        self.document.filterRelocations('stack_chk');self.assertEqual(self.document.relocations.snapshot()[0]['cells'][1],'__stack_chk_fail')
        self.document.filterRelocations('missing');self.assertEqual(self.document.relocations.rowCount(),0)
        self.assertEqual(self.document.executableSections,['.text'])
        self.assertEqual(self.document.sections.snapshot()[1]['cells'][-1],'-AX')

    def test_paths_expand_filter_ancestors_and_open_string_without_io(self):
        self.load();count=self.document.paths.rowCount();self.document.togglePath(0)
        self.assertEqual(self.document.paths.rowCount(),count-2)
        self.document.filterPaths('libnative.so');self.assertEqual(self.document.paths.rowCount(),3)
        paths=[];self.document.pathRequested.connect(paths.append);self.document.openPath(2)
        self.assertEqual(paths,['system/lib64/libnative.so'])
        self.wait(lambda:not self.document.busy and not self.document.pathXrefs.get('loading',True))
        self.assertTrue(self.document.pathXrefs['locations'])
        self.assertIn('/system/lib64/libnative.so',self.document.pathXrefs['summary_text'])
        self.assertIn('system/lib64/libnative.so',self.document.pathXrefs['agent_prompt'])
        self.document.filterStrings(paths[0]);self.assertIn('[path?] /system/lib64/libnative.so',self.document.stringsText)
        self.assertNotIn('config.xml',self.document.stringsText)
        self.document.filterPaths('PNG image');self.assertEqual(self.document.paths.rowCount(),2)

    def test_section_bytes_embedded_save_nested_and_json_export(self):
        self.load();self.document.openSection(1);self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.embeddedInfo['title'],'Section .text');self.assertIn('b8 2a 00 00 00 c3',self.document.embeddedInfo['hex'])
        raw=Path(self.temporary.name)/'text.bin';self.document.saveEmbedded(str(raw));self.wait(lambda:not self.document.busy)
        self.assertEqual(raw.read_bytes(),b'\xb8\x2a\0\0\0\xc3')
        self.document.openSection(6);self.wait(lambda:not self.document.busy)
        self.assertIn('SHT_NOBITS',self.messages[-1][1])
        self.document.filterPaths('Nested ELF');self.document.openPath(1);nested=[];self.document.nestedRequested.connect(nested.append)
        self.document.analyzeNested();self.wait(lambda:not self.document.busy);self.assertIn('nested_export',nested[0])
        export=Path(self.temporary.name)/'export.json';self.document.exportJson(str(export));self.wait(lambda:not self.document.busy)
        result=json.loads(export.read_text());self.assertEqual(result['symbols']['total'],3)
        self.assertEqual(result['embedded_files'][0]['type'],'PNG image');self.assertNotIn('data',result['embedded_files'][0])

    def test_compare_uses_exported_symbol_names_and_reload_keeps_filters(self):
        self.load();other=Path(self.temporary.name)/'other.so';other.write_bytes(fixture_bytes('second_export'))
        self.document.compare(str(other));self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.comparison['only_in_a'],['native_export']);self.assertEqual(self.document.comparison['only_in_b'],['second_export'])
        self.document.filterSymbols('','import',False);self.document.reload();self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.symbols.rowCount(),1);self.assertEqual(self.document.symbols.snapshot()[0]['cells'][0],'import')

    def test_close_or_new_load_suppresses_previous_analysis_result(self):
        from core import binary_core
        entered=threading.Event();release=threading.Event();finished=threading.Event();analyze=binary_core.analyze_file
        def delayed(path):
            entered.set();release.wait(3)
            try:return analyze(path)
            finally:finished.set()
        with patch('ui.so_viewer.binary_core.analyze_file',delayed):
            self.document.load(str(self.path));self.wait(entered.is_set);self.document.close();release.set();self.wait(finished.is_set)
            for _ in range(10):self.app.processEvents();time.sleep(.002)
        self.assertFalse(self.document.ready);self.assertEqual(self.document.symbols.rowCount(),0);self.assertFalse(self.messages)

    def test_invalid_elf_is_reported_and_missing_file_never_created(self):
        from core.error_reporting import register_error_presenter, unregister_error_presenter
        from PySide6.QtCore import QObject, Signal
        class Presenter(QObject):
            fatalRequested = Signal(str)
            operationErrorRequested = Signal(str)
            def __init__(self):
                super().__init__(); self._closing = False; self.fatals = []; self.operations = []
                self.fatalRequested.connect(self.fatals.append)
                self.operationErrorRequested.connect(self.operations.append)
        presenter = Presenter()
        register_error_presenter(presenter); self.addCleanup(unregister_error_presenter, presenter)
        self.path.write_bytes(b'not an elf');self.document.load(str(self.path));self.wait(lambda:not self.document.busy)
        self.app.processEvents()
        self.assertFalse(self.document.ready);self.assertFalse(self.messages)
        self.assertFalse(presenter.operations);self.assertFalse(presenter.fatals)
        self.assertIn('Error:',self.document.status);self.assertNotIn('Traceback',self.document.status)
        self.assertIn('Unsupported binary format',self.document.overview)
        missing=self.path.with_name('missing.so');self.document.load(str(missing))
        self.assertIn('Not found',self.document.status);self.assertFalse(missing.exists())

    def test_ghidra_functions_and_shared_annotation_revision(self):
        from core import binary_session
        def analyze(session,address=None):
            if address is None:return {'functions':[{'address':'1000','name':'check_config','size':6}]}
            return {'function':{'address':'1000','name':'check_config','signature':'int check_config(void)'},'pseudocode':'return 42;',
                    'instructions':[{'address':'1000','text':'RET'}],'references':[{'from':'1000','to':'2000','type':'CALL','to_name':'printf','string_value':'/system/lib64/libnative.so'}],
                    'callers':[],'callees':[],'blocks':[{'address':'1000','destinations':[]}]}
        with patch.object(binary_session,'storage_root',return_value=Path(self.temporary.name)/'sessions'), \
             patch.object(binary_session.BinarySession,'analyze',analyze):
            self.load();self.document.analyzeGhidra();self.wait(lambda:not self.document.busy)
            self.assertEqual(self.document.functions.snapshot()[0]['cells'][1],'check_config')
            self.document.inspectFunction(0);self.wait(lambda:not self.document.busy)
            self.assertEqual(self.document.functionDetail['pseudocode'],'return 42;')
            self.assertIn('1000 → 2000',self.document.functionDetail['referencesText'])
            self.assertIn('[printf]',self.document.functionDetail['referencesText'])
            self.assertEqual(len(self.document.functionDetail['blocks']),1)
            self.assertIn('check_config',self.document.functionDetail['reaPrompt'])
            saved=[];self.document.annotationSaved.connect(lambda *args:saved.append(args))
            self.document.annotate('1000','read_config','user evidence',0);self.wait(lambda:not self.document.busy)
            self.assertEqual(saved,[('1000',1)])
            session=binary_session.BinarySession(self.path)
            session.annotate('1000','agent_name','agent evidence',1)
            self.document.annotate('1000','stale_name','stale evidence',1);self.wait(lambda:not self.document.busy)
            self.assertIn('changed',self.messages[-1][1])
            self.assertEqual(session.annotations()['entries']['1000']['name'],'agent_name')


if __name__=='__main__':unittest.main()
