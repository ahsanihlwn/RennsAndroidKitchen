# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QLineEdit, QMessageBox,
)
from PySide6.QtCore import Qt

from main_ext.class_ThemeUi import theme as _th
from main_ext.class_AppDialog import AppDialog as _AppDialog
from core.ui_lang import t
from core.threader import run_in_thread

print("[MAIN STAGE] MAKE_FILESYSTEM_DIALOG")


def _card_qss():
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#mf_card {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
        QWidget#mf_card QWidget {{
            background: transparent;
            color: {_th.FG};
            font-family: 'Poppins','Inter','Segoe UI',sans-serif;
            font-size: 12px;
        }}
        QWidget#mf_card QLabel {{ background: transparent; border: none; color: {_th.FG}; }}

        QLineEdit {{
            background: {_th.border_color(0.05)}; border: 1px solid {_th.border_color(0.10)};
            border-radius: 6px; color: {_th.FG}; font-size: 12px; padding: 3px 8px; min-height: 22px;
        }}
        QLineEdit:focus {{ border-color: {_th.accent(0.45)}; }}
        QLineEdit:disabled {{ color: {_th.FG_DISABLED2}; }}
    """


def _ss_primary():
    return f"""
        QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.38)};
            border-radius:6px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600; padding:0 14px; height:30px; }}
        QPushButton:hover {{ background:{_th.accent(0.32)}; border-color:{_th.accent(0.60)}; color:{_th.FG_BLUE_LIGHT}; }}
        QPushButton:pressed {{ background:{_th.accent(0.48)}; }}
    """


def _ss_neutral():
    return f"""
        QPushButton {{ background:{_th.border_color(0.06)}; border:1px solid {_th.border_color(0.10)};
            border-radius:6px; color:{getattr(_th,'FG3',_th.FG_MUTED)}; font-size:12px; font-weight:400; padding:0 14px; height:30px; }}
        QPushButton:hover {{ background:{_th.border_color(0.10)}; border-color:{_th.border_color(0.18)}; color:{getattr(_th,'FG_HOVER',_th.FG)}; }}
        QPushButton:pressed {{ background:{_th.border_color(0.14)}; }}
        QPushButton:disabled {{ background:{_th.border_color(0.03)}; border-color:{_th.border_color(0.08)}; color:{_th.FG_DESC}; }}
    """


def _ss_toggle(active):
    if active:
        return f"""
            QPushButton {{ background:{_th.accent(0.28)}; border:1px solid {_th.accent(0.55)};
                border-radius:6px; color:{_th.FG_BLUE_LIGHT}; font-size:12px; font-weight:600; padding:0 16px; height:32px; }}
            QPushButton:hover {{ background:{_th.accent(0.36)}; }}
        """
    return f"""
        QPushButton {{ background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.12)};
            border-radius:6px; color:{_th.FG_MUTED}; font-size:12px; font-weight:400; padding:0 16px; height:32px; }}
        QPushButton:hover {{ background:{_th.border_color(0.09)}; border-color:{_th.border_color(0.20)}; }}
    """


def _section_style():
    return f"color:{_th.FG_BLUE_MUTED}; font-size:10px; font-weight:600; letter-spacing:1px; background:transparent; border:none;"


def _hint_style():
    return f"color:{_th.FG_DESC}; font-size:10px; background:transparent; border:none;"


class MakeFilesystemDialog(QDialog):
    def __init__(self, parent, folder_path, main_win):
        super().__init__(parent)
        self._tree = parent
        self._main = main_win
        self._folder_path = os.path.normpath(folder_path)
        self._part_name = os.path.basename(self._folder_path.rstrip("/\\"))
        self._fs_type = "EROFS"

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        card = QWidget()
        card.setObjectName("mf_card")
        card.setStyleSheet(_card_qss())
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel(t("mf_title"))
        title.setStyleSheet(f"color:{_th.FG}; font-size:14px; font-weight:600; background:transparent; border:none;")
        root.addWidget(title)

        hint = QLabel(t("mf_hint", part=self._part_name))
        hint.setWordWrap(True)
        hint.setStyleSheet(_hint_style())
        root.addWidget(hint)

        # ── FS TYPE TOGGLE ──
        sec_fs = QLabel(t("mf_fs_type"))
        sec_fs.setStyleSheet(_section_style())
        root.addWidget(sec_fs)

        fs_row = QHBoxLayout()
        fs_row.setSpacing(8)
        self.btn_erofs = QPushButton("EROFS")
        self.btn_erofs.setCursor(Qt.PointingHandCursor)
        self.btn_ext4 = QPushButton("EXT4")
        self.btn_ext4.setCursor(Qt.PointingHandCursor)
        self.btn_erofs.clicked.connect(lambda: self._set_fs_type("EROFS"))
        self.btn_ext4.clicked.connect(lambda: self._set_fs_type("EXT4"))
        fs_row.addWidget(self.btn_erofs)
        fs_row.addWidget(self.btn_ext4)
        root.addLayout(fs_row)
        self._sync_fs_buttons()

        # ── PARTITION NAME (read-only, = nama folder) ──
        sec_part = QLabel(t("mf_partition_name"))
        sec_part.setStyleSheet(_section_style())
        root.addWidget(sec_part)
        self.part_edit = QLineEdit(self._part_name)
        self.part_edit.setEnabled(False)
        root.addWidget(self.part_edit)

        # ── MOUNT POINT ──
        sec_mp = QLabel(t("mf_mount_point"))
        sec_mp.setStyleSheet(_section_style())
        root.addWidget(sec_mp)
        self.mp_edit = QLineEdit(f"/{self._part_name}")
        root.addWidget(self.mp_edit)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{_th.border_color(0.07)};border:none;")
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton(t("mf_cancel"))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(_ss_neutral())
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        create = QPushButton(t("mf_create"))
        create.setCursor(Qt.PointingHandCursor)
        create.setStyleSheet(_ss_primary())
        create.clicked.connect(self._do_create)
        btn_row.addWidget(create)
        root.addLayout(btn_row)

        self.adjustSize()

    def _set_fs_type(self, fs_type):
        self._fs_type = fs_type
        self._sync_fs_buttons()

    def _sync_fs_buttons(self):
        self.btn_erofs.setStyleSheet(_ss_toggle(self._fs_type == "EROFS"))
        self.btn_ext4.setStyleSheet(_ss_toggle(self._fs_type == "EXT4"))

    def _do_create(self):
        mount_point = self.mp_edit.text().strip() or f"/{self._part_name}"
        fs_type = self._fs_type
        folder_path = self._folder_path
        main = self._main
        task_id = f"make_filesystem:{folder_path}"

        if main:
            main.push_task(task_id)

        def worker():
            from core.make_filesystem import make_filesystem
            return make_filesystem(folder_path, fs_type, mount_point)

        def after(ok, msg):
            if main:
                main.pop_task(task_id, ok)
            if not ok:
                if main:
                    main.set_loading_failed(t("mf_failed"))
                _AppDialog.error(self, t("mf_failed"), str(msg))
                return
            if main:
                main.maybe_finish_loading()
                tree = getattr(main, "active_tree", None) or self._tree
                if tree is not None:
                    try:
                        tree.reload_config_cache()
                        parent_dir = os.path.dirname(folder_path)
                        parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                        if parent_item:
                            tree.refresh_folder(parent_item)
                    except Exception as e:
                        print("[MakeFilesystemDialog] refresh warn:", e)
            QMessageBox.information(self, t("mf_done"), f"{t('mf_done')}\n{msg}")
            self.accept()

        run_in_thread(worker, None, callback=after)

    @staticmethod
    def launch(tree_widget, full_path):
        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        main_win = tree_widget.window()
        dlg = MakeFilesystemDialog(main_win, full_path, main_win)
        dlg._tree = tree_widget
        _ov = _BlurOverlay(main_win) if main_win else None
        if _ov:
            dlg._overlay = _ov
        try:
            install_window_fade(dlg)
        except Exception:
            pass
        dlg.exec()
        if _ov:
            _ov.close_overlay()
