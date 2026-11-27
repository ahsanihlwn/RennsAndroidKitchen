# SPDX-License-Identifier: MPL-2.0
import os
import tempfile
from PySide6.QtCore import QObject,Slot,QUrl
from core.workspace_transfer import plan_transfer,is_self_drop
from services.async_jobs import AsyncJobs


class WorkspaceTransfers(QObject):
    def __init__(self,workspace):
        super().__init__(workspace);self.workspace=workspace;self._jobs=AsyncJobs(self,limit=None)
        self._plan=None;self._after=None;self._serial=0;self._temporary=[]

    def start(self,base,sources,directory,*,move=False,after=None,resources=(),duplicate_in_place=False,drag=False):
        self._serial+=1;token=self._serial
        def work():
            plan=plan_transfer(base,sources,directory,move=move,duplicate_in_place=duplicate_in_place,drag=drag)
            plan.resources=resources
            return True,plan
        def done(ok,result):
            if token!=self._serial:return
            if not ok:self.workspace.message(str(result),'Move' if move else 'Copy');return
            self._plan=result;self._after=after
            if result.conflicts:self.workspace.presentationRequested.emit('fileConflicts',{'rows':result.conflicts,'token':token})
            else:self.resolve(token,{})
        self._jobs.submit('preflight',work,None,done)

    @Slot(int,'QVariantMap')
    def resolve(self,token,decisions):
        if token!=self._serial or self._plan is None:return
        plan,self._plan=self._plan,None
        after,self._after=self._after,None
        if decisions.get('__cancel__'):return
        from ui.workspace_actions import operation
        transfer_targets=tuple(dict.fromkeys(
            [*(item['source'] for item in plan.items),
             *(item['target'] for item in plan.items),
             *(src for _f,src,_t in plan.merged)]
        ))
        operation(self.workspace,'Move' if plan.move else 'Copy',lambda:plan.execute(decisions),after,
                  lane='light',targets=transfer_targets)

    @Slot(int,str,'QVariantMap',bool)
    def dropInto(self,tree_id,directory,data,move):
        destination=self.workspace.tree if tree_id==0 else self.workspace.secondTreeModel
        if self.workspace.mounts.reference(directory,resolve=False):return
        if hasattr(self.workspace,'isPathLocked') and self.workspace.isPathLocked(directory):return
        if data.get('mounted'):
            from core.workspace_files import WorkspaceFiles
            WorkspaceFiles(destination.base).logical(directory,allow_root=True)
            temporary=tempfile.TemporaryDirectory(prefix='rak-mounted-drop-');self._temporary.append(temporary)
            selected=[]
            for image,internal,name in data['mounted']:
                internal=internal.replace('\\','/').strip('/');key=os.path.normcase(os.path.abspath(image))
                if any(old_key==key and (not old_internal or internal==old_internal or internal.startswith(old_internal+'/')) for old_key,old_internal,_,_ in selected):continue
                selected=[row for row in selected if row[0]!=key or (internal and not row[1].startswith(internal+'/'))]
                selected.append((key,internal,name,image))
            def work():
                from core import imgfs
                from core.workspace_files import validate_name
                outputs=[];names=set()
                for _,internal,name,image in selected:
                    output=validate_name(os.path.basename(internal) if internal else name)
                    if output.casefold() in names:raise ValueError('Mounted selections have the same output name: '+output)
                    names.add(output.casefold())
                    target=os.path.join(temporary.name,output)
                    if not internal:os.makedirs(target,exist_ok=True)
                    imgfs.extract_path(image,internal,target if not internal else temporary.name)
                    outputs.append(target)
                return True,outputs
            def done(ok,result):
                if ok:self.start(destination.base,[(temporary.name,path) for path in result],directory,resources=(temporary,))
                else:self.workspace.message(str(result),'Extract mounted files')
            self._jobs.submit('mounted-drop',work,None,done)
            return
        if data.get('archive'):
            temporary=tempfile.TemporaryDirectory(prefix='rak-archive-drop-');self._temporary.append(temporary)
            def work():
                from core.archive_document import ArchiveReader
                with ArchiveReader(data['archive']) as reader:
                    return True,reader.extract_selection_to(data.get('members',[]),temporary.name)
            def done(ok,result):
                if ok:self.start(destination.base,[(temporary.name,path) for path in result],directory,resources=(temporary,))
                else:self.workspace.message(str(result),'Extract archive')
            self._jobs.submit('archive-drop',work,None,done)
            return
        paths=data.get('paths',[]);base=data.get('base')
        if not paths:
            paths=[url.toLocalFile() if isinstance(url,QUrl) else QUrl(str(url)).toLocalFile() for url in data.get('urls',[])]
        if base and move:
            paths=[path for path in paths if not is_self_drop(path,directory) and
                   (os.path.normcase(os.path.dirname(path))!=os.path.normcase(directory) or os.path.normcase(base)!=os.path.normcase(destination.base))]
        if not paths:return
        self.start(destination.base,[(base or os.path.dirname(path),path) for path in paths if path],directory,
                   move=move and bool(base),drag=move and bool(base))

    def close(self):
        self._serial+=1;self._plan=None;self._jobs.stop()
        # Extraction/transfer workers retain the TemporaryDirectory objects until
        # their callbacks finish; their finalizers remove only owned scratch data.
        self._temporary=[]
