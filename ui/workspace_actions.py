# SPDX-License-Identifier: MPL-2.0
"""Workspace commands; no widgets or nested modal event loops."""
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QUrl
from core import ui_lang
from core.workspace_files import WorkspaceFiles, validate_name


def operation(session, label, worker, done=None, *, settled=None, batch=None):
    if session._closing:return
    session._operation_serial+=1
    def finished(ok,result):
        session._history.append({"label":label,"ok":ok,"text":str(result or "")})
        session.statusChanged.emit()
        for directory in session.directories:
            if directory.base:directory.reloadMetadata()
        if not ok:
            if not str(result).startswith('Queue halted:'):session.message(result,label)
        elif done:done(result)
        if settled:settled(ok,result)
    try:return session._tasks.enqueue(label,worker,finished,batch=batch)
    except RuntimeError as error:
        session.message(str(error),label)
        if settled:settled(False,str(error))


def form(session,title,fields,callback,*,description="",width=400,accept=""):
    if not hasattr(session,"_forms"):session._forms={}
    token=str(session._operation_serial)+":"+str(len(session._forms))
    session._forms[token]=callback
    session.presentationRequested.emit("actionDialog",{"title":title,"fields":fields,"token":token,
                                       "description":description,"width":width,"accept":accept})


def _targets(session,data):
    return data.get("paths") or ([data["path"]] if data.get("path") else session._selection)


def _directory(session,path=None):
    if path:
        if path=='..':return session.active_tree._history[-1].path if session.active_tree._history else session.active_tree.base
        node=session.active_tree._nodes.get(path)
        if node:
            from core.tree_entries import APKTOOL_LINK_ROLE
            mirror=node.data(0,APKTOOL_LINK_ROLE)
            if mirror:return mirror
        if node and node.folder:return path
        if os.path.isdir(path):return path
        return os.path.dirname(path)
    if session._selection:return _directory(session,session._selection[0])
    return session.active_tree.location


def clipboard(session):
    value=session._clipboard
    if not value:return None
    files=WorkspaceFiles(value['base']);paths=[];physical_paths=[]
    for path in value['paths']:
        try:
            _,relative=files.logical(path)
            physical,_=files.view.resolve(path)
            if relative!='config' and physical and os.path.exists(physical):paths.append(path);physical_paths.append(physical)
        except (ValueError,OSError):pass
    if not paths:session._clipboard=None;return None
    return dict(value,paths=paths,phys=physical_paths)


def context_actions(session,path,paths):
    ref=session.mounts.reference(path,resolve=False)
    if ref and ref[1]:
        image,internal=ref
        from core.imgfs import is_text_filename
        node=session.active_tree._nodes.get(path);folder=bool(node and node.folder)
        return [{'key':'mountOpen','label':'Expand' if folder else 'Open (Text)','enabled':folder or is_text_filename(internal)},
                {'key':'mountExtract','label':'Extract to...'},
                {'key':'mountDelete','label':ui_lang.t('rc_delete'),'danger':True,'enabled':session.mounts.writable(image)}]
    tr=ui_lang.t
    can_paste=bool(clipboard(session))
    multiple=len(paths)>1
    if multiple:
        count=len(paths)
        items=[{'header':True,'label':tr('rc_batch_header',n=count)},{'separator':True},
               {'key':'delete','label':tr('rc_delete'),'danger':True},{'separator':True},
               {'key':'copy','label':tr('rc_batch_copy',n=count)},{'key':'cut','label':tr('rc_batch_cut',n=count)},
               {'key':'paste','label':tr('rc_paste'),'enabled':can_paste}]
        groups={}
        for target in paths:
            node=session.active_tree._nodes.get(target)
            badge=node.data(0,257) if node else None
            physical=(node.data(0,276) if node else None) or target
            folder=not os.path.isfile(physical) and bool(node and node.folder)
            action=('repack_erofs' if badge=='EROFS' else 'repack_ext' if badge in ('EXT2','EXT3','EXT4') else None) if folder else (
                'unpack_erofs' if badge=='EROFS' else 'unpack_ext' if badge in ('EXT2','EXT3','EXT4') else 'convert_sparse' if badge=='SPARSE' else None)
            if action:groups.setdefault(action,[]).append((target,badge))
        for action in ('unpack_erofs','unpack_ext','convert_sparse','repack_erofs','repack_ext'):
            members=groups.get(action,[])
            if members:
                items.extend([{'separator':True},{'key':'batchTool','tool':action,'paths':[path for path,_ in members],
                    'label':tr('rc_batch_'+action,n=len(members),fs='/'.join(sorted({badge for _,badge in members})))}])
        return items
    items=[{"key":"rename","label":tr("rc_rename"),"enabled":not multiple},
           {"key":"delete","label":tr("rc_delete"),"danger":True},
           {"separator":True},
           {"key":"copy","label":tr("rc_copy")},{"key":"cut","label":tr("rc_cut")},
           {"key":"paste","label":tr("rc_paste"),"enabled":can_paste},
           {"separator":True},
           {"key":"newFile","label":tr("rc_new_file")},{"key":"newFolder","label":tr("rc_new_folder")},
           {"key":"symlink","label":tr("rc_create_symlink")},
           {"separator":True},{"key":"explorer","label":tr("rc_open_explorer")}]
    if not multiple:
        from ui.workspace_tools import context_tools
        items.extend(context_tools(session,path))
    return items


def edit_metadata(session,directory,path,column,value):
    files=WorkspaceFiles(directory.base)
    operation(session,"SELinux" if column==1 else "Permissions",lambda:files.edit_metadata(path,column,value))


def dispatch(session,action,data):
    if data.get('path')=='..':data=dict(data,path=_directory(session,'..'),paths=[])
    paths=_targets(session,data)
    path=paths[0] if paths else session.active_tree.location
    if action in ('mount','unmount','usage','mountOpen','mountDelete','mountExtract'):
        if action=='mount':session.mounts.mount(path)
        elif action=='unmount':session.mounts.unmount(path)
        elif action=='usage':session.mounts.usage(path)
        elif action=='mountOpen':
            node=session.active_tree._nodes.get(path)
            if node and node.folder:
                if session.active_tree.displayMode=='single_dir':session.active_tree.activate(path)
                else:session.presentationRequested.emit('mountExpand',{'path':path,'treeId':session.activeTreeId})
            else:session.mounts.open(path)
        elif action=='mountDelete':session.mounts.delete(path)
        else:session.presentationRequested.emit('mountExtract',{'path':path})
        return
    base=session.active_tree.base
    files=WorkspaceFiles(base)
    if action=="submitForm":
        callback=getattr(session,"_forms",{}).pop(data.get("token"),None)
        if callback:
            try:callback(data.get("values",{}))
            except Exception as error:
                from core.command_context import failure_from_exception
                session.message(failure_from_exception(error))
    elif action=="contextMenu":
        data=dict(data)
        data["actions"]=context_actions(session,path,paths)
        if session.agentChat.available:data['actions']+=[{'separator':True},{'key':'agentSend','label':'Send to agent'}]
        data['origin']='tree'+str(session.activeTreeId)
        ref=session.mounts.reference(path,resolve=False)
        data['menuKind']='mounted' if ref and ref[1] else 'batch' if len(paths)>1 else 'single'
        session.presentationRequested.emit("contextMenu",data)
    elif action=='batchTool':
        from ui.workspace_tools import dispatch_tool
        batch=session._tasks.loading.start_batch('context-batch',len(paths),label=data.get('label') or data['tool'])
        for target in paths:dispatch_tool(session,data['tool'],target,{'batch':batch})
    elif action=="import":session.presentationRequested.emit("import",{})
    elif action=="importPaths":
        destination=_directory(session)
        urls=data.get("urls",[])
        sources=[url.toLocalFile() if isinstance(url,QUrl) else QUrl(str(url)).toLocalFile() for url in urls]
        session.transfers.start(base,[(os.path.dirname(source),source) for source in sources],destination)
    elif action in ("newFile","newFolder"):
        directory=_directory(session,path)
        session.beginCreate(directory,action=="newFolder")
    elif action=="rename":
        if not paths:return
        session.beginInlineEdit(path)
    elif action=="delete":
        if not paths:return
        def remove():
            def worker():
                for target in paths:files.delete(target)
                return ""
            operation(session,ui_lang.t("rc_delete"),worker)
        session.message("\n".join(os.path.basename(target) for target in paths),ui_lang.t("rc_delete"),confirm=remove,danger=True,
                        accept=ui_lang.t("rc_delete"))
    elif action in ("copy","cut"):
        if paths:
            previous=session._clipboard;session._clipboard={"paths":list(dict.fromkeys(paths)),"base":base,"move":action=="cut"}
            value=clipboard(session)
            if value:
                session._clipboard=value;count=len(value['paths'])
                session._tasks.loading.set_finished(
                    f"{ui_lang.t('rc_copy' if action=='copy' else 'rc_cut')}: {count} item{'s' if count!=1 else ''}")
            else:session._clipboard=previous
    elif action=="paste":
        value=clipboard(session)
        if not value:return
        destination=_directory(session,data.get("path"))
        sources=value['paths']
        for source,physical in zip(sources,value['phys']):
            if not os.path.isdir(physical):continue
            try:inside=os.path.commonpath([os.path.normcase(destination),os.path.normcase(source)])==os.path.normcase(source)
            except ValueError:inside=False
            if inside:
                session.message(ui_lang.t('rc_paste_into_self'),ui_lang.t('rc_paste'));return
        if value['move']:
            session._clipboard=None
            sources=[source for source in sources if os.path.normcase(os.path.dirname(source))!=os.path.normcase(destination) or os.path.normcase(value['base'])!=os.path.normcase(base)]
        if sources:session.transfers.start(base,[(value['base'],source) for source in sources],destination,
                                         move=value['move'],duplicate_in_place=not value['move'])
    elif action=="duplicate":operation(session,"Duplicate",lambda:files.duplicate(path))
    elif action=="openText":session.openAs(path,"editor")
    elif action=="openHex":session.openAs(path,"hex")
    elif action=="editContext":
        session.beginInlineEdit(path,1)
    elif action=="editPermission":
        node=session.active_tree._nodes.get(path)
        permission=node.texts[2] if node else "rw-r--r--"
        session._permission_target=(session.active_tree,path)
        session.presentationRequested.emit("permissions",{"current":permission})
    elif action=="applyPermission":
        directory,target=session._permission_target
        edit_metadata(session,directory,target,2,data["value"])
    elif action=="history":session.presentationRequested.emit("history",{})
    elif action=="clearHistory":
        session._history=[];session._tasks.clear_history();session.statusChanged.emit()
    else:
        from ui.workspace_tools import dispatch_tool
        dispatch_tool(session,action,path,data)
