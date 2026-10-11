# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath

from core.vdev.initmodel import InitModel, parse_rc

print("[CORE STAGE] VDEV INITRC")

_ROOT_RC = ("/system/etc/init/hw/init.rc", "/init.rc")
_INIT_DIRS = ("/system/etc/init", "/system_ext/etc/init", "/vendor/etc/init",
              "/odm/etc/init", "/product/etc/init")
_MAX_DEPTH = 24


def collect(vfs, props):
    model = InitModel(props)
    visited = set()

    def load(vpath, depth):
        vpath = posixpath.normpath(vpath)
        if vpath in visited or depth > _MAX_DEPTH:
            return
        visited.add(vpath)
        node = vfs.lookup(vpath)
        if node is None:
            return
        if node.is_dir:
            for child in vfs.glob(vpath, ".rc"):
                load(child, depth + 1)
            return
        text = vfs.read_text(vpath)
        if not text:
            return
        model.files.append(vpath)
        svc, actions, imports, unknown = parse_rc(text, vpath, props)
        model.services.extend(svc)
        model.actions.extend(actions)
        model.unknown_props |= unknown
        for target, unk in imports:
            if unk:
                continue
            if not vfs.exists(target):
                model.missing_imports.append((target, vpath))
            else:
                load(target, depth + 1)

    root_used = ""
    for cand in _ROOT_RC:
        if vfs.is_file(cand):
            root_used = cand
            load(cand, 0)
            break
    for d in _INIT_DIRS:
        if vfs.is_dir(d):
            for p in vfs.glob(d, ".rc"):
                load(p, 1)

    model.root_rc = root_used
    return model

