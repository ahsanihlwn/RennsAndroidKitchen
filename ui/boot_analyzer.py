# SPDX-License-Identifier: MPL-2.0
"""Native Boot Analyzer (core.vdev) QObject controller for QtQuick Workspace.

Architecture:
- Staging container model: explicit outer Firmware slots (boot, vendor_boot,
  vbmeta, vbmeta_system, vbmeta_vendor, init_boot, dtbo) and inner SUPER
  container (accepts super.img, individual logical partition .img/folders,
  or full firmware .zip via drag-and-drop or RAK's tree picker).
- Live Virtual Mounted Filesystem model: populated directly from VirtualFS
  as soon as build_vfs() finishes.
- Live Android-style Logcat streamer: streams stage findings in real time
  as each boot stage executes, with level/search filtering and copy support.
- Always runs with dex_verify=True and verify_hashtree=True.
"""
from __future__ import annotations

import atexit
import datetime
import json
import os
import posixpath
import threading
import time

from PySide6.QtCore import QCoreApplication, Property, QObject, Qt, QTimer, QUrl, Signal, Slot

from services.async_jobs import AsyncJobs
from ui.file_picker import LocalFilePicker

# QEMU live virtual device execution is temporarily disabled (kept in codebase for future work).
QEMU_LIVE_BOOT_ENABLED = False


FIRMWARE_SLOT_DEFS = (
    {"key": "boot", "label": "boot.img", "role": "Kernel & Header", "required": True},
    {"key": "vendor_boot", "label": "vendor_boot.img", "role": "Ramdisk & DTB", "required": True},
    {"key": "vbmeta", "label": "vbmeta.img", "role": "AVB Root Chain", "required": True},
    {"key": "vbmeta_system", "label": "vbmeta_system.img", "role": "AVB System", "required": True},
    {"key": "vbmeta_vendor", "label": "vbmeta_vendor.img", "role": "AVB Vendor", "required": True},
    {"key": "init_boot", "label": "init_boot.img", "role": "GKI Ramdisk", "required": False},
    {"key": "dtbo", "label": "dtbo.img", "role": "DT Overlay", "required": False},
)

FIRMWARE_SLOT_KEYS = {d["key"] for d in FIRMWARE_SLOT_DEFS}

_STAGE_LOGCAT_META = {
    "kernel": ("0", "0", "kernel"),
    "device": ("1", "1", "init"),
    "mount": ("1", "1", "fs_mgr"),
    "selinux": ("1", "1", "selinux"),
    "vintf": ("162", "162", "vintf"),
    "initrc": ("1", "1", "init"),
    "configs": ("1", "1", "init"),
    "avc": ("175", "175", "auditd"),
    "linker": ("248", "248", "linker"),
    "runtime": ("412", "412", "zygote64"),
    "framework": ("724", "741", "PackageManager"),
    "apex": ("210", "210", "apexd"),
    "hardware": ("260", "260", "hwservicemanager"),
    "dex_bytecode": ("724", "768", "art"),
    "impact": ("724", "724", "BootControl"),
    "system_server": ("724", "724", "SystemServer"),
}

_PREVIEWABLE_EXTS = {
    ".prop", ".rc", ".xml", ".json", ".txt", ".conf", ".cfg", ".te", ".cil",
    ".map", ".list", ".load", ".dep", ".sh", ".ini", ".contexts", ".seapp",
}
_PREVIEWABLE_NAMES = {
    "build.prop", "default.prop", "plat_file_contexts", "vendor_file_contexts",
    "plat_property_contexts", "vendor_property_contexts", "plat_service_contexts",
    "vendor_service_contexts", "modules.load", "modules.dep", "modules.alias",
    "public.libraries.txt", "ld.config.txt",
}


def _file_icon_key(name: str, kind: str, *, is_mount: bool = False, is_link: bool = False, vpath: str = "") -> str:
    """Map any file/folder/mount/symlink to RAK's native WorkspaceTree icon key."""
    if is_mount:
        return "drive_mounted" if vpath.startswith("/apex/") else "fs_folder"
    if is_link:
        return "symlink" if kind == "dir" else "symlink_file"
    if kind == "dir":
        return "folder"
    low = (name or "").lower()
    _, ext = os.path.splitext(low)
    if ext in (".apk", ".apex", ".capex"):
        return "apk"
    if ext == ".jar":
        return "archive_jar"
    if ext in (".zip", ".7z", ".rar", ".tar"):
        return {".7z": "archive_7z", ".rar": "archive_rar", ".tar": "archive_tar"}.get(ext, "archive")
    if ext in (".gz", ".tgz", ".zst", ".xz", ".bz2"):
        return "archive_gz"
    if ext == ".img":
        return "drive"
    if ext in (".so", ".ko"):
        return "icon_so"
    if ext in (".xml", ".xsd", ".xsl"):
        return "icon_xml"
    if ext in (".prop", ".properties") or low.endswith(".prop"):
        return "icon_prop"
    if ext == ".rc":
        return "icon_rc"
    if ext in (".cfg", ".conf", ".ini", ".cil", ".te", ".seapp", ".contexts", ".toml") or low.startswith("fstab."):
        return "icon_cfg"
    if ext in (".json", ".jsonc"):
        return "icon_json"
    if ext in (".sh", ".bash", ".mk", ".bat", ".ps1"):
        return "icon_sh"
    if ext in (".txt", ".list", ".load", ".dep", ".map", ".md", ".log"):
        return "txt"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico"):
        return "image"
    if ext in (".ogg", ".mp3", ".wav", ".flac", ".aac", ".m4a"):
        return "icon_audio"
    if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
        return "icon_video"
    if ext == "" and ("/bin/" in vpath or "/xbin/" in vpath or vpath.startswith("/bin/")):
        return "icon_bin"
    return "file"


def _fmt_size(nbytes: int) -> str:
    n = int(nbytes or 0)
    if n <= 0:
        return ""
    if n >= 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _logcat_ts() -> str:
    now = datetime.datetime.now()
    return now.strftime("%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _url_to_path(url_or_path) -> str:
    if isinstance(url_or_path, QUrl):
        return os.path.normpath(url_or_path.toLocalFile())
    text = str(url_or_path or "").strip()
    if text.startswith("file://"):
        return os.path.normpath(QUrl(text).toLocalFile())
    return os.path.normpath(text) if text else ""


def _strip_slot(stem: str) -> str:
    low = stem.lower().strip()
    if len(low) > 2 and low[-2:] in ("_a", "_b"):
        return low[:-2]
    return low


def _classify_firmware_stem(stem: str) -> str:
    norm = _strip_slot(stem)
    if norm in FIRMWARE_SLOT_KEYS:
        return norm
    if norm.startswith("vbmeta_system"):
        return "vbmeta_system"
    if norm.startswith("vbmeta_vendor"):
        return "vbmeta_vendor"
    if norm == "vbmeta" or norm.startswith("vbmeta_"):
        return "vbmeta"
    if "vendor_boot" in norm or norm == "vndrboot":
        return "vendor_boot"
    if "init_boot" in norm:
        return "init_boot"
    if norm == "boot":
        return "boot"
    if norm == "dtbo":
        return "dtbo"
    return ""


def _format_autofix_recipe(item: dict) -> dict:
    rid = str(item.get("id", ""))
    preview_lines = []
    copy_text = ""

    if item.get("cil_rules"):
        cil = str(item["cil_rules"]).strip()
        te = str(item.get("te_rules") or "").strip()
        preview_lines.append("# CIL rules\n" + cil)
        if te:
            preview_lines.append("\n# TE rules\n" + te)
        copy_text = cil
    elif item.get("content"):
        content = str(item["content"]).strip()
        target = str(item.get("target_path") or "")
        if target:
            preview_lines.append(f"# Target: {target}")
        preview_lines.append(content)
        copy_text = content
    elif item.get("entries"):
        entries = item["entries"]
        for e in entries[:15]:
            preview_lines.append(
                f"{e.get('path', '?')}  :  {e.get('actual', '?')}  ->  {e.get('expected', '?')}"
            )
        if len(entries) > 15:
            preview_lines.append(f"... (+{len(entries) - 15} more)")
        copy_text = "\n".join(preview_lines)
    elif item.get("overlays"):
        ovs = item["overlays"]
        for o in ovs[:15]:
            preview_lines.append(
                f"[{o.get('kind', '?')}] {o.get('apk_path', '?')} -> {o.get('target_package', '?')} "
                f"(missing={o.get('missing_count', 0)})"
            )
        if len(ovs) > 15:
            preview_lines.append(f"... (+{len(ovs) - 15} more)")
        copy_text = "\n".join(preview_lines)
    elif item.get("missing") or item.get("abi_mismatch"):
        miss = item.get("missing") or []
        abi = item.get("abi_mismatch") or []
        if miss:
            preview_lines.append("Missing .so:\n  " + "\n  ".join(miss[:20]))
        if abi:
            preview_lines.append("Wrong ABI .so:\n  " + "\n  ".join(abi[:20]))
        copy_text = "\n".join(preview_lines)
    elif item.get("targets"):
        t = item["targets"]
        preview_lines.append("Targets: " + ", ".join(str(x) for x in t))
        if item.get("fstab_origin"):
            preview_lines.append(f"fstab: {item['fstab_origin']}")
        copy_text = "\n".join(preview_lines)

    return {
        "id": rid,
        "stage": str(item.get("stage", "")),
        "kind": str(item.get("kind", "")),
        "title": str(item.get("title", rid)),
        "tool": str(item.get("tool", "")),
        "preview": "\n".join(preview_lines),
        "copyText": copy_text or str(item.get("title", "")),
        "canApply": bool(item.get("can_apply", False)),
        "applySummary": str(item.get("apply_summary", "")),
    }


def _build_profile_summary(profile: dict, mounts: list) -> str:
    props = profile.get("props") or {}
    parts = []
    rel = props.get("ro.build.version.release") or ""
    sdk = profile.get("sdk") or props.get("ro.build.version.sdk") or ""
    if rel or sdk:
        parts.append(f"Android {rel or '?'}" + (f" (API {sdk})" if sdk else ""))
    dev = props.get("ro.product.device") or props.get("ro.product.vendor.device") or ""
    if dev:
        parts.append(dev)
    soc = props.get("ro.board.platform") or props.get("ro.hardware") or ""
    if soc:
        parts.append(soc)
    spl = (profile.get("security_patch") or {}).get("system") or props.get("ro.build.version.security_patch") or ""
    if spl:
        parts.append(f"SPL {spl}")
    pnames = profile.get("partitions") or []
    if pnames:
        parts.append(f"{len(pnames)} partitions")
    elif mounts:
        parts.append(f"{len(mounts)} mounts")
    return " · ".join(parts)


def _severity_to_logcat_level(severity: str, stage_key: str, code: str) -> str:
    sev = str(severity or "").lower()
    if sev == "blocker":
        if stage_key in ("system_server", "mount", "selinux") or "halt" in code or "fatal" in code:
            return "F"
        return "E"
    if sev == "warn":
        return "W"
    return "I"


def _finding_to_tag(stage_key: str, code: str, default_tag: str) -> str:
    c = str(code or "").lower()
    if "overlay" in c:
        return "OverlayManager"
    if "avb" in c or "vbmeta" in c or "hashtree" in c or "verity" in c:
        return "avb"
    if "fbe" in c or "fde" in c:
        return "vold"
    if "elf-symbol" in c:
        return "linker"
    if "shared-uid" in c:
        return "PackageManager"
    if (
        "npe" in c or "noclassdef" in c or "nosuchmethod" in c or "abstract" in c
        or "verify" in c or "jni" in c or "reflection-member" in c
    ):
        return "AndroidRuntime"
    if "blocking-hal" in c:
        return "ServiceManager"
    if "logic-summary" in c or "boot-service" in c:
        return "SystemServer"
    if "apex" in c:
        return "apexd"
    if "selinux" in c or "avc" in c or "sepolicy" in c:
        return "auditd" if stage_key == "avc" else "selinux"
    if "dlkm" in c or "module" in c:
        return "modprobe"
    return default_tag


class BootTargetPicker(LocalFilePicker):
    """Tree-view file/folder picker reusing RAK's WorkspaceFilePickerDialog."""

    def __init__(self, parent, controller):
        super().__init__(parent, extensions=(".zip", ".img"), multi_select=True)
        self._controller = controller

    @Slot()
    def refreshLocations(self):
        self._locations_requested = True
        tree_roots = []
        ws = getattr(self._controller, "workspace", None)
        if ws is not None:
            proj = getattr(ws, "project", "")
            if isinstance(proj, str) and proj:
                for pname in (getattr(ws, "p1", ""), getattr(ws, "p2", "")):
                    if pname:
                        full = os.path.normpath(os.path.join(proj, pname))
                        if os.path.isdir(full) and full not in tree_roots:
                            tree_roots.append(full)
                if not tree_roots and os.path.isdir(proj):
                    tree_roots.append(os.path.normpath(proj))
            for tree in (getattr(ws, "directories", None) or ()):
                base = getattr(tree, "base", "") or ""
                if base and os.path.isdir(base) and base not in tree_roots:
                    tree_roots.append(base)
            active = self._controller._active_project_base()
            if active and os.path.isdir(active) and active not in tree_roots:
                tree_roots.insert(0, active)

        def work():
            from core.file_picker import _quick_access, _get_drives
            rows = []
            if tree_roots:
                rows.append({"header": "WORKSPACE TREE", "path": "", "name": "", "kind": ""})
                for p in tree_roots:
                    rows.append({"name": os.path.basename(p) or p, "path": p, "header": "", "kind": "folder"})
            rows.append({"header": "QUICK ACCESS", "path": "", "name": "", "kind": ""})
            rows.extend({"name": name, "path": path, "header": "", "kind": "folder"} for name, path in _quick_access())
            rows.append({"header": "DRIVES", "path": "", "name": "", "kind": ""})
            rows.extend({"name": path, "path": path, "header": "", "kind": "drive"} for path in _get_drives())
            return True, rows

        def done(ok, result):
            if ok:
                self._locations = result
                self.locationsChanged.emit()

        self._jobs.submit("locations", work, None, done)

    @Slot()
    def openSelected(self):
        selected_list = list(getattr(self, "_selected_paths", []) or [])
        if selected_list:
            valid = [p for p in selected_list if p and (os.path.isfile(p) or os.path.isdir(p))]
            if valid:
                self.chosen.emit(valid[0])
                self.chosenUrls.emit([QUrl.fromLocalFile(p).toString() for p in valid])
                return
        target = self._selected or self._directory
        if not target or not (os.path.isfile(target) or os.path.isdir(target)):
            return
        self.chosen.emit(target)
        self.chosenUrls.emit([QUrl.fromLocalFile(target).toString()])


class BootAnalyzerController(QObject):
    """Controller for the redesigned Boot Analyzer (Staging + Live VFS + Live Logcat)."""

    stateChanged = Signal()
    stagingChanged = Signal()
    treeChanged = Signal()
    vfsChanged = Signal()
    logcatChanged = Signal()
    diffChanged = Signal()
    logicMapChanged = Signal()
    correlatorChanged = Signal()
    autofixChanged = Signal()
    qemuChanged = Signal()
    qemuConsoleChanged = Signal()
    qemuFrameChanged = Signal()
    qemuStarted = Signal()
    bootStarted = Signal()
    jumpToLiveBootRequested = Signal()
    _progressSignal = Signal(str)
    _vfsReadySignal = Signal()
    _logcatBatchSignal = Signal(object)
    _qemuLineSignal = Signal(object)
    _qemuPhaseSignal = Signal(str)
    _qemuDoneSignal = Signal(bool, str)
    _qemuFrameSignal = Signal()
    _qemuPrepDoneSignal = Signal(object)

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self._jobs = AsyncJobs(self)
        self._picker = BootTargetPicker(self, self)
        self._run_seq = 0

        # ── Inline Workspace Tree Browser State (for direct drag-and-drop) ──
        self._tree_index = 0
        self._tree_base = ""
        self._tree_dir = ""
        self._tree_items: list[dict] = []
        self._tree_loading = False

        # ── Explicit Staging State ──
        self._zip_path = ""
        self._super_path = ""
        # slot_key -> {"path": str, "fileName": str, "size": int, "origin": "file"|"zip"|"dir"}
        self._firmware_staged: dict[str, dict] = {}
        # part_name -> {"path": str, "fileName": str, "size": int, "fsType": str, "origin": "super"|"zip"|"file"|"dir"}
        self._super_children: dict[str, dict] = {}
        self._staging_busy = False
        self._staging_notice = ""

        # ── Analysis & Runtime State ──
        self._running = False
        self._has_report = False
        self._progress_text = ""
        self._progress_percent = -1
        self._error_text = ""
        self._permissive = False
        self._avb_enabled = True

        # Report data
        self._verdict_status = "pending"   # "ok" | "warn" | "error" | "pending"
        self._verdict_summary = ""
        self._profile_summary = ""
        self._reaches_system_server = False
        self._duration_ms = 0
        self._blocker_count = 0
        self._warn_count = 0
        self._pass_count = 0
        self._phases: list[dict] = []
        self._autofix: list[dict] = []
        self._raw_autofix_recipes: dict[str, dict] = {}
        self._autofix_backup_stack: list[str] = []
        self._autofix_status = ""
        self._autofix_status_ok = True
        self._autofix_diff_recipe_id = ""
        self._autofix_diff_text = ""
        self._last_report = None
        self._last_layout = None
        self._raw_report_dict: dict | None = None

        # ── Logic Map Explorer State ──
        self._logic_map_raw: dict = {}
        self._daemon_audit_raw: list[dict] = []
        self._hardware_raw: dict = {}
        self._kernel_config_raw: dict = {}
        self._kernel_hw_raw: dict = {}
        self._logic_map_summary = ""
        self._logic_map_category = "all"
        self._logic_map_query = ""
        self._logic_map_all_items: list[dict] = []
        self._logic_map_filtered: list[dict] = []

        # ── Real Logcat / dmesg / ramoops Correlator State ──
        self._correlator_busy = False
        self._correlator_source = ""
        self._correlator_summary = ""
        self._correlator_error = ""
        self._correlator_events: list[dict] = []
        self._correlator_cil = ""

        # ── Live Virtual Mounted Filesystem State ──
        self._vfs_lock = threading.Lock()
        self._vfs = None
        self._ramdisk_vfs = None
        self._vfs_ready = False
        self._vfs_current_path = "/"
        self._vfs_selected_path = ""
        self._vfs_reveal_row = -1
        self._vfs_expanded: set[str] = {"/"}
        self._vfs_mounts: list[dict] = []
        self._vfs_entries: list[dict] = []
        self._vfs_preview_path = ""
        self._vfs_preview_text = ""

        # ── Live Logcat State ──
        self._logcat_lines: list[dict] = []
        self._logcat_level_filter = "all"   # "all" | "I" | "W" | "E"
        self._logcat_query = ""
        self._logcat_verbose = True         # True = include full chronological boot_trace rows
        self._filtered_logcat_cache: list[dict] = []

        # ── Diff vs Stock State ──
        self._diff_running = False
        self._stock_path = ""
        self._diff_summary = ""
        self._diff_error = ""
        self._diff_regressions: list[dict] = []
        self._diff_resolved: list[dict] = []
        self._diff_prop_drifts: list[dict] = []
        self._diff_partition_deltas: list[dict] = []

        # ── Unified Live Boot / QEMU Virtual Device State (Governed by Kernel-Vendor Hardware Gate) ──
        self._qemu_env: dict = {}
        self._qemu_abi = "auto"          # "auto" | "arm64" | "arm64_32" | "arm32"
        self._qemu_ram_mb = 3072
        self._qemu_auto_boot = False     # User explicitly clicks ▶ Play inside Live Boot after Boot finishes
        self._hardware_gate: dict = {}
        self._qemu_kernel_path = ""
        self._qemu_running = False
        self._qemu_preparing = False
        self._qemu_status = ""
        self._qemu_session = None
        self._qemu_last_log = ""
        self._qemu_frame_seq = 0
        self._qemu_milestones: list[str] = []
        self._qemu_console_lines: list[dict] = []
        self._qemu_last_console_render = 0.0
        self._qemu_pending_lock = threading.Lock()
        self._qemu_pending_lines: list[dict] = []
        self._qemu_last_frame_ts = 0.0
        self._qemu_frame_pending = False
        self._qemu_flush_timer = QTimer(self)
        self._qemu_flush_timer.setInterval(160)
        self._qemu_flush_timer.timeout.connect(self._flush_qemu_ui)

        # Ensure ephemeral ROM/QEMU caches are always wiped on app exit
        atexit.register(self._cleanup_on_exit)
        app_inst = QCoreApplication.instance()
        if app_inst is not None:
            app_inst.aboutToQuit.connect(self._cleanup_on_exit)

        self._progressSignal.connect(self._apply_progress, Qt.ConnectionType.QueuedConnection)
        self._vfsReadySignal.connect(self._on_vfs_ready_ui, Qt.ConnectionType.QueuedConnection)
        self._logcatBatchSignal.connect(self._append_logcat_batch_ui, Qt.ConnectionType.QueuedConnection)
        self._qemuLineSignal.connect(self._on_qemu_line_ui, Qt.ConnectionType.QueuedConnection)
        self._qemuPhaseSignal.connect(self._on_qemu_phase_ui, Qt.ConnectionType.QueuedConnection)
        self._qemuDoneSignal.connect(self._on_qemu_done_ui, Qt.ConnectionType.QueuedConnection)
        self._qemuFrameSignal.connect(self._on_qemu_frame_ui, Qt.ConnectionType.QueuedConnection)
        self._qemuPrepDoneSignal.connect(self._on_qemu_prep_done_ui, Qt.ConnectionType.QueuedConnection)

    # ── Helpers ──
    def _workspace_tree_entries(self) -> list[tuple[str, str]]:
        ws = self.workspace
        if ws is None:
            return []
        out = []
        proj = getattr(ws, "project", "")
        if isinstance(proj, str) and proj:
            for pname in (getattr(ws, "p1", ""), getattr(ws, "p2", "")):
                if pname:
                    full = os.path.normpath(os.path.join(proj, pname))
                    if os.path.isdir(full):
                        out.append((str(pname), full))
            if not out and os.path.isdir(proj):
                out.append((os.path.basename(proj) or proj, os.path.normpath(proj)))
        elif proj is not None:
            for attr in ("path", "base", "root", "location"):
                val = getattr(proj, attr, "")
                if isinstance(val, str) and val and os.path.isdir(val):
                    out.append((os.path.basename(val) or val, os.path.normpath(val)))
                    break
        if not out:
            for tree in (getattr(ws, "directories", None) or ()):
                base = getattr(tree, "base", "") or ""
                if base and os.path.isdir(base):
                    out.append((os.path.basename(base) or base, os.path.normpath(base)))
        return out

    def _active_project_base(self) -> str:
        entries = self._workspace_tree_entries()
        if entries:
            idx = max(0, min(self._tree_index, len(entries) - 1))
            return entries[idx][1]
        return ""

    def _current_staged_payload(self) -> dict:
        firmware = {
            k: v["path"]
            for k, v in self._firmware_staged.items()
            if v.get("origin") in ("file", "dir") and v.get("path")
        }
        partitions = {
            k: v["path"]
            for k, v in self._super_children.items()
            if v.get("origin") in ("file", "dir") and v.get("path")
        }
        return {
            "zip_path": self._zip_path,
            "super_path": self._super_path,
            "firmware": firmware,
            "partitions": partitions,
        }

    def _rebuild_filtered_logcat(self):
        lv = self._logcat_level_filter
        q = self._logcat_query.strip().lower()
        verbose = self._logcat_verbose
        out = []
        for row in self._logcat_lines:
            if not verbose and row.get("verbose"):
                continue
            if lv == "E" and row["level"] not in ("E", "F"):
                continue
            elif lv == "W" and row["level"] != "W":
                continue
            elif lv == "I" and row["level"] != "I":
                continue
            if q:
                hay = (
                    f"{row['tag']} {row['msg']} {row['detail']} {row['path']} {row['stage']}"
                ).lower()
                if q not in hay:
                    continue
            out.append(row)
        self._filtered_logcat_cache = out

    # ── Qt Properties: Picker & General ──
    @Property(QObject, constant=True)
    def picker(self):
        return self._picker

    @Property(str, notify=stagingChanged)
    def activeTreeUrl(self):
        base = self._active_project_base()
        if base and os.path.isdir(base):
            return QUrl.fromLocalFile(base).toString()
        return ""

    @Property(bool, notify=stateChanged)
    def running(self):
        return self._running

    @Property(bool, notify=stateChanged)
    def hasReport(self):
        return self._has_report

    @Property(str, notify=stateChanged)
    def progressText(self):
        return self._progress_text

    @Property(int, notify=stateChanged)
    def progressPercent(self):
        return self._progress_percent

    @Property(str, notify=stateChanged)
    def errorText(self):
        return self._error_text

    @Property(bool, notify=stateChanged)
    def permissive(self):
        return self._permissive

    @Property(bool, notify=stateChanged)
    def avbEnabled(self):
        return self._avb_enabled

    @Property(str, notify=stateChanged)
    def verdictStatus(self):
        return self._verdict_status

    @Property(str, notify=stateChanged)
    def verdictSummary(self):
        return self._verdict_summary

    @Property(str, notify=stateChanged)
    def profileSummary(self):
        return self._profile_summary

    @Property(bool, notify=stateChanged)
    def reachesSystemServer(self):
        return self._reaches_system_server

    @Property(int, notify=stateChanged)
    def durationMs(self):
        return self._duration_ms

    @Property(int, notify=stateChanged)
    def blockerCount(self):
        return self._blocker_count

    @Property(int, notify=stateChanged)
    def warnCount(self):
        return self._warn_count

    @Property(int, notify=stateChanged)
    def passCount(self):
        return self._pass_count

    @Property("QVariantList", notify=stateChanged)
    def phases(self):
        return self._phases

    @Property("QVariantList", notify=stateChanged)
    def autofixRecipes(self):
        return self._autofix

    # ── Qt Properties: Staging Containers ──
    @Property(bool, notify=stagingChanged)
    def stagingBusy(self):
        return self._staging_busy

    @Property(str, notify=stagingChanged)
    def stagingNotice(self):
        return self._staging_notice

    @Property(str, notify=stagingChanged)
    def zipPath(self):
        return self._zip_path

    @Property(str, notify=stagingChanged)
    def zipLabel(self):
        return os.path.basename(self._zip_path) if self._zip_path else ""

    @Property(str, notify=stagingChanged)
    def superPath(self):
        return self._super_path

    @Property(str, notify=stagingChanged)
    def superLabel(self):
        return os.path.basename(self._super_path) if self._super_path else ""

    @Property(bool, notify=stagingChanged)
    def canBoot(self):
        return bool(self._zip_path or self._super_path or self._super_children)

    @Property(int, notify=stagingChanged)
    def firmwareStagedCount(self):
        return len(self._firmware_staged)

    @Property(int, notify=stagingChanged)
    def superPartitionCount(self):
        return len(self._super_children)

    @Property(str, notify=stagingChanged)
    def stagedSummary(self):
        parts = []
        if self._zip_path:
            parts.append(os.path.basename(self._zip_path))
        elif self._super_path:
            parts.append(os.path.basename(self._super_path))
        fw_n = len(self._firmware_staged)
        sp_n = len(self._super_children)
        if fw_n:
            parts.append(f"{fw_n} firmware")
        if sp_n:
            parts.append(f"{sp_n} super")
        return " · ".join(parts)

    @Property("QVariantList", notify=stagingChanged)
    def firmwareSlots(self):
        out = []
        for meta in FIRMWARE_SLOT_DEFS:
            key = meta["key"]
            item = self._firmware_staged.get(key)
            if item:
                out.append({
                    "key": key,
                    "label": meta["label"],
                    "role": meta["role"],
                    "required": meta["required"],
                    "filled": True,
                    "fileName": item.get("fileName") or meta["label"],
                    "path": item.get("path") or "",
                    "sizeLabel": _fmt_size(item.get("size", 0)),
                    "origin": item.get("origin", "file"),
                    "status": "ok",
                })
            else:
                out.append({
                    "key": key,
                    "label": meta["label"],
                    "role": meta["role"],
                    "required": meta["required"],
                    "filled": False,
                    "fileName": "",
                    "path": "",
                    "sizeLabel": "",
                    "origin": "",
                    "status": "missing" if meta["required"] else "optional",
                })
        return out

    @Property("QVariantList", notify=stagingChanged)
    def superPartitions(self):
        priority = {
            "system": 0, "system_ext": 1, "product": 2, "vendor": 3, "odm": 4,
            "system_dlkm": 5, "vendor_dlkm": 6, "odm_dlkm": 7,
        }
        names = sorted(self._super_children.keys(), key=lambda n: (priority.get(n, 50), n))
        out = []
        for name in names:
            item = self._super_children[name]
            out.append({
                "name": name,
                "fileName": item.get("fileName") or f"{name}.img",
                "path": item.get("path") or "",
                "fsType": item.get("fsType") or "",
                "sizeLabel": _fmt_size(item.get("size", 0)),
                "origin": item.get("origin", "file"),
                "core": name in ("system", "vendor", "product", "system_ext", "odm"),
                "removable": item.get("origin") in ("file", "dir"),
            })
        return out

    # ── Qt Properties: Virtual Mounted Filesystem ──
    @Property(bool, notify=vfsChanged)
    def vfsReady(self):
        return self._vfs_ready

    @Property(str, notify=vfsChanged)
    def vfsCurrentPath(self):
        return self._vfs_current_path

    @Property(str, notify=vfsChanged)
    def vfsSelectedPath(self):
        return self._vfs_selected_path

    @Property(int, notify=vfsChanged)
    def vfsRevealRow(self):
        return self._vfs_reveal_row

    @Slot(str, str, result=QUrl)
    def fileIconUrl(self, key: str, palette: str = ""):
        ws = self.workspace
        if ws is not None and hasattr(ws, "fileIconUrl"):
            try:
                return ws.fileIconUrl(key, palette)
            except Exception:
                pass
        try:
            from icons.handler import icon
            from main_ext.class_ThemeUi import _PALETTES
            return QUrl.fromLocalFile(icon(key or "file", light=_PALETTES.get(palette, {}).get("_is_light", False)))
        except Exception:
            return QUrl()

    @Property("QVariantList", notify=vfsChanged)
    def vfsBreadcrumbs(self):
        cur = self._vfs_current_path or "/"
        crumbs = [{"label": "/", "path": "/"}]
        if cur != "/":
            acc = ""
            for seg in [s for s in cur.split("/") if s]:
                acc += "/" + seg
                crumbs.append({"label": seg, "path": acc})
        return crumbs

    @Property("QVariantList", notify=vfsChanged)
    def vfsMounts(self):
        return self._vfs_mounts

    @Property("QVariantList", notify=vfsChanged)
    def vfsEntries(self):
        return self._vfs_entries

    @Property(str, notify=vfsChanged)
    def vfsPreviewPath(self):
        return self._vfs_preview_path

    @Property(str, notify=vfsChanged)
    def vfsPreviewText(self):
        return self._vfs_preview_text

    # ── Qt Properties: Live Logcat ──
    @Property(int, notify=logcatChanged)
    def logcatCount(self):
        return len(self._logcat_lines)

    @Property("QVariantList", notify=logcatChanged)
    def logcatLines(self):
        return self._filtered_logcat_cache

    @Property(str, notify=logcatChanged)
    def logcatLevelFilter(self):
        return self._logcat_level_filter

    @Property(str, notify=logcatChanged)
    def logcatQuery(self):
        return self._logcat_query

    @Property(bool, notify=logcatChanged)
    def logcatVerbose(self):
        return self._logcat_verbose

    # ── Qt Properties: Diff vs Stock ──
    @Property(bool, notify=diffChanged)
    def diffRunning(self):
        return self._diff_running

    @Property(str, notify=diffChanged)
    def stockPath(self):
        return self._stock_path

    @Property(str, notify=diffChanged)
    def stockLabel(self):
        return os.path.basename(self._stock_path) if self._stock_path else ""

    @Property(str, notify=diffChanged)
    def diffSummary(self):
        return self._diff_summary

    @Property(str, notify=diffChanged)
    def diffError(self):
        return self._diff_error

    @Property("QVariantList", notify=diffChanged)
    def diffRegressions(self):
        return self._diff_regressions

    @Property("QVariantList", notify=diffChanged)
    def diffResolved(self):
        return self._diff_resolved

    @Property("QVariantList", notify=diffChanged)
    def diffPropDrifts(self):
        return self._diff_prop_drifts

    @Property("QVariantList", notify=diffChanged)
    def diffPartitionDeltas(self):
        return self._diff_partition_deltas

    # ── Qt Properties: Inline Workspace Tree Browser ──
    @Property("QStringList", notify=treeChanged)
    def trees(self):
        return [name for name, _ in self._workspace_tree_entries()]

    @Property(int, notify=treeChanged)
    def treeIndex(self):
        return self._tree_index

    @Property(str, notify=treeChanged)
    def treeRelPath(self):
        if not self._tree_dir:
            return ""
        if self._tree_base:
            try:
                rel = os.path.relpath(self._tree_dir, self._tree_base)
                if rel in (".", ""):
                    return "/"
                return "/" + rel.replace("\\", "/")
            except ValueError:
                pass
        return self._tree_dir

    @Property(bool, notify=treeChanged)
    def treeCanUp(self):
        if not self._tree_dir or not self._tree_base:
            return False
        return os.path.normcase(os.path.normpath(self._tree_dir)) != os.path.normcase(os.path.normpath(self._tree_base))

    @Property(bool, notify=treeChanged)
    def treeLoading(self):
        return self._tree_loading

    @Property("QVariantList", notify=treeChanged)
    def treeItems(self):
        return self._tree_items

    @Slot(int)
    def selectTree(self, index: int):
        entries = self._workspace_tree_entries()
        if not (0 <= index < len(entries)):
            return
        self._tree_index = index
        self._tree_base = entries[index][1]
        self._tree_dir = self._tree_base
        self.treeChanged.emit()
        self.refreshTree()

    @Slot(str)
    def treeOpenDir(self, path: str):
        p = _url_to_path(path)
        if not p or not os.path.isdir(p):
            return
        self._tree_dir = p
        self.treeChanged.emit()
        self.refreshTree()

    @Slot()
    def treeUp(self):
        if not self.treeCanUp:
            return
        parent = os.path.dirname(self._tree_dir)
        if parent and os.path.isdir(parent):
            self._tree_dir = parent
            self.treeChanged.emit()
            self.refreshTree()

    @Slot()
    def refreshTree(self):
        entries = self._workspace_tree_entries()
        if not self._tree_base and entries:
            idx = max(0, min(self._tree_index, len(entries) - 1))
            self._tree_index = idx
            self._tree_base = entries[idx][1]
            self._tree_dir = self._tree_base
        target_dir = self._tree_dir or self._tree_base
        if not target_dir or not os.path.isdir(target_dir):
            self._tree_items = []
            self._tree_loading = False
            self.treeChanged.emit()
            return

        self._tree_loading = True
        self.treeChanged.emit()

        def work():
            skip_dirs = {"config", "neocat_config", "automation", ".git", "neobranch"}
            dirs = []
            files = []
            try:
                names = sorted(os.listdir(target_dir), key=lambda s: s.lower())
            except OSError:
                names = []
            for name in names:
                full = os.path.join(target_dir, name)
                if os.path.isdir(full):
                    if name in skip_dirs:
                        continue
                    dirs.append({
                        "name": name,
                        "path": full,
                        "kind": "dir",
                        "iconKey": "folder",
                        "size": "",
                        "ext": "",
                    })
                elif os.path.isfile(full):
                    low = name.lower()
                    if not (low.endswith(".img") or low.endswith(".zip")):
                        continue
                    try:
                        sz = os.path.getsize(full)
                    except OSError:
                        sz = 0
                    is_zip = low.endswith(".zip")
                    files.append({
                        "name": name,
                        "path": full,
                        "kind": "zip" if is_zip else "img",
                        "iconKey": "archive" if is_zip else "drive",
                        "size": _fmt_size(sz),
                        "ext": "ZIP" if is_zip else "IMG",
                    })
            return True, dirs + files

        def done(ok, result):
            self._tree_loading = False
            if ok and isinstance(result, list):
                self._tree_items = result
            self.treeChanged.emit()

        self._jobs.submit("vdev-tree", work, None, done)

    # ── Lifecycle ──
    @Slot()
    def open(self):
        """Open Boot Analyzer cleanly in Staging mode — NEVER auto-analyze on open."""
        self._staging_notice = ""
        entries = self._workspace_tree_entries()
        if entries:
            idx = max(0, min(self._tree_index, len(entries) - 1))
            self._tree_index = idx
            if not self._tree_base or not os.path.isdir(self._tree_base):
                self._tree_base = entries[idx][1]
                self._tree_dir = self._tree_base
        self.stagingChanged.emit()
        self.refreshTree()
        self.refreshQemuEnv()

    @Slot()
    def stop(self):
        self._run_seq += 1
        self.stopQemuBoot()
        self._jobs.stop()

    @Slot(bool)
    def setPermissive(self, value: bool):
        v = bool(value)
        if self._permissive != v:
            self._permissive = v
            self.stateChanged.emit()

    @Slot(bool)
    def setAvbEnabled(self, value: bool):
        v = bool(value)
        if self._avb_enabled != v:
            self._avb_enabled = v
            self.stateChanged.emit()

    # ── Staging Slots (Drag & Drop + Tree Import) ──
    @Slot(str)
    def clearFirmwareSlot(self, key: str):
        k = str(key or "").strip().lower()
        if k in self._firmware_staged:
            del self._firmware_staged[k]
            self._staging_notice = ""
            self.stagingChanged.emit()

    @Slot(str)
    def removeSuperPartition(self, name: str):
        n = str(name or "").strip().lower()
        if n in self._super_children:
            del self._super_children[n]
            self._staging_notice = ""
            self.stagingChanged.emit()

    @Slot()
    def clearSuperContainer(self):
        self._super_path = ""
        self._super_children.clear()
        self._staging_notice = ""
        self.stagingChanged.emit()

    @Slot()
    def clearAllStaged(self):
        self._zip_path = ""
        self._super_path = ""
        self._firmware_staged.clear()
        self._super_children.clear()
        self._staging_notice = ""
        self.stagingChanged.emit()

    @Slot("QVariantList", str, str)
    def stagePaths(self, raw_paths, zone: str = "auto", slot_key: str = ""):
        """Stage dropped or picked files/folders into Firmware slots or SUPER container."""
        paths = []
        for item in (raw_paths or []):
            p = _url_to_path(item)
            if p and (os.path.isfile(p) or os.path.isdir(p)):
                paths.append(p)
        if not paths:
            return

        zone_norm = str(zone or "auto").strip().lower()
        forced_slot = str(slot_key or "").strip().lower()
        self._staging_busy = True
        self._staging_notice = ""
        self.stagingChanged.emit()

        def work():
            from core import native_backend
            from core.imgfs import detect_fs

            res = {
                "zip_path": "",
                "super_path": "",
                "firmware": {},
                "super_children": {},
                "notes": [],
            }

            for p in paths:
                base = os.path.basename(p)
                low = base.lower()

                # 1) Full firmware .zip -> probe and populate BOTH firmware slots and super container
                if os.path.isfile(p) and low.endswith(".zip"):
                    res["zip_path"] = p
                    try:
                        entries = native_backend.vdev_probe_zip(p) or []
                    except Exception as exc:
                        res["notes"].append(f"ZIP probe failed: {exc}")
                        continue
                    for ent in entries:
                        ebase = ent.get("basename") or os.path.basename(ent.get("name", ""))
                        estem = _strip_slot(os.path.splitext(ebase)[0])
                        erole = ent.get("role", "")
                        esize = int(ent.get("size") or 0)
                        fw_slot = _classify_firmware_stem(estem)
                        if fw_slot:
                            res["firmware"][fw_slot] = {
                                "path": p,
                                "fileName": ebase,
                                "size": esize,
                                "origin": "zip",
                            }
                        elif erole == "super" or estem == "super":
                            res["super_children"]["super"] = {
                                "path": p,
                                "fileName": ebase,
                                "size": esize,
                                "fsType": "SUPER",
                                "origin": "zip",
                            }
                        elif erole == "partition":
                            res["super_children"][estem] = {
                                "path": p,
                                "fileName": ebase,
                                "size": esize,
                                "fsType": "IMG",
                                "origin": "zip",
                            }
                    continue

                # 2) Forced single firmware slot drop/pick
                if forced_slot and forced_slot in FIRMWARE_SLOT_KEYS:
                    sz = os.path.getsize(p) if os.path.isfile(p) else 0
                    res["firmware"][forced_slot] = {
                        "path": p,
                        "fileName": base,
                        "size": sz,
                        "origin": "dir" if os.path.isdir(p) else "file",
                    }
                    continue

                # 3) Folder dropped -> either single partition dir OR a folder containing multiple imgs/dirs
                if os.path.isdir(p):
                    from core.vdev.mounts import _is_known_or_oem_partition, _looks_like_partition
                    stem = _strip_slot(base)
                    fw_slot = _classify_firmware_stem(stem)
                    if fw_slot and os.path.isdir(os.path.join(p, "ramdisk")):
                        res["firmware"][fw_slot] = {
                            "path": p,
                            "fileName": base,
                            "size": 0,
                            "origin": "dir",
                        }
                    elif _is_known_or_oem_partition(stem) or _looks_like_partition(p):
                        res["super_children"][stem] = {
                            "path": p,
                            "fileName": base,
                            "size": 0,
                            "fsType": "DIR",
                            "origin": "dir",
                        }
                    else:
                        # Scan direct children of folder if user explicitly imported a ROM folder
                        try:
                            children = sorted(os.listdir(p))
                        except OSError:
                            children = []
                        matched = 0
                        for cname in children:
                            cp = os.path.join(p, cname)
                            clow = cname.lower()
                            if os.path.isfile(cp) and clow.endswith(".img"):
                                cstem = _strip_slot(os.path.splitext(cname)[0])
                                cfw = _classify_firmware_stem(cstem)
                                if cfw:
                                    res["firmware"][cfw] = {
                                        "path": cp,
                                        "fileName": cname,
                                        "size": os.path.getsize(cp),
                                        "origin": "file",
                                    }
                                    matched += 1
                                elif cstem == "super":
                                    res["super_path"] = cp
                                    matched += 1
                                else:
                                    try:
                                        fs_t = detect_fs(cp)
                                    except Exception:
                                        fs_t = None
                                    if fs_t:
                                        res["super_children"][cstem] = {
                                            "path": cp,
                                            "fileName": cname,
                                            "size": os.path.getsize(cp),
                                            "fsType": str(fs_t).upper(),
                                            "origin": "file",
                                        }
                                        matched += 1
                            elif os.path.isdir(cp) and cname != "config":
                                cstem = _strip_slot(cname)
                                if _is_known_or_oem_partition(cstem) and _looks_like_partition(cp):
                                    res["super_children"][cstem] = {
                                        "path": cp,
                                        "fileName": cname,
                                        "size": 0,
                                        "fsType": "DIR",
                                        "origin": "dir",
                                    }
                                    matched += 1
                        if matched == 0:
                            res["notes"].append(f"No firmware/partition images found inside '{base}'")
                    continue

                # 4) Single .img file
                stem = _strip_slot(os.path.splitext(base)[0])
                sz = os.path.getsize(p)

                if stem == "super" or (zone_norm == "super" and low.startswith("super")):
                    res["super_path"] = p
                    try:
                        sinfo = native_backend.vdev_probe_super(p, slot=0) or {}
                        for part in (sinfo.get("partitions") or []):
                            pname = _strip_slot(part.get("name", ""))
                            fst = str(part.get("fs_type") or "").upper()
                            psz = int(part.get("size") or 0)
                            if pname and fst in ("EXT", "EROFS"):
                                res["super_children"][pname] = {
                                    "path": p,
                                    "fileName": f"{pname} (super)",
                                    "size": psz,
                                    "fsType": fst,
                                    "origin": "super",
                                }
                    except Exception as exc:
                        res["notes"].append(f"super.img probe failed: {exc}")
                    continue

                fw_slot = _classify_firmware_stem(stem)
                if fw_slot and zone_norm != "super":
                    res["firmware"][fw_slot] = {
                        "path": p,
                        "fileName": base,
                        "size": sz,
                        "origin": "file",
                    }
                    continue

                # Try super probe if unknown filename might be a renamed super.img
                fs_t = None
                try:
                    fs_t = detect_fs(p)
                except Exception:
                    fs_t = None

                if fs_t:
                    res["super_children"][stem] = {
                        "path": p,
                        "fileName": base,
                        "size": sz,
                        "fsType": str(fs_t).upper(),
                        "origin": "file",
                    }
                else:
                    # Fallback magic check for boot/vbmeta/super with non-standard filename
                    try:
                        sinfo = native_backend.vdev_probe_super(p, slot=0) or {}
                        sparts = [pt for pt in (sinfo.get("partitions") or []) if pt.get("fs_type") in ("EXT", "EROFS")]
                    except Exception:
                        sparts = []
                    if sparts:
                        res["super_path"] = p
                        for pt in sparts:
                            pname = _strip_slot(pt.get("name", ""))
                            res["super_children"][pname] = {
                                "path": p,
                                "fileName": f"{pname} (super)",
                                "size": int(pt.get("size") or 0),
                                "fsType": str(pt.get("fs_type") or "").upper(),
                                "origin": "super",
                            }
                    else:
                        from core.header_magic import check_magic
                        try:
                            magic = check_magic(p)
                        except Exception:
                            magic = ""
                        if magic == "VNDRBOOT":
                            res["firmware"]["vendor_boot"] = {"path": p, "fileName": base, "size": sz, "origin": "file"}
                        elif magic == "BOOT":
                            res["firmware"]["boot"] = {"path": p, "fileName": base, "size": sz, "origin": "file"}
                        elif fw_slot:
                            res["firmware"][fw_slot] = {"path": p, "fileName": base, "size": sz, "origin": "file"}
                        else:
                            res["notes"].append(f"Unrecognised image format: {base}")

            # If a folder import found super.img inside it, probe it now
            if res["super_path"] and not any(v.get("origin") == "super" for v in res["super_children"].values()):
                try:
                    sinfo = native_backend.vdev_probe_super(res["super_path"], slot=0) or {}
                    for part in (sinfo.get("partitions") or []):
                        pname = _strip_slot(part.get("name", ""))
                        fst = str(part.get("fs_type") or "").upper()
                        if pname and fst in ("EXT", "EROFS"):
                            res["super_children"].setdefault(pname, {
                                "path": res["super_path"],
                                "fileName": f"{pname} (super)",
                                "size": int(part.get("size") or 0),
                                "fsType": fst,
                                "origin": "super",
                            })
                except Exception as exc:
                    res["notes"].append(f"super.img probe failed: {exc}")

            return True, res

        def done(ok, result):
            self._staging_busy = False
            if not ok or not isinstance(result, dict):
                self._staging_notice = str(result or "Failed to stage files")
                self.stagingChanged.emit()
                return

            if result.get("zip_path"):
                # Replacing base ZIP clears old ZIP-derived entries while keeping manual overrides clean
                self._zip_path = result["zip_path"]
                self._super_path = ""
                self._firmware_staged = {
                    k: v for k, v in self._firmware_staged.items() if v.get("origin") != "zip"
                }
                self._super_children = {
                    k: v for k, v in self._super_children.items() if v.get("origin") != "zip"
                }

            if result.get("super_path"):
                # Switching to direct super.img clears any previously staged base ZIP
                self._zip_path = ""
                self._super_path = result["super_path"]
                self._firmware_staged = {
                    k: v for k, v in self._firmware_staged.items() if v.get("origin") != "zip"
                }
                self._super_children = {
                    k: v for k, v in self._super_children.items()
                    if v.get("origin") not in ("super", "zip")
                }
            elif not result.get("zip_path") and "system" in (result.get("super_children") or {}):
                # User explicitly staged direct system.img / ROM folder -> drop old base ZIP so Boot never re-extracts it!
                if self._zip_path:
                    self._zip_path = ""
                    self._firmware_staged = {
                        k: v for k, v in self._firmware_staged.items() if v.get("origin") != "zip"
                    }
                    self._super_children = {
                        k: v for k, v in self._super_children.items() if v.get("origin") != "zip"
                    }

            for k, v in (result.get("firmware") or {}).items():
                self._firmware_staged[k] = v
            for k, v in (result.get("super_children") or {}).items():
                self._super_children[k] = v

            notes = result.get("notes") or []
            self._staging_notice = "; ".join(notes[:2])
            self.stagingChanged.emit()

        self._jobs.submit("vdev-stage", work, None, done)

    # ── Live Boot Execution (VirtualFS + Streaming Logcat) ──
    @Slot()
    def startBoot(self):
        if self._running or not self.canBoot:
            return
        if self._qemu_running or self._qemu_preparing:
            self.stopQemuBoot()

        staged = self._current_staged_payload()
        self._run_seq += 1
        seq = self._run_seq

        self._running = True
        self._has_report = False
        self._error_text = ""
        self._progress_text = "Initializing virtual device…"
        self._progress_percent = -1
        self._verdict_status = "pending"
        self._verdict_summary = ""
        self._profile_summary = ""
        self._reaches_system_server = False
        self._blocker_count = 0
        self._warn_count = 0
        self._pass_count = 0
        self._phases = []
        self._autofix = []
        self._raw_autofix_recipes = {}
        self._autofix_diff_recipe_id = ""
        self._autofix_diff_text = ""
        self._last_report = None
        self._last_layout = None
        self._raw_report_dict = None
        self._logic_map_raw = {}
        self._daemon_audit_raw = []
        self._hardware_raw = {}
        self._kernel_config_raw = {}
        self._kernel_hw_raw = {}
        self._logic_map_summary = ""
        self._logic_map_all_items = []
        self._logic_map_filtered = []

        with self._vfs_lock:
            self._vfs = None
            self._ramdisk_vfs = None
        self._vfs_ready = False
        self._vfs_current_path = "/"
        self._vfs_mounts = []
        self._vfs_entries = []
        self._vfs_preview_path = ""
        self._vfs_preview_text = ""

        self._logcat_lines = [
            {
                "ts": _logcat_ts(),
                "pid": "0",
                "tid": "0",
                "level": "I",
                "tag": "bootloader",
                "msg": f"Starting virtual Android boot ({self.stagedSummary or 'staged partitions'})",
                "detail": "",
                "path": "",
                "stage": "bootloader",
                "code": "boot-start",
            }
        ]
        self._rebuild_filtered_logcat()

        self.stateChanged.emit()
        self.vfsChanged.emit()
        self.logcatChanged.emit()
        self.bootStarted.emit()

        permissive = self._permissive
        avb_enabled = self._avb_enabled
        # Always ON per user preference
        opts = {
            "staged": staged,
            "permissive": permissive,
            "vbmeta_enabled": avb_enabled,
            "dex_verify": True,
            # Full multi-GB dm-verity hashtree block hashing pegs disk at 100% for minutes;
            # keep AVB vbmeta chain/descriptor/boot-hash verification ON without thrashing disk!
            "verify_hashtree": False,
        }
        port_dir = staged["zip_path"] or staged["super_path"] or self._active_project_base() or "."

        _last_logged_prog = [""]

        def on_progress(text: str):
            if self._run_seq != seq:
                return
            msg = str(text or "")
            self._progressSignal.emit(msg)
            # Stream major non-noisy milestones directly into Live Logcat in real time
            # so Logcat updates live line-by-line instead of dumping 20 lines all at once!
            low = msg.lower()
            is_milestone = (
                low.startswith(("probing ", "inspecting ", "checking partition ", "indexing ", "mounting ", "stage "))
                and msg != _last_logged_prog[0]
            )
            if is_milestone:
                _last_logged_prog[0] = msg
                tag = "bootloader" if ("boot" in low or "dtbo" in low or "firmware" in low) else ("fs_mgr" if ("partition" in low or "mount" in low or "indexing" in low) else "BootAnalyzer")
                self._logcatBatchSignal.emit((seq, [{
                    "ts": _logcat_ts(), "pid": "1", "tid": "1", "level": "I", "tag": tag,
                    "msg": msg, "detail": "", "path": "", "stage": "progress", "code": "live-progress",
                }]))

        def on_vfs(vfs, ramdisk_vfs, layout, fstab, fstab_origin):
            if self._run_seq != seq:
                return
            with self._vfs_lock:
                self._vfs = vfs
                self._ramdisk_vfs = ramdisk_vfs
                self._last_layout = layout
            self._vfsReadySignal.emit()

            batch = []
            ts = _logcat_ts()
            if layout.boot_inspections:
                for bname, brep in layout.boot_inspections.items():
                    hdr = brep.get("header") or {}
                    hv = hdr.get("header_version", "?")
                    rds = len(brep.get("ramdisks") or [])
                    batch.append({
                        "ts": ts, "pid": "0", "tid": "0", "level": "I", "tag": "bootloader",
                        "msg": f"Loaded {bname}.img (header v{hv}, ramdisks={rds})",
                        "detail": hdr.get("cmdline") or "", "path": "", "stage": "bootloader", "code": "boot-hdr",
                    })
            if layout.vbmeta_reports:
                batch.append({
                    "ts": ts, "pid": "0", "tid": "0", "level": "I", "tag": "avb",
                    "msg": f"Verified {len(layout.vbmeta_reports)} vbmeta descriptor chain(s): {', '.join(sorted(layout.vbmeta_reports.keys()))}",
                    "detail": "", "path": "", "stage": "avb", "code": "avb-loaded",
                })
            if getattr(layout, "dtbo_report", None):
                dr = layout.dtbo_report
                batch.append({
                    "ts": ts, "pid": "0", "tid": "0",
                    "level": "I" if dr.get("valid") else "E",
                    "tag": "bootloader",
                    "msg": f"Loaded dtbo.img (v{dr.get('version', 0)}, {dr.get('valid_fdt_count', 0)}/{dr.get('entry_count', 0)} FDT overlays)",
                    "detail": ", ".join((dr.get("all_compatibles") or [])[:4]),
                    "path": "", "stage": "kernel", "code": "dtbo-loaded",
                })
            for m in sorted(vfs.mounts, key=lambda x: x.point):
                if m.point.startswith("/apex/"):
                    continue
                batch.append({
                    "ts": ts, "pid": "1", "tid": "1", "level": "I", "tag": "fs_mgr",
                    "msg": f"Mounted {m.partition} -> {m.point} ({getattr(m.source, 'label', 'vfs')})",
                    "detail": fstab_origin or "", "path": m.point, "stage": "mount", "code": "vfs-mount",
                })
            apex_n = sum(1 for m in vfs.mounts if m.point.startswith("/apex/"))
            if apex_n:
                batch.append({
                    "ts": ts, "pid": "210", "tid": "210", "level": "I", "tag": "apexd",
                    "msg": f"Activated {apex_n} APEX package(s) under /apex",
                    "detail": "", "path": "/apex", "stage": "apex", "code": "apex-mount",
                })
            if batch:
                self._logcatBatchSignal.emit((seq, batch))

        def on_stage(stage_res, ctx):
            if self._run_seq != seq:
                return
            pid, tid, default_tag = _STAGE_LOGCAT_META.get(stage_res.key, ("1", "1", stage_res.key))
            ts = _logcat_ts()
            batch = []
            if not stage_res.ran and stage_res.skipped_reason:
                batch.append({
                    "ts": ts, "pid": pid, "tid": tid, "level": "I", "tag": default_tag,
                    "msg": f"[{stage_res.label}] skipped: {stage_res.skipped_reason}",
                    "detail": "", "path": "", "stage": stage_res.key, "code": "stage-skipped",
                })
            else:
                # Capture Kernel IKCFG, Hardware Bring-Up & native daemon C++ symbol audit for Logic Map Explorer
                if stage_res.key == "kernel" and ctx is not None:
                    self._kernel_config_raw = dict(ctx.shared.get("kernel_config") or {})
                    self._kernel_hw_raw = dict(ctx.shared.get("kernel_hardware") or {})
                if stage_res.key == "hardware" and ctx is not None:
                    self._hardware_raw = dict(ctx.shared.get("hardware") or {})
                if stage_res.key == "linker" and ctx is not None:
                    self._daemon_audit_raw = list((ctx.shared.get("linker") or {}).get("daemon_symbol_audit") or ())
                # Stream real extracted SystemServer boot sequence into Live Logcat!
                if stage_res.key == "dex_bytecode" and ctx is not None:
                    lm = ((ctx.shared.get("dex_bytecode") or {}).get("logic_map") or {})
                    self._logic_map_raw = dict(lm)
                    for ph_key, ph_label in (
                        ("bootstrap_services", "StartBootstrapServices"),
                        ("core_services", "StartCoreServices"),
                        ("other_services", "StartOtherServices"),
                    ):
                        svcs = lm.get(ph_key) or []
                        if svcs:
                            short = ", ".join(s.rsplit(".", 1)[-1] for s in svcs[:8])
                            if len(svcs) > 8:
                                short += f" (+{len(svcs) - 8} more)"
                            batch.append({
                                "ts": ts, "pid": "724", "tid": "724", "level": "I", "tag": "SystemServer",
                                "msg": f"{ph_label} ({len(svcs)}): {short}",
                                "detail": "\n".join(svcs), "path": "/system/framework/services.jar",
                                "stage": stage_res.key, "code": f"dex-boot-{ph_key}",
                            })
                emitted_any = False
                for f in stage_res.findings:
                    lvl = _severity_to_logcat_level(f.severity, stage_res.key, f.code)
                    tag = _finding_to_tag(stage_res.key, f.code, default_tag)
                    msg = f.title
                    detail = f.detail or ""
                    if f.hint:
                        detail = (detail + "\nHint: " + f.hint).strip()
                    batch.append({
                        "ts": ts,
                        "pid": pid,
                        "tid": tid,
                        "level": lvl,
                        "tag": tag,
                        "msg": msg,
                        "detail": detail,
                        "path": f.path or "",
                        "stage": stage_res.key,
                        "code": f.code,
                    })
                    emitted_any = True
                if not emitted_any:
                    batch.append({
                        "ts": ts, "pid": pid, "tid": tid, "level": "I", "tag": default_tag,
                        "msg": f"{stage_res.label} completed cleanly ({stage_res.duration_ms} ms)",
                        "detail": "", "path": "", "stage": stage_res.key, "code": "stage-ok",
                    })
            if batch:
                self._logcatBatchSignal.emit((seq, batch))

        def work():
            t0 = time.perf_counter()
            from core.vdev import analyze
            report = analyze(
                port_dir,
                options=opts,
                progress=on_progress,
                on_vfs=on_vfs,
                on_stage=on_stage,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return True, (seq, report, elapsed_ms)

        def done(ok, payload):
            if not ok:
                self._running = False
                self._progress_text = ""
                self._error_text = str(payload or "Boot simulation failed")
                self._verdict_status = "error"
                self._append_logcat_batch_ui((seq, [{
                    "ts": _logcat_ts(), "pid": "1", "tid": "1", "level": "F", "tag": "BootAnalyzer",
                    "msg": self._error_text, "detail": "", "path": "", "stage": "fatal", "code": "error",
                }]))
                self.stateChanged.emit()
                return

            done_seq, report, elapsed_ms = payload
            if done_seq != self._run_seq:
                return
            self._apply_report(report, elapsed_ms)

        self._jobs.submit("vdev-boot", work, None, done)

    @staticmethod
    def _parse_percent(text: str) -> int:
        import re
        m = re.search(r"\((\d{1,3})%", str(text or ""))
        if m:
            return max(0, min(100, int(m.group(1))))
        return -1

    @Slot(str)
    def _apply_progress(self, text: str):
        if self._running:
            self._progress_text = text
            self._progress_percent = self._parse_percent(text)
            self.stateChanged.emit()

    @Slot()
    def _on_vfs_ready_ui(self):
        with self._vfs_lock:
            vfs = self._vfs
        if vfs is None:
            return
        mounts = []
        for m in sorted(vfs.mounts, key=lambda x: x.point):
            if m.point.startswith("/apex/"):
                continue
            mounts.append({
                "point": m.point,
                "partition": m.partition,
                "sourceLabel": getattr(m.source, "label", "vfs"),
            })
        self._vfs_mounts = mounts
        self._vfs_ready = True
        self._vfs_current_path = "/"
        self._vfs_selected_path = ""
        self._vfs_reveal_row = -1
        self._vfs_expanded = {"/"}
        self._vfs_preview_path = ""
        self._vfs_preview_text = ""
        self._rebuild_vfs_tree()
        self.vfsChanged.emit()

    @Slot(object)
    def _append_logcat_batch_ui(self, payload):
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        seq, batch = payload
        if seq != self._run_seq or not batch:
            return
        self._logcat_lines.extend(batch)
        self._rebuild_filtered_logcat()
        self.logcatChanged.emit()

    def _apply_report(self, report, elapsed_ms: int):
        self._running = False
        self._progress_text = ""
        self._progress_percent = -1
        self._duration_ms = elapsed_ms

        if report.error:
            self._has_report = False
            self._error_text = report.error
            self._verdict_status = "error"
            self._verdict_summary = report.error
            self._append_logcat_batch_ui((self._run_seq, [{
                "ts": _logcat_ts(), "pid": "1", "tid": "1", "level": "F", "tag": "init",
                "msg": report.error, "detail": "", "path": "", "stage": "error", "code": "report-error",
            }]))
            self.stateChanged.emit()
            return

        self._has_report = True
        self._error_text = ""
        self._last_report = report
        self._raw_report_dict = report.to_dict()

        blockers = int(report.blockers)
        warns = int(report.warns)
        passes = sum(
            1 for st in report.stages for f in st.findings
            if str(f.severity).lower() == "pass"
        )
        self._blocker_count = blockers
        self._warn_count = warns
        self._pass_count = passes

        bs = report.boot_simulation or {}
        self._reaches_system_server = bool(bs.get("reaches_system_server", blockers == 0))
        if blockers > 0 or not self._reaches_system_server:
            self._verdict_status = "error"
        elif warns > 0:
            self._verdict_status = "warn"
        else:
            self._verdict_status = "ok"

        self._verdict_summary = bs.get("summary") or bs.get("verdict") or (
            "Reached SystemServer cleanly" if self._reaches_system_server else "Boot halted before SystemServer"
        )
        self._profile_summary = _build_profile_summary(report.profile or {}, report.mounts or [])

        phases_out = []
        for idx, ph in enumerate(bs.get("phases") or [], start=1):
            raw_st = str(ph.get("status", "pass")).lower()
            if raw_st in ("blocker", "fail", "error"):
                sym = "status_error"
                st = "error"
            elif raw_st in ("warn", "warning"):
                sym = "status_warn"
                st = "warn"
            else:
                sym = "status_check"
                st = "ok"
            phases_out.append({
                "index": idx,
                "name": str(ph.get("label") or ph.get("name") or f"Phase {idx}"),
                "status": st,
                "symbol": sym,
                "summary": "; ".join(ph.get("checks") or []) if isinstance(ph.get("checks"), list) else str(ph.get("summary", "")),
            })
        self._phases = phases_out
        raw_af = list(report.autofix or [])
        self._raw_autofix_recipes = {str(r.get("id", "")): r for r in raw_af}
        self._autofix = [_format_autofix_recipe(r) for r in raw_af]
        self._rebuild_logic_map_items()
        self.logicMapChanged.emit()
        self.autofixChanged.emit()

        # Append chronological verbose boot_trace + final prioritized Boot Verdict & Top Root Causes
        end_ts = _logcat_ts()
        final_batch: list[dict] = []

        boot_trace = list(getattr(report, "boot_trace", None) or ())
        if boot_trace:
            final_batch.append({
                "ts": end_ts,
                "pid": "1",
                "tid": "1",
                "level": "I",
                "tag": "BootTrace",
                "msg": f"─── Detailed Chronological Boot Trace ({len(boot_trace)} entries) ───",
                "detail": "Toggle 'Verbose' in the Logcat bar to show/hide full property, VINTF, SELinux CIL, init trigger, graphics, daemon .dynsym, ART .vdex, and DEX details.",
                "path": "",
                "stage": "boot_trace",
                "code": "trace-header",
                "verbose": True,
            })
            for item in boot_trace:
                final_batch.append({
                    "ts": end_ts,
                    "pid": str(item.get("pid") or "1"),
                    "tid": str(item.get("tid") or item.get("pid") or "1"),
                    "level": str(item.get("level") or "I"),
                    "tag": str(item.get("tag") or "init"),
                    "msg": str(item.get("msg") or ""),
                    "detail": str(item.get("detail") or ""),
                    "path": str(item.get("path") or ""),
                    "stage": str(item.get("stage") or "boot_trace"),
                    "code": str(item.get("code") or "trace"),
                    "verbose": bool(item.get("verbose", True)),
                })

        root_causes = list(getattr(report, "root_causes", None) or ())
        rc_blockers = [c for c in root_causes if str(c.get("severity", "")).lower() == "blocker"]
        rc_warns = [c for c in root_causes if str(c.get("severity", "")).lower() == "warn"]
        if rc_blockers or rc_warns:
            rc_lines = []
            if rc_blockers:
                rc_lines.append(f"BLOCKERS ({len(rc_blockers)}) — highest priority bootloop causes:")
                for c in rc_blockers:
                    loc = f" @{c['path']}" if c.get("path") else ""
                    rc_lines.append(f"  #{c.get('rank', '?')} [{c.get('stage')}/{c.get('code')}] {c.get('title')}{loc}")
                    if c.get("hint"):
                        rc_lines.append(f"      Fix: {c['hint']}")
            if rc_warns:
                rc_lines.append(f"WARNINGS ({len(rc_warns)}):")
                for c in rc_warns:
                    loc = f" @{c['path']}" if c.get("path") else ""
                    rc_lines.append(f"  #{c.get('rank', '?')} [{c.get('stage')}/{c.get('code')}] {c.get('title')}{loc}")
            final_batch.append({
                "ts": end_ts,
                "pid": "724",
                "tid": "724",
                "level": "E" if rc_blockers else "W",
                "tag": "BootVerdict",
                "msg": f"Top Root Causes: {len(rc_blockers)} blocker(s), {len(rc_warns)} warning(s)",
                "detail": "\n".join(rc_lines),
                "path": rc_blockers[0].get("path", "") if rc_blockers else (rc_warns[0].get("path", "") if rc_warns else ""),
                "stage": "verdict",
                "code": "root-causes-summary",
                "verbose": False,
            })

        # Final SystemServer summary logcat line
        final_lvl = "I" if self._reaches_system_server else "F"
        phase_detail = "\n".join(
            f"  [{p['status'].upper():<5}] {p['name']}: {p['summary']}"
            for p in phases_out
        )
        verdict_detail = (
            (self._profile_summary + "\n" if self._profile_summary else "")
            + f"Counters: {passes} PASS · {warns} WARN · {blockers} BLOCKER\n"
            + phase_detail
        ).strip()
        final_batch.append({
            "ts": end_ts,
            "pid": "724",
            "tid": "724",
            "level": final_lvl,
            "tag": "SystemServer",
            "msg": f"{self._verdict_summary} ({elapsed_ms / 1000.0:.2f}s)",
            "detail": verdict_detail,
            "path": "",
            "stage": "system_server",
            "code": "boot-complete",
            "verbose": False,
        })
        self._append_logcat_batch_ui((self._run_seq, final_batch))

        # Build Kernel-Contract Hardware Gate from Rust+Python static analysis so ▶ Play obeys real kernel+vendor
        try:
            from core.vdev.qemu_runner import build_hardware_gate
            self._hardware_gate = build_hardware_gate(
                report=report,
                kernel_config=self._kernel_config_raw,
                kernel_hw=self._kernel_hw_raw,
                hardware_audit=self._hardware_raw,
            )
        except Exception:
            self._hardware_gate = {}

        self.stateChanged.emit()
        self.qemuChanged.emit()

    # ── Virtual Mounted Filesystem Expandable FileTree ──
    def _list_vfs_children(self, vfs, base: str, mount_map: dict) -> list[dict]:
        names = vfs.listdir(base) or []
        node_dir = vfs.lookup(base)
        raw_dir_entries = node_dir.source.entries(node_dir.rel) if (node_dir and node_dir.is_dir) else {}

        dirs = []
        files = []
        for name in names:
            child_vp = ("/" + name) if base == "/" else (base + "/" + name)
            m = mount_map.get(child_vp)
            is_mount = m is not None
            raw_ent = raw_dir_entries.get(name)
            link_target = ""
            if raw_ent is not None and raw_ent[0] == "file" and node_dir is not None:
                rel = f"{node_dir.rel}/{name}" if node_dir.rel else name
                try:
                    lt = node_dir.source.link_target(rel, raw_ent[1])
                    if lt:
                        link_target = lt
                except Exception:
                    pass

            resolved = vfs.lookup(child_vp)
            badge = ""
            if is_mount:
                kind = "dir"
                size = 0
                part = m.partition
                fs_t = str(getattr(m.source, "fs_type", "") or "").upper()
                if child_vp.startswith("/apex/"):
                    badge = "APEX"
                elif fs_t in ("EROFS", "EXT", "EXT4"):
                    badge = fs_t
                elif getattr(m.source, "label", "") == "folder":
                    badge = "DIR"
                else:
                    badge = "MOUNT"
            elif resolved is not None:
                kind = "dir" if resolved.is_dir else "file"
                size = int(getattr(resolved, "size", 0) or 0)
                part = resolved.partition or ""
            elif raw_ent is not None:
                kind = raw_ent[0]
                size = int(raw_ent[1] or 0)
                part = node_dir.partition if node_dir else ""
            else:
                kind = "link" if link_target else "file"
                size = 0
                part = ""

            entry = {
                "name": name,
                "vpath": child_vp,
                "kind": kind,
                "isLink": bool(link_target),
                "linkTarget": link_target,
                "isMount": is_mount,
                "partition": part,
                "badge": badge,
                "iconKey": _file_icon_key(name, kind, is_mount=is_mount, is_link=bool(link_target), vpath=child_vp),
                "sizeLabel": "" if kind == "dir" else _fmt_size(size),
            }
            if kind == "dir":
                dirs.append(entry)
            else:
                files.append(entry)

        dirs.sort(key=lambda e: (0 if e["isMount"] else 1, e["name"].lower()))
        files.sort(key=lambda e: e["name"].lower())
        return dirs + files

    def _rebuild_vfs_tree(self):
        with self._vfs_lock:
            vfs = self._vfs
        if vfs is None:
            self._vfs_entries = []
            return

        mount_map = {m.point: m for m in vfs.mounts}
        rows: list[dict] = []
        max_rows = 2500

        def walk(dir_vp: str, depth: int, ancestor_lines: list[bool]):
            if len(rows) >= max_rows:
                return
            children = self._list_vfs_children(vfs, dir_vp, mount_map)
            total = len(children)
            for idx, item in enumerate(children):
                if len(rows) >= max_rows:
                    break
                is_last = (idx == total - 1)
                is_dir = item["kind"] == "dir"
                expanded = is_dir and (item["vpath"] in self._vfs_expanded)
                row = dict(item)
                row["depth"] = depth
                row["expanded"] = expanded
                row["branchLines"] = list(ancestor_lines)
                row["lastSibling"] = is_last
                rows.append(row)
                if expanded:
                    walk(item["vpath"], depth + 1, ancestor_lines + [not is_last])

        walk("/", 0, [])
        self._vfs_entries = rows
        self._vfs_reveal_row = -1
        if self._vfs_selected_path:
            for i, r in enumerate(rows):
                if r["vpath"] == self._vfs_selected_path:
                    self._vfs_reveal_row = i
                    break

    @Slot(str)
    def vfsToggle(self, vpath: str):
        norm = posixpath.normpath(str(vpath or "/"))
        if not norm.startswith("/"):
            norm = "/" + norm
        if norm in self._vfs_expanded:
            self._vfs_expanded.discard(norm)
        else:
            self._vfs_expanded.add(norm)
        self._vfs_selected_path = norm
        self._vfs_current_path = norm
        self._rebuild_vfs_tree()
        self.vfsChanged.emit()

    @Slot()
    def vfsCollapseAll(self):
        self._vfs_expanded = {"/"}
        self._vfs_current_path = "/"
        self._vfs_selected_path = ""
        self._rebuild_vfs_tree()
        self.vfsChanged.emit()

    @Slot(str)
    def vfsOpen(self, vpath: str):
        with self._vfs_lock:
            vfs = self._vfs
        if vfs is None or not vpath:
            return
        norm = posixpath.normpath(str(vpath))
        if not norm.startswith("/"):
            norm = "/" + norm

        # Ensure every ancestor folder is expanded so node is visible in the tree
        parts = [p for p in norm.split("/") if p]
        acc = ""
        self._vfs_expanded.add("/")
        for seg in parts[:-1]:
            acc += "/" + seg
            self._vfs_expanded.add(acc)

        node = vfs.lookup(norm)
        self._vfs_selected_path = norm
        if norm == "/":
            self._vfs_current_path = "/"
            self._vfs_preview_path = ""
            self._vfs_preview_text = ""
            self._rebuild_vfs_tree()
            self.vfsChanged.emit()
            return

        if node is not None and node.is_dir:
            self._vfs_expanded.add(norm)
            self._vfs_current_path = norm
            self._vfs_preview_path = ""
            self._vfs_preview_text = ""
            self._rebuild_vfs_tree()
            self.vfsChanged.emit()
            return

        if node is not None and node.is_file:
            self._vfs_current_path = posixpath.dirname(norm) or "/"
            self._rebuild_vfs_tree()
            base_low = posixpath.basename(norm).lower()
            _, ext = os.path.splitext(base_low)
            if base_low in _PREVIEWABLE_NAMES or ext in _PREVIEWABLE_EXTS or base_low.startswith("fstab."):
                raw = vfs.read_bytes(norm, 16384)
                if raw is not None:
                    text = raw.decode("utf-8", errors="replace")
                    if len(raw) >= 16384:
                        text += "\n… (truncated at 16 KB)"
                    self._vfs_preview_path = norm
                    self._vfs_preview_text = text
                else:
                    self._vfs_preview_path = norm
                    self._vfs_preview_text = "(Unable to read file)"
            else:
                ctx_str = vfs.selinux(norm) or "unlabeled"
                self._vfs_preview_path = norm
                self._vfs_preview_text = (
                    f"Binary / structured file\n"
                    f"Path      : {norm}\n"
                    f"Partition : {node.partition}\n"
                    f"Size      : {_fmt_size(node.size)} ({node.size:,} bytes)\n"
                    f"SELinux   : {ctx_str}"
                )
            self.vfsChanged.emit()

    @Slot()
    def vfsUp(self):
        cur = self._vfs_current_path or "/"
        if cur == "/":
            return
        parent = posixpath.dirname(cur) or "/"
        self.vfsOpen(parent)

    @Slot()
    def vfsClosePreview(self):
        if self._vfs_preview_path or self._vfs_preview_text:
            self._vfs_preview_path = ""
            self._vfs_preview_text = ""
            self.vfsChanged.emit()

    # ── Logcat Filtering & Clipboard ──
    @Slot(str)
    def setLogcatLevelFilter(self, level: str):
        lv = str(level or "all")
        if self._logcat_level_filter != lv:
            self._logcat_level_filter = lv
            self._rebuild_filtered_logcat()
            self.logcatChanged.emit()

    @Slot(str)
    def setLogcatQuery(self, query: str):
        q = str(query or "")
        if self._logcat_query != q:
            self._logcat_query = q
            self._rebuild_filtered_logcat()
            self.logcatChanged.emit()

    @Slot(bool)
    def setLogcatVerbose(self, value: bool):
        v = bool(value)
        if self._logcat_verbose != v:
            self._logcat_verbose = v
            self._rebuild_filtered_logcat()
            self.logcatChanged.emit()

    @Slot()
    def copyLogcat(self):
        lines = []
        for r in self._filtered_logcat_cache:
            lines.append(f"{r['ts']}  {r['pid']:>4s}  {r['tid']:>4s} {r['level']} {r['tag']}: {r['msg']}")
            if r.get("detail"):
                for dl in str(r["detail"]).splitlines():
                    lines.append(f"    {dl}")
        self.copyText("\n".join(lines))

    @Slot(str)
    def copyText(self, text: str):
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(str(text or ""))

    @Slot(str)
    def exportJson(self, dest_url_or_path: str):
        dest = _url_to_path(dest_url_or_path)
        if not dest or self._raw_report_dict is None:
            return
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(self._raw_report_dict, fh, indent=2, ensure_ascii=False)
            self._progress_text = f"Exported {os.path.basename(dest)}"
            self.stateChanged.emit()
        except Exception as exc:
            self._error_text = f"Export failed: {exc}"
            self.stateChanged.emit()

    # ── Diff vs Stock ──
    @Slot("QVariantList")
    def runDiffUrls(self, urls):
        for item in (urls or []):
            p = _url_to_path(item)
            if p and (os.path.isfile(p) or os.path.isdir(p)):
                self.runDiffWithStock(p)
                return

    @Slot(str)
    def runDiffWithStock(self, stock_url_or_path: str):
        stock = _url_to_path(stock_url_or_path)
        if not stock or not (os.path.isdir(stock) or os.path.isfile(stock)):
            return
        if not self.canBoot or self._diff_running:
            return

        self._stock_path = stock
        self._diff_running = True
        self._diff_error = ""
        self._diff_summary = "Comparing against stock…"
        self._diff_regressions = []
        self._diff_resolved = []
        self._diff_prop_drifts = []
        self._diff_partition_deltas = []
        self.diffChanged.emit()

        staged = self._current_staged_payload()
        port_dir = staged["zip_path"] or staged["super_path"] or self._active_project_base() or "."
        opts = {
            "staged": staged,
            "permissive": self._permissive,
            "vbmeta_enabled": self._avb_enabled,
            "dex_verify": True,
            "verify_hashtree": self._avb_enabled,
        }

        def work():
            from core.vdev import analyze
            from core.vdev.diff import diff_reports
            stock_opts = {
                "permissive": self._permissive,
                "vbmeta_enabled": self._avb_enabled,
                "dex_verify": True,
                "verify_hashtree": self._avb_enabled,
            }
            stock_rep = analyze(stock, options=stock_opts)
            port_rep = analyze(port_dir, options=opts)
            return True, diff_reports(stock_rep, port_rep)

        def done(ok, result):
            self._diff_running = False
            if not ok or not isinstance(result, dict):
                self._diff_error = str(result or "Diff failed")
                self._diff_summary = ""
                self.diffChanged.emit()
                return

            regs = result.get("regressions") or []
            resv = result.get("resolved") or []
            drifts = result.get("prop_drifts") or []
            pdelta = result.get("partition_deltas") or {}
            self._diff_regressions = [
                {
                    "stage": str(x.get("stage", "")),
                    "status": "error" if str(x.get("severity", "")).lower() == "blocker" else "warn",
                    "title": str(x.get("title", "")),
                    "detail": str(x.get("detail", "")),
                }
                for x in regs
            ]
            self._diff_resolved = [
                {
                    "stage": str(x.get("stage", "")),
                    "status": "ok",
                    "title": str(x.get("title", "")),
                    "detail": str(x.get("detail", "")),
                }
                for x in resv
            ]
            self._diff_prop_drifts = [
                {
                    "key": str(d.get("key", "")),
                    "stock": str(d.get("stock", "")),
                    "port": str(d.get("port", "")),
                }
                for d in drifts
            ]
            deltas = []
            for p in pdelta.get("added") or []:
                deltas.append({"kind": "added", "name": str(p)})
            for p in pdelta.get("removed") or []:
                deltas.append({"kind": "removed", "name": str(p)})
            self._diff_partition_deltas = deltas
            self._diff_summary = f"{len(regs)} regression(s) · {len(resv)} resolved · {len(drifts)} prop drift(s)"
            self.diffChanged.emit()

        self._jobs.submit("vdev-diff", work, None, done)

    # ── Interactive Logic Map Explorer (DEX Semantic Logic + Native Daemons) ──
    def _rebuild_logic_map_items(self):
        lm = self._logic_map_raw or {}
        daemons = self._daemon_audit_raw or []
        items: list[dict] = []

        for ph_key, badge in (
            ("bootstrap_services", "BOOTSTRAP"),
            ("core_services", "CORE"),
            ("other_services", "OTHER"),
        ):
            for svc in (lm.get(ph_key) or []):
                items.append({
                    "category": "services",
                    "badge": badge,
                    "status": "ok",
                    "title": str(svc),
                    "detail": f"SystemServer {badge.lower()} service phase",
                    "vfsPath": "/system/framework/services.jar",
                })

        for so in (lm.get("missing_jni") or []):
            items.append({
                "category": "jni",
                "badge": "MISSING .SO",
                "status": "warn",
                "title": str(so),
                "detail": "Unguarded System.loadLibrary() target absent from VFS/APEX",
                "vfsPath": "/system/lib64",
            })
        for so in (lm.get("missing_jni_guarded") or []):
            items.append({
                "category": "jni",
                "badge": "GUARDED .SO",
                "status": "ok",
                "title": str(so),
                "detail": "Guarded by try/catch (safe optional OEM probe)",
                "vfsPath": "/system/lib64",
            })
        for sym in (lm.get("missing_jni_symbols") or []):
            cls_s = str(sym.get("class_name", "")).strip("L;").replace("/", ".")
            meths = ", ".join(sym.get("methods") or ())
            libs = "+".join(sym.get("libs") or ())
            items.append({
                "category": "jni",
                "badge": "JNI SYM",
                "status": "error" if (not sym.get("guarded") and sym.get("boot_critical")) else "warn",
                "title": f"{cls_s}.{meths}",
                "detail": f"Static Java_* symbol missing in {libs}",
                "vfsPath": str(sym.get("source") or ""),
            })

        for hal in (lm.get("undeclared_blocking_hals") or []):
            items.append({
                "category": "binder",
                "badge": "HAL WAIT",
                "status": "warn",
                "title": str(hal),
                "detail": "Blocking ServiceManager wait not declared in vendor/odm VINTF manifest",
                "vfsPath": "/vendor/etc/vintf/manifest.xml",
            })

        for rm in (lm.get("missing_reflection_members") or []):
            items.append({
                "category": "reflection",
                "badge": "REFLECT",
                "status": "warn",
                "title": str(rm),
                "detail": "Unguarded getMethod/getField lookup missing on resolved target class",
                "vfsPath": "/system/framework/services.jar",
            })

        for d in daemons:
            miss_n = int(d.get("missing_count") or len(d.get("missing_symbols") or ()))
            complete = bool(d.get("complete"))
            tier = str(d.get("tier") or "subsystem")
            st = "error" if (miss_n and tier == "critical") else ("warn" if miss_n else "ok")
            detail = (
                f"Missing {miss_n} symbol(s): {', '.join((d.get('missing_symbols') or [])[:4])}"
                if miss_n else
                (f"All symbols resolved ({d.get('libs_scanned', 0)} transitive libs)" if complete
                 else f"Partial closure ({d.get('libs_scanned', 0)} libs scanned)")
            )
            items.append({
                "category": "daemons",
                "badge": "CRITICAL" if tier == "critical" else "DAEMON",
                "status": st,
                "title": str(d.get("binary") or ""),
                "detail": detail,
                "vfsPath": str(d.get("binary") or ""),
            })

        hw_subs = (self._hardware_raw or {}).get("subsystems") or {}
        hw_vfs_map = {
            "display": "/vendor/lib64/egl",
            "audio": "/vendor/etc",
            "camera_media": "/vendor/etc/media_codecs.xml",
            "ril": "/vendor/bin/hw",
            "connectivity": "/vendor/bin/hw",
            "biometrics": "/vendor/bin/hw",
            "kernel_drivers": "/vendor_dlkm/lib/modules",
            "ueventd": "/vendor/etc/ueventd.rc",
        }
        for sub_name, info in hw_subs.items():
            if not isinstance(info, dict):
                continue
            raw_st = str(info.get("status") or "na")
            if raw_st == "na":
                continue
            st = "error" if raw_st == "fail" else ("warn" if raw_st == "warn" else "ok")
            items.append({
                "category": "hardware",
                "badge": "HARDWARE",
                "status": st,
                "title": sub_name.replace("_", " / ").title(),
                "detail": str(info.get("detail") or ""),
                "vfsPath": hw_vfs_map.get(sub_name, "/vendor/etc"),
            })

        khw = self._kernel_hw_raw or {}
        if khw.get("ko_total"):
            kmi = khw.get("kmi") or {}
            crcs = kmi.get("kmi_crcs") or []
            fw = khw.get("firmware") or {}
            items.append({
                "category": "kernel",
                "badge": "KMI CRC" if len(crcs) <= 1 else "KMI ERR",
                "status": "error" if len(crcs) > 1 or kmi.get("sym_mismatches") else "ok",
                "title": f"{khw.get('ko_total', 0)} .ko modules · module_layout={', '.join(crcs[:2]) or 'n/a'}",
                "detail": (
                    f"{khw.get('of_alias_count', 0)} OF aliases · "
                    f"{len(khw.get('matched_of_compatibles') or ())} DTB↔.ko matched · "
                    f"{fw.get('found', 0)}/{fw.get('declared', 0)} firmware blobs"
                ),
                "vfsPath": "/vendor_dlkm/lib/modules",
            })

        kcfg_flags = (self._kernel_config_raw or {}).get("flags") or {}
        for kname, kval in sorted(kcfg_flags.items()):
            st = "ok" if kval in ("y", "m") else "warn"
            items.append({
                "category": "kernel",
                "badge": f"={kval}",
                "status": st,
                "title": str(kname),
                "detail": "Extracted from boot.img embedded IKCFG .config",
                "vfsPath": "",
            })

        self._logic_map_all_items = items
        parts = []
        if lm:
            parts.append(f"{lm.get('system_services_count', 0)} SystemServer")
            parts.append(f"{lm.get('jni_libraries_count', 0)} JNI")
            parts.append(f"{lm.get('reflection_members_count', 0)} Reflection")
            parts.append(f"{lm.get('binder_calls_count', 0)} Binder/HAL")
            if lm.get("static_strings_resolved"):
                parts.append(f"{lm['static_strings_resolved']} static-const")
            if lm.get("dead_branches_pruned"):
                parts.append(f"{lm['dead_branches_pruned']} dead-branches pruned")
        if daemons:
            parts.append(f"{len(daemons)} native daemons")
        if hw_subs:
            active_hw = sum(1 for v in hw_subs.values() if isinstance(v, dict) and v.get("status") != "na")
            if active_hw:
                parts.append(f"{active_hw} hardware subsystems")
        if kcfg_flags:
            parts.append(f"{(self._kernel_config_raw or {}).get('total', len(kcfg_flags))} kernel configs")
        self._logic_map_summary = " · ".join(parts)
        self._filter_logic_map()

    def _filter_logic_map(self):
        cat = self._logic_map_category
        q = self._logic_map_query.strip().lower()
        out = []
        for it in self._logic_map_all_items:
            if cat != "all" and it["category"] != cat:
                continue
            if q and q not in (it["title"] + " " + it["detail"] + " " + it["badge"]).lower():
                continue
            out.append(it)
        self._logic_map_filtered = out[:400]

    @Property(str, notify=logicMapChanged)
    def logicMapSummary(self):
        return self._logic_map_summary

    @Property(str, notify=logicMapChanged)
    def logicMapCategory(self):
        return self._logic_map_category

    @Property(str, notify=logicMapChanged)
    def logicMapQuery(self):
        return self._logic_map_query

    @Property("QVariantList", notify=logicMapChanged)
    def logicMapItems(self):
        return self._logic_map_filtered

    @Property(int, notify=logicMapChanged)
    def logicMapCount(self):
        return len(self._logic_map_all_items)

    @Slot(str)
    def setLogicMapCategory(self, cat: str):
        c = str(cat or "all")
        if self._logic_map_category != c:
            self._logic_map_category = c
            self._filter_logic_map()
            self.logicMapChanged.emit()

    @Slot(str)
    def setLogicMapQuery(self, query: str):
        q = str(query or "")
        if self._logic_map_query != q:
            self._logic_map_query = q
            self._filter_logic_map()
            self.logicMapChanged.emit()

    @Slot(str)
    def jumpToVfs(self, vpath: str):
        vp = str(vpath or "").strip()
        if not vp or not self._vfs_ready:
            return
        self.vfsOpen(vp)
        self.jumpToLiveBootRequested.emit()

    # ── Real Logcat / dmesg / ramoops Correlator ──
    @Property(bool, notify=correlatorChanged)
    def correlatorBusy(self):
        return self._correlator_busy

    @Property(str, notify=correlatorChanged)
    def correlatorSource(self):
        return self._correlator_source

    @Property(str, notify=correlatorChanged)
    def correlatorSummary(self):
        return self._correlator_summary

    @Property(str, notify=correlatorChanged)
    def correlatorError(self):
        return self._correlator_error

    @Property("QVariantList", notify=correlatorChanged)
    def correlatorEvents(self):
        return self._correlator_events

    @Property(str, notify=correlatorChanged)
    def correlatorCil(self):
        return self._correlator_cil

    def _run_correlate(self, source_label: str, text_loader):
        if self._correlator_busy:
            return
        self._correlator_busy = True
        self._correlator_error = ""
        self._correlator_source = source_label
        self.correlatorChanged.emit()

        with self._vfs_lock:
            vfs = self._vfs
        rep = self._last_report

        def work():
            ok, payload = text_loader()
            if not ok:
                return False, payload
            from core.vdev.logcat_correlator import correlate_log
            return True, correlate_log(payload, vfs=vfs, report=rep)

        def done(ok, res):
            self._correlator_busy = False
            if not ok or not isinstance(res, dict):
                self._correlator_error = str(res or "Log correlation failed")
                self._correlator_events = []
                self._correlator_cil = ""
                self._correlator_summary = ""
                self.correlatorChanged.emit()
                return
            self._correlator_error = ""
            self._correlator_events = list(res.get("events") or ())
            self._correlator_cil = str(res.get("generatedCil") or "")
            self._correlator_summary = (
                f"{res.get('totalEvents', 0)} crash/boot event(s) from {res.get('linesParsed', 0):,} lines · "
                f"{res.get('matchedStatic', 0)} matched static analysis · "
                f"{res.get('runtimeOnly', 0)} runtime-only"
                + (f" · {res.get('avcCount', 0)} AVC denial(s)" if res.get("avcCount") else "")
            )
            self.correlatorChanged.emit()

        self._jobs.submit("vdev-correlate", work, None, done)

    @Slot(str)
    def correlateLogFile(self, url_or_path: str):
        path = _url_to_path(url_or_path)
        if not path or not os.path.isfile(path):
            return

        def _read():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return True, f.read(8 * 1024 * 1024)
            except OSError as exc:
                return False, f"Cannot read log file: {exc}"

        self._run_correlate(os.path.basename(path), _read)

    @Slot(str)
    def correlateLogText(self, text: str):
        raw = str(text or "")
        if not raw.strip():
            return
        self._run_correlate("Pasted Log", lambda: (True, raw))

    @Slot(str)
    def captureAdbLog(self, mode: str = "logcat"):
        m = "dmesg" if str(mode).lower() == "dmesg" else "logcat"

        def _cap():
            from core.vdev.logcat_correlator import capture_adb_log
            return capture_adb_log(mode=m)

        self._run_correlate(f"ADB {m}", _cap)

    @Slot()
    def clearCorrelator(self):
        self._correlator_source = ""
        self._correlator_summary = ""
        self._correlator_error = ""
        self._correlator_events = []
        self._correlator_cil = ""
        self.correlatorChanged.emit()

    # ── 1-Click Safe Apply Auto-Fix + Preview Diff + Undo ──
    @Property(str, notify=autofixChanged)
    def autofixStatus(self):
        return self._autofix_status

    @Property(bool, notify=autofixChanged)
    def autofixStatusOk(self):
        return self._autofix_status_ok

    @Property(bool, notify=autofixChanged)
    def autofixCanUndo(self):
        return len(self._autofix_backup_stack) > 0

    @Property(str, notify=autofixChanged)
    def autofixDiffRecipeId(self):
        return self._autofix_diff_recipe_id

    @Property(str, notify=autofixChanged)
    def autofixDiffText(self):
        return self._autofix_diff_text

    def _autofix_workspace_root(self) -> str:
        base = self._active_project_base()
        if base and os.path.isdir(base):
            return base
        for _k, v in self._super_children.items():
            if v.get("origin") == "dir" and v.get("path"):
                parent = os.path.dirname(v["path"])
                if os.path.isdir(parent):
                    return parent
        return ""

    @Slot(str)
    def previewAutofix(self, recipe_id: str):
        rid = str(recipe_id or "")
        raw = self._raw_autofix_recipes.get(rid)
        if not raw:
            return
        from core.vdev.autofix import preview_autofix
        ws_root = self._autofix_workspace_root()
        res = preview_autofix(raw, ws_root, layout=self._last_layout)
        if res.get("ok"):
            blocks = []
            for ch in res.get("changes") or []:
                blocks.append(f"[{ch['action'].upper()}] {ch['vpath']}\n{ch['diff']}")
            self._autofix_diff_recipe_id = rid
            self._autofix_diff_text = "\n".join(blocks).strip()
            self._autofix_status = res.get("reason", "")
            self._autofix_status_ok = True
        else:
            self._autofix_diff_recipe_id = ""
            self._autofix_diff_text = ""
            self._autofix_status = res.get("reason", "Cannot preview Auto-Fix on read-only target.")
            self._autofix_status_ok = False
        self.autofixChanged.emit()

    @Slot()
    def closeAutofixDiff(self):
        self._autofix_diff_recipe_id = ""
        self._autofix_diff_text = ""
        self.autofixChanged.emit()

    @Slot(str)
    def applyAutofix(self, recipe_id: str):
        rid = str(recipe_id or "")
        raw = self._raw_autofix_recipes.get(rid)
        if not raw:
            return
        from core.vdev.autofix import apply_autofix
        ws_root = self._autofix_workspace_root()
        res = apply_autofix(raw, ws_root, layout=self._last_layout)
        self._autofix_status = str(res.get("message") or "")
        self._autofix_status_ok = bool(res.get("ok"))
        if res.get("ok") and res.get("backupManifest"):
            self._autofix_backup_stack.append(res["backupManifest"])
            self._autofix_diff_recipe_id = ""
            self._autofix_diff_text = ""
        self.autofixChanged.emit()

    @Slot()
    def undoLastAutofix(self):
        if not self._autofix_backup_stack:
            return
        manifest = self._autofix_backup_stack.pop()
        from core.vdev.autofix import undo_autofix
        res = undo_autofix(manifest)
        self._autofix_status = str(res.get("message") or "")
        self._autofix_status_ok = bool(res.get("ok"))
        self.autofixChanged.emit()

    # ── Built-In RAK Editor Integration (Plain Text Report & Live VFS Editing — No HTML/Markdown Export) ──
    def _build_plain_report_text(self) -> str:
        rep = self._last_report
        if rep is None:
            return ""
        lines = [
            "================================================================================",
            "  RENN'S ANDROID KITCHEN — BOOT ANALYZER DIAGNOSTIC REPORT",
            "================================================================================",
            f"Generated  : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Verdict    : {rep.verdict()}",
            f"Summary    : {self._verdict_summary}",
            f"Profile    : {self._profile_summary or '(unknown)'}",
            f"Counters   : {self._pass_count} PASS | {self._warn_count} WARN | {self._blocker_count} BLOCKER",
            f"Duration   : {self._duration_ms} ms",
            "",
        ]
        if self._phases:
            lines.append("── BOOT SIMULATION PHASES ──────────────────────────────────────────────────────")
            for ph in self._phases:
                st = str(ph.get("status", "ok")).upper()
                lines.append(f"  [{st:<5}] {ph.get('name', '')}: {ph.get('summary', '')}")
            lines.append("")

        hw_subs = (self._hardware_raw or {}).get("subsystems") or {}
        if hw_subs:
            lines.append("── HARDWARE BRING-UP AUDIT ─────────────────────────────────────────────────────")
            for sub_name, info in hw_subs.items():
                if isinstance(info, dict):
                    lines.append(f"  [{str(info.get('status', 'na')).upper():<4}] {sub_name:<14}: {info.get('detail', '')}")
            lines.append("")

        kcfg = self._kernel_config_raw or {}
        if kcfg.get("flags"):
            lines.append(f"── KERNEL IKCFG .CONFIG ({kcfg.get('total', 0)} options parsed) ─────────────────────────")
            for k, v in sorted(kcfg["flags"].items()):
                lines.append(f"  {k}={v}")
            lines.append("")

        khw = self._kernel_hw_raw or {}
        if khw.get("ko_total"):
            kmi = khw.get("kmi") or {}
            fw = khw.get("firmware") or {}
            lines.append("── KERNEL-HARDWARE & .KO KMI AUDIT ─────────────────────────────────────────────")
            lines.append(f"  Total .ko scanned   : {khw.get('ko_total', 0)}")
            lines.append(f"  KMI module_layout   : {', '.join(kmi.get('kmi_crcs') or ()) or 'n/a'}")
            lines.append(f"  Exported sym CRCs   : {kmi.get('exported_symbols', 0)} (mismatches: {kmi.get('sym_mismatches', 0)})")
            lines.append(f"  OF modalias / DTB   : {khw.get('of_alias_count', 0)} aliases ({len(khw.get('matched_of_compatibles') or ())} matched)")
            lines.append(f"  .ko firmware blobs  : {fw.get('found', 0)}/{fw.get('declared', 0)} found")
            lines.append("")

        lines.append("── STAGE FINDINGS ──────────────────────────────────────────────────────────────")
        for st in rep.stages:
            if not st.ran:
                lines.append(f"\n[{st.label}] (skipped: {st.skipped_reason})")
                continue
            lines.append(f"\n[{st.label}] ({st.duration_ms} ms)")
            for f in st.findings:
                sev = str(f.severity).upper()
                loc = f"  @{f.path}" if f.path else ""
                lines.append(f"  [{sev:<7}] ({f.code}) {f.title}{loc}")
                if f.detail:
                    lines.append(f"            Detail: {f.detail}")
                if f.hint:
                    lines.append(f"            Fix   : {f.hint}")

        if rep.autofix:
            lines.append("\n── AUTO-FIX RECIPES ────────────────────────────────────────────────────────────")
            for r in rep.autofix:
                lines.append(f"  - [{r.get('id', '')}] {r.get('title', '')}")
                if r.get("hint"):
                    lines.append(f"      {r['hint']}")
        lines.append("")
        return "\n".join(lines)

    @Slot(result=str)
    def openReportInEditor(self):
        if self._last_report is None:
            return ""
        text = self._build_plain_report_text()
        if not text:
            return ""
        try:
            from core.app_paths import CACHE_DIR
            out_dir = os.path.join(CACHE_DIR, "vdev_reports")
        except Exception:
            out_dir = os.path.join(os.path.expanduser("~"), ".rak_vdev_reports")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.normpath(os.path.join(out_dir, f"boot_report_{stamp}.log"))
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            return ""
        if self.workspace is not None and hasattr(self.workspace, "openAs"):
            self.workspace.openAs(out_path, "editor")
        return out_path

    @Slot(str, result=str)
    def openVfsFileInEditor(self, vpath: str):
        norm = posixpath.normpath(str(vpath or "").strip())
        if not norm or norm == "/":
            return ""
        if not norm.startswith("/"):
            norm = "/" + norm
        with self._vfs_lock:
            vfs = self._vfs
        if vfs is None:
            return ""
        node = vfs.lookup(norm)
        if node is None or not node.is_file:
            return ""
        # 1) Live host file (unpacked workspace ROM) -> open directly for editing!
        if node.host and os.path.isfile(node.host):
            target = os.path.normpath(node.host)
            if self.workspace is not None and hasattr(self.workspace, "openAs"):
                self.workspace.openAs(target, "editor")
            return target
        # 2) Read-only image/ZIP/APEX file -> extract snapshot and open in built-in Editor!
        raw = vfs.read_bytes(norm, 4 * 1024 * 1024)
        if raw is None:
            return ""
        try:
            from core.app_paths import CACHE_DIR
            out_dir = os.path.join(CACHE_DIR, "vdev_vfs_view")
        except Exception:
            out_dir = os.path.join(os.path.expanduser("~"), ".rak_vdev_vfs")
        rel = norm.lstrip("/").replace("/", os.sep)
        out_path = os.path.normpath(os.path.join(out_dir, rel))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        try:
            with open(out_path, "wb") as fh:
                fh.write(raw)
        except OSError:
            return ""
        if self.workspace is not None and hasattr(self.workspace, "openAs"):
            self.workspace.openAs(out_path, "editor")
        return out_path

    # ── QEMU ARM64 / ARM32 / 64_32 Live Virtual Device Controls (currently disabled) ──
    @Property(bool, constant=True)
    def qemuFeatureEnabled(self):
        return QEMU_LIVE_BOOT_ENABLED

    @Property(bool, notify=qemuChanged)
    def qemuAvailable(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            return False
        return bool(self._qemu_env.get("available"))

    @Property(str, notify=qemuChanged)
    def qemuBackendLabel(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            return "QEMU disabled"
        return str(self._qemu_env.get("backendLabel") or "QEMU not detected")

    @Property(str, notify=qemuChanged)
    def qemuLicense(self):
        return str(self._qemu_env.get("license") or "GPL-2.0-only (external subprocess)")

    @Property(str, notify=qemuChanged)
    def qemuAbi(self):
        return self._qemu_abi

    @Property(str, notify=qemuChanged)
    def qemuKernelPath(self):
        if self._qemu_kernel_path:
            return self._qemu_kernel_path
        if self._qemu_abi == "arm32" and self._qemu_env.get("arm32Kernel"):
            return str(self._qemu_env["arm32Kernel"])
        return str(self._qemu_env.get("arm64Kernel") or "")

    @Property(str, notify=qemuChanged)
    def qemuKernelLabel(self):
        kp = self.qemuKernelPath
        return os.path.basename(kp) if kp else "Auto AOSP 6.6"

    @Property(bool, notify=qemuChanged)
    def qemuRunning(self):
        return QEMU_LIVE_BOOT_ENABLED and self._qemu_running

    @Property(bool, notify=qemuChanged)
    def qemuPreparing(self):
        return QEMU_LIVE_BOOT_ENABLED and self._qemu_preparing

    @Property(int, notify=qemuChanged)
    def qemuRamMb(self):
        return self._qemu_ram_mb

    @Property(bool, notify=qemuChanged)
    def qemuAutoBoot(self):
        return self._qemu_auto_boot

    @Slot(bool)
    def setQemuAutoBoot(self, enabled: bool):
        val = bool(enabled)
        if self._qemu_auto_boot != val:
            self._qemu_auto_boot = val
            self.qemuChanged.emit()

    @Property(str, notify=qemuChanged)
    def qemuStatus(self):
        return self._qemu_status

    @Property(bool, notify=qemuChanged)
    def qemuHasLog(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            return False
        return bool(self._qemu_last_log.strip() or self._qemu_console_lines)

    @Property(int, notify=qemuFrameChanged)
    def qemuFrameSeq(self):
        return self._qemu_frame_seq

    @Property(bool, notify=qemuFrameChanged)
    def qemuHasLiveGpu(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            return False
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            return get_qemu_display_hub().has_live_gpu
        except Exception:
            return False

    @Property("QVariantList", notify=qemuChanged)
    def qemuHardwareGates(self):
        return list((self._hardware_gate or {}).get("subsystems") or ())

    @Property(int, notify=qemuChanged)
    def qemuHardwareGateFaults(self):
        return int((self._hardware_gate or {}).get("faultCount") or 0)

    @Property("QVariantList", notify=qemuChanged)
    def qemuMilestones(self):
        stages = (
            ("kernel", "Kernel"),
            ("init_first", "1st init"),
            ("fs_mounted", "Mounts"),
            ("selinux", "SELinux"),
            ("init_second", "2nd init"),
            ("apexd", "APEX"),
            ("zygote", "Zygote"),
            ("system_server", "SysSrv"),
            ("boot_completed", "Booted"),
        )
        done_set = set(self._qemu_milestones)
        return [{"key": k, "label": lbl, "done": k in done_set} for k, lbl in stages]

    @Property("QVariantList", notify=qemuConsoleChanged)
    def qemuConsoleLines(self):
        return self._qemu_console_lines[-250:]

    @Slot()
    def refreshQemuEnv(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            self._qemu_env = {"available": False, "backend": "disabled", "backendLabel": "QEMU disabled"}
            self.qemuChanged.emit()
            return
        try:
            from core.vdev.qemu_runner import detect_qemu_environment
            self._qemu_env = detect_qemu_environment()
        except Exception:
            self._qemu_env = {}
        self.qemuChanged.emit()

    @Slot(str)
    def setQemuAbi(self, abi: str):
        if not QEMU_LIVE_BOOT_ENABLED:
            return
        val = str(abi or "auto").strip().lower()
        if val not in ("auto", "arm64", "arm64_32", "arm32"):
            val = "auto"
        if self._qemu_abi != val:
            self._qemu_abi = val
            self.qemuChanged.emit()

    @Slot(int)
    def setQemuRamMb(self, ram_mb: int):
        if not QEMU_LIVE_BOOT_ENABLED:
            return
        raw = int(ram_mb or 3072)
        val = 4096 if raw >= 4096 else (3072 if raw >= 3072 else 2048)
        if self._qemu_ram_mb != val:
            self._qemu_ram_mb = val
            self.qemuChanged.emit()

    @Slot(str)
    def setQemuKernel(self, url_or_path: str):
        if not QEMU_LIVE_BOOT_ENABLED:
            return
        path = _url_to_path(url_or_path)
        if path and os.path.isfile(path):
            self._qemu_kernel_path = path
            self._qemu_status = f"VirtIO kernel set: {os.path.basename(path)}"
            self.qemuChanged.emit()

    def _bump_qemu_frame(self):
        self._qemu_last_frame_ts = time.monotonic()
        self._qemu_frame_pending = False
        self._qemu_frame_seq += 1
        self.qemuFrameChanged.emit()

    @Slot()
    def _on_qemu_frame_ui(self):
        # Throttle live VNC redraws to ~15 FPS so Qt Quick stays silky smooth
        if (time.monotonic() - self._qemu_last_frame_ts) < 0.066:
            self._qemu_frame_pending = True
            return
        self._bump_qemu_frame()

    def _render_embedded_console_frame(self, force: bool = False):
        now = time.monotonic()
        if not force and (now - self._qemu_last_console_render) < 0.35:
            return
        self._qemu_last_console_render = now
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            hub = get_qemu_display_hub()
            if hub.has_live_gpu:
                return
            sess = self._qemu_session
            elapsed = (time.perf_counter() - sess.started_at) if (sess and sess.started_at) else 0.0
            tail = [f"[{ln.get('tag', 'qemu')}] {ln.get('msg', '')}" for ln in self._qemu_console_lines[-32:]]
            hub.render_boot_console(
                phase=self._qemu_status,
                abi=sess.plan.abi_mode if sess else self._qemu_abi,
                backend=self.qemuBackendLabel,
                elapsed_s=elapsed,
                milestones=list(self._qemu_milestones),
                tail_lines=tail,
                panic=bool(sess and sess.panic_detected),
                running=self._qemu_running or self._qemu_preparing,
                hw_gates=self.qemuHardwareGates,
            )
            self._bump_qemu_frame()
        except Exception:
            pass

    def _enqueue_qemu_line(self, entry):
        """Called from the QEMU reader background thread: zero Qt signals per line!"""
        if not isinstance(entry, dict):
            return
        with self._qemu_pending_lock:
            self._qemu_pending_lines.append(entry)
            if len(self._qemu_pending_lines) > 2000:
                self._qemu_pending_lines = self._qemu_pending_lines[-1500:]

    @Slot()
    def _flush_qemu_ui(self):
        """Batched UI-thread drain (160 ms interval) so hundreds of dmesg lines/sec never freeze QML."""
        with self._qemu_pending_lock:
            if self._qemu_pending_lines:
                batch = self._qemu_pending_lines
                self._qemu_pending_lines = []
            else:
                batch = None

        if self._qemu_frame_pending and (time.monotonic() - self._qemu_last_frame_ts) >= 0.066:
            self._bump_qemu_frame()

        if not batch:
            return

        self._qemu_console_lines.extend(batch)
        if len(self._qemu_console_lines) > 1500:
            self._qemu_console_lines = self._qemu_console_lines[-1000:]

        self._logcat_lines.extend(batch)
        trimmed = False
        if len(self._logcat_lines) > 12000:
            self._logcat_lines = self._logcat_lines[-10000:]
            trimmed = True

        # Incremental append when no filter is active (O(batch) instead of O(12,000)!)
        if not trimmed and self._logcat_level_filter == "all" and not self._logcat_query.strip():
            self._filtered_logcat_cache.extend(batch)
        else:
            self._rebuild_filtered_logcat()

        self.qemuConsoleChanged.emit()
        self.logcatChanged.emit()

        if self._qemu_session is not None:
            new_ms = list(self._qemu_session.milestones)
            if new_ms != self._qemu_milestones:
                self._qemu_milestones = new_ms
                self.qemuChanged.emit()
        self._render_embedded_console_frame(force=False)

    @Slot(object)
    def _on_qemu_line_ui(self, entry):
        self._enqueue_qemu_line(entry)

    @Slot(str)
    def _on_qemu_phase_ui(self, label: str):
        self._qemu_status = str(label or "")
        pct = self._parse_percent(self._qemu_status)
        if pct >= 0:
            self._progress_percent = pct
            self.stateChanged.emit()
        if self._qemu_session is not None:
            self._qemu_milestones = list(self._qemu_session.milestones)
        self._render_embedded_console_frame(force=True)
        self.qemuChanged.emit()

    @Slot(bool, str)
    def _on_qemu_done_ui(self, ok: bool, summary: str):
        self._qemu_flush_timer.stop()
        self._flush_qemu_ui()
        self._qemu_running = False
        self._qemu_preparing = False
        self._progress_percent = -1
        self._qemu_status = str(summary or "")
        if self._qemu_session is not None:
            self._qemu_last_log = self._qemu_session.full_log_text()
            self._qemu_milestones = list(self._qemu_session.milestones)
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            get_qemu_display_hub().attach_rfb(None)
        except Exception:
            pass
        self._render_embedded_console_frame(force=True)
        self._append_logcat_batch_ui((self._run_seq, [{
            "ts": _logcat_ts(), "pid": "0", "tid": "0",
            "level": "I" if ok else "E", "tag": "qemu",
            "msg": self._qemu_status, "detail": "", "path": "",
            "stage": "qemu", "code": "qemu-exit",
        }]))
        self.qemuChanged.emit()
        # Automatically correlate live QEMU boot logs against the Rust static report
        if self._qemu_last_log.strip() and self._last_report is not None:
            self.correlateQemuLog()

    def _resolve_qemu_target(self):
        if self._last_layout is not None:
            return self._last_layout
        if self._super_path and os.path.isfile(self._super_path):
            return self._super_path
        if self._zip_path and os.path.isfile(self._zip_path):
            return self._zip_path
        # Build lightweight layout-like object from explicitly staged partition & firmware .img files
        staged_imgs = {}
        for part, info in (self._super_children or {}).items():
            p = info.get("path") or ""
            if p and os.path.isfile(p) and p.lower().endswith(".img"):
                staged_imgs[part] = p
        if staged_imgs:
            fw_boot = (self._firmware_staged.get("boot") or {}).get("path") or ""
            fw_init_boot = (self._firmware_staged.get("init_boot") or {}).get("path") or ""

            class _StagedTarget:
                fs_images = staged_imgs
                super_image = ""
                zip_path = ""
                boot_image = fw_boot
                init_boot_image = fw_init_boot
                root = os.path.dirname(next(iter(staged_imgs.values())))

            return _StagedTarget()
        return self._active_project_base()

    @Slot(object)
    def _on_qemu_prep_done_ui(self, plan):
        self._qemu_preparing = False
        if plan is None or not getattr(plan, "ok", False):
            err = getattr(plan, "error", "") or "Failed to prepare QEMU boot plan."
            self._qemu_status = err
            self._qemu_console_lines.append({
                "ts": _logcat_ts(), "pid": "0", "tid": "0", "level": "E",
                "tag": "qemu", "msg": err, "detail": "", "path": "",
                "stage": "qemu", "code": "qemu-preflight",
            })
            self._render_embedded_console_frame(force=True)
            self.qemuChanged.emit()
            return

        # Refresh detected kernel label & unified Hardware Gate from plan (includes real disk FS vs IKCFG check)
        self.refreshQemuEnv()
        if getattr(plan, "hardware_gate", None):
            self._hardware_gate = dict(plan.hardware_gate)
        from core.vdev.qemu_runner import QemuSession
        from core.vdev.qemu_display import get_qemu_display_hub, RfbClient

        hub = get_qemu_display_hub()
        hub.reset_for_boot()
        self._qemu_running = True
        self._qemu_status = f"Booting QEMU ({plan.backend} · {plan.abi_mode})…"
        init_batch = [{
            "ts": _logcat_ts(), "pid": "0", "tid": "0", "level": "I", "tag": "qemu",
            "msg": f"Launching {plan.backend} ({plan.abi_mode}) · {len(plan.disks)} disk(s)"
                   + (f" · VNC 127.0.0.1:{plan.vnc_port}" if plan.vnc_port else " · Display gated OFF")
                   + f" · {plan.license}",
            "detail": " ".join(plan.command), "path": plan.work_dir,
            "stage": "qemu", "code": "qemu-start",
        }]
        for warn_msg in (plan.warnings or ()):
            init_batch.append({
                "ts": _logcat_ts(), "pid": "0", "tid": "0",
                "level": "E" if "HW GATE FAULT" in warn_msg else "W",
                "tag": "hwgate" if "HW GATE" in warn_msg else "qemu",
                "msg": warn_msg, "detail": "", "path": "",
                "stage": "qemu", "code": "hwgate-fault",
            })
        self._qemu_console_lines.extend(init_batch)
        self._append_logcat_batch_ui((self._run_seq, init_batch))
        self._render_embedded_console_frame(force=True)
        self.qemuConsoleChanged.emit()
        self.qemuChanged.emit()

        sess = QemuSession(plan, timeout_sec=0)
        self._qemu_session = sess
        if plan.vnc_port:
            rfb = RfbClient("127.0.0.1", plan.vnc_port, hub, on_frame=lambda: self._qemuFrameSignal.emit())
            hub.attach_rfb(rfb)
            rfb.start()

        self._qemu_flush_timer.start()
        sess.start(
            on_line=self._enqueue_qemu_line,
            on_milestone=lambda _k, lbl: self._qemuPhaseSignal.emit(lbl),
            on_done=lambda ok, summary, _s: self._qemuDoneSignal.emit(bool(ok), str(summary)),
        )

    @Slot()
    def startQemuBoot(self):
        """1-Click Automatic Play (currently disabled until QEMU live boot is stabilized)."""
        if not QEMU_LIVE_BOOT_ENABLED:
            self._qemu_running = False
            self._qemu_preparing = False
            self._qemu_status = "QEMU Live Boot is disabled"
            self.qemuChanged.emit()
            return
        if self._qemu_running or self._qemu_preparing:
            return
        if not self._qemu_env:
            self.refreshQemuEnv()
        self._qemu_preparing = True
        self._qemu_last_log = ""
        self._qemu_milestones = []
        self._qemu_console_lines = []
        with self._qemu_pending_lock:
            self._qemu_pending_lines.clear()
        self._qemu_status = "Preparing virtual device & checking VirtIO kernel…"
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            get_qemu_display_hub().reset_for_boot()
        except Exception:
            pass
        self._render_embedded_console_frame(force=True)
        self.qemuConsoleChanged.emit()
        self.qemuChanged.emit()
        self.qemuStarted.emit()

        target = self._resolve_qemu_target()
        report_ref = self._last_report
        profile = (report_ref.profile if report_ref is not None else None) or {}
        abi = self._qemu_abi
        kpath = self.qemuKernelPath
        ram_mb = self._qemu_ram_mb
        permissive = self._permissive
        avb_off = not self._avb_enabled
        kcfg = dict(self._kernel_config_raw)
        khw = dict(self._kernel_hw_raw)
        hw_raw = dict(self._hardware_raw)

        def _prep_worker():
            from core.vdev.qemu_runner import QemuBootPlan, prepare_qemu_plan
            try:
                plan = prepare_qemu_plan(
                    target,
                    profile=profile,
                    abi=abi,
                    kernel_path=kpath,
                    ram_mb=ram_mb,
                    headless=True,
                    embedded_display=True,
                    permissive=permissive,
                    avb_disabled=avb_off,
                    timeout_sec=0,
                    auto_download_kernel=True,
                    on_progress=lambda msg: self._qemuPhaseSignal.emit(msg),
                    report=report_ref,
                    kernel_config=kcfg,
                    kernel_hw=khw,
                    hardware_audit=hw_raw,
                )
            except Exception as exc:
                try:
                    from core.error_reporting import report_fatal
                    report_fatal(exc, operation="QEMU Virtual Device Preparation")
                except Exception:
                    pass
                plan = QemuBootPlan(ok=False, error=f"QEMU preparation error: {exc}", abi_mode=abi or "auto")
            self._qemuPrepDoneSignal.emit(plan)

        threading.Thread(target=_prep_worker, daemon=True, name="RAK-QEMU-Prep").start()

    @Slot()
    def stopQemuBoot(self):
        self._qemu_flush_timer.stop()
        self._flush_qemu_ui()
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            get_qemu_display_hub().attach_rfb(None)
        except Exception:
            pass
        if self._qemu_session is not None:
            self._qemu_session.stop()
            self._qemu_last_log = self._qemu_session.full_log_text()
        self._qemu_running = False
        self._qemu_preparing = False
        self._qemu_status = "QEMU stopped by user"
        self._render_embedded_console_frame(force=True)
        self.qemuChanged.emit()

    @Slot()
    def onDialogClosed(self):
        """Immediately stop any running QEMU session and wipe multi-GB ephemeral caches when dialog closes."""
        if self._qemu_running or self._qemu_preparing:
            self.stopQemuBoot()
        try:
            from core.vdev.qemu_runner import cleanup_vdev_ephemeral_caches
            cleanup_vdev_ephemeral_caches(include_extracted=True, async_run=True)
        except Exception:
            pass

    def _cleanup_on_exit(self):
        try:
            if self._qemu_session is not None:
                self._qemu_session.stop()
        except Exception:
            pass
        try:
            from core.vdev.qemu_runner import cleanup_vdev_ephemeral_caches
            cleanup_vdev_ephemeral_caches(include_extracted=True, async_run=False)
        except Exception:
            pass

    @Slot(float, float, bool)
    def qemuTouch(self, norm_x: float, norm_y: float, pressed: bool):
        if not QEMU_LIVE_BOOT_ENABLED:
            return
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            get_qemu_display_hub().send_touch(float(norm_x), float(norm_y), bool(pressed))
        except Exception:
            pass

    @Slot(str)
    def qemuKey(self, key_name: str):
        if not QEMU_LIVE_BOOT_ENABLED:
            return
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            get_qemu_display_hub().send_android_key(str(key_name or ""))
        except Exception:
            pass

    @Slot(result=str)
    def qemuSaveScreenshot(self):
        if not QEMU_LIVE_BOOT_ENABLED:
            return ""
        try:
            from core.vdev.qemu_display import get_qemu_display_hub
            from core.app_paths import CACHE_DIR
            out_dir = os.path.join(CACHE_DIR, "vdev_reports")
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.normpath(os.path.join(out_dir, f"qemu_screen_{stamp}.png"))
            get_qemu_display_hub().snapshot().save(out_path, "PNG")
            self._qemu_status = f"Screenshot saved: {os.path.basename(out_path)}"
            self.qemuChanged.emit()
            return out_path
        except Exception:
            return ""

    @Slot()
    def correlateQemuLog(self):
        raw = self._qemu_last_log
        if not raw and self._qemu_session is not None:
            raw = self._qemu_session.full_log_text()
        if not raw and self._qemu_console_lines:
            raw = "\n".join(str(x.get("msg", "")) for x in self._qemu_console_lines)
        if not raw.strip():
            return
        self._run_correlate(f"QEMU Live Boot ({self._qemu_abi})", lambda: (True, raw))

    @Slot(result=str)
    def openQemuLogInEditor(self):
        raw = self._qemu_last_log
        if not raw and self._qemu_session is not None:
            raw = self._qemu_session.full_log_text()
        if not raw and self._qemu_console_lines:
            raw = "\n".join(f"[{x.get('tag', 'qemu')}] {x.get('msg', '')}" for x in self._qemu_console_lines)
        if not raw.strip():
            return ""
        try:
            from core.app_paths import CACHE_DIR
            out_dir = os.path.join(CACHE_DIR, "vdev_reports")
        except Exception:
            out_dir = os.path.join(os.path.expanduser("~"), ".rak_vdev_reports")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.normpath(os.path.join(out_dir, f"qemu_live_boot_{stamp}.log"))
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(raw)
        except OSError:
            return ""
        if self.workspace is not None and hasattr(self.workspace, "openAs"):
            self.workspace.openAs(out_path, "editor")
        return out_path
