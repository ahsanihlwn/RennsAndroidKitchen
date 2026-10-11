# SPDX-License-Identifier: MPL-2.0
import os
import time

from core import rak_cli


_ACTION_META = {
    # basic
    "rename":            {"kind": "any", "needs_name": True,
                          "desc": "Ganti nama file/folder. Butuh arg 'name' (nama baru)."},
    "delete":            {"kind": "any",
                          "desc": "Hapus file/folder (CLI: tanpa dialog konfirmasi)."},
    "duplicate":         {"kind": "any",
                          "desc": "Duplikat file/folder jadi salinan (nama auto '(2)')."},
    "copy":              {"kind": "any",
                          "desc": "Salin file/folder ke clipboard tree (belum menulis apa pun; "
                                  "pakai 'paste' untuk menempelkannya)."},
    "cut":               {"kind": "any",
                          "desc": "Potong file/folder ke clipboard tree (pindah baru terjadi saat 'paste')."},
    "paste":             {"kind": "any",
                          "desc": "Tempel isi clipboard ke folder target (target file = folder induknya). "
                                  "Paste ke folder asal saat mode copy otomatis pakai nama '(2)'."},
    "new_file":          {"kind": "dir", "needs_name": True,
                          "desc": "Bikin file kosong baru di dalam folder. Butuh arg 'name'."},
    "new_folder":        {"kind": "dir", "needs_name": True,
                          "desc": "Bikin folder baru di dalam folder. Butuh arg 'name'."},
    "create_symlink":    {"kind": "any",
                          "desc": "Bikin symlink (lewat dialog; terbatas untuk CLI headless)."},
    "open_in_explorer":  {"kind": "any",
                          "desc": "Buka lokasi di File Explorer (efek UI, no disk change)."},
    # mount
    "mount":             {"kind": "file",
                          "desc": "Mount image EROFS/EXT/SPARSE jadi folder virtual read-only di tree."},
    "unmount":           {"kind": "file",
                          "desc": "Unmount image yg sebelumnya di-mount."},
    # selinux / branch / devicetree
    "neocat":            {"kind": "dir",
                          "desc": "Scan SELinux context (NeoCat) untuk FS-root folder."},
    "neobranch":         {"kind": "dir",
                          "desc": "Kelola NeoBranch (overlay) FS-root (lewat dialog di UI)."},
    "branch_create":     {"kind": "dir", "needs_name": True,
                          "desc": "Buat & aktifkan NeoBranch baru untuk folder partisi/fs. Arg: 'path' (folder partisi), 'name' (nama branch), opsional 'color'."},
    "branch_switch":     {"kind": "dir", "needs_name": True,
                          "desc": "Pindah NeoBranch atau kembali ke 'source' (no branch). Arg: 'path' (folder partisi), 'name' (nama branch atau 'source')."},
    "branch_list":       {"kind": "dir",
                          "desc": "Daftar semua NeoBranch untuk folder partisi/fs serta branch aktif saat ini. Arg: 'path' (folder partisi)."},
    "branch_current":    {"kind": "dir",
                          "desc": "Cek NeoBranch yang sedang aktif untuk folder partisi. Arg: 'path' (folder partisi)."},
    "branch_delete":     {"kind": "dir", "needs_name": True,
                          "desc": "Hapus NeoBranch permanen. Arg: 'path' (folder partisi), 'name' (nama branch)."},
    "gen_devicetree":    {"kind": "dir",
                          "desc": "Generate device tree (OFox/TWRP) dari boot-unpack + vendor."},
    # fs convert (folder)
    "convert_fs":        {"kind": "dir",
                          "desc": "Convert metadata FS-root: EROFS<->EXT4 (in-place, pure-python)."},
    # image convert (file)
    "convert_sparse":      {"kind": "file",
                            "desc": "SPARSE image -> RAW (lpunpack --unsparse), in-place."},
    "convert_to_sparse":   {"kind": "file",
                            "desc": "RAW .img -> SPARSE (img2simg), in-place."},
    "convert_img_to_br":   {"kind": "file",
                            "desc": ".img -> brotli (.img.br), source dipertahankan."},
    "convert_img_to_new_dat": {"kind": "file",
                            "desc": ".img -> .new.dat (+ .transfer.list)."},
    "convert_img_to_dat_br":  {"kind": "file",
                            "desc": ".img -> .new.dat.br (sparse->sdat->brotli)."},
    # super / sparse
    "open_super":        {"kind": "file",
                          "desc": "Buka image SUPER di tab khusus (efek UI)."},
    "unpack_super":      {"kind": "file",
                          "desc": "Unpack SUPER -> partisi-partisi (.img) via lpunpack."},
    "unpack_sparse":     {"kind": "file",
                          "desc": "Unpack SPARSE -> isi (unsparse lalu lpunpack)."},
    # boot
    "unpack_boot":       {"kind": "file",
                          "desc": "Unpack boot/vendor_boot image (magiskboot) -> folder."},
    "repack_boot":       {"kind": "dir",
                          "desc": "Repack folder boot/vendor_boot -> image (magiskboot)."},
    # erofs / ext
    "unpack_erofs":      {"kind": "file",
                          "desc": "Unpack EROFS image -> folder (+ config)."},
    "unpack_ext":        {"kind": "file",
                          "desc": "Unpack EXT2/3/4 image -> folder (+ config)."},
    "repack_erofs":      {"kind": "dir",
                          "desc": "Repack folder EROFS -> image (<name>_new.img)."},
    "repack_ext":        {"kind": "dir",
                          "desc": "Repack folder EXT -> image (<name>_new.img)."},
    "repack_erofs_to_image":   {"kind": "dir", "desc": "Repack EROFS -> .img."},
    "repack_erofs_to_brotli":  {"kind": "dir", "desc": "Repack EROFS -> .img.br."},
    "repack_erofs_to_new_dat": {"kind": "dir", "desc": "Repack EROFS -> .new.dat."},
    "repack_erofs_to_dat_br":  {"kind": "dir", "desc": "Repack EROFS -> .new.dat.br."},
    "repack_ext_to_image":     {"kind": "dir", "desc": "Repack EXT -> .img."},
    "repack_ext_to_brotli":    {"kind": "dir", "desc": "Repack EXT -> .img.br."},
    "repack_ext_to_new_dat":   {"kind": "dir", "desc": "Repack EXT -> .new.dat."},
    "repack_ext_to_dat_br":    {"kind": "dir", "desc": "Repack EXT -> .new.dat.br."},
    # brotli / dat
    "unpack_br_to_image":      {"kind": "file", "desc": ".img.br -> .img (decompress)."},
    "unpack_br_to_folder":     {"kind": "file", "desc": ".img.br -> folder (decompress+unpack)."},
    "unpack_dat_br_to_dat":    {"kind": "file", "desc": ".new.dat.br -> .new.dat."},
    "unpack_dat_br_to_image":  {"kind": "file", "desc": ".new.dat.br -> raw .img."},
    "unpack_dat_br_to_folder": {"kind": "file", "desc": ".new.dat.br -> folder."},
    "unpack_dat_to_image":     {"kind": "file", "desc": ".new.dat -> raw .img."},
    "unpack_dat_to_folder":    {"kind": "file", "desc": ".new.dat -> folder."},
    # archive
    "extract_here":      {"kind": "file",
                          "desc": "Extract arsip (zip/tar/dst) ke folder yg sama."},
    "extract_to":        {"kind": "file",
                          "desc": "Extract arsip ke subfolder bernama sama."},
}


_UI_ONLY = {"open_in_explorer", "open_super", "neobranch", "create_symlink",
            "copy", "cut"}


def available_actions():

    a = rak_cli.actions()
    return set(a["single"]), set(a["batch"])


def _scan(root, max_entries=20000):

    from core.rak_neobranch_view import view_for_directory
    out = {}
    try:
        view = view_for_directory(root)
    except (OSError, ValueError):
        return out
    stack = [root]
    while stack and len(out) < max_entries:
        logical_dir = stack.pop()
        try:
            entries = view.list_dir(logical_dir)
        except OSError:
            continue
        for entry in entries:
            physical, _ = view.resolve(entry["path"])
            if not physical:
                continue
            try:
                stat = os.stat(physical, follow_symlinks=False)
            except OSError:
                continue
            logical = os.path.normpath(entry["path"])
            is_dir = entry["kind"] == "dir"
            out[logical] = (is_dir, 0 if is_dir else stat.st_size, stat.st_mtime_ns)
            if is_dir and not os.path.islink(physical):
                stack.append(logical)
            if len(out) >= max_entries:
                break
    return out


def _diff(before, after):

    created, modified = [], []
    for p, meta in after.items():
        if p not in before:
            created.append(p)
        elif before[p] != meta:
            modified.append(p)
    created.sort()
    modified.sort()
    return created, modified


def _watch_root(action, target):

    target = os.path.normpath(target)
    if os.path.isdir(target):
        # untuk repack dir, output ditaro di parent → scan parent
        return os.path.dirname(target) or target
    return os.path.dirname(target) or target


def call_tool(name, args=None, *, timeout=300.0):

    args = args or {}
    path = args.get("path") or args.get("target")
    aname = args.get("name")

    res = {
        "ok": False, "action": name, "target": path, "name": aname,
        "artifacts": {"created": [], "modified": []},
        "message": "", "duration_ms": 0, "ui_only": name in _UI_ONLY,
        "error": None,
    }

    if not rak_cli.is_attached():
        res["error"] = "no live editor attached (app belum siap / RAK_CLI belum attach)"
        return res

    singles, _ = available_actions()
    if name not in singles:
        res["error"] = f"unknown/unavailable action: {name!r}"
        return res

    if not path:
        res["error"] = "arg 'path' wajib"
        return res

    from core import rak_fs_tools
    from core.rak_neobranch_view import ActiveProjectView
    path, perr = rak_fs_tools._resolve_in_sandbox(path)
    if perr:
        res["error"] = perr
        return res
    res["target"] = path
    physical, _ = ActiveProjectView(rak_fs_tools.workspace_root()).resolve(path)
    if not physical:
        res["error"] = f"path not found in active NeoBranch view: {path}"
        return res

    meta = _ACTION_META.get(name, {})
    if meta.get("needs_name") and not aname:
        res["error"] = f"action {name!r} butuh arg 'name'"
        return res

    watch = _watch_root(name, path)
    before = {} if res["ui_only"] else _scan(watch)

    t0 = time.time()
    try:
        ok, msg = rak_cli.run_and_wait(name, path, name=aname, timeout=timeout)
    except Exception as e:
        import traceback
        traceback.print_exc()
        res["error"] = f"exec error: {e}"
        res["duration_ms"] = int((time.time() - t0) * 1000)
        return res

    res["duration_ms"] = int((time.time() - t0) * 1000)
    res["ok"] = bool(ok)
    res["message"] = msg
    if not ok:
        res["error"] = msg
        return res

    if not res["ui_only"]:
        after = _scan(watch)
        created, modified = _diff(before, after)
        # buang target sendiri dari modified (mis. in-place convert) tetap
        # dilaporkan — itu informatif buat AI.
        res["artifacts"]["created"] = created
        res["artifacts"]["modified"] = modified
    return res


def call_batch(name, paths, *, timeout=600.0):

    res = {"ok": False, "action": name, "targets": list(paths or []),
           "message": "", "duration_ms": 0, "error": None,
           "artifacts": {"created": [], "modified": []}}

    if not rak_cli.is_attached():
        res["error"] = "no live editor attached"
        return res
    _, batches = available_actions()
    if name not in batches:
        res["error"] = f"unknown/unavailable batch action: {name!r}"
        return res
    if not paths:
        res["error"] = "paths kosong"
        return res

    from core import rak_fs_tools
    from core.rak_neobranch_view import ActiveProjectView
    view = ActiveProjectView(rak_fs_tools.workspace_root())
    resolved_paths = []
    for path in paths:
        logical, err = rak_fs_tools._resolve_in_sandbox(path)
        if err:
            res["error"] = err
            return res
        physical, _ = view.resolve(logical)
        if not physical:
            res["error"] = f"path not found in active NeoBranch view: {logical}"
            return res
        resolved_paths.append(logical)
    res["targets"] = resolved_paths
    watch_roots = sorted({_watch_root(name, p) for p in resolved_paths})
    before = {}
    for w in watch_roots:
        before.update(_scan(w))

    t0 = time.time()
    try:
        ok, msg = rak_cli.run_batch(name, resolved_paths)
        if ok:
            rak_cli.wait_idle(timeout=timeout)
    except Exception as e:
        import traceback
        traceback.print_exc()
        res["error"] = f"exec error: {e}"
        res["duration_ms"] = int((time.time() - t0) * 1000)
        return res

    res["duration_ms"] = int((time.time() - t0) * 1000)
    res["ok"] = bool(ok)
    res["message"] = msg
    if not ok:
        res["error"] = msg
        return res

    after = {}
    for w in watch_roots:
        after.update(_scan(w))
    created, modified = _diff(before, after)
    res["artifacts"]["created"] = created
    res["artifacts"]["modified"] = modified
    return res


def inspect(path):

    # resolve relatif-ke-workspace + sandbox biar AI bisa kasih path relatif.
    try:
        from core import rak_fs_tools
        resolved, err = rak_fs_tools._resolve_in_sandbox(path or "")
        if err:
            return {"path": path, "exists": False, "kind": None,
                    "fs_badge": None, "actions": [], "error": err}
        path = resolved
    except Exception:
        path = os.path.normpath(path) if path else ""
    out = {"path": path, "exists": False, "kind": None,
           "fs_badge": None, "actions": []}
    try:
        from core.rak_neobranch_view import ActiveProjectView
        physical, origin = ActiveProjectView(_active_workspace_root()).resolve(path)
    except (OSError, ValueError) as exc:
        out["error"] = str(exc)
        return out
    if not physical:
        return out
    out["exists"] = True
    out["origin"] = origin
    is_dir = os.path.isdir(physical)
    out["kind"] = "dir" if is_dir else "file"
    try:
        out["fs_badge"] = rak_cli.badge(path)
    except Exception:
        out["fs_badge"] = None

    singles, _ = available_actions()
    badge = out["fs_badge"]
    suggested = []
    for act, meta in _ACTION_META.items():
        if act not in singles:
            continue
        kind = meta.get("kind", "any")
        if kind == "dir" and not is_dir:
            continue
        if kind == "file" and is_dir:
            continue
        suggested.append(act)
    # filter kasar by badge biar relevan (opsional, AI tetap bisa coba yg lain)
    if badge:
        b = badge.upper()
        def _rel(a):
            if b == "EROFS":
                return ("erofs" in a or a in ("mount", "convert_fs", "neocat",
                        "neobranch", "duplicate", "copy", "cut", "paste",
                        "delete", "rename"))
            if b.startswith("EXT"):
                return ("ext" in a or a in ("mount", "convert_fs", "neocat",
                        "neobranch", "duplicate", "copy", "cut", "paste",
                        "delete", "rename"))
            if b == "SPARSE":
                return a in ("convert_sparse", "unpack_sparse", "mount",
                             "duplicate", "copy", "cut", "paste", "delete", "rename")
            if b == "SUPER":
                return a in ("open_super", "unpack_super", "convert_to_sparse",
                             "duplicate", "copy", "cut", "paste", "delete", "rename")
            if b in ("BOOT", "VNDRBOOT"):
                return a in ("unpack_boot", "repack_boot", "duplicate",
                             "copy", "cut", "paste", "delete", "rename")
            if b == "BR":
                return a.startswith("unpack_br") or a in ("delete", "rename", "duplicate",
                                                     "copy", "cut", "paste")
            if b == "DAT":
                return a.startswith("unpack_dat_to") or a in ("delete", "rename", "duplicate",
                                                     "copy", "cut", "paste")
            if b == "DAT.BR":
                return a.startswith("unpack_dat_br") or a in ("delete", "rename", "duplicate",
                                                     "copy", "cut", "paste")
            return True
        suggested = [a for a in suggested if _rel(a)]
    out["actions"] = suggested
    return out


def list_dir(path=None):

    # resolve relatif-ke-workspace + sandbox (sama kayak fs-tools), jadi AI bisa
    # kasih "boot" / "boot/ramdisk" tanpa path absolut.
    try:
        from core import rak_fs_tools
        resolved, err = rak_fs_tools._resolve_in_sandbox(path or "")
        if err:
            return {"path": path, "exists": False, "entries": [], "error": err}
        path = resolved
    except Exception:
        if not path:
            path = _active_workspace_root()
    out = {"path": path, "exists": False, "entries": []}
    if not path:
        return out
    try:
        from core.rak_neobranch_view import ActiveProjectView
        entries = ActiveProjectView(_active_workspace_root()).list_dir(path)
    except (OSError, ValueError) as e:
        out["error"] = str(e)
        return out
    out["exists"] = True
    for entry in entries[:200]:
        badge = None
        try:
            badge = rak_cli.badge(entry["path"])
        except Exception:
            pass
        out["entries"].append({**entry, "fs_badge": badge})
    return out


def web_search(query):
    from core.rennsproject_bridge import official_web_search
    return official_web_search(query)


def _active_workspace_root():

    try:
        from core import rak_cli as _rc
        main = _rc._main_ref
        tree = getattr(main, "active_tree", None) if main else None
        return getattr(tree, "base", None)
    except Exception:
        return None


def list_tools(only_available=True):

    singles, batches = available_actions()
    tools = []
    for act, meta in _ACTION_META.items():
        if only_available and act not in singles and not act.startswith("branch_"):
            continue
        props = {
            "path": {"type": "string",
                     "description": "Path absolut file/folder target."},
        }
        required = ["path"]
        if meta.get("needs_name"):
            props["name"] = {"type": "string",
                             "description": "Nama target (file/folder baru atau nama hasil rename)."}
            required.append("name")
        if act == "branch_create":
            props["color"] = {"type": "string",
                              "description": "Warna dot marker branch (blue, green, red, yellow, orange, purple, pink). Default 'blue'."}
        tools.append({
            "name": act,
            "description": meta.get("desc", act),
            "target_kind": meta.get("kind", "any"),
            "ui_only": act in _UI_ONLY,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    # meta-tools
    tools.append({
        "name": "inspect",
        "description": "Cek path: exists, kind, fs_badge, dan daftar aksi yg masuk akal.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
    })
    tools.append({
        "name": "list_dir",
        "description": ("Lihat isi folder pada tampilan NeoBranch aktif (source + overlay - deletion). "
                        "File yang hanya ada di branch tetap terlihat dengan path logisnya. "
                        "Kosongkan 'path' untuk root aktif. Jangan baca project lewat shell mentah."),
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string",
                            "description": "Folder yg mau dilihat. Kosong = root aktif."}},
                       "required": []},
    })
    # fs-tools (read/grep/edit/write/run_command) — bikin AI jadi agent beneran.
    try:
        from core import rak_fs_tools
        for d in rak_fs_tools.tool_declarations(include_destructive=True):
            tools.append({
                "name": d["name"], "description": d["description"],
                "destructive": d["destructive"], "parameters": d["parameters"],
            })
    except Exception:
        pass
    return tools


def read_only_tool_names():

    names = {"inspect", "list_dir", "branch_list", "branch_current"}
    try:
        from core import rak_fs_tools
        for n in rak_fs_tools.FS_TOOLS:
            if not rak_fs_tools.is_destructive(n):
                names.add(n)
    except Exception:
        pass
    return names


def _is_destructive_tool(name):

    if name in ("inspect", "list_dir", "web_search", "list_tools", "branch_list", "branch_current"):
        return False
    try:
        from core import rak_fs_tools
        if rak_fs_tools.is_fs_tool(name):
            return rak_fs_tools.is_destructive(name)
    except Exception:
        pass
    # Aksi RAK (unpack/repack/delete/convert/...) semuanya mengubah disk,
    # kecuali yang murni efek UI.
    return name not in _UI_ONLY


def dispatch_tool(name, args=None, *, can_edit=True):

    args = args or {}
    if not can_edit and _is_destructive_tool(name):
        return {"ok": False,
                "error": f"DITOLAK: '{name}' mengubah disk tapi mode Edit OFF. "
                         f"Nyalakan toggle Edit untuk mengizinkan."}
    if name == "inspect":
        return inspect(args.get("path"))
    if name == "list_dir":
        return list_dir(args.get("path"))
    if name == "web_search":
        return web_search(args.get("query"))
    if name == "list_tools":
        return {"tools": list_tools()}
    # branch tools
    if name == "branch_create":
        ok, msg = rak_cli.branch_create(args.get("path"), args.get("name"), color=args.get("color", "blue"))
        return {"ok": ok, "message": msg, "action": name, "target": args.get("path")}
    if name in ("branch_switch", "branch_checkout"):
        ok, msg = rak_cli.branch_switch(args.get("path"), args.get("name"))
        return {"ok": ok, "message": msg, "action": name, "target": args.get("path")}
    if name == "branch_list":
        ok, msg = rak_cli.branch_list(args.get("path"))
        return {"ok": ok, "message": msg, "action": name, "target": args.get("path")}
    if name in ("branch_current", "branch_status"):
        ok, msg = rak_cli.branch_current(args.get("path"))
        return {"ok": ok, "message": msg, "action": name, "target": args.get("path")}
    if name == "branch_delete":
        ok, msg = rak_cli.branch_delete(args.get("path"), args.get("name"))
        return {"ok": ok, "message": msg, "action": name, "target": args.get("path")}
    # fs-tools (read/grep/edit/write/run_command)
    try:
        from core import rak_fs_tools
        if rak_fs_tools.is_fs_tool(name):
            return rak_fs_tools.dispatch(name, args)
    except Exception as e:
        return {"ok": False, "error": f"fs tool error: {e}"}
    return call_tool(name, args)
