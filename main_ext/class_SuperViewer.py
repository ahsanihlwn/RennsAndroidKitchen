# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel,
    QListWidget, QListWidgetItem,
    QPushButton
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QTextEdit, QApplication
from icons.handler import icon
import json
import subprocess
import re
import os
from PySide6.QtCore import QTimer
from core.threader import run_in_thread
from core.proc_util import CREATE_NO_WINDOW
from main_ext.class_ThemeUi import theme as _th
print("[MAIN STAGE] SUPERVIEW")
SUPER_NAME_RE = re.compile(r"^\s*Name:\s+([a-zA-Z0-9_]+)")
SUPER_GROUP_RE = re.compile(r"^\s*Group:\s+([a-zA-Z0-9_]+)")

BIN_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "RennsAndroidKitchen",
    "bin"
)

from core.binary_handler import resolve_binary
from main_ext.class_DeferredCloseViewer import DeferredCloseViewer

class SuperViewer(DeferredCloseViewer, QWidget):
    def __init__(self, img_path, loading_handler=None):
        super().__init__()
        self.unsaved = False
        self.img = img_path

        layout = QVBoxLayout(self)
        from PySide6.QtWidgets import QHBoxLayout

        self.list_a = QListWidget()  # SLOT A (KIRI)
        self.list_b = QListWidget()  # SLOT B (KANAN)
        self.list_a.itemSelectionChanged.connect(self._on_select_a)
        self.list_b.itemSelectionChanged.connect(self._on_select_b)
        for lst in (self.list_a, self.list_b):
            lst.setSelectionMode(QListWidget.ExtendedSelection)
            lst.setFocusPolicy(Qt.NoFocus)
            lst.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                color: {_th.FG_LABEL};
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 6px;
            }}
            QListWidget::item:hover {{
                background: {_th.border_color(0.06)};
            }}
            QListWidget::item:selected {{
                background: {_th.accent(0.35)};
            }}
            QScrollBar:vertical {{
                width: 4px;
                background: transparent;
            }}

            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.28)};
                border-radius: 2px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {_th.border_color(0.45)};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """)

        lists = QHBoxLayout()
        lists.addWidget(self.list_a)  # ✅ KIRI = A
        lists.addWidget(self.list_b)  # ✅ KANAN = B
        layout.addLayout(lists)


        btn_sel = QPushButton("Extract Selected")
        btn_all = QPushButton("Extract All")
        for btn in (btn_sel, btn_all):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
            QPushButton {{
                background: {_th.accent(0.18)};
                border: 1px solid {_th.accent(0.40)};
                color: {_th.FG_BRIGHT};
                padding: 8px 14px;
                border-radius: 6px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {_th.accent(0.35)};
                border-color: {_th.accent(0.80)};
            }}
            QPushButton:pressed {{
                background: {_th.accent(0.55)};
            }}
            QPushButton:disabled {{
                background: {_th.border_color(0.05)};
                border-color: {_th.border_color(0.10)};
                color: {_th.border_color(0.40)};
            }}
            """)

        btns = QHBoxLayout()
        btns.addWidget(btn_sel)
        btns.addWidget(btn_all)
        layout.addLayout(btns)

        btn_sel.clicked.connect(self.extract_selected)
        btn_all.clicked.connect(self.extract_all)

        def worker():
            info = self.read_super_info()
            return True, json.dumps(info)

        def after(ok, payload):
            if not ok or not payload:
                return

            info = json.loads(payload)

            self.list_a.clear()
            self.list_b.clear()

            icon_drive = QIcon(icon("drive"))

            title = "Dynamic Partitions"
            if info["active_slot"]:
                title += f" | Active slot: {info['active_slot']}"
            self.setWindowTitle(title)

            for p in info["partitions"]:
                # 🔥 skip group / sampah
                if p.startswith("main_") or p.startswith("default"):
                    continue

                item = QListWidgetItem(icon_drive, p)
                item.setSizeHint(QSize(0, 26))

                if p.endswith("_a"):
                    self.list_a.addItem(item)
                elif p.endswith("_b"):
                    self.list_b.addItem(item)

        run_in_thread(worker, None, callback=after)

    def _on_select_a(self):
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self.list_b.blockSignals(True)
            self.list_b.clearSelection()
            self.list_b.blockSignals(False)

    def _on_select_b(self):
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self.list_a.blockSignals(True)
            self.list_a.clearSelection()
            self.list_a.blockSignals(False)

    def extract_partition(self, part_name, out_dir):
        LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
        if not LPUNPACK:
            return False, "LPUNPACK NOT FOUND"
        cmd = [
            LPUNPACK,
            "-p",
            part_name,
            self.img,
            out_dir
        ]
        print(f"[SUPER_DBG][WORKER] START part={part_name!r} out={out_dir!r}", flush=True)
        print(f"[SUPER_DBG][WORKER] CMD part={part_name!r} cmd={cmd!r}", flush=True)

        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )

        for line in p.stdout:
            print("[SUPER][RAW]", line.rstrip(), flush=True)
        try:
            p.stdout.close()
        except Exception:
            pass
        rc = p.wait()
        out_path = os.path.join(out_dir, part_name + ".img")
        print(
            f"[SUPER_DBG][WORKER] EXIT part={part_name!r} rc={rc} exists={os.path.exists(out_path)}",
            flush=True,
        )
        if rc != 0:
            return False, f"lpunpack failed ({rc})"
        return True, part_name

    def extract_all_partitions(self, out_dir):
        LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
        if not LPUNPACK:
            return False, "LPUNPACK NOT FOUND"
        cmd = [
            LPUNPACK,
            self.img,
            out_dir
        ]
        print(f"[SUPER_DBG][WORKER] ALL_START out={out_dir!r}", flush=True)
        print(f"[SUPER_DBG][WORKER] ALL_CMD cmd={cmd!r}", flush=True)

        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )

        for line in p.stdout:
            print("[SUPER][RAW]", line.rstrip(), flush=True)
        try:
            p.stdout.close()
        except Exception:
            pass
        rc = p.wait()
        print(f"[SUPER_DBG][WORKER] ALL_EXIT rc={rc}", flush=True)
        if rc != 0:
            return False, f"lpunpack failed ({rc})"
        return True, None

    def _refresh_out_dir(self, out_dir, part_name=None):
        main = self.window()

        def _find_item_norm(tree, target_path):
            target = os.path.normcase(os.path.abspath(os.path.normpath(target_path)))

            def walk(item):
                if not item:
                    return None
                path = item.data(0, Qt.UserRole)
                if path:
                    current = os.path.normcase(os.path.abspath(os.path.normpath(path)))
                    if current == target:
                        return item
                for idx in range(item.childCount()):
                    found = walk(item.child(idx))
                    if found:
                        return found
                return None

            return walk(tree.topLevelItem(0))

        candidates = []
        for attr in ("active_tree", "tree", "tree2", "tree_mirror"):
            tree = getattr(main, attr, None) if main else None
            if tree and tree not in candidates:
                candidates.append(tree)

        for tree in candidates:
            base = getattr(tree, "base", None)
            if not base:
                continue
            try:
                base_abs = os.path.normcase(os.path.abspath(os.path.normpath(base)))
                out_abs = os.path.normcase(os.path.abspath(os.path.normpath(out_dir)))
                if os.path.commonpath([base_abs, out_abs]) != base_abs:
                    continue
            except Exception:
                continue

            parent_item = _find_item_norm(tree, out_dir)
            if parent_item:
                tree.refresh_folder(parent_item)
                print(f"[SUPER_DBG][UI] REFRESH part={part_name!r} out={out_dir!r}", flush=True)
                return True

            root_item = tree.topLevelItem(0)
            if root_item:
                tree.refresh_folder(root_item)
                print(f"[SUPER_DBG][UI] REFRESH_ROOT part={part_name!r} out={out_dir!r}", flush=True)
                return True

        print(f"[SUPER_DBG][UI] REFRESH_MISS part={part_name!r} out={out_dir!r}", flush=True)
        return False

    def extract_selected(self):
        items = (
            self.list_a.selectedItems() +
            self.list_b.selectedItems()
        )
        if not items:
            return

        parts = [i.text() for i in items]
        out = os.path.dirname(self.img)
        print(
            f"[SUPER_DBG][UI] extract_selected count={len(parts)} parts={parts} "
            f"img={self.img!r} out={out!r}",
            flush=True,
        )

        from core.task_queue import TaskItem
        main = self.window()


        batch_id = None
        if main:
            main._ensure_loading_manager()
            batch_id = main.loading_manager.start_batch(
                "extracting", len(parts), label="Extracting super"
            )

        for idx, part_name in enumerate(parts, start=1):
            task_id = f"extracting:super:{part_name}"
            print(
                f"[SUPER_DBG][UI] enqueue idx={idx}/{len(parts)} part={part_name!r} "
                f"task_id={task_id!r} has_main={bool(main)}",
                flush=True,
            )

            def _make_worker(p, n, total):
                def worker():
                    print(f"[SUPER_DBG][TASK] START idx={n}/{total} part={p!r}", flush=True)
                    ok, msg = self.extract_partition(p, out)
                    print(
                        f"[SUPER_DBG][TASK] DONE idx={n}/{total} part={p!r} ok={ok} msg={msg!r}",
                        flush=True,
                    )
                    return ok, msg
                return worker

            def _make_after(p, tid, n, total, bid):
                def after(ok, msg):
                    print(
                        f"[SUPER_DBG][UI] CALLBACK idx={n}/{total} part={p!r} ok={ok} msg={msg!r}",
                        flush=True,
                    )
                    _main = self.window()
                    if not _main:
                        return


                    try:
                        if not ok:
                            return
                        self._refresh_out_dir(out, p)
                    finally:
                        if bid is not None:
                            _main.loading_manager.report_done(bid, bool(ok), member_name=p)
                        else:
                            _main.pop_task(tid, ok)
                return after

            self.mark_task_started()
            task = TaskItem(
                task_id=task_id,
                worker=_make_worker(part_name, idx, len(parts)),
                callback=self.guard_task_callback(
                    _make_after(part_name, task_id, idx, len(parts), batch_id)
                ),
            )

            if main:
                main.task_queue.enqueue(task)
            else:
                run_in_thread(
                    _make_worker(part_name, idx, len(parts)),
                    None,
                    callback=self.guard_task_callback(
                        _make_after(part_name, task_id, idx, len(parts), batch_id)
                    ),
                )

    def extract_all(self):
        out = os.path.dirname(self.img)
        task_id = f"extracting:super:all:{os.path.basename(self.img)}"

        from core.task_queue import TaskItem
        main = self.window()

        def worker():
            return self.extract_all_partitions(out)

        def after(ok, msg):
            _main = self.window()
            if not _main:
                return
            _main.pop_task(task_id, ok)
            if not ok:
                _main.set_loading_failed("Extract All failed")
                return
            self._refresh_out_dir(out, "all")

        self.mark_task_started()
        task = TaskItem(
            task_id=task_id,
            worker=worker,
            callback=self.guard_task_callback(after),
        )

        if main:
            main.push_task(task_id)
            main.task_queue.enqueue(task)
        else:
            run_in_thread(worker, None, callback=self.guard_task_callback(after))

    def read_super_info(self):
        info = {
            "raw": [],
            "partitions": [],
            "active_slot": None
        }
        LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
        if not LPUNPACK:
            print("LPUNPACK ERROR")
            return
        cmd = [
            LPUNPACK,
            self.img,
            "--info"
        ]

        print("[SUPER][DEBUG] LPUNPACK =", LPUNPACK)
        print("[SUPER][DEBUG] IMG =", self.img)
        print("[SUPER][DEBUG] CMD =", cmd)

        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )

        current_name = None

        for line in p.stdout:
            print("[SUPER][RAW]", line.rstrip())
            line = line.rstrip()
            info["raw"].append(line)

            # ambil nama partition
            m_name = SUPER_NAME_RE.match(line)
            if m_name:
                current_name = m_name.group(1)
                info["partitions"].append(current_name)
                continue

            # deteksi slot aktif dari group
            m_group = SUPER_GROUP_RE.match(line)
            if m_group:
                grp = m_group.group(1)
                if grp.endswith("_a"):
                    info["active_slot"] = "A"
                elif grp.endswith("_b"):
                    info["active_slot"] = "B"

        return info
