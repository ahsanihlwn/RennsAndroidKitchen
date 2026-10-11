# SPDX-License-Identifier: MPL-2.0
# core/loading.py
from PySide6.QtCore import QTimer, QObject
from PySide6.QtGui import QIcon
from icons.handler import icon

print("[CORE STAGE] LOADER")

class LoadingAnimator(QObject):
    instances = {}   # per-path animator

    def __init__(self, item, tree, icon_loading, icon_finish):
        super().__init__()
        self.item = item
        self.tree = tree
        if isinstance(icon_loading, str):
            icon_loading = icon(icon_loading)
        if isinstance(icon_finish, str):
            icon_finish = icon(icon_finish)
        self.icon_loading = QIcon(icon_loading)
        self.icon_finish = QIcon(icon_finish)
        self.original_icon = item.icon(0) if item else QIcon()

        self.opacity = 0.0
        self.direction = 1
        self.timer = QTimer()
        self.timer.setInterval(60)  # 60ms per frame
        self.timer.timeout.connect(self.animate)

    def animate(self):
        if not self.item:
            return
        self.opacity += 0.08 * self.direction

        if self.opacity >= 1.0:
            self.opacity = 1.0
            self.direction = -1
        elif self.opacity <= 0.0:
            self.opacity = 0.0
            self.direction = 1

        try:
            self.item.setIcon(0, self.icon_loading)
        except Exception:
            pass

    def start(self):
        self.timer.start()

    def stop_with_finish(self):
        self.running = False
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()

        # ganti icon ke finish
        if self.item:
            try:
                self.item.setIcon(0, self.icon_finish)
            except Exception:
                pass

        def _restore():
            if not self.item:
                return
            try:
                # Kembalikan icon yang semestinya (jangan sampai QIcon() kosong)
                restored = self.original_icon
                if (not restored or restored.isNull()) and self.tree:
                    target_path = self.item.data(0, 0)
                    fs_type = self.item.data(0, 1) or ""
                    if target_path:
                        restored = self.tree._resolve_folder_icon(target_path, fs_type)
                if restored and not restored.isNull():
                    self.item.setIcon(0, restored)
            except Exception:
                pass

        # tunggu 5 detik lalu kembalikan icon asli
        QTimer.singleShot(5000, _restore)

        # PATCH: otomatis sembunyikan loading bar global
        if self.tree and hasattr(self.tree.window(), "hide_erofs_loading"):
            self.tree.window().hide_erofs_loading()

