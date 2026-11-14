# SPDX-License-Identifier: MPL-2.0
"""Production Qt Quick launcher and workspace lifecycle."""
from __future__ import annotations

import logging
import os
import re
import time
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon

from core import ui_lang
from core.flag import window_title
from core.project_lifecycle import log_project_event
from core.project_metrics import trash_remaining_text,project_drive_fill
from core.rennsproject_bridge import AppFeatures, private_bundle_enabled
from core.wsl_manager import load_disk_usage_cache, project_storage_key
from core.threader import run_in_thread  # Account callbacks must not first-import this during editor prewarm.
from main_ext.class_ThemeUi import theme
from ui.presentation import LauncherModel
from ui.project_store import ProjectStore
from ui.runtime import create_launcher, WindowLifetime
from ui.loading import QuickLoadingWindow


class AccountState(AppFeatures, QObject):
    """Reuse account verification/session services without building account widgets."""
    official_authorization_finished = Signal(bool)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_authorization()
        self._init_account_features()

    def refresh_profile_mini(self):
        self.changed.emit()

    def _refresh_settings_badge(self):
        self.changed.emit()

    def profile(self):
        if not self.account:
            return {}
        from rennsproject.avatar.handler import resolve_avatar_path
        path=resolve_avatar_path(self.account.get("avatar","000"),self.account)
        avatar_url=QUrl.fromLocalFile(path)
        if os.path.isfile(path):avatar_url.setQuery('v='+str(os.stat(path).st_mtime_ns))
        avatar=avatar_url.toString()
        level = self.verified_level
        active = level in ("core", "grayr", "admin") or self._dev_authorized is True
        pending = self._dev_authorized is None and not active
        details = ui_lang.t("txt_" + level + "_access") if level in ("core", "grayr", "admin") else (
            "" if pending else ui_lang.t("txt_dev_active" if active else "txt_dev_expired"))
        return {"name": self.account.get("username", ""),
                "subtitle": self.account.get("employee_status", ""), "avatar": avatar,
                "status": "…" if pending else ui_lang.t("lbl_status_active" if active else "lbl_status_expired"),
                "details": details, "active": active, "pending": pending,
                "badge": self.badge_icon_path or "", "verifiedLevel": level or "",
                "sync": getattr(self,"sync_status",""),"canEdit": not self.is_offline or self.is_local_account}


class LauncherJob(QThread):
    done = Signal(object, bool, object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            from core.command_context import failure_from_exception
            self.done.emit(self,False,failure_from_exception(error))
        else:
            self.done.emit(self, True, result)


class LauncherApplication(QObject):
    diskUsageReady = Signal(str, object)
    startupPrepared = Signal(str)
    startupFinished = Signal()
    fatalRequested = Signal(str)
    operationErrorRequested = Signal(str)

    def __init__(self, app, legacy_type=None, *, store=None, start_services=True, bootstrap=None):
        super().__init__(app)
        self.app = app
        app._quick_launcher = self
        from core.error_reporting import install_error_logging
        install_error_logging()
        self._fatal_pending = []
        self._fatal_visible = False
        self.fatalRequested.connect(self._fatal_message,Qt.QueuedConnection)
        self.operationErrorRequested.connect(self._operation_error,Qt.QueuedConnection)
        self._retiring_editors = []
        self.store = store or ProjectStore()
        self.editor = None
        self.loader = None
        self._jobs = {}
        self._confirmation = None
        self._confirmation_cancel = None
        self._confirmation_delete_path = ""
        self._deferred_completion = None
        self._deletion_timer = QTimer(self)
        self._deletion_timer.setSingleShot(True)
        self._deletion_timer.timeout.connect(self._finish_deferred_job)
        self._quitting = False
        self._dialogs = []
        self._output_location = None
        self._pending_created = None
        self._windows_usage = []
        self._wsl_usage = load_disk_usage_cache() if start_services else []
        self._disk_scanning = set()
        self._last_wsl_scan = 0
        self._prewarm_thread = None
        self._prewarm_complete = False
        self._pending_editor = None
        self._workspace_process = None
        self._startup_started = False
        self._startup_ready = False
        self._startup_verification_pending = False
        self._startup_disks_ready = not start_services
        self._startup_reveal_pending = False
        self._services_started = False
        self._services_enabled = start_services
        self.diskUsageReady.connect(self._disk_usage_ready)
        if bootstrap is None:
            theme.load()
            ui_lang.init()
        self.accounts = AccountState(self)
        self.profile_provider = self.accounts.profile_provider
        if bootstrap is None:
            self.model = LauncherModel(palette=theme.name, language=ui_lang._current_lang,
                                       settings=self._settings(), profile=self.accounts.profile(), parent=self)
        else:
            self.engine,self.window,self.model = bootstrap
            self.model.setParent(self)
            self.model.replaceProfile(self.accounts.profile())
        self.model.setStartupActive(start_services)
        self.startupPrepared.connect(self._startup_prepared)
        self.model.command.connect(self.handle)
        self.accounts.changed.connect(self._refresh_profile)
        if bootstrap is None:
            self.engine, self.window = create_launcher(self.model, visible=False)
        from ui.packages import PackagesModel
        self.packages = PackagesModel(self)
        self.window.setProperty("packageBackend",self.packages)
        self.profile_editor = None
        self.auth = None
        if private_bundle_enabled():
            from ui.accounts import ProfileModel
            self.profile_editor = ProfileModel(self.accounts,self)
            self.profile_editor.failed.connect(self._message)
            self.profile_editor.logoutRequested.connect(self._request_logout)
            self.window.setProperty("profileBackend",self.profile_editor)
            from ui.auth import AuthModel
            self.auth = AuthModel(self)
            self.auth.accountSaved.connect(self._logged_in)
            self.window.setProperty("authBackend",self.auth)
            self._login_finish_timer = QTimer(self)
            self._login_finish_timer.setSingleShot(True)
            self._login_finish_timer.timeout.connect(self.auth.dismissRequested)
        self.window.setProperty("managedClose", True)
        self.window.setTitle(window_title())
        from icons.handler import icon
        self.window.setIcon(QIcon(icon("ic_launcher")))
        self.lifetime = WindowLifetime(self.window, parent=self)
        self.lifetime.finished.connect(self._finish_quit)
        theme.on_change(self._preferences_changed)
        ui_lang.on_change(self._preferences_changed)
        self._reload()
        if not self.model.hasProjects:
            self.model.navigate("create")
        screen = app.primaryScreen()
        if screen is not None:
            self.window.setPosition(screen.availableGeometry().center() - QPoint(self.window.width() // 2, self.window.height() // 2))
        self._trash_timer = QTimer(self)
        self._trash_timer.setInterval(60_000)
        self._trash_timer.timeout.connect(self._maintain_trash)
        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(1500)
        self._disk_timer.timeout.connect(self._scan_disks)
        if start_services:
            self.engine._frame_presenter.presented.connect(self._maybe_start_services)
            self.window.contentPrepared.connect(self._maybe_start_services)
        if bootstrap is not None:
            self.window.setProperty("contentReady",True)
        self.window.show()
        if start_services and self.engine._frame_presenter.ready:
            QTimer.singleShot(0,self._maybe_start_services)

    @Slot()
    def _maybe_start_services(self):
        if self._quitting or self.engine is None:return
        if self.engine._frame_presenter.ready and self.window.property("contentLoaded"):
            self._start_services()

    @Slot()
    def _start_services(self):
        if self._quitting or self._services_started:return
        self._services_started=True
        self._trash_timer.start()
        self._disk_timer.start()
        QTimer.singleShot(0, self._scan_disks)
        # This is the startup run_in_thread worker that prints worker.run() END.
        # Its existing completion callback runs after that worker has exited.
        account = self.accounts.account
        if private_bundle_enabled() and account.get("id") and account.get("dev_id"):
            self._startup_verification_pending = True
            self.accounts.set_verified_done_callback(self._startup_verification_finished)
        QTimer.singleShot(0, self.accounts._start_account_features)
        if private_bundle_enabled():
            QTimer.singleShot(0, self.accounts.begin_restore_session)
            if not self.accounts.account.get("dev_id"):
                QTimer.singleShot(100, self._login)
        QTimer.singleShot(500, self._maintain_trash)
        QTimer.singleShot(0, self._prepare_startup)
        # Account completion alone does not make the launcher interactive yet.
        # Keep the splash over imports, initial drives and the first final layout.
        QTimer.singleShot(0, self._reveal_startup)

    @Slot()
    def _startup_verification_finished(self):
        self._startup_verification_pending = False
        self._reveal_startup()

    def _prepare_startup(self):
        if self._quitting or self._startup_started:
            return
        self._startup_started = True
        # The splash is already exposed. Prepare the actual editor module and
        # configuration process while its original SVG glint keeps animating.
        def prepare():
            manager = None
            try:
                from services.process_manager import get_manager
                manager = get_manager()
                manager.start_process()
            except Exception:
                logging.exception("Cannot prewarm the configuration service")
            error = ""
            try:
                import main  # noqa: F401
                from icons.handler import warm_icon_catalog
                warm_icon_catalog()
                if manager is not None:
                    manager.wait_ready()
            except Exception as exception:
                error = str(exception)
                logging.exception("Cannot prewarm the editor")
            try:
                self.startupPrepared.emit(error)
            except RuntimeError:
                pass
        self._prewarm_thread = threading.Thread(target=prepare, name="rak-quick-editor-import", daemon=True)
        self._prewarm_thread.start()

    @Slot(str)
    def _startup_prepared(self, error):
        self._prewarm_complete = True
        self._reveal_startup()
        pending, self._pending_editor = self._pending_editor, None
        if pending is not None:
            self._construct_editor(*pending)

    @Slot()
    def _reveal_startup(self):
        if (self._quitting or self._startup_ready or self._startup_reveal_pending
                or not self._services_started or self._startup_verification_pending
                or not self._prewarm_complete or not self._startup_disks_ready):
            return
        self._startup_reveal_pending = True
        self.window.frameSwapped.connect(self._startup_frame_ready,Qt.QueuedConnection)
        self.window.update()

    @Slot()
    def _startup_frame_ready(self):
        if not self._startup_reveal_pending:return
        self._startup_reveal_pending = False
        self.window.frameSwapped.disconnect(self._startup_frame_ready)
        if self._quitting:return
        self._startup_ready = True
        self.model.presentationRequested.emit("startupReady", {})

    @staticmethod
    def _settings():
        return {"effects": theme.effect_level, "blurRadius": theme.blur_radius, "animations": theme.animations_enabled,
                "treeMode": theme.tree_view_mode}

    def _preferences_changed(self):
        self.model.refreshPreferences(theme.name, ui_lang._current_lang, self._settings())
        self._update_drives()
        self._refresh_profile()

    def _refresh_profile(self):
        self.model.replaceProfile(self.accounts.profile())

    def _reload(self):
        try:
            rows = self.store.projects()
            # Retain entries on disconnected drives; opening reports the error.
            # Sizes are metadata only: never traverse project contents for cards.
            rows = [{**row, "parentPath": os.path.dirname(row["path"]),
                     "drive": project_storage_key(row["path"]),
                     "sizeLabel": ui_lang.t("lbl_size") + row["cached_size"] if row.get("cached_size") else ""} for row in rows]
            self.model.replaceProjects(rows)
            self._update_drives()
            entries = self.store.trash.entries()
            self.model.replaceTrash([{**entry["project"], "_trash_id": entry["id"],
                                      "available": bool(entry.get("handle")),
                                      "parentPath": os.path.dirname(entry["project"]["path"]),
                                      "toolTip": trash_remaining_text(entry),
                                      "sizeLabel": ui_lang.t("lbl_size")+entry["project"]["cached_size"] if entry["project"].get("cached_size") else ""} for entry in entries])
            self.model.replaceLocation(self._output_location or self.store.resolved_location(), self.store.default_location())
        except Exception as error:
            logging.exception("Cannot load launcher state")
            QTimer.singleShot(0, lambda message=str(error): self._message(message))

    def show(self):
        self.window.show()
        self.window.requestActivate()

    def hide(self):
        self.window.hide()

    def _message(self, text, title=None, *, confirmation=None, delete_path="", on_cancel=None, accept_label="", danger=False, error=False):
        if (error or hasattr(text,'application_error')) and confirmation is None:
            from core.error_reporting import report_fatal
            report_fatal((str(title)+'\n\n' if title else '')+str(text), owner=self,application_error=getattr(text,'application_error',False))
            return
        if not self.window.property("contentLoaded"):
            def show_later():
                self._message(text,title,confirmation=confirmation,delete_path=delete_path,
                              on_cancel=on_cancel,accept_label=accept_label,danger=danger,error=error)
            self.window.contentPrepared.connect(show_later,Qt.SingleShotConnection)
            return
        self._confirmation = confirmation
        self._confirmation_cancel = on_cancel
        self._confirmation_delete_path = delete_path
        self.model.presentationRequested.emit("message", {"title": title or ui_lang.t("lbl_error"),
                                               "text": str(text), "confirm": confirmation is not None,
                                               "acceptLabel": accept_label, "danger": danger})

    @Slot(str)
    def _operation_error(self,text):
        if self._quitting:return
        if not self.window.property('contentLoaded'):
            self.window.contentPrepared.connect(lambda text=text:self._operation_error(text),Qt.SingleShotConnection);return
        from html import escape
        self.model.presentationRequested.emit('message',{'title':'Operation failed','text':escape(text[:500]),'log':text,'danger':True,'width':440})

    @Slot(str)
    def _fatal_message(self,text):
        if self._quitting:return
        if not self.window.property('contentLoaded'):
            self.window.contentPrepared.connect(lambda text=text:self._fatal_message(text),Qt.SingleShotConnection)
            return
        if text and text not in self._fatal_pending:self._fatal_pending.append(text)
        if self._fatal_visible or not self._fatal_pending:return
        from html import escape
        log="\n\n".join(self._fatal_pending);self._fatal_pending.clear()
        self._fatal_visible=True
        self.model.presentationRequested.emit("message",{"title":"Fatal Crash!","fatal":True,"danger":True,"width":380,"log":log,
            "text":'<span style="color:#f08080; font-size:18px; font-weight:600;">Fatal Crash!</span><br><br>'+escape(ui_lang.t("dlg_crash_message"))})

    @Slot(str, "QVariantMap")
    def handle(self, command, data):
        try:
            if command == "quit":
                self.quit()
            elif self._quitting:
                return
            elif command == "startupRevealed":
                self.model.setStartupActive(False)
                self.startupFinished.emit()
            elif command == "createPopupClosed" and self._pending_created is not None:
                record, self._pending_created = self._pending_created, None
                self._open_project(record,"create_popup")
            elif command == "theme":
                theme.set(data["name"])
            elif command == "language":
                ui_lang.set_lang(data["code"])
                self._refresh_profile()
            elif command == "setting":
                {"effects": theme.set_effect_level, "animations": theme.set_animations_enabled,
                 "blurRadius": theme.set_blur_radius,
                 "treeMode": theme.set_tree_view_mode}[data["key"]](data["value"])
                if data["key"] == "animations" and not data["value"]:
                    self._finish_deferred_job()
            elif command == "newProject" and not self.model.busy:
                self._output_location = None
                self._reload()
                self.model.presentationRequested.emit("newProject", {})
            elif command == "importProject" and not self.model.busy:
                self.model.presentationRequested.emit("chooseFolder", {
                    "purpose": "importProject",
                    "url": QUrl.fromLocalFile(self.model.location).toString()
                })
            elif command in ("chooseOutput", "defaultLocation"):
                self.model.presentationRequested.emit("chooseFolder", {"purpose": command,
                    "url": QUrl.fromLocalFile(self.model.location).toString()})
            elif command == "folderSelected":
                path = QUrl(data["url"]).toLocalFile()
                if path:
                    path = os.path.normpath(path)
                    if data["purpose"] == "defaultLocation":
                        self.store.set_location(path)
                    elif data["purpose"] == "importProject":
                        self._import_project(path)
                    else:
                        self._output_location = path
                    self._reload()
            elif command == "resetLocation":
                self.store.set_location("")
                self._reload()
            elif command == "toggleDrive" and not self.model.busy:
                key = data["key"]
                theme.set_project_drive_enabled(key, not theme.project_drive_enabled(key))
                self._update_drives()
            elif command == "createProject" and not self.model.busy:
                location = self.model.location
                self._job(lambda: self.store.create(data, location=location), self._created)
            elif command == "openProject" and not self.model.busy:
                self._open_project(self._project(data["path"]))
            elif command == "projectMenu" and not self.model.busy:
                self._project_menu(data)
            elif command == "projectAction" and not self.model.busy:
                self._project_action(data)
            elif command == "restoreProject" and not self.model.busy:
                self._job(lambda: self.store.trash.restore(data["id"]))
            elif command == "confirm":
                operation, self._confirmation = self._confirmation, None
                self._confirmation_cancel = None
                if operation is not None and not self.model.busy:
                    self._job(operation, delete_path=self._confirmation_delete_path)
                self._confirmation_delete_path = ""
            elif command == "cancelConfirmation":
                self._confirmation = None
                self._confirmation_delete_path = ""
                callback,self._confirmation_cancel = self._confirmation_cancel,None
                if callback is not None:
                    callback()
            elif command in ("license", "credit", "packages"):
                self._open_dialog(command)
            elif command == "themePicker":
                self.model.presentationRequested.emit("showDialog",{"kind":"theme"})
            elif command == "profile":
                self._open_account()
            elif command == "copyText":
                self.app.clipboard().setText(data["text"])
            elif command == "errorLog":
                self.model.presentationRequested.emit("errorLog",data)
            elif command == "fatalDismissed":
                self._fatal_visible=False
                self._fatal_message("")
        except Exception as error:
            logging.exception("Quick launcher command %s failed", command)
            self._message(ui_lang.t("dlg_pin_limit_msg", n=3) if str(error) == "pin_limit" else str(error))

    def _project(self, path):
        return next(row for row in self.store.projects() if os.path.normcase(row["path"]) == os.path.normcase(path))

    def _scan_disks(self):
        if self._quitting:
            return
        def start(kind, operation):
            if kind in self._disk_scanning:
                return
            self._disk_scanning.add(kind)
            def run():
                try:
                    rows = operation()
                except Exception:
                    logging.exception("Cannot scan %s volumes", kind)
                    rows = None
                try:
                    self.diskUsageReady.emit(kind, rows)
                except RuntimeError:
                    pass
            threading.Thread(target=run, name="rak-quick-disks-" + kind, daemon=True).start()
        from core.disk_monitor import list_windows_disk_usage
        start("windows", list_windows_disk_usage)
        if time.monotonic() - self._last_wsl_scan >= 60:
            self._last_wsl_scan = time.monotonic()
            from core.wsl_manager import list_disk_usage, save_disk_usage_cache
            def scan_wsl():
                rows = list_disk_usage()
                save_disk_usage_cache(rows)
                return rows
            start("wsl", scan_wsl)

    @Slot(str, object)
    def _disk_usage_ready(self, kind, rows):
        self._disk_scanning.discard(kind)
        if rows is not None and not self._quitting:
            if kind == "windows":
                self._windows_usage = rows
            else:
                self._wsl_usage = rows
            self._update_drives()
        if kind == "windows":
            self._startup_disks_ready = True
            self._reveal_startup()

    def _update_drives(self):
        rows = []
        for index, row in enumerate(self._windows_usage + self._wsl_usage):
            total, used = row["total"], row["used"]
            special = bool(row.get("distro"))
            percent = row.get("percent", int(100 * used / total))
            rows.append({**row, "active": theme.project_drive_enabled(row["key"]),
                         "name": "WSL · " + row["distro"] if special else row["key"],
                         "distro": row.get("distro", ""),
                         "usage": f"{used / 1024**3:.1f} / {total / 1024**3:.1f} GB",
                         "fraction": min(1., max(0., percent / 100)), "percent": percent,
                         "fill": project_drive_fill(theme,special),
                         "barColor": theme.accent_hex if special else ("#F25D91" if index == 0 else "#9368F5" if index == 1 else theme.gauge_hex(percent))})
        excluded = {row["drive"] for row in self.model._projects if not theme.project_drive_enabled(row.get("drive", ""))}
        self.model.replaceDrives(rows, excluded)

    def _project_menu(self, data):
        if data.get("id"):
            entry = next(row for row in self.store.trash.entries() if row["id"] == data["id"])
            actions = [{"label": ui_lang.t("dlg_btn_delete"), "action": "delete", "danger": True, "enabled": bool(entry.get("handle"))},
                       {"label": ui_lang.t("btn_restore_project"), "action": "restore", "enabled": bool(entry.get("handle"))}]
        else:
            project = self._project(data["path"])
            actions = [{"label": ui_lang.t("menu_open_project"), "action": "open"},
                       {"label": ui_lang.t("menu_unpin_project" if project.get("pinned") else "menu_pin_project"), "action": "pin"},
                       {"label": ui_lang.t("menu_delete_project"), "action": "delete", "danger": True}]
            if project.get("imported"):
                actions.insert(2, {"label": ui_lang.t("menu_remove_from_list"), "action": "remove_from_list", "danger": True})
        self.model.presentationRequested.emit("projectMenu", {**data, "actions": actions})

    def _project_action(self, data):
        action, identifier = data["action"], data.get("id")
        if action == "restore":
            self._job(lambda: self.store.trash.restore(identifier))
        elif action == "open" and not identifier:
            self._open_project(self._project(data["path"]))
        elif action == "pin" and not identifier:
            self.store.toggle_pin(data["path"])
            self._reload()
        elif action == "remove_from_list":
            record = self._project(data["path"])
            text = ui_lang.t("dlg_remove_from_list_msg", name=record["project_name"])
            self._message(text, ui_lang.t("dlg_remove_from_list_title"),
                          confirmation=lambda: self._remove_from_list(record["path"]))
        elif action == "delete":
            if identifier:
                entry = next(row for row in self.store.trash.entries() if row["id"] == identifier)
                text = ui_lang.t("dlg_delete_msg", name=entry["project"]["project_name"])
                text = re.sub("<[^>]*>", "", text.replace("<br>", "\n"))
                self._message(text, ui_lang.t("dlg_delete_title"), confirmation=lambda: self.store.trash.delete(identifier))
            else:
                record = self._project(data["path"])
                self._message(ui_lang.t("dlg_trash_msg", name=record["project_name"]), ui_lang.t("dlg_trash_title"),
                              confirmation=lambda: self.store.trash.recycle(record), delete_path=record["path"])

    def _job(self, operation, callback=None, *, delete_path=""):
        self.model.setBusy(True)
        self.model.setDeletingPath(delete_path)
        job = LauncherJob(operation, self)
        job.delete_path = delete_path
        job.started_at = time.monotonic()
        self._jobs[job] = callback
        job.done.connect(self._job_done)
        job.finished.connect(lambda: self._jobs.pop(job, None))
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot(object, bool, object)
    def _job_done(self, job, ok, result):
        callback = self._jobs.get(job)
        if ok and job.delete_path and self.model.settings["animations"] and not self._quitting:
            remaining = max(0,round(550-(time.monotonic()-job.started_at)*1000))
            if remaining:
                self._deferred_completion = (callback,ok,result)
                self._deletion_timer.start(remaining)
                return
        self._complete_job(callback,ok,result)

    def _finish_deferred_job(self):
        if self._deferred_completion is not None:
            self._deletion_timer.stop()
            completion,self._deferred_completion = self._deferred_completion,None
            self._complete_job(*completion)

    def _complete_job(self, callback, ok, result):
        deleting = bool(self.model.deletingPath) and ok
        self.model.setDeletingPath("")
        self._reload()
        # Keep deletion input blocked through the overlapping exit/reflow.
        # Ordinary filter motion still permits subsequent drive clicks.
        if deleting and self.model.projectRows.animating:
            self.model.projectRows.animationFinished.connect(self._deletion_motion_finished, Qt.SingleShotConnection)
        else:
            self.model.setBusy(False)
        if self._quitting:
            return
        if not ok:
            self._message(result)
        elif callback is not None:
            callback(result)
        elif isinstance(result,dict) and result.get("logout"):
            self._login()

    def _deletion_motion_finished(self):
        if not self._quitting:
            self.model.setBusy(False)

    def _maintain_trash(self):
        if (not self._startup_disks_ready or self._jobs or self.model.busy or self._quitting):
            return
        # Do not immediately probe a volume that the capacity scan skipped:
        # automatic maintenance would block launcher input on that same drive.
        available = {row["key"].casefold() for row in self._windows_usage + self._wsl_usage}
        def maintain():
            predicate = (lambda path: project_storage_key(path).casefold() in available) if os.name == "nt" else None
            return self.store.trash.maintain(drive_available=predicate)
        self._job(maintain)

    def _import_project(self, folder_path):
        def job():
            try:
                return self.store.import_project(folder_path)
            except ValueError:
                from core.project_trash import _key
                for row in self.store.projects():
                    if _key(row["path"]) == _key(folder_path):
                        return row
                raise
        def on_done(record):
            self._open_project(record, "import")
        self._job(job, on_done)

    def _remove_from_list(self, path):
        self.store.remove_from_list(path)
        self._reload()

    def _created(self, record):
        self._pending_created = record
        self.model.presentationRequested.emit("created", {})
        # Full-page creation has no popup to dismiss.
        if self.model.page == "create":
            self._pending_created = None
            self._open_project(record,"create")

    def _open_project(self, record, origin="existing"):
        if self.editor is not None or self.model.openingPath or getattr(self, "_workspace_process", None) is not None:
            return
        if not os.path.isdir(record["path"]):
            raise FileNotFoundError(record["path"])
        project_root = record["path"]
        opened_at = time.perf_counter()
        log_project_event("OPEN_START", project_root, origin=origin)
        self.model.setBusy(True)
        self.model.setOpeningPath(project_root)

        if getattr(self, "_in_process_workspace", False) or os.environ.get("RAK_TEST_IN_PROCESS") == "1":
            QTimer.singleShot(0, lambda: self._construct_editor(record, origin, opened_at))
            return

        self._spawn_workspace_process(record, origin, opened_at)

    def _spawn_workspace_process(self, record, origin, opened_at):
        from PySide6.QtCore import QProcess, QProcessEnvironment
        process = QProcess(self)
        self._workspace_process = process

        is_compiled = getattr(sys, "frozen", False) or "__compiled__" in globals() or (
            Path(sys.executable).suffix.lower() == ".exe" and
            Path(sys.executable).name.lower() not in ("python.exe", "pythonw.exe")
        )

        args = ["--workspace", record["path"], record["port1"]]
        if record.get("port2"):
            args.append(record["port2"])
        args.extend(["--palette", self.model.paletteName, "--lang", self.model._language])

        if is_compiled:
            program = sys.executable
            working_dir = str(Path(sys.executable).resolve().parent)
        else:
            program = sys.executable
            repo_root = str(Path(__file__).resolve().parents[1])
            app_script = str(Path(__file__).resolve().parents[1] / "app.py")
            args.insert(0, app_script)
            working_dir = repo_root

        process.setWorkingDirectory(working_dir)
        process.setProgram(program)
        process.setArguments(args)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)

        return_requested = [False]
        stderr_lines = []

        def on_stdout():
            data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            for line in data.splitlines():
                if "[RAK_WORKSPACE_READY]" in line:
                    self._on_workspace_process_ready(record["path"], opened_at, origin)
                elif "[RAK_WORKSPACE_RETURN]" in line:
                    return_requested[0] = True
                elif "[RAK_WORKSPACE_FAILED]" in line:
                    err = line.partition("[RAK_WORKSPACE_FAILED]")[2].strip()
                    self._on_workspace_process_failed(err)

        def on_stderr():
            data = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
            if data:
                stderr_lines.append(data)

        def on_finished(exit_code, exit_status):
            self._workspace_process = None
            if return_requested[0] or exit_code == 42:
                self.on_return_from_editor()
            elif exit_code == 0:
                self.quit()
            else:
                err_text = "".join(stderr_lines).strip()
                self._on_workspace_process_failed(err_text or f"Workspace process exited with code {exit_code}")

        def on_error(err):
            if process is getattr(self, "_workspace_process", None):
                self._workspace_process = None
                self._on_workspace_process_failed(f"Failed to start workspace: {err}")

        process.readyReadStandardOutput.connect(on_stdout)
        process.readyReadStandardError.connect(on_stderr)
        process.finished.connect(on_finished)
        process.errorOccurred.connect(on_error)
        process.start()

    def _on_workspace_process_ready(self, project_root, opened_at, origin):
        self._log_project_entered(project_root, opened_at, origin)
        self.model.setOpeningPath("")
        if not self.model.settings["animations"]:
            self.hide()
            self.window.setOpacity(1)
            return
        fade = QPropertyAnimation(self.window, b"opacity", self)
        fade.setDuration(160)
        fade.setStartValue(self.window.opacity())
        fade.setEndValue(0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        def finish():
            fade.deleteLater()
            self.hide()
            self.window.setOpacity(1)
        fade.finished.connect(finish)
        fade.start()

    def _on_workspace_process_failed(self, error):
        logging.error("Workspace process failed: %s", error)
        if getattr(self, "_workspace_process", None) is not None:
            try:
                self._workspace_process.kill()
            except Exception:
                pass
            self._workspace_process = None
        self.model.setOpeningPath("")
        self.model.setBusy(False)
        self.window.setOpacity(1)
        self.show()
        self._message(str(error))

    def _show_editor_with_reveal(self, project_root, opened_at, origin):
        editor, loader = self.editor, self.loader
        def show_editor():
            if self.editor is not editor or getattr(editor,"_closing",False):
                return
            editor.reveal()
            self._log_project_entered(project_root,opened_at,origin)
            self.model.setOpeningPath("")
            self.hide()
            self.window.setOpacity(1)

        if loader is not None:
            loader._block_close = True
            fade = QPropertyAnimation(loader.window,b"opacity",loader)
            fade.setDuration(160 if self.model.settings["animations"] else 0)
            fade.setStartValue(loader.window.opacity()); fade.setEndValue(0)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            def finish_loader():
                dismissed = not loader.isVisible()
                loader._block_close = False
                loader.close(); loader._handoff_fade = None
                fade.deleteLater(); loader.deleteLater()
                if self.loader is loader: self.loader = None
                if dismissed:
                    log_project_event("OPEN_CANCELLED",project_root,origin=origin)
                else: show_editor()
            fade.finished.connect(finish_loader)
            loader._handoff_fade = fade
            fade.start()
            return

        if not self.model.settings["animations"]:
            show_editor()
            return

        fade = QPropertyAnimation(self.window, b"opacity", self)
        fade.setDuration(160)
        fade.setStartValue(self.window.opacity())
        fade.setEndValue(0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.finished.connect(lambda: [fade.deleteLater(), show_editor()])
        fade.start()

    def _log_project_entered(self, project_root, opened_at, origin):
        if self.editor is not None:
            self.editor._project_session_started_at = time.perf_counter()
        log_project_event("ENTER", project_root, origin=origin,
                          open_ms=round((time.perf_counter()-opened_at)*1000),load_status="ok")

    def _cancel_project_preparation(self, editor=None):
        target = editor or self.editor
        if target is not None and self.editor is not target:return
        self.editor = None
        if target is not None:
            # Keep the QObject alive until its scene graph and WebChannels have
            # finished their asynchronous shutdown. A cancelled open never quits.
            target._shutdown_and_quit = lambda:None
            target._returning = False
            self._retiring_editors.append(target)
            def retired():
                if target in self._retiring_editors:self._retiring_editors.remove(target)
                target.deleteLater()
            target.closed.connect(retired)
            target.abort_project_preparation()
        loader,self.loader = self.loader,None
        if loader is not None:
            loader._on_hide_requested = None
            animation=getattr(loader,"_handoff_fade",None)
            if animation:animation.stop()
            loader._block_close=False
            loader.close();loader.deleteLater()

    def _construct_editor(self, record, origin, opened_at, loader=None):
        if not self.model.openingPath or self._quitting:
            return
        if self._prewarm_thread is not None and not self._prewarm_complete:
            # Importing Main here while the prewarm owns its import lock would
            # freeze the GUI/spinner. Resume from its queued completion instead.
            self._pending_editor = (record, origin, opened_at)
            return
        try:
            from main import Main
            editor = Main(record["path"], record["port1"], record.get("port2") or None,
                          preferences=self.model, profile_provider=self.profile_provider,
                          enable_private_services=self._services_enabled,enable_cli_services=self._services_enabled,parent=self)
            self.editor = editor
            # Main's normal close also waits for the launcher's filesystem jobs
            # and releases its scene graph before the application event loop ends.
            editor._shutdown_and_quit = self.quit
            editor.return_to_project_signal.connect(self.on_return_from_editor)
            editor.preparation_status.connect(self._preparation_status)
            editor.preparation_failed.connect(self._preparation_failed)
            editor.ready.connect(lambda: self._editor_ready(editor, record["path"], opened_at, origin))
        except Exception as error:
            self._preparation_failed(error)

    def _preparation_status(self, status):
        if self.loader is not None:
            self.loader.set_status(status)

    def _preparation_failed(self, error):
        logging.error("Project preparation failed: %s", error)
        self._cancel_open()
        self._message(str(error))

    def _editor_ready(self, editor, path, opened_at, origin):
        if self.editor is editor:
            if self.loader is not None:
                self.loader._block_close = False
            self._show_editor_with_reveal(path, opened_at, origin)

    def _cancel_open(self):
        self._pending_editor = None
        self._workspace_process = None
        self._cancel_project_preparation(self.editor)
        self.model.setOpeningPath("")
        self.model.setBusy(False)
        self.window.setOpacity(1)
        self.show()

    def on_return_from_editor(self):
        editor=self.editor
        self.editor = None
        if editor is not None:editor.deleteLater()
        if self.loader is not None:
            self.loader._block_close = False
            self.loader.close()
            self.loader = None
        self.model.setOpeningPath("")
        self.model.setBusy(False)
        self.model.navigate("projects")
        self._reload()
        self.window.setOpacity(1)
        self.show()

    def _open_dialog(self, kind):
        self.model.presentationRequested.emit("showDialog",{"kind":kind})

    def _login(self):
        self.model.presentationRequested.emit("login",{})

    def _logged_in(self,local):
        self.auth.finishing()
        if not local:
            from rennsproject.core.account_manager import clear_pending_signup
            clear_pending_signup()
            self.accounts.set_verified_done_callback(self._login_finished)
        self.accounts.sync_status=""
        self.accounts.refresh_after_login()
        self._login_finish_timer.start(200 if local else 8000)

    def _login_finished(self):
        self._login_finish_timer.stop()
        self.auth.dismissRequested.emit()

    def _open_account(self):
        if not private_bundle_enabled():
            return
        self.model.presentationRequested.emit("showDialog",{"kind":"profile"})

    def _request_logout(self):
        self._message(ui_lang.t("dlg_logout_msg"),ui_lang.t("dlg_logout_title"),confirmation=self._logout,
                      on_cancel=self._open_account,accept_label=ui_lang.t("dlg_btn_logout"),danger=True)

    def _logout(self):
        from rennsproject.core.account_manager import clear_account
        clear_account()
        try:
            from rennsproject.core.secure_store import clear_session
            from rennsproject.core.supabase_client import logout
            clear_session()
            logout()
        except Exception:
            pass
        # Auth presentation belongs to the GUI thread after the worker finishes.
        return {"logout":True}

    def quit(self):
        if getattr(self, "_workspace_process", None) is not None:
            try:
                self._workspace_process.kill()
            except Exception:
                pass
            self._workspace_process = None
        if self._quitting:
            return
        self._quitting = True
        self.packages.close()
        self.engine._appearance_captures.close()
        self.engine._dialog_stack.close()
        self._finish_deferred_job()
        self._trash_timer.stop()
        self._disk_timer.stop()
        theme.remove_listener(self._preferences_changed)
        ui_lang.remove_listener(self._preferences_changed)
        self.hide()
        self._await_jobs()

    def _await_jobs(self):
        restore = getattr(self.accounts, "_restore_worker", None)
        if (self._retiring_editors or self._jobs or self.packages.running or (self.profile_editor is not None and self.profile_editor.running)
                or (self.auth is not None and self.auth.running)
                or (restore is not None and restore.isRunning())
                or (self._prewarm_thread is not None and self._prewarm_thread.is_alive())):
            QTimer.singleShot(50, self._await_jobs)
            return
        for dialog in self._dialogs:
            dialog.close()
        self.lifetime.close()

    def _finish_quit(self):
        self.window.setProperty("managedClose", False)
        self.window.close()
        # Destroy the QML engine while the event loop still processes render
        # cleanup, before QtWebEngine and the application begin tearing down.
        engine,self.engine = self.engine,None
        engine.destroyed.connect(self._quit_services)
        engine.deleteLater()

    def _quit_services(self):
        try:
            from main_ext.config_engine_client import _ServiceConnection
            connection = _ServiceConnection.get()
            if connection.is_connected():
                connection.ws.close()
            from services.process_manager import get_manager
            get_manager().stop()
        except Exception:
            logging.exception("Cannot stop configuration service")
        from core.threader import stop_all_threads, quit_when_idle
        stop_all_threads()
        quit_when_idle()


def run_launcher(app, legacy_type=None, *, on_ready=None):
    from core.fonts import apply_app_font
    theme.load()
    ui_lang.init()
    apply_app_font(app,polish_widgets=False)
    model=LauncherModel(palette=theme.name,language=ui_lang._current_lang,
                        settings=LauncherApplication._settings(),parent=app)
    model.setStartupActive(True)
    engine,window=create_launcher(model,visible=False,defer_content=True)
    from icons.handler import icon
    window.setTitle(window_title());window.setIcon(QIcon(icon("ic_launcher")))
    screen=app.primaryScreen()
    if screen is not None:
        window.setPosition(screen.availableGeometry().center()-QPoint(window.width()//2,window.height()//2))
    state={"runtime":None}
    def prepare():
        if not window.isVisible():app.quit();return
        try:
            runtime=LauncherApplication(app,legacy_type,bootstrap=(engine,window,model))
            state["runtime"]=runtime
            if on_ready is not None:
                runtime.startupPrepared.connect(lambda error:on_ready())
        except Exception:
            logging.exception("Cannot prepare launcher behind startup splash")
            if on_ready is not None:on_ready()
            app.exit(1)
    engine._frame_presenter.presented.connect(lambda:QTimer.singleShot(0,prepare))
    def content_failed():
        logging.error("Cannot load launcher content: %s","\n".join(engine._diagnostics))
        if on_ready is not None:on_ready()
        app.exit(1)
    window.contentFailed.connect(content_failed)
    window.visibleChanged.connect(lambda:app.quit() if not window.isVisible() and state["runtime"] is None else None)
    window.show()
    result = app.exec()
    runtime=state["runtime"]
    remaining=runtime.engine if runtime is not None else engine
    if remaining is not None:
        remaining.deleteLater()
        app.processEvents()
    return result
