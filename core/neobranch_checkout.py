# SPDX-License-Identifier: MPL-2.0
"""Recoverable physical NeoBranch checkouts for ordinary editors and host tools.

Snapshots never share writable files with the working directory.  A journal owns
every displaced path until the complete partition, metadata and state agree.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
import re
import errno
from concurrent.futures import ThreadPoolExecutor
from core.command_context import OperationError


def _helpers():
    from main_ext.class_NeoBranchManager import (
        CONFIG_SUFFIXES, _atomic_json_write, _remove_path, _retry_fs_op,
        SourceRestoreError, neocat_cache_dir,
    )
    return CONFIG_SUFFIXES, _atomic_json_write, _remove_path, _retry_fs_op, SourceRestoreError, neocat_cache_dir


def _owned(manager, root):
    # Partition names must be single components; never derive move/delete paths
    # from arbitrary state-file values or from symlinked internal storage.
    if not root or root in ('.', '..', 'config', 'neobranch') or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,128}',root):
        raise ValueError('Invalid NeoBranch partition')
    base = Path(manager.tree_base).resolve()
    store = base / 'neobranch' / root / 'checkout'
    if store.resolve() != store.absolute() or os.path.commonpath([str(store.resolve()), str(base.resolve())]) != str(base.resolve()):
        raise ValueError('NeoBranch storage escapes the project')
    store.mkdir(parents=True, exist_ok=True)
    return base, store


def _snapshot(store, identifier):
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid NeoBranch snapshot')
    path = store / 'snapshots' / identifier
    if path.resolve() != path.absolute():
        raise ValueError('Symlinked NeoBranch snapshot')
    return path


def _copy(source, target):
    if source.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _replace_path(source, target, remove):
    source_p = Path(source)
    target_p = Path(target)
    try:
        os.replace(source_p, target_p)
    except OSError as err:
        if getattr(err, 'winerror', None) == 17 or getattr(err, 'errno', None) == errno.EXDEV:
            temp_target = target_p.parent / f".tmp-{uuid.uuid4().hex}"
            if source_p.is_symlink():
                temp_target.parent.mkdir(parents=True, exist_ok=True)
                temp_target.symlink_to(os.readlink(source_p), target_is_directory=source_p.is_dir())
            elif source_p.is_dir():
                shutil.copytree(source_p, temp_target, symlinks=True)
            else:
                target_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_p, temp_target)
            try:
                os.replace(temp_target, target_p)
            finally:
                if temp_target.exists():
                    remove(str(temp_target))
            remove(str(source_p))
        else:
            raise


def _manifest(path):
    result = {}
    files=[]
    def visit(node, rel):
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode):
            result[rel] = ['link', os.readlink(node)]
        elif getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsupported directory junction/reparse point: ' + str(node))
        elif stat.S_ISDIR(info.st_mode):
            result[rel] = ['dir', stat.S_IMODE(info.st_mode)]
            for child in sorted(node.iterdir()):
                visit(child, (rel + '/' if rel else '') + child.name)
        elif stat.S_ISREG(info.st_mode):
            files.append((node,rel,info))
        else:
            raise ValueError('Unsupported special file: ' + str(node))
    if os.path.lexists(path):
        visit(path, '')
    def hash_file(entry):
        node,rel,info=entry
        digest=hashlib.sha256()
        with node.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return rel,['file',info.st_size,stat.S_IMODE(info.st_mode),info.st_mtime_ns,digest.hexdigest()]
    # Windows file opens can be far slower than hashing the tiny ROM configs.
    # Bound I/O concurrency while still reading every byte at each checkpoint.
    if len(files)>=64:
        with ThreadPoolExecutor(max_workers=8,thread_name_prefix='NeoBranchHash') as pool:
            result.update(pool.map(hash_file,files))
    else:result.update(map(hash_file,files))
    return result


def _locations(manager, root):
    suffixes, _, _, _, _, cache = _helpers()
    base = Path(manager.tree_base).absolute()
    if (base/'config').resolve() != base.resolve()/'config':
        raise ValueError('Project config directory must not be redirected through a symlink')
    return [(root, base / root)] + [
        ('config/' + root + suffix, base / 'config' / (root + suffix)) for suffix in suffixes
    ] + [('neocat/' + root + '_file_contexts', Path(cache(manager.tree_base)) / (root + '_file_contexts'))]


def _live_manifest(manager, root):
    return {key: _manifest(path) for key, path in _locations(manager, root)}


def _capture(manager, root, store, progress=None, before=None):
    _, write, _, _, _, _ = _helpers()
    if before is None:before = _live_manifest(manager, root)
    if not before[root] or before[root].get('', [''])[0] != 'dir':
        raise FileNotFoundError('Partition directory missing: ' + root)
    identifier = uuid.uuid4().hex
    target = _snapshot(store, identifier)
    target.mkdir(parents=True)
    if progress:
        progress('Checkpointing NeoBranch: ' + root)
    state=manager._read_state(root)
    previous=state.get('checkout_snapshots',{}).get(_key(state.get('active_branch')))
    reference=_snapshot(store,previous) if previous else None
    old_manifest=json.loads((reference/'manifest.json').read_text(encoding='utf-8')) if reference else {}
    copied_count=0
    def copy_entry(source,destination,key,relative=''):
        nonlocal copied_count
        entry=before[key][relative]
        old=reference/key/relative if reference else None
        if entry[0]=='dir':
            destination.mkdir(parents=True,exist_ok=True)
            for child in source.iterdir():
                copy_entry(child,destination/child.name,key,(relative+'/' if relative else '')+child.name)
            shutil.copystat(source,destination,follow_symlinks=False)
        elif entry[0]=='file' and old and old_manifest.get(key,{}).get(relative)==entry:
            destination.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(old,destination)
            except OSError:_copy(source,destination)
        else:_copy(source,destination)
        copied_count+=1
        if progress and copied_count%500==0:progress(f'Checkpointing {root}: {copied_count} entries copied')
    for key, source in _locations(manager, root):
        if os.path.lexists(source):
            copy_entry(source,target/key,key)
    copied = {key: _manifest(target / key) for key, _ in _locations(manager, root)}
    if before != copied or before != _live_manifest(manager, root):
        # Keep the completed candidate for diagnostics/retry; never replace live
        # bytes with a snapshot made while an external editor was changing them.
        raise OperationError('Files changed during NeoBranch checkpoint; finish the write and retry')
    write(str(target / 'manifest.json'), before)
    return identifier


def _key(name):
    return 'branch:' + name if name else 'source'


def _prune_snapshots(manager, root, store):
    """Retire old generations only after a complete checkout was committed."""
    keep=set(manager._read_state(root).get('checkout_snapshots',{}).values())
    snapshots=store/'snapshots'
    if not snapshots.is_dir():return
    for path in snapshots.iterdir():
        if path.name in keep or not (path/'manifest.json').is_file():continue
        try:
            checked=_snapshot(store,path.name)
            _helpers()[2](str(checked))
        except (OSError,ValueError):
            import logging
            logging.exception('NeoBranch snapshot retained after checkout: %s',path)


def checkpoint(manager, root, *, progress=None):
    _owned(manager,root)
    with manager.repack_transaction(root, progress=progress):
        recover(manager, root, progress=progress)
        if not manager.is_physical_checkout(root):
            return None
        _, store = _owned(manager, root)
        state = manager._read_state(root)
        key = _key(state.get('active_branch'))
        previous = state.get('checkout_snapshots', {}).get(key)
        observed=_live_manifest(manager,root)
        if previous:
            saved = _snapshot(store, previous) / 'manifest.json'
            if saved.is_file() and json.loads(saved.read_text(encoding='utf-8')) == observed:
                return previous
        identifier = _capture(manager, root, store, progress,before=observed)
        state.setdefault('checkout_snapshots', {})[key] = identifier
        manager._write_state(root, state)
        return identifier


def _legacy_snapshot(manager, root, name, source, store):
    _, write, remove, _, _, _ = _helpers()
    identifier = uuid.uuid4().hex
    target = _snapshot(store, identifier)
    shutil.copytree(source, target, symlinks=True)
    for rel in manager._read_branch_json(manager._branch_json_path(root, name)).get('deleted_paths', []):
        normalized = manager._normalize_branch_rel(root, name, rel)
        if normalized:
            path = target / normalized
            if os.path.commonpath([str(path.absolute()), str(target)]) != str(target):
                raise ValueError('Invalid deleted branch path')
            if os.path.commonpath([str(path.parent.resolve()), str(target)]) != str(target):
                raise ValueError('Deleted branch path traverses a symlink')
            if os.path.lexists(path):
                remove(str(path))
    overlay = Path(manager._branch_fs_dir(root, name))
    _manifest(overlay)  # Reject unsupported reparse points before copying them.
    def merge(src, dst):
        if src.is_dir() and not src.is_symlink():
            if os.path.lexists(dst) and (not dst.is_dir() or dst.is_symlink()):
                remove(str(dst))
            dst.mkdir(parents=True, exist_ok=True)
            for child in src.iterdir():
                merge(child, dst / child.name)
        else:
            if os.path.lexists(dst):
                remove(str(dst))
            _copy(src, dst)
    if overlay.is_dir():
        merge(overlay, target / root)
    for key, _ in _locations(manager, root)[1:]:
        folder, filename = key.split('/', 1)
        directory = manager._branch_config_dir(root, name) if folder == 'config' else manager._branch_neocat_config_dir(root, name)
        src = Path(directory) / filename
        if src.is_file():
            merge(src, target / key)
    manifest = {key: _manifest(target / key) for key, _ in _locations(manager, root)}
    write(str(target / 'manifest.json'), manifest)
    return identifier


def switch(manager, root, name, *, progress=None, cancelled=None):
    _, store = _owned(manager,root)
    if name is not None:
        manager._validate_branch_name(name)
        if not Path(manager._branch_json_path(root, name)).is_file():
            raise ValueError('Unknown NeoBranch: ' + name)
    with manager.repack_transaction(root, progress=progress, cancelled=cancelled):
        _, store = _owned(manager, root)
        recover(manager, root, progress=progress)
        if Path(manager._source_dir(root), 'repack_manifest.json').is_file():
            manager.restore_after_repack(root, progress=progress, cancelled=cancelled)
        state = manager._read_state(root)
        physical = state.get('workspace_mode') == 'checkout'
        if physical:
            checkpoint(manager, root, progress=progress)
            state = manager._read_state(root)
            if state.get('active_branch') == name:
                return
        else:
            # Legacy live partition is Source; preserve it before materializing
            # a sparse branch. Existing repack backups are never repurposed.
            state['checkout_snapshots'] = {'source': _capture(manager, root, store, progress)}
            old = state.get('active_branch')
            if old:
                state['checkout_snapshots'][_key(old)] = _legacy_snapshot(
                    manager, root, old, _snapshot(store, state['checkout_snapshots']['source']), store)
        snapshots = state['checkout_snapshots']
        if _key(name) not in snapshots:
            snapshots[_key(name)] = _legacy_snapshot(manager, root, name, _snapshot(store, snapshots['source']), store)
        desired = _snapshot(store, snapshots[_key(name)])
        state['workspace_mode'] = 'checkout'
        original_name=state.get('active_branch')
        state['active_branch'] = name
        current = snapshots[_key(original_name)] if physical else snapshots['source']
        if current == snapshots[_key(name)]:
            # A new Source-based branch already has these exact live bytes.
            # No directory swap or second full ROM copy is necessary.
            manager._write_state(root,state)
            _prune_snapshots(manager,root,store)
            return
        # Build the whole next checkout before touching a live path.
        transaction = store / ('transaction-' + uuid.uuid4().hex)
        transaction.mkdir()
        pieces = []
        for index, (key, live) in enumerate(_locations(manager, root)):
            source = desired / key
            stage = transaction / ('next-' + str(index))
            if os.path.lexists(source):
                _copy(source, stage)
            pieces.append({'key': key, 'old': os.path.lexists(live), 'new': os.path.lexists(stage), 'phase': 'pending'})
        # Detect writers racing the staging copy as well as the snapshot copy.
        if json.loads((_snapshot(store, current) / 'manifest.json').read_text(encoding='utf-8')) != _live_manifest(manager, root):
            raise OperationError('Files changed while preparing NeoBranch checkout; retry')
        journal = {'transaction': transaction.name, 'target_state': state, 'pieces': pieces}
        _helpers()[1](str(store / 'journal.json'), journal)
        recover(manager, root, progress=progress)


def recover(manager, root, *, progress=None):
    _, write, remove, retry, failure, _ = _helpers()
    _, store = _owned(manager, root)
    journal_path = store / 'journal.json'
    if not journal_path.is_file():
        return False
    try:
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        name = journal['transaction']
        if not name.startswith('transaction-') or len(name) != 44 or any(c not in '0123456789abcdef' for c in name[12:]):
            raise ValueError('Invalid checkout transaction')
        transaction = store / name
        if transaction.resolve() != transaction.absolute():
            raise ValueError('Symlinked checkout transaction')
        locations = _locations(manager, root)
        if [p['key'] for p in journal['pieces']] != [key for key, _ in locations]:
            raise ValueError('Invalid checkout paths')
        state = journal['target_state']
        if state.get('workspace_mode') != 'checkout':
            raise ValueError('Invalid checkout state')
        active=state.get('active_branch')
        if active is not None:manager._validate_branch_name(active)
        snapshots=state['checkout_snapshots']
        if 'source' not in snapshots or _key(active) not in snapshots:raise ValueError('Missing checkout snapshots')
        for identifier in snapshots.values():
            if not (_snapshot(store,identifier)/'manifest.json').is_file():raise ValueError('Missing snapshot manifest')
        for piece in journal['pieces']:
            if type(piece['old']) is not bool or type(piece['new']) is not bool or piece['phase'] not in ('pending','displaced','installed'):
                raise ValueError('Invalid checkout phase')
        if progress:
            progress('Installing NeoBranch checkout: ' + root)
        for index, (piece, (_, live)) in enumerate(zip(journal['pieces'], locations)):
            stage = transaction / ('next-' + str(index))
            previous = transaction / ('previous-' + str(index))
            if piece['phase'] == 'pending':
                if piece['old'] and not os.path.lexists(previous):
                    retry(lambda: _replace_path(live, previous, remove), str(live))
                piece['phase'] = 'displaced'
                write(str(journal_path), journal)
            if piece['phase'] == 'displaced':
                if piece['new'] and os.path.lexists(stage):
                    live.parent.mkdir(parents=True, exist_ok=True)
                    retry(lambda: _replace_path(stage, live, remove), str(live))
                elif piece['new'] and not os.path.lexists(live):
                    raise FileNotFoundError('Missing staged checkout: ' + str(stage))
                piece['phase'] = 'installed'
                write(str(journal_path), journal)
        manager._write_state(root, state)
        journal_path.unlink()
        # Only this validated, owned transaction is eligible for recursive removal.
        try:
            remove(str(transaction))
        except OSError:
            import logging
            logging.exception('NeoBranch checkout installed; old transaction retained: %s', transaction)
        _prune_snapshots(manager,root,store)
        return True
    except Exception as error:
        if isinstance(error, failure):
            raise
        raise failure(root, 'checkout', str(journal_path), error) from error


def create(manager, root, name, color, *, progress=None):
    _owned(manager,root)
    manager._validate_branch_name(name)
    if Path(manager._branch_dir(root,name)).exists():raise ValueError('NeoBranch already exists: '+name)
    with manager.repack_transaction(root,progress=progress):
        active = manager.get_active_branch(root)
        switch(manager, root, active['name'] if active else None,progress=progress)
        info = manager.create_branch(root, name, color)
        state = manager._read_state(root)
        # New branches start from Source, including its Android metadata.
        state['checkout_snapshots'][_key(name)] = state['checkout_snapshots']['source']
        manager._write_state(root, state)
        switch(manager, root, name,progress=progress)
        try:
            from core import filesystem_map
            filesystem_map.clone_branch_map(manager.tree_base, root, None, name)
        except Exception:
            pass
        return info
