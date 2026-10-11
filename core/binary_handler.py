# SPDX-License-Identifier: MPL-2.0
import os, json
from PySide6.QtCore import QObject, QFileSystemWatcher, Signal, QCoreApplication, QThread


def _find_recursive(root, filename):

    if not os.path.isdir(root):
        return None
    target = filename.lower()
    for base, _, files in os.walk(root):
        for f in files:
            if f.lower() == target:
                return os.path.join(base, f)
    return None


class BinaryIndexWatcher(QObject):
    updated = Signal()

    def __init__(self, bin_root, parent=None):
        super().__init__(parent)
        self.bin_root = bin_root
        self.package_json = os.path.join(bin_root, "package.json")
        self.index = {}

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_change)

        self._attach()
        self._reload()

    def _attach(self):
        self.watcher.removePaths(self.watcher.files())
        if os.path.isfile(self.package_json):
            self.watcher.addPath(self.package_json)

    def _on_change(self):
        self._reload()
        self.updated.emit()
        self._attach()  # Windows suka copot watcher

    def _reload(self):
        if not os.path.isfile(self.package_json):
            self.index = {}
            return
        try:
            with open(self.package_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        idx = {}
        for env, env_data in data.items():
            env_path = os.path.join(self.bin_root, env)
            for b in env_data.get("binaries", []):
                name = b.get("name")
                if not name:
                    continue
                flat = os.path.join(env_path, name)
                if not os.path.isfile(flat):
                    found = _find_recursive(env_path, name)
                    if found:
                        idx[name] = found
                        continue
                idx[name] = flat
        self.index = idx

    def resolve(self, name):
        # 1️⃣ coba dari index (fast path)
        path = self.index.get(name)
        if path and os.path.isfile(path):
            return path

        # 2️⃣ fallback: baca JSON LANGSUNG (source of truth)
        if not os.path.isfile(self.package_json):
            return None

        try:
            with open(self.package_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        for env, env_data in data.items():
            env_path = os.path.join(self.bin_root, env)
            for b in env_data.get("binaries", []):
                if b.get("name") == name:
                    real = os.path.join(env_path, name)
                    if os.path.isfile(real):
                        # 🔥 sinkronkan index biar call berikutnya cepet
                        self.index[name] = real
                        return real
                    found = _find_recursive(env_path, name)
                    if found:
                        self.index[name] = found
                        return found

        return None


_BIN_WATCHERS = {}

def _resolve_from_package(bin_root, binary_name):
    package_json = os.path.join(bin_root, "package.json")
    if not os.path.isfile(package_json):
        return None

    try:
        with open(package_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    for env, env_data in data.items():
        env_path = os.path.join(bin_root, env)
        for b in env_data.get("binaries", []):
            if b.get("name") == binary_name:
                real = os.path.join(env_path, binary_name)
                if os.path.isfile(real):
                    return real
                found = _find_recursive(env_path, binary_name)
                if found:
                    return found

    return None

def _get_watcher(bin_root):
    watcher = _BIN_WATCHERS.get(bin_root)
    if watcher:
        return watcher

    app = QCoreApplication.instance()
    if app is None:
        # ⛔ Qt belum hidup → JANGAN bikin watcher
        return None

    if QThread.currentThread() != app.thread():
        return None

    watcher = BinaryIndexWatcher(bin_root, parent=app)
    _BIN_WATCHERS[bin_root] = watcher
    return watcher


def resolve_binary(bin_root, binary_name):
    watcher = _get_watcher(bin_root)
    if not watcher:
        return _resolve_from_package(bin_root, binary_name)
    return watcher.resolve(binary_name) or _resolve_from_package(bin_root, binary_name)


from core.app_paths import BIN_DIR as _DEFAULT_BIN_ROOT


def is_available(*binary_names, bin_root=None):

    root = bin_root or _DEFAULT_BIN_ROOT
    for name in binary_names:
        if not resolve_binary(root, name):
            return False
    return True
