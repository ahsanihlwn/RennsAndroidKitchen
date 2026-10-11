# SPDX-License-Identifier: MPL-2.0
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QWidget, QRadioButton,
    QFileDialog, QScrollArea, QSizePolicy, QApplication, QLayout, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, Signal, QSize, QPoint, QRect, QEvent, QTimer,
)
from PySide6.QtGui import QColor

from main_ext.class_ThemeUi import theme as _th
from core.theme_binding import bind_style, bind_icon, refresh_theme, panel_background, with_alpha
from core.language_binding import bind_text, bind_language, refresh_language
from icons.handler import tint_icon, create_themed_icon
from app_ext.class_BlurOverlay import BlurOverlay, install_window_fade
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from app_ext.class_ButtonCardMorph import ButtonCardMorph
from app_ext.class_ButtonPressAnimation import (
    button_press_scale, has_button_press_feedback, reset_button_press_feedback,
)


def _t(key):
    try:
        from core.ui_lang import t
        return t(key)
    except Exception:
        return key

def _btn_ss(variant="normal"):
    ac = _th.accent
    bc = _th.border_color
    if variant == "primary":
        return f"""
            QPushButton {{
                background: {ac(0.18)}; border: 1px solid {ac(0.38)};
                border-radius: 7px; color: {_th.FG_BLUE};
                font-size: 12px; font-weight: 600;
                padding: 0 14px; height: 34px;
            }}
            QPushButton:hover {{ background: {ac(0.30)}; border-color: {ac(0.55)}; color: {_th.FG_BLUE_LIGHT}; }}
            QPushButton:pressed {{ background: {ac(0.44)}; }}
            QPushButton:disabled {{ background: {ac(0.07)}; border-color: {ac(0.12)}; color: {ac(0.30)}; }}
        """
    return f"""
        QPushButton {{
            background: {bc(0.05)}; border: 1px solid {bc(0.09)};
            border-radius: 7px; color: {_th.FG_BLUE_MUTED};
            font-size: 12px; padding: 0 14px; height: 34px;
        }}
        QPushButton:hover {{ background: {bc(0.09)}; border-color: {bc(0.15)}; color: {_th.FG_HOVER}; }}
        QPushButton:pressed {{ background: {bc(0.13)}; }}
        QPushButton:disabled {{ background: {bc(0.02)}; border-color: {bc(0.04)}; color: {_th.FG_DISABLED2}; }}
    """

def _load_default_location():
    try:
        from app_ext.nonclass_VerifyDir import resolve_default_location
        return resolve_default_location()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Documents")


class CreateProjectDialog(QDialog):
    project_created = Signal(dict)  # legacy — kini record dibaca via _result_record di show_popup

    def __init__(self, parent=None, as_popup=False):
        super().__init__(parent)


        flags = Qt.Tool | Qt.NoDropShadowWindowHint if as_popup else Qt.Dialog
        self.setWindowFlags(flags | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if as_popup:
            self.setAttribute(Qt.WA_NoMouseReplay)
        self.setObjectName("cp_dialog")
        bind_style(self, lambda: _th.app_stylesheet() + "\nQDialog#cp_dialog { background: transparent; border: none; }")
        self.setModal(not as_popup)

        self._port_mode = 1
        self._blur = None
        self._result_record = None
        self._popup_open = False
        self._popup_on_hidden = None
        self._anchor_pos = None
        self._card_morph = None
        self._closing_with_morph = False
        self._pending_result = None
        self._source_button = None
        self._source_effect = None
        self._morph_margin = 12 if getattr(parent, "btn_create", None) is not None else 0
        self.setFixedWidth(440 + 2 * self._morph_margin)

        self._build_ui()
        self._bound_theme_name = _th.name
        self._bound_blur_enabled = _th.blur_enabled
        self.adjustSize()
        self._chrome_height = sum(widget.minimumHeight() for widget in (
            self._header, self._header_divider, self._footer_divider, self._footer,
        ))
        box_margins = self._box_layout.contentsMargins()
        self._chrome_height += box_margins.top() + box_margins.bottom()
        if parent is not None:
            parent.installEventFilter(self)

    # UI

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._mask_host = QWidget(self)
        self._mask_host.setObjectName("cp_morph_host")
        self._mask_host.setStyleSheet("QWidget#cp_morph_host { background: transparent; border: none; }")
        host_layout = QVBoxLayout(self._mask_host)
        margin = self._morph_margin
        host_layout.setContentsMargins(margin, margin, margin, margin)
        outer.addWidget(self._mask_host)

        box = QWidget()
        box.setObjectName("cp_box")
        bind_style(box, lambda: f"""
            QWidget#cp_box {{
                background: {panel_background(120)};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
        """)
        host_layout.addWidget(box)
        self._box = box

        lay = QVBoxLayout(box)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self._box_layout = lay

        # header
        hdr = QWidget()
        hdr.setStyleSheet("background:transparent; border:none;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(20, 16, 16, 12)
        hdr_lay.setSpacing(8)

        title = bind_text(QLabel(), lambda: _t("hdr_new_project"))
        bind_style(title, lambda: f"font-size:17px; font-weight:700; color:{_th.FG_BRIGHT};"
                            " background:transparent; border:none;")
        hdr_lay.addWidget(title, 1)

        btn_x = QPushButton("✕")
        btn_x.setFixedSize(28, 28)
        btn_x.setCursor(Qt.PointingHandCursor)
        bind_style(btn_x, lambda: f"""
            QPushButton {{
                background: transparent; border: none;
                color: {_th.FG_MUTED}; font-size: 14px;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.10)}; color: {_th.FG}; }}
        """)
        btn_x.clicked.connect(self.reject)
        hdr_lay.addWidget(btn_x)
        hdr.setFixedHeight(hdr.sizeHint().height())
        self._header = hdr
        lay.addWidget(hdr)

        # divider
        div_hdr = QWidget(); div_hdr.setFixedHeight(1)
        bind_style(div_hdr, lambda: f"background:{_th.border_color(0.07)}; border:none;")
        self._header_divider = div_hdr
        lay.addWidget(div_hdr)

        # scroll content
        _input_ss = lambda: f"""
            QLineEdit {{
                background: {_th.BG_INPUT};
                border: 1px solid {_th.border_color(0.10)};
                border-radius: 8px;
                padding: 8px 12px;
                color: {_th.FG};
                font-size: 13px;
                selection-background-color: {_th.accent(0.35)};
            }}
            QLineEdit:focus {{
                border-color: {_th.accent(0.55)};
                background: {_th.BG_SELECTED};
            }}
            QLineEdit:disabled {{
                color: {_th.FG_DISABLED};
                border-color: {_th.border_color(0.05)};
                background: {_th.BG_DISABLED};
            }}
        """
        _label_ss = (lambda: f"font-size:11px; font-weight:600; color:{_th.FG_MUTED};"
                     " background:transparent; border:none;")

        def _card(obj_name):
            c = QWidget(); c.setObjectName(obj_name)
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            # sub-card tipis di dalam glass dialog — pakai overlay putih halus
            bind_style(c, lambda obj_name=obj_name: f"""
                QWidget#{obj_name} {{
                    background: rgba(255, 255, 255, 10);
                    border: 1px solid {_th.border_color(0.10)};
                    border-radius: 12px;
                }}
            """)
            return c

        def _divider():
            d = QWidget(); d.setFixedHeight(1)
            bind_style(d, lambda: f"background:{_th.border_color(0.07)}; border:none;")
            return d

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(16, 14, 16, 24)
        cv.setSpacing(12)
        cv.setSizeConstraint(QLayout.SetMinimumSize)

        # card: project info
        card_info = _card("cp_card_info")
        ci_lay = QVBoxLayout(card_info)
        ci_lay.setContentsMargins(18, 16, 18, 18)
        ci_lay.setSpacing(12)

        lbl_name = bind_text(QLabel(), lambda: _t("lbl_project_name")); bind_style(lbl_name, _label_ss)
        self.input_name = QLineEdit()
        bind_language(self.input_name, "setPlaceholderText", lambda: _t("placeholder_project_name"))
        self.input_name.setFixedHeight(38)
        bind_style(self.input_name, _input_ss)
        ci_lay.addWidget(lbl_name)
        ci_lay.addWidget(self.input_name)
        ci_lay.addWidget(_divider())

        # mode selector
        lbl_mode = bind_text(QLabel(), lambda: _t("lbl_porting_mode")); bind_style(lbl_mode, _label_ss)
        ci_lay.addWidget(lbl_mode)

        _mode_active = lambda: f"""
            QPushButton {{
                background: {_th.accent(0.18)};
                border: 1.5px solid {_th.accent(0.50)};
                border-radius: 8px; color: {_th.FG_BLUE};
                font-size: 13px; font-weight: 600; padding: 8px 0;
            }}
        """
        _mode_inactive = lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.04)};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 8px; color: {_th.FG_MUTED};
                font-size: 13px; font-weight: 400; padding: 8px 0;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.08)}; color: {_th.FG}; }}
        """

        self.radio_1 = QRadioButton(); self.radio_1.setChecked(True); self.radio_1.hide()
        self.radio_2 = QRadioButton(); self.radio_2.hide()

        self._btn_single = bind_text(QPushButton(), lambda: _t("btn_single_rom"))
        self._btn_dual   = bind_text(QPushButton(), lambda: _t("btn_dual_rom"))
        for b in (self._btn_single, self._btn_dual):
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setFixedHeight(38)
        bind_style(self._btn_single, _mode_active)
        bind_style(self._btn_dual, _mode_inactive)
        self._mode_active_ss   = _mode_active
        self._mode_inactive_ss = _mode_inactive

        def _pick_single():
            self.radio_1.setChecked(True); self._port_mode = 1
            bind_style(self._btn_single, _mode_active)
            bind_style(self._btn_dual, _mode_inactive)
            self._apply_port_mode(); self._update_btn()

        def _pick_dual():
            self.radio_2.setChecked(True); self._port_mode = 2
            bind_style(self._btn_single, _mode_inactive)
            bind_style(self._btn_dual, _mode_active)
            self._apply_port_mode(); self._update_btn()

        self._btn_single.clicked.connect(_pick_single)
        self._btn_dual.clicked.connect(_pick_dual)

        mode_row = QHBoxLayout(); mode_row.setSpacing(8); mode_row.setContentsMargins(0,0,0,0)
        mode_row.addWidget(self._btn_single); mode_row.addWidget(self._btn_dual)
        ci_lay.addLayout(mode_row)
        cv.addWidget(card_info)

        # card: port names
        card_ports = _card("cp_card_ports")
        cp_lay = QVBoxLayout(card_ports)
        cp_lay.setContentsMargins(18, 16, 18, 18)
        cp_lay.setSpacing(12)

        lbl_ph = bind_text(QLabel(), lambda: _t("lbl_folder_names")); bind_style(lbl_ph, _label_ss)
        lbl_hint = bind_text(QLabel(), lambda: _t("hint_folder_names"))
        bind_style(lbl_hint, lambda: f"font-size:11px; color:{_th.FG_MUTED}; background:transparent; border:none;")
        lbl_hint.setWordWrap(True)
        cp_lay.addWidget(lbl_ph); cp_lay.addWidget(lbl_hint)
        cp_lay.addWidget(_divider())

        lbl_p1 = bind_text(QLabel(), lambda: _t("lbl_ft1_folder")); bind_style(lbl_p1, _label_ss)
        self.input_port1 = QLineEdit()
        bind_language(self.input_port1, "setPlaceholderText", lambda: _t("placeholder_ft1"))
        self.input_port1.setFixedHeight(38)
        bind_style(self.input_port1, _input_ss)
        cp_lay.addWidget(lbl_p1); cp_lay.addWidget(self.input_port1)

        self._div_p2  = _divider()
        self._lbl_p2  = bind_text(QLabel(), lambda: _t("lbl_ft2_folder")); bind_style(self._lbl_p2, _label_ss)
        self.input_port2 = QLineEdit()
        bind_language(self.input_port2, "setPlaceholderText", lambda: _t("placeholder_ft2"))
        self.input_port2.setFixedHeight(38)
        bind_style(self.input_port2, _input_ss)
        cp_lay.addWidget(self._div_p2)
        cp_lay.addWidget(self._lbl_p2)
        cp_lay.addWidget(self.input_port2)
        self._div_p2.hide(); self._lbl_p2.hide(); self.input_port2.hide()

        cv.addWidget(card_ports)

        # card: directory
        card_dir = _card("cp_card_dir")
        cd_lay = QVBoxLayout(card_dir)
        cd_lay.setContentsMargins(18, 16, 18, 18)
        cd_lay.setSpacing(10)

        lbl_dir = bind_text(QLabel(), lambda: _t("lbl_output_dir")); bind_style(lbl_dir, _label_ss)
        cd_lay.addWidget(lbl_dir)

        dir_row = QHBoxLayout(); dir_row.setSpacing(8); dir_row.setContentsMargins(0,0,0,0)

        self.path_label = QLabel(_load_default_location())
        bind_style(self.path_label, lambda: f"color:{_th.FG_MUTED}; font-size:12px; background:transparent; border:none;")
        self.path_label.setWordWrap(False)
        self.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.path_label.setToolTip(self.path_label.text())

        btn_browse = bind_text(QPushButton(), lambda: _t("btn_browse"))
        btn_browse.setFixedHeight(32)
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._select_path)
        dir_row.addWidget(self.path_label, 1); dir_row.addWidget(btn_browse)
        cd_lay.addLayout(dir_row)
        cv.addWidget(card_dir)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMaximumHeight(420)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidget(content)
        self._scroll = scroll
        self._content = content
        self._last_card = card_dir
        self._scroll_extent_pending = False
        scroll.verticalScrollBar().rangeChanged.connect(self._queue_scroll_extent)
        lay.addWidget(scroll)

        # footer
        div_ft = QWidget(); div_ft.setFixedHeight(1)
        bind_style(div_ft, lambda: f"background:{_th.border_color(0.07)}; border:none;")
        self._footer_divider = div_ft
        lay.addWidget(div_ft)

        footer = QWidget()
        footer.setStyleSheet("background: transparent; border: none;")
        ft_lay = QHBoxLayout(footer)
        ft_lay.setContentsMargins(16, 16, 16, 18)
        ft_lay.setSpacing(8)
        ft_lay.addStretch()

        btn_cancel = bind_text(QPushButton(), lambda: _t("btn_back"))
        btn_cancel.setLayoutDirection(Qt.LeftToRight)
        bind_icon(btn_cancel, lambda: tint_icon("arrow_right", _th.FG2, 15))
        btn_cancel.setIconSize(QSize(15, 15))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setFixedHeight(34)
        bind_style(btn_cancel, lambda: _btn_ss("normal"))
        btn_cancel.clicked.connect(self.reject)

        self._btn_create = bind_text(QPushButton(), lambda: _t("btn_confirm_project"))
        self._btn_create.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self._btn_create, lambda: create_themed_icon(
            "check_white", _th.FG_BLUE, _th.FG_BLUE_LIGHT, 15,
            disabled_color=with_alpha(_th.accent_hex, 0.30),
        ))
        self._btn_create.setIconSize(QSize(15, 15))
        self._btn_create.setCursor(Qt.PointingHandCursor)
        self._btn_create.setFixedHeight(34)
        bind_style(self._btn_create, lambda: _btn_ss("primary"))
        self._btn_create.setEnabled(False)
        self._btn_create.clicked.connect(self._do_create)

        ft_lay.addWidget(btn_cancel)
        ft_lay.addWidget(self._btn_create)
        footer.setFixedHeight(74)
        self._footer = footer
        lay.addWidget(footer)

        # connect validation
        self.input_name.textChanged.connect(self._update_btn)
        self.input_port1.textChanged.connect(self._update_btn)
        self.input_port2.textChanged.connect(self._update_btn)


    # logic

    def _apply_port_mode(self):
        dual = self._port_mode == 2
        self._div_p2.setVisible(dual)
        self._lbl_p2.setVisible(dual)
        self.input_port2.setVisible(dual)
        if not dual:
            self.input_port2.clear()
        self.adjustSize()
        if self.isVisible():
            QTimer.singleShot(0, self, self._position_near_button)

    def _update_btn(self):
        name = self.input_name.text().strip()
        p1   = self.input_port1.text().strip()
        p2   = self.input_port2.text().strip()
        valid = bool(name and p1) if self._port_mode == 1 else bool(name and p1 and p2)
        self._btn_create.setEnabled(valid)

    def _select_path(self):
        path = QFileDialog.getExistingDirectory(self, _t("dlg_choose_folder"))
        if path:
            self.path_label.setText(path)
            self.path_label.setToolTip(path)

    def _do_create(self):
        from main_ext.class_AppDialog import AppDialog
        name = self.input_name.text().strip()
        p1   = self.input_port1.text().strip()
        p2   = self.input_port2.text().strip()

        if self._port_mode == 2 and not p2:
            AppDialog.warning(self, _t("lbl_error"), _t("err_ports_required"))
            return

        base_dir = self.path_label.text().strip()
        if not base_dir or not os.path.isabs(base_dir):
            base_dir = _load_default_location()

        proj_root = os.path.join(base_dir, name)
        try:
            os.makedirs(proj_root, exist_ok=True)
        except Exception:
            AppDialog.error(self, _t("lbl_error"), _t("err_create_folder"))
            return

        os.makedirs(os.path.join(proj_root, p1, "config"), exist_ok=True)
        if self._port_mode == 2 and p2:
            os.makedirs(os.path.join(proj_root, p2, "config"), exist_ok=True)

        # Store the record now; show_popup opens the editor after the closing
        # morph has hidden the popup and restored the source button.
        self._result_record = {
            "project_name": name,
            "path": proj_root,
            "port1": p1,
            "port2": p2 if self._port_mode == 2 else "",
            "mode": self._port_mode,
        }
        self.accept()

    def _position_near_button(self):

        parent = self.parentWidget()
        if parent is None:
            return None
        if self._closing_with_morph:
            self._complete_done()
            return None
        self._stop_card_morph()

        side_margin, top_margin, bottom_margin = 8, 16, 8
        top_left = parent.mapToGlobal(QPoint(0, 0))
        button = getattr(parent, "btn_create", None)
        if button is not None and button.isVisible():
            button_right = button.mapToGlobal(QPoint(button.width(), 0)).x()
        else:
            button_right = top_left.x() + parent.width() - side_margin

        max_height = max(1, min(500, parent.height() - top_margin - bottom_margin))
        card_width = max(1, min(440, parent.width() - 2 * side_margin))
        self.setFixedWidth(card_width + 2 * self._morph_margin)
        scroll_height = max(1, min(420, max_height - self._chrome_height))
        self._scroll.setFixedHeight(scroll_height)
        self.layout().activate()
        target_height = min(max_height, self._chrome_height + scroll_height)
        self.setFixedHeight(target_height + 2 * self._morph_margin)
        self.layout().activate()
        self._queue_scroll_extent()

        left = top_left.x() + side_margin
        right = top_left.x() + parent.width() - card_width
        x = max(left, min(button_right - card_width, right))
        y = top_left.y() + parent.height() - bottom_margin - target_height
        self._anchor_pos = QPoint(x - self._morph_margin, y - self._morph_margin)
        self.move(self._anchor_pos)
        self._mask_host.layout().activate()
        ButtonCardMorph.settle_card(self._mask_host, self._box)
        return self._anchor_pos

    def _morph_button(self):
        parent = self.parentWidget()
        button = getattr(parent, "btn_create", None) if parent is not None else None
        if button is None or not button.isVisible():
            return None
        if (button.graphicsEffect() not in (None, self._source_effect)
                and not has_button_press_feedback(button)):
            return None
        return button

    def _start_card_morph(self, opening, start=True):
        button = self._morph_button()
        if button is None:
            return False
        rect = QRect(button.mapToGlobal(QPoint()), button.size())
        rect.translate(-self._mask_host.mapToGlobal(QPoint()))
        if not self._mask_host.rect().contains(rect):
            return False
        self._stop_card_morph()
        launch_scale = button_press_scale(button) if opening else 1.0
        reset_button_press_feedback(button)
        if self._source_effect is None:
            self._source_button = button
            self._source_effect = QGraphicsOpacityEffect(button)
            self._source_effect.setOpacity(0.0)
            button.setGraphicsEffect(self._source_effect)
        morph = ButtonCardMorph(
            self._mask_host, self._box, button, rect, opening,
            source_effect=self._source_effect,
            launch_scale=launch_scale,
        )
        self._card_morph = morph

        def _finished():
            if self._card_morph is not morph:
                return
            if opening:
                self._card_morph = None
                self._focus_first_field()
            else:
                self._complete_done()

        morph.finished.connect(_finished)
        if start:
            morph.start()
        return True

    def _stop_card_morph(self):
        morph = self._card_morph
        self._card_morph = None
        if morph is not None:
            morph.stop()

    def _restore_source_button(self):
        if self._source_effect is not None:
            try:
                if self._source_button.graphicsEffect() is self._source_effect:
                    self._source_button.setGraphicsEffect(None)
                    self._source_button.repaint()
            except RuntimeError:
                pass
            self._source_effect = None
            self._source_button = None

    def done(self, result):
        if self._closing_with_morph:
            return
        self._pending_result = result
        if self._card_morph is not None and self._card_morph._opening:
            # An early Escape should cancel the unfinished opening, without
            # jumping to a full-sized card just to animate it closed again.
            self._complete_done()
            return
        if self.isVisible() and _th.animations_enabled and not getattr(self, "_window_fade_installed", False):
            self._closing_with_morph = True
            if self._start_card_morph(opening=False):
                overlay = getattr(self, "_overlay", None)
                if overlay is not None:
                    self._overlay = None
                    overlay.close_overlay()
                return
        self._complete_done()

    def _complete_done(self):
        if self._card_morph is not None:
            self._card_morph._group.stop()
        self._restore_source_button()
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            overlay.close_overlay()
            self._overlay = None
        result = self._pending_result if self._pending_result is not None else QDialog.Rejected
        super().done(result)
        self._stop_card_morph()
        self._mask_host.clearMask()
        self._box.setGraphicsEffect(None)
        self.setWindowOpacity(1.0)

    def hideEvent(self, event):
        self._stop_card_morph()
        self._restore_source_button()
        super().hideEvent(event)
        callback = self._popup_on_hidden
        if callback is not None:
            self._popup_on_hidden = None
            # Complete done()/the native hide before opening another window.
            QTimer.singleShot(0, self, callback)

    def _focus_first_field(self):
        # Qt can auto-scroll a newly shown QScrollArea to the last focusable
        # control. Always open the form with Project Name visible.
        if not self.isVisible():
            return
        self.input_name.setFocus(Qt.OtherFocusReason)
        self._scroll._anim.stop()
        self._scroll._target_scroll = 0
        self._scroll.verticalScrollBar().setValue(0)

    def _queue_scroll_extent(self, *_):
        if self._scroll_extent_pending:
            return
        self._scroll_extent_pending = True
        QTimer.singleShot(0, self, self._ensure_scroll_extent)

    def _ensure_scroll_extent(self):
        self._scroll_extent_pending = False
        if not self.isVisible():
            return
        self._content.layout().activate()
        viewport = self._scroll.viewport()
        bar = self._scroll.verticalScrollBar()
        card_bottom = self._last_card.mapTo(
            viewport, QPoint(0, self._last_card.height())
        ).y()
        bottom_at_limit = card_bottom - (bar.maximum() - bar.value())
        extra = bottom_at_limit - (viewport.height() - 20)
        if extra <= 0:
            return

        was_at_bottom = bar.maximum() > 0 and bar.value() >= bar.maximum() - 1
        self._content.setMinimumHeight(self._content.height() + extra)
        self._content.updateGeometry()
        if was_at_bottom:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "_overlay", None)
                and event.type() == QEvent.MouseButtonPress and self._popup_open):
            # A nested dialog owns input until it closes, even if its native
            # folder picker forwards a click to the backdrop.
            if QApplication.activeModalWidget() is None:
                self.reject()
            return True
        if watched is self.parentWidget():
            kind = event.type()
            if kind in (QEvent.Move, QEvent.Resize) and self.isVisible():
                QTimer.singleShot(0, self, self._position_near_button)
            elif kind == QEvent.Hide and self._popup_open:
                self._pending_result = QDialog.Rejected
                self._complete_done()
        return super().eventFilter(watched, event)

    def showEvent(self, e):
        super().showEvent(e)
        self._closing_with_morph = False
        self._pending_result = None
        self._position_near_button()
        self._focus_first_field()
        morph_ready = False
        if _th.animations_enabled and not getattr(self, "_window_fade_installed", False):
            morph_ready = self._start_card_morph(opening=True, start=False)
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and not getattr(self, "_window_fade_installed", False):
            surface = ButtonCardMorph.expand_backdrop(self._mask_host)
            if surface is not None:
                surface.show()
            self._mask_host._morph_blur_surface = overlay.attach_window_surface(
                self, host=self._mask_host, freeze_immediately=True,
            )
        QTimer.singleShot(0, self, self._focus_first_field)
        if morph_ready:
            self._card_morph.start()
        else:
            ButtonCardMorph.settle_card(self._mask_host, self._box)

    # show dengan blur

    @staticmethod
    def prepare_popup(app_ui):

        dialog = getattr(app_ui, "_create_project_dialog", None)
        if dialog is not None:
            refresh_language(dialog)
            blur_changed = dialog._bound_blur_enabled != _th.blur_enabled
            if blur_changed:
                # The dialog is reused, but a frozen glass surface belongs to
                # the old Blur setting. Remove it before the next opening.
                surface = getattr(dialog._mask_host, "_morph_blur_surface", None)
                dialog._mask_host._morph_blur_surface = None
                if surface is not None:
                    surface.hide()
                    surface.setParent(None)
                    surface.deleteLater()
            if dialog._bound_theme_name != _th.name or blur_changed:
                refresh_theme(dialog)
                surface = getattr(dialog._mask_host, "_morph_blur_surface", None)
                if surface is not None:
                    surface._tint = with_alpha(_th.BG2, 72 / 255)
                dialog._bound_theme_name = _th.name
                dialog._bound_blur_enabled = _th.blur_enabled
            return dialog
        dialog = CreateProjectDialog(parent=app_ui, as_popup=True)
        app_ui._create_project_dialog = dialog
        dialog.ensurePolished()
        dialog._position_near_button()
        dialog._box.grab()
        dialog.winId()
        return dialog

    def _reset_form(self):

        self._closing_with_morph = False
        self._pending_result = None
        self._result_record = None
        self.input_name.clear()
        self.input_port1.clear()
        self.input_port2.clear()
        self._port_mode = 1
        self.radio_1.setChecked(True)
        bind_style(self._btn_single, self._mode_active_ss)
        bind_style(self._btn_dual, self._mode_inactive_ss)
        self._content.setMinimumHeight(0)
        self._apply_port_mode()
        self._update_btn()
        self._scroll.verticalScrollBar().setValue(0)

    @staticmethod
    def show_popup(app_ui):

        dlg = CreateProjectDialog.prepare_popup(app_ui)
        if dlg._popup_open:
            return
        dlg._popup_open = True
        path = _load_default_location()
        dlg.path_label.setText(path)
        dlg.path_label.setToolTip(path)
        ov = BlurOverlay(app_ui, capture_without_backdrop=True, block_input=True,
                         cache_fade_background=True)
        dlg._overlay = ov
        ov.installEventFilter(dlg)
        if dlg._morph_button() is None:
            install_window_fade(dlg)

        def _finalize():
            record = dlg._result_record
            ov.close_overlay()
            dlg._overlay = None
            dlg._popup_open = False
            if getattr(dlg, "_window_fade_installed", False):
                app_ui._create_project_dialog = None
                dlg.deleteLater()
                warm_timer = getattr(app_ui, "_create_popup_warm_timer", None)
                if warm_timer is not None:
                    warm_timer.start(0)
            else:
                dlg._reset_form()
            if record:
                app_ui._on_popup_project_created(record)

        dlg._popup_on_hidden = _finalize
        dlg.show()
