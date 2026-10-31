# SPDX-License-Identifier: MPL-2.0
"""Native login state using the existing authentication transport."""
from PySide6.QtCore import QObject, Property, QThread, QTimer, Signal, Slot


class ConnectionProbe(QThread):
    done = Signal(bool)
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation=operation
    def run(self):
        try:
            connected=bool(self.operation())
        except Exception:
            connected=False
        self.done.emit(connected)


class AuthModel(QObject):
    changed = Signal()
    shake = Signal(str)
    accountSaved = Signal(bool)
    dismissRequested = Signal()

    def __init__(self,parent=None,*,service=None,save=None,pending=None):
        super().__init__(parent)
        if service is None:
            from rennsproject.core.auth_service import AuthService
            service=AuthService(self)
        self.service=service
        self._save,self._pending=save,pending
        self._mode="signin";self._busy=False;self._label="Sign in";self._alert="";self._email="";self._offline=False
        self._probes=set()
        service.signin_success.connect(self._success)
        service.google_success.connect(lambda profile:self._success({**profile,"auth_provider":profile.get("auth_provider","google")}))
        service.signup_success.connect(self._success)
        service.signup_pending.connect(self._signup_pending)
        service.signin_error.connect(self._error)
        service.signup_error.connect(self._error)
        service.google_error.connect(self._error)

    @Property(str,notify=changed)
    def mode(self):return self._mode
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(str,notify=changed)
    def label(self):return self._label
    @Property(str,notify=changed)
    def alert(self):return self._alert
    @Property(str,notify=changed)
    def successEmail(self):return self._email
    @Property(bool,notify=changed)
    def offline(self):return self._offline
    @property
    def running(self):
        worker=getattr(self.service,"_worker",None)
        return bool(self._probes) or (worker is not None and worker.isRunning())

    @Slot(str)
    def setMode(self,mode):
        if self._busy or mode not in ("signin","signup","local"):return
        self._mode=mode;self._offline=False;self._alert=""
        self._set_busy(False)

    @Slot()
    def clearAlert(self):
        if self._alert:self._alert="";self.changed.emit()

    def _set_busy(self,on,label=None):
        self._busy=on
        self._label=label or {"signin":"Sign in","signup":"Create account","local":"Enter"}.get(self._mode,"Sign in")
        self.changed.emit()

    def _validation(self,message,field):
        self._alert=message;self.changed.emit();self.shake.emit(field)

    def _save_account(self,*args,**kwargs):
        save=self._save
        if save is None:
            from rennsproject.core.account_manager import save_account
            save=save_account
        return save(*args,**kwargs)

    @Slot(str,str,str,str)
    def submit(self,email,password,username,role):
        if self._busy:return
        self.clearAlert()
        if self._mode=="local":
            if not username.strip():self._validation("Username is required.","user");return
            self._set_busy(True,"Entering…")
            try:
                self._save_account(username.strip(),"Local Account","000","LOCAL_DEV","LOCAL_ID",auth_provider="local")
                self.accountSaved.emit(True)
            except Exception as error:self._error(str(error))
            return
        if not email.strip() or not password:
            self._validation("Email and password are required.","email");return
        if self._mode=="signup":
            if not username.strip():self._validation("Username is required.","user");return
            self._set_busy(True,"Creating account…")
            self.service.signup(email.strip(),password,username.strip(),role.strip() or "Developer")
        else:
            self._connect(lambda:self.service.signin(email.strip(),password),"Signing in…")

    @Slot()
    def google(self):
        if not self._busy:
            self.clearAlert()
            self._connect(self.service.google,"Opening browser…")

    def _connect(self,operation,label):
        # Preserve the source's offline fallback without blocking native motion
        # for its two-second socket check.
        self._set_busy(True,label)
        probe=ConnectionProbe(self.service.has_internet,self)
        self._probes.add(probe)
        probe.done.connect(lambda connected:self._connected(connected,operation))
        probe.finished.connect(lambda:self._probe_finished(probe))
        probe.start()

    def _probe_finished(self,probe):
        self._probes.discard(probe);probe.deleteLater()

    def _connected(self,connected,operation):
        if connected:operation()
        else:
            self._mode="local";self._offline=True;self._alert="";self._set_busy(False)

    def _success(self,profile):
        try:
            self._save_account(*(profile[key] for key in ("username","employee_status","avatar","dev_id","id")),auth_provider=profile.get("auth_provider","email"))
            self._set_busy(True,"Finishing up…")
            self.accountSaved.emit(False)
        except Exception as error:self._error(str(error))

    def _signup_pending(self,email,username,role):
        pending=self._pending
        if pending is None:
            from rennsproject.core.account_manager import save_pending_signup
            pending=save_pending_signup
        try:
            pending(email=email,username=username,employee_status=role,avatar="000")
            self._email=email;self._mode="success";self._set_busy(False)
        except Exception as error:self._error(str(error))

    def _error(self,message):
        from core.error_reporting import report_fatal
        report_fatal('Authentication\n\n'+str(message),owner=self,application_error=False)
        self._set_busy(False)
        self._alert=message;self.changed.emit();self.shake.emit("")

    @Slot()
    def finishing(self):self._set_busy(True,"Finishing up…")

    @Slot()
    def reset(self):
        if not self.running:
            self._busy=False;self.setMode("signin")
