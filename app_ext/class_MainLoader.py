# SPDX-License-Identifier: MPL-2.0
# class_MainLoader.py
from PySide6.QtCore import QThread, Signal

class MainLoader(QThread):
    loaded = Signal()

    def __init__(self, project, p1, p2):
        super().__init__()
        self.project = project
        self.p1 = p1
        self.p2 = p2

    def run(self):
        # 🔥 CUMA KERJA BERAT / DUMMY
        import time
        time.sleep(0.3)   # simulasi persiapan
        self.loaded.emit()
