# SPDX-License-Identifier: MPL-2.0
"""Filesystem mutations shared by the native workspace and its command bridge."""
import errno
import os
from pathlib import Path
import shutil
import tempfile

from core.config_engine_pure import ConfigEngine
from core.rak_neobranch_view import ActiveProjectView
from core.project_view_work import _copy_view


def validate_name(name):
    name = str(name).strip()
    if not name or name in (".", "..") or any(ch in name for ch in '<>:"/\\|?*') or name.endswith((".", " ")):
        raise ValueError("Invalid file name")
    if name.split(".",1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        raise ValueError("This file name is reserved by Windows")
    return name


class WorkspaceFiles:
    def __init__(self, base):
        self.base = os.path.abspath(base)
        self.view = ActiveProjectView(self.base)

    def logical(self, path, *, allow_root=False):
        logical, rel = self.view._logical(self.view.canonical(path,follow_final=False))
        if rel == "." and not allow_root: raise ValueError("The project root cannot be modified")
        if rel.split(os.sep)[0] in ("config", "neobranch") and rel == "neobranch":
            raise ValueError("Internal NeoBranch path")
        return logical, rel.replace(os.sep, "/")

    def branch(self, path):
        logical, rel = self.logical(path)
        root = rel.split("/",1)[0]
        name, _ = self.view._branch(root)
        active=self.view.manager.get_active_branch(root)
        if name!=(active.get('name') if active else None):
            raise RuntimeError('Active branch changed; retry the file operation')
        return logical, rel, root, name

    def write_path(self, path):
        logical, rel, root, branch = self.branch(path)
        if self.view.manager.is_physical_checkout(root):self.view.manager._assert_checkout_branch(root,branch)
        if branch:
            manifest = os.path.join(self.view.manager._source_dir(root), "repack_manifest.json")
            if os.path.exists(manifest): raise RuntimeError("Wait for the partition repack to finish")
            physical = (logical if self.view.manager.is_physical_checkout(root) else
                        os.path.join(self.view.manager._branch_dir(root, branch), rel.replace("/",os.sep)))
        else: physical = logical
        return physical

    def engine(self, path):
        _logical, _rel, root, branch = self.branch(path)
        base=self.base
        if branch and not self.view.manager.is_physical_checkout(root):
            base=self.view.manager._branch_dir(root,branch)
            cfgdir=self.view.manager._branch_config_dir(root,branch)
            # Missing branch metadata may be seeded, but writes must never fall
            # back to the source config. Use the same suffixes as branch creation.
            from main_ext.class_NeoBranchManager import CONFIG_SUFFIXES
            for suffix in CONFIG_SUFFIXES:
                source=os.path.join(self.base,'config',root+suffix)
                target=os.path.join(cfgdir,root+suffix)
                if not os.path.exists(target) and os.path.isfile(source):
                    os.makedirs(cfgdir,exist_ok=True);shutil.copy2(source,target)
        return ConfigEngine(base)

    def is_filesystem_root(self,path):
        _logical,rel,root,branch=self.branch(path)
        cfgdir=self.view.manager.get_live_config_dir(root)
        return rel==root and all(os.path.isfile(os.path.join(cfgdir,root+suffix)) for suffix in ('_file_contexts','_fs_config'))

    def has_filesystem_metadata(self,path):
        _logical,_rel,root,branch=self.branch(path)
        cfgdir=self.view.manager.get_live_config_dir(root)
        return all(os.path.isfile(os.path.join(cfgdir,root+suffix)) for suffix in ('_file_contexts','_fs_config'))

    def _record(self, path, physical):
        logical, rel, root, branch = self.branch(path)
        if branch:
            if not self.view.manager.is_physical_checkout(root):self.view.manager.undelete(root,branch,rel)
            self.view.manager.record_tree_edit(root,branch,logical,physical)
            self.view._branches.pop(root,None)

    def create(self, directory, name, *, folder=False):
        directory, _ = self.logical(self.view.canonical(directory),allow_root=True)
        target = os.path.join(directory,validate_name(name))
        source, _ = self.view.resolve(target)
        if source: raise FileExistsError(target)
        physical = self.write_path(target)
        os.makedirs(os.path.dirname(physical),exist_ok=True)
        if folder: os.mkdir(physical)
        else:
            with open(physical,"xb"): pass
        self._record(target,physical)
        if not self.branch(target)[3]:
            self.engine(target).apply(os.path.relpath(target,self.base).replace(os.sep,"/"))
        from core import filesystem_map
        filesystem_map.record_entry(self.base,target,physical)
        return target

    def delete(self, path, *, keep_metadata=False):
        logical, rel, root, branch = self.branch(path)
        if branch:
            self.write_path(path)  # repack guard
            engine=self.engine(path)
            if self.view.manager.is_physical_checkout(root):
                if os.path.islink(logical) or not os.path.isdir(logical):os.remove(logical)
                else:shutil.rmtree(logical)
            self.view.manager.record_delete(root,branch,rel)
            if not keep_metadata:engine.delete_paths(rel)
            self.view._branches.pop(root,None)
        else:
            if os.path.islink(logical) or not os.path.isdir(logical): os.remove(logical)
            else: shutil.rmtree(logical)
            if not keep_metadata:self.engine(path).delete_paths(rel)
        from core import filesystem_map
        filesystem_map.record_delete(self.base,logical)

    def rename(self, path, name, *, force=False):
        logical, rel, root, branch = self.branch(path)
        destination = os.path.join(os.path.dirname(logical),validate_name(name))
        if os.path.normcase(destination)==os.path.normcase(logical) and destination==logical: return logical
        existing, _ = self.view.resolve(destination)
        if existing and os.path.normcase(destination)!=os.path.normcase(logical): raise FileExistsError(destination)
        source, _ = self.view.resolve(logical)
        if not source: raise FileNotFoundError(logical)
        from core.symlink import is_symlink_placeholder
        if not force and os.path.isfile(source) and is_symlink_placeholder(source):raise ValueError('Symlink placeholders cannot be renamed')
        if branch and rel==root:
            raise ValueError('Switch back to source before renaming the partition root')
        if branch:
            target=self.write_path(destination)
            physical_checkout=self.view.manager.is_physical_checkout(root)
            if physical_checkout:os.rename(source,target)
            elif os.path.isdir(source): _copy_view(self.view,logical,target)
            else:
                os.makedirs(os.path.dirname(target),exist_ok=True)
                shutil.copy2(source,target)
            try:
                self._record(destination,target)
                self.engine(path).move_paths(rel,os.path.relpath(destination,self.base).replace(os.sep,"/"))
                self.view.manager.record_delete(root,branch,rel)
            except Exception:
                if physical_checkout:os.rename(target,source)
                raise
            from core import filesystem_map
            filesystem_map.record_rename(self.base,logical,destination,target)
        else:
            os.rename(source,destination)
            try:
                from main_ext.class_FilesystemRenamer import FilesystemRenamer
                engine=self.engine(destination)
                if not FilesystemRenamer(self.base,engine).rename(logical,destination):
                    engine.move_paths(rel,os.path.relpath(destination,self.base).replace(os.sep,"/"))
            except Exception:
                os.rename(destination,source)
                raise
            from core import filesystem_map
            filesystem_map.record_rename(self.base,logical,destination,destination)
        return destination

    def duplicate(self, path):
        logical,_ = self.logical(path)
        stem,ext=os.path.splitext(os.path.basename(logical))
        index=1
        while True:
            name=f"{stem} - Copy{(' ('+str(index)+')') if index>1 else ''}{ext}"
            destination=os.path.join(os.path.dirname(logical),name)
            if not self.view.resolve(destination)[0]:break
            index+=1
        return self.transfer(self,path,os.path.dirname(logical),name=name)

    def transfer(self, source_files, source, directory, *, name=None, move=False, replace=False, drag=False, preserve=False):
        directory,_=self.logical(self.view.canonical(directory),allow_root=True)
        source_logical,_=source_files.logical(source)
        source_write=source_files.write_path(source)  # source branch/repack guard before any I/O
        physical,origin=source_files.view.resolve(source_logical)
        if not physical:raise FileNotFoundError(source)
        destination=os.path.join(directory,validate_name(name or os.path.basename(source)))
        if os.path.normcase(destination)==os.path.normcase(source_logical):raise ValueError("Source and destination are the same")
        if os.path.isdir(physical):
            try:
                if os.path.commonpath([os.path.abspath(directory),source_logical])==source_logical:
                    raise ValueError("A folder cannot be placed inside itself")
            except ValueError as error:
                if str(error).startswith("A folder"):raise
        existing,_=self.view.resolve(destination)
        if existing and not replace:raise FileExistsError(destination)
        target=self.write_path(destination)
        source_engine=source_files.engine(source) if os.path.isdir(os.path.join(source_files.base,'config')) else None
        destination_engine=self.engine(destination)
        old_rel=os.path.relpath(source_logical,source_files.base).replace(os.sep,'/')
        new_rel=os.path.relpath(destination,self.base).replace(os.sep,'/')
        whole_root=(os.path.isdir(physical) and source_files.base!=self.base and old_rel==new_rel
                    and source_files.is_filesystem_root(source) and '/' not in new_rel)
        source_branch=source_files.branch(source)[3]
        destination_branch=self.branch(destination)[3]
        # The original dropEvent checks this before running FilesystemBuilder.
        # A plain destination keeps the source metadata for a later return move.
        transfer_metadata=(not drag or whole_root or source_branch or destination_branch
                           or self.has_filesystem_metadata(destination))
        build_partition=(os.path.basename(destination) if drag and move and os.path.isdir(physical)
                         and source_files.base==self.base and old_rel.split('/')[0]!=new_rel.split('/')[0]
                         and not source_branch and not destination_branch else None)
        # Validate metadata before moving bytes, and retain it for rollback if
        # a later config/branch write fails after the physical rename.
        metadata_before={}
        metadata_roots=[(source_engine,old_rel),(destination_engine,new_rel)]
        if build_partition:metadata_roots.append((destination_engine,build_partition))
        for engine,rel in metadata_roots:
            if engine:
                for suffix in ('_file_contexts','_fs_config',*(('_fs_options','_info') if whole_root or build_partition else ())):
                    candidate=Path(engine.cfgdir,rel.split('/')[0]+suffix)
                    contents=candidate.read_bytes() if candidate.is_file() else None
                    if contents is not None and suffix in ('_file_contexts','_fs_config'):contents.decode('utf-8')
                    metadata_before[candidate]=contents
        for files,path in ((source_files,source_logical),(self,destination)):
            _logical,_rel,root,branch=files.branch(path)
            if branch:
                candidate=Path(files.view.manager._branch_json_path(root,branch))
                metadata_before[candidate]=candidate.read_bytes()

        from main_ext.class_NeoBranchManager import _remove_path,_copy2_with_retry
        os.makedirs(os.path.dirname(target),exist_ok=True)
        backup_dir=backup=None
        if os.path.lexists(target):
            backup_dir=tempfile.mkdtemp(prefix='.rak-transfer-',dir=os.path.dirname(target))
            backup=os.path.join(backup_dir,'previous')
            try:os.rename(target,backup)
            except Exception:
                os.rmdir(backup_dir)
                raise
        renamed=started=metadata_changed=False
        try:
            # Source trees and self-contained overlay items can move with one
            # OS rename. A merged/inherited branch view must be materialized:
            # renaming its Source would mutate the immutable source partition.
            direct=move and source_files._can_move_payload(source_logical,physical,origin)
            if direct:
                try:
                    os.rename(physical,target)
                    renamed=started=True
                except OSError as error:
                    if error.errno!=errno.EXDEV and getattr(error,'winerror',None)!=17:raise
                    # Only a real cross-device move needs copy + source cleanup.
            if not renamed:
                started=True
                if os.path.isdir(physical):_copy_view(source_files.view,source_logical,target)
                else:_copy2_with_retry(physical,target)
            metadata_changed=True
            self._record(destination,target)
            if build_partition:
                from main_ext.class_FilesystemBuilder import FilesystemBuilder
                old_root=old_rel.split('/')[0]
                old_cfg=source_engine.cfgdir if source_engine else os.path.join(source_files.base,'config')
                fs_type=('EROFS' if os.path.isfile(os.path.join(old_cfg,old_root+'_fs_options')) else
                         'EXT' if os.path.isfile(os.path.join(old_cfg,old_root+'_info')) else None)
                builder=FilesystemBuilder(self.base,target,forced_fs_type=fs_type,
                    source_fs_root=old_root,source_rel_prefix=old_rel)
                if not builder.create_filesystem():raise RuntimeError('Could not prepare filesystem metadata')
                destination_engine.rebuild_root_map()
            if source_engine and transfer_metadata:
                self._copy_metadata(source_engine,destination_engine,old_rel,new_rel,allow_create=whole_root,
                    replace_existing=preserve or move and source_files.base==self.base and not source_files.branch(source)[3] and not self.branch(destination)[3])
            if not whole_root and transfer_metadata:self._fill_metadata(destination_engine,new_rel,target)
            identity=self._copy_identity(source_engine,destination_engine,old_rel,new_rel) if whole_root else []
            if move:source_files._forget_transfer_source(source_logical,source_engine if transfer_metadata else None,old_rel)
        except Exception as error:
            try:
                if renamed:os.rename(target,physical)
                elif started:_remove_path(target)
                if backup:os.rename(backup,target)
                if metadata_changed:
                    for path,contents in metadata_before.items():
                        if contents is None:path.unlink(missing_ok=True)
                        elif not path.is_file() or path.read_bytes()!=contents:self._write_metadata(path,contents)
                source_files.view._branches.clear();self.view._branches.clear()
                if backup_dir:os.rmdir(backup_dir)
            except Exception as rollback_error:
                raise RuntimeError(f'{error}; transfer restore failed: {rollback_error}') from error
            raise
        # All fallible metadata work succeeded. Never delete an already-renamed
        # source, or remove inherited Source bytes for a NeoBranch logical move.
        if move:
            if not renamed:_remove_path(source_write)
            for path in identity:path.unlink()
        if backup_dir:_remove_path(backup_dir)
        from core import filesystem_map
        filesystem_map.record_entry(self.base,destination,target)
        if move:
            filesystem_map.record_delete(source_files.base,source_logical)
        return destination

    def _can_move_payload(self,logical,physical,origin):
        if self.view.manager.is_physical_checkout(self.branch(logical)[2]):return True
        if not self.branch(logical)[3]:return True
        if origin!='branch':return False
        if not os.path.isdir(physical) or not os.path.isdir(logical):return True
        _logical,rel,root,_branch=self.branch(logical)
        _name,deleted=self.view._branch(root)
        return self.view.manager.is_deleted_in_set(rel,deleted)

    def _forget_transfer_source(self,logical,engine,relative):
        _logical,_rel,root,branch=self.branch(logical)
        if branch:
            # Payload cleanup is separate so metadata failures can roll a rename
            # back before any copied overlay bytes are removed.
            self.view.manager.prune_edit_prefix(root,branch,relative)
            self.view.manager.record_delete(root,branch,relative,prune_overlay=False)
            self.view._branches.pop(root,None)
        if engine:
            for suffix,context in (('_file_contexts',True),('_fs_config',False)):
                path=Path(engine.cfgdir,relative.split('/')[0]+suffix)
                if not path.is_file():continue
                prefix=engine.escaped_ctx_key(relative) if context else relative
                lines=path.read_text(encoding='utf-8').splitlines()
                output=[]
                for line in lines:
                    parts=line.strip().split(None,1)
                    if parts and not parts[0].startswith('#') and (parts[0]==prefix or parts[0].startswith(prefix+'/')):continue
                    output.append(line)
                if output!=lines:self._write_metadata(path,'\n'.join(output)+('\n' if output else ''))

    @staticmethod
    def _copy_metadata(source_engine,destination_engine,old_rel,new_rel,*,allow_create=False,exact=False,replace_existing=False):
        from core.native_backend import insert_config_lines
        old_root=old_rel.split("/",1)[0];new_root=new_rel.split("/",1)[0]
        for suffix,is_context in (("_file_contexts",True),("_fs_config",False)):
            source=os.path.join(source_engine.cfgdir,old_root+suffix)
            destination=os.path.join(destination_engine.cfgdir,new_root+suffix)
            if not os.path.isfile(source) or (not os.path.isfile(destination) and not allow_create):continue
            if allow_create and not exact and old_rel==new_rel and not os.path.exists(destination):
                # A whole filesystem keeps its authoritative regex rules,
                # comments and byte encoding, including non-literal root keys.
                shutil.copy2(source,destination)
                continue
            prefix=source_engine.escaped_ctx_key(old_rel) if is_context else old_rel
            target=destination_engine.escaped_ctx_key(new_rel) if is_context else new_rel
            entries=[]
            for line in Path(source).read_text(encoding="utf-8").splitlines():
                parts=line.strip().split(None,1)
                if len(parts)==2 and (parts[0]==prefix or not exact and parts[0].startswith(prefix+"/")):
                    entries.append((target+parts[0][len(prefix):],parts[1]))
            if entries or allow_create:
                lines=Path(destination).read_text(encoding='utf-8').splitlines() if os.path.isfile(destination) else []
                contents=insert_config_lines(lines,entries,skip_existing=not replace_existing)
                WorkspaceFiles._write_metadata(destination,contents)
        destination_engine.rebuild_root_map()

    @staticmethod
    def _write_metadata(path,contents):
        os.makedirs(os.path.dirname(path),exist_ok=True)
        temporary=None
        try:
            binary=isinstance(contents,bytes)
            with tempfile.NamedTemporaryFile(mode='wb' if binary else 'w',encoding=None if binary else 'utf-8',dir=os.path.dirname(path),prefix='.rak-meta-',delete=False) as stream:
                temporary=stream.name;stream.write(contents)
            os.replace(temporary,path)
        finally:
            if temporary and os.path.exists(temporary):os.remove(temporary)

    @staticmethod
    def _fill_metadata(engine,relative,physical):
        from core.native_backend import insert_config_lines
        from core.config_engine_pure import DEFAULT_CTX
        root=relative.split('/')[0]
        if root not in engine.root_map_ctx or root not in engine.root_map_fs:return
        paths=[relative]
        if os.path.isdir(physical):
            for base,dirs,files in os.walk(physical):
                prefix=os.path.relpath(base,physical).replace(os.sep,'/')
                prefix=relative if prefix=='.' else relative+'/'+prefix
                paths.extend(prefix+'/'+name for name in dirs+files)
        for filename,context in ((engine.root_map_ctx[root],True),(engine.root_map_fs[root],False)):
            path=os.path.join(engine.cfgdir,filename);lines=Path(path).read_text(encoding='utf-8').splitlines()
            existing={line.split()[0] for line in lines if line.strip() and not line.lstrip().startswith('#')}
            entries=[]
            for rel in paths:
                key=engine.escaped_ctx_key(rel) if context else rel
                if key in existing:continue
                value=DEFAULT_CTX if context else ' '.join(engine.infer_permission(rel)[:3])
                entries.append((key,value));existing.add(key)
            if entries:WorkspaceFiles._write_metadata(path,insert_config_lines(lines,entries,skip_existing=True))

    @staticmethod
    def _copy_identity(source_engine,destination_engine,old_rel,new_rel,*,move=False):
        copied=[]
        if not source_engine:return copied
        for suffix in ('_fs_options','_info'):
            source=Path(source_engine.cfgdir,old_rel+suffix);target=Path(destination_engine.cfgdir,new_rel+suffix)
            if source.is_file() and not target.exists():
                shutil.copy2(source,target)
                copied.append(source)
                if move:source.unlink()
        return copied

    def edit_metadata(self,path,column,value):
        logical,rel,root,_branch=self.branch(path)
        if any(character.isspace() for character in rel):
            raise ValueError('Kitchen Android metadata parsers cannot represent whitespace in paths; rename the file before setting its label or permission')
        engine=self.engine(path)
        if column==1:
            value=value.strip()
            if not value or any(ch.isspace() for ch in value):raise ValueError("Invalid SELinux context")
            suffix="_file_contexts";key=engine.escaped_ctx_key(rel)
        elif column==2:
            value=value.strip()
            if len(value) not in (3,4) or any(ch not in "01234567" for ch in value):raise ValueError("Invalid octal permission")
            suffix="_fs_config";key=rel
        else:raise ValueError("Invalid metadata column")
        metadata_path=os.path.join(engine.cfgdir,root+suffix)
        if not os.path.isfile(metadata_path):raise FileNotFoundError(f"No filesystem metadata for {root}")
        lines=Path(metadata_path).read_text(encoding="utf-8").splitlines()
        found=False;changed=False;output=[]
        for line in lines:
            parts=line.split()
            if parts and not parts[0].startswith("#") and parts[0]==key:
                current=parts[1] if column==1 and len(parts)>1 else parts[3] if column==2 and len(parts)>3 else None
                if current==value:
                    output.append(line)
                    found=True
                    continue
                if column==1:parts=[key,value]
                else:
                    if len(parts)<4:raise ValueError("Malformed fs_config entry")
                    parts[3]=value
                line=" ".join(parts);found=True;changed=True
            output.append(line)
        if not found:
            output.append(f"{key} {value}" if column==1 else f"{key} 0 0 {value}")
            changed=True
        if not changed:return
        self.write_path(path)  # repack guard, without materializing contents
        self._write_metadata(metadata_path,"\n".join(output)+"\n")
