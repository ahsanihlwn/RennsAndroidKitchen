# SPDX-License-Identifier: MPL-2.0
"""Kitchen tool routing for the native workspace."""
import os
from pathlib import Path
import tempfile

from core import ui_lang
from core.workspace_files import WorkspaceFiles, validate_name


def _item(key,label,**extra):return {"key":key,"label":ui_lang.t(label),**extra}


def context_tools(session,path):
    """Reference group order and capability checks, independent of presentation."""
    from core.binary_handler import is_available
    from core import apktool_tool
    from core.oem_containers import OEM_CONTAINER_KINDS
    from ui.viewers import ARCHIVE_EXT
    from core.tree_entries import APKTOOL_LINK_ROLE
    directory=session.active_tree;node=directory._nodes.get(path)
    badge=node.data(0,257) if node else ""
    physical=(node.data(0,276) if node else None) or path
    is_file=os.path.isfile(physical)
    folder=not is_file and (bool(node and node.folder) or os.path.isdir(physical))
    result=[]
    def item(key,label,**parameters):return {'key':key,'label':ui_lang.t(label,**parameters)}
    def section(rows):
        if rows:result.extend([{'separator':True},*rows])
    def submenu(key,label,entries):
        children=[item(action,label) for label,action,*binaries in entries if not binaries or is_available(*binaries)]
        return {'key':key,'label':ui_lang.t(label).rstrip(' ▸>›'),'children':children} if children else None
    convert=submenu('convert_img','rc_convert',[
        ('rc_convert_sparse','convert_to_sparse','img2simg.exe'),('rc_to_brotli','convert_img_to_br','brotli.exe'),
        ('rc_to_new_dat','convert_img_to_new_dat','img2simg.exe'),('rc_to_dat_br','convert_img_to_dat_br','img2simg.exe','brotli.exe')])
    if is_file:
        if badge in ('EROFS','EXT2','EXT3','EXT4'):
            rows=[];binary='extract.erofs.exe' if badge=='EROFS' else 'ext_cli.exe'
            if is_available(binary):rows.append(item('unpack_erofs' if badge=='EROFS' else 'unpack_ext','rc_unpack_image'))
            if convert:rows.append(convert)
            section(rows)
        can_mount=badge in ('EROFS','EXT2','EXT3','EXT4')
        if badge=='SPARSE':
            try:
                from core import imgfs
                can_mount=imgfs.detect_fs(physical) in ('EXT','EROFS')
            except Exception:pass
        if can_mount:
            mounted=session.mounts.reference(path,resolve=False)
            section([item('unmount','rc_unmount'),{'key':'usage','label':'Space usage...'}] if mounted else [item('mount','rc_mount')])
        elif badge in ('BR','DAT','DAT.BR'):
            entries={
                'BR':[('rc_to_raw','unpack_br_to_image','brotli.exe'),('rc_to_folder','unpack_br_to_folder','brotli.exe','extract.erofs.exe')],
                'DAT':[('rc_to_raw','unpack_dat_to_image'),('rc_to_folder','unpack_dat_to_folder','extract.erofs.exe')],
                'DAT.BR':[('rc_to_new_dat','unpack_dat_br_to_dat','brotli.exe'),('rc_to_raw','unpack_dat_br_to_image','brotli.exe'),
                          ('rc_to_folder','unpack_dat_br_to_folder','brotli.exe','extract.erofs.exe')]}
            unpack=submenu('unpack_'+badge.lower().replace('.','_'),'rc_unpack',entries[badge])
            if unpack:section([unpack])
        if badge=='SPARSE' and is_available('lpunpack.exe'):
            section([item('convert_sparse','rc_convert_raw'),item('unpack_sparse','rc_unpack_sparse')])
        if badge=='SUPER':
            rows=[]
            if is_available('lpunpack.exe'):rows.extend([item('open_super','rc_open_super'),item('unpack_super','rc_unpack_super')])
            if is_available('img2simg.exe'):rows.append(item('convert_to_sparse','rc_convert_sparse'))
            section(rows)
        basename=os.path.basename(physical).lower()
        is_boot_file=(badge in ('BOOT','VNDRBOOT')) or (basename.startswith(('boot','init_boot','vendor_boot')) and basename.endswith('.img'))
        if is_boot_file:
            boot_rows=[]
            if is_available('magiskboot.exe'):
                if badge in ('BOOT','VNDRBOOT'):boot_rows.append(item('unpack_boot','rc_unpack_ext',fs=badge))
                boot_rows.append({'key':'magisk_patch','label':'Magisk Patch...'})
            section(boot_rows)
        if badge in OEM_CONTAINER_KINDS:
            section([{'key':'open_oem','label':'Inspect Container'},{'key':'unpack_oem','label':'Extract Container'}])
        if badge=='F2FS' and (is_available('extract.f2fs.exe') or is_available('imgkit.exe')):
            section([{'key':'unpack_f2fs','label':'Unpack F2FS Image'}])
        if badge=='VBMETA':
            section([{'key':'patch_vbmeta','label':'Disable AVB Verification (vbmeta)'}])
        if basename=='fstab' or basename.startswith('fstab.'):
            section([{'key':'patch_fstab','label':'Disable AVB & Encryption...'}])
        extension=os.path.splitext(path)[1].lower()
        if extension in ARCHIVE_EXT and badge not in OEM_CONTAINER_KINDS:section([item('extract_here','rc_extract_here'),item('extract_to','rc_extract_to')])
        if extension=='.apk':
            decompiled=apktool_tool.is_decompiled(directory.base,path)
            has_tool=apktool_tool.is_apktool_available();has_signer=apktool_tool.is_signer_available();rows=[]
            if not decompiled and has_tool:rows.append(item('apktool_decompile','rc_apktool_decompile'))
            if decompiled:
                rows.append(item('apktool_switch_mode','rc_apktool_switch_to_apk' if node and node.data(0,APKTOOL_LINK_ROLE) else 'rc_apktool_switch_to_folder'))
                if has_tool:
                    rows.append(item('apktool_build','rc_apktool_recompile'))
                    if has_signer:rows.append(item('apktool_build_sign','rc_apktool_recompile_sign'))
                    rows.append(item('apktool_build_keeporig','rc_apktool_recompile_orig'))
            if has_signer:rows.extend([item('apktool_sign','rc_apktool_sign'),item('apktool_unsign','rc_apktool_unsign')])
            section(rows)
        if badge in ('EROFS','EXT2','EXT3','EXT4','SPARSE','BOOT','VNDRBOOT','BR','DAT','DAT.BR') or extension in ('.img','.zst','.zstd','.bin'):
            section([item('create_flashable','rc_create_flashable')])
        if extension in ('.bin','.fsm','.ccu','.blob','.dat','.raw'):section([item('openHex','rc_inspect_bin')])
    elif folder:
        rel=os.path.relpath(path,directory.base).replace('\\','/')
        root_level='/' not in rel.strip('/');engine=directory.engine
        fs_root=root_level and engine is not None and rel in engine.root_map_ctx and rel in engine.root_map_fs
        if fs_root:section([item('neocat','rc_neocat_selinux')])
        from core.quick_mods import find_selinux_dir
        folder_tools=[]
        if find_selinux_dir(physical):
            folder_tools.append({'key':'sepolicy_rules_patch','label':'Sepolicy Rules Patch...'})
        rel_top=rel.strip('/').split('/')[0] if rel else ''
        if rel_top not in ('config','META-INF','neobranch','.rak_quarantine') and not os.path.basename(physical).startswith('.'):
            folder_tools.append({'key':'apk_debloater','label':'APK Debloater...'})
        if folder_tools:section(folder_tools)
        if root_level and badge not in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT','SUPER') and not fs_root and rel not in ('config','META-INF'):
            section([item('make_filesystem','rc_make_filesystem')])
        if badge in ('EROFS','EXT2','EXT3','EXT4'):
            kind='repack_erofs' if badge=='EROFS' else 'repack_ext'
            binary='mkfs.erofs.exe' if badge=='EROFS' else 'make_ext4fs.exe'
            repack=submenu(kind,'rc_repack',[
                ('rc_to_raw',kind+'_to_image',binary),('rc_to_brotli',kind+'_to_brotli',binary,'brotli.exe'),
                ('rc_to_new_dat',kind+'_to_new_dat',binary,'img2simg.exe'),('rc_to_dat_br',kind+'_to_dat_br',binary,'img2simg.exe','brotli.exe')])
            section(([repack] if repack else [])+[item('convert_fs','rc_convert_to_fs',fs='EXT4' if badge=='EROFS' else 'EROFS')])
        elif badge in ('BOOT','VNDRBOOT') and is_available('magiskboot.exe'):section([item('repack_boot','rc_repack_ext',fs=badge)])
        if fs_root:
            from main_ext.class_NeoBranchManager import NeoBranchManager
            manager=NeoBranchManager(directory.base);active=manager.get_active_branch(rel)
            label=ui_lang.t('rc_neobranch_active',name=active['name']) if active else ui_lang.t('rc_neobranch_source') if manager.has_any_branch(rel) else ui_lang.t('rc_neobranch_create')
            section([{'key':'neobranch','label':label}])
    return result

def _checked(result):
    if isinstance(result,tuple):
        if not result[0]:
            from core.command_context import OperationError
            raise OperationError(str(result[1]))
        return result[1]
    return result


def repack(path,kind):
    from main_ext.class_NeoBranchManager import NeoBranchManager
    from contextlib import nullcontext
    manager = NeoBranchManager(os.path.dirname(path))
    root = os.path.basename(path)
    transaction = manager.repack_transaction(root) if manager.get_active_branch(root) or manager.is_physical_checkout(root) else nullcontext()
    with transaction:
        physical=manager.is_physical_checkout(root)
        if physical:manager.checkpoint_checkout(root)
        try:return _repack(path,kind)
        finally:
            if physical:manager.checkpoint_checkout(root)


def _repack(path,kind):
    from main_ext.class_NeoBranchManager import NeoBranchManager
    from core.config_engine_pure import ConfigEngine
    from core.fs_verify import FSVerifier
    from core.repack_policy import get_selinux_mode
    base=os.path.dirname(path);root=os.path.basename(path)
    manager=NeoBranchManager(base);active=manager.get_active_branch(root)
    overlaid=False
    try:
        if active and not manager.is_physical_checkout(root):
            manager.prepare_for_repack(root,active["name"]);overlaid=True
        # Ramdisks are CPIO archives. Their mode overrides and policy validation
        # belong to the boot packer, not filesystem SELinux context prediction.
        if kind=="repack_boot":
            from core.boot_image import repack_boot_image
            return repack_boot_image(path)
        engine=ConfigEngine(base)
        _checked(FSVerifier(base).verify_root(root))
        engine.apply_perm_on_repack(root)
        mode=get_selinux_mode()
        if mode!="none":
            engine.apply_predicted_context(root,mode=mode)
            if active and not manager.is_physical_checkout(root):
                manager.sync_live_file_contexts_to_branch(root,active["name"])
                manager.sync_live_neocat_to_branch(root,active["name"])
        if kind=="repack_erofs":
            from core.erofs import repack_erofs
            return repack_erofs(path)
        from core.repack_ext4 import main
        return main(path)
    finally:
        if overlaid:
            try:manager.restore_after_repack(root)
            except Exception as error:raise RuntimeError("NEOBRANCH_RESTORE_FAILED: "+str(error)) from error


def dispatch_tool(session,action,path,data):
    from ui.workspace_actions import operation,form
    files=WorkspaceFiles(session.active_tree.base)
    def worker():
        physical,_=files.view.resolve(path)
        if not physical:raise FileNotFoundError(path)
        # Tools operate on image/container files outside an active partition.
        # Repack owns the original overlay/verify/restore transaction below.
        _logical,_rel,root,branch=files.branch(path)
        from core.rak_neobranch_view import source_image_branch
        if ((branch and not files.view.manager.is_physical_checkout(root)) or source_image_branch(path)) and not action.startswith("repack_"):
            from core.command_context import OperationError
            raise OperationError("Use the active partition's repack action before modifying its image")
        outputs=[]
        if action in ('unpack_br_to_image','unpack_br_to_folder','unpack_dat_br_to_dat'):
            outputs.append(path[:-3])
            if action.endswith('_folder'):outputs.append(os.path.splitext(path[:-3])[0])
        if action in ('unpack_dat_to_image','unpack_dat_to_folder','unpack_dat_br_to_image','unpack_dat_br_to_folder'):
            suffix='.new.dat.br' if path.lower().endswith('.new.dat.br') else '.new.dat'
            prefix=path[:-len(suffix)];outputs.append(prefix+'.img')
            if action.endswith('_folder'):outputs.append(prefix)
        if action in ('unpack_super','unpack_sparse') and files.view.active_branches():
            from core.partition_viewer import super_info
            from core.super_logic import is_super_norm_enabled, normalize_partition_names
            part_names = super_info(physical)['partitions']
            if is_super_norm_enabled():
                part_names = list(dict.fromkeys([*part_names, *normalize_partition_names(part_names)]))
            outputs.extend(os.path.join(os.path.dirname(path),name+'.img') for name in part_names)
        if outputs:
            from ui.workspace_cli import WorkspaceCli
            for output in outputs:WorkspaceCli._tool_target(files,output)
        if action=="unpack_erofs":
            from core.erofs import unpack_erofs
            return unpack_erofs(physical)
        if action=="unpack_ext":
            from core.unpack_ext4 import main
            return main(physical)
        if action=="unpack_boot":
            from core.boot_image import unpack_boot_image
            return unpack_boot_image(physical)
        if action.startswith("repack_"):
            requested=data.get('output')
            if requested:
                target_files=WorkspaceFiles(files.base)
                if getattr(session,'quick_cli',None):target_files=session.quick_cli.files(requested)[0]
                target_files.logical(requested)
                from ui.workspace_cli import WorkspaceCli
                WorkspaceCli._tool_target(target_files,requested)
                agent=getattr(session,'_agent',None)
                if agent:agent._protect_settings_path(requested)
                from core.kitchen_conversions import repack_targets
                for target in repack_targets(requested):
                    if os.path.lexists(target):raise FileExistsError(target)
                if not os.path.isdir(os.path.dirname(requested)):raise NotADirectoryError(os.path.dirname(requested))
            kind='repack_erofs' if action.startswith('repack_erofs') else 'repack_ext' if action.startswith('repack_ext') else action
            result=repack(path,kind)
            if action.endswith(('_to_brotli','_to_new_dat','_to_dat_br')):
                image=_checked(result)
                if not isinstance(image,str) or not os.path.isfile(image):image=path+'_new.img'
                if action.endswith('_to_brotli'):
                    from core.brotli_tool import compress_to_br
                    result=compress_to_br(image,keep_source=False)
                else:
                    from core.kitchen_conversions import image_to_dat
                    result=image_to_dat(image,brotli=action.endswith('_to_dat_br'));os.remove(image)
            if requested:
                actual=_checked(result)
                if not isinstance(actual,str) or not os.path.isfile(actual):actual=path+'_new.img'
                if not os.path.isfile(actual):
                    from core.command_context import OperationError
                    raise OperationError('Repack produced no output image: '+actual)
                from core.kitchen_conversions import publish_repack
                outputs=publish_repack(actual,requested)
                if len(outputs)>1:
                    import json
                    return True,json.dumps({'outputs':outputs})
                return True,requested
            return result
        if action=="convert_fs":
            from core.filesystem_converter import convert_filesystem_metadata
            return convert_filesystem_metadata(physical)
        if action=="convert_sparse":
            from core.super_logic import convert_to_raw
            return convert_to_raw(physical,os.path.dirname(physical))
        if action in ("unpack_super","unpack_sparse"):
            from core.super_logic import extract_all,convert_to_raw
            if action=='unpack_sparse':_checked(convert_to_raw(physical,os.path.dirname(physical)))
            return extract_all(physical,os.path.dirname(physical))
        if action=="convert_to_sparse":
            from core.img2simg import img2simg
            temporary=physical+".quick-sparse"
            try:
                _checked(img2simg(physical,temporary))
                os.replace(temporary,physical)
                return True,physical
            finally:
                if os.path.exists(temporary):os.remove(temporary)
        if action=="convert_img_to_br":
            from core.brotli_tool import compress_to_br
            return compress_to_br(physical,keep_source=True)
        if action in ('convert_img_to_new_dat','convert_img_to_dat_br'):
            from core.kitchen_conversions import image_to_dat
            return image_to_dat(physical,brotli=action.endswith('_dat_br'))
        if action in ("unpack_br_to_image",'unpack_br_to_folder','unpack_dat_br_to_dat'):
            from core.brotli_tool import decompress_from_br
            if action.endswith('_folder'):
                from core.kitchen_conversions import brotli_to_image
                return brotli_to_image(physical,folder=True)
            return decompress_from_br(physical,keep_source=True)
        if action in ('unpack_dat_to_image','unpack_dat_to_folder','unpack_dat_br_to_image','unpack_dat_br_to_folder'):
            from core.kitchen_conversions import dat_to_image
            return dat_to_image(physical,folder=action.endswith('_folder'))
        if action in ("extract_here","extract_to"):
            from ui.archive import extract_archive
            destination=os.path.dirname(physical)
            if action=="extract_to":destination=os.path.join(destination,os.path.splitext(os.path.basename(physical))[0])
            return extract_archive(physical,destination)
        if action=="apktool_decompile":
            from core import apktool_tool
            return apktool_tool.decompile_apk(physical,apktool_tool.mirror_dir_for(files.base,path),base=files.base)
        if action in ("apktool_build","apktool_build_sign","apktool_build_keeporig"):
            from core import apktool_tool
            result=apktool_tool.build_apk(apktool_tool.mirror_dir_for(files.base,path),physical,base=files.base,copy_original=action=='apktool_build_keeporig')
            if action=='apktool_build_sign':_checked(result);return apktool_tool.sign_apk(physical)
            return result
        if action=="apktool_sign":
            from core.apktool_tool import sign_apk
            return sign_apk(physical)
        if action=="apktool_unsign":
            from core.apktool_tool import unsign_apk
            return unsign_apk(physical)
        if action=="neocat":
            from core.rak_policy_view import run_policy_in_view
            return run_policy_in_view(files.base,os.path.basename(path),apply=True)
        if action=="unpack_oem":
            from core.oem_containers import extract_container
            result=extract_container(physical)
            return True,result.get("out_dir") or ""
        if action=="unpack_f2fs":
            from core.oem_containers import unpack_f2fs
            return True,unpack_f2fs(physical)
        if action=="patch_vbmeta":
            from core.quick_mods import apply_vbmeta_patch
            result=apply_vbmeta_patch(physical)
            return True,f"{path} (flags 0x{result.get('flags',0):x})"
        raise ValueError("Unknown tool: "+action)
    if action in ("open_super","open_payload","open_oem"):
        kind="oem" if action=="open_oem" else ("super" if action=="open_super" else "payload")
        session.openAs(path,kind)
    elif action=='apktool_switch_mode':
        from core.tree_entries import APKTOOL_LINK_ROLE
        directory=session.active_tree;node=directory._nodes.get(path)
        if node is None:return
        mode='apk' if node.data(0,APKTOOL_LINK_ROLE) else 'folder'
        directory.set_apk_view_mode(path,mode);parent=node.parent
        if mode=='folder' and directory.displayMode=='tree':
            def loaded(location):
                if location!=parent.path:return
                directory.directoryLoaded.disconnect(loaded)
                session.presentationRequested.emit('mountExpand',{'path':path,'treeId':session.activeTreeId})
            directory.directoryLoaded.connect(loaded)
        directory._scan(parent,refresh=True)
    elif action=="make_filesystem":
        session.presentationRequested.emit('makeFilesystem',{'path':path,'name':os.path.basename(path)})
    elif action=='create_flashable':
        physical,_=files.view.resolve(path)
        session.presentationRequested.emit('flashable',{'path':physical,'base':files.base})
    elif action=="symlink":
        from ui.workspace_actions import _directory
        target_dir=_directory(session,path)
        session._symlink_target=(files.base,target_dir)
        session.presentationRequested.emit('symlink',{'path':target_dir})
    elif action=="neobranch":
        try:session.branches.show(path)
        except (OSError,ValueError) as error:session.message(str(error),'NeoBranch')
    elif action=="find":
        from ui.workspace_actions import _directory
        session.search.prepare(session.active_tree.base,_directory(session))
        session.presentationRequested.emit("find",{})
    elif action=="android":session.presentationRequested.emit("android",{})
    elif action=="magisk_patch":
        def run_magisk(values):
            apk=(values.get("apk_path") or "").strip()
            if not apk or not os.path.isfile(apk):
                from core.command_context import OperationError
                raise OperationError("Select a valid Magisk APK file.")
            arch=values.get("arch") or "arm64-v8a"
            keep_verity=str(values.get("keep_verity") or "false").lower()=="true"
            keep_forceencrypt=str(values.get("keep_forceencrypt") or "false").lower()=="true"
            def do_patch():
                from core.terminal_execution import library_operation
                from core.quick_mods import patch_boot_with_magisk
                physical,_=files.view.resolve(path)
                if not physical:raise FileNotFoundError(path)
                with library_operation('rak magisk_patch '+path) as output:
                    res=patch_boot_with_magisk(physical,apk,arch=arch,keep_verity=keep_verity,keep_forceencrypt=keep_forceencrypt)
                    output(res.get("output","")+"\n")
                    return True,res.get("output","")
            operation(session,"Magisk Patch",do_patch)
        form(session,f"Magisk Patch: {os.path.basename(path)}",[
            {"key":"apk_path","label":"Magisk APK","placeholder":"Select Magisk.apk...","browseFile":True,"browseTitle":"Select Magisk APK","nameFilters":["Android Package (*.apk)","All Files (*)"]},
            {"key":"arch","label":"Architecture","value":"arm64-v8a","choices":[
                {"key":"arm64-v8a","label":"arm64-v8a"},{"key":"armeabi-v7a","label":"armeabi-v7a"},
                {"key":"x86_64","label":"x86_64"},{"key":"x86","label":"x86"}]},
            {"key":"keep_verity","label":"AVB / dm-verity","value":"false","choices":[
                {"key":"false","label":"Disable Verity"},{"key":"true","label":"Keep Verity"}]},
            {"key":"keep_forceencrypt","label":"Force Encryption","value":"false","choices":[
                {"key":"false","label":"Disable ForceEncrypt"},{"key":"true","label":"Keep ForceEncrypt"}]},
        ],run_magisk,description="Patch boot / init_boot image with Magisk using magiskboot.",width=460,accept="Patch")
    elif action=="patch_fstab":
        def run_fstab(values):
            dis_avb=str(values.get("disable_avb") or "true").lower()=="true"
            dis_enc=str(values.get("disable_encryption") or "true").lower()=="true"
            def do_patch():
                from core.terminal_execution import library_operation
                from core.quick_mods import apply_fstab_patch
                physical,_=files.view.resolve(path)
                if not physical:raise FileNotFoundError(path)
                with library_operation('rak patch_fstab '+path) as output:
                    res=apply_fstab_patch(physical,disable_avb=dis_avb,disable_encryption=dis_enc)
                    msg=f"{path} ({len(res.get('changes',[]))} line(s) updated)" if res.get("changed") else f"{path} (no matching flags)"
                    output(msg+"\n")
                    return True,msg
            operation(session,"Patch Fstab",do_patch)
        form(session,f"Patch {os.path.basename(path)}",[
            {"key":"disable_avb","label":"AVB / Verified Boot Flags","value":"true","choices":[
                {"key":"true","label":"Remove avb / verify flags"},{"key":"false","label":"Keep AVB flags"}]},
            {"key":"disable_encryption","label":"Data Encryption Flags","value":"true","choices":[
                {"key":"true","label":"Remove forceencrypt / fileencryption"},{"key":"false","label":"Keep encryption flags"}]},
        ],run_fstab,width=420,accept="Patch")
    elif action=="apk_debloater":
        target=path or ""
        if target:
            try:
                phys,_=files.view.resolve(target)
                if phys:target=phys
            except OSError:pass
        session.presentationRequested.emit("apkDebloater",{"path":target})
    elif action=="sepolicy_rules_patch":
        target=path or ""
        if target:
            try:
                phys,_=files.view.resolve(target)
                if phys:target=phys
            except OSError:pass
        session.presentationRequested.emit("sepolicyPatch",{"path":target})
    elif action=='automation':session.privateFeatures.pickAutomation()
    elif action=='compare':session.privateFeatures.compare.showPicker()
    else:
        def recorded_worker():
            from core.terminal_execution import library_operation
            with library_operation('rak '+action+' '+path) as output:
                result=worker()
                if isinstance(result,tuple) and len(result)==2 and result[0] is False:
                    output(str(result[1])+'\n');output.failed=True
                    return result
                detail=_checked(result)
                output(str(detail)+'\n')
                return result
        operation(session,action.replace("_"," ").title(),recorded_worker,batch=data.get('batch'))
