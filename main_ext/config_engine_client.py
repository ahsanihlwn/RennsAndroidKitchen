# SPDX-License-Identifier: MPL-2.0
import json
import re
import uuid
import threading

from PySide6.QtCore import (
    QObject, QEventLoop, QTimer, QUrl, Signal, Slot,
    QCoreApplication, QMetaObject, Qt, Q_ARG, QThread,
)
from PySide6.QtWebSockets import QWebSocket

from services.protocol import (
    OP_CONFIG_INIT, OP_CONFIG_APPLY, OP_CONFIG_MOVE_PATHS,
    OP_CONFIG_MOVE_PATHS_CROSS, OP_CONFIG_DELETE_PATHS,
    OP_CONFIG_APPLY_CTX_ONLY, OP_CONFIG_APPLY_PERM_ONLY,
    OP_CONFIG_APPLY_REPACK, OP_CONFIG_PREDICTED_CTX,
    OP_CONFIG_VALIDATE_FS, OP_CONFIG_HANDLE_TRANSFER,
    OP_CONFIG_REBUILD_ROOT_MAP, OP_CONFIG_GET_STATE,
    OP_CONFIG_LOAD_FULL_STATE,
    OP_PRESCAN_FULL, OP_PRESCAN_FAST,
    OP_LOAD_FILE_CONTEXT, OP_LOAD_FS_CONFIG,
    OP_PAYLOAD_EXTRACT_PARTITION,
    OP_PING,
)

# Timeout defaults (ms)
TIMEOUT_DEFAULT = 30_000      # apply / move / delete — file I/O ringan
TIMEOUT_HEAVY   = 300_000     # repack / predicted_ctx / prescan — bisa lama
TIMEOUT_PAYLOAD = 3_600_000   # payload extract bisa lama di disk lambat


# CONNECTION SINGLETON

class _ServiceConnection(QObject):

    eventReceived = Signal(str, dict)  # (event_name, payload)

    _instance: "_ServiceConnection | None" = None

    @classmethod
    def get(cls) -> "_ServiceConnection":
        if cls._instance is None:
            app = QCoreApplication.instance()
            if app is None or QThread.currentThread() != app.thread():
                raise RuntimeError("Initialize the config service connection on the Qt main thread before starting workers")
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Config service sockets must be created on the Qt main thread")
        super().__init__(app)
        self.ws = QWebSocket(parent=self)


        self._pending_loops: dict[str, QEventLoop] = {}
        self._pending_events: dict[str, threading.Event] = {}
        self._results: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._connected = False

        self.ws.textMessageReceived.connect(self._on_msg)
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.errorOccurred.connect(self._on_error)

    # connection lifecycle

    def connect_to(self, port: int, timeout_ms: int = 5000) -> bool:

        if self._connected:
            return True

        self.ws.open(QUrl(f"ws://127.0.0.1:{port}"))

        loop = QEventLoop()
        self.ws.connected.connect(loop.quit)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        loop.exec()
        timer.stop()

        return self._connected

    def is_connected(self) -> bool:
        return self._connected

    def _on_connected(self):
        self._connected = True
        print(f"[ServiceConnection] connected to service")

    def _on_disconnected(self):
        self._connected = False
        print(f"[ServiceConnection] disconnected from service")
        # bangunin semua pending wait — biar gak gantung selamanya
        with self._lock:
            for req_id, loop in list(self._pending_loops.items()):
                self._results[req_id] = {
                    "id": req_id, "ok": False,
                    "error": "disconnected",
                    "msg": "service connection lost while waiting",
                }
                loop.quit()
            for req_id, ev in list(self._pending_events.items()):
                self._results[req_id] = {
                    "id": req_id, "ok": False,
                    "error": "disconnected",
                    "msg": "service connection lost while waiting",
                }
                ev.set()

    def _on_error(self, err):
        print(f"[ServiceConnection] ws error: {err}")

    # message dispatch

    def _on_msg(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception as e:
            print(f"[ServiceConnection] bad msg: {e}")
            return

        # event push (no id required)
        if "event" in msg:
            try:
                self.eventReceived.emit(msg["event"], msg)
            except Exception:
                pass
            return

        # reply
        req_id = msg.get("id")
        if not req_id:
            return

        with self._lock:
            if req_id not in self._pending_loops and req_id not in self._pending_events:
                # Replies arriving after a timeout have no consumer. Keeping
                # large config dictionaries here would retain them indefinitely.
                return
            self._results[req_id] = msg
            loop = self._pending_loops.get(req_id)
            ev = self._pending_events.get(req_id)
        if loop is not None:
            loop.quit()
        if ev is not None:
            ev.set()

    # send (must run on main thread)

    @Slot(str)
    def _send_text(self, payload: str):

        try:
            self.ws.sendTextMessage(payload)
        except Exception as e:
            print(f"[ServiceConnection] _send_text error: {e}")

    # sync RPC (thread-safe)

    def _is_main_thread(self) -> bool:
        app = QCoreApplication.instance()
        if app is None:
            return True
        return QThread.currentThread() is app.thread()

    def call_sync(self, op: str, args: dict | None = None,
                  timeout_ms: int = TIMEOUT_DEFAULT):

        if not self._connected:
            raise RuntimeError(
                f"Service tidak terhubung — pastikan ServiceProcessManager.start() "
                f"& _ServiceConnection.connect_to() dipanggil sebelum call_sync()"
            )

        req_id = str(uuid.uuid4())


        try:
            from services.process_manager import get_manager as _get_mgr
            _tok = _get_mgr().get_token() or ""
        except Exception:
            _tok = ""
        payload = json.dumps({"id": req_id, "op": op, "args": args or {}, "token": _tok})


        if self._is_main_thread():


            try:
                loop = QEventLoop()
                with self._lock:
                    self._pending_loops[req_id] = loop

                self.ws.sendTextMessage(payload)

                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                timer.start(timeout_ms)
                loop.exec()
                timer.stop()
            finally:
                pass

            with self._lock:
                reply = self._results.pop(req_id, None)
                self._pending_loops.pop(req_id, None)

        else:
            # ── WORKER THREAD PATH: threading.Event, marshal send ──
            ev = threading.Event()
            with self._lock:
                self._pending_events[req_id] = ev

            # Marshal sendTextMessage ke main thread (QueuedConnection)
            QMetaObject.invokeMethod(
                self,
                "_send_text",
                Qt.QueuedConnection,
                Q_ARG(str, payload),
            )

            # Block worker thread sampai response atau timeout
            got = ev.wait(timeout=timeout_ms / 1000.0)

            with self._lock:
                reply = self._results.pop(req_id, None)
                self._pending_events.pop(req_id, None)

            if not got and reply is None:
                raise TimeoutError(f"Service tidak balas dalam {timeout_ms}ms (op={op})")

        # parse reply (sama untuk dua path)
        if reply is None:
            raise TimeoutError(f"Service tidak balas dalam {timeout_ms}ms (op={op})")

        if not reply.get("ok"):
            err = reply.get("error", "unknown")
            msg = reply.get("msg", "")
            tb = reply.get("traceback", "")
            if tb:
                print(f"[ServiceConnection] remote traceback for op={op}:\n{tb}")
            raise RuntimeError(f"Service error ({err}): {msg}")

        return reply.get("result")

    def ping(self) -> dict:
        return self.call_sync(OP_PING, timeout_ms=2000)


# PUBLIC: bootstrap helper

def ensure_connected(port: int) -> _ServiceConnection:

    conn = _ServiceConnection.get()
    if not conn.is_connected():
        if not conn.connect_to(port):
            raise RuntimeError(f"Gagal connect ke service di port {port}")
    return conn


# CONFIG ENGINE CLIENT

class ConfigEngineClient:


    def __init__(self, base):
        self.base = base
        self.cfgdir = f"{base.rstrip(chr(47)+chr(92))}/config"

        self.tree_ref = None
        self.ctx = {}
        self.fs = {}
        self.root_map_ctx = {}
        self.root_map_fs = {}

        self._conn = _ServiceConnection.get()
        if not self._conn.is_connected():
            raise RuntimeError(
                "ConfigEngineClient: service belum terhubung. "
                "Panggil services.process_manager.get_manager().start() & "
                "main_ext.config_engine_client.ensure_connected(port) di startup."
            )

        # init di service + ambil state awal
        state = self._conn.call_sync(OP_CONFIG_INIT, {"base": base})
        self._apply_state(state)

    # state mirror

    def _apply_state(self, state: dict | None):
        if not state:
            return
        self.ctx = state.get("ctx", self.ctx) or {}
        self.fs = state.get("fs", self.fs) or {}
        self.root_map_ctx = state.get("root_map_ctx", self.root_map_ctx) or {}
        self.root_map_fs = state.get("root_map_fs", self.root_map_fs) or {}

    def _notify_tree(self):

        if self.tree_ref is None:
            return
        try:
            if hasattr(self.tree_ref, "reload_config_cache"):
                self.tree_ref.reload_config_cache()
            else:
                self.tree_ref.ctx = self.ctx
                self.tree_ref.fs = self.fs
        except Exception:
            try:
                self.tree_ref.ctx = self.ctx
                self.tree_ref.fs = self.fs
            except Exception:
                pass

    # METHOD MIRRORS (drop-in API)

    def apply(self, rel):
        state = self._conn.call_sync(OP_CONFIG_APPLY, {"base": self.base, "rel": rel})
        self._apply_state(state)
        self._notify_tree()

    def move_paths(self, rel_old, rel_new):
        state = self._conn.call_sync(OP_CONFIG_MOVE_PATHS, {
            "base": self.base, "rel_old": rel_old, "rel_new": rel_new,
        })
        self._apply_state(state)
        self._notify_tree()

    def move_paths_cross(self, src_engine, rel_old, rel_new):

        base_src = getattr(src_engine, "base", None)
        if not base_src:
            raise ValueError("move_paths_cross: src_engine harus punya .base")

        result = self._conn.call_sync(OP_CONFIG_MOVE_PATHS_CROSS, {
            "base_dst": self.base,
            "base_src": base_src,
            "rel_old": rel_old,
            "rel_new": rel_new,
        })

        # update kedua engine
        if result:
            self._apply_state(result.get("dst"))
            if isinstance(src_engine, ConfigEngineClient):
                src_engine._apply_state(result.get("src"))
                src_engine._notify_tree()

        self._notify_tree()

    def delete_paths(self, rel):
        state = self._conn.call_sync(OP_CONFIG_DELETE_PATHS, {
            "base": self.base, "rel": rel,
        })
        self._apply_state(state)
        self._notify_tree()

    def apply_context_only(self, rel):
        state = self._conn.call_sync(OP_CONFIG_APPLY_CTX_ONLY, {
            "base": self.base, "rel": rel,
        })
        self._apply_state(state)

    def apply_permission_only(self, rel):
        state = self._conn.call_sync(OP_CONFIG_APPLY_PERM_ONLY, {
            "base": self.base, "rel": rel,
        })
        self._apply_state(state)

    def apply_perm_on_repack(self, fs_root_folder):
        state = self._conn.call_sync(OP_CONFIG_APPLY_REPACK, {
            "base": self.base, "fs_root_folder": fs_root_folder,
        }, timeout_ms=TIMEOUT_HEAVY)
        self._apply_state(state)

    def apply_predicted_context(self, fs_root_folder, mode="ai", tree_id=0, dry_run=False):
        state = self._conn.call_sync(OP_CONFIG_PREDICTED_CTX, {
            "base": self.base,
            "fs_root_folder": fs_root_folder,
            "mode": mode,
            "tree_id": tree_id,
            "dry_run": dry_run,
        }, timeout_ms=TIMEOUT_HEAVY)
        self._apply_state(state)

    def validate_fs_config_for_mkfs(self, fs_config_path):
        # raises RuntimeError di service kalau invalid — diteruskan ke caller
        self._conn.call_sync(OP_CONFIG_VALIDATE_FS, {
            "base": self.base, "fs_config_path": fs_config_path,
        })

    def handle_fs_transfer(self, old_rel, new_rel, old_base=None, new_base=None,
                           src_ctx=None, src_fs=None):
        state = self._conn.call_sync(OP_CONFIG_HANDLE_TRANSFER, {
            "base": self.base,
            "old_rel": old_rel,
            "new_rel": new_rel,
            "old_base": old_base,
            "new_base": new_base,
            "src_ctx": src_ctx,
            "src_fs": src_fs,
        }, timeout_ms=TIMEOUT_HEAVY)
        self._apply_state(state)
        self._notify_tree()

    def rebuild_root_map(self):
        state = self._conn.call_sync(OP_CONFIG_REBUILD_ROOT_MAP, {"base": self.base})
        self._apply_state(state)

    # helpers (utility — bukan modifikasi state)

    def refresh_state(self):

        state = self._conn.call_sync(OP_CONFIG_GET_STATE, {"base": self.base})
        self._apply_state(state)

    def load_full_state(self):

        full = self._conn.call_sync(
            OP_CONFIG_LOAD_FULL_STATE,
            {"base": self.base},
            timeout_ms=TIMEOUT_HEAVY,  # neocat 4500+ entries bisa lama dikit
        )
        # Sync state mirror di client juga
        if full:
            self.ctx = full.get("ctx", {}) or {}
            self.fs = full.get("fs", {}) or {}
            self.root_map_ctx = full.get("root_map_ctx", {}) or {}
            self.root_map_fs = full.get("root_map_fs", {}) or {}
        return full


    def escaped_ctx_key(self, rel):
        parts = rel.replace("\\", "/").split("/")
        parts = [re.escape(p) for p in parts]
        return "/" + "/".join(parts)

    def _get_root(self, rel):
        return rel.split("/")[0]

    def infer_permission(self, rel):
        import os as _os
        full = _os.path.join(self.base, rel).replace("\\", "/")
        rel = rel.replace("\\", "/")

        if _os.path.isdir(full):
            uid = "0"
            gid = "2000" if ("system/bin" in rel or "system/xbin" in rel or "vendor/bin" in rel) else "0"
            return uid, gid, "0755"

        link = None
        if _os.path.isfile(full):
            try:
                import core.fspatch as fp
                link = fp.islink(full)
            except Exception:
                link = None

        if link:
            uid = "0"
            gid = "2000" if ("/system/bin" in rel or "/system/xbin" in rel or "/vendor/bin" in rel) else "0"
            if "/bin" in rel or "/xbin" in rel:
                mode = "0755"
            elif rel.endswith(".sh"):
                mode = "0750"
            else:
                mode = "0644"
            return uid, gid, mode

        if "/bin/" in rel or "/xbin/" in rel:
            uid = "0"
            gid = "2000" if ("system/bin" in rel or "vendor/bin" in rel or "system/xbin" in rel) else "0"
            mode = "0755"
            if rel.endswith(".sh"):
                mode = "0750"
            return uid, gid, mode

        return "0", "0", "0644"


def prescan_task(base, recursive=False):

    conn = _ServiceConnection.get()
    result = conn.call_sync(OP_PRESCAN_FULL, {"base": base, "recursive": recursive}, timeout_ms=TIMEOUT_HEAVY)
    return result["ok"], result["payload"]


def prescan_task_fast(base, recursive=False):

    conn = _ServiceConnection.get()
    result = conn.call_sync(OP_PRESCAN_FAST, {"base": base, "recursive": recursive}, timeout_ms=TIMEOUT_HEAVY)
    return result["ok"], result["payload"]


def load_file_context(basepath):
    conn = _ServiceConnection.get()
    return conn.call_sync(OP_LOAD_FILE_CONTEXT, {"base": basepath})


def load_fs_config(basepath):
    conn = _ServiceConnection.get()
    return conn.call_sync(OP_LOAD_FS_CONFIG, {"base": basepath})


def payload_extract_partition(payload_path, partition, out_dir, max_workers=None):
    if max_workers is None or max_workers <= 0:
        from core.storage_detector import get_optimal_io_workers
        max_workers = get_optimal_io_workers(out_dir, max_cap=64, hdd_cap=4)
    conn = _ServiceConnection.get()
    return conn.call_sync(
        OP_PAYLOAD_EXTRACT_PARTITION,
        {
            "payload_path": payload_path,
            "partition": partition,
            "out_dir": out_dir,
            "max_workers": max_workers,
        },
        timeout_ms=TIMEOUT_PAYLOAD,
    )


# Re-export DEFAULT_CTX / DEFAULT_PERM dari pure module untuk compat
from core.config_engine_pure import DEFAULT_CTX, DEFAULT_PERM, perm_to_rwx  # noqa: E402,F401


__all__ = [
    "ConfigEngineClient",
    "ensure_connected",
    "prescan_task",
    "prescan_task_fast",
    "load_file_context",
    "load_fs_config",
    "payload_extract_partition",
    "perm_to_rwx",
    "DEFAULT_CTX",
    "DEFAULT_PERM",
]
