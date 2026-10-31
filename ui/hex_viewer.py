# SPDX-License-Identifier: MPL-2.0
"""Native HexViewer state; worker results never depend on a living delegate."""
import os
import threading

from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl
from core.hex_document import PAGE_SIZES,analyze_binary,read_page,search_pattern,find_pattern
from core.bin_analyzer import extract_strings
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


class HexDocument(QObject):
    changed=Signal()
    pageChanged=Signal()
    stringsChanged=Signal()
    jumpToHex=Signal()
    messageRequested=Signal(str,str)
    copyRequested=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self._jobs=AsyncJobs(self,limit=3);self._closed=False;self._generation=0
        self._path='';self._file_size=0;self._page=0;self._page_size=4096
        self._raw=b'';self._page_text='';self._status='Analyzing binary...';self._analysis={}
        self._strings=[];self._filtered=[];self._filter='';self._minimum=4
        self._rows=RowsModel(parent=self);self._pending=set();self._cancel=threading.Event()

    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property(str,notify=changed)
    def path(self):return self._path
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(bool,notify=changed)
    def busy(self):return bool(self._pending)
    @Property('QVariantMap',notify=changed)
    def analysis(self):return self._analysis
    @Property(str,notify=pageChanged)
    def pageText(self):return self._page_text
    @Property(int,notify=pageChanged)
    def page(self):return self._page
    @Property(int,notify=pageChanged)
    def pageSize(self):return self._page_size
    @Property(int,notify=pageChanged)
    def pageCount(self):return max(1,(self._file_size+self._page_size-1)//self._page_size)
    @Property(int,notify=stringsChanged)
    def stringCount(self):return len(self._filtered)
    @Property(int,notify=stringsChanged)
    def minimumLength(self):return self._minimum
    @Property(bool,notify=stringsChanged)
    def canExport(self):return bool(self._strings)
    @Property(QUrl,notify=changed)
    def exportUrl(self):
        name=os.path.splitext(os.path.basename(self._path))[0]+'_strings.txt'
        return QUrl.fromLocalFile(os.path.join(os.path.dirname(self._path),name))

    def _submit(self,key,work,done):
        generation=self._generation;self._pending.add(key);self.changed.emit()
        def run(_=None):return True,work()
        def finished(ok,result):
            if generation!=self._generation or self._closed:return
            self._pending.discard(key)
            if ok:done(result)
            elif key=='analysis':self._status='Analysis failed.'
            elif key=='export':self.messageRequested.emit('Export Failed',str(result))
            elif key=='search':done(-1)
            self.changed.emit()
        self._jobs.submit(key,run,None,finished)

    @Slot(str)
    def load(self,path):
        if self._path==path and self._analysis:return
        self._path=path;self.reload()

    @Slot()
    def reload(self):
        if self._closed or not self._path:return
        self._generation+=1;self._jobs.reset();self._pending.clear();self._cancel.set();self._cancel=threading.Event()
        try:self._file_size=os.path.getsize(self._path)
        except OSError as error:
            self._status='Analysis failed.';self.messageRequested.emit('Binary Viewer',str(error));self.changed.emit();return
        self._status='Analyzing binary...';self._page=min(self._page,self.pageCount-1)
        path=self._path
        def analyzed(result):
            self._analysis={key:value for key,value in result.items() if key!='strings'};self._strings=result['strings'];self._filter_rows()
            self._status=f'Loaded: {result["format"].get("format","Generic Binary")} | {len(self._strings):,} strings'
            # A minimum-length change made during analysis wins over its defaults.
            if self._minimum!=4:self.setMinimumLength(self._minimum)
        self._submit('analysis',lambda:analyze_binary(path),analyzed)
        self._load_page(self._page,initial=True)

    @Slot(int)
    def goPage(self,page):
        self._load_page(page)

    def _load_page(self,page,*,status='',initial=False):
        if self._closed or page<0 or page>=self.pageCount:return
        self._page=page;path=self._path;size=self._page_size
        self.pageChanged.emit()
        def loaded(result):
            self._raw,self._page_text=result;offset=page*size;end=offset+len(self._raw)-1 if self._raw else 0
            if status:self._status=status
            elif not initial or 'analysis' in self._pending:
                self._status=f'Offset: 0x{offset:08X} - 0x{end:08X} ({len(self._raw):,} / {self._file_size:,} Bytes) | Total Pages: {self.pageCount}'
            self.pageChanged.emit()
        self._submit('page',lambda:read_page(path,page,size),loaded)

    @Slot(int)
    def setPageSize(self,size):
        if size not in PAGE_SIZES:return
        self._page_size=size;self.goPage(0)

    @Slot(str)
    def jump(self,text):
        try:offset=int(text.strip(),16 if text.strip().lower().startswith('0x') else 10)
        except ValueError:return
        offset=max(0,min(offset,self._file_size-1)) if self._file_size else 0
        self.goPage(offset//self._page_size)

    @Slot(str,bool)
    def find(self,query,forward=True):
        if not query.strip():return
        pattern=search_pattern(query)
        if not pattern:return
        self._cancel.set();self._cancel=threading.Event();cancel=self._cancel
        path=self._path;start=self._page*self._page_size+(1 if forward else -1)
        def found(offset):
            if cancel.is_set():return
            if offset<0:self._status=f"Pattern '{query.strip()}' not found."
            else:
                self._status=f'Match found at offset 0x{offset:08X}'
                self._load_page(offset//self._page_size,status=self._status)
        self._submit('search',lambda:find_pattern(path,pattern,start,forward,cancel),found)

    @Slot(str)
    def filterStrings(self,text):
        self._filter=text.strip().lower();self._filter_rows()

    def _filter_rows(self):
        query=self._filter
        self._filtered=[row for row in self._strings if not query or query in row['text'].lower() or query in f'0x{row["offset"]:08x}']
        self._rows.replace([{**row,'key':str(row['offset'])+':'+row['encoding'],'offsetHex':f'0x{row["offset"]:08X}'} for row in self._filtered],animate=False)
        self.stringsChanged.emit()

    @Slot(int)
    def setMinimumLength(self,value):
        if not 3<=value<=32:return
        self._minimum=value;path=self._path;self.stringsChanged.emit()
        def extracted(rows):self._strings=rows;self._filter_rows()
        self._submit('strings',lambda:extract_strings(path,min_len=value,max_strings=15000),extracted)

    @Slot(int)
    def openString(self,row):
        if 0<=row<len(self._filtered):
            offset=self._filtered[row]['offset'];self._status=f'Jumped to string at offset 0x{offset:08X}'
            self._load_page(offset//self._page_size,status=self._status)
            self.jumpToHex.emit();self.changed.emit()

    @Slot(str)
    def copy(self,kind):
        if kind in ('hex','ascii') and 'page' in self._pending:
            # Capture the page at the click, even if another page is requested
            # before its read completes. Never copy the previous page's cache.
            path,page,size=self._path,self._page,self._page_size
            self._submit('copy',lambda:read_page(path,page,size)[0],lambda raw:self._copy_page(kind,raw))
            return
        if kind in ('hex','ascii'):
            self._copy_page(kind,self._raw)
            return
        if kind=='strings':
            rows=self._filtered or self._strings;value='\n'.join(row['text'] for row in rows);status=f'Copied {len(rows)} strings to clipboard.'
        else:value=self._analysis.get('hashes',{}).get(kind,'Calculating...');status=self._status
        self.copyRequested.emit(value);self._status=status;self.changed.emit()

    def _copy_page(self,kind,raw):
        value=' '.join(f'{byte:02X}' for byte in raw) if kind=='hex' else ''.join(chr(byte) if 32<=byte<=126 else '.' for byte in raw)
        self.copyRequested.emit(value)
        self._status='Copied current page '+('hex' if kind=='hex' else 'ASCII')+' to clipboard.'
        self.changed.emit()

    @Slot(str)
    def exportStrings(self,url):
        if not self._strings:return
        destination=QUrl(url).toLocalFile() if url.startswith('file:') else url
        rows=list(self._filtered or self._strings)
        def export():
            with open(destination,'w',encoding='utf-8') as output:
                for row in rows:output.write(f'0x{row["offset"]:08X}\t[{row["encoding"]}]\t{row["text"]}\n')
            return len(rows)
        self._submit('export',export,lambda count:self.messageRequested.emit('Export Complete',f'Exported {count} strings to {destination}'))

    @Slot()
    def close(self):
        self._closed=True;self._cancel.set();self._jobs.stop();self._pending.clear()
