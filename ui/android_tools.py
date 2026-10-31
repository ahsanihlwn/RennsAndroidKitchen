# SPDX-License-Identifier: MPL-2.0
"""Qt Quick Android Tools state; QProcess owns every device command."""
import os
import re
from contextvars import copy_context

from PySide6.QtCore import QObject,Property,Signal,Slot,QProcess,QTimer,QFileSystemWatcher
from shiboken6 import isValid
from core.android_tools import parse_fastboot_output,_fmt_size,_fmt_sz2,_parse_size_arg,flow_layout
from core.app_paths import BIN_DIR
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


class AndroidTools(QObject):
    changed=Signal()
    chartChanged=Signal()
    deviceChanged=Signal()
    modalRequested=Signal(str,'QVariantMap')

    def __init__(self,workspace,parent=None):
        super().__init__(parent)
        self.workspace=workspace
        self._jobs=AsyncJobs(self)
        self._images=RowsModel(parent=self)
        self._partitions=RowsModel(parent=self)
        self._queue=RowsModel(parent=self)
        self._cache={}
        self._tree=0
        self._selected=''
        self._parsed={}
        self._chart=flow_layout({})
        self._badge='no device'
        self._mode=''
        self._status='Ready'
        self._placeholder='Connect a device in fastboot mode,\nthen press Refresh.'
        self._log=''
        self._refreshing=False
        self._refresh_generation=0
        self._processes=set()
        self._probe_processes=set()
        self._flash=None
        self._flash_task=None
        self._failed=False
        self._closed=False
        self._active=False
        self._watcher=QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._rescan_later)
        self._scan_timer=QTimer(self);self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self.scan)
        self._next_timer=QTimer(self);self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self.flashAll)

    @Property(QObject,constant=True)
    def images(self):return self._images
    @Property(QObject,constant=True)
    def partitions(self):return self._partitions
    @Property(QObject,constant=True)
    def queue(self):return self._queue
    @Property('QVariantMap',notify=chartChanged)
    def chart(self):return self._chart
    @Property('QStringList',constant=True)
    def trees(self):return [name for name in (self.workspace.p1,self.workspace.p2) if name]
    @Property(int,notify=changed)
    def treeIndex(self):return self._tree
    @Property(str,notify=changed)
    def selectedImage(self):return self._selected
    @Property(str,notify=changed)
    def badge(self):return self._badge
    @Property(str,notify=changed)
    def mode(self):return self._mode
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(str,notify=changed)
    def placeholder(self):return self._placeholder
    @Property(str,notify=changed)
    def partitionLog(self):return self._log
    @Property(bool,notify=changed)
    def refreshing(self):return self._refreshing
    @Property(bool,notify=changed)
    def running(self):return self._flash is not None or self._flash_task is not None
    @Property(int,notify=changed)
    def flashProgress(self):
        rows=self._queue.snapshot()
        if not rows:return 0
        if any(row['state']=='running' and row['progress']<0 for row in rows):return -1
        return int(sum(100 if row['state']=='done' else max(0,row['progress']) for row in rows)/len(rows))
    @Property(bool,notify=changed)
    def failed(self):return self._failed
    @Property(bool,notify=changed)
    def hasSlot(self):return bool(self._parsed.get('has_slot'))
    @Property(str,notify=changed)
    def slot(self):return self._parsed.get('slot') or 'a'

    @Slot()
    def open(self):
        self._active=True
        self.scan()

    @Slot()
    def dismiss(self):
        self._active=False
        self._scan_timer.stop()
        paths=self._watcher.directories()
        if paths:self._watcher.removePaths(paths)

    @Slot(int)
    def selectTree(self,index):
        if not 0<=index<len(self.trees):return
        self._tree=index;self._selected='';self.changed.emit();self.scan()

    @Slot(str)
    def selectImage(self,path):self._selected=path;self.changed.emit()

    def _rescan_later(self,_):
        if self._active:self._scan_timer.start(350)

    @Slot()
    def scan(self):
        index=self._tree;base=os.path.join(self.workspace.project,self.trees[index])
        def work(_=None):
            images=[];directories=[]
            for directory,dirs,files in os.walk(base):
                dirs[:]=sorted(d for d in dirs if d not in ('config','neocat_config','automation','.git','neobranch')
                               and not os.path.islink(os.path.join(directory,d)) and not os.path.isjunction(os.path.join(directory,d)))
                directories.append(directory)
                for name in sorted(files):
                    path=os.path.join(directory,name)
                    if name.lower().endswith('.img') and not os.path.islink(path):
                        try:images.append({'key':path,'path':path,'name':os.path.relpath(path,base)[:-4],'size':_fmt_size(os.path.getsize(path))})
                        except OSError:pass
            return True,(images,directories)
        def done(ok,result):
            if not ok:
                self._status='Image scan failed: '+str(result);self.changed.emit();return
            self._cache[index]=result[0]
            if index!=self._tree:return
            self._images.replace(result[0],animate=False)
            old=self._watcher.directories()
            if old:self._watcher.removePaths(old)
            if self._active and result[1]:self._watcher.addPaths(result[1])
            self.changed.emit()
        self._jobs.submit('images:'+str(index),work,None,done)

    def _program(self,name):
        from core.binary_handler import resolve_binary
        return resolve_binary(BIN_DIR,name+'.exe') or name

    def _launch(self,program,args,callback,*,timeout=15000,stream=None,report_errors=True):
        context=copy_context()
        process=QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        self._processes.add(process)
        output=[];buffer=[''];settled=[False]
        terminal=[None]
        timer=QTimer(process);timer.setSingleShot(True)
        timer.timeout.connect(process.kill)
        def read():
            if self._closed or not isValid(process):return
            value=bytes(process.readAllStandardOutput()).decode('utf-8',errors='replace')
            output.append(value)
            if terminal[0] is not None:terminal[0].feed(value)
            if stream:
                buffer[0]+=value.replace('\r\n','\n').replace('\r','\n')
                while '\n' in buffer[0]:
                    line,buffer[0]=buffer[0].split('\n',1);stream(line.rstrip())
        def finish(code,*_):
            if settled[0] or self._closed or not isValid(process):return
            settled[0]=True;timer.stop();read()
            if stream and buffer[0]:stream(buffer[0])
            self._processes.discard(process)
            self._probe_processes.discard(process)
            if not self._closed:
                from core.error_reporting import error_checkpoint, error_reported_since, report_fatal
                checkpoint = error_checkpoint()
                try:
                    callback(code,''.join(output))
                finally:
                    if report_errors and code and not error_reported_since(checkpoint, ''.join(output).strip() or process.errorString()):
                        report_fatal('Android tools: '+os.path.basename(program)+'\n\n'+(''.join(output).strip() or process.errorString()),owner=self,application_error=False)
            process.deleteLater()
        process.readyReadStandardOutput.connect(lambda:context.copy().run(read))
        process.finished.connect(lambda *args:context.copy().run(finish,*args))
        process.errorOccurred.connect(lambda error:context.copy().run(finish,-1) if error==QProcess.FailedToStart else None)
        process.setProgram(program);process.setArguments(args)
        if hasattr(self.workspace,'runTerminalProcess'):
            terminal[0]=self.workspace.runTerminalProcess(process,program,args)
        else:process.start()
        if timeout:timer.start(timeout)
        return process

    @Slot()
    def refresh(self):
        if self.running:return
        if self._refreshing:return
        self._refresh_generation+=1;generation=self._refresh_generation
        self._refreshing=True;self.changed.emit()
        finished=set();probes=[];winner=[False]
        def done(protocol,code,output):
            if self._closed or generation!=self._refresh_generation or winner[0]:return
            finished.add(protocol)
            parsed=parse_fastboot_output(output) if protocol=='fastboot' else None
            if parsed and (parsed['device'] or parsed['serialno'] or parsed['partitions'] or parsed['slot']):
                winner[0]=True;self.applyDevice(parsed)
            elif protocol=='adb' and code==0:
                lines=[line.strip() for line in output.splitlines() if '\t' in line and not line.startswith('List of')]
                if lines:
                    serial,mode=lines[0].split('\t',1)
                    winner[0]=True;self._refreshing=False
                    self._badge=f'{serial}  ·  adb  ·  {mode}';self._mode='adb'
                    self._status='ADB device detected — reboot buttons switched to adb mode.'
                    self._placeholder='';self.changed.emit();self.deviceChanged.emit()
            if winner[0]:
                for process in tuple(self._probe_processes & self._processes):
                    if process.state()!=QProcess.NotRunning:process.kill()
            elif len(finished)==2:
                self._refreshing=False;self._mode='';self._badge='no device'
                self._status='No device connected.';self._placeholder=self._status
                self._parsed={};self._chart=flow_layout({});self._partitions.replace([],animate=False)
                self.changed.emit();self.chartChanged.emit()
        probes.append(self._launch(self._program('fastboot'),['getvar','all'],lambda code,output:done('fastboot',code,output),timeout=5000,report_errors=False))
        probes.append(self._launch(self._program('adb'),['devices'],lambda code,output:done('adb',code,output),timeout=5000,report_errors=False))
        self._probe_processes.update(probes)

    def applyDevice(self,parsed):
        self._parsed=parsed;self._refreshing=False;self._mode='fastboot'
        self._badge=parsed.get('device') or 'unknown'
        if parsed.get('serialno'):self._badge+='  ·  '+parsed['serialno']
        if parsed.get('slot'):self._badge+='  ·  slot '+parsed['slot']
        self._badge+='  ·  '+('unlocked' if parsed.get('unlocked') else 'locked')
        self._placeholder='';self._chart=flow_layout(parsed)
        self._rebuild_partitions();self.chartChanged.emit();self.changed.emit();self.deviceChanged.emit()

    def _rebuild_partitions(self):
        parts=self._parsed.get('partitions',{})
        logical={k:v for k,v in parts.items() if v.get('is_logical')}
        physical={k:v for k,v in parts.items() if not v.get('is_logical')}
        total=sum(v.get('size_a') if v.get('size_a') is not None else v.get('size') or 0 for v in logical.values())
        super_size=(parts.get('super') or {}).get('size') or 0
        rows=[]
        if logical:
            info='super  '+_fmt_sz2(super_size or total) if super_size or total else ''
            if super_size>total:info+='   ·   unallocated  '+_fmt_sz2(super_size-total)
            rows.append({'key':'logical','kind':'header','name':'Logical Partitions','info':info,'create':True})
        for name,info in sorted(logical.items()):
            size=info.get('size_b') or 0 if info.get('has_slot') and self.slot=='b' else (info.get('size_a') if info.get('size_a') is not None else info.get('size') or 0)
            rows.append({'key':name,'kind':'logical','name':name,'size':_fmt_sz2(size),'slotted':info.get('has_slot',False),'type':info.get('type','raw')})
        if logical and super_size>total:rows.append({'key':'unallocated','kind':'unallocated','name':'Unallocated','size':_fmt_sz2(super_size-total)})
        if physical:rows.append({'key':'physical','kind':'header','name':'PHYSICAL PARTITIONS','info':'','create':False})
        for name,info in sorted(physical.items()):
            size=info.get('size_'+self.slot) if self.hasSlot and info.get('has_slot') else (info.get('size') if info.get('size') is not None else info.get('size_a'))
            rows.append({'key':name,'kind':'physical','name':name,'size':_fmt_sz2(size),'slotted':self.hasSlot and info.get('has_slot',False),'type':info.get('type') or 'raw'})
        self._partitions.replace(rows,animate=False)

    def command(self,args,program='fastboot'):
        if self.running:
            self._status='A flash is in progress. Device command was not started.';self.changed.emit();return
        self._log='→  '+program+' '+' '.join(args);self.changed.emit()
        def done(code,output):
            self._status=f'{program} {" ".join(args)}  (code {code})'+(': '+output.strip() if output.strip() else '')
            self.changed.emit()
        self._launch(self._program(program),args,done)

    @Slot(int)
    def reboot(self,index):
        if not self._mode or not 0<=index<4:return
        target=(['fastboot','bootloader','recovery',''] if self._mode=='adb' else ['bootloader','fastboot','recovery',''])[index]
        self.command(['reboot']+([target] if target else []),'adb' if self._mode=='adb' else 'fastboot')

    @Slot(str,bool)
    def deletePartition(self,name,slotted):
        self.command(['delete-logical-partition',name+'_'+self.slot if slotted else name])

    @Slot(str,str,result=bool)
    def resizePartition(self,name,size):
        amount=_parse_size_arg(size)
        if amount<0:return False
        self.command(['resize-logical-partition',name,str(amount)]);return True

    @Slot(str,str,bool,result=bool)
    def createPartition(self,name,size,append_slot):
        amount=_parse_size_arg(size)
        if amount<0 or not name.strip():return False
        name=name.strip()
        if append_slot and self.hasSlot and not name.endswith('_'+self.slot):name+='_'+self.slot
        self.command(['create-logical-partition',name,str(amount)]);return True

    @Slot(str,str,str)
    def assign(self,partition,name,path):
        if not path:
            image=next((r for r in self._images.snapshot() if r['path']==self._selected),None)
            if image:name,path=image['name'],image['path']
        if not path:return
        rows=self._queue.snapshot()
        existing=next((i for i,r in enumerate(rows) if r['partition']==partition),None)
        if existing is not None and rows[existing]['state']=='running':return
        row={'key':partition,'partition':partition,'image':name,'path':path,'state':'queued','progress':0,'message':''}
        if existing is None:rows.append(row)
        else:rows[existing]=row
        self._queue.replace(rows,animate=False)
        self._node_state(partition,'queued',name);self.changed.emit()

    def _node_state(self,partition,state,image=None):
        for node in self._chart['nodes']:
            if node['key']==partition:
                node['state']=state
                if image is not None:node['image']=image
        self.chartChanged.emit()

    @Slot()
    def flashAll(self):
        if self._closed or self.running:return
        self._probe_processes.intersection_update(self._processes)
        if self._probe_processes:
            self._refresh_generation+=1;self._refreshing=False
            pending=set(self._probe_processes)
            def stopped(*_):
                pending.intersection_update(self._processes)
                if not pending and not self._closed:
                    self._flash_task=None;self.flashAll()
            for process in tuple(pending):
                process.finished.connect(stopped)
                if process.state()!=QProcess.NotRunning:process.kill()
                else:pending.discard(process)
            self._probe_processes.clear()
            if pending:
                self._flash_task='stopping probes';self.changed.emit();return
        rows=self._queue.snapshot()
        index=next((i for i,row in enumerate(rows) if row['state']=='queued'),None)
        if index is None:self._status='All queued jobs are done.';self.changed.emit();return
        row=rows[index];row['state']='running';row['progress']=-1
        self._failed=False;self._queue.replace(rows,animate=False);self._node_state(row['partition'],'flashing')
        self._status=f'{row["partition"]}  {index+1}/{len(rows)}'
        chunks=[0,0];error=[''];completion=[None];progress=[None]
        def stream(line):
            self._status=line
            if 'Sending sparse' in line:
                match=re.search(r'(\d+)/(\d+)',line)
                if match:
                    chunks[0]=int(match[2]);row['progress']=int((int(match[1])-1)/chunks[0]*100) if chunks[0] else -1
            elif 'Writing' in line and 'OKAY' in line and chunks[0]:
                chunks[1]+=1;row['progress']=min(99,int(chunks[1]/chunks[0]*100))
            if 'FAILED' in line or 'ERROR' in line:error[0]=line
            current=self._queue.snapshot();current[index]=dict(row);self._queue.replace(current,animate=False);self.changed.emit()
            if progress[0]:progress[0](row['progress'],line)
        def done(code,output):
            self._flash=None;self._flash_task=None;ok=code==0 and not error[0]
            row['state']='done' if ok else 'error';row['progress']=100 if ok else row['progress']
            row['message']='Done' if ok else error[0] or output.strip() or f'exit code {code}'
            current=self._queue.snapshot();current[index]=dict(row);self._queue.replace(current,animate=False)
            self._node_state(row['partition'],row['state']);self._failed=not ok
            self._status=f'[FLASH] {row["partition"]} → {row["message"]}';self.changed.emit()
            if ok:self.flashAll()
            detail=output.strip() or row['message']
            if not ok:detail=f'fastboot flash {row["partition"]} exited with code {code}\n'+detail
            from core.command_context import CommandFailure
            completion[0](ok,detail if ok else CommandFailure(detail))
        def start(completed,updated):
            completion[0]=completed;progress[0]=updated
            updated(-1,'Starting fastboot')
            self._flash=self._launch(self._program('fastboot'),['flash',row['partition'],row['path']],done,timeout=0,stream=stream)
        self._flash_task='starting'
        self._flash_task=self.workspace._tasks.enqueue_external('Flash '+row['partition'],start,lambda *_:None)
        self.changed.emit()

    @Slot()
    def clearQueue(self):
        if self.running:return
        self._next_timer.stop();self._queue.replace([],animate=False);self._failed=False
        for node in self._chart['nodes']:node.update(state='idle',image='')
        self.chartChanged.emit();self.changed.emit()

    def close(self):
        self._closed=True;self.dismiss();self._next_timer.stop();self._jobs.stop()
        for process in tuple(self._processes):process.kill()
