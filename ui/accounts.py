# SPDX-License-Identifier: MPL-2.0
"""Account editing state. Draft edits never touch the saved account."""
import logging
from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot


class ProfileSync(QThread):
    done = Signal(str)

    def __init__(self, account, parent=None, operation=None):
        super().__init__(parent)
        self.account, self.operation = dict(account), operation

    def run(self):
        try:
            if self.operation is not None:
                self.operation(self.account)
            else:
                from rennsproject.core.login_auth import update_profile, restore_session, AuthError
                args = tuple(self.account.get(key) for key in ("id", "username", "employee_status", "avatar"))
                try:
                    update_profile(*args)
                except AuthError:
                    if restore_session() is None:
                        raise
                    update_profile(*args)
        except Exception:
            logging.exception('Could not sync account profile')
            self.done.emit("local only")
        else:
            self.done.emit("synced")


class ProfileModel(QObject):
    changed = Signal()
    saved = Signal()
    failed = Signal(str)
    logoutRequested = Signal()

    def __init__(self, accounts, parent=None, *, save=None, sync=None):
        super().__init__(parent)
        self.accounts, self._save, self._sync = accounts, save, sync
        self._draft = {}
        self._jobs = set()
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(lambda:self._status(""))

    @Property("QVariantMap", notify=changed)
    def draft(self):
        return self._draft

    @Property("QVariantList", constant=True)
    def avatars(self):
        from rennsproject.avatar.handler import list_avatars, avatar_path
        return [{"key": key, "url": QUrl.fromLocalFile(avatar_path(key)).toString()} for key in list_avatars()]

    @Property(str, notify=changed)
    def preview(self):
        from rennsproject.avatar.handler import resolve_avatar_path
        return QUrl.fromLocalFile(resolve_avatar_path(self._draft.get("avatar", "000"),self.accounts.account)).toString()

    @Slot()
    def begin(self):
        self._draft = dict(self.accounts.account)
        self.changed.emit()

    @Slot(str)
    def chooseAvatar(self, key):
        if key in {row["key"] for row in self.avatars}:
            self._draft = {**self._draft,"avatar": key}
            self.changed.emit()

    @Slot(str, str)
    def save(self, username, role):
        try:
            save = self._save
            if save is None:
                from rennsproject.core.account_manager import save_account
                save = save_account
            before = self.accounts.account
            account = save(username, role, self._draft.get("avatar", before.get("avatar","000")),before.get("dev_id"),before.get("id"))
            self.accounts.account = account
            self.accounts.profile_provider.update_account(account)
            self._clear_timer.stop()
            self._status("syncing…")
            job = ProfileSync(account,self,operation=self._sync)
            self._jobs.add(job)
            job.done.connect(self._synced)
            job.finished.connect(lambda job=job:self._finished(job))
            job.start()
            self.saved.emit()
        except Exception as error:
            logging.exception('Could not save account profile')
            self.failed.emit(str(error))

    def _finished(self, job):
        self._jobs.discard(job)
        job.deleteLater()

    def _status(self, state):
        self.accounts.sync_status = state
        self.accounts.changed.emit()

    def _synced(self, state):
        self._status(state)
        if state == "synced":
            self._clear_timer.start(1200)

    @property
    def running(self):
        return bool(self._jobs)
