# SPDX-License-Identifier: MPL-2.0
import os, sys, json, subprocess, time


from multiprocessing import freeze_support
freeze_support()


if "--rak-cli" in sys.argv[1:2]:
    if sys.platform == "win32":
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            ctypes.windll.kernel32.AllocConsole()
        for _stream_name in ("stdin", "stdout", "stderr"):
            try:
                setattr(sys, _stream_name, open("CON" + ("IN$" if _stream_name == "stdin" else "OUT$"), "r" if _stream_name == "stdin" else "w", encoding="utf-8", errors="replace"))
            except OSError:
                pass
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    from core.rak_cli import _main_cli
    _main_cli()
    raise SystemExit(0)


import traceback, logging, threading as _threading

_LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "RennsAndroidKitchen", "logs",
)
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_LOG_DIR, "crash.log"),
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Native Qt/access-violation crashes bypass sys.excepthook. Keep a file handle
# alive so those failures include all Python thread stacks in the next report.
_NATIVE_CRASH_LOG = None
try:
    import faulthandler
    if not faulthandler.is_enabled():
        _NATIVE_CRASH_LOG = open(os.path.join(_LOG_DIR, "native_crash.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_NATIVE_CRASH_LOG, all_threads=True)
except Exception:
    logging.exception("Could not initialize native crash logging")

# Concurrent first imports have crashed CPython 3.14/PySide6 6.10.1 during GC
# (0xC0000005). Serialize imports and defer GC during startup; see
# core/import_gate.py.
try:
    from core.import_gate import (
        install as _install_import_gate,
        release as _release_import_gate,
    )
    _install_import_gate()
except Exception:
    _release_import_gate = None
    logging.exception("Could not install the startup import gate")

import gc as _gc

_startup_gc_hold = [False]
try:
    if _gc.isenabled():
        _gc.disable()
        _startup_gc_hold[0] = True
except Exception:
    logging.exception("Could not pause the garbage collector for startup")


def _end_startup_import_window():
    """Resume the GC and restore the stock __import__ once startup is done.

    Idempotent: safe to call from the reveal signal and from the fallback
    timer, whichever fires first.
    """
    if _startup_gc_hold[0]:
        _startup_gc_hold[0] = False
        try:
            _gc.enable()
        except Exception:
            logging.exception("Could not resume the garbage collector")
    if _release_import_gate is not None:
        try:
            _release_import_gate()
        except Exception:
            logging.exception("Could not release the startup import gate")


# Fallback for spawn children and startup paths that never emit startup_revealed.
try:
    _gate_watchdog = _threading.Timer(15.0, _end_startup_import_window)
    _gate_watchdog.daemon = True
    _gate_watchdog.start()
except Exception:
    logging.exception("Could not arm the startup import-window watchdog")


def _queue_crash_dialog(text):
    try:
        from core.error_reporting import report_fatal
        report_fatal(text)
    except Exception:
        logging.exception("Could not queue the crash dialog")


def _global_excepthook(exc_type, exc_value, exc_tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.critical("UNCAUGHT EXCEPTION:\n%s", text)
    print(f"[FATAL] {text}", flush=True)
    # Let the failing Qt callback unwind before constructing any error widgets.
    _queue_crash_dialog(text)

sys.excepthook = _global_excepthook

def _thread_excepthook(args):
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    logging.critical("UNCAUGHT THREAD EXCEPTION (%s):\n%s", args.thread, text)
    print(f"[FATAL-THREAD] {text}", flush=True)
    _queue_crash_dialog(f"Thread: {getattr(args.thread, 'name', 'unknown')}\n\n{text}")

_threading.excepthook = _thread_excepthook


def _unraisable_excepthook(args):
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    text = f"{args.err_msg or 'Unhandled cleanup exception'}\n\n{text}"
    logging.critical("UNRAISABLE EXCEPTION:\n%s", text)
    print(f"[FATAL] {text}", flush=True)
    _queue_crash_dialog(text)


sys.unraisablehook = _unraisable_excepthook


if sys.platform != "win32":
    _chromium_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    _extra = "--disable-gpu --no-sandbox --disable-software-rasterizer"
    if _extra not in _chromium_flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{_chromium_flags} {_extra}".strip()

# Qt must read these settings before its first import.
from core.fonts import configure_text_rendering_env
configure_text_rendering_env()

_EARLY_APP = None
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication as _EarlyQApplication
    _EARLY_APP = _EarlyQApplication(sys.argv)
    _EARLY_APP.setQuitOnLastWindowClosed(False)
    from core.error_reporting import install_error_reporter
    install_error_reporter()

from core.app_paths import (
    APP_ROOT,
    APP_DIR,
    BIN_DIR,
    PROJECT_DATA_DIR,
    ensure_app_layout,
)
from core.project_lifecycle import log_project_event
from core.project_trash import ProjectTrash, TRASH_KEY
BASE_DIR = os.path.dirname(APP_ROOT)
PROJECT_DIR = PROJECT_DATA_DIR
PROJECT_JSON = os.path.join(PROJECT_DIR, "projects.json")
LOCATION_CFG = os.path.join(PROJECT_DIR, "default_location.json")
ensure_app_layout()

from PySide6.QtCore import QEvent, QEventLoop, QSignalBlocker
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QListWidget,
    QListWidgetItem, QStackedWidget, QRadioButton,
    QGroupBox, QFormLayout, QScrollArea, QSizePolicy,
    QFrame, QDialog, QStackedLayout, QGridLayout, QMenu, QProgressBar, QBoxLayout,
    QGraphicsEffect, QGraphicsOpacityEffect, QStyledItemDelegate, QAbstractButton,
)
from PySide6.QtGui import QTransform, QIcon, QPixmap, QPainter, QColor, QCursor
from shiboken6 import isValid
from core.theme_binding import bind_style, bind_icon, bind_pixmap, refresh_theme, with_alpha
from core.language_binding import bind_text, bind_language, refresh_language
from PySide6.QtCore import (
    Qt, QSize, QRect, QRectF, QPoint, QTimer, QPropertyAnimation, QEasingCurve,
    QThread, Signal, Slot, Property, QSequentialAnimationGroup, QPauseAnimation, QVariantAnimation,
)
from core.flag import (
    APP_NAME, APP_VERSION, NEOCAT_LABEL, edition_label, window_title,
)
import shutil
import urllib
from app_ext.class_SmoothListWidget import SmoothListWidget
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from app_ext.class_LoadingWindow import LoadingWindow
from app_ext.class_StartupVisual import StartupVisual, StartupContentReveal
from app_ext.class_StartupTransition import StartupTransition, DriveUsageTiming
from app_ext.class_ChoiceDropdown import ChoiceDropdown, show_choice_popup
from app_ext.class_RomModeButton import RomModeButton, bind_rom_mode_button
from app_ext.class_AppPageTransition import AppPageTransition
from app_ext.class_WindowContentReveal import WindowContentReveal
from app_ext.class_FooterActionTransition import FooterActionTransition
from app_ext.class_ButtonPressAnimation import (
    PressScaleEffect, PressAnimatedButton, install_button_press_animations,
)
from app_ext.class_ProjectLayoutTransition import ProjectLayoutTransition
from app_ext.class_DiskCardsTransition import DiskCardsTransition
from app_ext.class_ProjectCardsTransition import ProjectCardsTransition
from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, BlurCard
from app_ext.class_MainLoader import MainLoader
from main_ext.class_AppDialog import AppDialog
from core.ui_lang import t, init as _lang_init, set_lang, SUPPORTED
from core.wsl_manager import project_storage_key, load_disk_usage_cache
import core.ui_lang as _ul
_lang_init()

print("[PRIORITY_STAGE 0] LAUNCHER LOAD")


class DeleteProjectWorker(QThread):
    done = Signal(bool, str, str)

    def __init__(self, project: dict, trash: ProjectTrash):
        super().__init__()
        self.project = project
        self.path = project["path"]
        self.trash = trash

    def run(self):
        try:
            self.trash.recycle(self.project)
            self.done.emit(True, self.path, "")
        except Exception as e:
            self.done.emit(False, self.path, str(e))


class ProjectTrashWorker(QThread):
    done = Signal(bool, object, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit(True, self.operation(), "")
        except Exception as exc:
            self.done.emit(False, None, str(exc))


class ProjectSizeScannerWorker(QThread):

    size_calculated = Signal(str, str)  # (project_path, formatted_size)

    def __init__(self, paths: list):
        super().__init__()
        self.paths = paths

    def run(self):
        for path in self.paths:
            if self.isInterruptionRequested():
                break
            size_str = AppUI._get_folder_size_str(path, check_interrupted=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                break
            if size_str is not None:
                self.size_calculated.emit(path, size_str)


def ensure_dirs():
    os.makedirs(PROJECT_DIR, exist_ok=True)
    if not os.path.exists(PROJECT_JSON):
        try:
            with open(PROJECT_JSON, "w", encoding="utf-8") as f:
                f.write("[]")
        except Exception:
            pass

def _load_projects_json():
    ensure_dirs()
    try:
        with open(PROJECT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def _save_projects_json(data):
    ensure_dirs()
    try:
        with open(PROJECT_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def _load_default_location():
    try:
        ensure_dirs()
        with open(LOCATION_CFG, "r", encoding="utf-8") as f:
            path = json.load(f).get("path", "")
    except Exception:
        path = ""

    if path and os.path.isabs(path):
        return os.path.normpath(path)
    return ""

def _system_documents_dir():
    return os.path.join(os.path.expanduser("~"), "Documents")

def _resolve_default_location():
    return _load_default_location() or _system_documents_dir()

def _resolve_create_base_dir(label_text: str):
    base_dir = (label_text or "").strip()
    if base_dir and os.path.isabs(base_dir):
        return os.path.normpath(base_dir)
    return _resolve_default_location()

def _save_default_location(path: str):
    try:
        ensure_dirs()
        with open(LOCATION_CFG, "w", encoding="utf-8") as f:
            json.dump({"path": path}, f)
    except Exception:
        pass

ensure_dirs()


from core.cache import ensure_cache


def _startup_background_init():
    try:
        from core.neocat_load import ensure_neocat
        ensure_neocat()
    except Exception as e:
        print(f"[startup-bg] ensure_neocat error: {e}")
    try:
        from core.mount_temp import cleanup_all as _mount_temp_cleanup
        _mount_temp_cleanup()
    except Exception as e:
        print(f"[startup-bg] mount cleanup error: {e}")

import threading as _threading
_threading.Thread(
    target=_startup_background_init,
    name="rak-startup-bg",
    daemon=True,
).start()
from icons.handler import icon, icon_pixmap, tint_icon, create_themed_icon
from core.rennsproject_bridge import AppFeatures

def _clamp_popup(popup, global_pos, root_window):

    from PySide6.QtCore import QPoint
    hint = popup.sizeHint()
    pw = hint.width()  if hint.width()  > 0 else popup.width()
    ph = hint.height() if hint.height() > 0 else popup.height()
    gx, gy = global_pos.x(), global_pos.y()
    if root_window:
        tl = root_window.mapToGlobal(root_window.rect().topLeft())
        br = root_window.mapToGlobal(root_window.rect().bottomRight())
        x = gx if gx + pw <= br.x() else max(tl.x(), br.x() - pw)
        y = gy if gy + ph <= br.y() else max(tl.y(), br.y() - ph)
    else:
        x, y = gx, gy
    popup.move(QPoint(x, y))


print("[PRIORITY_STAGE 0] LAUNCHER LOAD - LOCAL CACHE")


class _ThemeSnapshotOverlay(QWidget):


    def __init__(self, owner, pixmap, bounds):
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus |
                         Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setGeometry(bounds)
        self._pixmap = pixmap

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()


class _ScrollingPathLabel(QLabel):


    def __init__(self, path: str, color: str):
        super().__init__(path)
        self._color_factory = color if callable(color) else lambda: color
        bind_style(self, lambda: f"font-size: 10px; color: {self._color_factory()};")
        self.setToolTip(path)
        self._text_color = QColor(self._color_factory())
        self._offset = 0
        self._direction = 1
        self._pause_ticks = 16
        self._width_cache = None
        from core.visible_label_clock import VisibleLabelClock
        self._label_clock = VisibleLabelClock.instance()

    def _refresh_theme(self):
        self._text_color = QColor(self._color_factory())

    def _max_offset(self) -> int:
        key = (self.text(), self.font().key())
        if self._width_cache is None or self._width_cache[0] != key:
            self._width_cache = (key, self.fontMetrics().horizontalAdvance(self.text()))
        return max(0, self._width_cache[1] - self.width() + 2)

    def _update_scroll_state(self):
        if self.isVisible() and self._max_offset() > 0:
            self._label_clock.track(self)
        else:
            self._offset = 0
            self._direction = 1
            self._pause_ticks = 16
        self.update()

    def _advance_scroll(self):
        maximum = self._max_offset()
        if maximum == 0:
            self._update_scroll_state()
            return
        if self._pause_ticks:
            self._pause_ticks -= 1
            return
        self._offset += self._direction * 2
        if self._offset >= maximum:
            self._offset = maximum
            self._direction = -1
            self._pause_ticks = 16
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause_ticks = 16
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scroll_state()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_scroll_state()

    def hideEvent(self, event):
        super().hideEvent(event)

    def paintEvent(self, event):
        if self._max_offset() == 0:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.setPen(self._text_color)
        painter.setFont(self.font())
        metrics = self.fontMetrics()
        baseline = (self.height() - metrics.height()) // 2 + metrics.ascent()
        painter.drawText(-self._offset, baseline, self.text())
        painter.end()


class _ElidedProjectLabel(QLabel):


    def __init__(self, text):
        super().__init__(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment() | Qt.TextSingleLine, text)
        painter.end()


class _ResponsiveDriveUsageLabel(QLabel):


    def __init__(self, used_gb: float, total_gb: float):
        super().__init__()
        self._used_gb = used_gb
        self._total_gb = total_gb
        self._final_variants = self._usage_variants(used_gb)
        self._variants = self._final_variants
        self.setText(self._variants[0])
        self.setToolTip(f"{used_gb:.1f} GB / {total_gb:.1f} GB")

    def set_usage_values(self, used_gb, total_gb):
        self._used_gb, self._total_gb = used_gb, total_gb
        self._final_variants = self._usage_variants(used_gb)
        self._variants = self._final_variants
        self.setToolTip(f"{used_gb:.1f} GB / {total_gb:.1f} GB")
        self._update_display()

    def _usage_variants(self, used_gb):
        return (
            f"{used_gb:.0f}/{self._total_gb:.0f} GB",
            f"{used_gb:.0f}/{self._total_gb:.0f}",
        )

    def set_usage_progress(self, progress):
        variants = self._usage_variants(self._used_gb * progress)
        if variants != self._variants:
            self._variants = variants
            self._update_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def _update_display(self):
        metrics = self.fontMetrics()
        width = self.contentsRect().width()
        # Choose a format using the final value's width so counting digits
        # never switches formats mid-animation.
        display = next(
            (self._variants[index] for index, value in enumerate(self._final_variants)
             if metrics.horizontalAdvance(value) <= width),
            metrics.elidedText(self._variants[-1], Qt.ElideRight, width),
        )
        if self.text() != display:
            self.setText(display)


class _ProjectDrivePressEffect(PressScaleEffect):
    def __init__(self, parent):
        super().__init__(parent)
        self.setEnabled(False)


class _ProjectDriveCard(QWidget):


    clicked = Signal(str)

    def __init__(self, drive, parent=None):
        super().__init__(parent)
        self.drive = drive
        self._pressed = False
        self._press_started = 0.0
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(f"Filter projects on {drive}")
        self._press_effect = _ProjectDrivePressEffect(self)
        self.setGraphicsEffect(self._press_effect)
        self._press_animation = QPropertyAnimation(self._press_effect, b"scale", self)
        self._press_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._press_animation.finished.connect(
            lambda: self._press_effect.setEnabled(self._press_effect.scale != 1.0)
        )
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(lambda: self._animate_press(1.0))

    def _animate_press(self, target):
        from main_ext.class_ThemeUi import theme as _th
        self._press_animation.stop()
        if not _th.animations_enabled:
            self._press_effect.scale = 1.0
            self._press_effect.setEnabled(False)
            return
        self._press_effect.setEnabled(True)
        self._press_animation.setStartValue(self._press_effect.scale)
        self._press_animation.setEndValue(target)
        self._press_animation.setDuration(110 if target < 1.0 else 170)
        self._press_animation.start()

    def _release_press(self):
        from main_ext.class_ThemeUi import theme as _th

        if not _th.animations_enabled:
            self._animate_press(1.0)
            return
        elapsed_ms = int((time.monotonic() - self._press_started) * 1000)
        remaining_ms = max(0, 120 - elapsed_ms)
        if remaining_ms:
            self._release_timer.start(remaining_ms)
        else:
            self._animate_press(1.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._release_timer.stop()
            self._press_started = time.monotonic()
            self._animate_press(0.96)
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self._release_press()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.drive)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and not event.isAutoRepeat():
            self._pressed = True
            self._release_timer.stop()
            self._press_started = time.monotonic()
            self._animate_press(0.96)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self._pressed:
            self._pressed = False
            self._release_press()
            self.clicked.emit(self.drive)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def hideEvent(self, event):
        self._pressed = False
        self._release_timer.stop()
        self._press_animation.stop()
        self._press_effect.scale = 1.0
        super().hideEvent(event)


class _ProjectCardWidget(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._open_button = None
        self._card_hovered = False
        self._card_pressed = False
        self._hover_callback = None
        self._press_callback = None
        self._hover_check = QTimer(self)
        self._hover_check.setSingleShot(True)
        self._hover_check.timeout.connect(self._sync_hover)

    def set_open_button(self, button):
        self._open_button = button
        if button is not None:
            button.setProperty("cardHovered", False)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def set_hover_callback(self, callback):
        self._hover_callback = callback

    def set_press_callback(self, callback):
        self._press_callback = callback

    def _set_card_pressed(self, pressed):
        if self._card_pressed == pressed:
            return
        self._card_pressed = pressed
        if self._press_callback is not None:
            self._press_callback(pressed)

    def _set_card_hovered(self, hovered):
        if self._card_hovered == hovered:
            return
        self._card_hovered = hovered
        button = self._open_button
        if button is not None:
            button.setProperty("cardHovered", hovered)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        if self._hover_callback is not None:
            self._hover_callback(hovered)

    def _sync_hover(self):
        inside = self.isVisible() and self.rect().contains(
            self.mapFromGlobal(QCursor.pos())
        )
        self._set_card_hovered(inside)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._set_card_hovered(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_check.start(0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_card_pressed(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_card_pressed(False)
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Enter:
            self._set_card_hovered(True)
        elif kind == QEvent.Leave:
            self._hover_check.start(0)
        elif (kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton
              and not isinstance(watched, QAbstractButton)):
            self._set_card_pressed(True)
        elif (kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
              and not isinstance(watched, QAbstractButton)):
            self._set_card_pressed(False)
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        self._hover_check.stop()
        self._card_pressed = False
        self._set_card_hovered(False)
        super().hideEvent(event)


_PROJECT_REMOVING_ROLE = Qt.UserRole + 1
_PROJECT_SEARCH_ROLE = Qt.UserRole + 2
_PROJECT_DRIVE_ROLE = Qt.UserRole + 3


class _ProjectListItem(QListWidgetItem):
    def __lt__(self, other):
        return self._project_order < other._project_order


class _ProjectCardDelegate(QStyledItemDelegate):


    def paint(self, painter, option, index):
        if index.data(_PROJECT_REMOVING_ROLE):
            return
        super().paint(painter, option, index)


from app_ext.class_ProjectHoverCanvas import ProjectHoverCanvas as _ProjectHoverEffect

class _ProjectDeleteIndicator(QWidget):


    def __init__(self, project_list, item, color):
        super().__init__(project_list.viewport())
        self.project_list = project_list
        self.item = item
        self.color = QColor(color)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(project_list.viewport().rect())
        project_list.viewport().installEventFilter(self)
        self._started = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        rect = self.project_list.visualItemRect(self.item)
        if not rect.isValid() or not rect.intersects(self.rect()):
            return
        left = rect.left() + 12
        width = max(0, rect.width() - 24)
        if width == 0:
            return
        # The card has an 8 px bottom margin in the item stylesheet.
        y = rect.bottom() - 5
        phase = ((time.monotonic() - self._started) % 1.3) / 1.3
        segment = max(24.0, width * 0.28)
        travel = width + segment
        segment_left = left - segment + phase * travel
        visible_left = max(left, segment_left)
        visible_right = min(left + width, segment_left + segment)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QColor(self.color)
        track.setAlpha(45)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(left, y, width, 3), 1.5, 1.5)
        if visible_right > visible_left:
            painter.setBrush(self.color)
            painter.drawRoundedRect(
                QRectF(visible_left, y, visible_right - visible_left, 3), 1.5, 1.5
            )

    def stop(self):
        self._timer.stop()
        self.project_list.viewport().removeEventFilter(self)
        self.hide()
        self.deleteLater()


def _snapshot_project_row(project_list, item):

    viewport = project_list.viewport()
    original_rect = project_list.visualItemRect(item)
    scrollbar = project_list.verticalScrollBar()
    original_scroll = scrollbar.value()
    try:
        if not viewport.rect().contains(original_rect):
            # This runs within one UI callback: restore the scroll position
            # before Qt paints the next on-screen frame.
            project_list.scrollToItem(item)
        pixmap = viewport.grab(project_list.visualItemRect(item))
    finally:
        if scrollbar.value() != original_scroll:
            scrollbar.setValue(original_scroll)
    return original_rect, pixmap


class _StartupSplash(StartupVisual):


    def __init__(self, parent):
        super().__init__(parent)
        self.opacity_effect = None


class AppUI(AppFeatures, QWidget):
    official_authorization_finished = Signal(bool)
    startup_cache_ready = Signal(object)
    startup_built = Signal()
    startup_animation_started = Signal()
    startup_revealed = Signal()
    wsl_disk_usage_ready = Signal(object)
    windows_disk_usage_ready = Signal(object)

    def __init__(self, offscreen_mode: bool = False, startup_splash: bool = False):
        super().__init__()
        from core.error_reporting import install_error_reporter
        install_error_reporter()
        install_button_press_animations(QApplication.instance())
        from core.skia_widgets import install_widget_gpu_rendering
        install_widget_gpu_rendering(QApplication.instance())
        self._all_projects = []
        self._size_scanner = None
        self._size_scanners = set()
        self._project_delete_active = False
        self._project_delete_indicator = None
        self._project_cards_transition = None
        self._pending_project_removal = None
        self._trash = ProjectTrash()
        self._trash_view = False
        self._trash_drive_layout = None
        self._trash_entries = self._read_trash_entries()
        self._trash_workers = set()
        self._trash_maintenance_running = False
        self._trash_timer = QTimer(self)
        self._trash_timer.setInterval(60 * 1000)
        self._trash_timer.timeout.connect(self._maintain_project_trash)
        self._init_authorization()
        # Offscreen shell dipakai benchmark UI tanpa listener/timer/network.
        self._offscreen_mode = offscreen_mode
        self.resize(1080, 700)
        self.setMinimumSize(550, 400)
        self._startup_splash = None
        self._startup_fade = None
        self._startup_reveal_started = False
        self._startup_content_reveal = None
        self._startup_transition = None
        self._startup_revealed_emitted = False
        self._startup_ui_built = False
        self._startup_builder = None
        self._startup_build_timer = QTimer(self)
        self._startup_build_timer.setSingleShot(True)
        self._startup_build_timer.timeout.connect(self._advance_startup_build)
        self._startup_rows = None
        self._startup_rows_timer = QTimer(self)
        self._startup_rows_timer.setSingleShot(True)
        self._startup_rows_timer.timeout.connect(self._build_startup_project_rows)
        self._startup_paint_widgets = None
        self._startup_prepare_timer = QTimer(self)
        self._startup_prepare_timer.setSingleShot(True)
        self._startup_prepare_timer.timeout.connect(self._prepare_startup_paints)
        self._page_content_reveal = None
        self._footer_action_transition = None
        self._project_drive_usage_animation = None
        self._project_layout_transition = None
        self._project_layout_animation_ready = False
        self._disk_cards_transition = None
        self._windows_disk_scan_running = False
        self._windows_disk_usage = None
        self._pending_windows_disk_usage = None
        self._unavailable_project_drives = set()
        self._normal_drive_colors = {}
        self.windows_disk_usage_ready.connect(self._on_windows_disk_usage_ready)
        self._disk_poll_timer = QTimer(self)
        self._disk_poll_timer.setInterval(1500)
        self._disk_poll_timer.timeout.connect(self._refresh_windows_disks)
        self._wsl_disk_usage = load_disk_usage_cache() if not offscreen_mode else []
        self._wsl_disk_ui_ready = False
        self._wsl_disk_scan_running = False
        self._wsl_disk_last_scan = None
        self._wsl_disk_pending = False
        self._wsl_disk_closed = False
        self.wsl_disk_usage_ready.connect(self._on_wsl_disk_usage_ready)
        if not offscreen_mode:
            self._refresh_wsl_disks()
            self._refresh_windows_disks()
            self._disk_poll_timer.start()
        if startup_splash and not offscreen_mode:
            self.startup_cache_ready.connect(self._on_startup_cache_ready)
            bind_language(self, "setWindowTitle", window_title)
            self.setWindowIcon(QIcon(icon("ic_launcher")))
            self._startup_splash = _StartupSplash(self)
            self._startup_splash.setGeometry(self.rect())
            self._startup_splash.show()
            # Let the native window paint before starting cache I/O.
            QTimer.singleShot(100, self._start_startup_cache)
        else:
            ensure_cache()
            self._build_ui()
        if not offscreen_mode:
            # Check pending update dinonaktifkan (pembaruan di-disable)
            pass

    def _start_startup_cache(self):
        if not self.isVisible():
            return

        def _prepare():
            error = None
            try:
                ensure_cache()
            except Exception as exc:
                error = exc
            try:
                self.startup_cache_ready.emit(error)
            except RuntimeError:
                pass  # The window was destroyed while the daemon was working.

        _threading.Thread(target=_prepare, name="rak-asset-cache", daemon=True).start()

    def _on_startup_cache_ready(self, error):
        if error is not None:
            if self._startup_splash is not None:
                self._startup_splash.hide()
            _global_excepthook(type(error), error, error.__traceback__)
            QApplication.quit()
            return
        self._finish_startup_build()

    def _finish_startup_build(self):
        if not self.isVisible():
            return
        if self._startup_builder is not None:
            return
        self._startup_builder = self._build_ui_steps()
        self._advance_startup_build()

    @Slot()
    def _advance_startup_build(self):
        if self._startup_builder is None:
            return
        if not self.isVisible():
            self._startup_build_timer.start(100)
            return
        started = time.perf_counter()
        try:
            while time.perf_counter() - started < .006:
                next(self._startup_builder)
        except StopIteration:
            self._startup_builder = None
            self._startup_splash.raise_()
            self._startup_ui_built = True
            self._emit_startup_built_if_ready()
            return
        except Exception:
            self._startup_builder = None
            if self._startup_splash is not None:
                self._startup_splash.hide()
            _global_excepthook(*sys.exc_info())
            QApplication.quit()
            return
        self._startup_splash.raise_()
        self._startup_build_timer.start(1)

    def _emit_startup_built_if_ready(self):
        if self._startup_ui_built and self._startup_rows is None:
            self._startup_ui_built = False
            self.startup_built.emit()

    @Slot()
    def _build_startup_project_rows(self):
        if self._startup_rows is None:
            return
        if not self.isVisible():
            self._startup_rows_timer.start(100)
            return
        started = time.perf_counter()
        try:
            while self._startup_rows:
                order, project = self._startup_rows.popleft()
                self._add_project_row(project, order)
                if time.perf_counter() - started >= .006:
                    self._startup_rows_timer.start(1)
                    return
            self._startup_rows = None
            self._filter_projects(self.search_input.text())
            self._update_trash_hint()
            self._emit_startup_built_if_ready()
        except Exception:
            self._startup_rows = None
            _global_excepthook(*sys.exc_info())
            QApplication.quit()

    def reveal_startup(self):

        splash = self._startup_splash
        if (splash is None or self._startup_reveal_started or self._startup_rows is not None
                or self._startup_builder is not None):
            return
        self._startup_reveal_started = True
        splash.raise_()
        # Finish pending layout work before capturing; the animation clock
        # starts only after the native Qt pixels and Skia assets are ready.
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        for name in ("_settings_warm_timer", "_create_popup_warm_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        from collections import deque
        widgets = []
        for widget in self.findChildren(QWidget):
            if (widget.isVisible() and isinstance(widget, (QLabel, QAbstractButton, QLineEdit, QProgressBar))
                    and self.rect().intersects(QRect(widget.mapTo(self, QPoint()), widget.size()))):
                widgets.append(widget)
        self._startup_paint_widgets = deque(widgets)
        self._startup_prepare_timer.start(1)

    @Slot()
    def _prepare_startup_paints(self):
        if self._startup_paint_widgets is None or getattr(self, "_closing", False):
            return
        if not self.isVisible():
            self._startup_prepare_timer.start(100)
            return
        started = time.perf_counter()
        while self._startup_paint_widgets:
            widget = self._startup_paint_widgets.popleft()
            if isValid(widget):
                # Warm the real leaf's native Qt glyphs and cached Skia surface
                # in separate GUI turns, before the full-page capture/fade.
                widget.grab()
            if time.perf_counter() - started >= .006:
                self._startup_prepare_timer.start(1)
                return
        self._startup_paint_widgets = None
        self._start_prepared_startup_scene()

    def _start_prepared_startup_scene(self):
        splash = self._startup_splash
        if splash is None or getattr(self, "_closing", False):
            return
        page = None
        project_list = None
        excluded = ()
        entries = ()
        if getattr(self, "_login_overlay", None) is None:
            current = self.stack.currentWidget()
            if current in (self.page_projects, self.page_create):
                page = current
                excluded = (self._page_footers[page],)
                if page is self.page_projects:
                    project_list = self.list_widget
                    entries = self._project_drive_cards.values()
        scene = StartupTransition(self, splash, page, project_list, excluded, entries)
        self._startup_transition = self._startup_content_reveal = scene
        scene.finished.connect(self._on_startup_scene_finished)
        scene.start()
        self.startup_animation_started.emit()

    def _on_startup_scene_finished(self):
        self._startup_transition = self._startup_content_reveal = None
        self._complete_startup_reveal()
        # Resume optional hidden-page preparation after the visible animation.
        if not getattr(self, "_closing", False) and self.isVisible():
            self._settings_warm_timer.start(100)
            if self._create_project_dialog is None:
                self._create_popup_warm_timer.start(150)

    def _complete_startup_reveal(self):
        splash = self._startup_splash
        if splash is None:
            return
        splash.stop()
        splash.hide()
        splash.deleteLater()
        self._startup_splash = None
        self._startup_fade = None
        self._emit_startup_revealed_if_ready()

    def _on_startup_content_reveal_finished(self):
        self._startup_content_reveal = None
        self._emit_startup_revealed_if_ready()

    def _emit_startup_revealed_if_ready(self):
        if (self._startup_splash is None and self._startup_content_reveal is None
                and not self._startup_revealed_emitted and not getattr(self, "_closing", False)):
            self._startup_revealed_emitted = True
            self.startup_revealed.emit()

    def _refresh_startup_frame(self):

        splash = self._startup_splash
        if splash is not None and splash.isVisible():
            splash.raise_()
            splash._update_glint()
            if self._startup_builder is None:
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _clear_page_content_reveal(self):
        filtering_projects = any((self._project_cards_transition,
                                  self._disk_cards_transition, self._project_layout_transition))
        self._stop_project_cards_transition()
        self._stop_disk_cards_transition()
        self._stop_project_layout_transition()
        self._stop_project_drive_usage_reveal()
        reveal = self._page_content_reveal
        self._page_content_reveal = None
        if reveal is not None:
            reveal.stop()
        transition = self._footer_action_transition
        self._footer_action_transition = None
        if transition is not None:
            transition.stop()

        self._stop_profile_card_transition()
        if filtering_projects:
            self._filter_projects(self.search_input.text())


    def _stop_profile_card_transition(self):
        # Settings scrolling and account refresh can end the profile model
        # before the page bands. Release its owning canvas first.
        scene = getattr(self, "_page_content_reveal", None)
        profile = getattr(self, "_profile_card_transition", None)
        if isinstance(scene, AppPageTransition) and scene._profile is profile and profile is not None:
            scene.stop()
        if profile is not None and not isValid(profile):
            self._profile_card_transition = None
        else:
            super()._stop_profile_card_transition()

    def _on_page_changed(self, index):
        previous_page = self._last_page
        page = self.stack.widget(index)
        self._last_page = page
        self._clear_page_content_reveal()
        self._refresh_theme_page(page)
        if page is self.page_projects and not self._offscreen_mode:
            self._refresh_wsl_disks()
        from main_ext.class_ThemeUi import theme
        if (index < 0 or not self.isVisible() or not theme.animations_enabled
                or self._startup_splash is not None
                or self._startup_content_reveal is not None
                or getattr(self, "_login_overlay", None) is not None):
            return

        self._project_hover_effect.reset()
        excluded = [self._page_footers[page]]
        excluded.extend(self._prepare_account_page_transition(previous_page, page))
        profile = getattr(self, "_profile_card_transition", None)
        footer = None
        if previous_page in self._page_footer_buttons:
            footer = FooterActionTransition(
                self.stack, self._page_footer_buttons[previous_page],
                self._page_footer_buttons[page],
            )
            self._footer_action_transition = footer
            def finish_footer():
                if self._footer_action_transition is footer:
                    self._footer_action_transition = None
            footer.finished.connect(finish_footer)

        transition = AppPageTransition(
            page, self.list_widget if page is self.page_projects else None,
            footer=footer, profile=profile, excluded_widgets=excluded,
            bottom_up=page is self.page_settings,
        )
        self._page_content_reveal = transition
        def finish():
            if self._page_content_reveal is transition:
                self._page_content_reveal = None
        transition.finished.connect(finish)
        transition.start()

    def closeEvent(self, event):
        self._closing = True
        self._startup_build_timer.stop()
        self._startup_builder = None
        self._startup_prepare_timer.stop()
        self._startup_paint_widgets = None
        self._startup_rows_timer.stop()
        self._startup_rows = None
        if self._startup_transition is not None:
            self._startup_transition.stop()
        self._wsl_disk_closed = True
        self._trash_timer.stop()
        # File operations finish before their QThread objects are destroyed.
        for worker in tuple(self._trash_workers) + tuple(getattr(self, "_delete_workers", [])):
            worker.wait()
        self._disk_poll_timer.stop()
        self._clear_page_content_reveal()
        self._clear_theme_snapshot_overlay()
        self._stop_project_size_scanners()


        if not getattr(self, "_offscreen_mode", False):
            try:
                from main_ext.config_engine_client import _ServiceConnection
                conn = _ServiceConnection.get()
                if conn.is_connected():
                    # Disconnect ws supaya service notice dan exit clean
                    try:
                        conn.ws.close()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                from services.process_manager import get_manager
                get_manager().stop()
            except Exception as e:
                print(f"[AppUI] WARN: stop service error: {e}")
            try:
                from core.threader import stop_all_threads
                stop_all_threads()
            except Exception as e:
                print(f"[AppUI] WARN: stop threads error: {e}")
            # quitOnLastWindowClosed=False (lihat entry point) → close AppUI = exit
            # beneran, jadi keluar app secara eksplisit di sini.
            from core.threader import quit_when_idle
            quit_when_idle()
        super().closeEvent(event)

    def _build_ui(self):
        # Rebuilds and offscreen fixtures remain synchronous. Startup advances
        # the same steps with a small GUI budget behind the opaque splash.
        for _ in self._build_ui_steps():
            pass

    def _build_ui_steps(self):
        self._wsl_disk_ui_ready = False
        settings_warm_timer = getattr(self, "_settings_warm_timer", None)
        if settings_warm_timer is not None:
            settings_warm_timer.stop()
            settings_warm_timer.deleteLater()
        warm_timer = getattr(self, "_create_popup_warm_timer", None)
        yield
        if warm_timer is not None:
            warm_timer.stop()
            warm_timer.deleteLater()
        self._create_project_dialog = None
        self._init_account_features()

        # Load saved theme BEFORE building UI so all colors are correct
        from main_ext.class_ThemeUi import theme as _th
        yield
        _th.load()
        # Keep the inherited font rules constant. Palette rules live on each
        # page, so recoloring Settings does not repolish every project row.
        self.setStyleSheet("QWidget { font-family: 'Poppins'; font-size: 13px; }")


        if not getattr(self, "_offscreen_mode", False):
            if not getattr(self, "_theme_listener_registered", False):
                _th.on_change(self._on_theme_changed)
                self._theme_listener_registered = True
            if not getattr(self, "_lang_listener_registered", False):
                _ul.on_change(self._on_lang_changed)
                self._lang_listener_registered = True
        self._init_update_features()
        yield

        bind_language(self, "setWindowTitle", window_title)
        self.setWindowIcon(QIcon(icon("ic_launcher")))

        main_layout = QVBoxLayout(self)

        # STACKED (TAB 1 & TAB 2)
        self.stack = QStackedWidget()
        yield
        self._page_footers = {}
        main_layout.addWidget(self.stack)

        # TAB 1 : PROJECT LIST
        self.page_projects = QWidget(self.stack)
        bind_style(self.page_projects, lambda: _th.app_stylesheet())
        self.stack.addWidget(self.page_projects)
        yield
        self.page_projects_layout = QVBoxLayout(self.page_projects)


        header_proj = QWidget(self.page_projects)
        header_proj_layout = QHBoxLayout(header_proj)
        header_proj_layout.setContentsMargins(12, 8, 12, 8)
        yield
        header_proj_layout.setSpacing(8)
        self._project_header = header_proj
        self._project_header_layout = header_proj_layout

        header_proj_title = bind_text(QLabel(), lambda: t("hdr_my_projects"))
        yield
        header_proj_title.setStyleSheet("font-size: 22px; font-weight: 600;")
        header_proj_title.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        header_proj_layout.addWidget(header_proj_title)
        self._project_header_title = header_proj_title
        yield

        self._project_drive_container = QWidget(header_proj)
        self._project_drive_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        drives_layout = QBoxLayout(QBoxLayout.LeftToRight, self._project_drive_container)
        drives_layout.setSpacing(8)
        yield
        drives_layout.setContentsMargins(6, 0, 0, 0)
        self._project_drives_layout = drives_layout
        self._project_drives_in_sidebar = None
        self._project_layout_animation_ready = False
        yield
        self._project_drive_card_width = 128
        self._project_drive_card_height = 44
        # Reserve the same header height whether disks are here or in the sidebar.
        header_proj.setFixedHeight(self._project_drive_card_height + 16)
        self._project_drive_cards = {}
        yield
        self._add_project_trash_card()

        # Disk enumeration/usage runs in the existing worker. A slow or
        # disconnected volume must not stop the splash's GUI clock.
        for usage in self._windows_disk_usage or ():
            self._add_project_drive_card(usage["key"], usage["total"], usage["used"])
        yield
        for usage in self._wsl_disk_usage:
            self._add_project_drive_card(usage["key"], usage["total"], usage["used"],
                                         distro=usage["distro"], usage_percent=usage["percent"])

        header_proj_layout.addWidget(self._project_drive_container)
        header_proj_layout.addStretch()

        self.search_input = QLineEdit(header_proj)
        yield
        bind_language(self.search_input, "setPlaceholderText", lambda: t("placeholder_search"))
        self.search_input.setFixedHeight(34)
        self.search_input.setMinimumWidth(115)
        self.search_input.setMaximumWidth(174)
        yield
        self.search_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._search_action = self.search_input.addAction(
            QIcon(icon_pixmap("project_search")), QLineEdit.LeadingPosition
        )
        bind_icon(self._search_action, lambda: QIcon(icon_pixmap("project_search")))
        bind_style(self.search_input, lambda: f"""
            QLineEdit {{
                background: {_th.BG2};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 10px;
                padding: 0 8px;
                color: {_th.FG};
                font-size: 11px;
                selection-background-color: {_th.accent(0.35)};
            }}
            QLineEdit:focus {{
                border-color: {_th.accent(0.45)};
                background: {_th.BG_INPUT};
            }}
        """)
        yield
        self.search_input.setFocusPolicy(Qt.ClickFocus)
        self.search_input.textChanged.connect(self._filter_projects)
        header_proj_layout.addWidget(self.search_input, 0, Qt.AlignVCenter)

        self.page_projects_layout.addWidget(header_proj)
        yield


        # List
        self.list_widget = SmoothListWidget(self.page_projects)
        self.list_widget.setItemDelegate(_ProjectCardDelegate(self.list_widget))
        self._project_hover_effect = _ProjectHoverEffect(self.list_widget, _th.BG)
        self.list_widget.scrollingChanged.connect(self._project_hover_effect.set_scrolling)
        yield
        self.list_widget.set_viewport_background(_th.BG)
        # An empty-state overlay does not change the header/sidebar geometry.
        self._trash_hint = bind_text(QLabel(self.list_widget.viewport()), self._trash_hint_text)
        self._trash_hint.setWordWrap(True)
        self._trash_hint.setAlignment(Qt.AlignCenter)
        yield
        self._trash_hint.setContentsMargins(16, 0, 16, 0)
        self._trash_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        bind_style(self._trash_hint, lambda: f"font-size: 11px; color: {_th.FG3}; background: transparent;")
        self._trash_hint.hide()
        yield
        self.list_widget.viewport().installEventFilter(self)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        bind_style(self.list_widget, lambda: f"""
        QListWidget {{
            background: transparent;
            border: none;
            padding: 8px 16px;
            outline: 0;
        }}

        /* ITEM CARD */
        QListWidget::item {{
            background-color: {_th.BG2};
            border-radius: 10px;
            border: 1px solid {_th.border_color(0.07)};
            padding: 6px 14px;
            margin-bottom: 8px;
        }}
        QListWidget::item:hover {{
            background-color: {_th.BG_SELECTED};
            border: 1px solid {_th.accent(0.30)};
        }}
        QListWidget::item:selected {{
            background-color: {_th.accent(0.12)};
            border: 1px solid {_th.accent(0.45)};
        }}
        QListWidget::item:selected:!active {{
            background-color: {_th.accent(0.12)};
            border: 1px solid {_th.accent(0.45)};
        }}

        /* FOCUS OUTLINE */
        QListWidget::item:!selected {{
            outline: 0;
        }}

        QListWidget::item:selected {{
            outline: 0;
        }}
        /* =========================
        SCROLLBAR TOTAL HIDE (0px)
        ========================= */
        QScrollBar:vertical {{
            width: 0px;
            background: transparent;
            margin: 0px;
        }}

        QScrollBar::handle:vertical {{
            width: 0px;
            min-height: 0px;
        }}

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            width: 0px;
            height: 0px;
            background: none;
        }}
        /* =========================
        HORIZONTAL SCROLLBAR (TIDURAN)
        ========================= */
        QScrollBar:horizontal {{
            height: 0px;
            min-height: 0px;
            max-height: 0px;
            background: transparent;
        }}

        QScrollBar::handle:horizontal,
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            height: 0px;
            background: transparent;
        }}

        """)

        self.list_widget.itemClicked.connect(self.open_project)
        yield
        self._projects_body = QWidget(self.page_projects)
        self._projects_body_layout = QHBoxLayout(self._projects_body)
        self._projects_body_layout.setContentsMargins(0, 0, 0, 0)
        self._projects_body_layout.setSpacing(0)
        yield
        self._project_drive_sidebar = SmoothScrollArea()
        self._project_drive_sidebar.setFrameShape(QScrollArea.NoFrame)
        self._project_drive_sidebar.setWidgetResizable(True)
        self._project_drive_sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        yield
        self._project_drive_sidebar.setFixedWidth(self._project_drive_card_width + 4)
        self._project_drive_sidebar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._project_drive_sidebar.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._project_drive_sidebar.viewport().setStyleSheet("background: transparent;")
        yield
        self._project_drive_sidebar.hide()
        self._projects_body_layout.addWidget(self._project_drive_sidebar)
        self._projects_body_layout.addWidget(self.list_widget, 1)
        self.page_projects_layout.addWidget(self._projects_body, 1)
        yield
        self.list_widget.setContextMenuPolicy(Qt.NoContextMenu)
        self.list_widget.viewport().setContextMenuPolicy(Qt.NoContextMenu)


        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 12)
        yield
        bar.setSpacing(12)

        # ⬅️ PROFILE MINI (KIRI)
        self._add_account_footer(bar)

        bar.addStretch()  # ⬅️ DORONG TOMBOL KE KANAN

        self.btn_settings = bind_text(QPushButton(), lambda: t("btn_settings"))
        yield
        self.btn_settings.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self.btn_settings, lambda: tint_icon("settings_octagon", _th.FG2, 15))
        self.btn_settings.setIconSize(QSize(15, 15))
        self.btn_settings.clicked.connect(
            lambda: self._show_page(self.page_settings)
        )
        yield

        self.btn_create = bind_text(QPushButton(), lambda: t("btn_create_project"))
        self.btn_create.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self.btn_create, lambda: tint_icon("plus", _th.FG2, 15))
        self.btn_create.setIconSize(QSize(15, 15))
        yield
        self.btn_create.clicked.connect(self._show_create_popup)

        bar.addWidget(self.btn_settings)
        bar.addWidget(self.btn_create)

        footer = QWidget()
        yield
        footer.setLayout(bar)
        self._page_footers[self.page_projects] = footer
        self.page_projects_layout.addWidget(footer)

        yield
        self._refresh_startup_frame()

        # TAB 2 : CREATE PROJECT
        self.page_create = QWidget(self.stack)
        bind_style(self.page_create, lambda: _th.app_stylesheet())
        self.stack.addWidget(self.page_create)
        self.page_create_layout = QVBoxLayout(self.page_create)
        yield

        # header (identik project list)
        header_create = QWidget(self.page_create)
        header_create_layout = QVBoxLayout(header_create)
        header_create_layout.setContentsMargins(16, 12, 16, 8)
        header_create_title = bind_text(QLabel(), lambda: t("hdr_new_project"))
        yield
        header_create_title.setStyleSheet("font-size: 22px; font-weight: 600;")
        header_create_layout.addWidget(header_create_title)
        self.page_create_layout.addWidget(header_create)

        # shared input style
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
        yield
        _label_ss = lambda: f"font-size:11px; font-weight:600; color:{_th.FG_MUTED}; background:transparent; border:none;"

        # content column (same margins as project list: 16px sides)
        outer_h = QHBoxLayout()
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_wrap = QWidget()
        outer_wrap.setStyleSheet("background: transparent;")
        outer_wrap.setLayout(outer_h)
        scroll = SmoothScrollArea(self.page_create)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(outer_wrap)
        self._scroll_create = scroll
        self.page_create_layout.addWidget(scroll, 1)
        yield

        center_col = QWidget(outer_wrap)
        yield
        center_v = QVBoxLayout(center_col)
        center_v.setContentsMargins(16, 8, 16, 16)
        center_v.setSpacing(12)

        # card: project info
        card_info = QWidget(center_col)
        yield
        card_info.setObjectName("card_info")
        bind_style(card_info, lambda: f"""
            QWidget#card_info {{
                background: {_th.BG2};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 12px;
            }}
        """)
        card_info_lay = QVBoxLayout(card_info)
        card_info_lay.setContentsMargins(18, 16, 18, 18)
        yield
        card_info_lay.setSpacing(14)

        # project name
        lbl_name = bind_text(QLabel(), lambda: t("lbl_project_name"))
        bind_style(lbl_name, _label_ss)
        self.input_name = QLineEdit()
        yield
        bind_language(self.input_name, "setPlaceholderText", lambda: t("placeholder_project_name"))
        self.input_name.setFixedHeight(38)
        bind_style(self.input_name, _input_ss)

        card_info_lay.addWidget(lbl_name)
        yield
        card_info_lay.addWidget(self.input_name)

        # divider
        div1 = QWidget(); div1.setFixedHeight(1)
        bind_style(div1, lambda: f"background:{_th.border_color(0.07)}; border:none;")
        yield
        card_info_lay.addWidget(div1)

        # porting mode — custom pill selector
        lbl_mode = bind_text(QLabel(), lambda: t("lbl_porting_mode"))
        bind_style(lbl_mode, _label_ss)
        card_info_lay.addWidget(lbl_mode)
        yield

        mode_container = QWidget()
        mode_container.setStyleSheet("background:transparent; border:none;")
        mode_h = QHBoxLayout(mode_container)
        mode_h.setContentsMargins(0, 0, 0, 0)
        yield
        mode_h.setSpacing(8)

        _mode_active = lambda: f"""
            QPushButton {{
                background: {_th.accent(0.18)};
                border: 1.5px solid {_th.accent(0.50)};
                border-radius: 8px;
                color: {_th.FG_BLUE};
                font-size: 13px; font-weight: 600;
                padding: 8px 0;
            }}
        """
        _mode_inactive = lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.04)};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 8px;
                color: {_th.FG_MUTED};
                font-size: 13px; font-weight: 400;
                padding: 8px 0;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.08)};
                color: {_th.FG};
            }}
        """

        self.radio_1 = QRadioButton()  # keep for logic compat
        yield
        self.radio_2 = QRadioButton()
        self.radio_1.setChecked(True)
        self.radio_1.hide(); self.radio_2.hide()
        yield
        self._port_mode = 1  # 1 = single, 2 = dual

        btn_single = bind_rom_mode_button(RomModeButton(), 1, lambda: self._port_mode == 1)
        btn_dual = bind_rom_mode_button(RomModeButton(), 2, lambda: self._port_mode == 2)
        for b in (btn_single, btn_dual):
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setFixedHeight(40)
        yield

        bind_style(btn_single, _mode_active)
        bind_style(btn_dual, _mode_inactive)

        def _pick_single():
            self.radio_1.setChecked(True)
            self._port_mode = 1
            bind_style(btn_single, _mode_active)
            bind_style(btn_dual, _mode_inactive)
            self._apply_port_mode()
            self.update_create_state()

        def _pick_dual():
            self.radio_2.setChecked(True)
            self._port_mode = 2
            bind_style(btn_single, _mode_inactive)
            bind_style(btn_dual, _mode_active)
            self._apply_port_mode()
            self.update_create_state()
        yield

        btn_single.clicked.connect(_pick_single)
        btn_dual.clicked.connect(_pick_dual)

        mode_h.addWidget(btn_single)
        mode_h.addWidget(btn_dual)
        yield
        card_info_lay.addWidget(mode_container)

        center_v.addWidget(card_info)
        center_v.addSpacing(12)

        # card: port names
        self._card_ports = QWidget()
        yield
        self._card_ports.setObjectName("card_ports")
        bind_style(self._card_ports, lambda: f"""
            QWidget#card_ports {{
                background: {_th.BG2};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 12px;
            }}
        """)

        card_ports_lay = QVBoxLayout(self._card_ports)
        card_ports_lay.setContentsMargins(18, 16, 18, 18)
        yield
        card_ports_lay.setSpacing(14)

        # card header
        lbl_ports_header = bind_text(QLabel(), lambda: t("lbl_folder_names"))
        bind_style(lbl_ports_header, _label_ss)
        lbl_ports_hint = bind_text(QLabel(), lambda: t("hint_folder_names"))
        yield
        bind_style(lbl_ports_hint, lambda: f"font-size:11px; color:{_th.FG_MUTED}; background:transparent; border:none;")
        card_ports_lay.addWidget(lbl_ports_header)
        card_ports_lay.addWidget(lbl_ports_hint)
        card_ports_lay.addSpacing(2)
        yield

        div_header = QWidget(); div_header.setFixedHeight(1)
        bind_style(div_header, lambda: f"background:{_th.border_color(0.07)}; border:none;")
        card_ports_lay.addWidget(div_header)
        yield
        card_ports_lay.addSpacing(2)

        self.port1_label = bind_text(QLabel(), lambda: t("lbl_ft1_folder"))
        bind_style(self.port1_label, _label_ss)
        self.input_port1 = QLineEdit()
        yield
        bind_language(self.input_port1, "setPlaceholderText", lambda: t("placeholder_ft1"))
        self.input_port1.setFixedHeight(38)
        bind_style(self.input_port1, _input_ss)

        card_ports_lay.addWidget(self.port1_label)
        yield
        card_ports_lay.addWidget(self.input_port1)

        div2 = QWidget(); div2.setFixedHeight(1)
        bind_style(div2, lambda: f"background:{_th.border_color(0.07)}; border:none;")
        yield

        self.port2_label = bind_text(QLabel(), lambda: t("lbl_ft2_folder"))
        bind_style(self.port2_label, _label_ss)
        self.input_port2 = QLineEdit()
        bind_language(self.input_port2, "setPlaceholderText", lambda: t("placeholder_ft2"))
        yield
        self.input_port2.setFixedHeight(38)
        bind_style(self.input_port2, _input_ss)

        self._div2_ports = div2
        self._lbl_port2  = self.port2_label
        yield
        self._inp_port2  = self.input_port2

        card_ports_lay.addWidget(div2)
        card_ports_lay.addWidget(self.port2_label)
        card_ports_lay.addWidget(self.input_port2)
        yield

        # hide port2 row by default (single mode)
        div2.hide(); self.port2_label.hide(); self.input_port2.hide()


        center_v.addWidget(self._card_ports)
        yield
        center_v.addSpacing(12)

        # card: directory
        card_dir = QWidget(center_col)
        card_dir.setObjectName("card_dir")
        bind_style(card_dir, lambda: f"""
            QWidget#card_dir {{
                background: {_th.BG2};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 12px;
            }}
        """)
        yield
        card_dir_lay = QVBoxLayout(card_dir)
        card_dir_lay.setContentsMargins(18, 16, 18, 18)
        card_dir_lay.setSpacing(10)

        lbl_dir = bind_text(QLabel(), lambda: t("lbl_output_dir"))
        yield
        bind_style(lbl_dir, _label_ss)
        card_dir_lay.addWidget(lbl_dir)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        yield
        dir_row.setContentsMargins(0, 0, 0, 0)

        self.path_label = QLabel(_resolve_default_location())
        bind_style(self.path_label, lambda: f"color:{_th.FG_MUTED}; font-size:12px; background:transparent; border:none;")
        self.path_label.setWordWrap(False)
        yield
        self.path_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.btn_path = bind_text(QPushButton(), lambda: t("btn_browse"))
        self.btn_path.setFixedHeight(32)
        self.btn_path.setCursor(Qt.PointingHandCursor)
        yield
        bind_style(self.btn_path, lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 6px;
                color: {_th.FG_BLUE_MUTED};
                font-size: 12px;
                padding: 0 12px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.09)}; color: {_th.FG}; }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
        """)
        self.btn_path.clicked.connect(self.select_path)

        dir_row.addWidget(self.path_label, 1)
        dir_row.addWidget(self.btn_path)
        yield
        card_dir_lay.addLayout(dir_row)

        center_v.addWidget(card_dir)
        center_v.addStretch()

        outer_h.addWidget(center_col)
        yield


        # connect validation
        self.input_name.textChanged.connect(self.update_create_state)
        self.input_port1.textChanged.connect(self.update_create_state)
        self.input_port2.textChanged.connect(self.update_create_state)
        yield
        self._apply_port_mode()

        # bottom bar
        bar2 = QHBoxLayout()
        bar2.setContentsMargins(16, 8, 16, 12)
        bar2.setSpacing(12)
        yield

        self._add_account_footer(bar2)

        bar2.addStretch()

        self.btn_back_create = bind_text(QPushButton(), lambda: t("btn_back"))
        self.btn_back_create.setLayoutDirection(Qt.LeftToRight)
        yield
        bind_icon(self.btn_back_create, lambda: tint_icon("arrow_right", _th.FG2, 15))
        self.btn_back_create.setIconSize(QSize(15, 15))
        self.btn_back_create.clicked.connect(
            lambda: self.stack.setCurrentWidget(self.page_projects)
        )

        self.btn_settings_create = bind_text(QPushButton(), lambda: t("btn_settings"))
        yield
        self.btn_settings_create.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self.btn_settings_create, lambda: tint_icon("settings_octagon", _th.FG2, 15))
        self.btn_settings_create.setIconSize(QSize(15, 15))
        self.btn_settings_create.clicked.connect(
            lambda: self._show_page(self.page_settings)
        )
        yield

        self.btn_create2 = bind_text(QPushButton(), lambda: t("btn_confirm_project"))
        self.btn_create2.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self.btn_create2, lambda: create_themed_icon(
            "check_white", _th.FG_BLUE, _th.FG_BLUE_LIGHT, 15,
            disabled_color=with_alpha(_th.accent_hex, 0.30),
        ))
        self.btn_create2.setIconSize(QSize(15, 15))
        yield
        self.btn_create2.clicked.connect(self.create_project)
        self.btn_create2.setProperty("role", "primary")
        self.btn_create2.setEnabled(False)

        bar2.addWidget(self.btn_back_create)
        yield
        bar2.addWidget(self.btn_settings_create)
        bar2.addWidget(self.btn_create2)

        footer = QWidget()
        footer.setLayout(bar2)
        yield
        self._page_footers[self.page_create] = footer
        self.page_create_layout.addWidget(footer)
        self.update_create_state()
        yield
        self._refresh_startup_frame()


        self.page_settings = QWidget(self.stack)
        bind_style(self.page_settings, lambda: _th.app_stylesheet())
        self.stack.addWidget(self.page_settings)
        self.page_settings_layout = QVBoxLayout(self.page_settings)
        yield


        header_set = QWidget(self.page_settings)
        header_set_layout = QVBoxLayout(header_set)
        header_set_layout.setContentsMargins(16, 12, 16, 8)

        header_set_title = bind_text(QLabel(), lambda: t("hdr_settings"))
        yield
        header_set_title.setStyleSheet("""
            font-size: 22px;
            font-weight: 600;
        """)

        header_set_layout.addWidget(header_set_title)
        self.page_settings_layout.addWidget(header_set)


        content = QWidget()
        scroll = SmoothScrollArea(self.page_settings)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self._scroll_settings = scroll
        self.page_settings_layout.addWidget(scroll)
        yield
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 8, 16, 16)
        content_layout.setSpacing(12)

        self._build_profile_card(content_layout)
        yield


        app_card = QWidget(content)
        bind_style(app_card, lambda: f"""
        QWidget {{
            background-color: {_th.BG2};
            border: 1px solid {_th.border_color(0.08)};
            border-radius: 10px;
        }}
        QLabel {{
            background: transparent;
            border: none;
        }}
        """)

        app_outer = QVBoxLayout(app_card)
        app_outer.setContentsMargins(14, 12, 14, 14)
        yield
        app_outer.setSpacing(8)

        # HEADER
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        icon_info = QLabel()
        yield
        bind_pixmap(icon_info, lambda: QIcon(icon("info")).pixmap(20, 20))
        icon_info.setAlignment(Qt.AlignVCenter)

        app_header = bind_text(QLabel(), lambda: t("hdr_product_info"))
        bind_style(app_header, lambda: f"font-size:14px; font-weight:600; color:{_th.FG}; background:transparent; border:none;")
        yield

        header_row.addWidget(icon_info)
        header_row.addWidget(app_header)
        header_row.addStretch()

        app_outer.addLayout(header_row)
        yield
        app_outer.addSpacing(12)

        # FORM
        app_layout = QFormLayout()
        app_layout.setHorizontalSpacing(16)
        app_layout.setVerticalSpacing(8)
        yield

        app_layout.addRow(bind_text(QLabel(), lambda: t("form_name")), QLabel(APP_NAME))
        app_layout.addRow(bind_text(QLabel(), lambda: t("form_version")), QLabel(APP_VERSION))
        #app_layout.addRow(t("form_neocat"), QLabel(NEOCAT_LABEL))
        app_layout.addRow(
            bind_text(QLabel(), lambda: t("form_edition")),
            bind_text(QLabel(), edition_label)
        )

        app_outer.addLayout(app_layout)
        yield

        content_layout.addWidget(app_card)
        content_layout.addSpacing(8)   # 👈 JARAK ANTAR CARD

        def build_card_base():
            card = QWidget(content)
            bind_style(card, lambda: f"""
            QWidget {{
                background-color: {_th.BG2};
                border: 1px solid {_th.border_color(0.08)};
                border-radius: 10px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            """)
            outer = QVBoxLayout(card)
            outer.setContentsMargins(14, 12, 14, 14)
            outer.setSpacing(8)
            return card, outer


        def build_card_header(icon_name, title_text, right_widget=None):
            row = QHBoxLayout()
            row.setSpacing(8)

            ic = QLabel()
            bind_pixmap(ic, lambda icon_name=icon_name: QIcon(icon(icon_name)).pixmap(20, 20))
            ic.setAlignment(Qt.AlignVCenter)

            title = bind_text(QLabel(), title_text)
            bind_style(title, lambda: f"""
                font-size: 14px;
                font-weight: 600;
                color: {_th.FG_LABEL};
            """)

            row.addWidget(ic)
            row.addWidget(title)
            row.addStretch()

            if right_widget:
                row.addWidget(right_widget)

            return row
        yield


        dep_card, dep_outer = build_card_base()

        btn_dep = bind_text(QPushButton(), lambda: t("btn_manage"))
        btn_dep.setFixedHeight(28)
        btn_dep.setCursor(Qt.PointingHandCursor)
        yield
        bind_style(btn_dep, lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 6px;
                color: {_th.FG_BLUE_MUTED};
                font-size: 12px;
                padding: 0 12px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.09)}; border-color: {_th.border_color(0.15)}; color: {_th.FG}; }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
        """)
        btn_dep.clicked.connect(self.open_dependency_window)

        dep_outer.addLayout(
            build_card_header(
                "package",          # ICON
                lambda: t("hdr_packages"),
                btn_dep             # ACTION
            )
        )

        content_layout.addWidget(dep_card)
        yield
        content_layout.addSpacing(8)


        loc_card, loc_outer = build_card_base()

        loc_outer.addLayout(build_card_header("default_location", lambda: t("hdr_default_location")))
        loc_outer.addSpacing(4)
        yield


        loc_path_label = bind_text(QLabel(), lambda: _load_default_location() or t("lbl_docs_system_default"))
        bind_style(loc_path_label, lambda: f"color:{_th.FG_MUTED}; font-size:12px; background:transparent; border:none;")
        loc_path_label.setWordWrap(True)

        btn_loc = bind_text(QPushButton(), lambda: t("btn_change"))
        yield
        btn_loc.setFixedHeight(28)
        btn_loc.setCursor(Qt.PointingHandCursor)
        bind_style(btn_loc, lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 6px;
                color: {_th.FG_BLUE_MUTED};
                font-size: 12px;
                padding: 0 12px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.09)}; border-color: {_th.border_color(0.15)}; color: {_th.FG}; }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
        """)

        btn_loc_reset = bind_text(QPushButton(), lambda: t("btn_reset"))
        yield
        btn_loc_reset.setFixedHeight(28)
        btn_loc_reset.setCursor(Qt.PointingHandCursor)
        bind_style(btn_loc_reset, lambda: f"""
            QPushButton {{
                background: transparent;
                border: 1px solid {_th.border_color(0.07)};
                border-radius: 6px;
                color: {_th.FG_DESC};
                font-size: 12px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.06)}; color: {_th.FG_BLUE_MUTED}; }}
            QPushButton:pressed {{ background: {_th.border_color(0.10)}; }}
        """)

        def _change_default_location():
            chosen = QFileDialog.getExistingDirectory(self, t("dlg_choose_default_folder"))
            if not chosen:
                return
            _save_default_location(chosen)
            loc_path_label.setText(chosen)
            # sync ke path_label di create page supaya langsung reflected
            self.path_label.setText(chosen)
        yield

        def _reset_default_location():
            _save_default_location("")
            loc_path_label.setText(t("lbl_docs_system_default"))
            self.path_label.setText(_resolve_default_location())

        btn_loc.clicked.connect(_change_default_location)
        btn_loc_reset.clicked.connect(_reset_default_location)

        loc_btn_row = QHBoxLayout()
        yield
        loc_btn_row.setSpacing(6)
        loc_btn_row.addWidget(loc_path_label, 1)
        loc_btn_row.addWidget(btn_loc_reset)
        loc_btn_row.addWidget(btn_loc)
        yield

        loc_outer.addLayout(loc_btn_row)

        content_layout.addWidget(loc_card)
        content_layout.addSpacing(8)
        self._refresh_startup_frame()
        yield


        from main_ext.class_ThemeUi import theme as _theme

        theme_card, theme_outer = build_card_base()
        theme_outer.addLayout(build_card_header("theme", lambda: t("hdr_appearance")))

        theme_row = QHBoxLayout()
        yield
        theme_row.setSpacing(10)

        _active_label = QLabel(_theme.palettes()[_theme.name]["label"])
        self._theme_name_label = _active_label
        bind_style(_active_label, lambda: f"font-size:12px; color:{_th.FG_MUTED}; background:transparent; border:none;")
        yield

        btn_theme = bind_text(QPushButton(), lambda: t("btn_change_theme"))
        btn_theme.setFixedHeight(28)
        btn_theme.setCursor(Qt.PointingHandCursor)
        bind_style(btn_theme, lambda: f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 6px;
                color: {_th.FG_BLUE_MUTED};
                font-size: 12px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.09)};
                border-color: {_th.border_color(0.15)};
                color: {_th.FG};
            }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
        """)
        yield

        def _open_theme_picker():
            from windows.theme_picker import ThemePickerDialog
            backdrop = _BlurOverlay(self, backdrop="texture")
            self._theme_picker_backdrop = backdrop
            from app_ext.class_BlurOverlay import install_window_fade
            dlg = ThemePickerDialog(parent=self)
            dlg._overlay = backdrop
            install_window_fade(dlg)
            try:
                dlg.exec()
            finally:
                backdrop.close_overlay()
                if getattr(self, "_theme_picker_backdrop", None) is backdrop:
                    self._theme_picker_backdrop = None

        btn_theme.clicked.connect(_open_theme_picker)

        theme_row.addWidget(_active_label)
        theme_row.addStretch()
        yield
        theme_row.addWidget(btn_theme)
        theme_outer.addLayout(theme_row)

        # ── divider ──
        _theme_sep = QWidget()
        _theme_sep.setFixedHeight(1)
        yield
        bind_style(_theme_sep, lambda: f"background: {_th.border_color(0.06)};")
        theme_outer.addSpacing(10)
        theme_outer.addWidget(_theme_sep)
        theme_outer.addSpacing(10)
        yield

        # ── shared effect-level dropdown ──
        from main_ext.class_WebToggle import WebToggle
        from app_ext.class_EffectLevelDropdown import EffectLevelDropdown

        blur_row = QHBoxLayout()
        blur_row.setSpacing(10)

        blur_text_col = QVBoxLayout()
        yield
        blur_text_col.setSpacing(2)

        blur_title = bind_text(QLabel(), lambda: t("lbl_effect_level"))
        bind_style(blur_title, lambda: f"font-size:12px; font-weight:600; color:{_th.FG}; background:transparent; border:none;")
        blur_desc = bind_text(QLabel(), lambda: t("txt_effect_level_desc"))
        yield
        blur_desc.setWordWrap(True)
        bind_style(blur_desc, lambda: f"font-size:11px; color:{_th.FG_DESC}; background:transparent; border:none;")
        blur_text_col.addWidget(blur_title)
        blur_text_col.addWidget(blur_desc)
        yield

        effect_level = EffectLevelDropdown()
        self._effect_level_button = effect_level

        blur_row.addLayout(blur_text_col, 1)
        blur_row.addWidget(effect_level)
        yield
        theme_outer.addLayout(blur_row)

        # ── divider ──
        _anim_sep = QWidget()
        _anim_sep.setFixedHeight(1)
        bind_style(_anim_sep, lambda: f"background: {_th.border_color(0.06)};")
        yield
        theme_outer.addSpacing(10)
        theme_outer.addWidget(_anim_sep)
        theme_outer.addSpacing(10)

        # ── animations toggle ──
        anim_row = QHBoxLayout()
        yield
        anim_row.setSpacing(10)

        anim_text_col = QVBoxLayout()
        anim_text_col.setSpacing(2)

        anim_title = bind_text(QLabel(), lambda: t("lbl_animations"))
        yield
        bind_style(anim_title, lambda: f"font-size:12px; font-weight:600; color:{_th.FG}; background:transparent; border:none;")
        anim_desc = bind_text(QLabel(), lambda: t("txt_animations_desc"))
        anim_desc.setWordWrap(True)
        bind_style(anim_desc, lambda: f"font-size:11px; color:{_th.FG_DESC}; background:transparent; border:none;")
        yield
        anim_text_col.addWidget(anim_title)
        anim_text_col.addWidget(anim_desc)

        anim_toggle = WebToggle(_theme.animations_enabled, neutral=True)
        def _set_project_animations():
            enabled = anim_toggle.isChecked()
            _theme.set_animations_enabled(enabled)
            if not enabled:
                self._clear_page_content_reveal()
                self._project_hover_effect.reset()
                indicator = self._project_delete_indicator
                self._project_delete_indicator = None
                if indicator is not None:
                    indicator.stop()
                pending = self._pending_project_removal
                if pending is not None:
                    pending(animate=False)
        yield

        anim_toggle.clicked.connect(_set_project_animations)

        anim_row.addLayout(anim_text_col, 1)
        anim_row.addWidget(anim_toggle)
        theme_outer.addLayout(anim_row)
        yield

        _mode_sep = QWidget()
        _mode_sep.setFixedHeight(1)
        bind_style(_mode_sep, lambda: f"background: {_th.border_color(0.06)};")
        theme_outer.addSpacing(10)
        yield
        theme_outer.addWidget(_mode_sep)
        theme_outer.addSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        yield
        mode_text_col = QVBoxLayout()
        mode_text_col.setSpacing(2)
        mode_title = bind_text(QLabel(), lambda: t("lbl_project_mode"))
        bind_style(mode_title, lambda: f"font-size:12px; font-weight:600; color:{_th.FG}; background:transparent; border:none;")
        yield
        mode_desc = bind_text(QLabel(), lambda: t("txt_tree_view_mode_desc"))
        mode_desc.setWordWrap(True)
        bind_style(mode_desc, lambda: f"font-size:11px; color:{_th.FG_DESC}; background:transparent; border:none;")
        mode_text_col.addWidget(mode_title)
        yield
        mode_text_col.addWidget(mode_desc)

        btn_project_mode = ChoiceDropdown(minimum_width=100)
        btn_project_mode.setObjectName("projectModeDropdown")
        mode_value = bind_text(btn_project_mode.caption, lambda: t("lbl_mode_single_dir") if _theme.tree_view_mode == "single_dir"
                            else t("lbl_mode_tree"))
        yield
        btn_project_mode.fit_width()
        self._project_mode_value = mode_value
        self._project_mode_button = btn_project_mode

        def _choose_project_mode(mode):
            _theme.set_tree_view_mode(mode)
            btn_project_mode.setCaption(t("lbl_mode_single_dir") if mode == "single_dir"
                                       else t("lbl_mode_tree"))
        yield

        btn_project_mode.clicked.connect(lambda: self._show_choice_popup(
            btn_project_mode,
            (("single_dir", t("lbl_mode_single_dir")),
             ("tree", t("lbl_mode_tree"))),
            _theme.tree_view_mode,
            _choose_project_mode,
        ))
        mode_row.addLayout(mode_text_col, 1)
        mode_row.addWidget(btn_project_mode)
        theme_outer.addLayout(mode_row)
        yield

        content_layout.addWidget(theme_card)
        content_layout.addSpacing(8)


        lang_card, lang_outer = build_card_base()

        btn_lang = ChoiceDropdown(minimum_width=80)
        yield
        lang_text = bind_text(btn_lang.caption, lambda: _ul.SUPPORTED.get(_ul._current_lang, "English"))
        btn_lang.fit_width()
        self._language_value = lang_text
        self._language_button = btn_lang
        yield

        def _open_lang_menu():
            self._show_lang_popup(btn_lang)

        btn_lang.clicked.connect(_open_lang_menu)

        lang_outer.addLayout(build_card_header("language", lambda: t("hdr_language"), btn_lang))
        content_layout.addWidget(lang_card)
        yield
        content_layout.addSpacing(8)

        self._build_activation_card(content_layout)

        # ======================
        # LICENSE / CREDIT CARD
        # ======================
        lic_card, lic_outer = build_card_base()

        def build_simple_row(icon_name, title_text, callback):
            row = QHBoxLayout()
            row.setSpacing(10)

            ic = QLabel()
            bind_pixmap(ic, lambda icon_name=icon_name: QIcon(icon(icon_name)).pixmap(20, 20))
            ic.setAlignment(Qt.AlignVCenter)

            title = bind_text(QLabel(), title_text)
            bind_style(title, lambda: f"font-size:13px; font-weight:600; color:{_th.FG};"
                f" background:transparent; border:none;")

            btn = bind_text(QPushButton(), lambda: t("btn_view"))
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            bind_style(btn, lambda: f"""
                QPushButton {{
                    background: {_th.border_color(0.05)};
                    border: 1px solid {_th.border_color(0.09)};
                    border-radius: 6px;
                    color: {_th.FG_BLUE_MUTED};
                    font-size: 12px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{ background: {_th.border_color(0.09)}; border-color: {_th.border_color(0.15)}; color: {_th.FG}; }}
                QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
            """)
            btn.clicked.connect(callback)

            row.addWidget(ic)
            row.addWidget(title)
            row.addStretch()
            row.addWidget(btn)

            return row
        yield


        # LICENSE
        lic_outer.addLayout(
            build_simple_row("legal", lambda: t("lbl_license"), self.open_license_window)
        )

        # DIVIDER
        divider = QFrame()
        divider.setFixedHeight(1)
        bind_style(divider, lambda: f"background: {_th.border_color(0.07)}; border:none;")
        yield
        lic_outer.addWidget(divider)

        # CREDITS
        lic_outer.addLayout(
            build_simple_row("user", lambda: t("lbl_credits"), self.open_credit_window)
        )

        content_layout.addWidget(lic_card)
        content_layout.addSpacing(8)
        yield

        content_layout.addStretch()


        yield
        scroll.verticalScrollBar().valueChanged.connect(self._stop_profile_card_transition)



        bar_set = QHBoxLayout()
        bar_set.setContentsMargins(16, 8, 16, 12)
        yield
        bar_set.addStretch()

        self.btn_back_settings = bind_text(QPushButton(), lambda: t("btn_back"))
        self.btn_back_settings.setLayoutDirection(Qt.LeftToRight)
        bind_icon(self.btn_back_settings, lambda: tint_icon("arrow_right", _th.FG2, 15))
        yield
        self.btn_back_settings.setIconSize(QSize(15, 15))
        self.btn_back_settings.clicked.connect(self._settings_back)

        bar_set.addWidget(self.btn_back_settings)
        footer = QWidget()
        yield
        footer.setLayout(bar_set)
        self._page_footers[self.page_settings] = footer
        self.page_settings_layout.addWidget(footer)

        # REGISTER PAGE
        yield
        self._refresh_startup_frame()

        # Load list — semua side effect (timer, network, project load) di-skip saat offscreen.
        if not getattr(self, "_offscreen_mode", False):
            self.load_projects()
            self.apply_window_state()
            self._start_account_features()

        # Connect after choosing the initial page so only navigation animates.
        self._page_footer_buttons = {
            self.page_projects: (self.btn_settings, self.btn_create),
            self.page_create: (self.btn_back_create, self.btn_settings_create, self.btn_create2),
            self.page_settings: (self.btn_back_settings,),
        }
        self._collect_profile_endpoints()
        yield
        self._last_page = self.stack.currentWidget()
        for page in (self.page_projects, self.page_create, self.page_settings):
            page._bound_theme_name = _th.name
        self.stack.currentChanged.connect(self._on_page_changed)
        self._project_header.installEventFilter(self)
        yield
        self._update_project_drive_layout()
        QTimer.singleShot(0, self, self._enable_project_layout_animation)
        self._create_popup_warm_timer = QTimer(self)
        self._create_popup_warm_timer.setSingleShot(True)
        yield
        self._create_popup_warm_timer.timeout.connect(self._prepare_create_popup)
        self._create_popup_warm_timer.start(0)
        self._wsl_disk_ui_ready = True
        self._apply_pending_wsl_disk_usage()
        yield
        self._apply_pending_windows_disk_usage()
        self._settings_warm_widgets = None
        self._settings_warm_timer = QTimer(self)
        self._settings_warm_timer.setSingleShot(True)
        yield
        self._settings_warm_timer.timeout.connect(self._warm_settings_page)
        self._settings_warm_timer.start(10)

        if not self._offscreen_mode:
            self._trash_timer.start()
            QTimer.singleShot(0, self, self._maintain_project_trash)
        yield


    def _warm_settings_page(self):
        if self._startup_rows is not None or getattr(self, "_cached_page_transition_active", False):
            self._settings_warm_timer.start(100)
            return
        if self._startup_reveal_started and not self._startup_revealed_emitted:
            return
        if self.page_settings.isVisible() or getattr(self, "_closing", False):
            return
        manager = getattr(QApplication.instance(), "_widget_gpu_manager", None)
        if manager is None:
            return
        if self._settings_warm_widgets is None:
            self.page_settings.ensurePolished()
            self.page_settings.resize(self.stack.contentsRect().size())
            self.page_settings.layout().activate()
            self._settings_warm_widgets = [self.page_settings] + self.page_settings.findChildren(QWidget)
        started = time.perf_counter()
        while self._settings_warm_widgets:
            widget = self._settings_warm_widgets.pop(0)
            manager._prepare(widget)
            if time.perf_counter()-started >= .004:
                self._settings_warm_timer.start(1)
                return
        # Glyphs and existing Skia widget surfaces are prepared once while the
        # page is hidden, instead of after the first Settings morph has begun.
        self.page_settings.layout().activate()
        self.page_settings.grab()

    def open_license_window(self):
        from windows.license_window import LicenseWindow
        from app_ext.class_BlurOverlay import install_window_fade
        ov = _BlurOverlay(self)
        dlg = LicenseWindow(self)
        dlg._overlay = ov
        install_window_fade(dlg)
        dlg.exec()
        ov.close_overlay()

    def open_credit_window(self):
        from windows.credit_window import CreditWindow
        from app_ext.class_BlurOverlay import install_window_fade
        ov = _BlurOverlay(self)
        dlg = CreditWindow(self)
        dlg._overlay = ov
        install_window_fade(dlg)
        dlg.exec()
        ov.close_overlay()

    def open_dependency_window(self):
        from windows.dependency_window import DependencyWindow
        from app_ext.class_BlurOverlay import install_window_fade
        ov = _BlurOverlay(self)
        dlg = DependencyWindow(self)
        dlg._overlay = ov
        install_window_fade(dlg)
        dlg.exec()
        ov.close_overlay()

    def get_project_icon(self, proj):

        icon_key = proj.get("icon", "project_list")

        try:
            return QIcon(icon(icon_key))
        except Exception:
            return QIcon(icon("project_list"))


    @staticmethod
    def _make_pin_pixmap(size=14, color="#ffffff"):
        from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen
        from PySide6.QtCore import QPointF
        px = QPixmap(size, size)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(color)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        s = size
        # pin head (top circle)
        p.drawEllipse(QPointF(s * 0.5, s * 0.28), s * 0.28, s * 0.28)
        # pin body (diamond/rhombus below head)
        body = QPainterPath()
        body.moveTo(s * 0.5, s * 0.52)
        body.lineTo(s * 0.22, s * 0.72)
        body.lineTo(s * 0.5, s * 0.92)
        body.lineTo(s * 0.78, s * 0.72)
        body.closeSubpath()
        p.drawPath(body)
        # needle (thin line at bottom)
        pen = QPen(c, s * 0.08)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.5, s * 0.88), QPointF(s * 0.5, s * 0.98))
        p.end()
        return px

    @staticmethod
    def _format_size(total_bytes: int) -> str:
        if total_bytes < 1024:
            return f"{total_bytes} B"
        elif total_bytes < 1024 * 1024:
            return f"{total_bytes / 1024:.1f} KB"
        elif total_bytes < 1024 * 1024 * 1024:
            return f"{total_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{total_bytes / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def _get_folder_size_str(path: str, check_interrupted=None) -> str:
        if not path or not os.path.exists(path):
            return "0 B"
        total = 0
        try:
            if os.path.isfile(path):
                total = os.path.getsize(path)
            else:
                stack = [path]
                while stack:
                    if check_interrupted and check_interrupted():
                        return None
                    current_dir = stack.pop()
                    try:
                        with os.scandir(current_dir) as it:
                            for index, entry in enumerate(it):
                                if index % 128 == 0 and check_interrupted and check_interrupted():
                                    return None
                                try:
                                    if entry.is_symlink():
                                        continue
                                    if entry.is_dir(follow_symlinks=False):
                                        stack.append(entry.path)
                                    elif entry.is_file(follow_symlinks=False):
                                        total += entry.stat(follow_symlinks=False).st_size
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except Exception:
            pass

        return AppUI._format_size(total)

    def build_project_item_widget(self, proj, item):
        from main_ext.class_ThemeUi import theme as _th
        trashed = bool(proj.get("_trash_id"))

        # Inherit the final viewport style before constructing descendants.
        # Reparenting a complete row later repolishes its entire subtree.
        root = _ProjectCardWidget(self.list_widget.viewport())
        root.hide()
        root.setAttribute(Qt.WA_TranslucentBackground)
        root.setAutoFillBackground(False)
        root.setStyleSheet("""
        QWidget {
            background: transparent;
        }
        """)
        if trashed:
            entry = next((entry for entry in self._trash_entries if entry["id"] == proj["_trash_id"]), None)
            if entry is not None:
                bind_language(root, "setToolTip", lambda entry=entry: self._trash_remaining_text(entry))

        layout = QHBoxLayout(root)
        layout.setContentsMargins(8, 2, 14, 2)
        layout.setSpacing(12)


        lbl_icon = QLabel()
        lbl_icon.setFixedSize(36, 36)
        bind_pixmap(lbl_icon, lambda proj=proj: self.get_project_icon(proj).pixmap(32, 32))
        lbl_icon.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(lbl_icon)


        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        text_col.setAlignment(Qt.AlignVCenter)

        # Project name
        lbl_name = _ElidedProjectLabel(proj["project_name"])
        bind_style(lbl_name, lambda: f"""
            font-size: 14px;
            font-weight: 600;
            color: {_th.FG_BRIGHT};
        """)
        lbl_name.ensurePolished()
        lbl_name.setFixedHeight(lbl_name.fontMetrics().height())
        text_col.addWidget(lbl_name)

        # Port info
        p1 = proj.get("port1", "")
        p2 = proj.get("port2", "")
        port_text = p1 if not p2 else f"{p1} - {p2}"

        lbl_ports = _ElidedProjectLabel(port_text)
        bind_style(lbl_ports, lambda: f"""
            font-size: 11px;
            color: {_th.FG2};
        """)
        lbl_ports.ensurePolished()
        lbl_ports.setFixedHeight(lbl_ports.fontMetrics().height())
        text_col.addWidget(lbl_ports)

        layout.addLayout(text_col, 1)


        right_side = QHBoxLayout()
        right_side.setSpacing(6)
        right_side.setAlignment(Qt.AlignVCenter)

        metadata = QWidget()
        metadata.setObjectName("projectMetadata")
        metadata.setFixedWidth(96)
        right_col = QVBoxLayout(metadata)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(3)
        right_col.setAlignment(Qt.AlignVCenter)

        path_row = QHBoxLayout()
        path_row.setSpacing(5)
        disk_lbl = QLabel()
        disk_lbl.setFixedSize(12, 12)
        bind_pixmap(disk_lbl, lambda: icon_pixmap("project_disk").scaled(
            12, 12, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))
        path_row.addWidget(disk_lbl)

        base_path = os.path.dirname(proj["path"])
        lbl_path = _ScrollingPathLabel(base_path, lambda: _th.FG3)
        path_width = 96 - 12 - 5 - (17 if proj.get("pinned") else 0)
        lbl_path.setFixedWidth(min(
            path_width, lbl_path.fontMetrics().horizontalAdvance(base_path) + 2
        ))
        path_row.addWidget(lbl_path)

        if proj.get("pinned"):
            pin_lbl = QLabel()
            pin_lbl.setFixedSize(12, 12)
            bind_pixmap(pin_lbl, lambda: self._make_pin_pixmap(12, _th.accent_hex))
            bind_language(pin_lbl, "setToolTip", lambda: t("tooltip_pinned"))
            path_row.addWidget(pin_lbl)

        path_row.addStretch()
        right_col.addLayout(path_row)

        lbl_size = bind_text(QLabel(), lambda proj=proj: f"{t('lbl_size')}{proj.get('cached_size', 'Calculating...')}")
        bind_style(lbl_size, lambda: f"font-size: 10px; color: {_th.FG_MUTED};")
        right_col.addWidget(lbl_size)

        if not hasattr(self, "_project_size_labels"):
            self._project_size_labels = {}
        if not trashed:
            self._project_size_labels[proj.get("path", "")] = lbl_size

        right_side.addWidget(metadata)

        btn_menu = PressAnimatedButton()
        btn_menu.setObjectName("projectMenuButton")
        btn_menu.setFixedSize(24, 28)
        bind_icon(btn_menu, lambda: QIcon(icon_pixmap("project_more")))
        btn_menu.setIconSize(QSize(14, 14))
        btn_menu.setCursor(Qt.PointingHandCursor)
        bind_language(btn_menu, "setToolTip", lambda: t("menu_project_options"))
        bind_style(btn_menu, lambda: f"""
            QPushButton#projectMenuButton {{
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 0;
            }}
            QPushButton#projectMenuButton:hover {{
                background-color: {_th.accent(0.14)};
            }}
            QPushButton#projectMenuButton:pressed {{
                background-color: {_th.accent(0.22)};
            }}
        """)
        btn_menu.clicked.connect(
            lambda _checked=False: self.open_project_menu(item, btn_menu)
        )
        right_side.addWidget(btn_menu)

        btn_open = None
        if not trashed:
            btn_open = PressAnimatedButton()
            btn_open.setObjectName("projectOpenButton")
            btn_open.setFixedSize(28, 28)
            bind_icon(btn_open, lambda: QIcon(icon_pixmap("arrow_right")))
            btn_open.setIconSize(QSize(12, 12))
            btn_open.setCursor(Qt.PointingHandCursor)
            bind_language(btn_open, "setToolTip", lambda: t("menu_open_project"))
            bind_style(btn_open, lambda: f"""
                QPushButton#projectOpenButton {{
                    background-color: {_th.BG4};
                    border: 1px solid {_th.border_color(0.05)};
                    border-radius: 8px;
                    padding: 0;
                }}
                QPushButton#projectOpenButton:hover {{
                    background-color: {_th.accent(0.18)};
                    border-color: {_th.accent(0.30)};
                }}
                QPushButton#projectOpenButton[cardHovered="true"] {{
                    background-color: {_th.accent(0.18)};
                    border-color: {_th.accent(0.30)};
                }}
                QPushButton#projectOpenButton:pressed {{
                    background-color: {_th.accent(0.28)};
                }}
            """)
            btn_open.clicked.connect(lambda _checked=False: self.open_project(item))
            right_side.addWidget(btn_open)
        layout.addLayout(right_side)
        root.set_open_button(btn_open)
        root.set_hover_callback(
            lambda hovered: self._on_project_card_hover(item, hovered)
        )
        root.set_press_callback(
            lambda pressed: self._on_project_card_press(item, pressed)
        )

        return root

    def _on_project_card_hover(self, item, hovered):
        if self._project_delete_active or self._project_cards_transition is not None:
            return
        effect = self._project_hover_effect
        from main_ext.class_ThemeUi import theme as _th
        if not _th.animations_enabled:
            effect.reset()
            return
        if hovered:
            effect.set_hovered_item(item)
        elif effect.item is item:
            effect.set_hovered_item(None)

    def _on_project_card_press(self, item, pressed):
        if self._project_delete_active or self._project_cards_transition is not None:
            return
        from main_ext.class_ThemeUi import theme as _th
        if not _th.animations_enabled:
            return
        self._project_hover_effect.set_pressed_item(item, pressed)


    def _reinit(self, skip_widgets=None):

        self._clear_page_content_reveal()
        self.stack.blockSignals(True)
        self._stop_update_features()

        skip = set(skip_widgets) if skip_widgets else set()

        # Persistent dialogs (mis. ThemePickerDialog) — jangan ke-delete saat ganti tema.
        # Skip dialog itu sendiri DAN semua child widget di dalamnya.
        for dlg in getattr(self, "_persistent_dialogs", []) or []:
            try:
                skip.add(dlg)
                for sub in dlg.findChildren(QWidget):
                    skip.add(sub)
            except Exception:
                pass

        # CrossfadeOverlay (snapshot saat transisi) — jangan ke-delete saat
        # rebuild, dia harus survive sampai fade-out selesai.
        _xfade = getattr(self, "_lang_xfade", None)
        if _xfade is not None:
            skip.add(_xfade)
        theme_overlay = getattr(self, "_theme_xfade_overlay", None)
        if theme_overlay is not None:
            skip.add(theme_overlay)
        picker_backdrop = getattr(self, "_theme_picker_backdrop", None)
        if picker_backdrop is not None:
            skip.add(picker_backdrop)


        _lo = getattr(self, "_login_overlay", None)
        if _lo is not None:
            try:
                for w in _lo.widgets_to_keep():
                    skip.add(w)
                    for sub in w.findChildren(QWidget):
                        skip.add(sub)
            except Exception:
                pass

        for child in self.findChildren(QWidget):
            if child in skip:
                continue
            child.setParent(None)
            child.deleteLater()

        old_layout = self.layout()
        if old_layout:
            QWidget().setLayout(old_layout)

        self._build_ui()

    def paintEvent(self, event):
        from main_ext.class_ThemeUi import theme as _th
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(_th.BG))
        painter.end()
        super().paintEvent(event)

    def eventFilter(self, watched, event):
        if (hasattr(self, "list_widget") and watched is self.list_widget.viewport()
                and event.type() in (QEvent.Resize, QEvent.Show)):
            self._update_trash_hint()
        if (watched is getattr(self, "_project_header", None)
                and event.type() in (QEvent.Resize, QEvent.Show)):
            self._update_project_drive_layout()
            if event.type() == QEvent.Show and not self._project_layout_animation_ready:
                QTimer.singleShot(0, self, self._enable_project_layout_animation)
        return super().eventFilter(watched, event)

    def _enable_project_layout_animation(self):
        if not self._project_layout_animation_ready and self._project_header.isVisible():
            self._update_project_drive_layout(animate=False)
            self._project_layout_animation_ready = True

    def _stop_project_layout_transition(self):
        transition = getattr(self, "_project_layout_transition", None)
        if transition is not None:
            self._project_layout_transition = None
            transition.stop()

    def _update_project_drive_layout(self, animate=True):

        if self._disk_cards_transition is not None:
            self._stop_disk_cards_transition(reflow=False)
        sidebar = getattr(self, "_project_drive_sidebar", None)
        if sidebar is None:
            return
        header = self._project_header_layout
        margins = header.contentsMargins()
        count = len(self._visible_project_drive_cards())
        width = count * self._project_drive_card_width + max(0, count - 1) * 8
        required = (margins.left() + margins.right()
                    + self._project_header_title.sizeHint().width()
                    + width + 6 + self.search_input.maximumWidth()
                    + 3 * header.spacing())
        in_sidebar = (self._trash_drive_layout[0] if self._trash_view and self._trash_drive_layout is not None
                      else bool(count and self._project_header.width() < required))
        self._project_drive_container.setMinimumHeight(
            self._trash_drive_layout[1] if self._trash_view and in_sidebar and self._trash_drive_layout is not None else 0
        )
        if in_sidebar == self._project_drives_in_sidebar:
            self._project_drive_container.setFixedWidth(
                self._project_drive_card_width if in_sidebar else width + 6
            )
            return

        from main_ext.class_ThemeUi import theme
        animate = (animate and self._project_layout_animation_ready
                   and self._project_drives_in_sidebar is not None
                   and self.page_projects.isVisible() and theme.animations_enabled
                   and self._startup_splash is None
                   and self._startup_content_reveal is None)
        source_cards = [
            (entry["widget"], QRect(entry["widget"].mapTo(self.page_projects, QPoint()), entry["widget"].size()))
            for entry in self._visible_project_drive_cards().values()
        ]
        source_list = QRect(self.list_widget.geometry())
        enabled = self.page_projects.updatesEnabled()
        self.page_projects.setUpdatesEnabled(False)
        try:
            self._stop_project_layout_transition()
            # Reparent existing widgets, retaining drive filters and usage counters.
            scroll = self.list_widget.verticalScrollBar()
            scroll_position = scroll.value()
            container = self._project_drive_container
            if in_sidebar:
                header.removeWidget(container)
                self._project_drives_layout.setDirection(QBoxLayout.TopToBottom)
                self._project_drives_layout.setContentsMargins(0, 8, 0, 0)
                self._project_drives_layout.setAlignment(Qt.AlignTop)
                container.setFixedWidth(self._project_drive_card_width)
                sidebar.setWidget(container)
                sidebar.show()
                self._projects_body_layout.setContentsMargins(12, 0, 0, 0)
            else:
                if sidebar.widget() is container:
                    sidebar.takeWidget()
                sidebar.hide()
                self._project_drives_layout.setDirection(QBoxLayout.LeftToRight)
                self._project_drives_layout.setContentsMargins(6, 0, 0, 0)
                self._project_drives_layout.setAlignment(Qt.AlignmentFlag(0))
                container.setFixedWidth(width + 6)
                header.insertWidget(1, container)
                container.setVisible(bool(count))
                self._projects_body_layout.setContentsMargins(0, 0, 0, 0)
            self._project_drives_in_sidebar = in_sidebar
            self._projects_body_layout.activate()
            header.activate()
            self._project_drives_layout.activate()
            scroll.setValue(scroll_position)
            if animate:
                transition = ProjectLayoutTransition(
                    self.page_projects, self.list_widget, self._projects_body_layout,
                    sidebar, self._project_drives_layout, source_cards, source_list, in_sidebar,
                )
                self._project_layout_transition = transition

                def _finished():
                    if self._project_layout_transition is transition:
                        self._project_layout_transition = None
                        self._filter_projects(self.search_input.text(), animate=True)

                transition.finished.connect(_finished)
                transition.start()
        finally:
            self.page_projects.setUpdatesEnabled(enabled)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        transition = getattr(self, "_footer_action_transition", None)
        if transition is not None:
            self._footer_action_transition = None
            transition.stop()
        self._stop_profile_card_transition()
        splash = getattr(self, "_startup_splash", None)
        if splash is not None:
            splash.setGeometry(self.rect())
            splash.raise_()
        # Login overlay (blur backdrop + kartu ter-center) ikut window resize.
        lo = getattr(self, "_login_overlay", None)
        if lo is not None:
            lo.reposition()

    def _rebuild_ui(self):

        try:
            cur_idx = self.stack.currentIndex()
        except Exception:
            cur_idx = 0

        scroll_pos = {}
        for name, attr in (("settings", "_scroll_settings"), ("create", "_scroll_create")):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    scroll_pos[name] = obj.verticalScrollBar().value()
                except Exception:
                    pass

        self._reinit()
        picker_backdrop = getattr(self, "_theme_picker_backdrop", None)
        if picker_backdrop is not None:
            picker_backdrop.raise_()

        try:
            if 0 <= cur_idx < self.stack.count():
                # Theme/language rebuilds already have their own transition.
                with QSignalBlocker(self.stack):
                    self.stack.setCurrentIndex(cur_idx)
                self._last_page = self.stack.currentWidget()
        except Exception:
            pass
        for name, attr in (("settings", "_scroll_settings"), ("create", "_scroll_create")):
            if name in scroll_pos:
                obj = getattr(self, attr, None)
                if obj is not None:
                    try:
                        obj.verticalScrollBar().setValue(scroll_pos[name])
                    except Exception:
                        pass

        for dlg in getattr(self, "_persistent_dialogs", []) or []:
            try:
                if hasattr(dlg, "_rebuild_for_theme"):
                    dlg._rebuild_for_theme()
            except Exception:
                pass

    def _capture_theme_window_snapshot(self, dialog=None):

        app_rect = QRect(self.mapToGlobal(self.rect().topLeft()), self.size())
        background = self.grab()
        picker = picker_rect = None
        if dialog is not None and dialog.isVisible():
            picker_rect = QRect(dialog.mapToGlobal(dialog.rect().topLeft()), dialog.size())
            picker = dialog.grab()
        bounds = app_rect.united(picker_rect) if picker_rect is not None else app_rect
        scale = background.devicePixelRatio()
        snapshot = QPixmap(max(1, round(bounds.width() * scale)),
                           max(1, round(bounds.height() * scale)))
        snapshot.setDevicePixelRatio(scale)
        snapshot.fill(Qt.transparent)
        painter = QPainter(snapshot)
        painter.drawPixmap(app_rect.topLeft() - bounds.topLeft(), background)
        if picker is not None:
            painter.drawPixmap(picker_rect.topLeft() - bounds.topLeft(), picker)
        painter.end()
        return snapshot, bounds

    def _clear_theme_snapshot_overlay(self):
        anim = getattr(self, "_theme_xfade_anim", None)
        if anim is not None:
            try:
                anim.stop()
                anim.deleteLater()
            except RuntimeError:
                pass
        overlay = getattr(self, "_theme_xfade_overlay", None)
        dialog = getattr(self, "_theme_overlay_dialog", None)
        if dialog is not None and getattr(dialog, "_theme_snapshot_overlay", None) is overlay:
            dialog._theme_snapshot_overlay = None
        if overlay is not None:
            try:
                overlay.close()
            except RuntimeError:
                pass
        self._theme_xfade_anim = None
        self._theme_xfade_overlay = None
        self._theme_overlay_dialog = None

    def _prepare_theme_snapshot(self, dialog=None):

        self._clear_theme_snapshot_overlay()
        snapshot, bounds = self._capture_theme_window_snapshot(dialog)
        overlay = _ThemeSnapshotOverlay(dialog or self, snapshot, bounds)
        self._theme_xfade_overlay = overlay
        self._theme_overlay_dialog = dialog
        if dialog is not None:
            dialog._theme_snapshot_overlay = overlay
        overlay.show()
        overlay.raise_()
        # Commit the fully opaque first frame before theme.set() can rebuild
        # the widgets behind it. show() alone only queues a paint on Windows.
        overlay.repaint()
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _on_theme_changed(self):

        overlay = getattr(self, "_theme_xfade_overlay", None)
        if overlay is None:
            dialog = next((d for d in getattr(self, "_persistent_dialogs", [])
                           if d.isVisible()), None)
            self._prepare_theme_snapshot(dialog)
            overlay = self._theme_xfade_overlay
        try:
            self._refresh_theme_ui()
            overlay.raise_()
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            overlay.raise_()
        except Exception:
            self._clear_theme_snapshot_overlay()
            raise

        anim = QPropertyAnimation(overlay, b"windowOpacity", overlay)
        anim.setDuration(280)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Linear)

        def _on_done():
            if self._theme_xfade_overlay is overlay:
                self._clear_theme_snapshot_overlay()

        anim.finished.connect(_on_done)
        self._theme_xfade_anim = anim
        anim.start()

    def _refresh_theme_page(self, page):

        from main_ext.class_ThemeUi import theme as _th
        if page is None or getattr(page, "_bound_theme_name", None) == _th.name:
            return
        if page is self.page_projects:
            self._project_hover_effect.background = QColor(_th.BG)
            self.list_widget.set_viewport_background(_th.BG)
            for index, (drive, entry) in enumerate(self._project_drive_cards.items()):
                if entry.get("trash"):
                    self._apply_project_drive_state(drive)
                    continue
                special = bool(entry.get("distro"))
                entry["fill"] = self._project_drive_fill(special)
                if special:
                    entry["bar_color"] = _th.accent_hex
                else:
                    entry["bar_color"] = (self._normal_drive_colors.get(drive)
                                          or _th.gauge_hex(int(entry["usage_fraction"] * 100)))
                active = self._project_drive_icon(special)
                muted = QPixmap(active.size())
                muted.setDevicePixelRatio(active.devicePixelRatioF())
                muted.fill(Qt.transparent)
                painter = QPainter(muted)
                painter.setOpacity(0.38)
                painter.drawPixmap(0, 0, active)
                painter.end()
                entry["icon_active"], entry["icon_muted"] = active, muted
                self._apply_project_drive_state(drive)
        refresh_theme(page)
        page._bound_theme_name = _th.name

    def _refresh_theme_ui(self):

        from main_ext.class_ThemeUi import theme as _th
        self._clear_page_content_reveal()
        refresh_theme(self, recursive=False)
        self._refresh_theme_page(self.stack.currentWidget())
        self._theme_name_label.setText(_th.palettes()[_th.name]["label"])
        self._refresh_settings_badge()
        for dialog in getattr(self, "_persistent_dialogs", []) or []:
            if hasattr(dialog, "_refresh_for_theme"):
                dialog._refresh_for_theme()

    def _refresh_language_ui(self):
        refresh_language(self, skip_widgets=(self._language_button,))
        self._update_project_drive_layout()
        self._project_mode_button.fit_width()
        self._refresh_settings_badge()
        for dialog in getattr(self, "_persistent_dialogs", []) or []:
            if hasattr(dialog, "_refresh_for_language"):
                dialog._refresh_for_language()

    def _on_lang_changed(self):

        from app_ext.class_BlurOverlay import CrossfadeOverlay

        button = self._language_button
        caption = _ul.SUPPORTED.get(_ul._current_lang, "English")
        if button.currentText() != caption:
            button.setCaption(caption)
        parent = button.parentWidget()
        while parent is not None:
            if parent.layout() is not None:
                parent.layout().activate()
            parent = parent.parentWidget()

        existing = getattr(self, "_lang_xfade", None)
        if existing is not None:
            try:
                existing.hide()
                existing.deleteLater()
            except Exception:
                pass

        try:
            self._lang_xfade = CrossfadeOverlay(self, exclude_widgets=(button,))
        except Exception:
            self._lang_xfade = None

        def _update_captions():
            try:
                self._clear_page_content_reveal()
                self._refresh_language_ui()
            finally:
                ov = getattr(self, "_lang_xfade", None)
                if ov is not None:
                    try:
                        ov.setGeometry(self.rect())
                        ov.refresh_exclusions()
                        ov.raise_()
                        ov.fade_out(duration=240)
                    except Exception:
                        try:
                            ov.deleteLater()
                        except Exception:
                            pass
                    self._lang_xfade = None

        QTimer.singleShot(0, self, _update_captions)

    def _show_lang_popup(self, anchor: "QPushButton"):
        self._show_choice_popup(anchor, _ul.SUPPORTED.items(), _ul._current_lang, set_lang)

    def _show_choice_popup(self, anchor: "QPushButton", choices, current, on_select):
        show_choice_popup(anchor, choices, current, on_select)

    def has_project(self) -> bool:
        return bool(_load_projects_json() or self._trash_entries or self._trash_view or self._trash_read_error)

    def _settings_back(self):
        self._show_page(self._settings_back_target)

    def _show_page(self, page):
        self.stack.setCurrentWidget(page)

    def apply_window_state(self):
        has_proj = self.has_project()


        self.page_projects.setVisible(has_proj)


        if has_proj:
            self.stack.setCurrentWidget(self.page_projects)
        else:
            self.stack.setCurrentWidget(self.page_create)


        if has_proj:
            # NORMAL MODE
            self.btn_back_create.show()
            self.btn_settings_create.hide()
        else:
            # FIRST TIME MODE (NO PROJECT)
            self.btn_back_create.hide()
            self.btn_settings_create.show()


        self._settings_back_target = (
            self.page_projects if has_proj else self.page_create
        )


    def delete_project(self, proj):
        if self._project_delete_active or self._trash_view:
            return
        self._stop_project_cards_transition()
        from main_ext.class_ThemeUi import theme as _th
        animate_delete = _th.animations_enabled
        name = proj["project_name"]
        path = proj["path"]

        if not AppDialog.confirm_close(self, t("dlg_trash_title"),
            t("dlg_trash_msg", name=name),
            close_label=t("dlg_btn_delete"), cancel_label=t("dlg_btn_cancel")):
            return

        self._stop_project_size_scanners()

        if not hasattr(self, "_delete_workers"):
            self._delete_workers = []

        self._project_delete_active = True
        self._pending_project_removal = None
        self._project_hover_effect.reset()
        # The viewport graphics effect can cache away child snapshot widgets.
        # Let the deletion ghosts paint directly on the viewport instead.
        self._project_hover_effect.setEnabled(False)
        # Keep the list enabled so Qt does not render its icons as disabled.
        # Ignore pointer input while the deletion animation owns the cards.
        self.list_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.search_input.setEnabled(False)
        target = os.path.normcase(os.path.abspath(path))
        item = next((
            self.list_widget.item(i) for i in range(self.list_widget.count())
            if os.path.normcase(os.path.abspath(
                self.list_widget.item(i).data(Qt.UserRole).get("path", "")
            )) == target
        ), None)
        if item is not None and animate_delete:
            self._project_delete_indicator = _ProjectDeleteIndicator(
                self.list_widget, item, _th.accent_hex
            )
        indicator_started = time.monotonic()

        worker = DeleteProjectWorker(proj, self._trash)
        self._delete_workers.append(worker)

        def release_worker():
            try:
                self._delete_workers.remove(worker)
            except ValueError:
                pass
            worker.deleteLater()

        # The result can arrive before QThread.run() has actually returned.
        worker.finished.connect(release_worker)

        def _done(ok, deleted_path, err):
            if self._wsl_disk_closed:
                return
            def stop_indicator():
                indicator = self._project_delete_indicator
                self._project_delete_indicator = None
                if indicator is not None:
                    indicator.stop()

            if not ok:
                stop_indicator()
                self._pending_project_removal = None
                self._project_delete_active = False
                self._project_hover_effect.setEnabled(True)
                self.list_widget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                self.search_input.setEnabled(True)
                self._filter_projects(self.search_input.text(), animate=True)
                AppDialog.error(self, "Error", err)
                return

            target = os.path.normcase(os.path.abspath(deleted_path))
            self._all_projects = [
                p for p in self._all_projects
                if os.path.normcase(os.path.abspath(p.get("path", ""))) != target
            ]
            self._refresh_trash_state(render=False)

            started = False

            def start_removal(animate=None):
                nonlocal started
                if started:
                    return
                started = True
                self._pending_project_removal = None
                stop_indicator()
                if animate is None:
                    animate = animate_delete and _th.animations_enabled
                self._animate_project_removal(item, deleted_path, animate=animate)

            # Small folders can disappear before a single progress frame paints.
            remaining_ms = max(0, 550 - int((time.monotonic() - indicator_started) * 1000))
            if not animate_delete or not _th.animations_enabled:
                start_removal(animate=False)
            elif item is not None and remaining_ms:
                self._pending_project_removal = start_removal
                QTimer.singleShot(remaining_ms, start_removal)
            else:
                start_removal()

        worker.done.connect(_done)
        worker.start()

    def _animate_project_removal(self, item, deleted_path, animate=True, on_finished=None):
        project_list = self.list_widget
        row = project_list.row(item) if item is not None else -1
        trashed = item is not None and bool(item.data(Qt.UserRole).get("_trash_id"))

        def finish():
            if item is not None:
                row_now = project_list.row(item)
                if row_now >= 0:
                    removed = project_list.takeItem(row_now)
                    del removed
            if not trashed:
                self._project_size_labels.pop(deleted_path, None)
            self._project_delete_active = False
            self._project_hover_effect.setEnabled(True)
            project_list.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.search_input.setEnabled(True)
            if on_finished is not None:
                on_finished()
            self._filter_projects(self.search_input.text(), animate=True)
            self.apply_window_state()

        if not animate or row < 0:
            finish()
            return

        viewport = project_list.viewport()
        target_rect = project_list.visualItemRect(item)
        if not target_rect.intersects(viewport.rect()):
            finish()
            return

        transition = ProjectCardsTransition(
            project_list, [(item, True)], _PROJECT_REMOVING_ROLE, _snapshot_project_row,
        )
        self._project_cards_transition = transition

        def _finished():
            if self._project_cards_transition is transition:
                self._project_cards_transition = None
            finish()

        transition.finished.connect(_finished)
        transition.start()

    def toggle_pin_project(self, proj):
        if self._project_delete_active:
            return
        MAX_PINS = 3
        data = _load_projects_json()
        target = next((p for p in data if p["path"] == proj["path"]), None)
        if not target:
            return
        currently_pinned = target.get("pinned", False)
        if not currently_pinned:
            pinned_count = sum(1 for p in data if p.get("pinned"))
            if pinned_count >= MAX_PINS:
                AppDialog.warning(self, t("dlg_pin_limit_title"),
                    t("dlg_pin_limit_msg", n=MAX_PINS))
                return
        target["pinned"] = not currently_pinned
        _save_projects_json(data)
        self.load_projects()

    def open_project_menu(self, item, anchor):
        if self._project_delete_active:
            return
        active_popup = getattr(self, "_project_popup", None)
        if active_popup is not None:
            try:
                active_popup.close()
                return
            except RuntimeError:
                self._project_popup = None

        proj = item.data(Qt.UserRole)
        if not proj:
            return

        global_pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        try:
            self._show_project_popup(global_pos, item, proj)
        finally:
            # Qt.Popup can swallow the button's Leave event, leaving :hover
            # painted after the popup closes. Pin/delete may rebuild the list.
            try:
                anchor.setDown(False)
                anchor.setAttribute(Qt.WA_UnderMouse, False)
                QApplication.sendEvent(anchor, QEvent(QEvent.Leave))
                anchor.style().unpolish(anchor)
                anchor.style().polish(anchor)
                anchor.update()
            except RuntimeError:
                pass

    def _show_project_popup(self, global_pos, item, proj):

        from PySide6.QtWidgets import QVBoxLayout as _VL
        from main_ext.class_ThemeUi import theme as _th

        popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        self._project_popup = popup

        def _clear_project_popup():
            if getattr(self, "_project_popup", None) is popup:
                self._project_popup = None

        popup.destroyed.connect(_clear_project_popup)

        outer = _VL(popup)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from PySide6.QtGui import QColor as _QC
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
            fg      = _th.FG if not danger else _th.RED_LIGHT
            fg_h    = _th.FG_BRIGHT if not danger else _th.RED_LIGHTER
            bg_h    = _th.accent(0.14) if not danger else _th.red(0.14)
            fg_dis  = _th.FG_DISABLED
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    color: {fg if enabled else fg_dis};
                    font-family: 'Poppins';
                    font-size: 12px;
                    text-align: left;
                    padding: 0 14px;
                }}
                QPushButton:hover {{ background: {bg_h}; color: {fg_h}; }}
                QPushButton:pressed {{ background: {_th.border_color(0.08)}; }}
            """)
            lay.addWidget(b)
            return b

        def _sep():
            d = QWidget()
            d.setFixedHeight(1)
            d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
            lay.addWidget(d)

        def _pick(action):
            result[0] = action
            popup.close()

        for index, (label, action, danger, enabled) in enumerate(self._project_menu_actions(proj)):
            if index:
                _sep()
            button = _btn(label, enabled=enabled, danger=danger)
            button.clicked.connect(lambda _checked=False, action=action: _pick(action))

        card.adjustSize()
        popup.adjustSize()
        _clamp_popup(popup, global_pos, self)
        card.freeze(self, popup.pos())
        popup.show()

        # event loop lokal — tunggu sampai popup ditutup
        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()

        if result[0] == "open":
            self.open_project(item)
        elif result[0] == "pin":
            self.toggle_pin_project(proj)
        elif result[0] == "delete":
            if proj.get("_trash_id"):
                self._delete_trash_project(proj["_trash_id"])
            else:
                self.delete_project(proj)
        elif result[0] == "restore":
            self._restore_trash_project(proj["_trash_id"])

    def _project_menu_actions(self, proj):
        identifier = proj.get("_trash_id")
        if identifier:
            entry = next((entry for entry in self._trash_entries if entry["id"] == identifier), None)
            available = bool(entry and entry.get("handle"))
            return [(t("dlg_btn_delete"), "delete", True, available),
                    (t("btn_restore_project"), "restore", False, available)]
        return [(t("menu_open_project"), "open", False, True),
                (t("menu_unpin_project" if proj.get("pinned") else "menu_pin_project"), "pin", False, True),
                (t("menu_delete_project"), "delete", True, True)]

    def _apply_port_mode(self):
        dual = getattr(self, "_port_mode", 1) == 2
        if dual:
            self._div2_ports.show()
            self._lbl_port2.show()
            self._inp_port2.show()
        else:
            self._div2_ports.hide()
            self._lbl_port2.hide()
            self._inp_port2.hide()
            self.input_port2.clear()

    def update_port_mode(self):
        self._apply_port_mode()


    def select_path(self):
        path = QFileDialog.getExistingDirectory(self, t("dlg_choose_folder"))
        if path:
            self.path_label.setText(path)

    def _show_create_popup(self):
        from app_ext.class_CreateProjectDialog import CreateProjectDialog
        self._clear_page_content_reveal()
        CreateProjectDialog.show_popup(self)

    def _prepare_create_popup(self):
        if self._startup_rows is not None or getattr(self, "_cached_page_transition_active", False):
            self._create_popup_warm_timer.start(100)
            return
        if self._startup_reveal_started and not self._startup_revealed_emitted:
            return
        from app_ext.class_CreateProjectDialog import CreateProjectDialog
        CreateProjectDialog.prepare_popup(self)

    def _on_popup_project_created(self, record):
        all_proj = _load_projects_json()
        all_proj.append(record)
        _save_projects_json(all_proj)

        proj_root = record["path"]
        p1 = record["port1"]
        p2 = record.get("port2") or None
        opened_at = time.perf_counter()
        log_project_event("OPEN_START", proj_root, origin="create_popup")

        self.load_projects()
        self.apply_window_state()
        self.hide()

        self.loader = LoadingWindow(proj_root)
        self.loader._block_close = True   # block close sampai Main ready
        self.loader.show()
        QApplication.processEvents()

        _hide_requested = [False]

        def _on_hide_requested():
            _hide_requested[0] = True
            log_project_event("OPEN_CANCELLED", proj_root, origin="create_popup")
            self._cancel_project_preparation(getattr(self, "editor", None))
            self.show()  # kembalikan AppUI

        self.loader._on_hide_requested = _on_hide_requested


        try:
            from main import Main
            self.editor = Main(
                proj_root, p1, p2,
                profile_provider=self.profile_provider
            )
        except Exception as e:
            self._handle_project_open_failure(proj_root, "create_popup", e)
            return
        self.editor.return_to_project_signal.connect(self.on_return_from_editor)
        editor = self.editor
        self._bind_project_preparation(editor, proj_root, "create_popup")

        def on_main_ready():
            if getattr(self, "editor", None) is not editor:
                return
            if self.loader is not None:
                self.loader._block_close = False  # sekarang boleh close beneran
            if _hide_requested[0]:
                self._cancel_project_preparation(editor)
                log_project_event("OPEN_CANCELLED", proj_root, origin="create_popup")
                return
            self._show_editor_with_reveal(proj_root, opened_at, "create_popup")

        self.editor.ready.connect(on_main_ready)


    def _disk_cards_busy(self):
        return (not self._wsl_disk_ui_ready
                or self._project_delete_active
                or any((self._startup_content_reveal, self._page_content_reveal,
                        self._project_layout_transition, self._project_drive_usage_animation,
                        self._disk_cards_transition, self._project_cards_transition)))

    def _refresh_windows_disks(self):
        if self._wsl_disk_closed or self._windows_disk_scan_running:
            return
        self._windows_disk_scan_running = True

        def _scan():
            from core.disk_monitor import list_windows_disk_usage
            try:
                usage = list_windows_disk_usage()
            except Exception:
                usage = None
            try:
                self.windows_disk_usage_ready.emit(usage)
            except RuntimeError:
                pass

        _threading.Thread(target=_scan, name="rak-disk-monitor", daemon=True).start()

    def _on_windows_disk_usage_ready(self, usage):
        self._windows_disk_scan_running = False
        if self._wsl_disk_closed or usage is None:
            return
        self._windows_disk_usage = usage
        present = {entry["key"].casefold() for entry in usage}
        self._unavailable_project_drives = {f"{chr(65+i)}:".casefold() for i in range(26)} - present
        if self._wsl_disk_ui_ready:
            self._filter_projects(self.search_input.text(), animate=True)
        self._pending_windows_disk_usage = usage
        self._apply_pending_windows_disk_usage()

    def _apply_pending_windows_disk_usage(self):
        if self._wsl_disk_closed or self._pending_windows_disk_usage is None or not self._wsl_disk_ui_ready:
            return
        if self._disk_cards_busy():
            QTimer.singleShot(100, self, self._apply_pending_windows_disk_usage)
            return
        usage = self._pending_windows_disk_usage
        self._pending_windows_disk_usage = None
        self._sync_project_disk_cards(usage, special=False)

    def _stop_disk_cards_transition(self, reflow=True):
        transition = self._disk_cards_transition
        if transition is not None:
            self._disk_cards_transition = None
            transition.stop()
            if reflow and hasattr(self, "_project_drive_sidebar"):
                self._update_project_drive_layout(animate=False)

    def _storage_available(self, key):
        value = str(key or "").casefold()
        if value.startswith("wsl:"):
            return any(value == drive.casefold() for drive in self._project_drive_cards)
        return value not in self._unavailable_project_drives

    def _sync_project_disk_cards(self, usage, special):
        from main_ext.class_ThemeUi import theme as _th
        desired = {entry["key"]: entry for entry in usage}
        old_keys = {key for key, entry in self._project_drive_cards.items()
                    if not entry.get("trash") and bool(entry.get("distro")) == special}
        changed = old_keys != desired.keys()
        animate = (changed and not self._trash_view and _th.animations_enabled and self.page_projects.isVisible()
                   and self._startup_splash is None and self._project_layout_animation_ready)
        host = self._project_drive_sidebar.viewport() if self._project_drives_in_sidebar else self._project_header
        source = DiskCardsTransition.capture(host, self._visible_project_drive_cards()) if animate else None
        page = self.page_projects
        enabled = page.updatesEnabled()
        page.setUpdatesEnabled(False)
        try:
            for key in old_keys - desired.keys():
                entry = self._project_drive_cards.pop(key)
                self._project_drives_layout.removeWidget(entry["widget"])
                entry["widget"].hide()
                entry["widget"].deleteLater()
            for key, data in desired.items():
                entry = self._project_drive_cards.get(key)
                reported = data["percent"] if special else None
                if entry is None:
                    self._add_project_drive_card(key, data["total"], data["used"],
                                                 distro=data.get("distro"), usage_percent=reported)
                else:
                    entry["usage_fraction"] = data["used"] / data["total"] if reported is None else reported / 100
                    entry["usage_percent"] = reported
                    entry["usage"].set_usage_values(data["used"] / 1024**3, data["total"] / 1024**3)
                    title = f"WSL · {data['distro']}" if special else key
                    entry["widget"].setToolTip(f"{title}\n{entry['usage'].toolTip()}")
                    self._set_project_drive_usage_progress(1.0, entry)
                    if not special and self._normal_drive_colors.get(key) is None:
                        color = _th.gauge_hex(int(entry["usage_fraction"] * 100))
                        if color != entry["bar_color"]:
                            entry["bar_color"] = color
                            self._apply_project_drive_state(key)
            if changed:
                order = sorted(self._project_drive_cards, key=lambda key: (
                    key != TRASH_KEY, bool(self._project_drive_cards[key].get("distro")), key.casefold()))
                self._project_drive_cards = {key: self._project_drive_cards[key] for key in order}
                for index, entry in enumerate(self._project_drive_cards.values()):
                    self._project_drives_layout.removeWidget(entry["widget"])
                    self._project_drives_layout.insertWidget(index, entry["widget"], 0, Qt.AlignVCenter)
                if not self._project_drives_in_sidebar:
                    count = len(self._visible_project_drive_cards())
                    self._project_drive_container.setFixedWidth(count*128 + max(0, count-1)*8 + 6)
                self._project_drives_layout.activate()
                self._project_drive_container.adjustSize()
                if self._project_drives_in_sidebar:
                    QApplication.sendPostedEvents(self._project_drive_sidebar, QEvent.LayoutRequest)
                if animate:
                    transition = DiskCardsTransition(host, source, self._visible_project_drive_cards())
                    self._disk_cards_transition = transition

                    def _finished():
                        if self._disk_cards_transition is transition:
                            self._disk_cards_transition = None
                            self._update_project_drive_layout()
                            self._filter_projects(self.search_input.text(), animate=True)
                            self._apply_pending_wsl_disk_usage()
                            self._apply_pending_windows_disk_usage()

                    transition.finished.connect(_finished)
                    transition.start()
                else:
                    self._update_project_drive_layout(animate=False)
        finally:
            page.setUpdatesEnabled(enabled)
        self._filter_projects(self.search_input.text(), animate=True)

    def _refresh_wsl_disks(self):
        if self._wsl_disk_closed or self._wsl_disk_scan_running:
            return
        now = time.monotonic()
        if self._wsl_disk_last_scan is not None and now - self._wsl_disk_last_scan < 60:
            return
        self._wsl_disk_last_scan = now
        self._wsl_disk_scan_running = True

        def _scan():
            from core.wsl_manager import list_disk_usage, save_disk_usage_cache
            try:
                usage = list_disk_usage()
                save_disk_usage_cache(usage)
            except Exception:
                usage = None
            try:
                self.wsl_disk_usage_ready.emit(usage)
            except RuntimeError:
                pass  # The window may have been destroyed during the command.

        _threading.Thread(target=_scan, name="rak-wsl-disk-usage", daemon=True).start()

    def _on_wsl_disk_usage_ready(self, usage):
        self._wsl_disk_scan_running = False
        if self._wsl_disk_closed or usage is None:
            return
        self._wsl_disk_usage = usage
        self._wsl_disk_pending = True
        self._apply_pending_wsl_disk_usage()

    def _apply_pending_wsl_disk_usage(self):
        if self._wsl_disk_closed or not self._wsl_disk_pending or not self._wsl_disk_ui_ready:
            return
        if self._disk_cards_busy():
            QTimer.singleShot(100, self, self._apply_pending_wsl_disk_usage)
            return
        self._wsl_disk_pending = False
        self._sync_project_disk_cards(self._wsl_disk_usage, special=True)

    @staticmethod
    def _project_drive_fill(special=False):
        from main_ext.class_ThemeUi import theme as _th
        base = QColor(_th.BG2) if _th.is_light else QColor(_th.BG4).lighter(120)
        if special:
            accent = QColor(_th.accent_hex)
            amount = 0.10 if _th.is_light else 0.18
            base = QColor(*(round(channel * (1 - amount) + tint * amount)
                            for channel, tint in zip(base.getRgb()[:3], accent.getRgb()[:3])))
        return base.name()

    @staticmethod
    def _project_drive_icon(special=False):
        from main_ext.class_ThemeUi import theme as _th
        if special:
            return tint_icon("project_disk", _th.accent_hex, 20).pixmap(20, 20)
        return icon_pixmap("project_disk").scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _add_project_drive_card(self, drive, total, used, *, distro=None, usage_percent=None):
        from main_ext.class_ThemeUi import theme as _th
        drives_layout = self._project_drives_layout
        total_gb = total / (1024**3)
        used_gb = used / (1024**3)
        pct = int((used / total) * 100) if usage_percent is None else usage_percent

        drive_index = sum(not entry.get("trash") for entry in self._project_drive_cards.values())
        if not distro and drive not in self._normal_drive_colors:
            self._normal_drive_colors[drive] = ("#F25D91" if drive_index == 0 else
                                                "#9368F5" if drive_index == 1 else None)
        bar_color = _th.accent_hex if distro else self._normal_drive_colors.get(drive) or _th.gauge_hex(pct)
        card_fill = self._project_drive_fill(bool(distro))

        drive_widget = _ProjectDriveCard(drive, self._project_drive_container)
        drive_widget.setObjectName("projectDriveCard")
        drive_widget.setFixedSize(self._project_drive_card_width, self._project_drive_card_height)
        drive_layout = QHBoxLayout(drive_widget)
        drive_layout.setContentsMargins(8, 4, 8, 4)
        drive_layout.setSpacing(7)

        drive_icon = QLabel()
        drive_icon.setFixedSize(20, 20)
        drive_icon.setStyleSheet("background: transparent; border: none;")
        active_drive_icon = self._project_drive_icon(bool(distro))
        muted_drive_icon = QPixmap(active_drive_icon.size())
        muted_drive_icon.setDevicePixelRatio(active_drive_icon.devicePixelRatioF())
        muted_drive_icon.fill(Qt.transparent)
        muted_painter = QPainter(muted_drive_icon)
        muted_painter.setOpacity(0.38)
        muted_painter.drawPixmap(0, 0, active_drive_icon)
        muted_painter.end()
        drive_icon.setPixmap(active_drive_icon)
        drive_layout.addWidget(drive_icon, 0, Qt.AlignVCenter)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(1)

        name = f"WSL · {distro}" if distro else drive
        drive_widget.setToolTip(f"{name}\n{used_gb:.1f} GB / {total_gb:.1f} GB")
        lbl_drv = _ElidedProjectLabel(name) if distro else QLabel(name)
        bind_style(lbl_drv, lambda: f"font-size: 10px; font-weight: 600; color: {_th.FG_BRIGHT}; background: transparent; border: none;")
        info_layout.addWidget(lbl_drv)

        lbl_usage = _ResponsiveDriveUsageLabel(used_gb, total_gb)
        bind_style(lbl_usage, lambda: f"font-size: 8.5px; color: {_th.FG3}; background: transparent; border: none;")
        lbl_usage.setMinimumWidth(0)
        lbl_usage.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info_layout.addWidget(lbl_usage)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(4)

        bar = QProgressBar()
        bar.setFixedHeight(4)
        bar.setMinimumWidth(0)
        bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bar.setTextVisible(False)
        usage_fraction = min(1.0, max(0.0, used / total if usage_percent is None else usage_percent / 100))
        bar.setRange(0, 10000)
        bar.setValue(round(usage_fraction * bar.maximum()))

        bind_style(bar, lambda bar_color=bar_color: f"""
            QProgressBar {{
                border: none;
                background-color: {_th.border_color(0.1)};
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {bar_color};
                border-radius: 2px;
            }}
        """)
        progress_row.addWidget(bar)

        lbl_pct = QLabel(f"{pct}%")
        lbl_pct.setFixedWidth(24)
        lbl_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bind_style(lbl_pct, lambda: f"font-size: 8px; color: {_th.FG3}; background: transparent; border: none;")
        progress_row.addWidget(lbl_pct)
        info_layout.addLayout(progress_row)

        drive_layout.addLayout(info_layout, 1)
        drives_layout.addWidget(drive_widget, 0, Qt.AlignVCenter)
        for child in (drive_icon, lbl_drv, lbl_usage, bar, lbl_pct):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._project_drive_cards[drive] = {
            "widget": drive_widget,
            "distro": distro,
            "icon": drive_icon,
            "icon_active": active_drive_icon,
            "icon_muted": muted_drive_icon,
            "name": lbl_drv,
            "usage": lbl_usage,
            "percent": lbl_pct,
            "bar": bar,
            "usage_fraction": usage_fraction,
            "usage_percent": usage_percent,
            "fill": card_fill,
            "bar_color": bar_color,
        }
        self._apply_project_drive_state(drive)
        drive_widget.clicked.connect(self._toggle_project_drive)
        policy = QSizePolicy(drive_widget.sizePolicy())
        policy.setRetainSizeWhenHidden(self._trash_view)
        drive_widget.setSizePolicy(policy)
        drive_widget.setVisible(not self._trash_view)
        return self._project_drive_cards[drive]

    def _set_project_drive_usage_progress(self, progress, entry=None):
        entries = (entry,) if entry is not None else getattr(self, "_project_drive_cards", {}).values()
        for entry in entries:
            if entry.get("trash"):
                continue
            entry["usage"].set_usage_progress(progress)
            fraction = entry["usage_fraction"] * progress
            entry["bar"].setValue(round(fraction * entry["bar"].maximum()))
            percent = (fraction * 100 if entry.get("usage_percent") is None
                       else entry["usage_percent"] * progress)
            text = f"{int(percent)}%"
            if entry["percent"].text() != text:
                entry["percent"].setText(text)

    def _stop_project_drive_usage_reveal(self):
        animation = self._project_drive_usage_animation
        self._project_drive_usage_animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
            self._set_project_drive_usage_progress(1.0)

    def _start_project_drive_usage_reveal(self, delay_ms=100, force=False):
        from main_ext.class_ThemeUi import theme
        self._stop_project_drive_usage_reveal()
        if not self._project_drive_cards or (not force and not theme.animations_enabled):
            return
        animation = QSequentialAnimationGroup(self)
        animation.addAnimation(QPauseAnimation(delay_ms, animation))
        entries = tuple(entry for entry in self._project_drive_cards.values() if not entry.get("trash"))
        if not entries:
            animation.deleteLater()
            return
        previous_progress = [0.0] * len(entries)

        def _advance(elapsed):
            for index, entry in enumerate(entries):
                progress = DriveUsageTiming.progress(elapsed, index)
                if progress != previous_progress[index]:
                    previous_progress[index] = progress
                    self._set_project_drive_usage_progress(progress, entry)

        fill = QVariantAnimation(animation)
        total_duration = DriveUsageTiming.total(len(entries))
        fill.setDuration(round(total_duration))
        fill.setStartValue(0.0)
        fill.setEndValue(float(total_duration))
        fill.setEasingCurve(QEasingCurve.Linear)
        fill.valueChanged.connect(_advance)
        animation.addAnimation(fill)
        self._project_drive_usage_animation = animation

        def _finished():
            if self._project_drive_usage_animation is animation:
                self._project_drive_usage_animation = None
                self._set_project_drive_usage_progress(1.0)
            animation.deleteLater()

        animation.finished.connect(_finished)
        self._set_project_drive_usage_progress(0.0)
        animation.start()

    def _apply_project_drive_state(self, drive):
        from main_ext.class_ThemeUi import theme as _th
        entry = self._project_drive_cards.get(drive)
        if entry is None:
            return
        if entry.get("trash"):
            self._style_project_trash_card(entry)
            return
        active = not self._trash_view and _th.project_drive_enabled(drive)
        special = bool(entry.get("distro"))
        bind_style(entry["widget"], lambda active=active, entry=entry: f"""
            QWidget#projectDriveCard {{
                background-color: {entry['fill'] if active else _th.BG};
                border: 1px solid {_th.accent(0.38 if active else 0.14) if special else _th.border_color(0.11 if active else 0.05)};
                border-radius: 10px;
            }}
        """)
        entry["icon"].setPixmap(entry["icon_active"] if active else entry["icon_muted"])
        bind_style(entry["name"], lambda active=active: f"font-size: 10px; font-weight: 600; color: {(_th.accent_hex if special else _th.FG_BRIGHT) if active else _th.FG_MUTED}; "
            "background: transparent; border: none;")
        secondary = lambda: _th.FG3 if active else _th.FG_DISABLED
        bind_style(entry["usage"], lambda: f"font-size: 8.5px; color: {secondary()}; background: transparent; border: none;")
        bind_style(entry["percent"], lambda: f"font-size: 8px; color: {secondary()}; background: transparent; border: none;")
        bind_style(entry["bar"], lambda active=active, entry=entry: f"""
            QProgressBar {{
                border: none;
                background-color: {_th.border_color(0.1 if active else 0.06)};
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {entry['bar_color'] if active else _th.border_color(0.18)};
                border-radius: 2px;
            }}
        """)

    def _read_trash_entries(self):
        try:
            entries = self._trash.entries()
            self._trash_read_error = ""
            return entries
        except Exception as exc:
            self._trash_read_error = str(exc)
            logging.error("Cannot read project Trash: %s", exc)
            return []

    def _visible_project_drive_cards(self):
        return {key: entry for key, entry in self._project_drive_cards.items()
                if not self._trash_view or entry.get("trash")}

    def _add_project_trash_card(self):
        card = _ProjectDriveCard(TRASH_KEY, self._project_drive_container)
        card.setObjectName("projectDriveCard")
        card.setFixedSize(self._project_drive_card_width, self._project_drive_card_height)
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(7)
        image = QLabel()
        image.setFixedSize(20, 20)
        image.setStyleSheet("background: transparent; border: none;")
        row.addWidget(image)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        name = bind_text(QLabel(), lambda: t("hdr_trash"))
        count = bind_text(QLabel(), lambda: t("trash_count", n=len(self._trash_entries)))
        text.addWidget(name)
        text.addWidget(count)
        row.addLayout(text, 1)
        for child in (image, name, count):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        entry = {"widget": card, "trash": True, "icon": image, "name": name, "count": count}
        self._project_drive_cards[TRASH_KEY] = entry
        self._project_drives_layout.addWidget(card, 0, Qt.AlignVCenter)
        self._style_project_trash_card(entry)
        card.clicked.connect(self._toggle_project_drive)
        card.show()

    def _style_project_trash_card(self, entry):
        from main_ext.class_ThemeUi import theme as th
        active = self._trash_view
        bind_style(entry["widget"], lambda: f"""
            QWidget#projectDriveCard {{
                background-color: {self._project_drive_fill(True) if active else th.BG2};
                border: 1px solid {th.accent(0.38) if active else th.border_color(0.11)};
                border-radius: 10px;
            }}
        """)
        bind_pixmap(entry["icon"], lambda: tint_icon("project_trash", th.accent_hex if active else th.FG2, 20).pixmap(20, 20))
        bind_style(entry["name"], lambda: f"font-size: 10px; font-weight: 600; color: {th.accent_hex if active else th.FG_BRIGHT}; background: transparent; border: none;")
        bind_style(entry["count"], lambda: f"font-size: 8.5px; color: {th.FG3}; background: transparent; border: none;")
        bind_language(entry["widget"], "setToolTip", lambda: t("trash_retention"))

    def _trash_hint_text(self):
        if self._trash_read_error:
            return t("trash_read_error", error=self._trash_read_error)
        return t("trash_retention" if self._trash_entries else "trash_empty")

    def _refresh_trash_state(self, *, render=True):
        self._trash_entries = self._read_trash_entries()
        entry = self._project_drive_cards.get(TRASH_KEY)
        if entry is not None:
            refresh_language(entry["widget"])
        self._update_trash_hint()
        if render:
            self._sync_project_rows()
            self._filter_projects(self.search_input.text(), animate=True)

    def _update_trash_hint(self):
        if not hasattr(self, "_trash_hint"):
            return
        self._trash_hint.setText(self._trash_hint_text())
        self._trash_hint.setGeometry(self.list_widget.viewport().rect())
        visible = any(not self.list_widget.item(index).isHidden()
                      for index in range(self.list_widget.count()))
        self._trash_hint.setVisible(self._trash_view and not visible
                                    and self._project_cards_transition is None)

    def _set_trash_view(self, active):
        if self._project_delete_active or bool(active) == self._trash_view:
            return
        self._stop_project_cards_transition()
        self._stop_disk_cards_transition(reflow=False)
        self._stop_project_layout_transition()
        from main_ext.class_ThemeUi import theme
        animate = (theme.animations_enabled and self.page_projects.isVisible()
                   and self._project_layout_animation_ready and self._startup_splash is None
                   and self._startup_content_reveal is None and self._page_content_reveal is None)
        host = self._project_drive_sidebar.viewport() if self._project_drives_in_sidebar else self._project_header
        drives = {key: entry for key, entry in self._project_drive_cards.items() if not entry.get("trash")}
        source = DiskCardsTransition.capture(host, drives if not self._trash_view else {}) if animate else None
        if active:
            # Hiding all drive cards must not reflow Trash from the sidebar
            # into the header, or clamp a scrolled sidebar to its top.
            self._trash_drive_layout = (self._project_drives_in_sidebar, self._project_drive_container.height())
        else:
            self._trash_drive_layout = None
        self._trash_view = bool(active)
        for drive in self._project_drive_cards:
            self._apply_project_drive_state(drive)
        for entry in drives.values():
            widget = entry["widget"]
            policy = QSizePolicy(widget.sizePolicy())
            policy.setRetainSizeWhenHidden(self._trash_view)
            widget.setSizePolicy(policy)
            widget.setVisible(not self._trash_view)
        self._update_project_drive_layout(animate=False)
        self._refresh_trash_state(render=False)
        # Retain the project list, its widgets and search across disk filters.
        self._sync_project_rows()
        self._filter_projects(self.search_input.text(), animate=True)
        if animate and drives:
            # Animate only the drives. Trash remains a native, clickable card
            # at the same position while the unplug/replug animation runs.
            host = self._project_drive_sidebar.viewport() if self._project_drives_in_sidebar else self._project_header
            transition = DiskCardsTransition(host, source, {} if self._trash_view else drives)
            self._disk_cards_transition = transition

            def finished():
                if self._disk_cards_transition is transition:
                    self._disk_cards_transition = None
                    self._update_project_drive_layout()
                    self._filter_projects(self.search_input.text(), animate=True)
                    self._apply_pending_wsl_disk_usage()
                    self._apply_pending_windows_disk_usage()

            transition.finished.connect(finished)
            transition.start()
        if self._trash_view:
            self._maintain_project_trash()

    @staticmethod
    def _trash_remaining_text(entry):
        import math
        if not entry.get("handle"):
            return t("trash_recovery_pending")
        remaining = max(0, entry["expires_at"] - time.time())
        if remaining <= 0:
            return t("trash_expired")
        if remaining < 86400:
            return t("trash_hours_left", n=math.ceil(remaining / 3600))
        return t("trash_days_left", n=math.ceil(remaining / 86400))

    def _run_trash_worker(self, operation, done):
        worker = ProjectTrashWorker(operation)
        self._trash_workers.add(worker)
        worker.done.connect(done)

        def release():
            self._trash_workers.discard(worker)
            worker.deleteLater()

        worker.finished.connect(release)
        worker.start()

    def _restore_trash_project(self, identifier):
        self._change_trash_project(identifier)

    def _delete_trash_project(self, identifier):
        if self._project_delete_active:
            return
        entry = next((entry for entry in self._trash_entries if entry["id"] == identifier), None)
        if entry is None:
            return
        if not AppDialog.confirm_close(self, t("dlg_delete_title"),
                t("dlg_delete_msg", name=entry["project"].get("project_name", "")),
                close_label=t("dlg_btn_delete"), cancel_label=t("dlg_btn_cancel")):
            return
        self._change_trash_project(identifier, permanent=True)

    def _change_trash_project(self, identifier, *, permanent=False):
        if self._project_delete_active:
            return
        self._stop_project_cards_transition()
        self._project_delete_active = True
        self.search_input.setEnabled(False)
        self.list_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._project_hover_effect.reset()
        self._project_hover_effect.setEnabled(False)
        item = next((self.list_widget.item(index) for index in range(self.list_widget.count())
                     if self.list_widget.item(index).data(Qt.UserRole).get("_trash_id") == identifier), None)

        def done(ok, project, error):
            if self._wsl_disk_closed:
                return
            if not ok:
                self._project_delete_active = False
                self.search_input.setEnabled(True)
                self.list_widget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                self._project_hover_effect.setEnabled(True)
                AppDialog.error(self, t("dlg_delete_title" if permanent else "btn_restore_project"), error)
                return
            self._all_projects = _load_projects_json()
            self._all_projects.sort(key=lambda project: 0 if project.get("pinned") else 1)
            self._refresh_trash_state(render=False)
            from main_ext.class_ThemeUi import theme as th
            self._animate_project_removal(item, project["path"], animate=th.animations_enabled,
                                          on_finished=self._sync_project_rows)

        operation = self._trash.delete if permanent else self._trash.restore
        self._run_trash_worker(lambda: operation(identifier), done)

    def _maintain_project_trash(self):
        if self._wsl_disk_closed or self._trash_maintenance_running or self._project_delete_active:
            return
        self._trash_maintenance_running = True

        def done(ok, errors, error):
            self._trash_maintenance_running = False
            if self._wsl_disk_closed:
                return
            if not ok:
                logging.error("Project Trash maintenance: %s", error)
            elif errors:
                logging.error("Project Trash maintenance: %s", errors)
            # An action or card transition can begin while cleanup is running.
            # Refresh once it releases the list, retaining its animation.
            def refresh():
                if self._wsl_disk_closed:
                    return
                if self._disk_cards_busy():
                    QTimer.singleShot(100, self, refresh)
                    return
                data = _load_projects_json()
                data.sort(key=lambda project: 0 if project.get("pinned") else 1)
                changed = data != self._all_projects
                self._all_projects = data
                previous = self._trash_entries
                self._refresh_trash_state(render=False)
                if changed or previous != self._trash_entries:
                    self._sync_project_rows()
                    self._filter_projects(self.search_input.text(), animate=True)
                elif self._trash_view:
                    refresh_language(self.list_widget)
            refresh()

        self._run_trash_worker(self._trash.maintain, done)

    def _toggle_project_drive(self, drive):
        if self._project_delete_active:
            return
        if drive == TRASH_KEY:
            self._set_trash_view(not self._trash_view)
            return
        from main_ext.class_ThemeUi import theme as _th
        if self._trash_view:
            # Physical drive filters do not apply to recycled projects.
            return
        _th.set_project_drive_enabled(drive, not _th.project_drive_enabled(drive))
        self._apply_project_drive_state(drive)
        self._filter_projects(self.search_input.text(), animate=True)

    @staticmethod
    def _project_on_enabled_drive(proj):
        from main_ext.class_ThemeUi import theme as _th
        drive = project_storage_key(proj.get("path", ""))
        return _th.project_drive_enabled(drive)

    def load_projects(self, rescan_path: str = None):
        from app_ext.nonclass_VerifyDir import verify_and_clean_projects
        data = verify_and_clean_projects(PROJECT_JSON)
        data.sort(key=lambda p: (0 if p.get("pinned") else 1))
        self._all_projects = data
        # Rebuild only when the project data changes, then reuse these rows
        # for both search and drive filtering.
        if hasattr(self, "search_input"):
            with QSignalBlocker(self.search_input):
                self.search_input.clear()
        self._refresh_trash_state(render=False)
        self._render_projects(data)

        # Request stop, tapi tahan referensinya sampai signal finished datang.
        # Menimpa satu-satunya referensi QThread saat masih running membuat Qt abort.
        if self._size_scanner is not None:
            try:
                self._size_scanner.size_calculated.disconnect(self._on_project_size_calculated)
            except Exception:
                pass
            self._size_scanner.requestInterruption()
            self._size_scanner = None

        # Only scan projects that DO NOT have cached_size yet, or the specific project that was just modified
        norm_rescan = os.path.normcase(os.path.abspath(rescan_path)) if rescan_path else None
        paths_to_scan = [
            p.get("path") for p in data
            if p.get("path") and (
                not p.get("cached_size")
                or (norm_rescan and os.path.normcase(os.path.abspath(p.get("path", ""))) == norm_rescan)
            ) and self._storage_available(project_storage_key(p.get("path", "")))
        ]
        if paths_to_scan:
            scanner = ProjectSizeScannerWorker(paths_to_scan)
            self._size_scanner = scanner
            self._size_scanners.add(scanner)
            scanner.size_calculated.connect(self._on_project_size_calculated)
            scanner.finished.connect(self._on_size_scanner_finished)
            scanner.start()

    def _on_size_scanner_finished(self):
        scanner = self.sender()
        if scanner is None:
            return
        if scanner is self._size_scanner:
            self._size_scanner = None
        self._size_scanners.discard(scanner)
        scanner.deleteLater()

    def _stop_project_size_scanners(self):
        for scanner in tuple(self._size_scanners):
            scanner.requestInterruption()
        for scanner in tuple(self._size_scanners):
            if scanner.isRunning():
                scanner.wait()

    def _on_project_size_calculated(self, path, size_str):
        if self._project_delete_active:
            return
        sender = self.sender()
        if sender is not None and sender is not self._size_scanner:
            return
        if not hasattr(self, "_project_size_labels"):
            return

        # Update UI label
        lbl = self._project_size_labels.get(path)
        if lbl:
            lbl.setText(f"{t('lbl_size')}{size_str}")

        # Update in-memory array and save to JSON if changed
        changed = False
        for p in self._all_projects:
            if p.get("path") == path:
                if p.get("cached_size") != size_str:
                    p["cached_size"] = size_str
                    changed = True
                break

        if changed:
            _save_projects_json(self._all_projects)

    def _stop_project_cards_transition(self):
        transition = self._project_cards_transition
        if transition is not None:
            self._project_cards_transition = None
            transition.stop()

    def _filter_projects(self, query: str, *, animate=False):
        from main_ext.class_ThemeUi import theme as _th
        if self._project_delete_active:
            return
        if self._project_cards_transition is not None:
            if animate:
                # Finish the current motion, then use the latest drive state.
                return
            self._stop_project_cards_transition()
        q = query.strip().lower()
        changes = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            matches = self._project_matches_filter(item, q)
            hidden = not matches
            if item.isHidden() != hidden:
                changes.append((item, hidden))
        if not changes:
            self._update_trash_hint()
            return

        if animate and (self._disk_cards_transition is not None
                        or self._project_layout_transition is not None):
            # Their finished callbacks apply the latest filter after reflow.
            return

        project_list = self.list_widget
        self._project_hover_effect.reset()
        project_list._scroll_timer.stop()
        project_list._anim.stop()
        animate = (animate and _th.animations_enabled and self.page_projects.isVisible()
                   and self._project_layout_animation_ready and self._startup_splash is None
                   and self._startup_content_reveal is None and self._page_content_reveal is None)
        if animate:
            transition = ProjectCardsTransition(
                project_list, changes, _PROJECT_REMOVING_ROLE, _snapshot_project_row,
            )
            self._project_cards_transition = transition

            def _finished():
                if self._project_cards_transition is transition:
                    self._project_cards_transition = None
                    self._filter_projects(self.search_input.text(), animate=True)
                    self._apply_pending_wsl_disk_usage()
                    self._apply_pending_windows_disk_usage()

            transition.finished.connect(_finished)
            transition.start()
            self._update_trash_hint()
            return
        updates_disabled = project_list.testAttribute(Qt.WA_ForceUpdatesDisabled)
        project_list.setUpdatesEnabled(False)
        try:
            for item, hidden in changes:
                item.setHidden(hidden)
            project_list.doItemsLayout()
            project_list._target_scroll = project_list.verticalScrollBar().value()
        finally:
            project_list.setUpdatesEnabled(not updates_disabled)
        self._update_trash_hint()

    def _project_matches_filter(self, item, query):
        from main_ext.class_ThemeUi import theme
        trashed = bool(item.data(Qt.UserRole).get("_trash_id"))
        return (trashed == self._trash_view and (trashed or (
                    theme.project_drive_enabled(item.data(_PROJECT_DRIVE_ROLE))
                    and self._storage_available(item.data(_PROJECT_DRIVE_ROLE))))
                and (not query or any(query in text for text in item.data(_PROJECT_SEARCH_ROLE))))

    def _project_rows_data(self, data=None):
        return list(self._all_projects if data is None else data) + [
            dict(entry["project"], _trash_id=entry["id"]) for entry in self._trash_entries
        ]

    @staticmethod
    def _project_row_key(project):
        if project.get("_trash_id"):
            return ("trash", project["_trash_id"])
        return ("project", os.path.normcase(os.path.abspath(project.get("path", ""))))

    def _add_project_row(self, project, order):
        item = _ProjectListItem()
        item._project_order = order
        item.setSizeHint(QSize(100, 74))
        item.setData(Qt.UserRole, project)
        fields = ("project_name", "path", "port1", "port2") if project.get("_trash_id") else ("project_name", "port1", "port2")
        item.setData(_PROJECT_SEARCH_ROLE, tuple((project.get(field, "") or "").lower() for field in fields))
        item.setData(_PROJECT_DRIVE_ROLE, project_storage_key(project.get("path", "")))
        widget = self.build_project_item_widget(project, item)
        self.list_widget.addItem(item)
        item.setHidden(True)
        self.list_widget.setItemWidget(item, widget)
        return item

    def _sync_project_rows(self):

        self._stop_project_cards_transition()
        projects = self._project_rows_data()
        existing = {self._project_row_key(self.list_widget.item(index).data(Qt.UserRole)): self.list_widget.item(index)
                    for index in range(self.list_widget.count())}
        for order, project in enumerate(projects):
            item = existing.pop(self._project_row_key(project), None)
            if item is None:
                self._add_project_row(project, order)
            else:
                item._project_order = order
                item.setData(Qt.UserRole, project)
        # Only remove rows whose underlying project was actually removed or
        # restored; selecting a disk never removes its matching list items.
        if existing:
            self._project_hover_effect.reset()
        for item in existing.values():
            project = item.data(Qt.UserRole)
            if not project.get("_trash_id"):
                self._project_size_labels.pop(project.get("path"), None)
            self.list_widget.takeItem(self.list_widget.row(item))
        order = [self.list_widget.item(index)._project_order for index in range(self.list_widget.count())]
        if order != sorted(order):
            self.list_widget.sortItems(Qt.AscendingOrder)

    def _render_projects(self, data):
        self._stop_project_cards_transition()
        self._project_hover_effect.reset()
        self._project_size_labels = {}
        self.list_widget._scroll_timer.stop()
        self.list_widget._anim.stop()
        self.list_widget.clear()
        self.list_widget._target_scroll = 0
        self._startup_rows_timer.stop()
        self._startup_rows = None
        if self._startup_splash is not None and not self._startup_reveal_started:
            from collections import deque
            self._startup_rows = deque(enumerate(self._project_rows_data(data)))
            self._startup_rows_timer.start(1)
            return
        for index, project in enumerate(self._project_rows_data(data)):
            self._add_project_row(project, index)
            if index % 3 == 2:
                self._refresh_startup_frame()
        self._filter_projects(self.search_input.text())
        self._update_trash_hint()


    def on_return_from_editor(self):
        closed_project = getattr(self.editor, "project", None) if self.editor else None
        # editor udah close dari dalem Main
        self.editor = None

        # pastikan loader mati (safety)
        if getattr(self, "loader", None):
            self.loader.close()
            self.loader = None
        # 🔥 INI WAJIB
        self.load_projects(rescan_path=closed_project)
        self.apply_window_state()
        # tampilkan AppUI lagi
        self.show()

    def _show_editor_with_reveal(self, project_root: str, opened_at: float, origin: str):

        editor = self.editor
        loader = self.loader
        editor._begin_entry_transition()

        def finish_editor_reveal():
            editor._entry_content_reveal = None
            editor._complete_entry_transition()

        reveal = WindowContentReveal(editor)
        editor._entry_content_reveal = reveal
        reveal.finished.connect(finish_editor_reveal)

        def show_editor():
            if self.editor is not editor or getattr(editor, "_closing", False):
                reveal.stop()
                finish_editor_reveal()
                return
            reveal.start()
            self._log_project_entered(project_root, opened_at, origin)

        if loader is None:
            reveal.prepared.connect(show_editor)
            reveal.prepare()
            return

        # Keep dismissal routed through the loading cancellation callback until
        # its exit finishes; closing it during the fade must not reopen Main.
        loader._block_close = True
        fade = QPropertyAnimation(loader, b"windowOpacity", loader)
        fade.setDuration(160)
        fade.setStartValue(loader.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        def finish_loader_fade():
            dismissed = not loader.isVisible()
            loader._block_close = False
            loader.close()
            loader._handoff_fade = None
            fade.deleteLater()
            loader.deleteLater()
            if self.loader is loader:
                self.loader = None
            if dismissed:
                reveal.stop()
                finish_editor_reveal()
                log_project_event("OPEN_CANCELLED", project_root, origin=origin)
                return
            show_editor()

        fade.finished.connect(finish_loader_fade)
        loader._handoff_fade = fade
        reveal.prepared.connect(fade.start)
        reveal.prepare()

    def _log_project_entered(self, project_root: str, opened_at: float, origin: str):
        editor = self.editor
        editor._project_session_started_at = time.perf_counter()
        log_project_event(
            "ENTER", project_root,
            origin=origin,
            open_ms=round((editor._project_session_started_at - opened_at) * 1000),
            load_status="failed" if getattr(editor, "_project_load_failed", False) else "ok",
        )

    def _bind_project_preparation(self, editor, project_root: str, origin: str):
        if editor is None:
            return

        def on_status(status):
            if getattr(self, "editor", None) is not editor:
                return
            loader = getattr(self, "loader", None)
            if loader is not None and hasattr(loader, "set_status"):
                loader.set_status(status)

        def on_failure(err):
            if getattr(self, "editor", None) is not editor:
                return
            exc = err if isinstance(err, BaseException) else Exception(str(err))
            self._handle_project_open_failure(project_root, origin, exc)

        prep_status = getattr(editor, "preparation_status", None)
        if prep_status is not None and hasattr(prep_status, "connect"):
            prep_status.connect(on_status)

        prep_failed = getattr(editor, "preparation_failed", None)
        if prep_failed is not None and hasattr(prep_failed, "connect"):
            prep_failed.connect(on_failure)

    def _cancel_project_preparation(self, editor=None):
        target = editor if editor is not None else getattr(self, "editor", None)
        if target is not None and getattr(self, "editor", None) is not target:
            return

        if target is not None:
            abort_fn = getattr(target, "abort_project_preparation", None)
            if callable(abort_fn):
                try:
                    abort_fn()
                except Exception:
                    pass
            target._returning = True
            try:
                target.close()
            except Exception:
                pass
            try:
                target.deleteLater()
            except Exception:
                pass
            if getattr(self, "editor", None) is target:
                self.editor = None
        else:
            self.editor = None

        loader = getattr(self, "loader", None)
        if loader is not None:
            loader._on_hide_requested = None
            anim = getattr(loader, "_handoff_fade", None)
            if anim is not None and hasattr(anim, "stop"):
                try:
                    anim.stop()
                except Exception:
                    pass
            loader._handoff_fade = None
            loader._block_close = False
            try:
                loader.close()
            except Exception:
                pass
            try:
                loader.deleteLater()
            except Exception:
                pass
            self.loader = None

    def _handle_project_open_failure(self, project_root: str, origin: str, error: Exception):
        traceback.print_exc()
        log_project_event("OPEN_FAILED", project_root, origin=origin, error=str(error))
        self._cancel_project_preparation(getattr(self, "editor", None))
        self.show()
        try:
            AppDialog.error(self, t("lbl_error"), str(error))
        except Exception:
            pass

    def update_create_state(self):
        name = self.input_name.text().strip()
        p1 = self.input_port1.text().strip()
        p2 = self.input_port2.text().strip()

        valid = False

        if getattr(self, "_port_mode", 1) == 1:
            valid = bool(name and p1)
        else:
            valid = bool(name and p1 and p2)

        self.btn_create2.setEnabled(valid)


    def create_project(self):
        name = self.input_name.text().strip()
        p1 = self.input_port1.text().strip()
        p2 = self.input_port2.text().strip()

        if not name:
            AppDialog.warning(self, t("lbl_error"), t("err_project_name_required"))
            return

        if self.radio_1.isChecked():
            if not p1:
                AppDialog.warning(self, t("lbl_error"), t("err_port_required"))
                return
        else:
            if not p1 or not p2:
                AppDialog.warning(self, t("lbl_error"), t("err_ports_required"))
                return

        base_dir = _resolve_create_base_dir(self.path_label.text())

        # buat folder
        proj_root = os.path.join(base_dir, name)

        try:
            os.makedirs(proj_root, exist_ok=True)
        except:
            AppDialog.error(self, t("lbl_error"), t("err_create_folder"))
            return

        # Struktur
        os.makedirs(os.path.join(proj_root, p1, "config"), exist_ok=True)

        filetree_mode = 1
        if self.radio_2.isChecked():
            filetree_mode = 2
            if p2:
                os.makedirs(os.path.join(proj_root, p2, "config"), exist_ok=True)

        # Simpan ke JSON
        record = {
            "project_name": name,
            "path": proj_root,
            "port1": p1,
            "port2": p2 if filetree_mode == 2 else "",
            "mode": filetree_mode
        }

        all_proj = _load_projects_json()
        all_proj.append(record)
        _save_projects_json(all_proj)
        opened_at = time.perf_counter()
        log_project_event("OPEN_START", proj_root, origin="create")

        # Launch main.py
        # FLOW BARU: CREATE PROJECT → MAIN

        # sembunyikan AppUI
        self.load_projects()
        self.apply_window_state()

        self.hide()

        # tampilkan loading instant
        self.loader = LoadingWindow(proj_root)
        self.loader._block_close = True
        self.loader.show()
        QApplication.processEvents()

        _hide_requested = [False]

        def _on_hide_requested():
            _hide_requested[0] = True
            log_project_event("OPEN_CANCELLED", proj_root, origin="create")
            self._cancel_project_preparation(getattr(self, "editor", None))
            self.show()

        self.loader._on_hide_requested = _on_hide_requested

        # bikin Main di PROCESS YANG SAMA
        try:
            from main import Main
            self.editor = Main(
                proj_root,
                p1,
                p2 if filetree_mode == 2 else None,
                profile_provider=self.profile_provider
            )
        except Exception as e:
            self._handle_project_open_failure(proj_root, "create", e)
            return
        self.editor.return_to_project_signal.connect(self.on_return_from_editor)
        editor = self.editor
        self._bind_project_preparation(editor, proj_root, "create")

        # pas Main siap → matiin loading
        def on_main_ready():
            if getattr(self, "editor", None) is not editor:
                return
            if self.loader is not None:
                self.loader._block_close = False
            if _hide_requested[0]:
                self._cancel_project_preparation(editor)
                log_project_event("OPEN_CANCELLED", proj_root, origin="create")
                return
            self._show_editor_with_reveal(proj_root, opened_at, "create")

        self.editor.ready.connect(on_main_ready)


    def open_project(self, item):
        if self._project_delete_active or self._trash_view:
            return
        if getattr(self, "loader", None) is not None:
            return
        proj = item.data(Qt.UserRole)

        project_root = proj["path"]
        p1 = proj["port1"]
        p2 = proj.get("port2")
        opened_at = time.perf_counter()
        log_project_event("OPEN_START", project_root, origin="existing")

        # 1️⃣ SEMBUNYIKAN APP UI
        self.hide()

        # 2️⃣ TAMPILKAN LOADING INSTANT
        self.loader = LoadingWindow(project_root)
        self.loader._block_close = True
        self.loader.show()
        QApplication.processEvents()

        _hide_requested = [False]

        def _on_hide_requested():
            _hide_requested[0] = True
            log_project_event("OPEN_CANCELLED", project_root, origin="existing")
            self._cancel_project_preparation(getattr(self, "editor", None))
            self.show()

        self.loader._on_hide_requested = _on_hide_requested

        # 3️⃣ BIKIN MAIN (SATU PROCESS)
        try:
            from main import Main
            self.editor = Main(project_root, p1, p2, profile_provider=self.profile_provider)
        except Exception as e:
            self._handle_project_open_failure(project_root, "existing", e)
            return
        self.editor.return_to_project_signal.connect(self.on_return_from_editor)
        editor = self.editor
        self._bind_project_preparation(editor, project_root, "existing")

        # 4️⃣ HUBUNGKAN SIGNAL READY → MATIKAN LOADING
        def on_main_ready():
            if getattr(self, "editor", None) is not editor:
                return
            if self.loader is not None:
                self.loader._block_close = False
            if _hide_requested[0]:
                self._cancel_project_preparation(editor)
                log_project_event("OPEN_CANCELLED", project_root, origin="existing")
                return
            self._show_editor_with_reveal(project_root, opened_at, "existing")

        self.editor.ready.connect(on_main_ready)

# RUN
if __name__ == "__main__":
    app = _EARLY_APP


    app.setQuitOnLastWindowClosed(False)

    w = AppUI(startup_splash=True)
    screen = app.primaryScreen()
    if screen is not None:
        w.move(screen.availableGeometry().center() - w.rect().center())
    w.show()
    app.processEvents(QEventLoop.ExcludeUserInputEvents)


    from core.fonts import apply_app_font
    apply_app_font(app)


    def _prewarm_config_service():
        try:
            from services.process_manager import get_manager
            get_manager().start_process()   # idempotent, non-blocking
            print("[app] ConfigEngine service pre-warm started")
        except Exception as e:
            print(f"[app] WARN: pre-warm service gagal (akan spawn lazy nanti): {e}")


    def _prewarm_editor_module(on_done):
        import_ok = [False]

        def _import_editor():
            try:
                import main  # noqa: F401
                from icons.handler import warm_icon_catalog
                warm_icon_catalog()
                import_ok[0] = True
                print("[app] editor module pre-warm ready")
            except Exception as e:
                print(f"[app] WARN: editor module pre-warm gagal: {e}")

        worker = _threading.Thread(
            target=_import_editor,
            name="rak-editor-import",
            daemon=True,
        )
        worker.start()


        warm_timer = QTimer(w)
        warm_timer.setInterval(50)

        def _maybe_warm_webengine():
            if worker.is_alive():
                return
            warm_timer.stop()
            warm_timer.deleteLater()
            if not import_ok[0] or not w.isVisible() or getattr(w, "editor", None) is not None:
                on_done()
                return
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView

                view = QWebEngineView()
                view.setHtml("<html><body></body></html>")
                view.close()
                view.deleteLater()
                print("[app] WebEngine pre-warm ready")
            except Exception as e:
                print(f"[app] WARN: WebEngine pre-warm gagal: {e}")
            on_done()

        warm_timer.timeout.connect(_maybe_warm_webengine)
        warm_timer.start()

    prewarm_ready = [False]
    login_ready = [True]

    def _maybe_reveal_startup():
        # Verified status is fetched in the background by _build_ui(). A slow
        # or stalled network request must not keep the launcher behind splash.
        if prewarm_ready[0] and login_ready[0]:
            w.reveal_startup()

    def _on_prewarms_done():
        prewarm_ready[0] = True
        _maybe_reveal_startup()

    def _on_startup_built():
        QTimer.singleShot(0, _prewarm_config_service)
        QTimer.singleShot(0, lambda: _prewarm_editor_module(_on_prewarms_done))
        def _on_login_ready():
            login_ready[0] = True
            _maybe_reveal_startup()
        login_ready[0] = not w._prepare_account_startup(_on_login_ready)
        _maybe_reveal_startup()

    def _on_startup_revealed():
        # Startup imports are complete; resume GC and restore the original hook.
        _end_startup_import_window()
        w._restore_account_startup()

    w.startup_built.connect(_on_startup_built)
    w.startup_revealed.connect(_on_startup_revealed)

    sys.exit(app.exec())
