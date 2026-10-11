# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QScrollArea, QPushButton, QSizePolicy, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect, QUrl
from PySide6.QtGui import QIcon, QPainter, QColor, QPen
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from icons.handler import icon
from core.dependency_manager import DependencyManager, REMOTE_REGISTRY_URL
from core import wsl_manager
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from main_ext.class_ThemeUi import theme as _th
from core.ui_lang import t


def _ss_primary():
    return f"""
    QPushButton {{
        background: {_th.accent(0.18)};
        border: 1px solid {_th.accent(0.38)};
        border-radius: 6px;
        color: {_th.FG_BLUE};
        font-size: 12px;
        font-weight: 600;
        padding: 0 14px;
        height: 30px;
    }}
    QPushButton:hover {{ background: {_th.accent(0.32)}; border-color: {_th.accent(0.60)}; color: {_th.FG_BLUE_LIGHT}; }}
    QPushButton:pressed {{ background: {_th.accent(0.48)}; }}
    """

def _ss_neutral():
    return f"""
    QPushButton {{
        background: {_th.border_color(0.06)};
        border: 1px solid {_th.border_color(0.10)};
        border-radius: 6px;
        color: {_th.FG_MUTED};
        font-size: 12px;
        font-weight: 400;
        padding: 0 14px;
        height: 30px;
    }}
    QPushButton:hover {{ background: {_th.border_color(0.10)}; border-color: {_th.border_color(0.18)}; color: {_th.FG}; }}
    QPushButton:pressed {{ background: {_th.border_color(0.14)}; }}
    """

def _ss_action():
    return f"""
    QPushButton {{
        background: {_th.accent(0.12)};
        border: 1px solid {_th.accent(0.28)};
        border-radius: 13px;
        color: {_th.FG_BLUE};
        font-size: 11px;
        font-weight: 600;
        padding: 0 12px;
    }}
    QPushButton:hover {{
        background: {_th.accent(0.24)};
        border-color: {_th.accent(0.50)};
        color: {_th.FG_BLUE_LIGHT};
    }}
    QPushButton:pressed {{ background: {_th.accent(0.36)}; }}
    QPushButton:disabled {{
        color: {_th.FG_DISABLED2};
        border-color: {_th.border_color(0.04)};
        background: transparent;
    }}
    """

def _ss_action_error():
    return """
    QPushButton {
        background: rgba(220,70,70,0.12);
        border: 1px solid rgba(220,70,70,0.28);
        border-radius: 13px;
        color: #f08080;
        font-size: 11px;
        font-weight: 600;
        padding: 0 12px;
        height: 26px;
    }
    QPushButton:hover {
        background: rgba(220,70,70,0.24);
        border-color: rgba(220,70,70,0.50);
        color: #ff9999;
    }
    """


class _Spinner(QWidget):
    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self._sz = size
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._angle = (self._angle - 5) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pad  = 3
        rect = QRect(pad, pad, self._sz - pad * 2, self._sz - pad * 2)

        br, bg, bb = _th._palette["_border_rgb"]
        pen = QPen(QColor(br, bg, bb, 18))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        ar, ag, ab = _th._palette["_accent_rgb"]
        pen2 = QPen(QColor(ar, ag, ab, 200))
        pen2.setWidth(2)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawArc(rect, self._angle * 16, 90 * 16)
        p.end()

    def stop(self):
        self._timer.stop()


class InstallWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, manager, pkg_name):
        super().__init__()
        self.manager = manager
        self.pkg_name = pkg_name

    def run(self):
        try:
            self.manager.install_package(self.pkg_name)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class WslDetectWorker(QThread):

    finished = Signal(dict)

    def run(self):
        try:
            info = wsl_manager.detect()
        except Exception as e:
            info = {"installed": False, "present": False, "version": "",
                    "has_distro": False, "distros": [], "detail": str(e)}
        self.finished.emit(info)


class WslInstallWorker(QThread):
    finished = Signal(bool, str)

    def run(self):
        try:
            ok, msg = wsl_manager.install()
        except Exception as e:
            ok, msg = False, str(e)
        self.finished.emit(ok, msg)


class DependencyWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(t("dep_title"))
        self.setFixedSize(540, 420)
        self.setModal(True)

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.fetch_done = False
        self.manager = None
        self.pkg_widgets = {}
        self.workers = []
        self._nam = QNetworkAccessManager(self)  # async HTTP, no QThread needed
        self._reply = None

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)

        # Convert _th.BG2 → rgba (efek kaca di atas BlurOverlay)
        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

        self._box = QWidget()
        self._box.setObjectName("dep_box")
        self._box.setStyleSheet(f"""
            QWidget#dep_box {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
            QWidget#dep_box QLabel {{
                background: transparent;
                border: none;
                color: {_th.FG};
            }}
        """)
        shell.addWidget(self._box)

        box_layout = QVBoxLayout(self._box)
        box_layout.setContentsMargins(18, 16, 18, 16)
        box_layout.setSpacing(12)

        # header
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        ic = QLabel()
        ic.setPixmap(QIcon(icon("package")).pixmap(15, 15))
        ic.setFixedSize(18, 18)
        ic.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        title = QLabel(t("dep_title"))
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {_th.FG};")

        header_row.addWidget(ic)
        header_row.addWidget(title)
        header_row.addStretch()
        box_layout.addLayout(header_row)

        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {_th.border_color(0.07)}; border: none;")
        box_layout.addWidget(div)

        # scroll
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.12)}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.accent(0.45)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0; background: transparent;
            }}
        """)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self._container)
        self.list_layout.setSpacing(6)
        self.list_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(self._container)
        box_layout.addWidget(scroll, 1)

        div2 = QWidget()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background: {_th.border_color(0.07)}; border: none;")
        box_layout.addWidget(div2)

        footer = QHBoxLayout()
        footer.addStretch()
        btn_close = QPushButton(t("dep_btn_close"))
        btn_close.setFixedHeight(30)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(_ss_neutral())
        btn_close.clicked.connect(self.reject)
        footer.addWidget(btn_close)
        box_layout.addLayout(footer)

        self._show_loading()
        self._start_fetch()

    def _show_loading(self):
        self._clear_layout(self.list_layout)

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(wrapper)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(12)

        self.spinner = _Spinner(size=28)
        v.addWidget(self.spinner, alignment=Qt.AlignCenter)

        lbl = QLabel(t("dep_fetching"))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 12px; background: transparent; border: none;")
        v.addWidget(lbl)

        self.list_layout.addWidget(wrapper)

    def _start_fetch(self):
        self.fetch_done = False

        # Batalkan request sebelumnya kalau masih pending
        if self._reply is not None:
            try:
                self._reply.abort()
                self._reply.deleteLater()
            except Exception:
                pass
            self._reply = None

        if hasattr(self, "fetch_timeout"):
            self.fetch_timeout.stop()

        # QNetworkAccessManager — async, no QThread, no blocking
        req = QNetworkRequest(QUrl(REMOTE_REGISTRY_URL))
        req.setRawHeader(b"Cache-Control", b"no-cache")
        req.setRawHeader(b"Pragma", b"no-cache")
        self._reply = self._nam.get(req)
        self._reply.finished.connect(self._on_network_reply)

        self.fetch_timeout = QTimer(self)
        self.fetch_timeout.setSingleShot(True)
        self.fetch_timeout.timeout.connect(self._on_registry_failed)
        self.fetch_timeout.start(6000)

    def _on_network_reply(self):
        reply = self._reply
        self._reply = None

        if self.fetch_done:
            if reply:
                reply.deleteLater()
            return

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise RuntimeError(reply.errorString())
            data = reply.readAll().data()
            registry = __import__("json").loads(data.decode("utf-8"))
            if not registry:
                raise RuntimeError("empty registry")
            self._on_registry_ready(registry)
        except Exception:
            self._on_registry_failed()
        finally:
            if reply:
                reply.deleteLater()

    def _on_registry_ready(self, registry):
        if self.fetch_done:
            return
        self.fetch_done = True
        self.fetch_timeout.stop()
        if hasattr(self, "spinner"):
            self.spinner.stop()

        self.manager = DependencyManager()
        self.manager.registry = registry

        self._clear_layout(self.list_layout)
        self._build_list(self.list_layout)

    def _on_registry_failed(self):
        if self.fetch_done:
            return
        self.fetch_done = True
        if hasattr(self, "spinner"):
            self.spinner.stop()

        self.manager = DependencyManager(allow_remote=False)
        packages = self.manager.list_packages()

        self._clear_layout(self.list_layout)

        if not packages:
            self._build_error_state(self.list_layout)
        else:
            self._build_list(self.list_layout)

    def _build_error_state(self, layout):
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        v = QVBoxLayout(wrapper)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(14)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(QIcon(icon("not_found")).pixmap(52, 52))
        icon_lbl.setAlignment(Qt.AlignCenter)

        text = QLabel(t("dep_fetch_error"))
        text.setAlignment(Qt.AlignCenter)
        text.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 12px; background: transparent; border: none;")
        text.setWordWrap(True)

        btn = QPushButton(t("dep_btn_retry"))
        btn.setFixedWidth(100)
        btn.setFixedHeight(30)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(_ss_neutral())
        btn.clicked.connect(self._reload_packages)

        v.addWidget(icon_lbl)
        v.addWidget(text)
        v.addWidget(btn, alignment=Qt.AlignCenter)

        layout.addWidget(wrapper)

    def _build_empty_state(self, layout):
        lbl = QLabel(t("dep_no_packages"))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 12px; background: transparent; border: none;")
        layout.addWidget(lbl)

    def _reload_packages(self):
        self._show_loading()
        self._start_fetch()

    def _build_list(self, layout):
        packages = self.manager.list_packages()

        if not packages:
            self._build_empty_state(layout)
            return

        card = QWidget()
        card.setObjectName("depCard")
        card.setStyleSheet(f"""
            QWidget#depCard {{
                background: {_th.border_color(0.03)};
                border: 1px solid {_th.border_color(0.07)};
                border-radius: 10px;
            }}
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 6, 0, 6)
        card_layout.setSpacing(0)

        for i, pkg in enumerate(packages):
            card_layout.addWidget(self._build_row(pkg))

            if i != len(packages) - 1:
                sep = QWidget()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {_th.border_color(0.05)}; border: none;")
                card_layout.addWidget(sep)

        layout.addWidget(card)


        self._build_wsl_section(layout)

        layout.addStretch(1)

    def _build_wsl_section(self, layout):
        cap = QLabel(t("dep_wsl_section"))
        cap.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {_th.FG_DESC}; "
            f"background: transparent; border: none; margin-top: 8px;")
        layout.addWidget(cap)

        card = QWidget()
        card.setObjectName("wslCard")
        card.setStyleSheet(f"""
            QWidget#wslCard {{
                background: {_th.border_color(0.03)};
                border: 1px solid {_th.border_color(0.07)};
                border-radius: 10px;
            }}
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 6, 0, 6)
        cl.setSpacing(0)

        self._wsl_row_holder = QWidget()
        self._wsl_row_holder.setStyleSheet("background: transparent;")
        QVBoxLayout(self._wsl_row_holder).setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._wsl_row_holder)

        layout.addWidget(card)

        # render a "checking…" placeholder, then probe in the background
        self._set_wsl_row(self._make_wsl_row(checking=True))
        self._start_wsl_detect()

    def _make_wsl_row(self, info=None, checking=False, busy=False):
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        outer = QHBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if checking:
            accent = _th.border_color(0.20)
            icon_name = "package"
            detail = t("dep_wsl_checking")
            action = None
        elif info and info.get("installed"):
            accent = "#7ec699"
            icon_name = "check"
            detail = info.get("detail") or t("dep_wsl_installed")
            action = None
        elif info and info.get("present"):
            # feature files exist but component disabled → still offer install
            accent = "#e5c07b"
            icon_name = "warning"
            detail = info.get("detail") or t("dep_wsl_disabled")
            action = t("dep_btn_install")
        else:
            accent = _th.border_color(0.20)
            icon_name = "download"
            detail = (info or {}).get("detail") or t("dep_wsl_not_installed")
            action = t("dep_btn_install")

        bar = QWidget()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {accent}; border-radius: 2px; "
                          f"margin: 8px 0 8px 10px;")
        outer.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c = QHBoxLayout(content)
        c.setContentsMargins(10, 8, 12, 8)
        c.setSpacing(10)

        ic_lbl = QLabel()
        ic_lbl.setPixmap(QIcon(icon(icon_name)).pixmap(15, 15))
        ic_lbl.setFixedSize(17, 17)
        ic_lbl.setAlignment(Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name_lbl = QLabel(t("dep_wsl_name"))
        name_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_th.FG}; background: transparent; border: none;")
        desc_lbl = QLabel(detail)
        desc_lbl.setStyleSheet(f"font-size: 11px; color: {_th.FG_MUTED}; background: transparent; border: none;")
        desc_lbl.setWordWrap(True)
        text_col.addWidget(name_lbl)
        text_col.addWidget(desc_lbl)

        c.addWidget(ic_lbl)
        c.addLayout(text_col, 1)
        c.addStretch()

        if busy:
            lbl = QLabel(t("dep_installing"))
            lbl.setStyleSheet(f"font-size: 11px; color: {_th.FG_MUTED}; padding: 0 4px; background: transparent; border: none;")
            c.addWidget(lbl)
        elif action:
            btn = QPushButton(action)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(max(80, len(action) * 9))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_ss_action())
            btn.clicked.connect(self._on_wsl_install)
            c.addWidget(btn)

        outer.addWidget(content)
        return widget

    def _set_wsl_row(self, row):
        holder_layout = self._wsl_row_holder.layout()
        while holder_layout.count():
            item = holder_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        holder_layout.addWidget(row)

    def _start_wsl_detect(self):
        self._wsl_detect = WslDetectWorker()
        self._wsl_detect.finished.connect(self._on_wsl_detected)
        self._wsl_detect.start()

    def _on_wsl_detected(self, info):
        self._wsl_info = info
        self._set_wsl_row(self._make_wsl_row(info=info))

    def _on_wsl_install(self):
        self._set_wsl_row(self._make_wsl_row(info=getattr(self, "_wsl_info", None), busy=True))
        self._wsl_install = WslInstallWorker()
        self._wsl_install.finished.connect(self._on_wsl_install_finished)
        self._wsl_install.start()

    def _on_wsl_install_finished(self, ok, msg):
        if ok:
            # re-probe so the row reflects the new state (often "reboot needed")
            self._start_wsl_detect()
        else:
            info = dict(getattr(self, "_wsl_info", None) or {})
            info["detail"] = msg or t("dep_wsl_install_failed")
            self._set_wsl_row(self._make_wsl_row(info=info))

    def _build_row(self, pkg):
        widget = QWidget()
        widget.setStyleSheet("background: transparent;")

        outer = QHBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        state = pkg["state"]

        if state == "installed":
            accent = "#7ec699"
            icon_name = "check"
            state_text = t("dep_status_installed")
            action_enabled = False
            action_text = t("dep_status_installed")
        elif state == "anomaly":
            accent = "#e5c07b"
            icon_name = "warning"
            state_text = t("dep_status_anomaly")
            action_enabled = True
            action_text = t("dep_btn_redownload")
        elif state == "update_available":
            accent = "#61afef"
            icon_name = "update"
            state_text = t("dep_status_update")
            action_enabled = True
            action_text = t("dep_btn_update")
        else:
            accent = _th.border_color(0.20)
            icon_name = "download"
            state_text = t("dep_status_not_installed")
            action_enabled = True
            action_text = t("dep_btn_install")

        bar = QWidget()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"""
            background: {accent};
            border-radius: 2px;
            margin-top: 8px;
            margin-bottom: 8px;
            margin-left: 10px;
        """)
        outer.addWidget(bar)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(10, 8, 12, 8)
        cl.setSpacing(10)

        ic_lbl = QLabel()
        ic_lbl.setPixmap(QIcon(icon(icon_name)).pixmap(15, 15))
        ic_lbl.setFixedSize(17, 17)
        ic_lbl.setAlignment(Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)

        name_lbl = QLabel(pkg["display"])
        name_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_th.FG}; background: transparent; border: none;")

        author_lbl = QLabel(pkg.get("author") or t("dep_unknown_author"))
        author_lbl.setStyleSheet(f"font-size: 11px; color: {_th.FG_DESC}; background: transparent; border: none;")

        ver_lbl = QLabel(f'v{pkg["version"]} · {state_text}')
        ver_lbl.setStyleSheet(f"font-size: 11px; color: {_th.FG_MUTED}; background: transparent; border: none;")

        text_col.addWidget(name_lbl)
        text_col.addWidget(author_lbl)
        text_col.addWidget(ver_lbl)

        license_name = pkg.get("license")
        if license_name:
            lic_color = "#e5c07b" if license_name.startswith("GPL") else _th.FG_MUTED
            lic_lbl = QLabel(license_name)
            lic_lbl.setStyleSheet(f"font-size: 10px; color: {lic_color}; background: transparent; border: none;")
            text_col.addWidget(lic_lbl)

        cl.addWidget(ic_lbl)
        cl.addLayout(text_col)
        cl.addStretch()

        if action_enabled:
            action_btn = QPushButton(action_text)
            action_btn.setFixedHeight(26)
            action_btn.setMinimumWidth(max(80, len(action_text) * 9))
            action_btn.setCursor(Qt.PointingHandCursor)
            action_btn.setStyleSheet(_ss_action())
            action_btn.clicked.connect(lambda _, p=pkg, b=action_btn: self._on_action(p, b))
            cl.addWidget(action_btn)
            widget._action_btn = action_btn
        else:
            done_lbl = QLabel(action_text)
            done_lbl.setStyleSheet(f"font-size: 11px; color: {_th.FG_MUTED}; padding: 0 4px; background: transparent; border: none;")
            cl.addWidget(done_lbl)

        outer.addWidget(content)
        widget._pkg = pkg
        self.pkg_widgets[pkg["name"]] = widget
        return widget

    def _on_action(self, pkg, btn):
        btn.setText(t("dep_installing"))
        btn.setEnabled(False)

        worker = InstallWorker(self.manager, pkg["name"])
        self.workers.append(worker)
        worker.finished.connect(
            lambda ok, err, w=worker, b=btn: self._on_install_finished(ok, err, b, w)
        )
        worker.start()

    def _on_install_finished(self, success, error, btn, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater()

        pkg_name = worker.pkg_name
        widget = self.pkg_widgets.get(pkg_name)
        if not widget:
            return

        if not success:
            btn.setText(t("dep_btn_try_again"))
            btn.setEnabled(True)
            btn.setStyleSheet(_ss_action_error())
            return

        pkg = next(p for p in self.manager.list_packages() if p["name"] == pkg_name)
        new_widget = self._build_row(pkg)

        layout = widget.parent().layout()
        idx = layout.indexOf(widget)
        widget.deleteLater()
        layout.insertWidget(idx, new_widget)
        self.pkg_widgets[pkg_name] = new_widget

    def closeEvent(self, event):
        # Abort pending network request (no QThread → no wait needed)
        try:
            self.fetch_timeout.stop()
        except Exception:
            pass
        if self._reply is not None:
            try:
                self._reply.abort()
                self._reply.deleteLater()
            except Exception:
                pass
            self._reply = None
        self.fetch_done = True  # cegah callback fire setelah close
        # Stop install workers (ini tetap QThread)
        for w in list(self.workers):
            if w.isRunning():
                try:
                    w.finished.disconnect()
                except Exception:
                    pass
                w.quit()
                w.wait(300)
        self.workers.clear()


        for attr in ("_wsl_detect", "_wsl_install"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.finished.disconnect()
                except Exception:
                    pass
                if w.isRunning():
                    w.wait(300)
        super().closeEvent(event)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
