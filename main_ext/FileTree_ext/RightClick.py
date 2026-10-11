# SPDX-License-Identifier: MPL-2.0
import os,shutil
from core.fsdetect import detect_fs_type
import json
from core.fsdetect import detect_fs_root
import subprocess
from core.fs_verify import FSVerifier
import sys
from core.super_logic import extract_all, convert_to_raw
from PySide6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QMenu,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QLabel,
)
from app_ext.class_BlurOverlay import BlurCard
from PySide6.QtCore import QThread, QMetaObject, Qt, QEventLoop


from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt
from core.threader import run_in_thread
from core.binary_handler import is_available
from core import apktool_tool
from main_ext.class_ConfigEngine import ConfigEngine,prescan_task
from core.context_patcher import ContextPatcher
from main_ext.class_ThemeUi import theme as _th
from main_ext.class_AppDialog import AppDialog as _AppDialog
from core.ui_lang import t
from core.app_paths import neocat_context_path, neocat_cache_dir

APKTOOL_LINK_ROLE = Qt.UserRole + 71


def _restore_neobranch_in_worker(worker, state):

    def wrapped():
        try:
            result = worker()
        except Exception as exc:
            result = False, str(exc)
        manager = state.get("mgr")
        fs_root = state.get("fs_root")
        if manager is not None and fs_root:
            manifest = os.path.join(manager._source_dir(fs_root), "repack_manifest.json")
            if os.path.isfile(manifest):
                try:
                    manager.restore_after_repack(fs_root)
                except Exception as exc:
                    return False, f"NeoBranch restore failed: {exc}"
            state["overlaid"] = False
        return result
    return wrapped


def _halt_on_neobranch_restore_failure(ok, msg):
    return not ok and str(msg).startswith("NeoBranch restore failed:")


def _clamp_popup_pos(popup, global_pos, root_window):

    from PySide6.QtCore import QPoint
    hint = popup.sizeHint()
    pw = hint.width()  if hint.width()  > 0 else popup.width()
    ph = hint.height() if hint.height() > 0 else popup.height()
    gx = global_pos.x()
    gy = global_pos.y()

    if root_window:
        win_tl = root_window.mapToGlobal(root_window.rect().topLeft())
        win_br = root_window.mapToGlobal(root_window.rect().bottomRight())
        wx, wy = win_tl.x(), win_tl.y()
        ww, wh = win_br.x(), win_br.y()
        x = gx if gx + pw <= ww else max(wx, ww - pw)
        y = gy if gy + ph <= wh else max(wy, wh - ph)
    else:
        x, y = gx, gy

    popup.move(QPoint(x, y))


class FileTreeContextMixin:
    def _guard_unpack_scan(self, folder, callback, *, task_id=None, on_failure=None):

        queue = getattr(self.window(), "task_queue", None)
        sequence = None
        if task_id is not None and queue is not None:
            results = [result for result in queue.results_since(0) if result[1] == task_id]
            if results:
                sequence = results[-1][0]
        def guarded(ok, payload):
            if getattr(self, "_background_stopped", False):
                return
            main = self.window()
            if getattr(main, "_closing", False):
                return
            try:
                if not ok:
                    raise RuntimeError(f"Unpack scan failed: {payload}")
                callback(ok, payload)
            except Exception as error:
                import traceback
                print(f"[UNPACK] post-processing failed for {folder}: {error}\n{traceback.format_exc()}")
                if sequence is not None:
                    queue.record_failure(task_id, str(error), sequence=sequence)
                try:
                    self.resume_watcher(folder)
                finally:
                    getattr(self, "pending_loading", {}).pop(os.path.normpath(folder), None)
                    if on_failure is not None:
                        on_failure(False)
                    elif task_id is not None and hasattr(main, "pop_task"):
                        main.pop_task(task_id, False)
                        main.maybe_finish_loading()
        return guarded

    def RightClick(self, pos):
        self._right_click_active = True
        FileTreeContextMixin._global_right_click_active = True
        try:
            self.viewport().update()
        except Exception:
            pass
        try:
            return self._RightClick_impl(pos)
        finally:
            self._right_click_active = False
            FileTreeContextMixin._global_right_click_active = False
            try:
                self.viewport().update()
            except Exception:
                pass

    def _RightClick_impl(self, pos):

        item = self.itemAt(pos)
        if not item:
            item = (self.invisibleRootItem() if getattr(self, "display_mode", "tree") == "single_dir"
                    else self.topLevelItem(0))
            if not item or not item.data(0, Qt.UserRole):
                return

        # multi-select batch menu
        selected = self.selectedItems()
        if len(selected) > 1 and item in selected:
            global_pos = self.viewport().mapToGlobal(pos)
            self._batch_rightclick(selected, global_pos)
            return


        if self.is_mount_node(item):
            ref = self.mount_ref(item)
            if ref and ref[1]:
                self._mount_rightclick(item, ref, self.viewport().mapToGlobal(pos))
                return

        full = item.data(0, Qt.UserRole)
        is_file = os.path.isfile(full)
        is_dir  = os.path.isdir(full)


        extract_archive_all_action  = None
        extract_archive_here_action = None
        unpack_action      = None   # EROFS ONLY
        unpack_ext_action  = None   # EXT2/3/4
        repack_action      = None
        repack_ext_action  = None
        neocat_scan_action = None   # NEOCAT CONTEXT SCAN
        convert_sparse_action = None
        unpack_sparse_action  = None
        unpack_super_action   = None
        open_super_action     = None

        fs_badge = item.data(0, Qt.UserRole + 1)


        global_pos = self.viewport().mapToGlobal(pos)

        popup = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)

        outer = QVBoxLayout(popup)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from PySide6.QtGui import QColor as _QC
        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = _QC(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=18.0, tint=_tint,
                        border=_QC(_br, _bg, _bb, int(0.18 * 255)))
        card.setObjectName("popup_card")
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        result = [None]
        # If non-None, parent popup closes and a submenu of this kind is
        # opened anchored at the saved button geometry.
        submenu_state = {"kind": None, "anchor_btn": None}

        _sub_buttons = {}
        _submenu_arrows = {}
        active_sub_popup = [None]
        _hover_filter = [None]
        _outside_filter = [None]
        _root_window = [self.window()]
        _popup_closing = [False]

        def _btn(label, enabled=True, danger=False):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(enabled)
            b.setAttribute(Qt.WA_Hover, True)
            fg     = _th.FG       if not danger else _th.RED_LIGHT
            fg_h   = _th.FG_BRIGHT if not danger else _th.RED_LIGHTER
            bg_h   = _th.accent(0.14) if not danger else _th.red(0.14)
            try:
                _h = fg.lstrip('#')
                fg_dis = f"rgba({int(_h[0:2], 16)}, {int(_h[2:4], 16)}, {int(_h[4:6], 16)}, 128)"
            except Exception:
                fg_dis = _th.FG_DISABLED2
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    color: {fg if enabled else fg_dis};
                    font-family: 'Poppins','Inter','Segoe UI',sans-serif;
                    font-size: 12px;
                    text-align: left;
                    padding: 0 28px 0 14px;
                }}
                QPushButton:hover:enabled {{ background: {bg_h}; color: {fg_h}; }}
                QPushButton:pressed:enabled {{ background: {_th.border_color(0.08)}; }}
            """)
            lay.addWidget(b)
            if _hover_filter[0]:
                b.installEventFilter(_hover_filter[0])
            return b

        def _sep():
            d = QWidget()
            d.setFixedHeight(1)
            d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
            lay.addWidget(d)

        def _pick(key):
            result[0] = key
            _close_popup()

        def _is_qt_alive(widget):
            if widget is None:
                return False
            try:
                from shiboken6 import isValid
                return isValid(widget)
            except Exception:
                return True

        def _close_active_sub():
            if _is_qt_alive(active_sub_popup[0]):
                try:
                    active_sub_popup[0].close()
                except RuntimeError:
                    pass
                except Exception:
                    pass
            active_sub_popup[0] = None

        def _close_popup():
            if _popup_closing[0]:
                return
            _popup_closing[0] = True
            if app is not None and _outside_filter[0] is not None:
                try:
                    app.removeEventFilter(_outside_filter[0])
                except Exception:
                    pass
                _outside_filter[0] = None
            _close_active_sub()
            if _is_qt_alive(popup):
                try:
                    popup.close()
                except RuntimeError:
                    pass
                except Exception:
                    pass

        def _submenu_label(label):
            text = str(label).rstrip()
            for suffix in ("▸", ">", "›"):
                if text.endswith(suffix):
                    text = text[:-len(suffix)].rstrip()
            return text

        def _position_submenu_arrow(btn):
            arrow = _submenu_arrows.get(btn)
            if arrow is None:
                return
            arrow.move(btn.width() - arrow.width() - 10,
                       (btn.height() - arrow.height()) // 2)

        def _make_submenu_button(btn, kind):
            btn.setText(_submenu_label(btn.text()))
            _sub_buttons[btn] = kind

            arrow = QLabel(btn)
            arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            arrow.setFixedSize(12, 12)
            try:
                from icons.handler import icon_pixmap
                pix = icon_pixmap("arrow_right")
                if not pix.isNull():
                    arrow.setPixmap(pix.scaled(12, 12, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception:
                arrow.setText(">")
                arrow.setStyleSheet(f"color: {_th.FG_DISABLED2}; background: transparent;")
            arrow.show()
            _submenu_arrows[btn] = arrow
            _position_submenu_arrow(btn)
            return btn

        from PySide6.QtCore import QObject, QEvent

        class _HoverFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.HoverEnter:
                    if obj in _sub_buttons:
                        _open_sub(_sub_buttons[obj], obj)
                    else:
                        _close_active_sub()
                elif event.type() == QEvent.Resize and obj in _submenu_arrows:
                    _position_submenu_arrow(obj)
                return False

        _hover_filter[0] = _HoverFilter(popup)

        class _OutsideClickFilter(QObject):
            def _global_pos(self, event):
                if hasattr(event, "globalPosition"):
                    return event.globalPosition().toPoint()
                if hasattr(event, "globalPos"):
                    return event.globalPos()
                return None

            def _contains_global(self, widget, point):
                if not _is_qt_alive(widget) or point is None or not widget.isVisible():
                    return False
                rect = widget.rect()
                rect.moveTopLeft(widget.mapToGlobal(rect.topLeft()))
                return rect.contains(point)

            def eventFilter(self, obj, event):
                if _popup_closing[0] or not _is_qt_alive(popup):
                    return False

                et = event.type()
                if et in (QEvent.ApplicationDeactivate,):
                    _close_popup()
                    return False

                if obj is _root_window[0] and et in (
                    QEvent.Move,
                    QEvent.Resize,
                    QEvent.Hide,
                    QEvent.WindowStateChange,
                ):
                    _close_popup()
                    return False

                if et == QEvent.MouseButtonPress:
                    point = self._global_pos(event)
                    if not self._contains_global(popup, point) and not self._contains_global(active_sub_popup[0], point):
                        _close_popup()
                return False

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            _outside_filter[0] = _OutsideClickFilter(app)
            app.installEventFilter(_outside_filter[0])

        def _cleanup_outside_filter():
            _popup_closing[0] = True
            if app is not None and _outside_filter[0] is not None:
                try:
                    app.removeEventFilter(_outside_filter[0])
                except Exception:
                    pass
                _outside_filter[0] = None

        popup.destroyed.connect(_cleanup_outside_filter)

        def _open_sub(kind, btn):
            _close_active_sub()

            sub_popup = QWidget(popup, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            sub_popup.setAttribute(Qt.WA_TranslucentBackground)
            sub_popup.setAttribute(Qt.WA_DeleteOnClose)
            active_sub_popup[0] = sub_popup

            sub_outer = QVBoxLayout(sub_popup)
            sub_outer.setContentsMargins(0, 0, 0, 0)
            sub_outer.setSpacing(0)

            from PySide6.QtGui import QColor as _QC2
            _tint2 = _QC2(_th.BG2); _tint2.setAlpha(128)
            _br2, _bg2, _bb2 = _th._palette["_border_rgb"]
            sub_card = BlurCard(radius=18.0, tint=_tint2,
                                border=_QC2(_br2, _bg2, _bb2, int(0.18 * 255)))
            sub_card.setObjectName("popup_card")
            sub_outer.addWidget(sub_card)

            sub_lay = QVBoxLayout(sub_card)
            sub_lay.setContentsMargins(4, 4, 4, 4)
            sub_lay.setSpacing(1)

            def _sbtn(label, enabled=True):
                b = QPushButton(label)
                b.setFixedHeight(32)
                b.setCursor(Qt.PointingHandCursor)
                b.setEnabled(enabled)
                fg     = _th.FG
                fg_h   = _th.FG_BRIGHT
                bg_h   = _th.accent(0.14)
                try:
                    _h = fg.lstrip('#')
                    fg_dis = f"rgba({int(_h[0:2], 16)}, {int(_h[2:4], 16)}, {int(_h[4:6], 16)}, 128)"
                except Exception:
                    fg_dis = _th.FG_DISABLED2
                b.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        border: none;
                        border-radius: 6px;
                        color: {fg if enabled else fg_dis};
                        font-family: 'Poppins','Inter','Segoe UI',sans-serif;
                        font-size: 12px;
                        text-align: left;
                        padding: 0 14px;
                    }}
                    QPushButton:hover:enabled {{ background: {bg_h}; color: {fg_h}; }}
                    QPushButton:pressed:enabled {{ background: {_th.border_color(0.08)}; }}
                """)
                sub_lay.addWidget(b)
                return b

            def _spick(key):
                result[0] = key
                _close_popup()

            def _ssep():
                d = QWidget()
                d.setFixedHeight(1)
                d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
                sub_lay.addWidget(d)

            # entries: list of (label_key, action_key, *required_binaries)
            # Entri di-skip kalau binary-nya belum terinstall.
            entries = []
            if kind == "unpack_br":
                entries = [
                    (t("rc_to_raw"),    "unpack_br_to_image",  "brotli.exe"),
                    (t("rc_to_folder"), "unpack_br_to_folder", "brotli.exe", "extract.erofs.exe"),
                ]
            elif kind == "unpack_dat_br":
                entries = [
                    (t("rc_to_new_dat"), "unpack_dat_br_to_dat",    "brotli.exe"),
                    (t("rc_to_raw"),     "unpack_dat_br_to_image",  "brotli.exe"),
                    (t("rc_to_folder"),  "unpack_dat_br_to_folder", "brotli.exe", "extract.erofs.exe"),
                ]
            elif kind == "unpack_dat":
                entries = [
                    # sdat2img pure-python → no binary required for raw image
                    (t("rc_to_raw"),    "unpack_dat_to_image"),
                    (t("rc_to_folder"), "unpack_dat_to_folder", "extract.erofs.exe"),
                ]
            elif kind == "repack_erofs":
                entries = [
                    (t("rc_to_raw"),     "repack_erofs_to_image",   "mkfs.erofs.exe"),
                    (t("rc_to_brotli"),  "repack_erofs_to_brotli",  "mkfs.erofs.exe", "brotli.exe"),
                    (t("rc_to_new_dat"), "repack_erofs_to_new_dat", "mkfs.erofs.exe", "img2simg.exe"),
                    (t("rc_to_dat_br"),  "repack_erofs_to_dat_br",  "mkfs.erofs.exe", "img2simg.exe", "brotli.exe"),
                ]
            elif kind == "repack_ext":
                entries = [
                    (t("rc_to_raw"),     "repack_ext_to_image",   "make_ext4fs.exe"),
                    (t("rc_to_brotli"),  "repack_ext_to_brotli",  "make_ext4fs.exe", "brotli.exe"),
                    (t("rc_to_new_dat"), "repack_ext_to_new_dat", "make_ext4fs.exe", "img2simg.exe"),
                    (t("rc_to_dat_br"),  "repack_ext_to_dat_br",  "make_ext4fs.exe", "img2simg.exe", "brotli.exe"),
                ]
            elif kind == "convert_img":
                entries = [
                    (t("rc_convert_sparse"), "convert_to_sparse",      "img2simg.exe"),
                    (t("rc_to_brotli"),      "convert_img_to_br",      "brotli.exe"),
                    (t("rc_to_new_dat"),     "convert_img_to_new_dat", "img2simg.exe"),
                    (t("rc_to_dat_br"),      "convert_img_to_dat_br",  "img2simg.exe", "brotli.exe"),
                ]

            first = True
            for entry in entries:
                label, action_key, *req_bins = entry
                if req_bins and not is_available(*req_bins):
                    continue
                if not first:
                    _ssep()
                first = False
                _b = _sbtn(label)
                _b.clicked.connect(lambda _=False, _k=action_key: _spick(_k))

            sub_card.adjustSize()
            sub_popup.adjustSize()

            from PySide6.QtCore import QPoint
            btn_global = btn.mapToGlobal(btn.rect().topRight())
            target_pos = btn_global + QPoint(5, -4)

            _root2 = self.window()
            _clamp_popup_pos(sub_popup, target_pos, _root2)
            if _root2:
                sub_card.freeze(_root2, sub_popup.pos())
            sub_popup.show()

        # BASIC
        btn_rename    = _btn(t("rc_rename"))
        btn_delete    = _btn(t("rc_delete"), danger=True)
        _sep()


        _can_paste = self.can_paste()
        btn_copy  = _btn(t("rc_copy"))
        btn_cut   = _btn(t("rc_cut"))
        btn_paste = _btn(t("rc_paste"), enabled=_can_paste)

        btn_rename.clicked.connect(lambda: _pick("rename"))
        btn_delete.clicked.connect(lambda: _pick("delete"))
        btn_copy.clicked.connect(lambda: _pick("copy"))
        btn_cut.clicked.connect(lambda: _pick("cut"))
        if _can_paste:
            btn_paste.clicked.connect(lambda: _pick("paste"))

        _sep()
        btn_new_file       = _btn(t("rc_new_file"))
        btn_new_folder     = _btn(t("rc_new_folder"))
        btn_create_symlink = _btn(t("rc_create_symlink"))
        btn_new_file.clicked.connect(lambda: _pick("new_file"))
        btn_new_folder.clicked.connect(lambda: _pick("new_folder"))
        btn_create_symlink.clicked.connect(lambda: _pick("create_symlink"))

        _sep()
        btn_open_explorer = _btn(t("rc_open_explorer"))
        btn_open_explorer.clicked.connect(lambda: _pick("open_in_explorer"))

        # Submenu kinds → list of binaries-per-entry (urut sama dgn entries di _open_sub).
        # Parent submenu button hanya muncul kalau MINIMAL satu entri masih tersedia.
        _SUBMENU_BINS = {
            "unpack_br":     [("brotli.exe",), ("brotli.exe", "extract.erofs.exe")],
            "unpack_dat_br": [("brotli.exe",), ("brotli.exe",), ("brotli.exe", "extract.erofs.exe")],
            "unpack_dat":    [(), ("extract.erofs.exe",)],
            "repack_erofs":  [("mkfs.erofs.exe",), ("mkfs.erofs.exe", "brotli.exe"),
                              ("mkfs.erofs.exe", "img2simg.exe"),
                              ("mkfs.erofs.exe", "img2simg.exe", "brotli.exe")],
            "repack_ext":    [("make_ext4fs.exe",), ("make_ext4fs.exe", "brotli.exe"),
                              ("make_ext4fs.exe", "img2simg.exe"),
                              ("make_ext4fs.exe", "img2simg.exe", "brotli.exe")],
            "convert_img":   [("img2simg.exe",), ("brotli.exe",),
                              ("img2simg.exe",), ("img2simg.exe", "brotli.exe")],
        }

        def _submenu_any_available(kind):
            for req in _SUBMENU_BINS.get(kind, []):
                if not req or is_available(*req):
                    return True
            return False

        # FILE-SPECIFIC
        if is_file:
            # EROFS / EXT image  →  "Unpack Image" (langsung)
            if fs_badge == "EROFS":
                _has_unpack = is_available("extract.erofs.exe")
                _has_convert = _submenu_any_available("convert_img")
                if _has_unpack or _has_convert:
                    _sep()
                if _has_unpack:
                    b = _btn(t("rc_unpack_image"))
                    b.clicked.connect(lambda: _pick("unpack_erofs"))
                    unpack_action = b
                if _has_convert:
                    b_cv = _btn(t("rc_convert"))
                    _make_submenu_button(b_cv, "convert_img")

            elif fs_badge in ("EXT2", "EXT3", "EXT4"):
                _has_unpack = is_available("ext_tool.exe")
                _has_convert = _submenu_any_available("convert_img")
                if _has_unpack or _has_convert:
                    _sep()
                if _has_unpack:
                    b = _btn(t("rc_unpack_image"))
                    b.clicked.connect(lambda: _pick("unpack_ext"))
                    unpack_ext_action = b
                if _has_convert:
                    b_cv = _btn(t("rc_convert"))
                    _make_submenu_button(b_cv, "convert_img")

            # MOUNT / UNMOUNT (virtual, read-only) — tanpa binary.
            # EROFS/EXT langsung; SPARSE hanya kalau isinya EXT/EROFS (tembus sparse).
            _can_mount = fs_badge in ("EROFS", "EXT2", "EXT3", "EXT4")
            if not _can_mount and fs_badge == "SPARSE":
                try:
                    from core import imgfs
                    _can_mount = imgfs.detect_fs(full) in ("EXT", "EROFS")
                except Exception:
                    _can_mount = False
            if _can_mount:
                _sep()
                if self.is_mounted(full):
                    b_um = _btn(t("rc_unmount"))
                    b_um.clicked.connect(lambda: _pick("unmount"))


                    b_us = _btn("Space usage...")
                    b_us.clicked.connect(lambda: _pick("usage"))
                else:
                    b_mt = _btn(t("rc_mount"))
                    b_mt.clicked.connect(lambda: _pick("mount"))

            # BROTLI .img.br  →  "Unpack ▸" submenu (To RAW / To Folder)
            elif fs_badge == "BR":
                if _submenu_any_available("unpack_br"):
                    _sep()
                    b = _btn(t("rc_unpack"))
                    _make_submenu_button(b, "unpack_br")

            # DAT.BR (.new.dat.br)  →  "Unpack ▸" submenu
            elif fs_badge == "DAT.BR":
                if _submenu_any_available("unpack_dat_br"):
                    _sep()
                    b = _btn(t("rc_unpack"))
                    _make_submenu_button(b, "unpack_dat_br")

            # DAT (.new.dat)  →  "Unpack ▸" submenu
            elif fs_badge == "DAT":
                if _submenu_any_available("unpack_dat"):
                    _sep()
                    b = _btn(t("rc_unpack"))
                    _make_submenu_button(b, "unpack_dat")

            # SPARSE — convert_raw (lpunpack --unsparse) & unpack_sparse (lpunpack)
            if fs_badge == "SPARSE":
                if is_available("lpunpack.exe"):
                    _sep()
                    b1 = _btn(t("rc_convert_raw"))
                    b2 = _btn(t("rc_unpack_sparse"))
                    b1.clicked.connect(lambda: _pick("convert_sparse"))
                    b2.clicked.connect(lambda: _pick("unpack_sparse"))
                    convert_sparse_action = b1
                    unpack_sparse_action  = b2

            # SUPER
            if fs_badge == "SUPER":
                _has_super = is_available("lpunpack.exe")
                _has_sparse_conv = is_available("img2simg.exe")
                if _has_super or _has_sparse_conv:
                    _sep()
                if _has_super:
                    b1 = _btn(t("rc_open_super"))
                    b2 = _btn(t("rc_unpack_super"))
                    b1.clicked.connect(lambda: _pick("open_super"))
                    b2.clicked.connect(lambda: _pick("unpack_super"))
                    open_super_action   = b1
                    unpack_super_action = b2
                if _has_sparse_conv:
                    b3 = _btn(t("rc_convert_sparse"))
                    b3.clicked.connect(lambda: _pick("convert_to_sparse"))

            # BOOT / VENDOR_BOOT
            if fs_badge in ("BOOT", "VNDRBOOT"):
                if is_available("magiskboot.exe"):
                    _sep()
                    b = _btn(t("rc_unpack_ext", fs=fs_badge))
                    b.clicked.connect(lambda: _pick("unpack_boot"))

            # ARCHIVE
            ext = os.path.splitext(full)[1].lower()
            if ext in self.ARCHIVE_EXT:
                _sep()
                b1 = _btn(t("rc_extract_here"))
                b2 = _btn(t("rc_extract_to"))
                b1.clicked.connect(lambda: _pick("extract_here"))
                b2.clicked.connect(lambda: _pick("extract_to"))
                extract_archive_here_action = b1
                extract_archive_all_action  = b2

            # APK — decompile / recompile+sign / sign / unsign via apktool
            if ext == ".apk" and self.base:
                _apk_decompiled = apktool_tool.is_decompiled(self.base, full)
                _has_apktool = apktool_tool.is_apktool_available()
                _has_signer = apktool_tool.is_signer_available()
                _show_decompile = not _apk_decompiled and _has_apktool
                _show_build     = _apk_decompiled and _has_apktool
                _show_recompile = _show_build and _has_signer
                if _show_decompile or _show_build or _has_signer or _apk_decompiled:
                    _sep()
                if _show_decompile:
                    b = _btn(t("rc_apktool_decompile"))
                    b.clicked.connect(lambda: _pick("apktool_decompile"))
                if _apk_decompiled:
                    _is_folder = bool(item.data(0, APKTOOL_LINK_ROLE))
                    if _is_folder:
                        b_mode = _btn(t("rc_apktool_switch_to_apk"))
                    else:
                        b_mode = _btn(t("rc_apktool_switch_to_folder"))
                    b_mode.clicked.connect(lambda: _pick("apktool_switch_mode"))
                if _show_build:
                    b = _btn(t("rc_apktool_recompile"))
                    b.clicked.connect(lambda: _pick("apktool_build"))
                if _show_recompile:
                    b = _btn(t("rc_apktool_recompile_sign"))
                    b.clicked.connect(lambda: _pick("apktool_build_sign"))
                if _show_build:
                    b = _btn(t("rc_apktool_recompile_orig"))
                    b.clicked.connect(lambda: _pick("apktool_build_keeporig"))
                if _has_signer:
                    b1 = _btn(t("rc_apktool_sign"))
                    b2 = _btn(t("rc_apktool_unsign"))
                    b1.clicked.connect(lambda: _pick("apktool_sign"))
                    b2.clicked.connect(lambda: _pick("apktool_unsign"))

            # CREATE FLASHABLE — file image partisi (raw/sparse/zst/br/dat/boot/...)
            _flash_badges = ("EROFS", "EXT2", "EXT3", "EXT4", "SPARSE",
                             "BOOT", "VNDRBOOT", "BR", "DAT", "DAT.BR")
            _flash_ext = ext in (".img", ".zst", ".zstd", ".bin")
            if fs_badge in _flash_badges or _flash_ext:
                _sep()
                b_flash = _btn(t("rc_create_flashable"))
                b_flash.clicked.connect(lambda: _pick("create_flashable"))

            # INSPECT BINARY / HEX
            if ext in (".bin", ".fsm", ".ccu", ".blob", ".dat", ".raw"):
                _sep()
                b_bin = _btn(t("rc_inspect_bin"))
                b_bin.clicked.connect(lambda: _pick("inspect_bin"))


        # DIR-SPECIFIC
        if is_dir:
            if self.base:
                rel_full = os.path.relpath(full, self.base).replace("\\", "/")
                _is_fs_root = (
                    "/" not in rel_full.strip("/")
                    and hasattr(self, "engine")
                    and self.engine is not None
                    and rel_full in self.engine.root_map_ctx
                    and rel_full in self.engine.root_map_fs
                )
                if _is_fs_root:
                    _sep()
                    b = _btn(t("rc_neocat_selinux"))
                    b.clicked.connect(lambda: _pick("neocat"))
                    neocat_scan_action = b


                _is_root_level = "/" not in rel_full.strip("/")
                _has_known_badge = fs_badge in (
                    "EROFS", "EXT2", "EXT3", "EXT4", "BOOT", "VNDRBOOT", "SUPER",
                )
                if (
                    _is_root_level
                    and not _has_known_badge
                    and not _is_fs_root
                    and rel_full.strip("/") not in ("config", "META-INF")
                ):
                    _sep()
                    b_mkfs = _btn(t("rc_make_filesystem"))
                    b_mkfs.clicked.connect(lambda: _pick("make_filesystem"))
                    make_filesystem_action = b_mkfs

            if fs_badge == "EROFS":
                _has_repack = _submenu_any_available("repack_erofs")
                # convert_fs (EROFS→EXT4) cuma rewrite metadata: pure-python, selalu OK.
                _sep()
                if _has_repack:
                    b = _btn(t("rc_repack"))
                    _make_submenu_button(b, "repack_erofs")
                    repack_action = b
                b = _btn(t("rc_convert_to_fs", fs="EXT4"))
                b.clicked.connect(lambda: _pick("convert_fs"))

            elif fs_badge in ("EXT2", "EXT3", "EXT4"):
                _has_repack = _submenu_any_available("repack_ext")
                _sep()
                if _has_repack:
                    b = _btn(t("rc_repack"))
                    _make_submenu_button(b, "repack_ext")
                    repack_ext_action = b
                b = _btn(t("rc_convert_to_fs", fs="EROFS"))
                b.clicked.connect(lambda: _pick("convert_fs"))

            elif fs_badge in ("BOOT", "VNDRBOOT"):
                if is_available("magiskboot.exe"):
                    _sep()
                    b = _btn(t("rc_repack_ext", fs=fs_badge))
                    b.clicked.connect(lambda: _pick("repack_boot"))

            # NeoBranch
            if self.base:
                _rel_nb = os.path.relpath(full, self.base).replace("\\", "/")
                _is_nb_root = (
                    "/" not in _rel_nb.strip("/")
                    and hasattr(self, "engine") and self.engine is not None
                    and _rel_nb in self.engine.root_map_ctx
                    and _rel_nb in self.engine.root_map_fs
                )
                if _is_nb_root:
                    from main_ext.class_NeoBranchManager import NeoBranchManager
                    _nb_tmp = NeoBranchManager(self.base)
                    _nb_active = _nb_tmp.get_active_branch(_rel_nb)
                    _nb_has    = _nb_tmp.has_any_branch(_rel_nb)
                    _sep()
                    if _nb_has:
                        _nb_lbl = t("rc_neobranch_active", name=_nb_active['name']) if _nb_active else t("rc_neobranch_source")
                    else:
                        _nb_lbl = t("rc_neobranch_create")
                    _btn_nb = _btn(_nb_lbl)
                    _btn_nb.clicked.connect(lambda: _pick("neobranch"))


        # SHOW & WAIT
        card.adjustSize()
        popup.adjustSize()
        _root = self.window()
        _clamp_popup_pos(popup, global_pos, _root)
        if _root:
            card.freeze(_root, popup.pos())
        popup.show()

        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()
        if app is not None and _outside_filter[0] is not None:
            try:
                app.removeEventFilter(_outside_filter[0])
            except Exception:
                pass
            _outside_filter[0] = None
        _close_active_sub()

        # SUBMENU LEVEL-2
        # Note: Submenu is now handled dynamically within _open_sub!
        action = result[0]

        if not action:
            return


        return self._dispatch_rc_action(action, item, full, is_file, is_dir, fs_badge)

    def _dispatch_rc_action(self, action, item, full, is_file, is_dir, fs_badge):


        if isinstance(action, str) and action.startswith(("repack_erofs", "repack_ext")):
            try:
                _actual = detect_fs_root(os.path.dirname(full), os.path.basename(full))
            except Exception:
                _actual = ""
            if _actual:
                _want_ext = _actual.startswith("EXT")
                _is_ext_action = action.startswith("repack_ext")
                if _want_ext != _is_ext_action:
                    if _want_ext:
                        action = action.replace("repack_erofs", "repack_ext", 1)
                    else:
                        action = action.replace("repack_ext", "repack_erofs", 1)
                    # sinkronkan badge item supaya menu berikutnya konsisten
                    try:
                        item.setData(0, Qt.UserRole + 1, _actual)
                    except Exception:
                        pass
                    print(f"[REPACK] action dikoreksi -> {action} (FS aktual={_actual})")


        if action == "extract_here":
            self._extract_archive_here(item, full, to_subdir=False)
            return

        if action == "extract_to":
            self._extract_archive_here(item, full, to_subdir=True)
            return


        if action == "create_flashable":
            from main_ext.class_FlashableDialog import FlashableDialog
            FlashableDialog.launch(self, full, fs_badge)
            return


        if action == "mount":
            self.mount_image(item)
            return

        if action == "unmount":
            self.unmount_image(item)
            return

        if action == "usage":
            self.show_mount_usage(full)
            return

        if action == "rename":
            return self.rename_item(item)

        if action == "delete":
            self.delete_item(item)
            return

        if action == "duplicate":
            self._duplicate_item(item, full)
            return

        if action == "copy":
            self.clip_copy([item])
            return

        if action == "cut":
            self.clip_cut([item])
            return

        if action == "paste":
            self.clip_paste(item)
            return

        if action == "new_file":
            target_dir = full if is_dir else os.path.dirname(full)
            parent_item = item if is_dir else (item.parent() or self.topLevelItem(0))
            self._create_new_item(parent_item, target_dir, is_folder=False)
            return

        if action == "new_folder":
            target_dir = full if is_dir else os.path.dirname(full)
            parent_item = item if is_dir else (item.parent() or self.topLevelItem(0))
            self._create_new_item(parent_item, target_dir, is_folder=True)
            return

        if action == "create_symlink":
            from main_ext.class_SymlinkDialog import SymlinkDialog
            target_dir = full if is_dir else os.path.dirname(full)
            parent_item = item if is_dir else (item.parent() or self.topLevelItem(0))
            created = SymlinkDialog.run(self.window(), target_dir)
            if created > 0:
                try:
                    self.refresh_folder(parent_item)
                except Exception:
                    pass
            return

        if action == "open_in_explorer":
            self._open_in_explorer(full)
            return


        if action == "neocat":
            self._run_neocat_scan(item, full)
            return


        if action == "neobranch":
            self._handle_neobranch(item, full)
            return


        if action == "make_filesystem":
            from main_ext.class_MakeFilesystemDialog import MakeFilesystemDialog
            MakeFilesystemDialog.launch(self, full)
            return


        if action == "convert_fs":
            from core.filesystem_converter import convert_filesystem_metadata

            target_fs = "EXT4" if fs_badge == "EROFS" else "EROFS"
            ok, msg = convert_filesystem_metadata(full, target_fs)
            if not ok:
                _AppDialog.error(self, t("lbl_error"), msg)
                return

            try:
                self.reload_config_cache()
            except Exception:
                pass

            try:
                new_fs = detect_fs_root(os.path.dirname(full), os.path.basename(full))
                if new_fs:
                    item.setData(0, Qt.UserRole + 1, new_fs)
                    item.setIcon(0, self.icon_fs_folder)
                parent = item.parent()
                if parent:
                    self.refresh_folder(parent)
            except Exception:
                pass

            return


        elif action == "convert_sparse":

            print("[SUPER] convert to RAW clicked:", full)

            main = self.window()
            task_id = f"convert:{full}"

            if main:
                main.push_task(task_id)

            img_path = full
            out_dir = os.path.dirname(img_path)

            def worker():
                print("[SUPER] running unsparse:", img_path)
                ok, msg = convert_to_raw(img_path, out_dir)
                return ok, msg

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    _AppDialog.error(self, t("rc_convert_raw_failed"), msg)
                    if main:
                        main.set_loading_failed(t("rc_convert_raw_failed"))
                    return

                print("[SUPER] convert to RAW finished")

                parent_item = self.find_item_by_path(
                    self.topLevelItem(0),
                    out_dir
                )
                if parent_item:


                    self.magic_cache.pop(img_path, None)
                    self.fsdetect_cache.pop(img_path, None)
                    self.superdetect_cache.pop(img_path, None)

                    # 🔁 RE-DETECT BADGE SETELAH CONVERT
                    new_badge = self.get_image_badge(img_path)
                    item.setData(0, Qt.UserRole + 1, new_badge)
                    self.refresh_folder(parent_item)

                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "convert_to_sparse":
            from core.img2simg import img2simg as _img2simg_conv

            print("[SPARSE] convert to SPARSE clicked:", full)

            main = self.window()
            task_id = f"convert-sparse:{full}"

            if main:
                main.push_task(task_id)

            img_path = full
            out_dir = os.path.dirname(img_path)

            def worker():
                sparse_out = img_path + ".sparse"
                ok, result = _img2simg_conv(img_path, sparse_out)
                if not ok:
                    return False, result


                backup = img_path + ".bak"
                try:
                    os.rename(img_path, backup)
                    os.rename(sparse_out, img_path)
                except Exception as e:
                    # Restore dari backup kalau rename sparse gagal
                    if os.path.exists(backup) and not os.path.exists(img_path):
                        try:
                            os.rename(backup, img_path)
                        except Exception:
                            pass
                    return False, f"rename failed: {e}"
                # Cleanup backup hanya setelah rename sukses
                try:
                    os.remove(backup)
                except Exception:
                    pass
                return True, img_path

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), "Convert to Sparse failed", msg)
                    if main:
                        main.set_loading_failed("Convert to Sparse failed")
                    return

                print("[SPARSE] convert to SPARSE finished")

                parent_item = self.find_item_by_path(
                    self.topLevelItem(0),
                    out_dir
                )
                if parent_item:
                    # magic_cache otoritatif — wajib di-pop juga, kalau ngga
                    # _detect_magic_once balik dari cache (mtime/size sama).
                    self.magic_cache.pop(img_path, None)
                    self.superdetect_cache.pop(img_path, None)
                    self.fsdetect_cache.pop(img_path, None)
                    new_badge = self.get_image_badge(img_path)
                    item.setData(0, Qt.UserRole + 1, new_badge)
                    self.refresh_folder(parent_item)

                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "convert_img_to_br":
            from core.brotli_tool import compress_to_br as _compress_img_br
            main = self.window()
            task_id = f"convert-br:{full}"
            if main: main.push_task(task_id)
            out_dir = os.path.dirname(full)

            def worker():
                ok, result = _compress_img_br(full, keep_source=True)
                return ok, result

            def after(ok, msg):
                if main: main.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), "Convert to Brotli failed", msg)
                    if main: main.set_loading_failed("Convert to Brotli failed")
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), out_dir)
                if parent_item: self.refresh_folder(parent_item)
                if main: main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "convert_img_to_new_dat":
            from core.img2simg import img2simg as _img2simg_cvdat
            from core.img2sdat import img2sdat as _img2sdat_cvdat
            main = self.window()
            task_id = f"convert-dat:{full}"
            if main: main.push_task(task_id)
            out_dir = os.path.dirname(full)

            def worker():
                sparse_out = full + ".sparse"
                ok, sparse_path = _img2simg_cvdat(full, sparse_out)
                if not ok:
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(full)[0]
                _img2sdat_cvdat.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                return True, prefix + ".new.dat"

            def after(ok, msg):
                if main: main.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), "Convert to new.dat failed", msg)
                    if main: main.set_loading_failed("Convert to new.dat failed")
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), out_dir)
                if parent_item: self.refresh_folder(parent_item)
                if main: main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "convert_img_to_dat_br":
            from core.img2simg import img2simg as _img2simg_cvdbr
            from core.img2sdat import img2sdat as _img2sdat_cvdbr
            from core.brotli_tool import compress_to_br as _compress_cvdbr
            main = self.window()
            task_id = f"convert-datbr:{full}"
            if main: main.push_task(task_id)
            out_dir = os.path.dirname(full)

            def worker():
                sparse_out = full + ".sparse"
                ok, sparse_path = _img2simg_cvdbr(full, sparse_out)
                if not ok:
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(full)[0]
                _img2sdat_cvdbr.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                dat_path = prefix + ".new.dat"
                ok_br, br_out = _compress_cvdbr(dat_path, keep_source=False)
                if not ok_br:
                    return False, f"brotli compress failed: {br_out}"
                return True, br_out

            def after(ok, msg):
                if main: main.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), "Convert to new.dat.br failed", msg)
                    if main: main.set_loading_failed("Convert to new.dat.br failed")
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), out_dir)
                if parent_item: self.refresh_folder(parent_item)
                if main: main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "open_super":
            main = self.window()
            if main:
                main.open_super_tab(full)
            return


        elif action == "inspect_bin":
            main = self.window()
            if main:
                main.open_bin_tab(full)
            return


        elif action == "unpack_boot":
            from core.boot_image import unpack_boot_image

            main = self.window()
            task_id = f"unpack-boot:{full}"
            img_path = full

            if main:
                main.push_task(task_id)

            def worker():
                return unpack_boot_image(img_path)

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    _AppDialog.error(self, t("rc_unpack_failed"), msg)
                    if main:
                        main.set_loading_failed(t("rc_unpack_failed"))
                    return

                # Reload cache agar fs_config boot/vendor_boot yang baru langsung aktif di UI
                self.reload_config_cache(with_neocat=False)

                parent_item = self.find_item_by_path(
                    self.topLevelItem(0),
                    os.path.dirname(img_path)
                )
                if parent_item:
                    self.refresh_folder(parent_item)

                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)
            return


        elif action == "apktool_decompile":
            from core.task_queue import TaskItem

            main = self.window()
            apk_path = full
            out_dir = apktool_tool.mirror_dir_for(self.base, apk_path)
            task_id = f"apktool-decompile:{apk_path}"
            paused_ancestor_watchers = []

            def _quiet_ancestors(enabled):
                self._set_bulk_watcher_quiet(self.base, False, enabled)
                self._set_bulk_watcher_quiet(
                    os.path.join(self.base, apktool_tool.APKTOOL_DEC_DIR),
                    True, enabled,
                )
                if enabled:
                    base_norm = os.path.normcase(os.path.normpath(self.base))
                    dec_norm = os.path.normcase(os.path.normpath(
                        os.path.join(self.base, apktool_tool.APKTOOL_DEC_DIR)))
                    out_norm = os.path.normcase(os.path.normpath(out_dir))
                    for watched in list(self.watcher.directories()):
                        wn = os.path.normcase(os.path.normpath(watched))
                        if (wn == base_norm or
                                (wn.startswith(dec_norm + os.sep) or wn == dec_norm)
                                and (out_norm == wn or out_norm.startswith(wn + os.sep))):
                            if self.watcher.removePath(watched):
                                paused_ancestor_watchers.append(watched)
                else:
                    for watched in paused_ancestor_watchers:
                        if os.path.isdir(watched) and (
                                self.show_apktool_dec or
                                os.path.normcase(os.path.normpath(watched)) ==
                                os.path.normcase(os.path.normpath(self.base))):
                            self.watcher.addPath(watched)
                    paused_ancestor_watchers.clear()

            def worker():
                return apktool_tool.decompile_apk(apk_path, out_dir, self.base)

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)
                try:
                    self.resume_watcher(out_dir)
                    # The hidden workspace should only be watched when its APK
                    # row is actually expanded (on_expand attaches it then).
                    if not self.show_apktool_dec:
                        self._detach_watchers_for(out_dir)
                    if not ok:
                        _AppDialog.error(self, t("rc_apktool_decompile_failed"), msg)
                        if main:
                            main.set_loading_failed(t("rc_apktool_decompile_failed"))
                        return

                    if self.show_apktool_dec and self.display_mode == "tree":
                        root = self.topLevelItem(0)
                        dec_root = os.path.join(self.base, apktool_tool.APKTOOL_DEC_DIR)
                        if (root and root.isExpanded()
                                and not self.find_item_by_path(root, dec_root)):
                            self.refresh_folder(root)


                    if not self.mark_apk_decompiled(apk_path, item):
                        if self.display_mode == "single_dir":
                            cur_sd = self.invisibleRootItem().data(0, Qt.UserRole)
                            parent_dir = os.path.dirname(apk_path)
                            if cur_sd and os.path.normpath(cur_sd) == os.path.normpath(parent_dir):
                                self._sd_render(self._sd_state_from_item(self.invisibleRootItem()))
                    if main:
                        main.maybe_finish_loading()
                finally:
                    _quiet_ancestors(False)

            _tree = self

            def _on_promote():
                _quiet_ancestors(True)
                _tree.pause_watcher(out_dir, refresh_ui=self.show_apktool_dec)

            if main:
                main.push_task(task_id)
                main.task_queue.enqueue(TaskItem(
                    task_id=task_id, worker=worker, callback=after, on_promote=_on_promote,
                ))
            else:
                _quiet_ancestors(True)
                self.pause_watcher(out_dir, refresh_ui=self.show_apktool_dec)
                run_in_thread(worker, None, callback=after)
            return

        elif action == "apktool_switch_mode":
            apk_path = full
            current_is_folder = bool(item.data(0, APKTOOL_LINK_ROLE))
            new_mode = "apk" if current_is_folder else "folder"
            if hasattr(self, "set_apk_view_mode"):
                self.set_apk_view_mode(apk_path, new_mode)

            if new_mode == "apk":
                item.setExpanded(False)
                item.takeChildren()
                item.setData(0, APKTOOL_LINK_ROLE, None)
                item.setIcon(0, self.icon_apk)
                item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            else:
                mirror_dir = apktool_tool.mirror_dir_for(self.base, apk_path)
                item.setData(0, APKTOOL_LINK_ROLE, mirror_dir)
                item.setIcon(0, self.icon_apk_linked)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                if item.childCount() == 0:
                    self.add_loading(item)
                item.setExpanded(True)
                self.on_expand(item)
            self.viewport().update()
            return


        elif action in ("apktool_build", "apktool_build_sign", "apktool_build_keeporig",
                        "apktool_sign", "apktool_unsign"):
            from core.task_queue import TaskItem

            main = self.window()
            apk_path = full
            task_id = f"{action}:{apk_path}"
            src_dir = None  # set below for the 3 build variants — apktool writes
                             # intermediate build/ files under it, same watcher
                             # event-storm hazard as decompile if it's expanded.

            if action == "apktool_build":
                src_dir = apktool_tool.mirror_dir_for(self.base, apk_path)

                def worker():
                    return apktool_tool.build_apk(src_dir, apk_path, self.base)
                fail_title_key = "rc_apktool_build_failed"
            elif action == "apktool_build_sign":
                src_dir = apktool_tool.mirror_dir_for(self.base, apk_path)

                def worker():
                    ok, msg = apktool_tool.build_apk(src_dir, apk_path, self.base)
                    if not ok:
                        return False, msg
                    return apktool_tool.sign_apk(apk_path)
                fail_title_key = "rc_apktool_build_failed"
            elif action == "apktool_build_keeporig":
                src_dir = apktool_tool.mirror_dir_for(self.base, apk_path)

                def worker():
                    return apktool_tool.build_apk(src_dir, apk_path, self.base, copy_original=True)
                fail_title_key = "rc_apktool_build_failed"
            elif action == "apktool_sign":
                def worker():
                    return apktool_tool.sign_apk(apk_path)
                fail_title_key = "rc_apktool_sign_failed"
            else:
                def worker():
                    return apktool_tool.unsign_apk(apk_path)
                fail_title_key = "rc_apktool_unsign_failed"

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)
                if src_dir:
                    self.resume_watcher(src_dir)
                if not ok:
                    _AppDialog.error(self, t(fail_title_key), msg)
                    if main:
                        main.set_loading_failed(t(fail_title_key))
                    return
                self.nb_record_save(apk_path)
                parent_dir = os.path.dirname(apk_path)
                parent_item = (
                    self.find_item_by_path(self.invisibleRootItem(), parent_dir)
                    or self.find_item_by_path(self.topLevelItem(0), parent_dir)
                )
                if parent_item:
                    self.refresh_folder(parent_item)
                elif getattr(self, "display_mode", "tree") == "single_dir":
                    cur_sd = self.invisibleRootItem().data(0, Qt.UserRole)
                    if cur_sd and os.path.normpath(cur_sd) == os.path.normpath(parent_dir):
                        self._sd_render(self._sd_state_from_item(self.invisibleRootItem()))
                if main:
                    main.maybe_finish_loading()

            _tree = self

            def _on_promote():
                if src_dir:
                    _tree.pause_watcher(src_dir)

            if main:
                main.push_task(task_id)
                main.task_queue.enqueue(TaskItem(
                    task_id=task_id, worker=worker, callback=after, on_promote=_on_promote,
                ))
            else:
                if src_dir:
                    self.pause_watcher(src_dir)
                run_in_thread(worker, None, callback=after)
            return


        elif action == "gen_devicetree":
            from core.dtgen import inputs_for_boot_unpack, boot_unpack_root, generate_device_tree

            main = self.window()
            clicked = full
            # Output lives at the base (next to the boot unpack folder + vendor).
            _unpack = boot_unpack_root(clicked)
            out_root = os.path.dirname(str(_unpack)) if _unpack else os.path.dirname(clicked)
            task_id = f"gen-devicetree:{out_root}"

            if main:
                main.push_task(task_id)

            def worker():
                inputs = inputs_for_boot_unpack(clicked)
                if inputs is None:
                    return False, "no valid vendor partition beside this boot unpack"
                try:
                    from pathlib import Path
                    dt = generate_device_tree(
                        inputs.vendor_boot, inputs.vendor, Path(out_root),
                        boot=inputs.boot,
                        boot_unpack=inputs.boot_unpack,
                        vendor_boot_unpack=inputs.vendor_boot_unpack)
                    return True, str(dt)
                except ValueError as exc:
                    return False, str(exc)
                except Exception as exc:
                    return False, f"device tree generation failed: {exc}"

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    _AppDialog.error(self, t("rc_gen_devicetree_failed"), msg)
                    if main:
                        main.set_loading_failed(t("rc_gen_devicetree_failed"))
                    return

                parent_item = self.find_item_by_path(
                    self.topLevelItem(0), out_root)
                if parent_item:
                    self.refresh_folder(parent_item)

                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)
            return


        elif action in ("unpack_sparse", "unpack_super"):

            print("[UI] Unpack SPARSE/SUPER clicked:", full)

            main = self.window()
            task_id = f"unpack:{full}"

            if main:
                main.push_task(task_id)

            img_path = full
            out_dir = os.path.dirname(img_path)

            def worker():
                print("[WORKER] unpack start:", img_path)

                # STEP 1 — UNSPARSE kalau perlu
                if fs_badge == "SPARSE":
                    print("[WORKER] image is SPARSE → unsparse")
                    ok, msg = convert_to_raw(img_path, os.path.dirname(img_path))
                    if not ok:
                        return False, f"Unsparse failed: {msg}"


                ok, msg = extract_all(img_path, out_dir)
                if not ok:
                    return False, msg

                return True, out_dir

            def after(ok, result):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    _AppDialog.error(self, t("rc_unpack_failed"), result)
                    if main:
                        main.set_loading_failed(t("rc_unpack_failed"))
                    return

                print("[UI] unpack finished:", result)

                # result = out_dir (folder output, bukan file) — refresh
                # langsung folder itu, bukan parent-nya.
                parent_item = self.find_item_by_path(
                    self.topLevelItem(0),
                    result
                )
                if not parent_item:
                    # Fallback: cari parent folder
                    parent_item = self.find_item_by_path(
                        self.topLevelItem(0),
                        os.path.dirname(result)
                    )
                if parent_item:
                    self.refresh_folder(parent_item)

                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_payload":
            main = self.window()
            task_id = f"unpack:{full}"
            if main:
                main.push_task(task_id)

            payload_path = full
            out_dir = os.path.dirname(payload_path)

            def worker():
                from core import payload_tool
                try:
                    payload_tool.extract_and_check(payload_path, out_dir)
                    return True, out_dir
                except Exception as e:
                    return False, str(e)

            def after(ok, result):
                if main:
                    main.pop_task(task_id, ok)
                if not ok:
                    _AppDialog.error(self, t("rc_unpack_failed"), str(result))
                    if main:
                        main.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), result)
                if not parent_item:
                    parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(result))
                if parent_item:
                    self.refresh_folder(parent_item)
                if main:
                    main.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_erofs":


            from core.erofs import unpack_erofs

            img_path = full
            extract_name = os.path.splitext(os.path.basename(img_path))[0]
            extracted_folder = os.path.join(os.path.dirname(img_path), extract_name)

            self.pending_loading = getattr(self, "pending_loading", {})
            self.pending_loading[os.path.normpath(extracted_folder)] = True

            def after(ok, msg):

                if hasattr(self, "current_loading_anim") and self.current_loading_anim:
                    self.current_loading_anim.stop_with_finish()
                    self.current_loading_anim = None

                if not ok:
                    _AppDialog.error(self, t("lbl_error"), msg)
                    self.resume_watcher(extracted_folder)
                    # <<< PATCH: akhiri loading walau gagal >>>
                    self.window().pop_task(f"unpack:{full}", False)
                    self.window().set_loading_failed(t("rc_unpack_failed"))
                    # <<< PATCH END >>>

                    return

                # >>> AUTO SCAN RESULT (sesuai kode lu)
                extract_name = os.path.splitext(os.path.basename(img_path))[0]
                extracted_folder = os.path.join(os.path.dirname(img_path), extract_name)

                if os.path.isdir(extracted_folder):

                    def after_subscan(ok2, payload):
                        if ok2:
                            try:
                                data = json.loads(payload)
                                folders = data.get("folders", {})
                                fscache = data.get("fscache", {})

                                for k, v in folders.items():
                                    self.folder_has_child[os.path.normpath(k)] = v

                                for k, v in fscache.items():
                                    self.fsdetect_cache[os.path.normpath(k)] = v
                            except:
                                pass

                        # apply_subtree DIHAPUS: unpacker sudah kasih config lengkap (sumber
                        # kebenaran). Menambal config yg sudah ada = ribuan engine.apply redundan.
                        self.reload_config_cache(with_neocat=False)

                        target_item = self.find_item_by_path(
                            self.topLevelItem(0),
                            extracted_folder
                        )

                        if target_item:
                            name, ctx, perm, _ = self.get_meta(extracted_folder)
                            target_item.setText(1, ctx)
                            target_item.setText(2, perm)

                            root_name = os.path.basename(extracted_folder)
                            fs_type = detect_fs_root(self.base, root_name)

                            if fs_type:
                                target_item.setText(0, root_name)
                                target_item.setIcon(0, self.icon_fs_folder)
                                target_item.setData(0, Qt.UserRole + 1, "EROFS")

                        # 🔓 RESUME WATCHER — SATU KALI, TANPA KONDISI
                        self.resume_watcher(extracted_folder)

                        parent_item = item.parent()
                        if parent_item:
                            self.refresh_folder(parent_item)

                        self.window().hide_erofs_loading()
                        self.window().pop_task(f"unpack:{full}")
                        # set_loading_finished dihandle on_queue_empty

                    run_in_thread(prescan_task, extracted_folder,
                                  callback=self._guard_unpack_scan(extracted_folder, after_subscan,
                                                                   task_id=f"unpack:{full}"))

            # ── push dulu, baru enqueue ──
            main = self.window()
            from core.task_queue import TaskItem

            def _erofs_worker():
                return unpack_erofs(full)

            _tree = self
            def _erofs_on_promote():
                _tree.pause_watcher(extracted_folder, "EROFS")

            task = TaskItem(
                task_id=f"unpack:{full}",
                worker=_erofs_worker,
                callback=after,
                on_promote=_erofs_on_promote,
            )
            if main:
                main.push_task(f"unpack:{full}")
                main.task_queue.enqueue(task)
            else:
                _tree.pause_watcher(extracted_folder, "EROFS")
                run_in_thread(unpack_erofs, full, callback=after)


        elif action == "repack_erofs":
            try:
                print("[UI] Repack clicked:", full)
                full = os.path.normpath(full)
                if not os.path.isdir(full):
                    raise RuntimeError(f"FS root not found anymore: {full}")

            except Exception as e:
                print("========== REPACK UI CRASH ==========")
                import traceback
                traceback.print_exc()
                print("====================================")
                from main_ext.class_AppDialog import AppDialog
                AppDialog.error(
                    self,
                    "Repack UI Error",
                    traceback.format_exc()
                )
                return

            base = os.path.dirname(full)
            task_id = f"repack:{full}"

            # Shared state — diisi worker, dibaca after (Pylance-safe)
            _nb_state = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from core.erofs import repack_erofs
                from main_ext.class_NeoBranchManager import NeoBranchManager

                engine = ConfigEngine(base)
                verifier = fs_verify.FSVerifier(base)


                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_state["mgr"]     = _nb_mgr
                _nb_state["fs_root"] = _fs_root

                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_state["overlaid"]    = True
                        _nb_state["branch_name"] = _nb_active["name"]
                        print(f"[NeoBranch] overlay ok: {_nb_active['name']}")
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"


                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_state["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg


                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_state["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"


                from core.repack_policy import get_selinux_mode, SELINUX_AI, SELINUX_PLAIN

                selinux_mode = get_selinux_mode()

                _neocat_cache = neocat_context_path(base, _fs_root)
                _has_neocat_cache = os.path.isfile(_neocat_cache) and os.path.getsize(_neocat_cache) > 0

                if selinux_mode == "none" and not _has_neocat_cache:
                    print("[REPACK] SELINUX PATCH SKIPPED")
                else:
                    try:
                        engine.apply_predicted_context(
                            _fs_root,
                            mode=selinux_mode if selinux_mode != "none" else "ai",
                        )
                        if _nb_state["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_state["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_state["branch_name"])
                    except Exception as e:
                        if _nb_state["overlaid"]:
                            _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"


                ok, msg = repack_erofs(full)
                if not ok and _nb_state["overlaid"]:
                    try:
                        _nb_mgr.restore_after_repack(_fs_root)
                        _nb_state["overlaid"] = False
                    except Exception as _ex:
                        print(f"[NeoBranch] restore on fail: {_ex}")
                elif ok and _nb_state["overlaid"]:
                    # Sukses: restore source tree sekarang (masih di worker)
                    try:
                        _nb_mgr.restore_after_repack(_fs_root)
                        print("[NeoBranch] source tree restored after repack")
                    except Exception as _ex:
                        return False, f"NeoBranch restore failed: {_ex}"
                    _nb_state["overlaid"] = False
                return ok, msg

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                self.window().hide_erofs_loading()

                if not ok:
                    print("[REPACK][AFTER] FULL ERROR MSG:")
                    print(msg)
                    print("[REPACK][AFTER] END ERROR")
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return

                self.on_repack_finished(full)

                # Restore sudah dilakukan di worker — cukup refresh tree
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()


            print("[REPACK][UI] about to start worker")
            print("[REPACK][UI] base =", base)
            print("[REPACK][UI] full =", full)
            print("[REPACK][UI] exists =", os.path.exists(full))

            _main = self.window()
            from core.task_queue import TaskItem
            worker = _restore_neobranch_in_worker(worker, _nb_state)
            task = TaskItem(
                task_id=task_id,
                worker=worker,
                callback=after,
                halt_if=_halt_on_neobranch_restore_failure,
            )
            if _main:
                _main.push_task(task_id)
                _main.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_boot":
            from core.boot_image import repack_boot_image
            from core.task_queue import TaskItem

            folder_path = os.path.normpath(full)
            task_id = f"repack-boot:{folder_path}"
            main = self.window()

            def worker():
                return repack_boot_image(folder_path)

            def after(ok, msg):
                if main:
                    main.pop_task(task_id, ok)

                if not ok:
                    if main:
                        main.set_loading_failed(t("rc_repack_failed"))
                    _AppDialog.error(self, t("rc_repack_failed"), msg)
                    return

                parent_item = self.find_item_by_path(
                    self.topLevelItem(0),
                    os.path.dirname(folder_path)
                )
                if parent_item:
                    self.refresh_folder(parent_item)

                try:
                    self.apply_fs_badge_after_finished(msg)
                except Exception:
                    pass

                if main:
                    # settle-point tunggal — finished cuma kalau semua service habis
                    main.maybe_finish_loading()

            task = TaskItem(
                task_id=task_id,
                worker=worker,
                callback=after,
            )
            if main:
                main.push_task(task_id)
                main.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "unpack_ext":

            from core.unpack_ext4 import main as unpack_ext

            img_path = full
            extract_name = os.path.splitext(os.path.basename(img_path))[0]
            extracted_folder = os.path.join(os.path.dirname(img_path), extract_name)

            self.pending_loading = getattr(self, "pending_loading", {})
            self.pending_loading[os.path.normpath(extracted_folder)] = True

            def after(ok, msg):

                if not ok:
                    _AppDialog.error(self, t("lbl_error"), msg)
                    self.resume_watcher(extracted_folder)
                    # <<< PATCH: loading selesai >>>
                    self.window().pop_task(f"unpack:{full}", False)
                    self.window().set_loading_failed(t("rc_unpack_failed"))
                    # <<< PATCH END >>>

                    return

                parent = item.parent()
                if parent:
                    self.refresh_folder(parent)

                if os.path.isdir(extracted_folder):

                    def after_subscan(ok2, payload):
                        if ok2:
                            try:
                                data = json.loads(payload)
                                folders = data.get("folders", {})
                                fscache = data.get("fscache", {})

                                for k, v in folders.items():
                                    self.folder_has_child[os.path.normpath(k)] = v
                                for k, v in fscache.items():
                                    self.fsdetect_cache[os.path.normpath(k)] = v
                            except:
                                pass

                        # apply_subtree DIHAPUS: unpacker sudah kasih config lengkap (sumber
                        # kebenaran). Menambal config yg sudah ada = ribuan engine.apply redundan.
                        self.reload_config_cache(with_neocat=False)

                        target_item = self.find_item_by_path(
                            self.topLevelItem(0),
                            extracted_folder
                        )

                        if target_item:
                            name, ctx, perm, _ = self.get_meta(extracted_folder)
                            target_item.setText(1, ctx)
                            target_item.setText(2, perm)

                            root_name = os.path.basename(extracted_folder)
                            fs_type = detect_fs_root(self.base, root_name)

                            if fs_type:
                                target_item.setText(0, root_name)
                                target_item.setIcon(0, self.icon_fs_folder)
                                target_item.setData(0, Qt.UserRole + 1, fs_type)

                        # 🔓 RESUME WATCHER — SATU KALI
                        self.resume_watcher(extracted_folder)

                        parent_item = item.parent()
                        if parent_item:
                            self.refresh_folder(parent_item)

                        self.window().hide_erofs_loading()
                        self.window().pop_task(f"unpack:{full}")
                        # set_loading_finished dihandle on_queue_empty

                    run_in_thread(prescan_task, extracted_folder,
                                  callback=self._guard_unpack_scan(extracted_folder, after_subscan,
                                                                   task_id=f"unpack:{full}"))

            # ── push dulu, baru enqueue ──
            main = self.window()
            from core.task_queue import TaskItem

            def _ext_worker():
                return unpack_ext(full)

            _tree2 = self
            def _ext_on_promote():
                _tree2.pause_watcher(extracted_folder, "EXT4")

            task = TaskItem(
                task_id=f"unpack:{full}",
                worker=_ext_worker,
                callback=after,
                on_promote=_ext_on_promote,
            )
            if main:
                main.push_task(f"unpack:{full}")
                main.task_queue.enqueue(task)
            else:
                _tree2.pause_watcher(extracted_folder, "EXT4")
                run_in_thread(unpack_ext, full, callback=after)


        elif action == "repack_ext":

            base = os.path.dirname(full)
            task_id = f"repack:{full}"

            _nb_state_ext = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from core.repack_ext4 import main as repack_ext
                from main_ext.class_NeoBranchManager import NeoBranchManager

                engine = ConfigEngine(base)
                verifier = fs_verify.FSVerifier(base)


                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_state_ext["mgr"]     = _nb_mgr
                _nb_state_ext["fs_root"] = _fs_root

                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_state_ext["overlaid"]    = True
                        _nb_state_ext["branch_name"] = _nb_active["name"]
                        print(f"[NeoBranch] overlay ok: {_nb_active['name']}")
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"


                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_state_ext["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg


                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_state_ext["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"


                from core.repack_policy import get_selinux_mode, SELINUX_AI, SELINUX_PLAIN

                selinux_mode = get_selinux_mode()

                _neocat_cache = neocat_context_path(base, _fs_root)
                _has_neocat_cache = os.path.isfile(_neocat_cache) and os.path.getsize(_neocat_cache) > 0

                if selinux_mode == "none" and not _has_neocat_cache:
                    print("[REPACK] SELINUX PATCH SKIPPED")
                else:
                    try:
                        engine.apply_predicted_context(
                            _fs_root,
                            mode=selinux_mode if selinux_mode != "none" else "ai",
                        )
                        if _nb_state_ext["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_state_ext["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_state_ext["branch_name"])
                    except Exception as e:
                        if _nb_state_ext["overlaid"]:
                            _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"


                try:
                    ok, msg = repack_ext(full)
                except Exception as _ex:
                    ok, msg = False, str(_ex)
                if _nb_state_ext["overlaid"]:
                    try:
                        _nb_mgr.restore_after_repack(_fs_root)
                        _nb_state_ext["overlaid"] = False
                    except Exception as _ex:
                        return False, f"NeoBranch restore failed: {_ex}"
                return ok, msg

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                self.on_repack_finished(full)
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()


            _main = self.window()
            from core.task_queue import TaskItem
            worker = _restore_neobranch_in_worker(worker, _nb_state_ext)
            task = TaskItem(
                task_id=task_id,
                worker=worker,
                callback=after,
                halt_if=_halt_on_neobranch_restore_failure,
            )
            if _main:
                _main.push_task(task_id)
                _main.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "unpack_br_to_image":
            from core.brotli_tool import decompress_from_br
            from core.task_queue import TaskItem

            logical_br_path = full
            branch_br_path = item.data(0, Qt.UserRole + 20)
            br_path  = branch_br_path or full
            rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_br_path)
            if nb and active and not branch_br_path:
                br_path, _, _ = self._nb_resolve_write_path(logical_br_path)
                os.makedirs(os.path.dirname(br_path), exist_ok=True)
                shutil.copy2(logical_br_path, br_path)
            main     = self.window()
            task_id  = f"unpack-br:{br_path}"

            def worker():
                return decompress_from_br(br_path, keep_source=False)

            def after(ok, msg_or_path):
                if main:
                    main.pop_task(task_id, ok)
                if not ok:
                    if main:
                        main.set_loading_failed(t("rc_unpack_failed"))
                    _AppDialog.error(self, t("rc_unpack_failed"), msg_or_path)
                    return
                logical_out = logical_br_path[:-3]
                self._nb_record_output_tree(logical_out, msg_or_path)
                if nb and active:
                    nb.record_delete(fs_root, active["name"], rel)
                # Refresh parent folder so new .img appears
                parent_item = self.find_item_by_path(
                    self.topLevelItem(0), os.path.dirname(logical_br_path)
                )
                if parent_item:
                    self.refresh_folder(parent_item)
                if main:
                    main.maybe_finish_loading()

            task = TaskItem(task_id=task_id, worker=worker, callback=after)
            if main:
                main.push_task(task_id)
                main.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "unpack_br_to_folder":
            from core.brotli_tool import decompress_from_br
            from core.task_queue import TaskItem

            logical_br_path = full
            branch_br_path = item.data(0, Qt.UserRole + 20)
            br_path  = branch_br_path or full
            rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_br_path)
            if nb and active and not branch_br_path:
                br_path, _, _ = self._nb_resolve_write_path(logical_br_path)
                os.makedirs(os.path.dirname(br_path), exist_ok=True)
                shutil.copy2(logical_br_path, br_path)
            main     = self.window()
            task_id  = f"unpack-br-folder:{br_path}"

            def worker():
                # Step 1: decompress .br → .img (keep .br so user can re-use)
                ok, img_path = decompress_from_br(br_path, keep_source=True)
                if not ok:
                    return False, img_path

                # Step 2: detect FS and unpack to folder
                from core.fsdetect import detect_fs_type
                fs = detect_fs_type(img_path)
                if fs in ("EROFS",):
                    from core.erofs import unpack_erofs
                    return unpack_erofs(img_path)
                elif fs in ("EXT2", "EXT3", "EXT4"):
                    from core.unpack_ext4 import main as unpack_ext4
                    return unpack_ext4(img_path)
                else:
                    return False, f"Unsupported FS type for unpack-to-folder: {fs!r}"

            def after(ok, msg_or_path):
                if main:
                    main.pop_task(task_id, ok)
                if not ok:
                    if main:
                        main.set_loading_failed(t("rc_unpack_failed"))
                    _AppDialog.error(self, t("rc_unpack_failed"), msg_or_path)
                    return
                logical_img = logical_br_path[:-3]
                phys_img = br_path[:-3]
                self._nb_record_output_tree(logical_img, phys_img)
                logical_folder = os.path.splitext(logical_img)[0]
                phys_folder = os.path.splitext(phys_img)[0]
                if os.path.isdir(phys_folder):
                    self._nb_record_output_tree(logical_folder, phys_folder)
                parent_item = self.find_item_by_path(
                    self.topLevelItem(0), os.path.dirname(logical_br_path)
                )
                if parent_item:
                    self.refresh_folder(parent_item)
                if main:
                    main.maybe_finish_loading()

            task = TaskItem(task_id=task_id, worker=worker, callback=after)
            if main:
                main.push_task(task_id)
                main.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_erofs_to_image":
            # Delegate to the existing erofs repack path
            action = "repack_erofs"
            # Re-enter the handler by recursing into the elif chain
            # is not possible inline — just replicate the enqueue call
            from core.erofs import repack_erofs as _repack_erofs_fn
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack:{full}"
            _nb_st    = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_st["mgr"] = _nb_mgr
                _nb_st["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_st["overlaid"]    = True
                        _nb_st["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_st["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_st["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_st["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_st["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_st["branch_name"])
                    except Exception as e:
                        if _nb_st["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, msg = _repack_erofs_fn(full)
                if not ok and _nb_st["overlaid"]:
                    try: _nb_mgr.restore_after_repack(_fs_root)
                    except Exception as _ex: print(f"[NeoBranch] restore on fail: {_ex}")
                    _nb_st["overlaid"] = False
                return ok, msg

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                self.window().hide_erofs_loading()
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                self.on_repack_finished(msg if isinstance(msg, str) else full + ".img")
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_st)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_erofs_to_brotli":
            from core.erofs import repack_erofs as _repack_erofs_br
            from core.brotli_tool import compress_to_br
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-br:{full}"
            _nb_br    = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_br["mgr"] = _nb_mgr
                _nb_br["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_br["overlaid"]    = True
                        _nb_br["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_br["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_br["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_br["branch_name"])
                    except Exception as e:
                        if _nb_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                # Step 1: repack → .img
                ok, img_out = _repack_erofs_br(full)
                if not ok:
                    if _nb_br["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except Exception as _ex: print(f"[NeoBranch] restore: {_ex}")
                        _nb_br["overlaid"] = False
                    return False, img_out
                print(f"[BROTLI_DEBUG] Raw img_out from EROFS repacker: {img_out!r}")

                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()

                print(f"[BROTLI_DEBUG] Cleaned img_out to compress: {img_out!r}")

                # Step 2: compress .img → .img.br
                ok_br, br_out = compress_to_br(img_out, keep_source=False)
                if not ok_br:
                    if _nb_br["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except Exception as _ex: print(f"[NeoBranch] restore: {_ex}")
                        _nb_br["overlaid"] = False
                    return False, br_out
                return True, br_out

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                self.window().hide_erofs_loading()
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_br)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_ext_to_image":
            from core.repack_ext4 import main as _repack_ext_fn
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack:{full}"
            _nb_ext   = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_ext["mgr"] = _nb_mgr
                _nb_ext["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_ext["overlaid"]    = True
                        _nb_ext["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_ext["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_ext["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_ext["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_ext["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_ext["branch_name"])
                    except Exception as e:
                        if _nb_ext["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, msg = _repack_ext_fn(full)
                if not ok and _nb_ext["overlaid"]:
                    try: _nb_mgr.restore_after_repack(_fs_root)
                    except Exception as _ex: print(f"[NeoBranch] restore on fail: {_ex}")
                    _nb_ext["overlaid"] = False
                return ok, msg

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                self.on_repack_finished(full)
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_ext)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_ext_to_brotli":
            from core.repack_ext4 import main as _repack_ext_br_fn
            from core.brotli_tool import compress_to_br
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-br:{full}"
            _nb_ext_br = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_ext_br["mgr"] = _nb_mgr
                _nb_ext_br["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_ext_br["overlaid"]    = True
                        _nb_ext_br["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_ext_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_ext_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_ext_br["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_ext_br["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_ext_br["branch_name"])
                    except Exception as e:
                        if _nb_ext_br["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                # Step 1: repack → .img
                ok, img_out = _repack_ext_br_fn(full)
                if not ok:
                    if _nb_ext_br["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except Exception as _ex: print(f"[NeoBranch] restore: {_ex}")
                        _nb_ext_br["overlaid"] = False
                    return False, img_out
                print(f"[BROTLI_DEBUG] Raw img_out from EXT4 repacker: {img_out!r}")

                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()

                print(f"[BROTLI_DEBUG] Cleaned img_out to compress: {img_out!r}")

                # Step 2: compress .img → .img.br
                ok_br, br_out = compress_to_br(img_out, keep_source=False)
                if not ok_br:
                    if _nb_ext_br["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except Exception as _ex: print(f"[NeoBranch] restore: {_ex}")
                        _nb_ext_br["overlaid"] = False
                    return False, br_out
                return True, br_out

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_ext_br)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_erofs_to_new_dat":
            from core.erofs import repack_erofs as _repack_erofs_dat
            from core.img2simg import img2simg as _img2simg_dat
            from core.img2sdat import img2sdat as _img2sdat_mod
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-dat:{full}"
            _nb_dat   = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_dat["mgr"] = _nb_mgr
                _nb_dat["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_dat["overlaid"]    = True
                        _nb_dat["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_dat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_dat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_dat["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_dat["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_dat["branch_name"])
                    except Exception as e:
                        if _nb_dat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, img_out = _repack_erofs_dat(full)
                if not ok:
                    if _nb_dat["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_dat["overlaid"] = False
                    return False, img_out
                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()
                sparse_out = img_out + ".sparse"
                ok_s, sparse_path = _img2simg_dat(img_out, sparse_out)
                if not ok_s:
                    if _nb_dat["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_dat["overlaid"] = False
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(img_out)[0]
                out_dir = os.path.dirname(img_out)
                _img2sdat_mod.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                try: os.remove(img_out)
                except: pass
                return True, prefix + ".new.dat"

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                self.window().hide_erofs_loading()
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_dat)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_erofs_to_dat_br":
            from core.erofs import repack_erofs as _repack_erofs_dbr
            from core.img2simg import img2simg as _img2simg_dbr
            from core.img2sdat import img2sdat as _img2sdat_dbr_mod
            from core.brotli_tool import compress_to_br as _compress_dbr
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-datbr:{full}"
            _nb_dbr   = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_dbr["mgr"] = _nb_mgr
                _nb_dbr["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_dbr["overlaid"]    = True
                        _nb_dbr["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_dbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_dbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_dbr["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_dbr["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_dbr["branch_name"])
                    except Exception as e:
                        if _nb_dbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, img_out = _repack_erofs_dbr(full)
                if not ok:
                    if _nb_dbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_dbr["overlaid"] = False
                    return False, img_out
                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()
                sparse_out = img_out + ".sparse"
                ok_s, sparse_path = _img2simg_dbr(img_out, sparse_out)
                if not ok_s:
                    if _nb_dbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_dbr["overlaid"] = False
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(img_out)[0]
                out_dir = os.path.dirname(img_out)
                _img2sdat_dbr_mod.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                try: os.remove(img_out)
                except: pass
                dat_path = prefix + ".new.dat"
                ok_br, br_out = _compress_dbr(dat_path, keep_source=False)
                if not ok_br:
                    if _nb_dbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_dbr["overlaid"] = False
                    return False, f"brotli compress failed: {br_out}"
                return True, br_out

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                self.window().hide_erofs_loading()
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_dbr)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_ext_to_new_dat":
            from core.repack_ext4 import main as _repack_ext_dat_fn
            from core.img2simg import img2simg as _img2simg_ext_dat
            from core.img2sdat import img2sdat as _img2sdat_ext_mod
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-dat:{full}"
            _nb_edat  = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_edat["mgr"] = _nb_mgr
                _nb_edat["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_edat["overlaid"]    = True
                        _nb_edat["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_edat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_edat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_edat["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_edat["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_edat["branch_name"])
                    except Exception as e:
                        if _nb_edat["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, img_out = _repack_ext_dat_fn(full)
                if not ok:
                    if _nb_edat["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_edat["overlaid"] = False
                    return False, img_out
                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()
                sparse_out = img_out + ".sparse"
                ok_s, sparse_path = _img2simg_ext_dat(img_out, sparse_out)
                if not ok_s:
                    if _nb_edat["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_edat["overlaid"] = False
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(img_out)[0]
                out_dir = os.path.dirname(img_out)
                _img2sdat_ext_mod.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                try: os.remove(img_out)
                except: pass
                return True, prefix + ".new.dat"

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_edat)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "repack_ext_to_dat_br":
            from core.repack_ext4 import main as _repack_ext_dbr_fn
            from core.img2simg import img2simg as _img2simg_ext_dbr
            from core.img2sdat import img2sdat as _img2sdat_ext_dbr_mod
            from core.brotli_tool import compress_to_br as _compress_ext_dbr
            from core.task_queue import TaskItem

            base_dir  = os.path.dirname(full)
            task_id   = f"repack-datbr:{full}"
            _nb_edbr  = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

            def worker():
                from core import fs_verify
                from main_ext.class_NeoBranchManager import NeoBranchManager
                engine   = ConfigEngine(base_dir)
                verifier = fs_verify.FSVerifier(base_dir)
                _fs_root   = os.path.basename(full)
                _nb_mgr    = NeoBranchManager(base_dir)
                _nb_active = _nb_mgr.get_active_branch(_fs_root)
                _nb_edbr["mgr"] = _nb_mgr
                _nb_edbr["fs_root"] = _fs_root
                if _nb_active:
                    try:
                        _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                        _nb_edbr["overlaid"]    = True
                        _nb_edbr["branch_name"] = _nb_active["name"]
                    except Exception as _e:
                        return False, f"NeoBranch overlay failed: {_e}"
                ok, msg = verifier.verify_root(_fs_root)
                if not ok:
                    if _nb_edbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, msg
                try:
                    engine.apply_perm_on_repack(_fs_root)
                except Exception as e:
                    if _nb_edbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"FS_CONFIG error: {e}"
                from core.repack_policy import get_selinux_mode
                selinux_mode = get_selinux_mode()
                if selinux_mode != "none":
                    try:
                        engine.apply_predicted_context(_fs_root, mode=selinux_mode)
                        if _nb_edbr["overlaid"]:
                            _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_edbr["branch_name"])
                            _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_edbr["branch_name"])
                    except Exception as e:
                        if _nb_edbr["overlaid"]: _nb_mgr.restore_after_repack(_fs_root)
                        return False, f"SELINUX context error: {e}"
                ok, img_out = _repack_ext_dbr_fn(full)
                if not ok:
                    if _nb_edbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_edbr["overlaid"] = False
                    return False, img_out
                if "→" in img_out:
                    img_out = img_out.split("→", 1)[1].strip()
                sparse_out = img_out + ".sparse"
                ok_s, sparse_path = _img2simg_ext_dbr(img_out, sparse_out)
                if not ok_s:
                    if _nb_edbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_edbr["overlaid"] = False
                    return False, f"img2simg failed: {sparse_path}"
                prefix = os.path.splitext(img_out)[0]
                out_dir = os.path.dirname(img_out)
                _img2sdat_ext_dbr_mod.main(sparse_path, out_dir, VERSION=4, PREFIX=os.path.basename(prefix))
                try: os.remove(sparse_path)
                except: pass
                try: os.remove(img_out)
                except: pass
                dat_path = prefix + ".new.dat"
                ok_br, br_out = _compress_ext_dbr(dat_path, keep_source=False)
                if not ok_br:
                    if _nb_edbr["overlaid"]:
                        try: _nb_mgr.restore_after_repack(_fs_root)
                        except: pass
                        _nb_edbr["overlaid"] = False
                    return False, f"brotli compress failed: {br_out}"
                return True, br_out

            def after(ok, msg):
                self.window().pop_task(task_id, ok)
                if not ok:
                    self.window().set_loading_failed(t("rc_repack_failed"))
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_repack_failed"), msg)
                    return
                parent_dir = os.path.dirname(full)
                tree = self.window().active_tree
                parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
                if parent_item:
                    tree.refresh_folder(parent_item)
                self.window().maybe_finish_loading()

            _main_w = self.window()
            worker = _restore_neobranch_in_worker(worker, _nb_edbr)
            task = TaskItem(task_id=task_id, worker=worker, callback=after, halt_if=_halt_on_neobranch_restore_failure)
            if _main_w:
                _main_w.push_task(task_id)
                _main_w.task_queue.enqueue(task)
            else:
                run_in_thread(worker, None, callback=after)


        elif action == "unpack_dat_br_to_dat":
            from core.brotli_tool import decompress_from_br
            task_id = f"unpack-datbr-dat:{full}"
            _main_w = self.window()
            if _main_w: _main_w.push_task(task_id)

            def worker():
                ok, result = decompress_from_br(full, keep_source=True)
                return ok, result

            def after(ok, msg):
                if _main_w: _main_w.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_unpack_failed"), msg)
                    if _main_w: _main_w.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(full))
                if parent_item: self.refresh_folder(parent_item)
                if _main_w: _main_w.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_dat_br_to_image":
            from core.brotli_tool import decompress_from_br
            from core.sdat2img import sdat2img as _sdat2img_mod
            task_id = f"unpack-datbr-img:{full}"
            _main_w = self.window()
            if _main_w: _main_w.push_task(task_id)

            def worker():
                ok, dat_path = decompress_from_br(full, keep_source=True)
                if not ok:
                    return False, dat_path
                low = full.lower()
                prefix = full[:-len(".new.dat.br")]
                tl = prefix + ".transfer.list"
                img_out = prefix + ".img"
                if not os.path.isfile(tl):
                    return False, f"transfer.list not found: {tl}"
                try:
                    _sdat2img_mod.main(tl, dat_path, img_out)
                except Exception as e:
                    return False, f"sdat2img failed: {e}"
                try: os.remove(dat_path)
                except: pass
                return True, img_out

            def after(ok, msg):
                if _main_w: _main_w.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_unpack_failed"), msg)
                    if _main_w: _main_w.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(full))
                if parent_item: self.refresh_folder(parent_item)
                if _main_w: _main_w.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_dat_br_to_folder":
            from core.brotli_tool import decompress_from_br
            from core.sdat2img import sdat2img as _sdat2img_fld_mod
            from core.header_magic import check_magic as _check_magic_fld
            task_id = f"unpack-datbr-fld:{full}"
            _main_w = self.window()
            if _main_w: _main_w.push_task(task_id)

            def worker():
                ok, dat_path = decompress_from_br(full, keep_source=True)
                if not ok:
                    return False, dat_path
                prefix = full[:-len(".new.dat.br")]
                tl = prefix + ".transfer.list"
                img_out = prefix + ".img"
                if not os.path.isfile(tl):
                    return False, f"transfer.list not found: {tl}"
                try:
                    _sdat2img_fld_mod.main(tl, dat_path, img_out)
                except Exception as e:
                    return False, f"sdat2img failed: {e}"
                try: os.remove(dat_path)
                except: pass
                fs = _check_magic_fld(img_out)
                if fs == "EROFS":
                    from core.erofs import unpack_erofs
                    ok2, msg2 = unpack_erofs(img_out, os.path.dirname(full))
                    try: os.remove(img_out)
                    except: pass
                    return ok2, msg2
                elif fs in ("EXT4", "EXT3", "EXT2"):
                    from core.unpack_ext4 import ext4_x as extract_ext4
                    ok2, msg2 = extract_ext4(img_out, os.path.dirname(full))
                    try: os.remove(img_out)
                    except: pass
                    return ok2, msg2
                return False, f"Unknown filesystem in image: {fs or 'unrecognized'}"

            def after(ok, msg):
                if _main_w: _main_w.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_unpack_failed"), msg)
                    if _main_w: _main_w.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(full))
                if parent_item: self.refresh_folder(parent_item)
                if _main_w: _main_w.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_dat_to_image":
            from core.sdat2img import sdat2img as _sdat2img_dat_mod
            task_id = f"unpack-dat-img:{full}"
            _main_w = self.window()
            if _main_w: _main_w.push_task(task_id)

            def worker():
                prefix = full[:-len(".new.dat")]
                tl = prefix + ".transfer.list"
                img_out = prefix + ".img"
                if not os.path.isfile(tl):
                    return False, f"transfer.list not found: {tl}"
                try:
                    _sdat2img_dat_mod.main(tl, full, img_out)
                except Exception as e:
                    return False, f"sdat2img failed: {e}"
                return True, img_out

            def after(ok, msg):
                if _main_w: _main_w.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_unpack_failed"), msg)
                    if _main_w: _main_w.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(full))
                if parent_item: self.refresh_folder(parent_item)
                if _main_w: _main_w.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


        elif action == "unpack_dat_to_folder":
            from core.sdat2img import sdat2img as _sdat2img_dat_fld_mod
            from core.header_magic import check_magic as _check_magic_dat_fld
            task_id = f"unpack-dat-fld:{full}"
            _main_w = self.window()
            if _main_w: _main_w.push_task(task_id)

            def worker():
                prefix = full[:-len(".new.dat")]
                tl = prefix + ".transfer.list"
                img_out = prefix + ".img"
                if not os.path.isfile(tl):
                    return False, f"transfer.list not found: {tl}"
                try:
                    _sdat2img_dat_fld_mod.main(tl, full, img_out)
                except Exception as e:
                    return False, f"sdat2img failed: {e}"
                fs = _check_magic_dat_fld(img_out)
                if fs == "EROFS":
                    from core.erofs import unpack_erofs
                    ok2, msg2 = unpack_erofs(img_out, os.path.dirname(full))
                    try: os.remove(img_out)
                    except: pass
                    return ok2, msg2
                elif fs in ("EXT4", "EXT3", "EXT2"):
                    from core.unpack_ext4 import ext4_x as extract_ext4
                    ok2, msg2 = extract_ext4(img_out, os.path.dirname(full))
                    try: os.remove(img_out)
                    except: pass
                    return ok2, msg2
                return False, f"Unknown filesystem in image: {fs or 'unrecognized'}"

            def after(ok, msg):
                if _main_w: _main_w.pop_task(task_id, ok)
                if not ok:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.error(self.window(), t("rc_unpack_failed"), msg)
                    if _main_w: _main_w.set_loading_failed(t("rc_unpack_failed"))
                    return
                parent_item = self.find_item_by_path(self.topLevelItem(0), os.path.dirname(full))
                if parent_item: self.refresh_folder(parent_item)
                if _main_w: _main_w.maybe_finish_loading()

            run_in_thread(worker, None, callback=after)


    # Action yang valid untuk single-target CLI. Sumber kebenaran = handler di
    # _dispatch_rc_action (jangan tambah di sini tanpa handler-nya).
    RC_CLI_ACTIONS = (
        # smart auto-detect actions
        "unpack", "repack",
        # basic
        "rename", "rename_force", "delete", "duplicate", "new_file", "new_folder",
        "copy", "cut", "paste",
        "create_symlink", "open_in_explorer",
        # mount
        "mount", "unmount", "usage",
        # selinux / branch / devicetree
        "neocat", "neobranch", "gen_devicetree",
        # fs convert (folder)
        "convert_fs", "make_filesystem",
        # image convert (file)
        "convert_sparse", "convert_to_sparse",
        "convert_img_to_br", "convert_img_to_new_dat", "convert_img_to_dat_br",
        # super / sparse / payload
        "open_super", "unpack_super", "unpack_sparse", "unpack_payload",
        # boot
        "unpack_boot", "repack_boot",
        # erofs / ext
        "unpack_erofs", "unpack_ext",
        "repack_erofs", "repack_ext",
        "repack_erofs_to_image", "repack_erofs_to_brotli",
        "repack_erofs_to_new_dat", "repack_erofs_to_dat_br",
        "repack_ext_to_image", "repack_ext_to_brotli",
        "repack_ext_to_new_dat", "repack_ext_to_dat_br",
        # brotli / dat
        "unpack_br_to_image", "unpack_br_to_folder",
        "unpack_dat_br_to_dat", "unpack_dat_br_to_image", "unpack_dat_br_to_folder",
        "unpack_dat_to_image", "unpack_dat_to_folder",
        # archive
        "extract_here", "extract_to",
        # apktool
        "apktool_decompile", "apktool_build", "apktool_build_sign", "apktool_build_keeporig",
        "apktool_sign", "apktool_unsign", "apktool_switch_mode",
    )

    # Action batch (multi-target) -> handler di _batch_enqueue/_batch_delete.
    RC_CLI_BATCH_ACTIONS = (
        "unpack", "repack",
        "delete", "copy", "cut", "unpack_erofs", "unpack_ext",
        "convert_sparse", "repack_erofs", "repack_ext",
    )

    def rc_cli_resolve_item(self, path, *, materialize=True):

        if not path:
            return None
        path = os.path.normpath(path)
        root = self.topLevelItem(0)
        if root is None:
            return None

        found = self.find_item_by_path(root, path)
        if found is not None or not materialize:
            return found

        base = getattr(self, "base", None)
        norm_path = os.path.normcase(path)
        norm_base = os.path.normcase(os.path.normpath(base)) if base else ""
        if not base or not (norm_path == norm_base or norm_path.startswith(norm_base + os.sep)):
            return found  # di luar project root — tidak bisa di-materialize

        # Expand bertahap dari root ke target supaya on_expand membangun anak.
        try:
            rel = os.path.relpath(path, base)
        except ValueError:
            return found
        if rel in (".", ""):
            return root

        parts = rel.replace("\\", "/").split("/")
        from core.rak_neobranch_view import ActiveProjectView
        logical_view = ActiveProjectView(base)
        cur = root
        acc = os.path.normpath(base)
        # root item mewakili base; pastikan ter-expand
        try:
            cur.setExpanded(True)
            self.on_expand(cur)
        except Exception:
            pass
        for part in parts:
            acc = os.path.normpath(os.path.join(acc, part))
            nxt = self.find_item_by_path(root, acc)
            if nxt is None:


                try:
                    physical, _ = logical_view.resolve(acc)
                    if physical and os.path.exists(acc):
                        self.refresh_folder(cur)
                    else:
                        cur.setExpanded(True)
                        self.on_expand(cur)
                except Exception:
                    pass
                nxt = self.find_item_by_path(root, acc)
            if nxt is None:
                return None
            cur = nxt
            physical, _ = logical_view.resolve(acc)
            if physical and os.path.isdir(physical):
                try:
                    cur.setExpanded(True)
                    self.on_expand(cur)
                except Exception:
                    pass
        return self.find_item_by_path(root, path)

    def _resolve_smart_unpack(self, full, is_file, is_dir, fs_badge=None):

        if is_dir:
            return None, f"'{os.path.basename(full)}' adalah folder. Gunakan 'repack' untuk membungkus folder."

        magic = fs_badge
        if not magic:
            try:
                from core.header_magic import check_magic
                magic = check_magic(full)
            except Exception:
                magic = ""

        if magic == "EROFS":
            return "unpack_erofs", None
        elif magic in ("EXT4", "EXT3", "EXT2"):
            return "unpack_ext", None
        elif magic == "SUPER":
            return "unpack_super", None
        elif magic == "SPARSE":
            return "unpack_sparse", None
        elif magic in ("BOOT", "VNDRBOOT"):
            return "unpack_boot", None
        elif magic == "PAYLOAD":
            return "unpack_payload", None

        lower = full.lower()
        if lower.endswith(".dat.br"):
            return "unpack_dat_br_to_folder", None
        elif lower.endswith((".new.dat", ".dat")):
            return "unpack_dat_to_folder", None
        elif lower.endswith(".br"):
            return "unpack_br_to_folder", None
        elif lower.endswith(".apk"):
            return "apktool_decompile", None
        elif lower.endswith((".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz")):
            return "extract_to", None


        return None, f"Format tidak dikenali untuk unpack: {os.path.basename(full)} (magic={magic or 'unknown'})"

    def _resolve_smart_repack(self, full, is_file, is_dir):

        if is_file:
            try:
                from core.header_magic import check_magic
                if check_magic(full) in ("BOOT", "VNDRBOOT"):
                    return "repack_boot", None
            except Exception:
                pass
            return None, f"'{os.path.basename(full)}' adalah file. Gunakan 'unpack' untuk mengekstrak file."

        folder_name = os.path.basename(os.path.normpath(full))
        base_dir = getattr(self, "base", os.path.dirname(full))

        try:
            from core.fsdetect import detect_fs_root
            fs = detect_fs_root(base_dir, folder_name)
        except Exception:
            fs = ""

        if fs == "EROFS":
            return "repack_erofs_to_image", None
        elif fs in ("EXT4", "EXT3", "EXT2"):
            return "repack_ext_to_image", None
        elif fs in ("BOOT", "VNDRBOOT"):
            return "repack_boot", None

        if os.path.isfile(os.path.join(full, "apktool.yml")):
            return "apktool_build", None

        cfg_dir = os.path.join(base_dir, "config")
        if os.path.isfile(os.path.join(cfg_dir, f"{folder_name}_fs_options")):
            return "repack_erofs_to_image", None
        if os.path.isfile(os.path.join(cfg_dir, f"{folder_name}_info")):
            return "repack_ext_to_image", None
        if os.path.isfile(os.path.join(cfg_dir, f"{folder_name}_bootconf")):
            return "repack_boot", None

        if os.path.exists(os.path.join(full, "build.prop")) or os.path.exists(os.path.join(full, "default.prop")):
            return "repack_erofs_to_image", None

        return None, f"Format partisi tidak terdeteksi untuk repack folder: {folder_name}"

    def rc_cli(self, action, path, *, confirm=False, name=None):

        if action not in self.RC_CLI_ACTIONS:
            return False, f"unknown action: {action!r}"

        path = os.path.normpath(path)
        from core.rak_neobranch_view import ActiveProjectView, source_image_branch
        source_branch = source_image_branch(path)
        if source_branch:
            return False, (f"source image belongs to active NeoBranch '{source_branch}'; "
                           "use the unpacked partition tree")
        logical_view = ActiveProjectView(self.base)
        rel = os.path.relpath(path, self.base)
        fs_root = rel.split(os.sep, 1)[0]
        branch = logical_view._branch(fs_root)[0] if rel != "." else None
        safe_actions = {"rename", "rename_force", "delete", "new_file", "new_folder", "copy", "cut", "paste"}
        repack_actions = {"repack", "repack_erofs", "repack_ext",
                          "repack_erofs_to_image", "repack_erofs_to_brotli",
                          "repack_erofs_to_new_dat", "repack_erofs_to_dat_br",
                          "repack_ext_to_image", "repack_ext_to_brotli",
                          "repack_ext_to_new_dat", "repack_ext_to_dat_br"}
        if branch and action not in safe_actions and not (rel == fs_root and action in repack_actions):
            return False, (f"RAK CLI action '{action}' does not yet use the active NeoBranch "
                           f"'{branch}' safely; use 'rak fs' or a branch-aware repack command")
        item = self.rc_cli_resolve_item(path)
        if item is None:
            return False, f"could not resolve tree item for: {path}"

        full = item.data(0, Qt.UserRole) or path
        branch_abs = item.data(0, Qt.UserRole + 20)
        physical = branch_abs if branch_abs and os.path.exists(branch_abs) else full
        visible, _ = logical_view.resolve(path)
        if not visible:
            return False, f"path not found in active NeoBranch view: {path}"
        if (os.path.normcase(os.path.realpath(physical)) != os.path.normcase(os.path.realpath(visible))
                and not (os.path.isdir(physical) and os.path.isdir(visible))):
            return False, f"tree item is stale for active NeoBranch: {path}; run rak refresh"
        if not os.path.exists(physical):
            return False, f"path not found: {path}"
        is_file = os.path.isfile(physical)
        is_dir = os.path.isdir(physical)
        fs_badge = item.data(0, Qt.UserRole + 1)

        try:
            # ── Auto-resolusi unpack & repack (Smart Dispatch) ──
            if action == "unpack":
                resolved_action, err = self._resolve_smart_unpack(full, is_file, is_dir, fs_badge)
                if not resolved_action:
                    return False, err
                action = resolved_action

            if action == "repack":
                resolved_action, err = self._resolve_smart_repack(full, is_file, is_dir)
                if not resolved_action:
                    return False, err
                action = resolved_action

            # ── Aksi yang butuh penanganan-CLI khusus (tanpa dialog/editor) ──
            if action == "delete":
                # UI: delete_item() munculin konfirmasi. Di CLI default skip.
                if confirm:
                    self.delete_item(item)
                else:
                    self._enqueue_delete_task(item, skip_confirm=True)
                return True, f"dispatched delete on {os.path.basename(full)}"

            if action in ("new_file", "new_folder", "rename", "rename_force"):
                ok, msg = self._rc_cli_named_action(action, item, full, is_dir, name)
                return ok, msg

            # ── Sisanya: jalur popup persis ──
            self._dispatch_rc_action(action, item, full, is_file, is_dir, fs_badge)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"dispatch error: {e}"
        return True, f"dispatched {action} on {os.path.basename(full)}"

    def _rc_cli_named_action_legacy_unsafe(self, action, item, full, is_dir, name):

        if not name:
            return False, f"{action} butuh parameter name (mis. 'new_file <dir> name=foo.txt')"
        if any(c in name for c in '\\/:*?"<>|'):
            return False, f"nama tidak valid: {name!r}"

        if action == "rename":
            new_path = os.path.join(os.path.dirname(full), name)
            if os.path.exists(new_path):
                return False, f"target sudah ada: {name}"
            os.rename(full, new_path)
            try:
                item.setData(0, Qt.UserRole, new_path)
                item.setText(0, name)
            except Exception:
                pass
            parent = item.parent() or self.topLevelItem(0)
            try:
                self.refresh_folder(parent)
            except Exception:
                pass
            return True, f"renamed -> {name}"

        # new_file / new_folder
        target_dir = full if is_dir else os.path.dirname(full)
        parent_item = item if is_dir else (item.parent() or self.topLevelItem(0))
        new_path = os.path.join(target_dir, name)
        if os.path.exists(new_path):
            return False, f"sudah ada: {name}"
        if action == "new_folder":
            os.makedirs(new_path, exist_ok=False)
        else:
            with open(new_path, "w", encoding="utf-8"):
                pass
        try:
            self.refresh_folder(parent_item)
        except Exception:
            pass
        return True, f"created {os.path.basename(new_path)}"


    @staticmethod
    def _rc_cli_validate_name(action, name):
        if not isinstance(name, str) or not name.strip():
            return False, f"{action} butuh parameter name"
        name = name.strip()
        if name in (".", "..") or os.path.basename(name) != name:
            return False, f"nama harus satu nama file/folder: {name!r}"
        if any(c in name for c in '\\/:*?\"<>|'):
            return False, f"nama tidak valid: {name!r}"
        return True, name

    def _rc_cli_named_action(self, action, item, full, is_dir, name):

        ok, clean_name = self._rc_cli_validate_name(action, name)
        if not ok:
            return False, clean_name
        if action in ("rename", "rename_force"):
            return self._rc_cli_rename(item, full, clean_name, force=action == "rename_force")
        return self._rc_cli_create(action, item, full, is_dir, clean_name)

    def _rc_cli_create(self, action, item, full, is_dir, name):

        branch_abs = item.data(0, Qt.UserRole + 20)
        actual = branch_abs if branch_abs and os.path.exists(branch_abs) else full
        target_is_dir = os.path.isdir(actual) or os.path.isdir(full)
        target_dir = full if target_is_dir else os.path.dirname(full)
        parent_item = item if target_is_dir else (item.parent() or self.topLevelItem(0))
        new_logical = os.path.join(target_dir, name)
        new_physical, _, _ = self._nb_resolve_write_path(new_logical)
        if os.path.exists(new_logical) or os.path.exists(new_physical):
            return False, f"target sudah ada: {name}"
        try:
            os.makedirs(os.path.dirname(new_physical), exist_ok=True)
            if action == "new_folder":
                os.makedirs(new_physical, exist_ok=False)
            else:
                with open(new_physical, "x", encoding="utf-8"):
                    pass
            rel = os.path.relpath(new_logical, self.base).replace("\\", "/")
            raw_ctx_key = self.engine.escaped_ctx_key(rel)
            if raw_ctx_key not in self.ctx and raw_ctx_key not in self.ctx_neocat:
                self._nb_apply(rel)
            self._nb_record_output_tree(new_logical, new_physical)
            self.refresh_folder(parent_item)
            return True, f"created {os.path.basename(new_logical)}"
        except Exception as exc:
            return False, f"create failed: {exc}"

    def rc_cli_create_symlink(self, folder_item, name, target):

        ok, clean_name = self._rc_cli_validate_name("symlink", name)
        if not ok:
            return False, clean_name
        if not isinstance(target, str) or not target or "\x00" in target:
            return False, "symlink target tidak valid"
        logical_dir = folder_item.data(0, Qt.UserRole)
        branch_abs = folder_item.data(0, Qt.UserRole + 20)
        physical_dir = branch_abs if branch_abs and os.path.exists(branch_abs) else logical_dir
        if not logical_dir or not os.path.isdir(physical_dir):
            return False, "symlink destination must be an existing project folder"
        logical_out = os.path.join(logical_dir, clean_name)
        physical_out, _, _ = self._nb_resolve_write_path(logical_out)
        if os.path.exists(logical_out) or os.path.exists(physical_out):
            return False, f"target sudah ada: {clean_name}"
        try:
            from core.make_symlink import make_symlink
            if not make_symlink(physical_out, target, overwrite=False):
                return False, f"gagal membuat symlink: {clean_name}"
            rel = os.path.relpath(logical_out, self.base).replace("\\", "/")
            raw_ctx_key = self.engine.escaped_ctx_key(rel)
            if raw_ctx_key not in self.ctx and raw_ctx_key not in self.ctx_neocat:
                self._nb_apply(rel)
            self._nb_record_output_tree(logical_out, physical_out)
            self.refresh_folder(folder_item)
            return True, f"created symlink {clean_name} -> {target}"
        except Exception as exc:
            return False, f"create symlink failed: {exc}"

    def _rc_cli_rename(self, item, old_logical, name, *, force=False):

        if item.data(0, Qt.UserRole + 50):
            return False, "symlink placeholder tidak dapat di-rename"
        logical_new = os.path.join(os.path.dirname(old_logical), name)
        if os.path.normcase(os.path.normpath(logical_new)) == os.path.normcase(os.path.normpath(old_logical)):
            return True, "rename is a no-op"
        old_branch_abs = item.data(0, Qt.UserRole + 20)
        old_actual = old_branch_abs if old_branch_abs and os.path.exists(old_branch_abs) else old_logical
        if not os.path.exists(old_actual):
            return False, f"path tidak ditemukan: {old_logical}"
        new_physical, _, _ = self._nb_resolve_write_path(logical_new)
        if os.path.exists(logical_new) or os.path.exists(new_physical):
            return False, f"target sudah ada: {name}"
        old_rel, old_root, nb, active = self._nb_active_state_for_logical(old_logical)
        try:
            if nb and active and old_rel:
                # Overlay only: source tree remains unchanged.
                os.makedirs(os.path.dirname(new_physical), exist_ok=True)
                branch_old = old_branch_abs if old_branch_abs and os.path.exists(old_branch_abs) else None
                if branch_old:
                    self._detach_watchers_for(branch_old)
                    rename_err = self._retry_fs_op(lambda: os.rename(branch_old, new_physical))
                    if rename_err is not None and rename_err[0] == "perm" and force:
                        from core.lock_recovery import force_release_managed_tools
                        force_release_managed_tools(only_children=True)
                        rename_err = self._retry_fs_op(
                            lambda: os.rename(branch_old, new_physical), attempts=3)
                    if rename_err is not None:
                        return False, f"rename failed: {rename_err[1]}"
                elif os.path.isdir(old_logical):
                    shutil.copytree(old_logical, new_physical, symlinks=True)
                else:
                    shutil.copy2(old_logical, new_physical)
                new_rel = os.path.relpath(logical_new, self.base).replace("\\", "/")
                branch_cfg = nb._branch_config_dir(old_root, active["name"])
                self._nb_remap_config(branch_cfg, branch_cfg, old_rel, new_rel)
                nb.record_delete(old_root, active["name"], old_rel)
                nb.record_tree_edit(old_root, active["name"], new_rel, new_physical)
                self.reload_config_cache()
            else:
                self._detach_watchers_for(old_actual)
                rename_err = self._retry_fs_op(lambda: os.rename(old_actual, new_physical))
                if rename_err is not None:
                    if rename_err[0] == "perm" and force:
                        from core.lock_recovery import force_release_managed_tools
                        force_release_managed_tools(only_children=True)
                        rename_err = self._retry_fs_op(lambda: os.rename(old_actual, new_physical), attempts=3)
                    if rename_err is not None:
                        return False, f"rename failed: {rename_err[1]}"
                try:
                    from main_ext.class_FilesystemRenamer import FilesystemRenamer
                    renamer = FilesystemRenamer(self.base, self.engine)
                    rel_old = os.path.relpath(old_logical, self.base).replace("\\", "/")
                    rel_new = os.path.relpath(logical_new, self.base).replace("\\", "/")
                    handled = renamer.rename(old_logical, logical_new)
                    if not handled:
                        self.engine.move_paths(rel_old, rel_new)
                    if handled:
                        self.reload_config_cache()
                        self.engine.rebuild_root_map()
                except Exception:
                    # Do not leave a renamed tree behind if metadata remapping fails.
                    try:
                        os.rename(new_physical, old_actual)
                    except Exception:
                        pass
                    raise
            parent = item.parent() or self.topLevelItem(0)
            self.refresh_folder(parent)
            return True, f"renamed -> {name}"
        except Exception as exc:
            return False, f"rename failed: {exc}"

    def rc_cli_batch(self, action, paths):

        if action not in self.RC_CLI_BATCH_ACTIONS:
            return False, f"unknown batch action: {action!r}"

        from core.rak_neobranch_view import ActiveProjectView, source_image_branch
        logical_view = ActiveProjectView(self.base)
        safe_batch = {"delete", "copy", "cut"}
        repack_batch = {"repack", "repack_erofs", "repack_ext"}
        items_info = []
        for p in paths:
            p = os.path.normpath(p)
            source_branch = source_image_branch(p)
            if source_branch:
                return False, (f"source image belongs to active NeoBranch '{source_branch}': {p}")
            rel = os.path.relpath(p, self.base)
            fs_root = rel.split(os.sep, 1)[0]
            branch = logical_view._branch(fs_root)[0] if rel != "." else None
            if branch and action not in safe_batch and not (rel == fs_root and action in repack_batch):
                return False, (f"RAK CLI batch action '{action}' does not yet use the active "
                               f"NeoBranch '{branch}' safely")
            it = self.rc_cli_resolve_item(p)
            if it is None:
                return False, f"could not resolve tree item for: {p}"
            full = it.data(0, Qt.UserRole)
            branch_abs = it.data(0, Qt.UserRole + 20)
            phys_full = branch_abs if branch_abs and os.path.exists(branch_abs) else full
            visible, _ = logical_view.resolve(p)
            if not visible:
                return False, f"path not found in active NeoBranch view: {p}"
            if (os.path.normcase(os.path.realpath(phys_full)) != os.path.normcase(os.path.realpath(visible))
                    and not (os.path.isdir(phys_full) and os.path.isdir(visible))):
                return False, f"tree item is stale for active NeoBranch: {p}; run rak refresh"
            if not os.path.exists(phys_full):
                return False, f"path not found in active NeoBranch view: {p}"
            items_info.append({
                "item": it, "full": full, "phys_full": phys_full,
                "branch_abs": branch_abs, "fs_badge": it.data(0, Qt.UserRole + 1),
                "is_file": os.path.isfile(phys_full), "is_dir": os.path.isdir(phys_full),
            })

        try:
            if action in ("unpack", "repack"):
                for info in items_info:
                    p = info["full"]
                    ok, msg = self.rc_cli(action, p)
                    if not ok:
                        return False, msg
                return True, f"dispatched smart batch {action} on {len(items_info)} item(s)"
            elif action == "delete":
                # batch delete biasanya konfirmasi; di CLI kita skip konfirmasi
                # dgn jalur per-item yang sama (skip_confirm=True).
                for info in items_info:
                    self._enqueue_delete_task(info["item"], skip_confirm=True)
            elif action == "copy":
                self.clip_copy([i["item"] for i in items_info])
            elif action == "cut":
                self.clip_cut([i["item"] for i in items_info])
            else:
                self._batch_enqueue(items_info, action)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"batch dispatch error: {e}"
        return True, f"dispatched batch {action} on {len(items_info)} item(s)"


    def _batch_rightclick(self, selected_items, global_pos):

        from PySide6.QtWidgets import QVBoxLayout as _VL
        from PySide6.QtGui import QColor as _QC
        from app_ext.class_BlurOverlay import BlurCard

        # kumpulkan info semua item
        items_info = []
        for it in selected_items:
            full     = it.data(0, Qt.UserRole)
            branch_abs = it.data(0, Qt.UserRole + 20)
            phys_full = branch_abs if branch_abs and os.path.exists(branch_abs) else full
            fs_badge = it.data(0, Qt.UserRole + 1)
            items_info.append({
                "item": it, "full": full, "phys_full": phys_full,
                "branch_abs": branch_abs, "fs_badge": fs_badge,
                "is_file": os.path.isfile(phys_full), "is_dir": os.path.isdir(phys_full),
            })

        # badge yang ada di selection
        badges    = {i["fs_badge"] for i in items_info}
        all_files = all(i["is_file"] for i in items_info)
        all_dirs  = all(i["is_dir"]  for i in items_info)
        count     = len(items_info)

        popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)

        outer = _VL(popup)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = _QC(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=18.0, tint=_tint,
                        border=_QC(_br, _bg, _bb, int(0.18 * 255)))
        card.setObjectName("popup_card")
        outer.addWidget(card)

        lay = _VL(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        result = [None]

        def _btn(label, enabled=True, danger=False):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(enabled)
            fg   = _th.FG if not danger else _th.RED_LIGHT
            fg_h = _th.FG_BRIGHT if not danger else _th.RED_LIGHTER
            bg_h = _th.accent(0.14) if not danger else _th.red(0.14)
            try:
                _h = fg.lstrip('#')
                fg_dis = f"rgba({int(_h[0:2], 16)}, {int(_h[2:4], 16)}, {int(_h[4:6], 16)}, 128)"
            except Exception:
                fg_dis = _th.FG_DISABLED2
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none; border-radius: 6px;
                    color: {fg if enabled else fg_dis};
                    font-family: 'Poppins','Inter','Segoe UI',sans-serif;
                    font-size: 12px; text-align: left; padding: 0 14px;
                }}
                QPushButton:hover:enabled {{ background: {bg_h}; color: {fg_h}; }}
                QPushButton:pressed:enabled {{ background: {_th.border_color(0.08)}; }}
            """)
            lay.addWidget(b)
            return b

        def _sep():
            d = QWidget(); d.setFixedHeight(1)
            d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
            lay.addWidget(d)

        def _pick(key):
            result[0] = key
            popup.close()

        # header info
        hdr = QPushButton(t("rc_batch_header", n=count))
        hdr.setFixedHeight(28)
        hdr.setEnabled(False)
        hdr.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {_th.FG_MUTED}; font-size: 10px;
                text-align: left; padding: 0 14px;
                font-family: 'Poppins','Inter','Segoe UI',sans-serif;
            }}
        """)
        lay.addWidget(hdr)
        _sep()

        # delete (always available)
        _btn(t("rc_delete"), danger=True).clicked.connect(lambda: _pick("delete"))

        # copy / cut / paste — sama seperti menu single, tapi untuk seluruh selection
        _sep()
        _btn(t("rc_batch_copy", n=count)).clicked.connect(lambda: _pick("copy"))
        _btn(t("rc_batch_cut", n=count)).clicked.connect(lambda: _pick("cut"))
        _can_paste = self.can_paste()
        _b_paste = _btn(t("rc_paste"), enabled=_can_paste)
        if _can_paste:
            _b_paste.clicked.connect(lambda: _pick("paste"))

        # EROFS unpack (semua file dengan badge EROFS)
        erofs_items = [i for i in items_info if i["is_file"] and i["fs_badge"] == "EROFS"]
        if erofs_items:
            _sep()
            _btn(t("rc_batch_unpack_erofs", n=len(erofs_items))).clicked.connect(
                lambda: _pick("unpack_erofs"))

        # EXT unpack
        ext_items = [i for i in items_info
                     if i["is_file"] and i["fs_badge"] in ("EXT2", "EXT3", "EXT4")]
        if ext_items:
            _sep()
            fs_label = "/".join(sorted({i["fs_badge"] for i in ext_items}))
            _btn(t("rc_batch_unpack_ext", fs=fs_label, n=len(ext_items))).clicked.connect(
                lambda: _pick("unpack_ext"))

        # SPARSE convert
        sparse_items = [i for i in items_info if i["is_file"] and i["fs_badge"] == "SPARSE"]
        if sparse_items:
            _sep()
            _btn(t("rc_batch_convert_sparse", n=len(sparse_items))).clicked.connect(
                lambda: _pick("convert_sparse"))

        # EROFS repack (dirs)
        erofs_dirs = [i for i in items_info if i["is_dir"] and i["fs_badge"] == "EROFS"]
        if erofs_dirs:
            _sep()
            _btn(t("rc_batch_repack_erofs", n=len(erofs_dirs))).clicked.connect(
                lambda: _pick("repack_erofs"))

        # EXT repack (dirs)
        ext_dirs = [i for i in items_info
                    if i["is_dir"] and i["fs_badge"] in ("EXT2", "EXT3", "EXT4")]
        if ext_dirs:
            _sep()
            fs_label = "/".join(sorted({i["fs_badge"] for i in ext_dirs}))
            _btn(t("rc_batch_repack_ext", fs=fs_label, n=len(ext_dirs))).clicked.connect(
                lambda: _pick("repack_ext"))

        card.adjustSize()
        popup.adjustSize()
        _root = self.window()
        _clamp_popup_pos(popup, global_pos, _root)
        if _root:
            card.freeze(_root, popup.pos())
        popup.show()

        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()

        action = result[0]
        if not action:
            return

        if action == "delete":
            self._batch_delete(items_info)
        elif action == "copy":
            self.clip_copy([i["item"] for i in items_info])
        elif action == "cut":
            self.clip_cut([i["item"] for i in items_info])
        elif action == "paste":
            self.clip_paste(items_info[0]["item"] if items_info else None)
        elif action == "unpack_erofs":
            self._batch_enqueue(erofs_items, "unpack_erofs")
        elif action == "unpack_ext":
            self._batch_enqueue(ext_items, "unpack_ext")
        elif action == "convert_sparse":
            self._batch_enqueue(sparse_items, "convert_sparse")
        elif action == "repack_erofs":
            self._batch_enqueue(erofs_dirs, "repack_erofs")
        elif action == "repack_ext":
            self._batch_enqueue(ext_dirs, "repack_ext")

    def _batch_delete(self, items_info):

        from main_ext.class_AppDialog import AppDialog
        print(f"[DEL][batch] ENTER | count={len(items_info)} | items={[os.path.basename(i['full']) for i in items_info]}")
        names = ", ".join(os.path.basename(i["full"]) for i in items_info[:3])
        if len(items_info) > 3:
            names += f" +{len(items_info) - 3}"
        if not AppDialog.confirm(
            self.window(), t("rc_delete"), t("rc_batch_delete_msg", names=names)
        ):
            print("[DEL][batch] CANCELLED by user")
            return

        print(f"[DEL][batch] confirmed → enqueue {len(items_info)} item(s) ke ring (skip_confirm=True)")
        for idx, info in enumerate(items_info):
            print(f"[DEL][batch] item {idx+1}/{len(items_info)}: {os.path.basename(info['full'])}")
            try:
                self._enqueue_delete_task(info["item"], skip_confirm=True)
            except Exception as e:
                print(f"[BATCH DELETE] {info['full']}: {e}")
        print("[DEL][batch] DONE enqueuing all items")


    def _run_single_unpack_erofs(self, full, item, on_complete, logical_hint=None):

        from core.erofs import unpack_erofs
        from core.task_queue import TaskItem

        logical_full = logical_hint or full
        branch_input = None
        if item and not logical_hint:
            try:
                logical_full = item.data(0, Qt.UserRole) or logical_full
            except RuntimeError:
                pass
        if item:
            try:
                branch_input = item.data(0, Qt.UserRole + 20)
            except RuntimeError:
                branch_input = None
        rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_full)
        if nb and active and not branch_input:
            full, _, _ = self._nb_resolve_write_path(logical_full)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copy2(logical_full, full)
        img_path = full
        extract_name = os.path.splitext(os.path.basename(img_path))[0]
        extracted_folder = os.path.join(os.path.dirname(img_path), extract_name)
        logical_extracted_folder = os.path.join(os.path.dirname(logical_full), extract_name)

        self.pending_loading = getattr(self, "pending_loading", {})
        self.pending_loading[os.path.normpath(extracted_folder)] = True

        def after(ok, msg):
            if hasattr(self, "current_loading_anim") and self.current_loading_anim:
                self.current_loading_anim.stop_with_finish()
                self.current_loading_anim = None

            if not ok:
                print(f"[BATCH][unpack_erofs] FAILED: {os.path.basename(full)} — {msg}")
                self.resume_watcher(extracted_folder)
                on_complete(False)
                return

            if os.path.isdir(extracted_folder):
                def after_subscan(ok2, payload):
                    if ok2:
                        try:
                            data = json.loads(payload)
                            folders = data.get("folders", {})
                            fscache = data.get("fscache", {})
                            for k, v in folders.items():
                                self.folder_has_child[os.path.normpath(k)] = v
                            for k, v in fscache.items():
                                self.fsdetect_cache[os.path.normpath(k)] = v
                        except:
                            pass

                    if not self._nb_record_output_tree(logical_extracted_folder, extracted_folder):
                        # apply_subtree DIHAPUS: unpacker sudah kasih config lengkap (sumber
                        # kebenaran). Menambal config yg sudah ada = ribuan engine.apply redundan.
                        self.reload_config_cache(with_neocat=False)

                    target_item = self.find_item_by_path(
                        self.topLevelItem(0), logical_extracted_folder
                    )
                    if target_item:
                        name, ctx, perm, _ = self.get_meta(extracted_folder)
                        target_item.setText(1, ctx)
                        target_item.setText(2, perm)

                        root_name = os.path.basename(extracted_folder)
                        fs_type = detect_fs_root(self.base, root_name)
                        if fs_type:
                            target_item.setText(0, root_name)
                            target_item.setIcon(0, self.icon_fs_folder)
                            target_item.setData(0, Qt.UserRole + 1, "EROFS")

                    self.resume_watcher(extracted_folder)

                    parent_item = self.find_item_by_path(
                        self.topLevelItem(0), os.path.dirname(logical_full)
                    )
                    if parent_item:
                        self.refresh_folder(parent_item)

                    self.window().hide_erofs_loading()
                    on_complete(True)

                run_in_thread(prescan_task, extracted_folder,
                              callback=self._guard_unpack_scan(extracted_folder, after_subscan,
                                                               on_failure=on_complete))
            else:
                self.resume_watcher(extracted_folder)
                on_complete(True)

        main = self.window()
        _tree = self

        def _on_promote():
            _tree.pause_watcher(extracted_folder, "EROFS")

        task = TaskItem(
            task_id=f"batch-unpack:{full}",
            worker=lambda: unpack_erofs(full),
            callback=after,
            on_promote=_on_promote,
        )
        if main:
            main.task_queue.enqueue(task)
        else:
            _tree.pause_watcher(extracted_folder, "EROFS")
            run_in_thread(unpack_erofs, full, callback=after)

    def _run_single_unpack_ext(self, full, item, on_complete, logical_hint=None):

        from core.unpack_ext4 import main as unpack_ext
        from core.task_queue import TaskItem

        logical_full = logical_hint or full
        branch_input = None
        if item and not logical_hint:
            try:
                logical_full = item.data(0, Qt.UserRole) or logical_full
            except RuntimeError:
                pass
        if item:
            try:
                branch_input = item.data(0, Qt.UserRole + 20)
            except RuntimeError:
                branch_input = None
        rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_full)
        if nb and active and not branch_input:
            full, _, _ = self._nb_resolve_write_path(logical_full)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copy2(logical_full, full)
        img_path = full
        extract_name = os.path.splitext(os.path.basename(img_path))[0]
        extracted_folder = os.path.join(os.path.dirname(img_path), extract_name)
        logical_extracted_folder = os.path.join(os.path.dirname(logical_full), extract_name)

        self.pending_loading = getattr(self, "pending_loading", {})
        self.pending_loading[os.path.normpath(extracted_folder)] = True

        def after(ok, msg):
            if not ok:
                print(f"[BATCH][unpack_ext] FAILED: {os.path.basename(full)} — {msg}")
                self.resume_watcher(extracted_folder)
                on_complete(False)
                return

            if os.path.isdir(extracted_folder):
                def after_subscan(ok2, payload):
                    if ok2:
                        try:
                            data = json.loads(payload)
                            folders = data.get("folders", {})
                            fscache = data.get("fscache", {})
                            for k, v in folders.items():
                                self.folder_has_child[os.path.normpath(k)] = v
                            for k, v in fscache.items():
                                self.fsdetect_cache[os.path.normpath(k)] = v
                        except:
                            pass

                    if not self._nb_record_output_tree(logical_extracted_folder, extracted_folder):
                        # apply_subtree DIHAPUS: unpacker sudah kasih config lengkap (sumber
                        # kebenaran). Menambal config yg sudah ada = ribuan engine.apply redundan.
                        self.reload_config_cache(with_neocat=False)

                    target_item = self.find_item_by_path(
                        self.topLevelItem(0), logical_extracted_folder
                    )
                    if target_item:
                        name, ctx, perm, _ = self.get_meta(extracted_folder)
                        target_item.setText(1, ctx)
                        target_item.setText(2, perm)

                        root_name = os.path.basename(extracted_folder)
                        fs_type = detect_fs_root(self.base, root_name)
                        if fs_type:
                            target_item.setText(0, root_name)
                            target_item.setIcon(0, self.icon_fs_folder)
                            target_item.setData(0, Qt.UserRole + 1, fs_type)

                    self.resume_watcher(extracted_folder)

                    parent_item = self.find_item_by_path(
                        self.topLevelItem(0), os.path.dirname(logical_full)
                    )
                    if parent_item:
                        self.refresh_folder(parent_item)

                    self.window().hide_erofs_loading()
                    on_complete(True)

                run_in_thread(prescan_task, extracted_folder,
                              callback=self._guard_unpack_scan(extracted_folder, after_subscan,
                                                               on_failure=on_complete))
            else:
                self.resume_watcher(extracted_folder)
                on_complete(True)

        main = self.window()
        _tree = self

        def _on_promote():
            _tree.pause_watcher(extracted_folder, "EXT4")

        task = TaskItem(
            task_id=f"batch-unpack:{full}",
            worker=lambda: unpack_ext(full),
            callback=after,
            on_promote=_on_promote,
        )
        if main:
            main.task_queue.enqueue(task)
        else:
            _tree.pause_watcher(extracted_folder, "EXT4")
            run_in_thread(unpack_ext, full, callback=after)

    def _run_single_repack_erofs(self, full, item, on_complete):

        from core.task_queue import TaskItem

        full = os.path.normpath(full)
        if not os.path.isdir(full):
            print(f"[BATCH][repack_erofs] FS root not found: {full}")
            on_complete(False)
            return

        base = os.path.dirname(full)
        _nb_state = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

        def worker():
            from core import fs_verify
            from core.erofs import repack_erofs
            from main_ext.class_NeoBranchManager import NeoBranchManager

            engine = ConfigEngine(base)
            verifier = fs_verify.FSVerifier(base)

            _fs_root   = os.path.basename(full)
            _nb_mgr    = NeoBranchManager(base)
            _nb_active = _nb_mgr.get_active_branch(_fs_root)
            _nb_state["mgr"]     = _nb_mgr
            _nb_state["fs_root"] = _fs_root

            if _nb_active:
                try:
                    _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                    _nb_state["overlaid"]    = True
                    _nb_state["branch_name"] = _nb_active["name"]
                except Exception as _e:
                    return False, f"NeoBranch overlay failed: {_e}"

            ok, msg = verifier.verify_root(_fs_root)
            if not ok:
                if _nb_state["overlaid"]:
                    _nb_mgr.restore_after_repack(_fs_root)
                return False, msg

            try:
                engine.apply_perm_on_repack(_fs_root)
            except Exception as e:
                if _nb_state["overlaid"]:
                    _nb_mgr.restore_after_repack(_fs_root)
                return False, f"FS_CONFIG error: {e}"

            from core.repack_policy import get_selinux_mode
            selinux_mode = get_selinux_mode()
            _neocat_cache = neocat_context_path(base, _fs_root)
            _has_neocat_cache = os.path.isfile(_neocat_cache) and os.path.getsize(_neocat_cache) > 0
            if selinux_mode != "none" or _has_neocat_cache:
                try:
                    engine.apply_predicted_context(
                        _fs_root,
                        mode=selinux_mode if selinux_mode != "none" else "ai",
                    )
                    if _nb_state["overlaid"]:
                        _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_state["branch_name"])
                        _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_state["branch_name"])
                except Exception as e:
                    if _nb_state["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"SELINUX context error: {e}"

            ok, msg = repack_erofs(full)
            if not ok and _nb_state["overlaid"]:
                try:
                    _nb_mgr.restore_after_repack(_fs_root)
                    _nb_state["overlaid"] = False
                except Exception as _ex:
                    print(f"[NeoBranch] restore on fail: {_ex}")
            elif ok and _nb_state["overlaid"]:
                try:
                    _nb_mgr.restore_after_repack(_fs_root)
                    print("[NeoBranch] source tree restored after repack")
                except Exception as _ex:
                    return False, f"NeoBranch restore failed: {_ex}"
                _nb_state["overlaid"] = False
            return ok, msg

        def after(ok, msg):
            if not ok:
                print(f"[BATCH][repack_erofs] FAILED: {os.path.basename(full)} — {msg}")
                on_complete(False)
                return

            self.on_repack_finished(full)

            parent_dir = os.path.dirname(full)
            tree = self.window().active_tree
            parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
            if parent_item:
                tree.refresh_folder(parent_item)
            on_complete(True)

        main = self.window()
        worker = _restore_neobranch_in_worker(worker, _nb_state)
        task = TaskItem(
            task_id=f"batch-repack:{full}",
            worker=worker,
            callback=after,
            halt_if=_halt_on_neobranch_restore_failure,
        )
        if main:
            main.task_queue.enqueue(task)
        else:
            run_in_thread(worker, None, callback=after)

    def _run_single_repack_ext(self, full, item, on_complete):

        from core.task_queue import TaskItem

        full = os.path.normpath(full)
        if not os.path.isdir(full):
            print(f"[BATCH][repack_ext] FS root not found: {full}")
            on_complete(False)
            return

        base = os.path.dirname(full)
        _nb_state = {"mgr": None, "fs_root": None, "overlaid": False, "branch_name": ""}

        def worker():
            from core import fs_verify
            from core.repack_ext4 import main as repack_ext
            from main_ext.class_NeoBranchManager import NeoBranchManager

            engine = ConfigEngine(base)
            verifier = fs_verify.FSVerifier(base)

            _fs_root   = os.path.basename(full)
            _nb_mgr    = NeoBranchManager(base)
            _nb_active = _nb_mgr.get_active_branch(_fs_root)
            _nb_state["mgr"]     = _nb_mgr
            _nb_state["fs_root"] = _fs_root

            if _nb_active:
                try:
                    _nb_mgr.prepare_for_repack(_fs_root, _nb_active["name"])
                    _nb_state["overlaid"]    = True
                    _nb_state["branch_name"] = _nb_active["name"]
                except Exception as _e:
                    return False, f"NeoBranch overlay failed: {_e}"

            ok, msg = verifier.verify_root(_fs_root)
            if not ok:
                if _nb_state["overlaid"]:
                    _nb_mgr.restore_after_repack(_fs_root)
                return False, msg

            try:
                engine.apply_perm_on_repack(_fs_root)
            except Exception as e:
                if _nb_state["overlaid"]:
                    _nb_mgr.restore_after_repack(_fs_root)
                return False, f"FS_CONFIG error: {e}"

            from core.repack_policy import get_selinux_mode
            selinux_mode = get_selinux_mode()
            _neocat_cache = neocat_context_path(base, _fs_root)
            _has_neocat_cache = os.path.isfile(_neocat_cache) and os.path.getsize(_neocat_cache) > 0
            if selinux_mode != "none" or _has_neocat_cache:
                try:
                    engine.apply_predicted_context(
                        _fs_root,
                        mode=selinux_mode if selinux_mode != "none" else "ai",
                    )
                    if _nb_state["overlaid"]:
                        _nb_mgr.sync_live_file_contexts_to_branch(_fs_root, _nb_state["branch_name"])
                        _nb_mgr.sync_live_neocat_to_branch(_fs_root, _nb_state["branch_name"])
                except Exception as e:
                    if _nb_state["overlaid"]:
                        _nb_mgr.restore_after_repack(_fs_root)
                    return False, f"SELINUX context error: {e}"

            ok, msg = repack_ext(full)
            if not ok and _nb_state["overlaid"]:
                try:
                    _nb_mgr.restore_after_repack(_fs_root)
                    _nb_state["overlaid"] = False
                except Exception as _ex:
                    print(f"[NeoBranch] restore on fail: {_ex}")
            elif ok and _nb_state["overlaid"]:
                try:
                    _nb_mgr.restore_after_repack(_fs_root)
                    print("[NeoBranch] source tree restored after repack")
                except Exception as _ex:
                    return False, f"NeoBranch restore failed: {_ex}"
                _nb_state["overlaid"] = False
            return ok, msg

        def after(ok, msg):
            if not ok:
                print(f"[BATCH][repack_ext] FAILED: {os.path.basename(full)} — {msg}")
                on_complete(False)
                return

            self.on_repack_finished(full)

            parent_dir = os.path.dirname(full)
            tree = self.window().active_tree
            parent_item = tree.find_item_by_path(tree.topLevelItem(0), parent_dir)
            if parent_item:
                tree.refresh_folder(parent_item)
            on_complete(True)

        main = self.window()
        worker = _restore_neobranch_in_worker(worker, _nb_state)
        task = TaskItem(
            task_id=f"batch-repack:{full}",
            worker=worker,
            callback=after,
            halt_if=_halt_on_neobranch_restore_failure,
        )
        if main:
            main.task_queue.enqueue(task)
        else:
            run_in_thread(worker, None, callback=after)

    def _batch_enqueue(self, items_info, action):

        from core.task_queue import TaskItem
        main = self.window()
        if not main:
            return

        # Pastikan manager ready
        main._ensure_loading_manager()
        mgr = main.loading_manager

        # ── convert_sparse: ringan, paralel via TaskQueue tapi report ke manager ──
        if action == "convert_sparse":
            total = len(items_info)
            if total == 0:
                return
            batch_id = mgr.start_batch(action, total)

            from core.super_logic import convert_to_raw
            for info in items_info:
                logical_full = info["full"]
                full = info.get("phys_full") or logical_full
                od   = os.path.dirname(full)

                def _cb(ok, msg, f=full, lf=logical_full, bid=batch_id):
                    if ok:
                        # Invalidate cache otoritatif biar badge re-detect (sama
                        # alasannya dgn jalur single: unsparse overwrite in-place).
                        self.magic_cache.pop(f, None)
                        self.fsdetect_cache.pop(f, None)
                        self.superdetect_cache.pop(f, None)
                        logical_out = os.path.join(os.path.dirname(lf), os.path.basename(f))
                        physical_out = os.path.join(os.path.dirname(f), os.path.basename(f))
                        self._nb_record_output_tree(logical_out, physical_out)
                        p = self.find_item_by_path(
                            self.topLevelItem(0), os.path.dirname(lf)
                        )
                        if p:
                            self.refresh_folder(p)
                    mgr.report_done(bid, bool(ok), member_name=os.path.basename(f.rstrip("/\\")))

                main.task_queue.enqueue(TaskItem(
                    task_id=f"{action}:{full}",
                    worker=(lambda f=full, o=od: convert_to_raw(f, o)),
                    callback=_cb,
                ))
            return

        # ── unpack/repack: chain serial via helper _run_single_* ──
        _runner = {
            "unpack_erofs": self._run_single_unpack_erofs,
            "unpack_ext":   self._run_single_unpack_ext,
            "repack_erofs": self._run_single_repack_erofs,
            "repack_ext":   self._run_single_repack_ext,
        }.get(action)
        if _runner is None:
            return

        # Snapshot queue (selection bisa berubah saat batch jalan)
        queue = list(items_info)
        total = len(queue)
        if total == 0:
            return

        batch_id = mgr.start_batch(action, total)
        state = {"idx": 0}

        def _finalize_batch_view():
            try:
                self.resume_watcher()
            except Exception:
                pass
            try:
                self.reload_config_cache()
            except Exception as e:
                print(f"[BATCH][{action}] final reload_config_cache: {e}")
            try:
                root = self.topLevelItem(0)
                if root and hasattr(self, "_update_ctx_perm_recursive"):
                    self._update_ctx_perm_recursive(root)
                self.viewport().update()
            except Exception as e:
                print(f"[BATCH][{action}] final metadata refresh: {e}")

        def _run_next():
            if state["idx"] >= total:
                _finalize_batch_view()
                return  # manager auto-emit terminal saat last report_done
            info = queue[state["idx"]]
            state["idx"] += 1
            logical_full = info["full"]
            full = info.get("phys_full") or logical_full
            it   = info.get("item")

            # grup mulai jalan → keluar dari hitungan "Service Pending"
            mgr.mark_group_active(batch_id, member_name=os.path.basename(full.rstrip("/\\")))

            def _on_done(ok, f=full, bid=batch_id):
                # member_name → tooltip ••• daftar service pending (basename partisi/file)
                mgr.report_done(bid, bool(ok), member_name=os.path.basename(f.rstrip("/\\")))
                _run_next()

            try:
                if action in ("unpack_erofs", "unpack_ext"):
                    _runner(full, it, _on_done, logical_full)
                else:
                    _runner(full, it, _on_done)
            except Exception as e:
                print(f"[BATCH][{action}] runner crash {os.path.basename(full)}: {e}")
                mgr.report_done(batch_id, False)
                _run_next()

        _run_next()


    def _extract_archive_here(self, item, full, to_subdir=True):
        main = self.window()
        if not main:
            return

        task_name = f"extracting-archive:{os.path.basename(full)}"
        main.push_task(task_name)

        src = item.data(0, Qt.UserRole + 20) if item else None
        if not (src and os.path.exists(src)):
            src = full
        parent_dir = os.path.dirname(full)
        parent_phys, _, _ = self._nb_resolve_write_path(parent_dir)

        def _safe_dir(base, name):

            out = os.path.join(base, name)
            i = 2
            while os.path.exists(out):
                out = os.path.join(base, f"{name} ({i})")
                i += 1
            os.makedirs(out, exist_ok=True)
            return out

        def worker():
            try:
                import traceback
                from core.archive_safety import safe_extract_zip, validate_archive_member_names

                # Safe extract helpers to prevent path traversal (Zip Slip / Tar traversal)
                def _safe_tar_extract(tf, target_dir):
                    target_real = os.path.realpath(target_dir)
                    members = tf.getmembers()
                    for member in members:
                        dest_path = os.path.realpath(os.path.join(target_dir, member.name))
                        if not (dest_path == target_real or dest_path.startswith(target_real + os.sep)):
                            raise ValueError(f"Path traversal blocked: {member.name}")
                        # Links and device nodes can escape the extraction
                        # root even when the member name itself is relative.
                        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                            raise ValueError(f"Unsafe TAR member blocked: {member.name}")
                    for member in members:
                        tf.extract(member, target_dir, set_attrs=False)

                def _safe_zip_extract(zf, target_dir):
                    safe_extract_zip(
                        zf, target_dir,
                        max_members=50_000,
                        max_uncompressed_bytes=1024 * 1024 * 1024,
                    )

                def _safe_rar_extract(rf, target_dir):

                    infos = rf.infolist()
                    if len(infos) > 50_000:
                        raise ValueError("RAR has too many members")
                    paths = validate_archive_member_names(info.filename for info in infos)
                    declared_size = sum(max(0, int(getattr(info, "file_size", 0))) for info in infos)
                    if declared_size > 1024 * 1024 * 1024:
                        raise ValueError("RAR expands beyond the permitted size")

                    root = os.path.normcase(os.path.realpath(target_dir))
                    written_size = 0
                    for info, parts in zip(infos, paths):
                        is_link = getattr(info, "is_symlink", False)
                        if callable(is_link):
                            is_link = is_link()
                        if is_link or getattr(info, "file_redir", None):
                            raise ValueError(f"RAR link member blocked: {info.filename}")
                        out_path = os.path.realpath(os.path.join(target_dir, *parts))
                        try:
                            contained = os.path.commonpath((root, os.path.normcase(out_path))) == root
                        except ValueError:
                            contained = False
                        if not contained:
                            raise ValueError(f"RAR path escapes destination: {info.filename}")
                        is_dir = getattr(info, "isdir", None)
                        if callable(is_dir):
                            is_dir = is_dir()
                        elif is_dir is None:
                            is_dir = info.filename.endswith("/")
                        if is_dir:
                            os.makedirs(out_path, exist_ok=True)
                            continue
                        os.makedirs(os.path.dirname(out_path), exist_ok=True)
                        with rf.open(info.filename) as src_fh, open(out_path, "wb") as dst_fh:
                            while chunk := src_fh.read(1024 * 1024):
                                written_size += len(chunk)
                                if written_size > 1024 * 1024 * 1024:
                                    raise ValueError("RAR expands beyond the permitted size")
                                dst_fh.write(chunk)

                # Strip all extensions to get stem name
                stem = os.path.basename(src)
                for compound in ('.tar.gz', '.tar.bz2', '.tar.xz', '.tar.lz4', '.tar.zst'):
                    if stem.lower().endswith(compound):
                        stem = stem[:len(stem) - len(compound)]
                        break
                else:
                    stem = os.path.splitext(stem)[0]

                if to_subdir:
                    out_dir = _safe_dir(parent_phys, stem)
                    logical_out_dir = os.path.join(parent_dir, os.path.basename(out_dir))
                else:
                    out_dir = parent_phys
                    logical_out_dir = parent_dir

                ext = os.path.splitext(src)[1].lower()
                # Handle compound tar extensions
                full_lower = src.lower()

                if full_lower.endswith('.tar.gz') or full_lower.endswith('.tgz'):
                    import tarfile
                    with tarfile.open(src, 'r:gz') as tf:
                        _safe_tar_extract(tf, out_dir)

                elif full_lower.endswith('.tar.bz2') or full_lower.endswith('.tbz2'):
                    import tarfile
                    with tarfile.open(src, 'r:bz2') as tf:
                        _safe_tar_extract(tf, out_dir)

                elif full_lower.endswith('.tar.xz') or full_lower.endswith('.txz'):
                    import tarfile
                    with tarfile.open(src, 'r:xz') as tf:
                        _safe_tar_extract(tf, out_dir)

                elif full_lower.endswith('.tar.lz4'):
                    try:
                        import lz4.frame  # type: ignore[import-untyped]
                        import tarfile, io
                        with open(src, 'rb') as fh:
                            raw = lz4.frame.decompress(fh.read())
                        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                            _safe_tar_extract(tf, out_dir)
                    except ImportError:
                        return False, "lz4 not installed"

                elif full_lower.endswith('.tar.zst'):
                    try:
                        import zstandard as zstd
                        import tarfile
                        with open(src, 'rb') as fh:
                            dctx = zstd.ZstdDecompressor()
                            with dctx.stream_reader(fh) as reader:
                                with tarfile.open(fileobj=reader, mode='r|') as tf:
                                    _safe_tar_extract(tf, out_dir)
                    except ImportError:
                        return False, "zstandard not installed"

                elif ext == '.tar':
                    import tarfile
                    with tarfile.open(src, 'r:') as tf:
                        _safe_tar_extract(tf, out_dir)

                elif ext in ('.zip', '.jar', '.apk'):
                    import zipfile
                    with zipfile.ZipFile(src, 'r') as z:
                        _safe_zip_extract(z, out_dir)

                elif ext == '.rar':
                    try:
                        import rarfile
                        with rarfile.RarFile(src) as r:
                            _safe_rar_extract(r, out_dir)
                    except ImportError:
                        return False, "rarfile not installed"

                elif ext == '.7z':
                    try:
                        import py7zr
                        with py7zr.SevenZipFile(src, mode='r') as z:
                            infos = z.list()
                            if len(infos) > 50_000:
                                raise ValueError("7z has too many members")
                            validate_archive_member_names(info.filename for info in infos)
                            if any(info.is_symlink or not (info.is_directory or info.is_file)
                                   for info in infos):
                                raise ValueError("7z links or special members are not supported")
                            if sum(max(0, info.uncompressed) for info in infos) > 1024 * 1024 * 1024:
                                raise ValueError("7z expands beyond the permitted size")
                            z.extractall(path=out_dir)
                    except ImportError:
                        return False, "py7zr not installed"

                elif ext in ('.gz',):
                    import gzip
                    out_name = os.path.basename(src)[:-3]
                    out_path = os.path.join(out_dir, out_name)
                    with gzip.open(src, 'rb') as fh, open(out_path, 'wb') as fo:
                        shutil.copyfileobj(fh, fo)

                elif ext in ('.bz2',):
                    import bz2
                    out_name = os.path.basename(src)[:-4]
                    out_path = os.path.join(out_dir, out_name)
                    with bz2.open(src, 'rb') as fh, open(out_path, 'wb') as fo:
                        shutil.copyfileobj(fh, fo)

                elif ext in ('.xz',):
                    import lzma
                    out_name = os.path.basename(src)[:-3]
                    out_path = os.path.join(out_dir, out_name)
                    with lzma.open(src, 'rb') as fh, open(out_path, 'wb') as fo:
                        shutil.copyfileobj(fh, fo)

                elif ext in ('.zst', '.zstd'):
                    try:
                        import zstandard as zstd
                        out_name = os.path.basename(src)
                        for s in ('.zst', '.zstd'):
                            if out_name.lower().endswith(s):
                                out_name = out_name[:len(out_name) - len(s)]
                                break
                        out_path = os.path.join(out_dir, out_name)
                        with open(src, 'rb') as fh, open(out_path, 'wb') as fo:
                            dctx = zstd.ZstdDecompressor()
                            dctx.copy_stream(fh, fo)
                    except ImportError:
                        return False, "zstandard not installed"

                elif ext == '.lz4':
                    try:
                        import lz4.frame  # type: ignore[import-untyped]
                        out_name = os.path.basename(src)[:-4]
                        out_path = os.path.join(out_dir, out_name)
                        with open(src, 'rb') as fh:
                            data = lz4.frame.decompress(fh.read())
                        with open(out_path, 'wb') as fo:
                            fo.write(data)
                    except ImportError:
                        return False, "lz4 not installed"

                elif ext == '.pac':
                    from core.pac_lister import list_pac

                    listing = list_pac(src)
                    extracted = 0
                    chunk_size = 1024 * 1024

                    with open(src, 'rb') as fh:
                        for entry in listing.files:
                            out_path = os.path.join(out_dir, entry.file_name)
                            os.makedirs(os.path.dirname(out_path), exist_ok=True)

                            remaining = entry.size
                            fh.seek(entry.data_offset)
                            with open(out_path, 'wb') as fo:
                                while remaining > 0:
                                    chunk = fh.read(min(chunk_size, remaining))
                                    if not chunk:
                                        return False, f"PAC payload ended early: {entry.file_name}"
                                    fo.write(chunk)
                                    remaining -= len(chunk)
                            extracted += 1

                    if extracted == 0:
                        return False, "PAC contains no extractable files"

                elif ext == '.iso':
                    # ISO: just copy for now, proper extraction needs pycdlib
                    return False, "ISO extraction not yet supported"

                else:
                    # Remove empty dir if unsupported
                    try:
                        os.rmdir(out_dir)
                    except:
                        pass
                    return False, f"Unsupported archive: {ext}"

                print(f"[Extract] OK -> {out_dir}")
                return True, (out_dir, logical_out_dir)

            except Exception as e:
                traceback.print_exc()
                return False, str(e)

        def after(ok, msg):
            m = self.window()
            if m:
                m.pop_task(task_name, ok)

            if ok:
                if not (isinstance(msg, (tuple, list)) and len(msg) == 2):
                    if m:
                        m.set_loading_failed(f"Extract failed: invalid worker result: {msg!r}")
                    return
                out_dir, logical_out_dir = msg
                if to_subdir:
                    rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_out_dir)
                    if nb and active and os.path.exists(logical_out_dir):
                        nb.record_delete(fs_root, active["name"], rel, prune_overlay=False)
                self._nb_record_output_tree(logical_out_dir, out_dir)
                parent = None
                if item:
                    try:
                        parent = item.parent()
                    except (RuntimeError, Exception):
                        parent = None
                if parent is None:
                    parent = self.find_item_by_path(self.topLevelItem(0), parent_dir) or self.topLevelItem(0)
                if parent:
                    self.refresh_folder(parent)
                if m:
                    m.maybe_finish_loading()
            else:
                if m:
                    m.set_loading_failed(f"Extract failed: {msg}")

        run_in_thread(worker, None, callback=after)


    def _duplicate_item(self, item, full):

        import re as _re

        main = self.window()
        parent_dir = os.path.dirname(full)
        base_name  = os.path.basename(full)
        src = item.data(0, Qt.UserRole + 20) if item else None
        if not (src and os.path.exists(src)):
            src = full

        if os.path.isfile(src):
            stem, ext = os.path.splitext(base_name)
            m = _re.match(r'^(.*?)\((\d+)\)$', stem.rstrip())
            clean_stem = m.group(1).rstrip() if m else stem

            def _next_name(n):
                return os.path.join(parent_dir, f"{clean_stem}({n}){ext}")
        else:
            m = _re.match(r'^(.*?)\((\d+)\)$', base_name.rstrip())
            clean_name = m.group(1).rstrip() if m else base_name

            def _next_name(n):
                return os.path.join(parent_dir, f"{clean_name}({n})")

        n = 2
        while True:
            candidate = _next_name(n)
            candidate_phys, _, _ = self._nb_resolve_write_path(candidate)
            if not os.path.exists(candidate) and not os.path.exists(candidate_phys):
                break
            n += 1
        dest = _next_name(n)
        dest_phys, _, _ = self._nb_resolve_write_path(dest)

        task_id = f"duplicate:{base_name}"
        if main:
            main.push_task(task_id)

        def worker():
            try:
                os.makedirs(os.path.dirname(dest_phys), exist_ok=True)
                if os.path.isfile(src):
                    shutil.copy2(src, dest_phys)
                else:
                    shutil.copytree(src, dest_phys)
                return True, (dest, dest_phys)
            except Exception as e:
                return False, str(e)

        def after(ok, result):
            if main:
                main.pop_task(task_id)
            if not ok:
                if main:
                    main.set_loading_failed(f"Duplicate failed: {result}")
                return
            logical_dest, physical_dest = result
            self._nb_record_output_tree(logical_dest, physical_dest)
            parent_item = item.parent() or self.topLevelItem(0)
            if parent_item:
                self.refresh_folder(parent_item)
            if main:
                main.set_loading_finished(f"Duplicated: {os.path.basename(logical_dest)}")

        run_in_thread(worker, None, callback=after)


    def _neobranch_prepare_editor_switch(self, fs_root: str) -> bool:
        main = self.window()
        queue = getattr(main, "task_queue", None)
        if queue and queue.is_busy():
            from main_ext.class_AppDialog import AppDialog
            AppDialog.warning(main, "NeoBranch busy", "Wait for the current filesystem task to finish.")
            return False
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager = NeoBranchManager(self.base)
        manifest = os.path.join(manager._source_dir(fs_root), "repack_manifest.json")
        if os.path.isfile(manifest):
            from main_ext.class_AppDialog import AppDialog
            AppDialog.warning(main, "NeoBranch busy", "Wait for the partition repack to finish.")
            return False
        tabs = getattr(main, "unified_tabs", None)
        return not tabs or tabs.prepare_neobranch_switch(self.base, fs_root)

    def _handle_neobranch(self, item, full):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        from main_ext.class_NeoBranchDialog import NeoBranchDialog, NeoBranchPickerDialog
        from main_ext.class_AppDialog import AppDialog

        if not self.base:
            return

        fs_root  = os.path.relpath(full, self.base).replace("\\", "/")
        nb_mgr   = NeoBranchManager(self.base)
        branches = nb_mgr.list_branches(fs_root)
        active   = nb_mgr.get_active_branch(fs_root)
        active_name = active["name"] if active else None

        if not branches:
            # Belum ada branch sama sekali → langsung create dialog
            self._neobranch_create(item, fs_root, nb_mgr)
            return

        choice = NeoBranchPickerDialog.ask(self, fs_root, branches, active_name)
        if choice is None:
            return

        if choice == "NONE":
            if not self._neobranch_prepare_editor_switch(fs_root):
                return
            nb_mgr.set_active_branch(fs_root, None)
            self._neobranch_update_dot(item, fs_root, nb_mgr)
            self._neobranch_on_switch(item, fs_root)

        elif choice == "__NEW__":
            self._neobranch_create(item, fs_root, nb_mgr)

        elif choice.startswith("__DEL__"):
            del_name = choice[len("__DEL__"):]
            confirmed = AppDialog.confirm(
                self, t("rc_delete_branch_title"),
                t("rc_delete_branch_msg", name=del_name),
                yes_label=t("rc_delete"), no_label=t("dlg_btn_cancel")
            )
            if confirmed:
                was_active = (active_name == del_name)
                if was_active and not self._neobranch_prepare_editor_switch(fs_root):
                    return
                nb_mgr.delete_branch(fs_root, del_name)
                self._neobranch_update_dot(item, fs_root, nb_mgr)
                if was_active:
                    self._neobranch_on_switch(item, fs_root)

        else:
            # switch ke branch
            if not self._neobranch_prepare_editor_switch(fs_root):
                return
            nb_mgr.set_active_branch(fs_root, choice)
            self._neobranch_update_dot(item, fs_root, nb_mgr)
            self._neobranch_on_switch(item, fs_root)

    def _neobranch_create(self, item, fs_root, nb_mgr):
        from main_ext.class_NeoBranchDialog import NeoBranchDialog
        from core.threader import run_in_thread

        res = NeoBranchDialog.ask(self, fs_root)
        if not res:
            return

        if not self._neobranch_prepare_editor_switch(fs_root):
            return

        branch_name, color = res
        main    = self.window()
        task_id = f"preparing-neobranch-{fs_root}-{branch_name}"
        if main:
            main.push_task(task_id)

        def worker():
            try:
                nb_mgr.create_branch(fs_root, branch_name, color)
                return True, branch_name
            except Exception as e:
                return False, str(e)

        def after(ok, msg):
            if main:
                main.pop_task(task_id)
            if not ok:
                if main:
                    main.set_loading_failed(f"NeoBranch failed: {msg}")
                return
            nb_mgr.set_active_branch(fs_root, branch_name)
            self._neobranch_update_dot(item, fs_root, nb_mgr)
            self._neobranch_on_switch(item, fs_root)
            if main:
                main.set_loading_finished(f"NeoBranch '{branch_name}' created")

        run_in_thread(worker, None, callback=after)

    def _neobranch_update_dot(self, item, fs_root, nb_mgr):

        active = nb_mgr.get_active_branch(fs_root)
        if active:
            item.setData(0, Qt.UserRole + 10, active.get("color", "blue"))
        else:
            item.setData(0, Qt.UserRole + 10, None)
        self.viewport().update()

    def _neobranch_on_switch(self, fs_root_item, fs_root: str):

        try:
            self.reload_config_cache()
            print(f"[NeoBranch] on_switch reload_config OK: fs_root={fs_root}, "
                  f"ctx_keys={len(self.ctx)}, fs_keys={len(self.fs)}")
        except Exception as _e:
            import traceback
            print(f"[NeoBranch] on_switch reload_config error: {_e}")
            traceback.print_exc()

        try:
            # refresh_folder rebuilds children — nb_is_deleted filter runs on each item
            self.refresh_folder(fs_root_item)
        except Exception as _e:
            import traceback
            print(f"[NeoBranch] on_switch refresh error: {_e}")
            traceback.print_exc()

    def _run_neocat_scan(self, item, full):

        main = self.window()
        project_base = self.base
        fs_root = os.path.relpath(full, project_base).replace("\\", "/")
        task_id = f"neocat:{fs_root}"

        if main:
            main.push_task(task_id)

        print(f"[NEOCAT] verified policy scan: project_base={project_base} fs_root={fs_root}")

        # Simpan result di closure biar tidak perlu passing lewat signal
        _state = {"ok": False, "total": 0, "rejected": 0, "err": ""}

        def worker():
            try:
                from main_ext.class_NeoBranchManager import NeoBranchManager
                scan_base = project_base
                nb_mgr = NeoBranchManager(project_base)
                active = nb_mgr.get_active_branch(fs_root)
                if active:
                    scan_base = nb_mgr._branch_dir(fs_root, active["name"])
                    os.makedirs(nb_mgr._branch_neocat_config_dir(fs_root, active["name"]), exist_ok=True)
                    print(f"[NEOCAT] NeoBranch branch-only scan: {active['name']}")
                from core.neocat_selinux_engine import DeviceSelinuxPatchEngine
                cache_dir = neocat_cache_dir(
                    project_base, fs_root, active["name"] if active else None,
                    create=bool(active),
                )
                planner = DeviceSelinuxPatchEngine(
                    project_base, target_base=scan_base, cache_dir=cache_dir
                )
                plan = planner.plan(fs_root)
                if not plan.ready:
                    raise RuntimeError(plan.summary())
                _state["total"] = planner.commit(plan)
                _state["rejected"] = len(plan.rejected)
                _state["ok"] = True
            except Exception as e:
                import traceback
                traceback.print_exc()
                _state["err"] = str(e)

        def after(*_):
            if main:
                main.pop_task(task_id)

            if not _state["ok"]:
                print(f"[NEOCAT] scan failed: {_state['err']}")
                if main:
                    main.set_loading_failed(f"Neocat SELinux Patch failed: {_state['err']}")
                return

            total = _state["total"]
            rejected = _state["rejected"]
            print(f"[NEOCAT] verified scan done: {total} cached, {rejected} withheld")

            # Reload ctx_neocat supaya context baru langsung terbaca di tree
            self.reload_config_cache()

            # Refresh tampilan — get_meta baca ctx_neocat langsung, tidak ada inject
            parent = item.parent() or self.topLevelItem(0)
            self.refresh_folder(parent)
            self.viewport().update()

            if main:
                main.set_loading_finished(
                    f"Neocat SELinux Patch: {total} verified, {rejected} withheld for {fs_root}")

        run_in_thread(worker, None, callback=after)

    def _create_new_item(self, parent_item, target_dir, is_folder=False):

        from main_ext.class_AppDialog import AppDialog

        if not parent_item:
            return

        print(f"[NEWITEM] start target_dir={target_dir} is_folder={is_folder} "
              f"parent_children={parent_item.childCount()} expanded={parent_item.isExpanded()}")

        self.expandItem(parent_item)

        # buat dummy kosong persis seperti add_new
        dummy = QTreeWidgetItem(["", "", ""])
        dummy.setData(0, Qt.UserRole, target_dir)
        if is_folder:
            dummy.setIcon(0, self.icon_folder)
            dummy.setData(0, Qt.UserRole + 30, "")
        else:
            dummy.setIcon(0, self.icon_file)

        dummy.setFlags(dummy.flags() | Qt.ItemIsEditable)

        parent_item.insertChild(0, dummy)
        self.setCurrentItem(dummy)
        self.scrollToItem(dummy)
        print(f"[NEWITEM] dummy inserted, parent_children={parent_item.childCount()}")

        self._creating_item  = dummy
        self._editing_item   = dummy
        dummy._creating_mode = "folder" if is_folder else "file"
        # ── KUNCI: set _renaming=True biar mousePressEvent skip _cancel_dummy ──
        self._renaming = True

        finished = [False]

        def cleanup():
            if finished[0]:
                return
            finished[0] = True
            try:
                self.itemChanged.disconnect(on_commit)
            except Exception:
                pass
            try:
                self.closePersistentEditor(dummy, 0)
            except Exception:
                pass
            self._creating_item = None
            self._editing_item  = None
            self._renaming      = False

        def on_commit(changed_item, col):
            if changed_item is not dummy or col != 0:
                return

            name = dummy.text(0).strip()


            if not name:
                return

            # resolve path ke branch kalau aktif
            new_source_path = os.path.join(target_dir, name)
            rel_new  = os.path.relpath(new_source_path, self.base).replace("\\", "/")
            fs_root  = rel_new.split("/")[0]
            create_path = new_source_path  # default: source

            if fs_root and self.nb_is_active(fs_root):
                nb     = self._get_nb_manager()
                active = nb.get_active_branch(fs_root) if nb else None
                if active and nb:
                    branch_fs_dir = nb._branch_fs_dir(fs_root, active["name"])
                    rel_inside    = "/".join(rel_new.split("/")[1:])
                    create_path   = os.path.join(branch_fs_dir, rel_inside.replace("/", os.sep))

            # nama bentrok
            if os.path.exists(create_path):
                AppDialog.warning(self.window(), t("rc_name_exists_title"), t("rc_name_exists_msg"))
                try:
                    parent_item.removeChild(dummy)
                except Exception:
                    pass
                cleanup()
                return

            # buat di disk
            try:
                if is_folder:
                    os.makedirs(create_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(create_path), exist_ok=True)
                    open(create_path, "w").close()
            except Exception as e:
                AppDialog.warning(self.window(), t("lbl_error"), str(e))
                try:
                    parent_item.removeChild(dummy)
                except Exception:
                    pass
                cleanup()
                return

            cleanup()


            try:
                parent_item.removeChild(dummy)
            except Exception:
                pass

            # inject ctx/perm ke branch config kalau belum ada
            raw_ctx_key = self.engine.escaped_ctx_key(rel_new)
            if raw_ctx_key not in self.ctx and raw_ctx_key not in self.ctx_neocat:
                self._nb_apply(rel_new)

            self.refresh_folder(parent_item)

            new_item = self.find_item_by_path(self.topLevelItem(0), new_source_path)
            if new_item:
                self.expandItem(parent_item)
                self.setCurrentItem(new_item)
                self.scrollToItem(new_item)

        self.itemChanged.connect(on_commit)

        # ── buka editor via openPersistentEditor + grab focus ke QLineEdit ──
        from PySide6.QtCore import QTimer
        def _open():
            print(f"[NEWITEM] _open creating_item={self._creating_item is not None} "
                  f"dummy_parent={dummy.parent() is not None}")
            if self._creating_item is None:
                return
            self.openPersistentEditor(dummy, 0)
            def _focus():
                print(f"[NEWITEM] _focus creating_item={self._creating_item is not None} "
                      f"dummy_parent={dummy.parent() is not None} "
                      f"lineedits={len(self.viewport().findChildren(__import__('PySide6.QtWidgets', fromlist=['QLineEdit']).QLineEdit))}")
                if self._creating_item is None:
                    return
                from PySide6.QtWidgets import QLineEdit
                from PySide6.QtCore import QObject, QEvent
                for w in self.viewport().findChildren(QLineEdit):
                    w.setFocus(Qt.OtherFocusReason)
                    w.deselect()
                    w.end(False)


                    def _commit_name(text):
                        if finished[0]:
                            return
                        name = (text or "").strip()
                        if not name:
                            try:
                                parent_item.removeChild(dummy)
                            except Exception:
                                pass
                            cleanup()
                        else:
                            # text sama persis → setText tidak emit itemChanged,
                            # jadi panggil on_commit langsung kalau perlu.
                            if dummy.text(0) == name:
                                on_commit(dummy, 0)
                            else:
                                dummy.setText(0, name)

                    class _EscFilter(QObject):
                        def __init__(self_, parent=None):
                            super().__init__(parent)
                        def eventFilter(self_, obj, ev):
                            if ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
                                try:
                                    parent_item.removeChild(dummy)
                                except Exception:
                                    pass
                                cleanup()
                                return True
                            if ev.type() == QEvent.KeyPress and ev.key() in (
                                Qt.Key_Return, Qt.Key_Enter
                            ):
                                _commit_name(obj.text())
                                return True
                            if ev.type() == QEvent.FocusOut:
                                _commit_name(obj.text())
                                return False
                            return False

                    _f = _EscFilter(w)
                    w.installEventFilter(_f)
                    break
            QTimer.singleShot(30, _focus)
        QTimer.singleShot(0, _open)

    def _find_child_by_path(self, parent_item, path):

        if parent_item is None:
            for i in range(self.topLevelItemCount()):
                it = self.topLevelItem(i)
                if it.data(0, Qt.UserRole) == path:
                    return it
            return None
        for i in range(parent_item.childCount()):
            it = parent_item.child(i)
            if it.data(0, Qt.UserRole) == path:
                return it
        return None

    def _open_in_explorer(self, full):
        target = full if os.path.isdir(full) else os.path.dirname(full)
        if sys.platform == "win32":
            os.startfile(target)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
