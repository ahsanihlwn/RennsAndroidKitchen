# SPDX-License-Identifier: MPL-2.0
import os
import io
import re
import subprocess
import shlex

from core.proc_util import CREATE_NO_WINDOW


MAX_READ_BYTES = 256 * 1024       # 256KB per read (cukup buat init.rc/fstab/prop)
MAX_GREP_MATCHES = 400
MAX_GREP_FILE_BYTES = 4 * 1024 * 1024
GREP_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".idea", ".vscode"}


def workspace_root():

    try:
        from core import rak_cli
        main = rak_cli._main_ref
        tree = getattr(main, "active_tree", None) if main else None
        base = getattr(tree, "base", None)
        return os.path.normpath(base) if base else None
    except Exception:
        return None


def _resolve_in_sandbox(path):

    root = workspace_root()
    if not root:
        return None, "tidak ada workspace aktif (buka project dulu)"
    if not path or not str(path).strip():
        return root, None
    p = str(path).strip()


    cand = os.path.normpath(p if os.path.isabs(p) else os.path.join(root, p))
    root_real = os.path.realpath(root)
    cand_real = os.path.realpath(cand)
    # cek containment (case-insensitive di Windows)
    try:
        common = os.path.commonpath([os.path.normcase(root_real), os.path.normcase(cand_real)])
    except ValueError:
        return None, f"path di luar workspace: {p}"
    if common != os.path.normcase(root_real):
        return None, f"DITOLAK: '{p}' di luar workspace ({root})"
    return cand, None


def _rel(root, p):
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except Exception:
        return p


def read_file(path, max_bytes=MAX_READ_BYTES, start_line=None, end_line=None):

    full, err = _resolve_in_sandbox(path)
    if err:
        return {"ok": False, "error": err}
    try:
        from core.rak_neobranch_view import ActiveProjectView
        physical, origin = ActiveProjectView(workspace_root()).resolve(full)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    if not physical or not os.path.isfile(physical):
        return {"ok": False, "error": f"bukan file / tidak ada: {path}"}
    try:
        size = os.path.getsize(physical)
        with open(physical, "rb") as f:
            raw = f.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        # decode toleran
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines)
        if start_line is not None or end_line is not None:
            s = max(1, int(start_line or 1))
            e = int(end_line) if end_line else total_lines
            sel = lines[s - 1:e]
            content = "\n".join(sel)
        else:
            content = text
        return {"ok": True, "path": full, "origin": origin, "size": size,
                "lines": total_lines, "truncated": truncated,
                "content": content}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def grep_search(pattern, path=None, *, ignore_case=True, glob=None, max_matches=MAX_GREP_MATCHES):

    root, err = _resolve_in_sandbox(path if path else "")
    if err:
        return {"ok": False, "error": err}
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return {"ok": False, "error": f"regex invalid: {e}"}
    wsroot = workspace_root()

    glob_rx = None
    if glob:
        # ubah glob sederhana (*.rc, fstab*) jadi regex
        gx = re.escape(glob).replace(r"\*", ".*").replace(r"\?", ".")
        glob_rx = re.compile("^" + gx + "$", re.IGNORECASE)

    matches = []
    truncated = False
    from core.rak_neobranch_view import ActiveProjectView
    view = ActiveProjectView(wsroot)
    try:
        targets = view.walk_files(root, skip_dirs=GREP_SKIP_DIRS)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        for logical in targets:
            if len(matches) >= max_matches:
                truncated = True
                break
            if glob_rx and not glob_rx.match(os.path.basename(logical)):
                continue
            try:
                fp, _ = view.resolve(logical)
                if not fp or os.path.getsize(fp) > MAX_GREP_FILE_BYTES:
                    continue
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            matches.append({"path": _rel(wsroot, logical), "line": i,
                                            "text": line.rstrip("\n")[:300]})
                            if len(matches) >= max_matches:
                                truncated = True
                                break
            except (OSError, UnicodeError):
                continue
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "pattern": pattern, "count": len(matches),
            "truncated": truncated, "matches": matches}


def write_file(path, content, *, create_dirs=True):

    full, err = _resolve_in_sandbox(path)
    if err:
        return {"ok": False, "error": err}
    if not create_dirs and not os.path.isdir(os.path.dirname(full)):
        return {"ok": False, "error": f"folder parent tidak ada: {path}"}
    try:
        data = content if isinstance(content, str) else str(content)
        # Single mutation gateway: records NeoBranch edits and refreshes the
        # live tree instead of writing silently behind its back.
        from core import rak_cli
        ok, msg = rak_cli.write_text(full, data)
        if not ok:
            return {"ok": False, "error": msg}
        return {"ok": True, "path": full, "bytes": len(data.encode("utf-8")),
                "message": msg}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def edit_file(path, old_string, new_string, *, replace_all=False):

    full, err = _resolve_in_sandbox(path)
    if err:
        return {"ok": False, "error": err}
    try:
        from core import rak_cli
        ok, msg = rak_cli.replace_text(full, old_string, new_string,
                                       replace_all=replace_all)
        if not ok:
            return {"ok": False, "error": msg}
        return {"ok": True, "path": full,
                "replacements": "all" if replace_all else 1,
                "message": msg}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def run_command(command, *, timeout=60, cwd=None):


    if os.environ.get("RAK_ENABLE_AGENT_COMMANDS", "").strip().lower() not in ("1", "true", "yes"):
        return {
            "ok": False,
            "error": "agent shell is disabled by default; use structured RAK commands",
        }

    _BLOCKED_PATTERNS = [


        # Block alternate shell spawns / nested shells yang bisa hindari filter
        r"\bpowershell\b",
        r"\bpwsh\b",
        r"\bcmd\.exe\b",

        # Block output redirection absolut (mencegah overwrite file sistem/host)
        r">\s*/",           # absolute Linux path (Cygwin/WSL/GitBash)
        r">\s*[A-Za-z]:\\", # absolute Windows path

        # Block command network yang sering dipakai untuk exfiltrasi / download payload
        r"\b(?:curl|wget|nc|netcat)\b",
    ]
    import re as _re
    cmd_str = str(command)
    for pat in _BLOCKED_PATTERNS:
        if _re.search(pat, cmd_str, _re.IGNORECASE):
            return {
                "ok": False,
                "error": (
                    f"[SEC] Command diblokir: pola berbahaya terdeteksi ({pat!r}). "
                    f"Gunakan command tunggal tanpa chaining/redirection ke path absolut."
                ),
            }
    # End Shell Injection Guard

    root = workspace_root()
    if not root:
        return {"ok": False, "error": "tidak ada workspace aktif"}
    from core.rak_neobranch_view import ActiveProjectView
    if ActiveProjectView(root).active_branches():
        return {"ok": False, "error": (
            "agent shell dinonaktifkan saat NeoBranch aktif; "
            "gunakan perintah RAK terstruktur agar file branch terbaca dengan benar"
        )}
    run_cwd = root
    if cwd:
        run_cwd, err = _resolve_in_sandbox(cwd)
        if err:
            return {"ok": False, "error": err}
        if not os.path.isdir(run_cwd):
            return {"ok": False, "error": f"cwd bukan folder: {cwd}"}
    if not command or not str(command).strip():
        return {"ok": False, "error": "command kosong"}
    try:
        argv = shlex.split(str(command), posix=False)
        if not argv:
            return {"ok": False, "error": "command kosong"}
        p = subprocess.run(
            argv, shell=False, cwd=run_cwd,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW)
        def _cap(s, n=8000):
            s = s or ""
            return s if len(s) <= n else s[:n] + f"\n…(+{len(s)-n} char)"
        return {"ok": p.returncode == 0, "exit_code": p.returncode,
                "stdout": _cap(p.stdout), "stderr": _cap(p.stderr),
                "cwd": run_cwd}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


FS_TOOLS = {
    "read_file": dict(
        fn=read_file, destructive=False,
        desc="Baca versi aktif file teks di NeoBranch, termasuk file yang hanya ada di branch. Sandboxed ke workspace.",
        props={"path": {"type": "string", "description": "Path file (relatif workspace atau absolut di dalamnya)."},
               "start_line": {"type": "integer", "description": "Baris awal (opsional, 1-based)."},
               "end_line": {"type": "integer", "description": "Baris akhir (opsional)."}},
        required=["path"]),
    "grep_search": dict(
        fn=grep_search, destructive=False,
        desc="Cari regex di file yang terlihat pada NeoBranch aktif (rekursif, sandboxed).",
        props={"pattern": {"type": "string", "description": "Regex yg dicari."},
               "path": {"type": "string", "description": "Folder/file awal (opsional, default root)."},
               "glob": {"type": "string", "description": "Filter nama file, mis. '*.rc' (opsional)."}},
        required=["pattern"]),
    "write_file": dict(
        fn=write_file, destructive=True,
        desc="Tulis/overwrite file (sandboxed). DESTRUKTIF.",
        props={"path": {"type": "string"},
               "content": {"type": "string", "description": "Isi file baru."}},
        required=["path", "content"]),
    "edit_file": dict(
        fn=edit_file, destructive=True,
        desc="Ganti old_string -> new_string di file (old_string harus unik). DESTRUKTIF.",
        props={"path": {"type": "string"},
               "old_string": {"type": "string", "description": "Teks lama (harus persis & unik)."},
               "new_string": {"type": "string", "description": "Teks pengganti."},
               "replace_all": {"type": "boolean", "description": "Ganti semua kemunculan (opsional)."}},
        required=["path", "old_string", "new_string"]),
}


def tool_declarations(include_destructive=True):

    out = []
    for name, t in FS_TOOLS.items():
        if t["destructive"] and not include_destructive:
            continue
        out.append({
            "name": name, "description": t["desc"],
            "destructive": t["destructive"],
            "parameters": {"type": "object", "properties": t["props"],
                           "required": t["required"]},
        })
    return out


def is_fs_tool(name):
    return name in FS_TOOLS


def is_destructive(name):
    return bool(FS_TOOLS.get(name, {}).get("destructive"))


def dispatch(name, args):

    t = FS_TOOLS.get(name)
    if not t:
        return {"ok": False, "error": f"unknown fs tool: {name}"}
    args = dict(args or {})
    try:
        return t["fn"](**args)
    except TypeError as e:
        return {"ok": False, "error": f"arg salah untuk {name}: {e}"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
