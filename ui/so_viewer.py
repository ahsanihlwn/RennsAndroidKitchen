# SPDX-License-Identifier: MPL-2.0
"""Qt Quick ELF viewer state, reusing the original analyzer without QWidget UI."""
import base64
import json
import os

from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl
from PySide6.QtGui import QImage
from core import so_core,so_document,binary_core,binary_session,binary_xref,native_decompiler
from core.command_context import failure_summary
from core.ui_lang import t
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel
from ui.file_icons import file_icon


class SoDocument(QObject):
    changed=Signal()
    tablesChanged=Signal()
    embeddedChanged=Signal()
    compareChanged=Signal()
    embeddedRequested=Signal()
    pathRequested=Signal(str)
    pathXrefsChanged=Signal()
    nestedRequested=Signal(str)
    compareRequested=Signal()
    messageRequested=Signal(str,str)
    copyRequested=Signal(str)
    functionChanged=Signal()
    annotationChanged=Signal()
    annotationSaved=Signal(str,int)
    reconstructProgress=Signal(int,int,str)
    reconstructFinished=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self._jobs=AsyncJobs(self,limit=2);self._generation=0;self._closed=False;self._pending=set()
        self._path='';self._result=None;self._status=t('so_loading');self._overview='';self._strings='';self._disasm=''
        self._symbol_query='';self._symbol_kind='';self._mangled=False;self._reloc_query='';self._string_query='';self._path_query=''
        self._models={name:RowsModel(parent=self) for name in ('symbols','sections','segments','relocations','paths')}
        self._path_nodes=[];self._expanded=set();self._visible_paths=[];self._embedded=None;self._embedded_info={};self._comparison={}
        self._path_xrefs={}
        self._session=None;self._function_query='';self._functions=[];self._visible_functions=[];self._resident_mode=False
        self._function_detail={};self._annotations={'revision':0,'entries':{}};self._ghidra={};self._rea={};self._native={}
        self._models['functions']=RowsModel(parent=self)

    @Property(QObject,constant=True)
    def functions(self):return self._models['functions']
    @Property('QVariantMap',notify=changed)
    def summary(self):
        if not self._result:return {}
        r=self._result;h=r['header']
        return {'name':os.path.basename(self._path),'format':r.get('format','ELF'),'architecture':h['abi'],
                'bits':h['class'],'size':so_core.human_size(h['size']),'engine':r.get('engine','python/pyelftools'),
                'sha256':r.get('sha256',''),'functions':len(self._functions),'strings':r['total_strings'],
                'sections':len(r['sections']),'warnings':'\n'.join(r.get('warnings',[]))}
    @Property('QVariantMap',notify=changed)
    def ghidra(self):return self._ghidra
    @Property('QVariantMap',notify=changed)
    def rea(self):return self._rea
    @Property('QVariantMap',notify=changed)
    def nativeDecompiler(self):return self._native
    @Property(bool,notify=changed)
    def canDecompile(self):return bool(self._ghidra.get('available') or self._native.get('available'))
    @Property(str,notify=changed)
    def filePath(self):return self._path
    @Property('QVariantMap',notify=pathXrefsChanged)
    def pathXrefs(self):return self._path_xrefs
    @Property('QVariantMap',notify=functionChanged)
    def functionDetail(self):return self._function_detail
    @Property('QVariantMap',notify=annotationChanged)
    def annotations(self):return self._annotations

    @Property(QObject,constant=True)
    def symbols(self):return self._models['symbols']
    @Property(QObject,constant=True)
    def sections(self):return self._models['sections']
    @Property(QObject,constant=True)
    def segments(self):return self._models['segments']
    @Property(QObject,constant=True)
    def relocations(self):return self._models['relocations']
    @Property(QObject,constant=True)
    def paths(self):return self._models['paths']
    @Property(bool,notify=changed)
    def ready(self):return self._result is not None
    @Property(bool,notify=changed)
    def busy(self):return bool(self._pending)
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(str,notify=changed)
    def overview(self):return self._overview
    @Property(str,notify=changed)
    def stringsText(self):return self._strings
    @Property(str,notify=changed)
    def disassembly(self):return self._disasm
    @Property(str,notify=tablesChanged)
    def symbolCount(self):return f"({self.symbols.rowCount()}/{self._result['symbols']['total']})" if self._result else ''
    @Property('QStringList',notify=changed)
    def executableSections(self):return self._result['exec_sections'] if self._result else []
    @Property('QVariantMap',notify=changed)
    def security(self):
        if not self._result:return {}
        return {key:str(value).upper() if key=='relro' else ('Yes' if value else 'No') if isinstance(value,bool) else str(value) for key,value in self._result['security'].items()}
    @Property('QVariantMap',notify=embeddedChanged)
    def embeddedInfo(self):return self._embedded_info
    @Property('QVariantMap',notify=compareChanged)
    def comparison(self):return self._comparison
    @Property(QUrl,notify=changed)
    def exportUrl(self):return QUrl.fromLocalFile(os.path.join(os.path.dirname(self._path),os.path.splitext(os.path.basename(self._path))[0]+'_analysis.json'))
    @Property(QUrl,notify=changed)
    def folderUrl(self):return QUrl.fromLocalFile(os.path.dirname(self._path))
    @Property(QUrl,notify=embeddedChanged)
    def embeddedSaveUrl(self):
        if self._embedded is None:return QUrl()
        ef=self._embedded;name=ef.label.split(' @ ')[0].split(' (')[0].strip()
        if not os.path.splitext(name)[1]:name+={'PNG image':'.png','JPEG image':'.jpg','GIF image':'.gif','ZIP/JAR/APK archive':'.zip','Nested ELF (XZ compressed)':'.elf','Nested ELF':'.elf'}.get(ef.file_type,'.bin')
        return QUrl.fromLocalFile(os.path.join(os.path.dirname(self._path),name))

    def _submit(self,key,work,done):
        if self._closed:return
        generation=self._generation;self._pending.add(key);self.changed.emit()
        def run(_=None):return True,work()
        def finished(ok,result):
            if self._closed or generation!=self._generation:return
            self._pending.discard(key)
            if ok:done(result)
            else:
                reason=failure_summary(result) or str(result)
                if key=='analysis':
                    self._status='Error: '+reason
                    self._overview=reason
                else:
                    self.messageRequested.emit(t('lbl_error'),reason)
            self.changed.emit()
        self._jobs.submit(key,run,None,finished,report_operation_errors=False)

    @Slot(str)
    def load(self,path):
        if self._path==path and self._result is not None:return
        self._path=path;self.reload()

    @Slot()
    def reload(self):
        if self._closed:return
        self._generation+=1;self._jobs.reset();self._pending.clear()
        self._result=None;self._session=None;self._function_detail={};self._functions=[];self.filterFunctions(self._function_query)
        self._overview='';self._strings='';self._disasm='';self._path_nodes=[];self._visible_paths=[];self._path_xrefs={}
        self.pathXrefsChanged.emit()
        for model in self._models.values():model.replace([],animate=False)
        self.tablesChanged.emit()
        self.functionChanged.emit()
        if not os.path.isfile(self._path):self._status=os.path.basename(self._path)+' (Not found)';self.changed.emit();return
        self._status=t('so_loading');path=self._path
        def analyze():
            result=binary_core.analyze_file(path)
            session=binary_session.BinarySession(path,result['sha256'])
            return result,session,session.annotations(),binary_session.ghidra_status(),binary_session.rea_status(),native_decompiler.status()
        def loaded(payload):
            result,self._session,self._annotations,self._ghidra,self._rea,self._native=payload
            self._result=result;self._status=so_document.status(result);self._overview=so_document.overview(result);self._disasm=''
            self._functions=[{'address':hex(f['addr']),'name':f['demangled'],'size':f['size'],'source':'symbol'} for f in result['functions']]
            self.filterFunctions(self._function_query);self.annotationChanged.emit()
            self.filterSymbols(self._symbol_query,self._symbol_kind,self._mangled)
            self._models['sections'].replace([{'key':str(i),'cells':[s['name'] or '(no name)',s['type'],s['addr'],s['offset'],so_core.human_size(s['size']),''.join(letter if s['flags']&flag else '-' for letter,flag in (('W',1),('A',2),('X',4)))]} for i,s in enumerate(result['sections'])],animate=False)
            self._models['segments'].replace([{'key':str(i),'cells':[s['type'],s['vaddr'],s['offset'],so_core.human_size(s['filesz']),''.join(letter if s['flags']&flag else '-' for letter,flag in (('R',4),('W',2),('X',1)))]} for i,s in enumerate(result['segments'])],animate=False)
            self.filterRelocations(self._reloc_query);self.filterStrings(self._string_query);self._build_paths()
        self._submit('analysis',analyze,loaded)

    @Slot(str)
    def filterFunctions(self,query):
        self._function_query=query;needle=query.lower()
        self._visible_functions=[f for f in self._functions if not needle or needle in f['name'].lower() or needle in f['address'].lower()]
        self.functions.replace([{'key':f['address'],'cells':[f['address'],f['name'],str(f['size']),f.get('source','ghidra')]} for f in self._visible_functions],animate=False)

    @Slot(str)
    def configureGhidra(self,url):
        home=self._destination(url)
        self._submit('ghidra-config',lambda:binary_session.configure_ghidra(home),self._set_ghidra)

    def _set_ghidra(self,status):self._ghidra=status

    def _run_ghidra_index(self, resident=False):
        if self._session is None:return
        self._resident_mode=bool(resident)
        session=self._session;path=self._path
        def work():
            binary_session.BinarySession(path,session.sha256)
            return session.analyze_resident() if resident else session.analyze()
        def loaded(result):
            src='rea-ghidra' if resident else 'ghidra'
            self._functions=[{**f,'source':src} for f in result['functions']]
            self._result['ghidra_analysis']=result
            self.filterFunctions(self._function_query)
            label='REA Bridge' if resident else 'Ghidra'
            self._status=f"{label}: {len(self._functions)} functions"+(' (truncated)' if result.get('functions_truncated') else '')
        self._submit('ghidra-analysis',work,loaded)

    @Slot()
    def analyzeGhidra(self):
        self._run_ghidra_index(resident=False)

    @Slot()
    def analyzeResidentGhidra(self):
        self._run_ghidra_index(resident=True)

    def _present_function_detail(self,result):
        result=dict(result)
        result['listing']='\n'.join(i['address']+'  '+i['text'] for i in result.get('instructions',[]))
        def _ref_line(r):
            line=r['from']+' → '+r['to']+'  '+r['type']
            if r.get('to_name'):line+=f"  [{r['to_name']}]"
            if r.get('string_value'):line+=f"  \"{r['string_value']}\""
            return line
        ref_lines=[_ref_line(r) for r in result.get('references',[])]
        if result.get('referenced_strings'):
            ref_lines.append('\n[Referenced Strings]')
            for s in result['referenced_strings']:
                ref_lines.append(f"  {s.get('address','')}  \"{s.get('value','')}\"")
        if result.get('function',{}).get('locals'):
            ref_lines.append('\n[Local Variables]')
            for loc in result['function']['locals']:
                ref_lines.append(f"  {loc.get('type','')} {loc.get('name','')}  ({loc.get('storage','')})")
        result['referencesText']='\n'.join(ref_lines)
        result['callsText']='Callers\n'+'\n'.join(f.get('address','')+'  '+f.get('name','') for f in result.get('callers',[]))+'\n\nCallees\n'+'\n'.join(f.get('address','')+'  '+f.get('name','') for f in result.get('callees',[]))
        fn=result.get('function',{})
        result['reaPrompt']=(
            f"Analyze and reconstruct readable source/intent for `{fn.get('name','function')}` @ `{fn.get('address','')}` "
            f"in `{os.path.basename(self._path)}`.\n"
            f"Signature: `{fn.get('signature','')}`\n\n"
            f"Pseudo-C (Ghidra):\n```c\n{result.get('pseudocode','')}\n```\n\n"
            f"Calls:\n{result['callsText']}\n\n"
            f"References (first 30):\n" + '\n'.join(result['referencesText'].splitlines()[:30])
        )
        return result

    @Slot(int)
    def inspectFunction(self,index):
        if self._session is None or not 0<=index<len(self._visible_functions):return
        function=self._visible_functions[index]
        self.inspectFunctionAtAddress(function['address'])

    @Slot(str)
    def inspectFunctionAtAddress(self,address):
        if self._session is None or not address:return
        session=self._session;path=self._path;resident=self._resident_mode;result=self._result
        has_ghidra_index=bool(result and 'ghidra_analysis' in result)
        use_native=not resident and not has_ghidra_index and bool(self._native.get('available'))
        if not use_native and not resident and not has_ghidra_index and not self._ghidra.get('available'):
            self.messageRequested.emit('Info',self._native.get('message') or 'No decompiler available');return
        def work():
            binary_session.BinarySession(path,session.sha256)
            if use_native:return session.native_function(result,address)
            return session.analyze_resident(address) if resident else session.analyze(address)
        def loaded(data):
            self._function_detail=self._present_function_detail(data);self.functionChanged.emit()
        self._submit('ghidra-function',work,loaded)

    @Slot(str,str,str,int)
    def annotate(self,address,name,comment,revision):
        if self._session is None:return
        session=self._session
        def done(data):
            self._annotations=data;self.annotationChanged.emit();self.annotationSaved.emit(address,data['revision'])
        self._submit('annotation',lambda:session.annotate(address,name,comment,revision),done)

    @Slot()
    def refreshAnnotations(self):
        if self._session is None:return
        session=self._session
        def done(data):self._annotations=data;self.annotationChanged.emit()
        self._submit('annotations-read',session.annotations,done)

    @Slot(str,str,bool)
    def filterSymbols(self,query,kind='',mangled=False):
        self._symbol_query=query;self._symbol_kind=kind;self._mangled=mangled
        if not self._result:return
        query=query.lower();rows=[]
        for group,key in (('export','exported'),('import','imported'),('local','local')):
            for i,s in enumerate(self._result['symbols'][key]):
                if kind and kind!=group:continue
                if query and query not in s['demangled'].lower() and query not in s['name'].lower():continue
                cells=[group,s['demangled']]
                if mangled:cells.append(s['name'])
                cells.extend([s['value'],str(s['size'])])
                rows.append({'key':group+':'+str(i),'cells':cells,'firstColor':{'export':'FG_BRIGHT','import':'FG2','local':'FG_MUTED'}[group]})
        self.symbols.replace(rows,animate=False);self.tablesChanged.emit()

    @Slot(str)
    def filterRelocations(self,query):
        self._reloc_query=query
        if not self._result:return
        query=query.lower()
        self.relocations.replace([{'key':str(i),'cells':[r['offset'],r['demangled'] or r['symbol'],r['reloc_section'],str(r['type'])]} for i,r in enumerate(self._result['relocations']) if not query or query in r['demangled'].lower() or query in r['symbol'].lower()],animate=False)

    @Slot(str)
    def filterStrings(self,query):
        self._string_query=query
        if self._result:self._strings=so_document.strings_text(self._result,query);self.changed.emit()

    def _build_paths(self):
        self._path_nodes=[];self._expanded=set()
        def visit(node,depth=0,current='',parent=''):
            for name,child in sorted(node.children.items()):
                key=f'n{len(self._path_nodes)}';self._expanded.add(key)
                self._path_nodes.append({'key':key,'parent':parent,'name':name,'kind':'Folder','size':'','depth':depth,'folder':True,'icon':'folder'})
                visit(child,depth+1,'' if name=='[embedded_in_binary]' else (current+'/'+name if current else name),key)
            for name in sorted(node.files):
                path=current+'/'+name if current else name
                self._path_nodes.append({'key':f'n{len(self._path_nodes)}','parent':parent,'name':name,'kind':'String','size':'','depth':depth,'folder':False,'path':path,'icon':file_icon(name)})
            for ef in node.embedded:
                lower=ef.file_type.lower();icon='image' if 'image' in lower else 'archive' if any(k in lower for k in ('archive','zip','apk','jar')) else 'icon_so' if 'elf' in lower or 'so' in lower else 'drive'
                self._path_nodes.append({'key':f'n{len(self._path_nodes)}','parent':parent,'name':ef.label,'kind':ef.file_type,'size':so_core.human_size(len(ef.data)),'depth':depth,'folder':False,'embedded':ef,'icon':icon})
        visit(self._result['path_tree']);self.filterPaths(self._path_query)

    @Slot(str)
    def filterPaths(self,query):
        self._path_query=query;query=query.lower();visible=set();nodes={row['key']:row for row in self._path_nodes}
        for row in self._path_nodes:
            if not query or any(query in str(row[key]).lower() for key in ('name','kind','size')):
                visible.add(row['key']);parent=row['parent']
                while parent:
                    visible.add(parent)
                    if query:self._expanded.add(parent)
                    parent=nodes[parent]['parent']
        self._visible_paths=[]
        for row in self._path_nodes:
            if row['key'] not in visible:continue
            parent=row['parent'];expanded=True
            while parent:
                if parent not in self._expanded:expanded=False;break
                parent=nodes[parent]['parent']
            if expanded:self._visible_paths.append(row)
        self.paths.replace([{**{k:v for k,v in row.items() if k!='embedded'},'expanded':row['key'] in self._expanded,'isEmbedded':'embedded' in row} for row in self._visible_paths],animate=False)

    @Slot(int)
    def togglePath(self,index):
        if not 0<=index<len(self._visible_paths):return
        row=self._visible_paths[index]
        if not row['folder']:return
        if row['key'] in self._expanded:self._expanded.remove(row['key'])
        else:self._expanded.add(row['key'])
        self.filterPaths(self._path_query)

    @Slot(int)
    def openPath(self,index):
        if not 0<=index<len(self._visible_paths):return
        row=self._visible_paths[index]
        if row['folder']:self.togglePath(index)
        elif 'embedded' in row:self._open_embedded(row['embedded'])
        else:
            self.pathRequested.emit(row['path'])
            self.inspectPathXrefs(row['path'])

    @Slot(str)
    def inspectPathXrefs(self,path_query):
        if not self._result or not path_query:return
        self._path_xrefs={'query':path_query,'loading':True,'summary_text':'Scanning binary sections for string locations & cross-references…','xrefs':[],'locations':[],'function_addresses':[],'agent_prompt':''}
        self.pathXrefsChanged.emit()
        path=self._path;result=self._result;session=self._session
        def work():
            if session is not None:return session.find_xrefs(result,path_query)
            return binary_xref.reconstruct_path_context(path,result,path_query)
        def loaded(payload):
            self._path_xrefs={**payload,'loading':False};self.pathXrefsChanged.emit()
        self._submit('path-xrefs',work,loaded)

    def _open_embedded(self,ef):
        self._embedded=ef;image=''
        if ef.file_type in ('PNG image','JPEG image','GIF image') and not QImage.fromData(ef.data).isNull():image='data:image/png;base64,'+base64.b64encode(ef.data).decode('ascii')
        self._embedded_info={'title':ef.label,'type':ef.file_type,'offset':hex(ef.offset),'size':so_core.human_size(len(ef.data)),'precision':t('so_precision_exact') if ef.exact_length else t('so_precision_approx'),'hex':so_document.hexdump(ef.data),'image':image,'nested':ef.file_type.startswith('Nested ELF')}
        self.embeddedChanged.emit();self.embeddedRequested.emit()

    @Slot(int)
    def openSection(self,index):
        if not self._result or not 0<=index<len(self._result['sections']):return
        section=self._result['sections'][index];name=section['name']
        if not name:return
        path=self._path
        def loaded(data):
            if data is None:self.messageRequested.emit('Info','Section ini tidak memiliki data di file (SHT_NOBITS).')
            else:self._open_embedded(so_core.EmbeddedFile('Section '+name,'section','Raw: '+name,0,data,True))
        self._submit('section',lambda:binary_core.section_bytes(path,section),loaded)

    @Slot(str,int)
    def disassemble(self,section,maximum):
        if not self._result:return
        if not section:self.messageRequested.emit('Info',t('so_no_exec_sec'));return
        if not 16<=maximum<=65536:return
        path=self._path;result=self._result
        def done(text):self._disasm=text
        self._submit('disassemble',lambda:binary_core.disassemble(path,result,section,maximum),done)

    @Slot()
    def analyzeNested(self):
        if self._embedded is not None:
            data=self._embedded.data
            self._submit('nested',lambda:so_document.nested_analysis(data),self.nestedRequested.emit)

    @staticmethod
    def _destination(url):return QUrl(url).toLocalFile() if url.startswith('file:') else url

    def _save(self,key,destination,work):
        path=self._destination(destination)
        if path:self._submit(key,lambda:work(path),lambda _:self.messageRequested.emit('OK',t('so_saved_ok',path=path)))

    @Slot(str)
    def saveEmbedded(self,destination):
        if self._embedded is None:return
        data=self._embedded.data
        def save(path):
            with open(path,'wb') as output:output.write(data)
        self._save('saveEmbedded',destination,save)

    @Slot(str)
    def exportJson(self,destination):
        if not self._result:self.messageRequested.emit('Info',t('so_not_ready'));return
        data=so_core.result_to_jsonable(self._result)
        data['annotations']=self._annotations
        def save(path):
            with open(path,'w',encoding='utf-8') as output:json.dump(data,output,indent=2,default=str)
        self._save('export',destination,save)

    @Slot(str)
    def compare(self,url):
        if not self._result:return
        path=self._destination(url)
        if not os.path.isfile(path):return
        result=self._result
        def compared(diff):
            a=os.path.basename(diff['file_a']);b=os.path.basename(diff['file_b'])
            lines=[f"=== ONLY IN {a} ({len(diff['only_in_a'])}) ===\n"]+['  + '+s for s in diff['only_in_a']]
            lines.append(f"\n=== ONLY IN {b} ({len(diff['only_in_b'])}) ===\n");lines.extend('  + '+s for s in diff['only_in_b'])
            self._comparison={**diff,'fileA':a,'fileB':b,'text':'\n'.join(lines)};self.compareChanged.emit();self.compareRequested.emit()
        self._submit('compare',lambda:so_core.compare_symbols(result,binary_core.analyze_file(path)),compared)

    @Slot(str)
    def copyText(self,text):self.copyRequested.emit(text)

    @Slot()
    def reconstructTree(self):
        self.reconstructSourceTree('')

    @Slot(str)
    def reconstructSourceTree(self,destination=''):
        if not self._result or not self._path:
            self.messageRequested.emit('Info',t('so_not_ready'));return
        dest=self._destination(destination) if destination else ''
        if not dest:
            dest=self._path+'_src'
        path=self._path;result=self._result;session=self._session
        def on_progress(c, t, f):
            self._status = f"Reconstructing sources: {c}/{t} ({os.path.basename(f)})"
            self.changed.emit()
            self.reconstructProgress.emit(c, t, f)
        def work():
            sess=session or binary_session.BinarySession(path,result.get('sha256'))
            return sess.reconstruct_tree(result,output_dir=dest,progress_callback=on_progress)
        def loaded(payload):
            out=payload.get('output_dir',dest)
            self._status = f"Reconstructed {payload.get('total_files',0)} files in {os.path.basename(out)}"
            self.changed.emit()
            self.reconstructFinished.emit(out)
            summary = f"Reconstructed {payload.get('total_files',0)} files & {payload.get('total_functions',0)} functions in:\n{out}"
            if payload.get('kernel_detected'):
                summary += "\nSee the exported README.md for kernel build requirements."
            self.messageRequested.emit('OK', summary)
        self._submit('reconstruct',work,loaded)

    @Slot()
    def close(self):
        self._closed=True;self._jobs.stop();self._generation+=1;self._pending.clear()
