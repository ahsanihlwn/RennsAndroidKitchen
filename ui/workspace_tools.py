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
                from core import wsl_manager
                if badge in ('BOOT','VNDRBOOT'):boot_rows.append(item('unpack_boot','rc_unpack_ext',fs=badge))
                if badge == 'BOOT' and not basename.startswith('init_boot'):
                    boot_rows.append({'key':'unpack_boot_decompile','label':'Unpack + Decompile'})
                    if os.name == 'nt' and wsl_manager.has_wsl_cached():
                        boot_rows.append({'key':'unpack_boot_decompile_wsl','label':'Unpack + Decompile with WSL'})
                boot_rows.append({'key':'magisk_patch','label':'Magisk Patch...'})
            section(boot_rows)
        if badge in OEM_CONTAINER_KINDS:
            section([{'key':'open_oem','label':'Inspect Container'},{'key':'unpack_oem','label':'Extract Container'}])
        if badge=='F2FS' and (is_available('extract.f2fs.exe') or is_available('imgkit.exe')):
            section([{'key':'unpack_f2fs','label':'Unpack F2FS Image'}])
        if badge=='VBMETA':
            section([{'key':'patch_vbmeta','label':'Disable AVB Verification (vbmeta)'}])
        if badge=='DTBO' and is_available('dtbo'):
            section([item('unpack_dtbo','rc_unpack_dtbo')])
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
        if badge in ('EROFS','EXT2','EXT3','EXT4','SPARSE','BOOT','VNDRBOOT','DTBO','BR','DAT','DAT.BR') or extension in ('.img','.zst','.zstd','.bin'):
            section([item('create_flashable','rc_create_flashable')])
        if extension in ('.bin','.fsm','.ccu','.blob','.dat','.raw'):section([item('openHex','rc_inspect_bin')])
        kernel_candidate = (
            badge == 'BOOT'
            or (basename.startswith('boot') and basename.endswith('.img'))
            or basename in ('kernel', 'image', 'vmlinux', 'zimage')
            or (basename.startswith(('kernel.', 'image.')) and extension in ('.img', '.bin', '.lz4', '.gz', '.xz', '.zst', '.zstd'))
        )
        is_binary = False
        if kernel_candidate or extension in ('.so', '.elf', '.exe', '.dll'):
            is_binary = True
        elif is_file and os.path.getsize(physical) <= 512 * 1024 * 1024:
            try:
                from core import binary_core
                binary_format = binary_core.identify(physical)
                is_binary = bool(binary_format)
                kernel_candidate = binary_format == 'ARM64 Image'
            except Exception:
                pass
        if is_binary and not is_boot_file:
            if kernel_candidate:
                from core import wsl_manager
                k_rows = [{'key':'decompile_kernel','label':'Decompile Kernel'}]
                if os.name == 'nt' and wsl_manager.has_wsl_cached():
                    k_rows.append({'key':'decompile_kernel_wsl','label':'Decompile Kernel with WSL'})
                k_rows.append({'key':'decompile_kernel_dwarf','label':'Decompile Kernel (Local vmlinux DWARF)...'})
                section(k_rows)
            else:
                section([{'key':'binary_reconstruct_tree','label':'Decompile Binary (Reconstruct Sources)...'}])
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
        from core.dtbo_image import is_dtbo_folder
        is_dtbo_dir = badge == 'DTBO' or is_dtbo_folder(physical)
        if root_level and badge not in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT','DTBO','SUPER') and not is_dtbo_dir and not fs_root and rel not in ('config','META-INF'):
            section([item('make_filesystem','rc_make_filesystem')])
        if badge in ('EROFS','EXT2','EXT3','EXT4'):
            kind='repack_erofs' if badge=='EROFS' else 'repack_ext'
            binary='mkfs.erofs.exe' if badge=='EROFS' else 'make_ext4fs.exe'
            repack=submenu(kind,'rc_repack',[
                ('rc_to_raw',kind+'_to_image',binary),('rc_to_brotli',kind+'_to_brotli',binary,'brotli.exe'),
                ('rc_to_new_dat',kind+'_to_new_dat',binary,'img2simg.exe'),('rc_to_dat_br',kind+'_to_dat_br',binary,'img2simg.exe','brotli.exe')])
            section(([repack] if repack else [])+[item('convert_fs','rc_convert_to_fs',fs='EXT4' if badge=='EROFS' else 'EROFS')])
        elif badge in ('BOOT','VNDRBOOT') and is_available('magiskboot.exe'):section([item('repack_boot','rc_repack_ext',fs=badge)])
        elif is_dtbo_dir and is_available('mkdtboimg'):section([item('repack_dtbo','rc_repack_dtbo')])
        from core import kernel_workspace
        if kernel_workspace.has_kernel_reconstruction(physical):
            kernel_tools = [{'key':'compile_kernel_sources','label':'Compile Kernel Sources...'}]
            if kernel_workspace.reconstructed_kernel(physical):
                kernel_tools.extend([{'key':'prepare_kernel_build','label':'Prepare Kernel Build...'},
                                     {'key':'manage_gki_cache','label':'Manage GKI Cache...'}])
            section(kernel_tools)
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
        if kind=="repack_dtbo":
            from core.dtbo_image import repack_dtbo_image
            return repack_dtbo_image(path)
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


def _tool_targets(path, action, data):
    targets = [path]
    stem = os.path.splitext(path)[0]
    if action in ("unpack_erofs", "unpack_ext", "unpack_boot", "unpack_boot_decompile", "unpack_dtbo", "extract_to"):
        targets.append(stem)
        if action == "unpack_boot_decompile":
            from core.kernel_workspace import SOURCE_DIR
            targets.append(os.path.join(stem, SOURCE_DIR))
    elif action.startswith("repack_"):
        targets.append(data.get("output") or (path + "_new.img"))
    elif action in ("unpack_br_to_image", "unpack_br_to_folder", "unpack_dat_br_to_dat"):
        if path.lower().endswith(".br"):
            targets.append(path[:-3])
        if action.endswith("_folder"):
            targets.append(os.path.splitext(path[:-3] if path.lower().endswith(".br") else path)[0])
    elif action in ("unpack_dat_to_image", "unpack_dat_to_folder", "unpack_dat_br_to_image", "unpack_dat_br_to_folder"):
        suffix = ".new.dat.br" if path.lower().endswith(".new.dat.br") else ".new.dat"
        prefix = path[:-len(suffix)] if path.lower().endswith(suffix) else stem
        targets.append(prefix + ".img")
        if action.endswith("_folder"):
            targets.append(prefix)
    elif action == "unpack_oem":
        targets.append(stem)
    elif action == "unpack_f2fs":
        targets.append(stem)
    elif action == "convert_img_to_br":
        targets.append(path + ".br")
    elif action in ("convert_img_to_new_dat", "convert_img_to_dat_br"):
        targets.append(stem + ".new.dat")
        targets.append(stem + ".transfer.list")
        if action.endswith("_dat_br"):
            targets.append(stem + ".new.dat.br")
    elif action == "extract_here":
        targets.append(os.path.dirname(path))
    elif action == "binary_reconstruct_tree":
        targets.append(stem + "_src")
    elif action == "decompile_kernel":
        from core.kernel_workspace import source_destination
        targets.append(str(source_destination(path)))
    lane = "light" if action in ("patch_vbmeta", "patch_fstab") else "heavy"
    return lane, tuple(dict.fromkeys(targets))


def _folder_operation_spec(path, action):
    stem = os.path.splitext(path)[0]
    if action in ("binary_reconstruct_tree", "decompile_kernel"):
        from core.kernel_workspace import source_destination
        dest = str(source_destination(path)) if action == "decompile_kernel" else stem + "_src"
        return (dest,), dest, None, None
    if action in ("unpack_erofs", "unpack_ext"):
        return (stem,), stem, path, stem
    if action in ("unpack_boot", "unpack_boot_decompile", "unpack_dtbo", "unpack_f2fs", "unpack_oem"):
        return (stem,), stem, None, None
    if action in ("unpack_br_to_folder", "unpack_dat_to_folder", "unpack_dat_br_to_folder"):
        low = path.lower()
        target = None
        for suffix in (".new.dat.br", ".new.dat", ".br"):
            if low.endswith(suffix):
                base_p = path[:-len(suffix)]
                target = os.path.splitext(base_p)[0] if suffix == ".br" else base_p
                break
        if target is None:
            target = stem
        return (target,), target, None, None
    if action.startswith("repack_") or action in ("convert_fs", "make_filesystem"):
        return (path,), path, None, None
    return (), None, None, None


def _estimate_task_weight(session, path, action):
    try:
        files = WorkspaceFiles(session.active_tree.base)
        phys, _ = files.view.resolve(path)
    except Exception:
        phys = path
    phys = phys or path
    try:
        if os.path.isfile(phys):
            sz = max(512 * 1024, os.path.getsize(phys))
            # For EROFS/EXT partition images, add superblock inode metadata weight (~8 KB per inode)
            if action in ("unpack_erofs", "unpack_ext"):
                try:
                    from core import native_backend
                    if action == "unpack_erofs":
                        info = native_backend.erofs_info(phys)
                        blocks_b = int(info.get("total_blocks", 0)) * int(info.get("block_size", 4096))
                        sz = max(sz, blocks_b)
                    else:
                        info = native_backend.ext_inspect(phys)
                        blocks_b = int(info.get("block_count", 0)) * int(info.get("block_size", 4096))
                        sz = max(sz, blocks_b)
                except Exception:
                    pass
            return float(sz)
        if os.path.isdir(phys):
            base = os.path.dirname(phys)
            name = os.path.basename(phys.rstrip("/\\"))
            fs_cfg = os.path.join(base, "config", f"{name}_fs_config")
            if os.path.isfile(fs_cfg):
                with open(fs_cfg, "r", encoding="utf-8", errors="ignore") as f:
                    lines = sum(1 for _ in f)
                return float(max(512 * 1024, lines * 64 * 1024))
            ref_img = os.path.join(base, f"{name}.img")
            if os.path.isfile(ref_img):
                return float(max(512 * 1024, os.path.getsize(ref_img)))
    except OSError:
        pass
    return float(1024 * 1024)


def dispatch_tool(session,action,path,data):
    from ui.workspace_actions import operation,form
    if action == "decompile_kernel_wsl":
        action = "decompile_kernel"
        data = {**data, "use_wsl": True}
    elif action == "unpack_boot_decompile_wsl":
        action = "unpack_boot_decompile"
        data = {**data, "use_wsl": True}
    files=WorkspaceFiles(session.active_tree.base)
    if action == "decompile_kernel_dwarf":
        def decompile_selected(values):
            dispatch_tool(session, "decompile_kernel", path, {**data, "dwarf_options": values})

        form(session, "Decompile Kernel (Local vmlinux DWARF)", [
            {"key": "reference", "label": "Unstripped vmlinux ELF (CONFIG_DEBUG_INFO=y)", "browseFile": True},
            {"key": "limit", "label": "Functions per file (0 for all)", "value": "5"},
        ], decompile_selected, description=(
            "Advanced option: only use this if you compiled this exact kernel yourself (or have a debug build package) "
            "and want to map functions using DWARF debug info in 'vmlinux'. Regular boot.img/Image files do NOT have "
            "vmlinux — use 'Decompile Kernel' or 'Decompile with WSL' instead."
        ), accept="Decompile", width=600)
        return
    if action in ("decompile_kernel", "unpack_boot_decompile", "binary_reconstruct_tree") and not data.get("download_confirmed"):
        _confirm_kernel_decompile_download(session, files, action, path, data)
        return
    if action == "compile_kernel_sources":
        _compile_kernel_sources(session, files, path)
        return
    if action in ("prepare_kernel_build", "manage_gki_cache") and os.path.isdir(path):
        from core.kernel_workspace import reconstructed_kernel
        kernel = reconstructed_kernel(path)
        if kernel is None:
            session.message("Choose the reconstructed sources folder with its original kernel.", "Kernel build")
            return
        path = str(kernel)
    if action == "prepare_kernel_build":
        _prepare_kernel_build(session, files, path)
        return
    if action == "manage_gki_cache":
        _manage_gki_cache(session, files, path)
        return
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
        if action=="unpack_dtbo":
            from core.dtbo_image import unpack_dtbo_image
            return unpack_dtbo_image(physical)
        if action == "unpack_boot_decompile":
            from core import kernel_workspace
            from core.boot_image import unpack_boot_image
            from core.command_context import OperationError
            from core.threader_control import get_progress_reporter, progress_scope

            report = get_progress_reporter()
            with progress_scope((lambda value, label: report(value * 30 // 100, label)) if report else None):
                folder = _checked(unpack_boot_image(physical))
            kernel = kernel_workspace.boot_kernel(folder)
            if kernel is None:
                raise OperationError(f"Unpacked to {folder}. This boot image has no kernel payload.")
            return True, kernel_workspace.decompile(
                kernel,
                progress_start=30,
                offline=bool(data.get("offline")),
                use_wsl=bool(data.get("use_wsl")),
            )
        if action == "decompile_kernel":
            from core import kernel_workspace
            offline = bool(data.get("offline"))
            use_wsl = bool(data.get("use_wsl"))
            options = data.get("dwarf_options")
            if options is None:
                return True, kernel_workspace.decompile(physical, offline=offline, use_wsl=use_wsl)
            from core.command_context import OperationError

            reference = str(options.get("reference", "")).strip()
            try:
                limit = int(options.get("limit", "5"))
            except (TypeError, ValueError) as error:
                raise OperationError("Enter a whole number of functions per file, or zero for all.") from error
            if not reference or limit < 0:
                raise OperationError("Choose vmlinux and enter zero or more functions per file.")
            return True, kernel_workspace.decompile(
                physical, dwarf_reference=reference, max_funcs_per_file=limit or None, offline=offline, use_wsl=use_wsl
            )
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
        if action=="binary_reconstruct_tree":
            from core import binary_reconstruct
            dest=os.path.splitext(physical)[0]+"_src"
            from core.threader_control import report_progress
            def on_progress(cur, total, path):
                pct = int((cur / max(1, total)) * 100)
                report_progress(pct, f"Reconstructing ({cur}/{total}): {os.path.basename(path)}")
            res=binary_reconstruct.reconstruct_source_tree(
                physical,
                output_dir=dest,
                progress_callback=on_progress,
                offline=bool(data.get("offline")),
                strict_failures=False,
            )
            summary = f"Reconstructed {res['total_files']} files & {res['total_functions']} functions in {os.path.basename(dest)}"
            if res["kernel_detected"]:
                summary += "\nSee the exported README.md for kernel build requirements."
            return True,summary
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
    elif action=='repack_super':
        selected=data.get('paths') or ([path] if path else [])
        session.presentationRequested.emit('packSuper',{'paths':list(selected),'base':files.base})
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
            operation(session,"Magisk Patch",do_patch,lane="heavy",targets=(path,))
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
            operation(session,"Patch Fstab",do_patch,lane="light",targets=(path,))
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
        lock_paths, highlight_folder, preview_image, preview_target = _folder_operation_spec(path, action)
        for lp in lock_paths:
            if hasattr(session, 'lock_path'):
                session.lock_path(lp)
        if highlight_folder and hasattr(session, 'set_path_highlight'):
            session.set_path_highlight(highlight_folder, 'loading')
        def recorded_worker():
            from core.terminal_execution import library_operation
            if preview_image and preview_target and hasattr(session, 'mounts'):
                try:
                    phys, _ = files.view.resolve(preview_image)
                    img_for_preview = phys or preview_image
                    payload = session.mounts.build_preview_payload(img_for_preview)
                    if payload is not None:
                        session.unpackPreviewReady.emit(img_for_preview, preview_target, files.base, payload)
                except Exception:
                    pass
            with library_operation('rak '+action+' '+path) as output:
                result=worker()
                if isinstance(result,tuple) and len(result)==2 and result[0] is False:
                    output(str(result[1])+'\n');output.failed=True
                    return result
                detail=_checked(result)
                output(str(detail)+'\n')
                return result
        def settled(ok, _result):
            if preview_target and hasattr(session, 'mounts'):
                session.mounts.end_unpack_preview(preview_target, ok=ok, refresh_views=not ok)
            for lp in lock_paths:
                if hasattr(session, 'unlock_path'):
                    session.unlock_path(lp)
            if highlight_folder and hasattr(session, 'set_path_highlight'):
                session.set_path_highlight(highlight_folder, 'done' if ok else 'error')
            for d in getattr(session, 'directories', ()):
                if d.base:
                    target_key = highlight_folder or path
                    target_node = d._nodes.get(target_key) if target_key else None
                    if not preview_target:
                        if target_node is not None and os.path.isdir(target_key):
                            d._scan(target_node, refresh=True)
                        else:
                            parent_dir = os.path.dirname(target_key) if target_key else d.base
                            parent_node = d._nodes.get(parent_dir) or d._nodes.get(d.base)
                            if parent_node is not None:
                                d._scan(parent_node, refresh=True)
                            else:
                                d.refresh()
        lane, targets = _tool_targets(path, action, data)
        weight = _estimate_task_weight(session, path, action)
        member_label = os.path.basename(path.rstrip('/\\')) or action
        operation(
            session,
            "Unpack + Decompile" if action == "unpack_boot_decompile" else action.replace("_", " ").title(),
            recorded_worker,
            done=(lambda message: session.message(message, "Kernel reconstruction", width=560))
            if action in ("unpack_boot_decompile", "decompile_kernel") else None,
            settled=settled,
            batch=data.get('batch'),
            lane=lane,
            targets=targets,
            weight=weight,
            member_label=member_label,
            highlight_path=highlight_folder or '',
        )


def _confirm_kernel_decompile_download(session, files, action, path, data):
    from core import kernel_workspace
    from core.command_context import OperationError
    from core.rak_neobranch_view import source_image_branch
    from ui.workspace_actions import form, operation

    def check_plan():
        physical, _ = files.view.resolve(path)
        if not physical:
            raise FileNotFoundError(path)
        _logical, _rel, root, branch = files.branch(path)
        if (branch and not files.view.manager.is_physical_checkout(root)) or source_image_branch(path):
            raise OperationError("Use the active partition's repack action before modifying its image")
        if action == "decompile_kernel" and data.get("dwarf_options") is not None:
            options = data["dwarf_options"]
            reference = str(options.get("reference", "")).strip()
            try:
                limit = int(options.get("limit", "5"))
            except (TypeError, ValueError) as error:
                raise OperationError("Enter a whole number of functions per file, or zero for all.") from error
            if not reference or limit < 0:
                raise OperationError("Choose vmlinux and enter zero or more functions per file.")
        if action == "binary_reconstruct_tree":
            from core import kernel_version
            if not kernel_version.inspect_kernel(physical).is_kernel:
                return {"needs_download": False}
        return kernel_workspace.inspect_download_plan(physical, use_wsl=bool(data.get("use_wsl")))

    def present_or_start(plan):
        def proceed(offline=False):
            dispatch_tool(session, action, path, {**data, "download_confirmed": True, "offline": offline})

        if not plan.get("needs_download"):
            proceed(offline=bool(plan.get("offline")))
            return

        def on_chosen(values):
            mode = (values or {}).get("mode", "download")
            proceed(offline=(mode == "offline"))

        est = plan.get("estimated_size") or "on-demand fetch"
        form(
            session,
            "Kernel Source Comparison & Decompile Mode",
            [
                {
                    "key": "mode",
                    "label": "Decompile Mode",
                    "value": "download",
                    "choices": [
                        {"key": "download", "label": f"Download & Compare with Source ({est})"},
                        {"key": "offline", "label": "Offline Decompile Only (No Download — Binary Analysis)"},
                    ],
                }
            ],
            on_chosen,
            description=kernel_workspace.format_download_prompt(plan),
            width=640,
            accept="Decompile",
        )

    operation(session, "Check kernel source cache", check_plan, done=present_or_start, lane="light", targets=(path,))


def _compile_kernel_sources(session, files, path):
    from core import kernel_workspace
    from core.command_context import OperationError
    from ui.workspace_actions import form, operation

    def compile_selected(values):
        def worker():
            folder, _ = files.view.resolve(path)
            source = str(values.get("source", "")).strip()
            database = str(values.get("commands", "")).strip()
            if not folder or not source or not database:
                raise OperationError("Choose the original kernel source and its compile_commands.json.")
            return kernel_workspace.compile_folder(
                folder, source, database, emit_rust=values.get("rust") == "yes",
                c2rust=str(values.get("c2rust", "")).strip() or None,
                vmlinux=str(values.get("vmlinux", "")).strip() or None,
            )

        operation(session, "Compile Kernel Sources", worker, lane="heavy", targets=(path,),
                  done=lambda report: session.message(f"Build report saved to\n{report}", "Kernel compilation", width=560))

    form(session, "Compile Kernel Sources", [
        {"key":"source","label":"Original kernel source folder","placeholder":"/path/to/kernel/common"},
        {"key":"commands","label":"Kbuild compilation database","browseFile":True,"nameFilters":["JSON files (*.json)"]},
        {"key":"vmlinux","label":"Same-build vmlinux with debug info (optional)","browseFile":True},
        {"key":"rust","label":"Export Rust with C2Rust","value":"no",
         "choices":[{"key":"no","label":"No"},{"key":"yes","label":"Yes"}]},
        {"key":"c2rust","label":"C2Rust executable (optional)","placeholder":"Use C2Rust from PATH if blank"},
    ], compile_selected, description="Use the source, toolchain and configured build that match this kernel. On Windows, compile in WSL.",
         width=560, accept="Compile")


def _prepare_kernel_build(session, files, path):
    from core import gki_checkout, wsl_manager
    from core.command_context import OperationError
    from core.threader_control import report_progress
    from ui.workspace_actions import operation

    def inspect():
        physical, _ = files.view.resolve(path)
        if not physical:
            raise OperationError(f"Kernel file not found: {path}")
        report_progress(10, "Detecting kernel and matching the AOSP release")
        info, release, reason = gki_checkout.inspect_release(physical)
        distro = wsl_manager.distro_for_path(physical)
        cache = gki_checkout.cache_checkout(release.build_id, release.commit, release=release, distro=distro) if release else {}
        return info, release, reason, distro, cache

    def show_release(result):
        info, release, reason, distro, cache = result
        if release is None:
            session.message(gki_checkout.vendor_requirements(info, reason), "Kernel build requirements")
            return

        def download():
            report_progress(10, "Syncing the pinned source and toolchain. See Kitchen Terminal for Repo progress.")
            gki_checkout.require_linux_tools(distro, "python3", "git", "repo")
            return gki_checkout.sync_release(release, distro=distro)

        def show_checkout(location):
            session.message(
                f"{release.display_name} ({release.build_id})\n\n"
                f"Checkout: {location}\nSource: {location}/common\n"
                f"Toolchain: {location}/{release.clang_path}\n\n"
                "Run the original build recipe in that environment to generate .config, generated headers "
                "and compile_commands.json.",
                "GKI checkout ready", width=580,
            )

        def confirm_download():
            operation(session, f"Download GKI {release.build_id}", download, done=show_checkout, lane="heavy")

        if cache.get("state") == "ready":
            show_checkout(cache["path"])
            return
        if cache.get("state") == "syncing":
            session.message(f"GKI build {release.build_id} is already syncing. See the Kitchen Terminal.",
                            "Prepare Kernel Build")
            return
        session.message(
            f"{release.display_name} ({release.build_id})\nCommit {release.commit}\n\n"
            f"Download {release.project_count} repositories pinned by the official build manifest, "
            "including source, build scripts and toolchain. The checkout uses the Linux home directory "
            "and can require many gigabytes. On Windows, use WSL with Python 3, Git and Repo.",
            "Prepare Kernel Build", confirm=confirm_download,
            accept="Resume" if cache.get("state") == "incomplete" else "Download", width=560,
        )

    operation(session, "Check kernel build", inspect, done=show_release, lane="light", targets=(path,))


def _manage_gki_cache(session, files, path):
    from core import gki_checkout, kernel_version, wsl_manager
    from core.command_context import OperationError
    from ui.workspace_actions import operation

    def inspect():
        physical, _ = files.view.resolve(path)
        if not physical:
            raise OperationError(f"Kernel file not found: {path}")
        info = kernel_version.inspect_kernel(physical)
        if not info.is_kernel:
            raise OperationError(info.error or "No kernel detected in this file.")
        distro = wsl_manager.distro_for_path(physical)
        entry = gki_checkout.cache_checkout(info.android_build_id, info.git_commit, distro=distro)
        return info.android_build_id, entry, distro

    def show_entry(result):
        build_id, entry, distro = result
        title = "GKI Build Cache"
        if not entry:
            session.message("No cached GKI checkout was found for this kernel in the selected Linux environment.", title)
            return
        location = entry["path"]
        environment = f"WSL: {entry['distro']}" if entry["distro"] else "Linux"
        if entry["state"] == "syncing":
            session.message(f"GKI {build_id} is syncing.\n{environment}\n{location}", title, width=580)
            return

        def remove():
            operation(
                session, f"Remove cached GKI {build_id}",
                lambda: gki_checkout.cache_checkout(build_id, entry["commit"], distro=entry["distro"] or distro, remove=True),
                done=lambda _: session.message(f"Removed cached checkout for GKI {build_id}.", title), lane="heavy",
            )

        state = {
            "ready": "Ready to reuse",
            "source": "Source references cached. Use Prepare Kernel Build to fetch the build tools.",
        }.get(entry["state"], "Partial checkout. Use Prepare Kernel Build to resume.")
        session.message(
            f"GKI {build_id}\n{state}\n{environment}\n{location}\n\n"
            "Remove the cached sources and downloaded build tools for this build?",
            title, confirm=remove, danger=True, accept="Remove", width=580,
        )

    operation(session, "Check GKI cache", inspect, done=show_entry, lane="light", targets=(path,))
