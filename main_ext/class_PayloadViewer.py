# SPDX-License-Identifier: MPL-2.0
import sys, os, re, shutil
from core.fsdetect import detect_fs_type
from core.erofs import unpack_erofs, repack_erofs
from main_ext.class_ThemeUi import theme as _th
from core.ui_lang import t
import json
from PySide6.QtCore import QEvent
import time
from icons.handler import icon
from core.threader import run_in_thread
from core.loading import LoadingAnimator
from core.code_editor import CodeHighlighter
from core.fsdetect import detect_fs_root
from PySide6.QtCore import Signal, QMetaObject, Q_ARG
from core.terminal import Terminal
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QTabWidget,
    QPlainTextEdit,
    QMenu,
    QFileDialog,
    QMessageBox,
    QSplitter,
    QSplitterHandle,
    QTabBar,
    QDialog,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QAbstractItemView,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QColor,
    QAction,
    QKeySequence,
    QTextCursor,
    QCursor,
    QFont,
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize,
    QFileSystemWatcher
)
from core.payload_extract import extract_partitions_from_payload

print("[MAIN STAGE] PAYLOADVIEWER")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


from main_ext.class_DeferredCloseViewer import DeferredCloseViewer


class PayloadViewer(DeferredCloseViewer, QWidget):
    def __init__(self, filepath, partitions=None, loading_handler=None):
        super().__init__()
        self.unsaved  = False
        self.filepath = filepath
        self.parts    = partitions or []
        self.loading_handler = loading_handler

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar / search
        toolbar = QWidget()
        toolbar.setFixedHeight(38)
        toolbar.setStyleSheet(f"""
            QWidget {{
                background: {_th.BG};
                border-bottom: 1px solid {_th.border_color(0.06)};
            }}
        """)
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(10, 0, 10, 0)
        tl.setSpacing(6)

        self._sel_lbl = QLabel(t("pv_lbl_n_partitions", n=len(self.parts)))
        self._sel_lbl.setStyleSheet(f"color: {_th.FG3}; font-size: 11px; background: transparent; border: none;")
        tl.addWidget(self._sel_lbl)

        tl.addStretch()

        sel_all_btn = QPushButton(t("pv_btn_select_all"))
        sel_all_btn.setFixedHeight(24)
        sel_all_btn.setStyleSheet(self._small_btn_style())
        sel_all_btn.clicked.connect(self._select_all)
        tl.addWidget(sel_all_btn)

        sel_none_btn = QPushButton(t("pv_btn_deselect"))
        sel_none_btn.setFixedHeight(24)
        sel_none_btn.setStyleSheet(self._small_btn_style())
        sel_none_btn.clicked.connect(self._deselect_all)
        tl.addWidget(sel_none_btn)

        root.addWidget(toolbar)

        self._payload_out_dir = os.path.normpath(os.path.dirname(self.filepath))

        # Partition list
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setStyleSheet(f"""
            QListWidget {{
                background: {_th.BG};
                border: none;
                outline: none;
                color: {_th.FG_LABEL};
                font-size: 13px;
                padding: 4px 0;
            }}
            QListWidget::item {{
                height: 32px;
                padding-left: 8px;
                border-radius: 0;
            }}
            QListWidget::item:hover {{
                background: {_th.border_color(0.045)};
            }}
            QListWidget::item:selected {{
                background: {_th.accent(0.18)};
                color: {_th.FG_BRIGHT};
            }}
            QListWidget::item:selected:hover {{
                background: {_th.accent(0.25)};
            }}
        """)

        self._icon_drive = QIcon(icon("drive"))
        self._populate_list(self.parts)

        _sb_style = f"""
            QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.35)}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.55)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """
        self.list.verticalScrollBar().setStyleSheet(_sb_style)

        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self.list, 1)

        # Bottom action bar
        action_bar = QWidget()
        action_bar.setFixedHeight(52)
        action_bar.setStyleSheet(f"""
            QWidget {{
                background: {_th.BG2};
                border-top: 1px solid {_th.border_color(0.08)};
            }}
        """)
        al = QHBoxLayout(action_bar)
        al.setContentsMargins(12, 0, 12, 0)
        al.setSpacing(8)

        self._extract_hint = QLabel(t("pv_hint_pick"))
        self._extract_hint.setStyleSheet(
            f"color: {_th.FG3}; font-size: 11px; background: transparent; border: none;"
        )
        al.addWidget(self._extract_hint)
        al.addStretch()

        self._btn_extract = QPushButton(t("pv_btn_extract_selected"))
        self._btn_extract.setFixedHeight(32)
        self._btn_extract.setEnabled(False)
        self._btn_extract.setStyleSheet(f"""
            QPushButton {{
                background: {_th.accent(0.18)};
                border: 1px solid {_th.accent(0.38)};
                color: {_th.FG_BLUE};
                padding: 0 18px;
                border-radius: 7px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_th.accent(0.30)};
                border-color: {_th.accent(0.55)};
                color: {_th.FG_BLUE_LIGHT};
            }}
            QPushButton:pressed {{
                background: {_th.accent(0.44)};
            }}
            QPushButton:disabled {{
                background: {_th.border_color(0.09)};
                border: 1px solid {_th.border_color(0.13)};
                color: {_th.FG3};
            }}
        """)
        self._btn_extract.clicked.connect(self.extract_selected)
        al.addWidget(self._btn_extract)

        root.addWidget(action_bar)

    # Helpers
    def _populate_list(self, partitions):
        self.list.clear()
        for p in partitions:
            item = QListWidgetItem(self._icon_drive, p)
            item.setSizeHint(QSize(0, 32))
            self.list.addItem(item)

    def set_partitions(self, partitions):

        self.parts = partitions or []
        self._populate_list(self.parts)
        self._sel_lbl.setText(t("pv_lbl_n_partitions", n=len(self.parts)))

    def _small_btn_style(self):
        return f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                color: {_th.FG2};
                padding: 0 10px;
                border-radius: 5px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.09)};
                color: {_th.FG_HOVER};
            }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
        """

    def _refresh_extracted_partition(self, part_name):
        main = self.loading_handler or self.window()
        out_dir = os.path.abspath(os.path.normpath(self._payload_out_dir))
        img_path = os.path.abspath(os.path.normpath(os.path.join(out_dir, f"{part_name}.img")))

        def _find_item_norm(tree, path):
            wanted = os.path.normcase(os.path.abspath(os.path.normpath(path)))

            def walk(item):
                if item is None:
                    return None
                current = item.data(0, Qt.UserRole)
                if current:
                    current_norm = os.path.normcase(os.path.abspath(os.path.normpath(current)))
                    if current_norm == wanted:
                        return item
                for idx in range(item.childCount()):
                    found = walk(item.child(idx))
                    if found:
                        return found
                return None

            return walk(tree.topLevelItem(0))

        def _apply_badge(tree):
            fs_type = tree._detect_magic_once(img_path)
            if fs_type not in ("EROFS", "EXT4", "EXT3", "EXT2", "SUPER", "SPARSE"):
                return
            img_item = _find_item_norm(tree, img_path)
            if img_item:
                img_item.setData(0, Qt.UserRole + 1, fs_type)
                tree.viewport().update()

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
                out_abs = os.path.normcase(out_dir)
                if os.path.commonpath([base_abs, out_abs]) != base_abs:
                    continue
            except Exception:
                continue

            parent_item = _find_item_norm(tree, out_dir)
            if parent_item:
                tree.refresh_folder(parent_item)
                _apply_badge(tree)
                return True

            root_item = tree.topLevelItem(0)
            if root_item:
                tree.refresh_folder(root_item)
                _apply_badge(tree)
                return True

        print(f"[PAYLOAD] refresh miss: out_dir={out_dir}")
        return False

    def _select_all(self):
        self.list.selectAll()

    def _deselect_all(self):
        self.list.clearSelection()

    def _on_selection_changed(self):
        count = len(self.list.selectedItems())
        total = self.list.count()
        if count == 0:
            self._sel_lbl.setText(t("pv_lbl_n_partitions", n=total))
            self._extract_hint.setText(t("pv_hint_pick"))
            self._btn_extract.setEnabled(False)
        else:
            self._sel_lbl.setText(t("pv_lbl_n_selected", count=count, total=total))
            self._extract_hint.setText(t("pv_hint_n_extract", n=count))
            self._btn_extract.setEnabled(True)

    # Extract
    def extract_selected(self):
        items = self.list.selectedItems()
        if not items:
            return

        parts   = [i.text() for i in items]
        out_dir = os.path.dirname(self.filepath)
        print(
            f"[PAYLOAD_DBG][UI] extract_selected count={len(parts)} parts={parts} "
            f"payload={self.filepath!r} out_dir={out_dir!r}",
            flush=True,
        )

        from core.task_queue import TaskItem
        main = self.window()


        batch_id = None
        if main:
            main._ensure_loading_manager()
            batch_id = main.loading_manager.start_batch(
                "extracting", len(parts), label="Extracting payload"
            )

        for idx, part_name in enumerate(parts, start=1):
            task_id = f"extracting:payload:{part_name}"
            print(
                f"[PAYLOAD_DBG][UI] enqueue idx={idx}/{len(parts)} part={part_name!r} "
                f"task_id={task_id!r} has_main={bool(main)}",
                flush=True,
            )

            def _make_worker(p, n, total):
                def worker():
                    try:
                        print(
                            f"[PAYLOAD_DBG][WORKER] START idx={n}/{total} part={p!r}",
                            flush=True,
                        )
                        service_connected = False
                        service_probe_error = None
                        try:
                            from main_ext.config_engine_client import _ServiceConnection, payload_extract_partition
                            service_connected = _ServiceConnection.get().is_connected()
                        except Exception as probe_error:
                            service_probe_error = probe_error
                        print(
                            f"[PAYLOAD_DBG][WORKER] route idx={n}/{total} part={p!r} "
                            f"service_connected={service_connected} probe_error={service_probe_error!r}",
                            flush=True,
                        )

                        from core.storage_detector import get_optimal_io_workers
                        opt_workers = get_optimal_io_workers(out_dir, max_cap=64, hdd_cap=4)
                        if service_connected:
                            print(
                                f"[PAYLOAD_DBG][WORKER] WS_CALL_START idx={n}/{total} part={p!r} workers={opt_workers}",
                                flush=True,
                            )
                            result = payload_extract_partition(
                                payload_path=self.filepath,
                                partition=p,
                                out_dir=out_dir,
                                max_workers=opt_workers,
                            )
                            print(
                                f"[PAYLOAD_DBG][WORKER] WS_CALL_DONE idx={n}/{total} part={p!r} result={result!r}",
                                flush=True,
                            )
                        else:
                            if service_probe_error:
                                print(f"[PAYLOAD] service unavailable for {p}, local fallback: {service_probe_error}")
                            print(
                                f"[PAYLOAD_DBG][WORKER] LOCAL_START idx={n}/{total} part={p!r} workers={opt_workers}",
                                flush=True,
                            )
                            with open(self.filepath, "rb") as f:
                                extract_partitions_from_payload(
                                    reader=f,
                                    partitions_name=[p],
                                    out_dir=out_dir,
                                    max_workers=opt_workers,
                                    on_partition_done=None
                                )
                            print(
                                f"[PAYLOAD_DBG][WORKER] LOCAL_DONE idx={n}/{total} part={p!r}",
                                flush=True,
                            )
                        print(
                            f"[PAYLOAD_DBG][WORKER] DONE idx={n}/{total} part={p!r}",
                            flush=True,
                        )
                        return True, p
                    except Exception as e:
                        print(
                            f"[PAYLOAD_DBG][WORKER] ERROR idx={n}/{total} part={p!r} err={e!r}",
                            flush=True,
                        )
                        return False, str(e)
                return worker

            def _make_after(p, tid, n, total, bid):
                def after(ok, msg):
                    print(
                        f"[PAYLOAD_DBG][UI] CALLBACK idx={n}/{total} part={p!r} "
                        f"ok={ok} msg={msg!r}",
                        flush=True,
                    )
                    _main = self.window()
                    if not _main:
                        return


                    try:
                        if not ok:
                            print(f"[PAYLOAD] failed: {p} → {msg}")
                            return
                        if not self._refresh_extracted_partition(p):
                            print(f"[PAYLOAD] refresh skipped: {p}")
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
