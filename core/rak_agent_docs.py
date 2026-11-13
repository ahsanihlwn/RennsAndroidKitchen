# SPDX-License-Identifier: MPL-2.0
# Refresh the kitchen CLI instructions in the user's project.

import os
import sys
import re
import stat
import subprocess
import tempfile
from typing import Optional, List, Tuple

# Ensure project root is in sys.path
_this_dir = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.dirname(_this_dir)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

# Canonical Delimiters
START_MARKER = "<!-- RAK_CLI_AUTO_GENERATED: START. DO NOT REMOVE THIS OR EDIT INSTRUCT -->"
END_MARKER = "<!-- RAK_CLI_AUTO_GENERATED: END -->"

AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "CODEX.md", "GEMINI.md")
DOCS_FORMAT_VERSION = 2

# Known partition directory names in Android ROMs
KNOWN_PARTITIONS = {
    "system", "vendor", "product", "system_ext", "odm", "oem", "mi_ext",
    "cust", "boot", "vendor_boot", "recovery", "dtbo", "vbmeta", "radio"
}


def is_start_delimiter(line: str) -> bool:
    payload=_delimiter_payload(line)
    return bool(payload is not None and (re.search(r'\bSTART\b',payload) or 'DONT REMOVE' in payload))


def is_end_delimiter(line: str) -> bool:
    payload=_delimiter_payload(line)
    return bool(payload is not None and re.search(r'\bEND\b',payload))


def _delimiter_payload(line):
    match=re.fullmatch(r'[ \t]{0,3}<!--[ \t]*RAK_CLI_AUTO_GENERATED\b([^<>]*?)-->[ \t]*',line.rstrip('\r\n'),re.IGNORECASE)
    return match.group(1).upper() if match else None


def _format_bytes(size_bytes: int) -> str:

    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def scan_project_partitions(cwd: str) -> List[dict]:
    """Snapshot relevant inputs; live inspect remains the operation authority."""
    if not cwd or not os.path.isdir(cwd):return []
    from core.rak_neobranch_view import project_root
    from core.workspace_files import WorkspaceFiles
    from core.file_detection import identify
    try:base=project_root(cwd)
    except ValueError:base=cwd
    files=WorkspaceFiles(base)
    try:entries=files.view.list_dir(cwd)
    except (OSError,ValueError):return []
    results=[]
    formats={'EROFS','EXT2','EXT3','EXT4','SUPER','SPARSE','PAYLOAD','BOOT','VNDRBOOT'}
    for entry in entries:
        name=entry['name'];logical=entry['path']
        if name.startswith(('.', '__')) or name in AGENT_FILES:continue
        try:
            identity=identify(files,logical);kind=identity['kind']
            physical,_=files.view.resolve(logical)
            folder=kind!='SYMLINK' and bool(physical and os.path.isdir(physical))
            if folder:
                android=any(files.view.resolve(os.path.join(logical,child))[0] for child in ('build.prop','default.prop'))
                if kind=='directory' and name.lower() not in KNOWN_PARTITIONS and not android and not os.path.isfile(os.path.join(physical,'apktool.yml')):continue
                category='dir';description='Filesystem directory' if kind in formats else 'Directory; inspect before choosing a rebuild format'
            elif kind=='SYMLINK':
                category='symlink';link=identity['symlink']
                behavior='directory' if link['is_dir'] else 'file' if link['is_file'] else 'unavailable'
                description=f"Symlink -> {link['target']} (behaves_as={behavior}, status={link['status']})"
            elif kind in formats or kind in ('archive','APK','BR','DAT','DAT.BR'):
                category='image' if kind in formats else 'file'
                description=f"{kind} ({_format_bytes(identity['size'])}; detected_by={identity['detected_by']})"
            else:continue
        except (OSError,ValueError):continue
        results.append({'name':name+'/' if folder else name,'kind':category,'format':kind,'desc':description,
                        'action':subprocess.list2cmdline(['rak','inspect',name,'--json'])})
    return results


def detect_dual_trees(cwd: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:

    # 1. Check live Main instance via rak_cli._main_ref or Main.instance
    main = None
    try:
        from core import rak_cli
        main = getattr(rak_cli, "_main_ref", None)
    except Exception:
        pass

    if not main:
        try:
            from main import Main
            main = getattr(Main, "instance", None)
        except Exception:
            pass

    if main:
        t1 = getattr(main.tree, "base", None) if hasattr(main, "tree") and main.tree else None
        t2 = getattr(main.tree2, "base", None) if hasattr(main, "tree2") and main.tree2 else None
        if t1 and os.path.isdir(t1):
            if t2 and os.path.isdir(t2):
                return (os.path.normpath(t1), os.path.normpath(t2))
            return (os.path.normpath(t1), None)

    # 2. Standalone / heuristic detection from cwd
    if cwd and os.path.isdir(cwd):
        norm_cwd = os.path.normpath(cwd)
        # Check if cwd is project root containing PORT1/PORT2 or port/base
        try:
            subs = {e.lower(): os.path.join(norm_cwd, e) for e in os.listdir(norm_cwd) if os.path.isdir(os.path.join(norm_cwd, e))}
            if "port1" in subs and "port2" in subs:
                return (subs["port1"], subs["port2"])
            if "port" in subs and ("base" in subs or "stock" in subs):
                return (subs["port"], subs.get("base") or subs.get("stock"))
        except Exception:
            pass

        # Check if cwd is itself Tree 1 and parent directory contains Tree 2
        parent = os.path.dirname(norm_cwd)
        if parent and os.path.isdir(parent):
            try:
                parent_subs = {e.lower(): os.path.join(parent, e) for e in os.listdir(parent) if os.path.isdir(os.path.join(parent, e))}
                base_name = os.path.basename(norm_cwd).lower()
                if base_name in ("port1", "port"):
                    t2 = parent_subs.get("port2") or parent_subs.get("base") or parent_subs.get("stock")
                    if t2:
                        return (norm_cwd, t2)
            except Exception:
                pass

    return (None, None)


def get_all_workspace_dirs(cwd: Optional[str] = None) -> List[str]:

    dirs = []

    def _add(d):
        if d and isinstance(d, str):
            norm = os.path.normpath(d)
            if os.path.isdir(norm) and norm not in dirs:
                dirs.append(norm)

    # 1. Check live Main instance
    main = None
    try:
        from core import rak_cli
        main = getattr(rak_cli, "_main_ref", None)
    except Exception:
        pass

    if not main:
        try:
            from main import Main
            main = getattr(Main, "instance", None)
        except Exception:
            pass

    if main:
        if hasattr(main, "active_tree") and main.active_tree and getattr(main.active_tree, "base", None):
            _add(main.active_tree.base)
        if hasattr(main, "tree") and main.tree and getattr(main.tree, "base", None):
            _add(main.tree.base)
        if hasattr(main, "tree2") and main.tree2 and getattr(main.tree2, "base", None):
            _add(main.tree2.base)
        if getattr(main, "project", None):
            _add(main.project)

    # 2. Check provided cwd
    if cwd:
        _add(cwd)
        try:
            from core.project_config import load_project_config
            cfg = load_project_config(cwd)
            trees = cfg.get("trees", {})
            for t_info in trees.values():
                target = t_info.get("target") if isinstance(t_info, dict) else t_info
                if target: _add(target)
        except Exception:
            pass

    # 3. If cwd was inside a tree directory, inspect parent for sibling trees
    if cwd and os.path.isdir(cwd) and os.path.basename(os.path.normpath(cwd)).lower() in ('port','port1','port2','base','stock','rom1','rom2'):
        parent = os.path.dirname(os.path.normpath(cwd))
        if parent and os.path.isdir(parent):
            try:
                for entry in os.listdir(parent):
                    sibling = os.path.join(parent, entry)
                    if os.path.isdir(sibling) and entry.lower() in ("port", "port1", "port2", "base", "stock", "rom1", "rom2"):
                        _add(sibling)
            except Exception:
                pass

    # 4. Fallback to os.getcwd()
    if not dirs:
        _add(os.getcwd())

    return dirs


def generate_dynamic_instructions(cwd: str) -> str:
    norm_cwd = os.path.normpath(cwd) if cwd else os.getcwd()
    t1_base, t2_base = detect_dual_trees(norm_cwd)
    physical_checkouts = []
    try:
        from core.rak_neobranch_view import project_root
        from main_ext.class_NeoBranchManager import NeoBranchManager
        base = project_root(norm_cwd)
        manager = NeoBranchManager(base)
        store=os.path.join(base,'neobranch')
        for entry in os.scandir(store) if os.path.isdir(store) else ():
            if entry.is_dir(follow_symlinks=False):
                # NeoBranchManager APIs are keyed by the partition basename.
                if manager.is_physical_checkout(entry.name):
                    active = manager.get_active_branch(entry.name)
                    physical_checkouts.append((entry.name, (active or {}).get("name")))
    except (ImportError, OSError, ValueError, AttributeError, TypeError):
        physical_checkouts = []
    physical_mode = bool(physical_checkouts)
    legacy_sparse = False
    try:
        from main_ext.class_NeoBranchManager import NeoBranchManager
        base = project_root(norm_cwd)
        manager = NeoBranchManager(base)
        store=os.path.join(base,'neobranch')
        for entry in os.scandir(store) if os.path.isdir(store) else ():
            if entry.is_dir(follow_symlinks=False):
                if manager.get_active_branch(entry.name) and not manager.is_physical_checkout(entry.name):
                    legacy_sparse = True
                    break
    except (ImportError, OSError, ValueError, AttributeError, TypeError, NameError):
        legacy_sparse = False

    # Native agent filesystem tools are the normal path. Keep the large legacy
    # command catalog only when an active sparse branch requires its logical view.
    if not legacy_sparse:
        compact = [
            START_MARKER,
            "# Renns Android Kitchen (RAK) — Workspace Guide",
            "",
            f"Instruction format: RAK CLI contract v{DOCS_FORMAT_VERSION}; shared by {', '.join(AGENT_FILES)}.",
            "Agent memory: keep notes, learned facts, and user rules outside this managed block.",
            "Communication & Debugging: Whenever you discover, observe, or inspect anything relevant in the code, logs, or workspace during debugging or investigation, communicate it immediately to the user! Never stay silent while running commands and only speak at the end as a finish—that is dangerous. Provide clear, proactive updates on what you found and what you are doing. Always adapt your communication and reply in whatever language the user is speaking (e.g. Indonesian or English).",
            "Use native Windows agent tools (Read, Write, Edit, shell, and Python) for ordinary project files. Do not route normal file work through RAK or WSL.",
            "Use `rak` for Kitchen operations: inspect, unpack/repack, archive and payload extraction, BR/DAT conversion, APK tools, virtual mounts, branch create/switch, and Android mode/UID/GID/SELinux metadata.",
            "",
            f"- Workspace: `{norm_cwd}`",
        ]
        if t1_base: compact.append(f"- Tree 1: `{t1_base}`")
        if t2_base: compact.append(f"- Tree 2: `{t2_base}`")
        for partition, branch in physical_checkouts:
            compact.append(f"- Physical NeoBranch: `{partition}` on `{branch or 'Source'}`; the live partition folder and project config are authoritative.")
        compact.extend([
            "",
            "Use `rak doctor --json` and `rak capabilities --json` to check available Kitchen tools and live-only UI/mount functions. Use `rak inspect PATH --json` and follow the returned operation argv exactly before handling unfamiliar ROM inputs.",
            "For a physical NeoBranch checkout, ordinary edits apply directly to its live folder. Use `rak branch current <partition>` and `rak branch switch <partition> <name>` to inspect or change the selected branch. Source checkouts without a physical branch use ordinary native file tools as well.",
            "Avoid concurrent external writes while switching branches because checkout changes the live partition contents. Android `!<symlink>` placeholders are regular files on Windows: use `rak inspect`/`rak readlink` to identify and resolve them; native `cd` does not traverse them.",
            "Branch commands wait for their actual task result by default. In a live session, `rak branch switch <partition> <name> --no-wait` returns a task_id; inspect that exact task with `rak activity show <task_id> --json` until it completes. `--timeout SECONDS` sets an explicit wait limit; a timeout does not cancel work.",
            "Mounted images are virtual; use `rak img` for image contents and `rak mount`/`rak unmount` for Kitchen mounts. Run `rak repack <partition> -o output.img` to build a partition image.",
            "Android metadata: `rak stat PATH --json` inspects labels/modes; `rak fs context PATH u:object_r:vendor_file:s0` changes one exact label; `rak fs permission PATH 0644` changes its Android mode. These are separate from Windows ACLs. Ordinary native copies do not infer donor labels: copy the matching config rules or set the required metadata explicitly. Kitchen metadata parsers currently reject whitespace in paths.",
            "Inspect header_magic before selecting tools. BR/DAT/payload are not mountable filesystems: decode/extract a supported partition IMG first. Repack a filesystem folder to IMG; use an explicit .zip/.7z destination only for an archive. Keep original transfer.list/patch.dat with DAT outputs.",
            "For an open unsaved Monaco buffer, use `rak ui read` then revision-checked `rak ui edit` with Monaco acknowledgment, or save/close the buffer before an external disk edit. Native tools read on-disk bytes. Tool completion comes from actual task results/events, not elapsed time.",
            "Keep agent notes outside this managed block. `rak doc` refreshes the managed instructions in AGENTS.md, CLAUDE.md, CODEX.md, and GEMINI.md.",
            END_MARKER,
        ])
        return "\n".join(compact)
    file_work_rule = (
        "2. **Use native agent file tools for ordinary work** (Read, Write, Edit, shell, or Python). In a physical NeoBranch checkout, the live partition folder and project config are authoritative; there is no sparse overlay or tombstone layer."
        if physical_mode else
        "2. **Use the unified `rak` CLI for Kitchen operations and files in legacy sparse NeoBranch views.** For ordinary files outside those views, use native agent file tools."
    )
    view_intro = ("For physical checkouts, the live partition folder and project config are authoritative; use the active branch shown above. For legacy sparse views, use the logical partition path shown by RAK." if physical_mode else "For files inside an unpacked ROM project, use the logical partition path shown by RAK. The active NeoBranch may contain files absent from the source folder.")
    native_view_rule = ("- Use native agent Read/Write/Edit and shell/Python tools for ordinary file inspection and edits in physical checkouts. Use `rak` for Kitchen tools, branch switching, mount operations, and Android filesystem metadata. Legacy sparse NeoBranch paths still require RAK's logical file commands." if physical_mode else "- Do not use host file tools for files in legacy sparse NeoBranch views; they bypass branch additions and deletions. Use the RAK logical file commands below.")
    mutation_rule = ("Ordinary file mutations in physical checkouts use native agent file tools against the live checkout. Use managed `rak` commands for Kitchen operations, branch switching, mounts, and Android metadata; legacy sparse views require managed RAK mutations." if physical_mode else "All project-tree file mutations in legacy sparse NeoBranch views must use managed `rak` commands (Unix-style cp/mv/rm/mkdir/touch/ln/chmod/sed or compatible `rak fs` actions). They validate logical paths, use the GUI task pipeline, and preserve NeoBranch and filesystem metadata.")

    lines = [
        START_MARKER,
        "# 🤖 Renns Android Kitchen (RAK) — Environment & Command Router",
        "",
        "You are operating in an Android ROM / partition project managed by **Renns Android Kitchen (RAK)**.",
        f"- **Instruction format:** RAK CLI contract v{DOCS_FORMAT_VERSION}; shared by {', '.join(AGENT_FILES)}.",
        "- **Agent memory:** Keep notes, learned facts and user rules outside the RAK_CLI_AUTO_GENERATED START/END block. RAK refreshes only its managed block.",
        f"- **Current Workspace:** `{norm_cwd}`",
    ]

    if t1_base:
        lines.append(f"- **Tree 1 (Port ROM / Active Build):** `{t1_base}`")
    if t2_base:
        lines.append(f"- **Tree 2 (Base ROM / Reference Donor):** `{t2_base}`")
    for partition, branch in physical_checkouts:
        lines.append(f"- **Physical NeoBranch:** `{partition}` is checked out on `{branch or 'unknown'}`; its live folder and project config are authoritative.")

    lines.extend([
        "- **Kitchen IPC:** Commands use the matching live Kitchen session when available; supported standalone commands run headlessly. UI context, events and virtual mount require a live session.",
        "",
        "## ⚠️ CRITICAL RULES FOR AI AGENTS",
        "1. **DO NOT invoke raw binaries directly** (never execute `extract.erofs.exe`, `make_ext4fs.exe`, `lpunpack.exe`, `simg2img.exe` manually).",
        "2. **Communication & Debugging:** Whenever you discover, observe, or inspect anything relevant in the code, logs, or workspace during debugging or investigation, communicate it immediately to the user! Never stay silent while running commands and only speak at the end when finished—that is dangerous. Provide clear, proactive updates on what you found and what you are doing along the way. Always adapt your communication and reply in whatever language the user is speaking (e.g. Indonesian or English).",
        file_work_rule,
        "   - RAK's primary workflow is filesystem IMG -> unpacked folder plus config metadata -> edit -> repacked IMG. Default `rak repack system` produces system_new.img using the original EXT/EROFS engine, never ZIP.",
        "   - `.img.br` decodes to IMG; `.new.dat` and `.new.dat.br` convert to IMG using the matching transfer.list; payload.bin and SUPER extract partition IMG files. These containers are not filesystem mount targets.",
        "   - Mount only a supported EXT/EROFS IMG (including a supported sparse filesystem image read-only). For BR/DAT/payload/SUPER, first obtain a partition IMG and inspect its actual type. Default sparse unpack converts to raw in place; inspect again before the next operation.",
        "   - Filesystem outputs: use `rak repack system -o rebuilt.img`; other supported destination suffixes are .img.br, .new.dat and .new.dat.br. DAT export retains its matching transfer.list and patch.dat. Boot/vendor_boot folders use the boot/CPIO rebuild engine and output IMG.",
        "   - Prefer familiar Unix operands: `rak ls -la system`, `rak grep -Rni ro.build system`, `rak cat system/build.prop`.",
        "   - Run `rak doctor --json` to see actual installed tools, active branches, and functions that require a live session. Standalone capabilities report their actual scope; they do not invent GUI buffers or activity.",
        "   - Verify your work with `rak stat PATH --json` (Android mode/UID/GID and exact SELinux entry or candidate rules), `rak sha256sum FILE`, `rak cmp FILE1 FILE2`, and `rak diff -ur DIR1 DIR2`. cmp/diff status 1 means differences; it is not a crash. Status 2 means a read/usage/tool failure. Hashes and comparisons read the current on-disk branch, not unsaved editor text.",
        "   - `rak find PATH -name GLOB -type f -maxdepth N`, `rak head -n N FILE`, `rak tail -n N FILE`, and `rak wc -lwcm FILE` inspect large projects. `rak cat` does not silently truncate text. For large filename sets, use `rak find PATH -type f -print0 | rak wc -l --files0-from=-` in a native terminal, or store a NUL-separated UTF-8 list and use `wc --files0-from LIST`. Filenames in lists still resolve through the active branch.",
        "   - Use `rak cp [-r|-a] <source> <destination>`, `rak mv <source> <destination>`, `rak rm [-rf] <path>`, `rak mkdir -p <path>`, `rak touch <path>`, `rak ln -s <target> <link>`, and `rak chmod 0644 <path>`.",
        "   - GNU sed is the text engine, including line addresses, multiple commands and hold-space scripts. Script filesystem I/O and shell execution are disabled so writes stay in the managed editor service. Edit text with `rak sed -i 's/old/new/g' <logical-path>` (or -E for extended regex). This reads the current revision and uses the same guarded editor/background edit service. Open buffers wait for Monaco acknowledgment and autosave; unopened files preserve BOM/encoding/EOL and write into the active branch.",
        "   - sed handles unopened text files above the UI read limit through a local revision-checked transaction. Open editor buffers retain their Monaco checks; a newly opened buffer or changed disk revision aborts a background save.",
        "   - For arbitrary text rewrites, use `rak ui read <logical-path>` and `rak ui edit --from request.json` with path, revision, content. Wait for applied and saved=true. Revision identity includes the active branch, even when both branches still read identical Source bytes.",
        "   - Smart Auto-Detect: `rak inspect PATH --json` reads the existing header_magic detector live for Android images. ZIP/APK use ZIP structure; BR/DAT and other archive suffixes can be filename hints, explicitly labeled detected_by=filename_hint. Do not treat an extension hint as proven binary format.",
        "   - Smart Auto-Repack: Use `rak repack <folder>` for partition images or managed APK mirrors; `rak repack <folder> -o output.zip` builds an archive (extension selects ZIP/7z/TAR).",
        "   - `rak` routes directly to the Kitchen engine & GUI task queue via local IPC.",
        "   - GUI state, loading bars, and the FileTree will refresh automatically.",
        "3. Run `rak inspect <logical-path> --json` for a file or folder you do not recognize. Use operations[].argv exactly; do not guess engine action names. `rak list` shows the canonical commands; `rak list --all` is the compatibility/advanced catalog.",
        "   Android !<symlink> placeholders and native symlinks retain kind=SYMLINK plus target, representation, resolved_path, status and behaves_as. `rak readlink LINK` reads the stored target; `rak readlink -e LINK` resolves its existing logical target. `rak inspect -L LINK --json` inspects that target. Folder links accept `rak ls LINK` and child paths; listings explicitly mark symlink:directory and report the traversed link. They follow the target's active NeoBranch. Raw host cd cannot traverse a regular Android placeholder. Copy/archive preserve placeholder bytes, and rm LINK removes the link itself.",
        "   Choose `unpack` to extract/decompile, `mount` to browse an image virtually, and `repack` to rebuild. Automatic detection chooses the format engine. Ordinary file work uses the Unix commands above.",
        "   `rak archive list ROM.zip --json`; `rak archive extract ROM.7z --to extracted --path system.img`; `rak archive pack folder --to output.zip`.",
        "   `rak payload list OTA.zip --json`; `rak payload extract payload.bin --to images --partition system --partition vendor --json` (or `--all`). Super uses the same list/extract syntax.",
        "   `rak unpack payload.bin` extracts all partitions without opening a viewer. `rak unpack app.apk` decompiles; `rak repack app.apk` rebuilds its managed apktool mirror.",
        "   `rak apk decompile|build|build-sign|build-keeporig|sign|unsign app.apk`; `rak br compress|decompress file`; `rak dat to-image|to-folder system.new.dat.br`.",
        "   Extraction and archive packing refuse existing output files. Choose another destination or explicitly remove intended outputs with `rak rm` before retrying. Extract outside active partitions. Archive packing reads the merged logical NeoBranch view.",
        "   For long-running tools in a live Kitchen session, append `--no-wait --json`, retain task_id, and inspect `rak activity show <task_id> --json` plus events until that exact task finishes. A dispatched or timed-out request is not completion; do not automatically retry it. `--timeout <seconds>` changes synchronous waiting.",
        "   Start with `rak capabilities --json` and `rak context --content --json` to discover live UI state and APIs.",
        "   `context.project` is the Kitchen container, not a new-file destination. Use `context.workspace.default_output_directory` and tree_roots for project work; never create files beside the opened tree by default.",
        "   Use `rak ui read <logical-path>` for the current Monaco buffer and its revision, including unsaved changes.",
        "   Read `rak ui settings` first. Only open/reveal editors or control project UI when agent.ui_triggers is enabled by the user.",
        "   Use `rak ui controls` to discover registered controls, `ui trigger <id>` to activate and `ui scroll <id>` to reveal/highlight one.",
        "   Never change project Settings or their JSON. Ask the user and use `rak ui guide agent.ui_triggers` (or agent.autosave/agent.highlight) to point to its switch.",
        "   Submit `rak ui ask --from request.json` to ask the user in Kitchen; wait for that request ID with `rak ui wait <id> --wait 30`.",
        "   `rak ui edit --from request.json` applies a revision-checked undoable buffer edit. `ui review` shows a diff for user review. Successful edits autosave when enabled in project settings.",
        "   `ui read/edit` also support unopened text files without opening an editor: preserve their disk revision and wait for the background edit's actual result.",
        "   File edits do not require UI access. Preferred: write replacement UTF-8 text to workspace.scratch_directory, then run `rak ui edit <path> --revision <revision> --content-file <replacement.txt> --wait 30`.",
        "   Never embed file contents in shell commands. `--from` expects a JSON FILE PATH, never inline JSON. Claude Write requires file_path and content; missing arguments are tool validation errors, not UI permission failures.",
        "   Ask/notify payload: {title,message,choices?}. Edit/review payload: {path,revision,content,title?}. Write UTF-8 JSON; preserve the revision returned by ui read.",
        "   Wait for events with `rak events --since <cursor> --wait 30 --json`, or stream with `rak events --stream`. Track session_id and refresh context after an event gap/session change.",
        "   Request states pending/applying are not completion. Read ui response/wait until answered/applied/cancelled/conflict, then act on that actual result.",
        "   Receive UI requests with `rak inbox next --agent <your-name> --wait 30`. This atomically claims one message. If serving the inbox and no message is available, wait again.",
        "   Read fresh `rak context --json` after claiming: the attached selection/buffer is a frozen user snapshot. Keep the returned message.id and private claim token.",
        "   Report work with `rak inbox start <id> --agent <your-name> --claim <token>` and `inbox progress ... --from status.json` (JSON message field).",
        "   Return actual results with `rak inbox reply|fail <id> --agent <your-name> --claim <token> --from result.json` (JSON answer field and optional paths array). Do not report a failed or merely enqueued job as completed.",
        "   Check `rak inbox get <id>` before more operations. When cancel_requested is true, stop safely and use `rak inbox ack-cancel <id> --agent <your-name> --claim <token>`. Never force-kill or automatically retry a flash.",
        "   Only a cooperating inbox consumer receives UI work while waiting; the API does not inject prompts into an arbitrary idle/busy agent terminal.",
        "4. Run `rak badge <path>` to check filesystem format of partition images.",
        "5. Run `rak refresh` to force the GUI FileTree to synchronize with disk.",
        "6. **VIRTUAL MOUNT vs PHYSICAL UNPACK**:",
        "   - EROFS and EXT4 images can be virtually mounted in GUI (`rak mount <file>`) or directly operated on via CLI (`rak img ...`).",
        "   - Virtual mount does NOT create a physical folder on disk. Do NOT look for an unpacked folder unless you ran `rak unpack <file>`.",
        "   - You can read and write files directly inside the image without unpacking using `rak img cat` and `rak img write`!",
        "",
        "## 💾 Virtual In-Image Operations (`rak img`) — Zero Disk Space!",
        "For EXT2/3/4 and EROFS images, you can inspect, read, and edit files in-place without unpacking:",
        "- `rak img ls <file.img> [subpath] [-l] [-r]` : List directory contents, permissions, and SELinux contexts.",
        "- `rak img cat <file.img> <internal_path>` : Dump file contents directly to stdout.",
        "- `rak img stat <file.img> <internal_path>` : Show detailed node metadata (mode, inode, SELinux).",
        "- `rak img extract <file.img> <internal_path> [dest]` : Extract single file or subtree (alias: `pull`).",
        "- `rak img write <file.img> <internal_path> <local_file>` : Write/edit file directly inside image in-place (alias: `push`).",
        "- `rak img perm <file.img> <internal_path> <octal>` : Change permission mode (e.g. 0644, 0755).",
        "- `rak img selinux <file.img> <internal_path> <context>` : Change SELinux context.",
        "- `rak img rm <file.img> <internal_path> [-r]` : Delete file or directory inside image.",
        "- `rak img info <file.img>` : Show filesystem & block usage stats (alias: `usage`).",
        "- Image reads resolve the active NeoBranch version of images inside a partition tree. The root-level source image for an active partition is blocked until repack; use `rak fs` on the unpacked tree.",
        "- Direct image writes are blocked when that image belongs to an active NeoBranch; use managed `rak fs` actions for unpacked files.",
        "- Extract image contents outside an active partition folder, then import with `rak fs`.",
        "- `rak mount <file.img>` : Mount image virtually into Kitchen GUI FileTree.",
        "- `rak unmount <file.img>` : Unmount virtual image from GUI.",
        "- `rak mounted` : List images currently mounted in GUI.",
        "",
        "## 🌿 NeoBranch Management (`rak branch`) — Non-Destructive Partition Branching",
        "NeoBranch provides Git-style non-destructive versioning per filesystem partition.",
        "Edits, additions, and deletions are saved into the active branch overlay while keeping the original firmware snapshot intact.",
        "- `rak branch create <partition_folder> <branch_name> [color]` : Create and activate a new branch for a partition (e.g. `rak branch create vendor my-feature blue`). Colors: blue, green, red, yellow, orange, purple, pink.",
        "- `rak branch switch <partition_folder> <branch_name>` : Switch between branches (alias: `checkout`). To switch back to untouched original source, use `rak branch switch <partition_folder> source`.",
        "- `rak branch list [partition_folder]` : List all branches and show which branch is currently active.",
        "- `rak branch current <partition_folder>` : Check the currently active branch (alias: `status`).",
        "- `rak branch delete <partition_folder> <branch_name> --yes` : Permanently delete a branch overlay.",
        "",
        "## Unix File Commands on the Active Project View",
        view_intro,
        "- `rak ls [folder]` (alias: `rak fs ls`) : List the merged active view (source files plus branch additions and replacements, minus branch deletions). The logical view includes branch additions and excludes branch deletions.",
        "- `rak fs ls <folder> --branch-only` : Show only entries physically present in the active branch overlay.",
        "- `rak cat <file>` (alias: `rak fs cat`) : Read the active version of a text file, including branch-only files.",
        "- `rak grep [-RniEF] <regex> <path>` : Search visible files in the active view.",
        "- In the built-in AI tools, use `list_dir`, `inspect`, `read_file`, and `grep_search`; these use the same active NeoBranch view.",
        native_view_rule,
        "- Check `rak branch current <partition_folder>` before work when the intended branch is unclear.",
        "",
        "## Managed File Changes",
        "- `rak cp [-r|-a] SOURCE DEST` copies files or merges directory trees. `-n` skips existing files, including inside a merged directory. `cp -a source/. destination` copies directory contents.",
        "- `rak mv SOURCE DEST`, `rak rm [-rf] PATH`, `rak mkdir -p PATH`, `rak touch PATH`, `rak ln -s TARGET LINK`, `rak chmod OCTAL PATH` use conventional operands and the active branch.",
        "- Use a second destination name for a backup: `rak cp system/build.prop system/build.prop.bak`. Use `rak sed -i SCRIPT FILE` or revision-checked `rak ui edit` for text.",
        "Before starting another operation, read `rak activity status --json` for live running/queued jobs, Source recovery state, and Android flash progress. `rak activity --json --failed` lists failed UI/CLI jobs; `rak activity show <id> --json` preserves full output. `rak activity clear --yes` clears completed history without stopping active work. Activity requires a live Kitchen session and never invents an empty standalone session.",
        mutation_rule,
        "- `rak fs make-filesystem <partition-folder> --type <erofs|ext4> [--mount-point /path] --yes` : Initialize a new partition filesystem.",
        "- Mounted images are not project-tree folders: use `rak img` for their contents.",
        "- Boot/vendor_boot ramdisks are CPIO: mode/UID/GID live in CPIO entries, but CPIO has no per-file SELinux label. RAK currently reapplies mode overrides from `config/<boot>_fs_config`; edited UID/GID fields are not reapplied. Never create `config/<boot>_file_contexts` to label a boot ramdisk; RAK rejects that ineffective metadata at repack. Find the policy actually loaded in this boot mode: recovery may carry `*_file_contexts` and `sepolicy` in the ramdisk root, while normal boot may use mounted system/vendor policy. On vendor_boot v4, RAK edits an existing policy file in its original fragment but rejects new or duplicate-fragment policy paths whose target cannot be inferred from the merged tree. For a file that survives until SELinux loads, inspect the applicable `restorecon` path before claiming it has a label.",
        "- `rak compare` reads each active view; save JSON or `.renxec` reports outside active partition folders.",
        "- `rak automation <file.renxec> <fs>` applies RDT to the selected partition's active NeoBranch view; without an active branch it writes to source.",
        "- `rak policy plan <fs> [--apply]` reads the active view of supported partitions. `--apply` saves verified NeoCat proposals in the target branch's cache when that is the only active branch.",
        "- Other legacy conversion, unpack, and APK actions exposed by `rak` are rejected for targets inside an active branch until their handlers use the logical view.",
        "",
    ])

    # Dual Tree section if Tree 2 exists
    if t1_base and t2_base:
        lines.extend([
            "## 🌲 Dual-Tree Workspace Awareness (Port vs Base/Stock)",
            "This Kitchen project contains two synchronized ROM trees:",
            f"- **Tree 1 (Port ROM / Target):** `{t1_base}` — This is your active target workspace where modifications and repacks take place.",
            f"- **Tree 2 (Base ROM / Reference Donor):** `{t2_base}` — This is the original reference / stock ROM.",
            "",
            "### 🔄 Cross-Tree Operations (Never Blind to Tree 2!)",
            f"1. **Inspect / Reference Tree 2 Freely:** When working in Tree 1, inspect Tree 2 at `{t2_base}` with `rak ls`, `rak cat`, or `rak grep` and explicit Tree 2 paths. This keeps reads on Tree 2's active NeoBranch view.",
            "2. **Compare Partitions Across Trees:**",
            "   Use `rak compare <tree1_dir> <tree2_dir>` to inspect each tree's active NeoBranch view, including branch-only files, deletions, permissions, and SELinux contexts.",
            f"   - Example: `rak compare \"{t1_base}\\system\" \"{t2_base}\\system\"`",
            f"   - Example: `rak compare \"{t1_base}\\vendor\" \"{t2_base}\\vendor\"`",
            "3. **Repack Modified Partitions:**",
            f"   Repack Tree 1 partitions using `rak repack <folder>` (e.g. `rak repack \"{t1_base}\\system\"` or `rak repack system` when inside Tree 1).",
            "",
        ])

        # Partitions for Tree 1
        t1_parts = scan_project_partitions(t1_base)
        lines.append("## 📦 Detected Partitions in Tree 1 (Port / Target)")
        if t1_parts:
            for p in t1_parts:
                if p["action"]:
                    lines.append(f"- `{p['name']}` ({p['desc']}) ➔ Command: `{p['action']}`")
                else:
                    lines.append(f"- `{p['name']}` ({p['desc']})")
        else:
            lines.append("- *(No raw partition images or unpacked folders detected in Tree 1 root)*")

        # Partitions for Tree 2
        t2_parts = scan_project_partitions(t2_base)
        lines.append("")
        lines.append("## 📦 Detected Partitions in Tree 2 (Base / Reference)")
        if t2_parts:
            for p in t2_parts:
                lines.append(f"- `{p['name']}` ({p['desc']}) [Reference only]")
        else:
            lines.append("- *(No raw partition images or unpacked folders detected in Tree 2 root)*")

    else:
        # Single Tree / Current Directory
        target_scan_dir = t1_base or norm_cwd
        partitions = scan_project_partitions(target_scan_dir)
        lines.append("## 📦 Detected Partitions in this Project")
        if partitions:
            for p in partitions:
                if p["action"]:
                    lines.append(f"- `{p['name']}` ({p['desc']}) ➔ Command: `{p['action']}`")
                else:
                    lines.append(f"- `{p['name']}` ({p['desc']})")
        else:
            lines.append("- *(No raw partition images or unpacked partition folders detected in root)*")
            lines.append("- Use `rak inspect PATH --json` for IMG, archives, APK, BR/DAT, payloads, directories or symlinks, including files with unfamiliar names.")

    from core.cli_contract import PRIMARY
    lines.extend(['', '## Canonical commands (shared with rak list and capabilities)', '```text'])
    for commands in PRIMARY.values():
        lines.extend('rak '+command for command in commands)
    lines.extend(['```', 'Detected entries above are a snapshot. Refresh live type, branch and supported operations with inspect before acting.'])

    lines.extend([
        "",
        "## 🛠️ Unified `rak` Command Quick Reference",
        "- `rak capabilities --json` / `rak context --content --json` : Discover live Kitchen capabilities, selected files/text, unsaved editor buffers, branches, mounts and devices.",
        "- `rak ui --help` : Open/reveal/read UI documents, ask the user, notify, apply or review revision-checked editor changes, and read request responses.",
        "- `rak inbox --help` : Receive user work from UI, claim it once, report progress/results, and acknowledge stop requests. History and queued work persist per project.",
        "- `rak events --since <cursor> --wait 30 --json` / `rak events --stream` : Receive condition-driven events for documents, selections, branches, devices and tasks.",
        "- `rak activity [--json] [--failed] [--limit N]` : Read the same activity log as the Kitchen UI, including live flash progress.",
        "- `rak activity status --json` / `rak activity show <id> --json` : Inspect live tasks or full output for one task.",
        "- `rak activity clear --yes` : Clear completed history in the UI; active tasks keep running.",
        "- `rak unpack <file>` : Auto-detect and unpack partition image, APK, payload or archive (EROFS, EXT4, SUPER, SPARSE, BOOT, PAYLOAD, etc.).",
        "- `rak repack <folder>` : Smart auto-repack directory to partition image.",
        "- `rak img ls <file.img> [subpath]` : List files, permissions & SELinux inside image without unpack.",
        "- `rak img cat <file.img> <path>` : Read file content directly from image to stdout.",
        "- `rak img write <file.img> <path> <file>` : Write/edit file directly into image in-place.",
        "- `rak img extract <file.img> <path> [dest]` : Extract single file or subtree from image.",
        "- `rak ls [-ladR] [folder]` / `rak cat <file>` / `rak grep -Rn <regex> <path>` : Read active NeoBranch project files. Directory symlinks are marked explicitly, including when traversed.",
        "- `rak fs make-filesystem <partition-folder> --type <erofs|ext4> --yes` : Create partition filesystem.",
        "- `rak mount <file.img>` : Mount image virtually into Kitchen GUI FileTree.",
        "- `rak unmount <file.img>` : Unmount virtual image from GUI.",
        "- `rak compare <dir_a> <dir_b>` : Compare the active NeoBranch view of each partition (gated by developer server).",
        "- `rak automation <file.renxec> [fs]` : Execute automation on the selected partition's active NeoBranch or source (gated by developer server). Specify [fs] when the project has multiple partitions.",
        "- `rak inspect [-L] PATH --json` : Return live header_magic/filename evidence, symlink identity/status, branch/origin and exact valid operation argv. `-L` inspects a link target. `rak readlink [-f|-e] LINK` reads or resolves its target. `rak list` lists primary commands; `rak list --all` lists compatibility actions.",
        "- `rak refresh` : Synchronize Kitchen GUI with filesystem.",
        "- `rak badge <path>` : Inspect live filesystem type badge.",
        "- `rak batch unpack <path1> | <path2>` : Run batch auto-unpack on multiple files.",
        "- `rak batch repack <path1> | <path2>` : Run batch auto-repack on multiple folders.",
        "- `rak doc` : Refresh dynamic agent documentation in current folder.",
        END_MARKER,
    ])

    return "\n".join(lines)


def update_agent_file(target_file_path: str, cwd: str, *, block=None) -> bool:
    """Replace managed sections atomically, preserving all text outside them."""
    new_block=generate_dynamic_instructions(cwd) if block is None else block
    temporary=None
    try:
        content='';mode=None;original_bytes=None
        if os.path.lexists(target_file_path):
            if os.path.islink(target_file_path):raise ValueError('Instruction file is a symlink; refusing to replace its link or write outside the workspace')
            with open(target_file_path,'rb') as stream:original_bytes=stream.read()
            content=original_bytes.decode('utf-8')
            mode=stat.S_IMODE(os.stat(target_file_path).st_mode)
        bom='\ufeff' if content.startswith('\ufeff') else ''
        content=content[len(bom):]
        newline='\r\n' if '\r\n' in content else '\n'
        replacement=newline.join(new_block.splitlines())+newline
        output=[];inside=False;inserted=False;fence=None
        for line in content.splitlines(keepends=True):
            if not inside:
                fenced=re.match(r'^[ \t]{0,3}(`{3,}|~{3,})(.*)$',line.rstrip('\r\n'))
                if fenced:
                    token,tail=fenced.groups()
                    if fence is None:fence=token
                    elif token[0]==fence[0] and len(token)>=len(fence) and not tail.strip():fence=None
                    output.append(line);continue
                if fence is not None:output.append(line);continue
            if is_start_delimiter(line):
                if inside:raise ValueError('Nested managed instruction markers; file left unchanged')
                inside=True
                if not inserted:output.append(replacement);inserted=True
            elif is_end_delimiter(line):
                if not inside:raise ValueError('Unmatched managed instruction end marker; file left unchanged')
                inside=False
            elif not inside:output.append(line)
        if inside:raise ValueError('Managed instruction end marker is missing; file left unchanged')
        final=bom+(''.join(output) if inserted else replacement+(newline+content if content else ''))
        original=bom+content
        if os.path.exists(target_file_path) and final==original:return True
        descriptor,temporary=tempfile.mkstemp(prefix='.rak-agent-docs-',dir=os.path.dirname(os.path.abspath(target_file_path)))
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='') as stream:
            stream.write(final);stream.flush();os.fsync(stream.fileno())
        if mode is not None:os.chmod(temporary,mode)
        current_bytes=None
        if os.path.lexists(target_file_path):
            if os.path.islink(target_file_path):raise ValueError('Instruction file became a symlink; retry synchronization')
            with open(target_file_path,'rb') as stream:current_bytes=stream.read()
        if current_bytes!=original_bytes:raise ValueError('Instruction file changed during synchronization; retry to preserve the latest agent notes')
        os.replace(temporary,target_file_path);temporary=None
        return True
    except (OSError,UnicodeError,ValueError) as error:
        print(f"[RAK Agent Docs] Error updating {target_file_path}: {error}")
        return False
    finally:
        if temporary and os.path.exists(temporary):os.remove(temporary)


def update_all_agent_files(cwd: Optional[str] = None) -> List[str]:
    updated=[];failed=[]
    for directory in get_all_workspace_dirs(cwd):
        block=generate_dynamic_instructions(directory)
        for filename in AGENT_FILES:
            path=os.path.join(directory,filename)
            if update_agent_file(path,directory,block=block):updated.append(os.path.normpath(path))
            else:failed.append(path)
    if failed:
        from core.command_context import OperationError
        raise OperationError('Agent instructions could not be synchronized: '+', '.join(failed)+
                             '. Successfully synchronized: '+(', '.join(updated) or '(none)'))
    return updated


def clean_agent_file(target_file_path: str, *, remove_empty: bool = True) -> bool:
    """Remove RAK auto-generated instruction block from an agent file, preserving all user text."""
    if not os.path.lexists(target_file_path):
        return True
    if os.path.islink(target_file_path):
        return False
    temporary = None
    try:
        with open(target_file_path, 'rb') as stream:
            original_bytes = stream.read()
        content = original_bytes.decode('utf-8')
        mode = stat.S_IMODE(os.stat(target_file_path).st_mode)
        bom = '\ufeff' if content.startswith('\ufeff') else ''
        content = content[len(bom):]
        newline = '\r\n' if '\r\n' in content else '\n'

        output = []
        inside = False
        found_marker = False
        fence = None

        for line in content.splitlines(keepends=True):
            if not inside:
                fenced = re.match(r'^[ \t]{0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
                if fenced:
                    token, tail = fenced.groups()
                    if fence is None:
                        fence = token
                    elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                        fence = None
                    output.append(line)
                    continue
                if fence is not None:
                    output.append(line)
                    continue

            if is_start_delimiter(line):
                if inside:
                    raise ValueError('Nested managed instruction markers; file left unchanged')
                inside = True
                found_marker = True
            elif is_end_delimiter(line):
                if not inside:
                    raise ValueError('Unmatched managed instruction end marker; file left unchanged')
                inside = False
            elif not inside:
                output.append(line)

        if inside:
            raise ValueError('Managed instruction end marker is missing; file left unchanged')

        if not found_marker:
            return True

        remaining_text = ''.join(output)
        trimmed = remaining_text.strip()
        if not trimmed:
            if remove_empty:
                os.remove(target_file_path)
                return True
            final = ''
        else:
            final = bom + trimmed + newline

        if final.encode('utf-8') == original_bytes:
            return True

        descriptor, temporary = tempfile.mkstemp(
            prefix='.rak-agent-docs-',
            dir=os.path.dirname(os.path.abspath(target_file_path))
        )
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(final)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)

        if os.path.lexists(target_file_path) and os.path.islink(target_file_path):
            raise ValueError('Instruction file became a symlink')
        os.replace(temporary, target_file_path)
        temporary = None
        return True
    except (OSError, UnicodeError, ValueError) as error:
        print(f"[RAK Agent Docs] Error cleaning {target_file_path}: {error}")
        return False
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


def clean_all_agent_files(cwd: Optional[str] = None, *, remove_empty: bool = True) -> List[str]:
    cleaned = []
    failed = []
    for directory in get_all_workspace_dirs(cwd):
        for filename in AGENT_FILES:
            path = os.path.join(directory, filename)
            if os.path.exists(path):
                if clean_agent_file(path, remove_empty=remove_empty):
                    cleaned.append(os.path.normpath(path))
                else:
                    failed.append(path)
    if failed:
        from core.command_context import OperationError
        raise OperationError('Agent instructions could not be cleaned: ' + ', '.join(failed))
    return cleaned


# Regex matching AI Agent command triggers (handles npx, paths, prefixes, suffixes)
_AGENT_REGEX = re.compile(
    r'(?:\b|[\/\\@])(claude(?:-code)?|gemini|codex|aider|copilot|amp|cursor|windsurf|qwen|deepseek|ollama|agent)\b',
    re.IGNORECASE
)


def handle_terminal_input(line: str, cwd: Optional[str] = None, extra_line: str = ""):

    candidates = [l for l in (line, extra_line) if l and l.strip()]
    if not candidates:
        return

    if not any(_AGENT_REGEX.search(text) for text in candidates):return
    updated_paths=update_all_agent_files(cwd)
    if updated_paths:
        print(f"[RAK Terminal] Synchronized {' & '.join(AGENT_FILES)} in {', '.join(dict.fromkeys(os.path.dirname(path) for path in updated_paths))}",flush=True)
