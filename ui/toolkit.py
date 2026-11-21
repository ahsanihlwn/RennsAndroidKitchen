# SPDX-License-Identifier: MPL-2.0
"""Modular Qt Quick controllers for APK Debloater and Sepolicy Rules Patch."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from core import apk_manager, native_backend, quick_mods
from core.project_config import resolve_tree_target
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


def _fmt_size(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def _local_path(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("file:"):
        local = QUrl(text).toLocalFile()
        if local:
            return local
    return text


class WorkspaceToolkit(QObject):
    """Workspace backend for separate context-triggered APK Debloater and Sepolicy Rules Patch."""

    changed = Signal()
    selectionChanged = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent or workspace)
        self.workspace = workspace
        self._jobs = AsyncJobs(self, limit=2)
        self._adb_cancels: set = set()
        self._closed = False
        self._busy = False
        self._status = "Ready"

        # APK Debloater state
        self._apk_scan_gen = 0
        self._apk_target_dir = ""
        self._all_apks: list[dict] = []
        self._checked_apks: set[str] = set()
        self._apks = RowsModel(parent=self)
        self._quarantined = RowsModel(parent=self)
        self._apk_query = ""
        self._apk_partition = ""
        self._apk_risk = ""
        self._partitions: list[str] = ["All"]
        self._selected_apk: dict = {}
        self._selected_apk_icon = ""

        # Sepolicy Rules Patch state
        self._selinux_target_folder = ""
        self._selinux_dir = ""
        self._selinux_cil_files: list[str] = []
        self._selected_cil_file = ""
        self._selinux_log_preview = ""
        self._selinux_te = ""
        self._selinux_magisk = ""
        self._selinux_cil = ""
        self._selinux_stats = ""

    def _project_base(self) -> str:
        if self.workspace.active_tree and self.workspace.active_tree.base:
            return self.workspace.active_tree.base
        return resolve_tree_target(self.workspace.project, self.workspace.p1)

    def _set_status(self, text: str, *, busy: bool | None = None):
        if busy is not None:
            self._busy = busy
        self._status = text
        self.changed.emit()

    # =========================================================================
    # Shared properties
    # =========================================================================

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    # =========================================================================
    # APK Debloater properties & slots
    # =========================================================================

    @Property(str, notify=changed)
    def apkTargetDir(self):
        return self._apk_target_dir

    @Property(QObject, constant=True)
    def apks(self):
        return self._apks

    @Property(int, notify=changed)
    def quarantinedCount(self):
        return self._quarantined.rowCount()

    @Property(QObject, constant=True)
    def quarantined(self):
        return self._quarantined

    @Property(int, notify=selectionChanged)
    def checkedApkCount(self):
        return len(self._checked_apks)

    @Property(int, notify=changed)
    def totalApkCount(self):
        return len(self._all_apks)

    @Property("QStringList", notify=changed)
    def apkPartitions(self):
        return self._partitions

    @Property("QVariantMap", notify=selectionChanged)
    def selectedApk(self):
        return dict(self._selected_apk)

    @Property(str, notify=selectionChanged)
    def selectedApkIcon(self):
        return self._selected_apk_icon

    @Slot(str)
    def openApkDebloater(self, target_dir: str = ""):
        chosen = _local_path(target_dir) or self._project_base()
        if chosen and os.path.isfile(chosen):
            chosen = os.path.dirname(chosen)
        self._apk_scan_gen += 1
        self._apk_target_dir = chosen
        self._apk_query = ""
        self._apk_partition = ""
        self._apk_risk = ""
        self._all_apks = []
        self._checked_apks.clear()
        self._selected_apk = {}
        self._selected_apk_icon = ""
        self._partitions = ["All"]
        self._apks.replace([])
        self.changed.emit()
        self.selectionChanged.emit()
        self.scanApks()

    @Slot()
    def scanApks(self):
        scan_dir = self._apk_target_dir or self._project_base()
        proj_base = self._project_base() or scan_dir
        if not scan_dir or not os.path.isdir(scan_dir):
            self._all_apks = []
            self._apks.replace([])
            self._quarantined.replace([])
            self._set_status("Select a valid filesystem folder to scan APKs.", busy=False)
            return

        self._apk_scan_gen += 1
        gen = self._apk_scan_gen
        self._set_status(f"Scanning APKs in {os.path.basename(scan_dir) or scan_dir}…", busy=True)

        def work():
            rows = apk_manager.scan_project_apks(scan_dir)
            scan_root = Path(scan_dir).resolve()
            base_root = Path(proj_base).resolve()
            enriched = []
            for row in rows:
                item = dict(row)
                full_p = Path(item.get("path", "")).resolve()
                try:
                    proj_rel = full_p.relative_to(base_root).as_posix()
                except ValueError:
                    proj_rel = item.get("rel_path", "").replace("\\", "/")
                try:
                    folder_rel = full_p.relative_to(scan_root).as_posix()
                except ValueError:
                    folder_rel = proj_rel
                item["rel_path"] = proj_rel
                item["origin_folder"] = os.path.dirname(proj_rel) or "."
                item["scan_rel_path"] = folder_rel
                if not item.get("partition"):
                    parts = proj_rel.split("/")
                    if parts and parts[0]:
                        item["partition"] = parts[0]
                enriched.append(item)
            quarantined = apk_manager.list_quarantined_apks(proj_base)
            return True, (enriched, quarantined)

        def done(ok, res):
            if self._closed or gen != self._apk_scan_gen:
                return
            if not ok:
                self._set_status(f"APK scan failed: {res}", busy=False)
                return
            rows, quarantined = res
            self._all_apks = rows
            self._checked_apks.clear()
            parts = sorted({r.get("partition", "") for r in rows if r.get("partition")})
            self._partitions = ["All", *parts]
            self._quarantined.replace([
                {
                    **q,
                    "origin_folder": os.path.dirname(q.get("rel_path", "")) or ".",
                }
                for q in quarantined
            ])
            self._apply_apk_filters()
            if rows and not self._selected_apk:
                self.selectApk(rows[0]["rel_path"])
            self._set_status(
                f"Found {len(rows)} APK(s) in {os.path.basename(scan_dir) or scan_dir} "
                f"({len(quarantined)} quarantined).",
                busy=False,
            )

        if self._jobs.submit("apk_scan", work, None, done) is None:
            self._set_status("Scan queue unavailable.", busy=False)

    def _filtered_apk_rows(self) -> list[dict]:
        query = self._apk_query.strip().lower()
        part = self._apk_partition.strip()
        risk = self._apk_risk.strip().lower()
        out = []
        for row in self._all_apks:
            if part and part != "All" and row.get("partition", "") != part:
                continue
            if risk and risk != "all" and row.get("risk", "").lower() != risk:
                continue
            if query:
                hay = " ".join((
                    row.get("name", ""),
                    row.get("package", ""),
                    row.get("rel_path", ""),
                    row.get("origin_folder", ""),
                    row.get("filename", ""),
                )).lower()
                if query not in hay:
                    continue
            out.append({
                **row,
                "origin_folder": row.get("origin_folder") or (os.path.dirname(row.get("rel_path", "")) or "."),
                "size_human": _fmt_size(int(row.get("size", 0))),
                "permissions_text": ", ".join(row.get("permissions", [])),
                "checked": row.get("rel_path", "") in self._checked_apks,
            })
        return out

    def _apply_apk_filters(self):
        self._apks.replace(self._filtered_apk_rows())
        self.changed.emit()
        self.selectionChanged.emit()

    @Slot(str, str, str)
    def setApkFilters(self, query: str, partition: str, risk: str):
        self._apk_query = query or ""
        self._apk_partition = partition or ""
        self._apk_risk = risk or ""
        self._apply_apk_filters()

    @Slot(str)
    def toggleApkCheck(self, rel_path: str):
        if not rel_path:
            return
        if rel_path in self._checked_apks:
            self._checked_apks.remove(rel_path)
        else:
            self._checked_apks.add(rel_path)
        self._apply_apk_filters()

    @Slot(bool)
    def selectAllFilteredApks(self, select: bool):
        visible_rels = {r["rel_path"] for r in self._filtered_apk_rows() if r.get("rel_path")}
        if select:
            self._checked_apks.update(visible_rels)
        else:
            self._checked_apks.difference_update(visible_rels)
        self._apply_apk_filters()

    @Slot(str)
    def selectApk(self, rel_path: str):
        match = next((r for r in self._all_apks if r.get("rel_path") == rel_path), None)
        if not match:
            self._selected_apk = {}
            self._selected_apk_icon = ""
            self.selectionChanged.emit()
            return
        self._selected_apk = {
            **match,
            "origin_folder": match.get("origin_folder") or (os.path.dirname(match.get("rel_path", "")) or "."),
            "size_human": _fmt_size(int(match.get("size", 0))),
            "permissions_text": "\n".join(match.get("permissions", [])) or "(none declared)",
        }
        icon_url = apk_manager.load_apk_icon_data_url(
            match.get("path", ""), match.get("icon_entry", "")
        )
        self._selected_apk_icon = icon_url
        self.selectionChanged.emit()

    @Slot()
    def quarantineSelectedApks(self):
        base = self._project_base()
        if not base or not self._checked_apks:
            self._set_status("Check one or more APKs to quarantine.")
            return
        selected_records = [r for r in self._all_apks if r.get("rel_path") in self._checked_apks]
        self._set_status(f"Moving {len(selected_records)} APK(s) to quarantine…", busy=True)

        def work():
            return True, apk_manager.remove_apks(base, selected_records)

        def done(ok, res):
            if self._closed:
                return
            if not ok:
                self._set_status(f"Quarantine failed: {res}", busy=False)
                return
            moved = len(res.get("moved", []))
            self._checked_apks.clear()
            for d in self.workspace.directories:
                if d.base:
                    d.reloadMetadata()
            self._set_status(f"Moved {moved} APK(s) to project quarantine.", busy=False)
            self.scanApks()

        self._jobs.submit("apk_quarantine", work, None, done)

    @Slot(str)
    def restoreSelectedApks(self, rel_path: str = ""):
        base = self._project_base()
        if not base:
            return
        targets = [rel_path] if rel_path else None
        self._set_status("Restoring APK(s) from quarantine…", busy=True)

        def work():
            return True, apk_manager.restore_apks(base, targets)

        def done(ok, res):
            if self._closed:
                return
            if not ok:
                self._set_status(f"Restore failed: {res}", busy=False)
                return
            count = len(res.get("restored", []))
            for d in self.workspace.directories:
                if d.base:
                    d.reloadMetadata()
            self._set_status(f"Restored {count} APK(s).", busy=False)
            self.scanApks()

        self._jobs.submit("apk_restore", work, None, done)

    @Slot(str)
    def importDebloatList(self, url_or_path: str):
        local = _local_path(url_or_path)
        if not local or not os.path.isfile(local):
            self._set_status("Debloat list file not found.")
            return
        tokens = set(apk_manager.import_debloat_list(local))
        matched = 0
        for row in self._all_apks:
            pkg = row.get("package", "")
            rel = row.get("rel_path", "")
            fname = row.get("filename", "")
            if (pkg and pkg in tokens) or (rel and rel in tokens) or (fname and fname in tokens):
                self._checked_apks.add(rel)
                matched += 1
        self._apply_apk_filters()
        self._set_status(f"Imported {len(tokens)} entries, checked {matched} matching APK(s).")

    @Slot(str)
    def exportDebloatList(self, url_or_path: str):
        local = _local_path(url_or_path)
        if not local:
            return
        entries = []
        for row in self._all_apks:
            if row.get("rel_path") in self._checked_apks:
                entries.append(row.get("package") or row.get("rel_path"))
        if not entries:
            self._set_status("No APKs checked to export.")
            return
        apk_manager.export_debloat_list(entries, local)
        self._set_status(f"Exported {len(entries)} entries to {os.path.basename(local)}.")

    # =========================================================================
    # Sepolicy Rules Patch properties & slots
    # =========================================================================

    @Property(str, notify=changed)
    def selinuxTargetFolder(self):
        return self._selinux_target_folder

    @Property(str, notify=changed)
    def selinuxDir(self):
        return self._selinux_dir

    @Property("QStringList", notify=changed)
    def selinuxCilFiles(self):
        return self._selinux_cil_files

    @Property(str, notify=changed)
    def selectedCilFile(self):
        return self._selected_cil_file

    @Property(str, notify=changed)
    def selinuxLogPreview(self):
        return self._selinux_log_preview

    @Property(str, notify=changed)
    def selinuxTe(self):
        return self._selinux_te

    @Property(str, notify=changed)
    def selinuxMagisk(self):
        return self._selinux_magisk

    @Property(str, notify=changed)
    def selinuxCil(self):
        return self._selinux_cil

    @Property(str, notify=changed)
    def selinuxStats(self):
        return self._selinux_stats

    @Slot(str)
    def openSepolicyPatch(self, folder_path: str):
        chosen = _local_path(folder_path)
        self._selinux_target_folder = chosen
        selinux_dir = quick_mods.find_selinux_dir(chosen)
        self._selinux_dir = selinux_dir
        cils: list[str] = []
        if selinux_dir and os.path.isdir(selinux_dir):
            cils = sorted(
                name
                for name in os.listdir(selinux_dir)
                if name.lower().endswith(".cil") and os.path.isfile(os.path.join(selinux_dir, name))
            )
        if "rak_custom_sepolicy.cil" not in cils:
            cils.append("rak_custom_sepolicy.cil")
        self._selinux_cil_files = cils
        preferred = next(
            (n for n in ("plat_sepolicy.cil", "vendor_sepolicy.cil", "system_ext_sepolicy.cil", "product_sepolicy.cil") if n in cils),
            cils[0] if cils else "rak_custom_sepolicy.cil",
        )
        self._selected_cil_file = preferred
        self._set_status(
            f"Selinux directory detected: {selinux_dir}" if selinux_dir else "No etc/selinux directory found."
        )

    @Slot(str)
    def selectCilFile(self, filename: str):
        if filename:
            self._selected_cil_file = filename
            self.changed.emit()

    @Slot(bool)
    def captureLiveLogcat(self, clear_after: bool = False):
        self._set_status("Capturing live logcat via ADB…", busy=True)
        try:
            cancel = native_backend.platform_cancel_token()
        except Exception:
            cancel = None
        if cancel is not None:
            self._adb_cancels.add(cancel)

        def work(_=None):
            try:
                res = native_backend.adb_run(
                    ["logcat", "-d"],
                    timeout_ms=15000,
                    cancel=cancel,
                )
                code = int(res.get("exit_code", -1))
                raw = str(res.get("stdout", ""))
                err = str(res.get("stderr", "") or res.get("output", "")).strip()
                if code == 0 and clear_after:
                    denials = [ln for ln in raw.splitlines() if "avc:" in ln and "denied" in ln]
                    if denials:
                        try:
                            native_backend.adb_run(["logcat", "-c"], timeout_ms=5000, cancel=cancel)
                        except Exception:
                            pass
                return True, {"code": code, "raw": raw, "err": err}
            finally:
                if cancel is not None:
                    self._adb_cancels.discard(cancel)

        def done(ok, result):
            if self._closed:
                return
            if not ok or not isinstance(result, dict):
                self._set_status(f"adb logcat failed: {result}", busy=False)
                return
            code = int(result.get("code", -1))
            raw = str(result.get("raw", ""))
            err = str(result.get("err", "")).strip()
            if code != 0:
                lower_err = err.lower()
                if "no devices" in lower_err or "device" in lower_err or "offline" in lower_err:
                    self._set_status("No ADB device connected (or unauthorized).", busy=False)
                else:
                    self._set_status(err or f"adb logcat failed (exit {code}).", busy=False)
                return
            denials = [ln for ln in raw.splitlines() if "avc:" in ln and "denied" in ln]
            if not denials:
                self._selinux_log_preview = raw[-8192:] if raw else ""
                self._set_status("Connected to ADB, no `avc: denied` lines found in logcat.", busy=False)
                self.generateSelinuxRules(self._selinux_log_preview, False)
                return
            joined = "\n".join(denials)
            self._selinux_log_preview = joined
            self.generateSelinuxRules(joined, False)

        self._jobs.submit("selinux_adb_logcat", work, None, done)

    @Slot(str)
    def loadLogFile(self, url_or_path: str):
        local = _local_path(url_or_path)
        if not local or not os.path.isfile(local):
            self._set_status("Log file not found.")
            return
        try:
            text = Path(local).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self._set_status(f"Failed reading log: {error}")
            return
        self._selinux_log_preview = text
        self.generateSelinuxRules(text, False)

    @Slot(str, bool)
    def generateSelinuxRules(self, log_text: str, include_untrusted: bool = False):
        self._selinux_log_preview = log_text or ""
        try:
            res = quick_mods.generate_selinux_allow(
                self._selinux_log_preview, include_untrusted=include_untrusted
            )
            self._selinux_te = res.get("te_rules", "")
            self._selinux_magisk = res.get("magisk_rules", "")
            self._selinux_cil = res.get("cil_rules", "")
            denials = int(res.get("denial_count", 0))
            rules = int(res.get("rule_count", 0))
            self._selinux_stats = f"{denials} denial(s) -> {rules} unique rule(s)"
            self._set_status(
                f"Generated {rules} SELinux rule(s) from {denials} denial(s)."
                if rules
                else "No `avc: denied` entries parsed.",
                busy=False,
            )
        except Exception as error:
            self._set_status(f"SELinux parse error: {error}", busy=False)

    @Slot(str)
    def applyCilRules(self, filename: str = ""):
        if not self._selinux_dir or not os.path.isdir(self._selinux_dir):
            self._set_status("Target etc/selinux directory is unavailable.")
            return
        cil_text = (self._selinux_cil or "").strip()
        if not cil_text:
            self._set_status("Generate CIL rules from logcat or a .txt file first.")
            return
        chosen = os.path.basename((filename or self._selected_cil_file or "rak_custom_sepolicy.cil").strip())
        if not chosen.lower().endswith(".cil"):
            chosen += ".cil"
        target_path = os.path.join(self._selinux_dir, chosen)
        self._set_status(f"Patching {chosen}…", busy=True)

        def work():
            return True, quick_mods.append_cil_rules(target_path, cil_text)

        def done(ok, res):
            if self._closed:
                return
            if not ok:
                self._set_status(f"Patch failed: {res}", busy=False)
                return
            added = int(res.get("added", 0))
            skipped = int(res.get("skipped", 0))
            for d in self.workspace.directories:
                if d.base:
                    d.reloadMetadata()
            if added:
                self._set_status(f"Appended {added} rule(s) to {chosen} ({skipped} existing skipped).", busy=False)
            else:
                self._set_status(f"All {skipped} rule(s) already present in {chosen}.", busy=False)

        self._jobs.submit("selinux_cil_patch", work, None, done)

    @Slot()
    def dismiss(self):
        for tok in list(self._adb_cancels):
            try:
                tok.cancel()
            except Exception:
                pass
        self._adb_cancels.clear()
        self._jobs.reset()
        if self._busy:
            self._busy = False
            self.changed.emit()

    @Slot()
    def close(self):
        self._closed = True
        self.dismiss()
        self._jobs.stop()
