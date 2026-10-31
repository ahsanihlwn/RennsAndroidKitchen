# SPDX-License-Identifier: MPL-2.0
"""Android placeholder/native links resolved in the active logical ROM view."""
import errno
import ntpath
import os


def raw_link(view,path,*,resolved=None):
    physical,origin=resolved if resolved is not None else view._resolve_direct(path)
    if not physical:return None
    if os.path.islink(physical):
        return {'target':os.readlink(physical),'representation':'native','origin':origin}
    if not os.path.isfile(physical):return None
    from core.symlink import is_candidate,read_symlink_target
    if not is_candidate(physical):return None
    target=read_symlink_target(physical)
    return {'target':target,'representation':'android_placeholder','origin':origin} if target else None


def _candidates(view,path,link):
    target=link['target'];normalized=target.replace('\\','/')
    if link['representation']=='native':
        return [os.path.normpath(target if os.path.isabs(target) else os.path.join(os.path.dirname(path),target))]
    if ntpath.splitdrive(target)[0]:return [os.path.normpath(target)]
    if normalized.startswith('/'):
        clean=normalized.lstrip('/');values=[os.path.join(view.root,clean)]
        if clean.startswith('system/'):values.append(os.path.join(view.root,clean[7:]))
        values.append(os.path.join(view.root,'system',clean))
    else:
        values=[os.path.join(os.path.dirname(path),normalized),os.path.join(view.root,normalized),os.path.join(view.root,'system',normalized)]
    return list(dict.fromkeys(os.path.normpath(value) for value in values))


def canonical(view,path,*,follow_final=True,seen=frozenset()):
    logical,relative=view._logical(path)
    physical,_=view._resolve_direct(logical)
    # Ordinary paths have no file-placeholder parents. Avoid per-component
    # scans unless a final link is requested, a path is absent, or OS links exist.
    if physical and os.path.normcase(os.path.realpath(physical))==os.path.normcase(os.path.abspath(physical)):
        if not follow_final or not raw_link(view,logical):return logical
    if relative=='.':return logical
    parts=relative.split(os.sep)
    for index in range(len(parts) if follow_final else max(0,len(parts)-1)):
        prefix=os.path.join(view.root,*parts[:index+1]);link=raw_link(view,prefix)
        if not link:continue
        key=os.path.normcase(prefix)
        if key in seen or len(seen)>=40:raise OSError(errno.ELOOP,'Symlink cycle or too many links',prefix)
        chain=seen|{key};chosen=None;missing=None;cycle=None
        for candidate in _candidates(view,prefix,link):
            try:resolved=canonical(view,candidate,follow_final=True,seen=chain)
            except OSError as error:
                if error.errno!=errno.ELOOP:raise
                cycle=error;continue
            if missing is None:missing=resolved
            if view._resolve_direct(resolved)[0]:chosen=resolved;break
        if chosen is None and cycle is not None:raise cycle
        chosen=chosen or missing
        if chosen is None:raise FileNotFoundError(prefix)
        combined=os.path.join(chosen,*parts[index+1:])
        return canonical(view,combined,follow_final=follow_final,seen=chain)
    return logical


def describe(view,path,*,node=None,link=None):
    node=canonical(view,path,follow_final=False) if node is None else node
    link=raw_link(view,node) if link is None else link
    if not link:return None
    result={**link,'path':path,'resolved_path':None,'exists':False,'is_dir':False,'is_file':False,'status':'missing'}
    try:
        resolved=canonical(view,node);physical,origin=view._resolve_direct(resolved)
        result.update(resolved_path=resolved,exists=bool(physical),is_dir=bool(physical and os.path.isdir(physical)),
                      is_file=bool(physical and os.path.isfile(physical)),target_origin=origin,status='resolved' if physical else 'missing')
    except OSError as error:
        result['status']='cycle' if error.errno==errno.ELOOP else 'unavailable';result['error']=str(error)
    except ValueError as error:result.update(status='outside_workspace',error=str(error))
    return result
