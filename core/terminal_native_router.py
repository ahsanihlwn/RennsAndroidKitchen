# SPDX-License-Identifier: MPL-2.0
"""Locked first-token terminal router backed in-process by `rak_native`."""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Callable

LOCKED_NATIVE_TERMINAL_TOOLS = frozenset({
    "adb",
    "fastboot",
    "erofs",
    "erofs-rs",
    "erofs_rs",
    "dump.erofs",
    "extract.erofs",
    "mkfs.erofs",
    "fsck.erofs",
    "ext_cli",
    "ext_tool",
    "make_ext4fs",
    "lpunpack",
    "lpmake",
    "magiskboot",
    "brotli",
    "unbrotli",
    "brcat",
    "brotli_rs",
    "img2simg",
    "simg2img",
    "sdat2img",
    "img2sdat",
    "sdatimg",
})

_SHELL_OPERATORS = frozenset({"|", "||", "&&", "&", ";", "<", ">", ">>", "2>", "2>&1"})

_CMD_PROMPT_RE = re.compile(r"^(?P<cwd>(?:[A-Za-z]:\\|\\\\)[^>\r\n]*)>(?P<cmd>.*)$")
_PS_PROMPT_RE = re.compile(r"^PS\s+(?P<cwd>(?:[A-Za-z]:\\|\\\\)[^>\r\n]*)>(?P<cmd>.*)$")
_POSIX_PROMPT_RE = re.compile(
    r"^(?:[^$#\r\n]+:)?(?P<cwd>(?:/|[A-Za-z]:/|~)[^$#\r\n]*)\s*[$#]\s+(?P<cmd>.*)$"
)


def normalize_command_token(token: str) -> str:
    raw = str(token or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    base = os.path.basename(raw.replace("\\", "/")).lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


def split_command_tokens(command: str) -> list[str]:
    clean = str(command or "").strip()
    if not clean:
        return []
    try:
        return shlex.split(clean, posix=False)
    except ValueError:
        return clean.split()


def _strip_outer_quotes(token: str) -> str:
    if len(token) >= 2 and (
        (token[0] == '"' and token[-1] == '"')
        or (token[0] == "'" and token[-1] == "'")
    ):
        return token[1:-1]
    return token


def _has_unquoted_shell_operator(tokens: list[str]) -> bool:
    for tok in tokens:
        if tok in _SHELL_OPERATORS:
            return True
        if tok.startswith(("\"", "'")):
            continue
        if any(op in tok for op in ("|", "&", ";", ">", "<")):
            return True
    return False


def is_locked_terminal_command(command_or_token: str) -> bool:
    tokens = split_command_tokens(command_or_token)
    if not tokens:
        return False
    if _has_unquoted_shell_operator(tokens):
        return False
    return normalize_command_token(tokens[0]) in LOCKED_NATIVE_TERMINAL_TOOLS


def extract_prompt_command(
    lines: list[str],
    cursor_row: int,
    cols: int,
    fallback_cwd: str = "",
) -> tuple[str, str, list[str], str] | None:
    """Inspect the active shell prompt row and return (cwd, raw_cmd, tokens, shell_kind) if locked."""
    if not lines or cursor_row < 0 or cursor_row >= len(lines):
        return None

    # Reconstruct wrapped input if previous rows hit the right column edge.
    start_row = cursor_row
    while start_row > 0:
        prev = lines[start_row - 1]
        if cols > 0 and len(prev) >= cols:
            if (
                _PS_PROMPT_RE.match(prev)
                or _CMD_PROMPT_RE.match(prev)
                or _POSIX_PROMPT_RE.match(prev)
            ):
                start_row -= 1
                break
            start_row -= 1
        else:
            break

    joined = "".join(lines[start_row : cursor_row + 1])
    shell_kind = "cmd"
    prompt_cwd = ""
    raw_cmd = ""

    for pattern, kind in (
        (_PS_PROMPT_RE, "powershell"),
        (_CMD_PROMPT_RE, "cmd"),
        (_POSIX_PROMPT_RE, "posix"),
    ):
        m = pattern.match(joined)
        if m:
            shell_kind = kind
            prompt_cwd = m.group("cwd").strip()
            raw_cmd = m.group("cmd").strip()
            break
    else:
        return None

    if not raw_cmd:
        return None

    tokens = split_command_tokens(raw_cmd)
    if not tokens or _has_unquoted_shell_operator(tokens):
        return None

    if normalize_command_token(tokens[0]) not in LOCKED_NATIVE_TERMINAL_TOOLS:
        return None

    effective_cwd = prompt_cwd if (prompt_cwd and os.path.isdir(prompt_cwd)) else (fallback_cwd or os.getcwd())
    cleaned_tokens = [_strip_outer_quotes(t) for t in tokens]
    return effective_cwd, raw_cmd, cleaned_tokens, shell_kind


def _resolve_path(cwd: str, raw_path: str) -> str:
    p = _strip_outer_quotes(raw_path)
    if not p:
        return cwd
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(cwd, p))


def _ensure_adb_server_if_needed(tool_args: list[str]) -> None:
    from core import native_backend

    i = 0
    host = "127.0.0.1"
    port = 5037
    subcmd = ""
    while i < len(tool_args):
        tok = tool_args[i]
        if tok in ("-s", "-t", "-H", "-P", "-L") and i + 1 < len(tool_args):
            if tok == "-H":
                host = tool_args[i + 1]
            elif tok == "-P":
                try:
                    port = int(tool_args[i + 1])
                except ValueError:
                    pass
            i += 2
            continue
        if tok in ("-d", "-e", "-a"):
            i += 1
            continue
        subcmd = tok.lower()
        break

    no_server_cmds = {
        "", "version", "--version", "help", "--help", "-h",
        "keygen", "kill-server", "start-server", "server", "nodaemon",
    }
    if subcmd not in no_server_cmds:
        try:
            native_backend.adb_ensure_server(host, port)
        except Exception:
            pass


def _run_adb_or_fastboot(
    tool: str,
    args: list[str],
    cwd: str,
    emit: Callable[[str], None],
    cancel_token=None,
) -> int:
    from core import native_backend

    if tool == "adb":
        _ensure_adb_server_if_needed(args)

    streamed = False

    def on_output(text: str, _is_stderr: bool) -> None:
        nonlocal streamed
        if text:
            streamed = True
            emit(text)

    last_pct = -1

    def on_progress(info: dict) -> None:
        nonlocal last_pct
        if not isinstance(info, dict):
            return
        pct = info.get("percent")
        stage = info.get("stage") or ""
        part = info.get("partition") or ""
        detail = info.get("detail") or ""
        if isinstance(pct, (int, float)):
            ipct = int(pct)
            if ipct == last_pct and ipct not in (0, 100):
                return
            last_pct = ipct
            label = f"{part} {stage}".strip() or stage or detail
            emit(f"[{ipct}%] {label}\n" if label else f"[{ipct}%]\n")

    runner = native_backend.adb_run if tool == "adb" else native_backend.fastboot_run
    res = runner(
        args,
        cwd=cwd,
        cancel=cancel_token,
        on_output=on_output,
        on_progress=on_progress if tool == "fastboot" else None,
    )
    if not streamed:
        out = res.get("output") or ((res.get("stdout") or "") + (res.get("stderr") or ""))
        if out:
            emit(out if out.endswith("\n") else out + "\n")
    return int(res.get("exit_code", 0))


def _format_kv(data: dict, emit: Callable[[str], None]) -> None:
    emit(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _parse_int(val: str) -> int:
    s = val.strip().lower()
    if s.endswith("k"):
        return int(s[:-1]) * 1024
    if s.endswith("m"):
        return int(s[:-1]) * 1024 * 1024
    if s.endswith("g"):
        return int(s[:-1]) * 1024 * 1024 * 1024
    return int(s, 0)


def _run_erofs(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    def progress_cb(pct: int, stage: str) -> None:
        emit(f"[{pct}%] {stage}\n")

    if tool == "dump.erofs":
        if not args or args[0] in ("-h", "--help", "help"):
            emit("dump.erofs (native erofs-rs): dump.erofs [--json] <image.img>\n")
            return 0
        img = _resolve_path(cwd, args[-1])
        info = native_backend.erofs_info(img)
        _format_kv(info, emit)
        return 0

    if tool == "fsck.erofs":
        if not args or args[0] in ("-h", "--help", "help"):
            emit("fsck.erofs (native erofs-rs): fsck.erofs <image.img>\n")
            return 0
        img = _resolve_path(cwd, args[-1])
        rep = native_backend.erofs_verify(img)
        _format_kv(rep, emit)
        return 0 if rep.get("ok", True) else 1

    if tool == "extract.erofs":
        if not args or args[0] in ("-h", "--help", "help"):
            emit("extract.erofs (native erofs-rs): extract.erofs -i <image.img> [-o <out_dir>] [-x]\n")
            return 0
        img = None
        out_dir = None
        threads = 0
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-i", "--image") and i + 1 < len(args):
                img = _resolve_path(cwd, args[i + 1])
                i += 2
            elif a in ("-o", "--out") and i + 1 < len(args):
                out_dir = _resolve_path(cwd, args[i + 1])
                i += 2
            elif a.startswith("--threads=") or a.startswith("-T"):
                val = a.split("=", 1)[1] if "=" in a else a[2:]
                threads = int(val or 0)
                i += 1
            elif not a.startswith("-"):
                if img is None:
                    img = _resolve_path(cwd, a)
                elif out_dir is None:
                    out_dir = _resolve_path(cwd, a)
                i += 1
            else:
                i += 1
        if not img:
            emit("error: extract.erofs requires <image.img>\n")
            return 2
        if not out_dir:
            out_dir = os.path.dirname(img)
        rep = native_backend.erofs_unpack(img, out_dir, threads=threads, progress=progress_cb)
        _format_kv(rep, emit)
        return 0

    if tool == "mkfs.erofs":
        if not args or args[0] in ("-h", "--help", "help"):
            emit("mkfs.erofs (native erofs-rs): mkfs.erofs [-z ALGO] [-T TS] [-U UUID] [--mount-point=/p] [--fs-config-file=F] [--file-contexts=F] <out.img> <src_dir>\n")
            return 0
        algo = None
        ts = None
        uuid_val = None
        mount_point = None
        fs_config = None
        file_contexts = None
        threads = 0
        pos: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("-z"):
                algo = a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else None)
                if len(a) == 2:
                    i += 1
            elif a.startswith("-T"):
                val = a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else "0")
                ts = int(val)
                if len(a) == 2:
                    i += 1
            elif a.startswith("-U"):
                uuid_val = a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else None)
                if len(a) == 2:
                    i += 1
            elif a.startswith("--mount-point="):
                mount_point = a.split("=", 1)[1]
            elif a.startswith("--fs-config-file="):
                fs_config = _resolve_path(cwd, a.split("=", 1)[1])
            elif a.startswith("--file-contexts="):
                file_contexts = _resolve_path(cwd, a.split("=", 1)[1])
            elif a.startswith("--workers="):
                threads = int(a.split("=", 1)[1])
            elif not a.startswith("-"):
                pos.append(a)
            i += 1
        if len(pos) < 2:
            emit("error: mkfs.erofs requires <out.img> <src_dir>\n")
            return 2
        out_img = _resolve_path(cwd, pos[0])
        src_dir = _resolve_path(cwd, pos[1])
        rep = native_backend.erofs_pack(
            src_dir,
            out_img,
            algo=algo,
            mount_point=mount_point,
            fs_config=fs_config,
            file_contexts=file_contexts,
            uuid=uuid_val,
            timestamp=ts,
            threads=threads,
            progress=progress_cb,
        )
        _format_kv(rep, emit)
        return 0

    # Unified `erofs` / `erofs-rs`
    if not args or args[0] in ("-h", "--help", "help"):
        emit(
            "erofs (native erofs-rs in-process)\n"
            "Commands:\n"
            "  erofs detect <image>\n"
            "  erofs info <image>\n"
            "  erofs list <image> [path]\n"
            "  erofs stat <image> <path>\n"
            "  erofs read <image> <path>\n"
            "  erofs unpack <image> [-o <dir>]\n"
            "  erofs pack <src_dir> <out.img> [--algo=...] [--mount-point=...] [--fs-config=...] [--file-contexts=...]\n"
            "  erofs verify <image>\n"
            "  erofs usage <image>\n"
            "  erofs compact <image> [--dry-run]\n"
            "  erofs avb inspect|strip <image>\n"
        )
        return 0

    sub = args[0].lower()
    rest = args[1:]
    if sub == "detect" and rest:
        _format_kv(native_backend.erofs_detect(_resolve_path(cwd, rest[0])), emit)
        return 0
    if sub == "info" and rest:
        _format_kv(native_backend.erofs_info(_resolve_path(cwd, rest[0])), emit)
        return 0
    if sub in ("list", "ls") and rest:
        img = _resolve_path(cwd, rest[0])
        target = rest[1] if len(rest) > 1 and not rest[1].startswith("-") else None
        rows = native_backend.erofs_list(img, target=target)
        for node in rows:
            emit(f"{node.get('perm', '')}\t{node.get('size', 0)}\t{node.get('path', '')}\n")
        return 0
    if sub == "stat" and len(rest) >= 2:
        _format_kv(native_backend.erofs_stat(_resolve_path(cwd, rest[0]), rest[1]), emit)
        return 0
    if sub in ("read", "cat") and len(rest) >= 2:
        data = native_backend.erofs_read_bytes(_resolve_path(cwd, rest[0]), rest[1])
        emit(data.decode("utf-8", errors="replace"))
        if not data.endswith(b"\n"):
            emit("\n")
        return 0
    if sub == "unpack" and rest:
        img = _resolve_path(cwd, rest[0])
        out_dir = os.path.dirname(img)
        if "-o" in rest:
            idx = rest.index("-o")
            if idx + 1 < len(rest):
                out_dir = _resolve_path(cwd, rest[idx + 1])
        _format_kv(native_backend.erofs_unpack(img, out_dir, progress=progress_cb), emit)
        return 0
    if sub == "pack" and len(rest) >= 2:
        src_dir = _resolve_path(cwd, rest[0])
        out_img = _resolve_path(cwd, rest[1])
        kw: dict = {"progress": progress_cb}
        for tok in rest[2:]:
            if tok.startswith("--algo="):
                kw["algo"] = tok.split("=", 1)[1]
            elif tok.startswith("--mount-point="):
                kw["mount_point"] = tok.split("=", 1)[1]
            elif tok.startswith("--fs-config="):
                kw["fs_config"] = _resolve_path(cwd, tok.split("=", 1)[1])
            elif tok.startswith("--file-contexts="):
                kw["file_contexts"] = _resolve_path(cwd, tok.split("=", 1)[1])
        _format_kv(native_backend.erofs_pack(src_dir, out_img, **kw), emit)
        return 0
    if sub == "verify" and rest:
        rep = native_backend.erofs_verify(_resolve_path(cwd, rest[0]))
        _format_kv(rep, emit)
        return 0 if rep.get("ok", True) else 1
    if sub == "usage" and rest:
        _format_kv(native_backend.erofs_usage(_resolve_path(cwd, rest[0])), emit)
        return 0
    if sub == "compact" and rest:
        dry = "--dry-run" in rest
        _format_kv(native_backend.erofs_compact(_resolve_path(cwd, rest[0]), dry_run=dry), emit)
        return 0
    if sub == "avb" and len(rest) >= 2:
        act = rest[0].lower()
        img = _resolve_path(cwd, rest[1])
        if act == "inspect":
            _format_kv(native_backend.erofs_avb_inspect(img), emit)
            return 0
        if act == "strip":
            _format_kv(native_backend.erofs_avb_strip(img), emit)
            return 0
    emit(f"error: unsupported erofs command: {' '.join(args)}\n")
    return 2


def _run_ext(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    def progress_cb(pct: int, stage: str) -> None:
        emit(f"[{pct}%] {stage}\n")

    if tool == "make_ext4fs":
        if not args or args[0] in ("-h", "--help", "help"):
            emit("make_ext4fs (native make_ext4fs_rs): make_ext4fs [-s] [-l SIZE] [-a MOUNT] [-C FS_CONFIG] [-S FILE_CONTEXTS] [-L LABEL] [-b BLKSIZE] <out.img> [src_dir]\n")
            return 0
        sparse = False
        size_bytes = 0
        mount_point = None
        fs_config = None
        file_contexts = None
        label = None
        block_size = 4096
        pos: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-s":
                sparse = True
            elif a == "-l" and i + 1 < len(args):
                size_bytes = _parse_int(args[i + 1])
                i += 1
            elif a == "-a" and i + 1 < len(args):
                mount_point = args[i + 1]
                i += 1
            elif a == "-C" and i + 1 < len(args):
                fs_config = _resolve_path(cwd, args[i + 1])
                i += 1
            elif a == "-S" and i + 1 < len(args):
                file_contexts = _resolve_path(cwd, args[i + 1])
                i += 1
            elif a == "-L" and i + 1 < len(args):
                label = args[i + 1]
                i += 1
            elif a == "-b" and i + 1 < len(args):
                block_size = int(args[i + 1])
                i += 1
            elif not a.startswith("-"):
                pos.append(a)
            i += 1
        if not pos:
            emit("error: make_ext4fs requires <out.img>\n")
            return 2
        out_img = _resolve_path(cwd, pos[0])
        src_dir = _resolve_path(cwd, pos[1]) if len(pos) > 1 else None
        out = native_backend.ext_make_fs(
            out_img,
            src_dir,
            size_bytes=size_bytes,
            mount_point=mount_point,
            file_contexts=file_contexts,
            fs_config=fs_config,
            block_size=block_size,
            sparse=sparse,
            label=label,
            progress=progress_cb,
        )
        emit(f"[OK] {out}\n")
        return 0

    # ext_cli / ext_tool
    if len(args) < 2 or args[0] in ("-h", "--help", "help"):
        emit(
            "ext_cli (native ext_cli in-process)\n"
            "Usage:\n"
            "  ext_cli --read <image.img>\n"
            "  ext_cli --unpack <image.img>\n"
            "  ext_cli --list <image.img>\n"
            "  ext_cli --usage <image.img>\n"
            "  ext_cli --cat <image.img> <internal_path>\n"
        )
        return 0
    cmd = args[0]
    img = _resolve_path(cwd, args[1])
    if cmd == "--read":
        _format_kv(native_backend.ext_inspect(img), emit)
        return 0
    if cmd == "--unpack":
        out_dir = native_backend.ext_unpack(img, progress=progress_cb)
        emit(f"[OK] Unpack finished: {out_dir}\n")
        return 0
    if cmd == "--usage":
        _format_kv(native_backend.ext_usage(img), emit)
        return 0
    if cmd == "--list":
        reader = native_backend.ext_open_reader(img)
        try:
            for node in reader.list("/"):
                emit(f"{node.get('perm', '')}\t{node.get('size', 0)}\t{node.get('path', '')}\n")
        finally:
            reader.close()
        return 0
    if cmd == "--cat" and len(args) >= 3:
        reader = native_backend.ext_open_reader(img)
        try:
            data = reader.read_bytes(args[2])
        finally:
            reader.close()
        emit(data.decode("utf-8", errors="replace"))
        if not data.endswith(b"\n"):
            emit("\n")
        return 0
    emit(f"error: unsupported ext_cli command: {' '.join(args)}\n")
    return 2


def _run_super(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    if tool == "lpmake":
        emit(
            "lpmake is not exposed in-process in rak_native yet; "
            "use `lpunpack` / `rak super list|extract` for super operations.\n"
        )
        return 2

    if not args or args[0] in ("-h", "--help", "help"):
        emit("lpunpack (native lp_tools): lpunpack [--info] [--slot=N] [--norm] [--unsparse] [-p NAME] <super.img> [out_dir]\n")
        return 0

    show_info = False
    normalize = False
    unsparse = False
    slot = 0
    partitions: list[str] = []
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--info":
            show_info = True
        elif a == "--norm":
            normalize = True
        elif a == "--unsparse":
            unsparse = True
        elif a.startswith("--slot="):
            slot = int(a.split("=", 1)[1])
        elif a in ("-S", "--slot") and i + 1 < len(args):
            slot = int(args[i + 1])
            i += 1
        elif a.startswith("--partition="):
            partitions.extend(p for p in a.split("=", 1)[1].split(",") if p)
        elif a in ("-p", "--partition") and i + 1 < len(args):
            partitions.extend(p for p in args[i + 1].split(",") if p)
            i += 1
        elif not a.startswith("-"):
            pos.append(a)
        i += 1

    if not pos:
        emit("error: lpunpack requires <super.img>\n")
        return 2

    img = _resolve_path(cwd, pos[0])
    out_dir = _resolve_path(cwd, pos[1]) if len(pos) > 1 else os.path.dirname(img)

    if unsparse:
        out_raw = native_backend.super_unsparse(img, out_dir if len(pos) > 1 else None,
                                              progress=lambda p, s: emit(f"[{p}%] {s}\n"))
        emit(f"[OK] Unsparsed: {out_raw}\n")
        return 0
    if show_info:
        _format_kv(native_backend.super_info(img, slot=slot), emit)
        return 0
    res = native_backend.super_extract(
        img,
        out_dir,
        partitions=partitions or None,
        slot=slot,
        normalize=normalize,
        progress=lambda p, s: emit(f"[{p}%] {s}\n"),
    )
    _format_kv(res, emit)
    return 0


def _run_magiskboot(args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    if not args or args[0] in ("-h", "--help", "help"):
        emit(
            "magiskboot (native magiskboot in-process)\n"
            "Commands: unpack [-n] [-h] <boot.img> | repack [-n] <orig.img> [out.img] | "
            "inspect <boot.img> | decompress <in> <out> | compress[=fmt] <in> <out> | cpio <cpio> <cmds...>\n"
        )
        return 0

    action = args[0]
    if action == "inspect" and len(args) >= 2:
        _format_kv(native_backend.boot_inspect(_resolve_path(cwd, args[1])), emit)
        return 0
    if action == "unpack":
        flags = args[1:-1]
        if len(args) < 2:
            emit("error: magiskboot unpack requires <boot.img>\n")
            return 2
        img = _resolve_path(cwd, args[-1])
        res = native_backend.boot_unpack(img, cwd, skip_decomp=("-n" in flags), dump_hdr=("-h" in flags))
        _format_kv(res, emit)
        return 0
    if action == "repack":
        flags = [a for a in args[1:] if a.startswith("-")]
        pos = [a for a in args[1:] if not a.startswith("-")]
        if not pos:
            emit("error: magiskboot repack requires <orig.img> [out.img]\n")
            return 2
        orig = _resolve_path(cwd, pos[0])
        out = _resolve_path(cwd, pos[1] if len(pos) > 1 else "new-boot.img")
        res = native_backend.boot_repack(cwd, orig, out, skip_comp=("-n" in flags))
        _format_kv(res, emit)
        return 0
    if action == "decompress" and len(args) >= 3:
        res = native_backend.boot_decompress(_resolve_path(cwd, args[1]), _resolve_path(cwd, args[2]))
        _format_kv(res, emit)
        return 0
    if action.startswith("compress") and len(args) >= 3:
        fmt = action.partition("=")[2] or "gzip"
        res = native_backend.boot_compress(_resolve_path(cwd, args[1]), _resolve_path(cwd, args[2]), fmt)
        _format_kv(res, emit)
        return 0
    if action == "cpio" and len(args) >= 3:
        res = native_backend.boot_cpio(_resolve_path(cwd, args[1]), args[2:])
        _format_kv(res, emit)
        return 0 if res.get("ok", True) else int(res.get("exit_code", 1))
    emit(f"error: unsupported magiskboot command: {' '.join(args)}\n")
    return 2


def _run_brotli(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    if not args or args[0] in ("-h", "--help", "help"):
        emit("brotli (native brotli-rs): brotli [-d] [-q 0..11] [-o OUT] <file> | unbrotli <file.br>\n")
        return 0

    decompress = tool in ("unbrotli", "brcat") or "-d" in args or "--decompress" in args
    quality = 1
    out_path = None
    src_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-d", "--decompress"):
            decompress = True
        elif a in ("-q", "--quality") and i + 1 < len(args):
            quality = int(args[i + 1])
            i += 1
        elif a.startswith("-q") and len(a) > 2:
            quality = int(a[2:])
        elif a in ("-o", "--output") and i + 1 < len(args):
            out_path = _resolve_path(cwd, args[i + 1])
            i += 1
        elif not a.startswith("-"):
            src_path = _resolve_path(cwd, a)
        i += 1

    if not src_path:
        emit("error: brotli requires an input file\n")
        return 2
    if decompress:
        if not out_path:
            out_path = src_path[:-3] if src_path.lower().endswith(".br") else src_path + ".out"
        _format_kv(native_backend.brotli_decompress_file(src_path, out_path), emit)
    else:
        if not out_path:
            out_path = src_path + ".br"
        _format_kv(native_backend.brotli_compress_file(src_path, out_path, quality=quality), emit)
    return 0


def _run_sparse(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    if len(args) < 2 or args[0] in ("-h", "--help", "help"):
        emit(f"{tool} (native sparse): {tool} <input.img> <output.img>\n")
        return 0
    src = _resolve_path(cwd, args[0])
    dst = _resolve_path(cwd, args[1])
    if tool == "img2simg":
        blk = int(args[2]) if len(args) > 2 else 4096
        out = native_backend.sparse_img2simg(src, dst, block_size=blk, progress=lambda p, s: emit(f"[{p}%] {s}\n"))
    else:
        out = native_backend.sparse_simg2img(src, dst, progress=lambda p, s: emit(f"[{p}%] {s}\n"))
    emit(f"[OK] {out}\n")
    return 0


def _run_sdat(tool: str, args: list[str], cwd: str, emit: Callable[[str], None]) -> int:
    from core import native_backend

    if not args or args[0] in ("-h", "--help", "help"):
        emit("sdat2img <transfer.list> <new.dat> [out.img] | img2sdat <image.img> [outdir]\n")
        return 0
    if tool in ("sdat2img", "sdatimg") and len(args) >= 2:
        tl = _resolve_path(cwd, args[0])
        dat = _resolve_path(cwd, args[1])
        out = _resolve_path(cwd, args[2]) if len(args) > 2 else os.path.join(cwd, "system.img")
        res = native_backend.sdat2img(tl, dat, out)
        emit(f"[OK] {res}\n")
        return 0
    if tool == "img2sdat":
        img = _resolve_path(cwd, args[0])
        outdir = _resolve_path(cwd, args[1]) if len(args) > 1 else os.path.dirname(img)
        res = native_backend.img2sdat(img, outdir)
        emit(f"[OK] {res}\n")
        return 0
    emit(f"error: invalid arguments for {tool}\n")
    return 2


def run_locked_terminal_command(
    tokens: list[str],
    *,
    cwd: str,
    emit: Callable[[str], None],
    cancel_token=None,
) -> int:
    """Execute a locked native command completely in-process and pipe output to `emit`."""
    if not tokens:
        return 0
    tool = normalize_command_token(tokens[0])
    args = [_strip_outer_quotes(t) for t in tokens[1:]]
    try:
        if tool in ("adb", "fastboot"):
            return _run_adb_or_fastboot(tool, args, cwd, emit, cancel_token=cancel_token)
        if tool in ("erofs", "erofs-rs", "erofs_rs", "dump.erofs", "extract.erofs", "mkfs.erofs", "fsck.erofs"):
            return _run_erofs(tool, args, cwd, emit)
        if tool in ("ext_cli", "ext_tool", "make_ext4fs"):
            return _run_ext(tool, args, cwd, emit)
        if tool in ("lpunpack", "lpmake"):
            return _run_super(tool, args, cwd, emit)
        if tool == "magiskboot":
            return _run_magiskboot(args, cwd, emit)
        if tool in ("brotli", "unbrotli", "brcat", "brotli_rs"):
            return _run_brotli(tool, args, cwd, emit)
        if tool in ("img2simg", "simg2img"):
            return _run_sparse(tool, args, cwd, emit)
        if tool in ("sdat2img", "img2sdat", "sdatimg"):
            return _run_sdat(tool, args, cwd, emit)
        emit(f"error: tool '{tool}' is not registered in native router\n")
        return 2
    except Exception as exc:
        emit(f"error: {exc}\n")
        return 1
