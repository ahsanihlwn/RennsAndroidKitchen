# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSizePolicy,
    QStackedLayout
)
from PySide6.QtCore import Qt, QSize, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th

print("[MAIN STAGE] VIDEO_VIEWER")


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024.0 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{f:.1f} GB"


def _fmt_time(ms: int) -> str:
    if ms is None or ms < 0:
        ms = 0
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class _VideoSurface(QVideoWidget):


    def __init__(self, on_click=None, parent=None):
        super().__init__(parent)
        self._on_click = on_click

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._on_click:
            self._on_click()
        super().mousePressEvent(e)


class VideoViewer(QWidget):
    def set_read_only(self, ro=True):

        try:
            if hasattr(self, "_btn_convert"):
                self._btn_convert.setVisible(not ro)
        except Exception:
            pass

    def __init__(self, filepath, loading_handler=None):
        super().__init__()
        self.unsaved = False
        self.filepath = filepath
        self._duration = 0
        self._seeking = False
        self._last_volume = 80

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── media player ──
        self._audio_out = QAudioOutput(self)
        self._audio_out.setVolume(self._last_volume / 100.0)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)

        # ── video surface + error overlay (stacked) ──
        self._stage = QWidget()
        self._stage.setStyleSheet("background:#000; border:none;")
        self._stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        stage_lay = QStackedLayout(self._stage)
        stage_lay.setStackingMode(QStackedLayout.StackAll)
        stage_lay.setContentsMargins(0, 0, 0, 0)

        self._video = _VideoSurface(on_click=self._toggle_play)
        self._video.setStyleSheet("background:#000;")
        self._player.setVideoOutput(self._video)
        stage_lay.addWidget(self._video)


        self._freeze_lbl = QLabel()
        self._freeze_lbl.setAlignment(Qt.AlignCenter)
        self._freeze_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._freeze_lbl.setStyleSheet("background:#000; border:none;")
        self._freeze_lbl.setVisible(False)
        stage_lay.addWidget(self._freeze_lbl)
        self._was_playing_before_block = False

        self._err_lbl = QLabel("")
        self._err_lbl.setAlignment(Qt.AlignCenter)
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._err_lbl.setStyleSheet(
            "color:#cfd4dc; font-size:13px; background:transparent; border:none;"
        )
        self._err_lbl.setVisible(False)
        stage_lay.addWidget(self._err_lbl)

        root.addWidget(self._stage, 1)

        # ── transport bar ──
        bar = QWidget()
        bar.setStyleSheet(f"background:{_th.BG2}; border:none; border-top:1px solid {_th.border_color(0.08)};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(10)

        self._btn_play = QPushButton()
        self._btn_play.setCursor(Qt.PointingHandCursor)
        self._btn_play.setFixedSize(34, 34)
        self._set_play_icon(False)
        self._btn_play.setStyleSheet(f"""
            QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.40)};
                border-radius:17px; color:{_th.FG_BLUE}; font-size:14px; }}
            QPushButton:hover {{ background:{_th.accent(0.30)}; border-color:{_th.accent(0.60)}; }}
            QPushButton:pressed {{ background:{_th.accent(0.44)}; }}
            QPushButton:disabled {{ background:{_th.border_color(0.05)}; border-color:{_th.border_color(0.08)}; color:{_th.FG_MUTED}; }}
        """)
        self._btn_play.clicked.connect(self._toggle_play)
        bl.addWidget(self._btn_play)

        self._cur_lbl = QLabel("0:00")
        self._cur_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._cur_lbl.setFixedWidth(54)
        self._cur_lbl.setAlignment(Qt.AlignCenter)
        bl.addWidget(self._cur_lbl)

        self._seek = QSlider(Qt.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.setStyleSheet(self._slider_ss())
        self._seek.sliderPressed.connect(self._on_seek_pressed)
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.sliderMoved.connect(self._on_seek_moved)
        bl.addWidget(self._seek, 1)

        self._tot_lbl = QLabel("0:00")
        self._tot_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._tot_lbl.setFixedWidth(54)
        self._tot_lbl.setAlignment(Qt.AlignCenter)
        bl.addWidget(self._tot_lbl)

        sep = QWidget(); sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{_th.border_color(0.10)};")
        bl.addWidget(sep)

        self._btn_mute = QPushButton()
        self._btn_mute.setCursor(Qt.PointingHandCursor)
        self._btn_mute.setFixedSize(30, 26)
        self._set_mute_icon(False)
        self._btn_mute.setStyleSheet(self._flat_btn_ss())
        self._btn_mute.clicked.connect(self._toggle_mute)
        bl.addWidget(self._btn_mute)

        self._vol = QSlider(Qt.Horizontal)
        self._vol.setRange(0, 100)
        self._vol.setValue(self._last_volume)
        self._vol.setFixedWidth(90)
        self._vol.setStyleSheet(self._slider_ss())
        self._vol.valueChanged.connect(self._on_volume)
        bl.addWidget(self._vol)

        sep2 = QWidget(); sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background:{_th.border_color(0.10)};")
        bl.addWidget(sep2)

        # Convert to Images — ekstrak frame video jadi gambar.
        self._btn_convert = QPushButton("  To Images")
        self._btn_convert.setCursor(Qt.PointingHandCursor)
        self._btn_convert.setFixedHeight(26)
        _cic = QIcon(icon("convert_frames"))
        if not _cic.isNull():
            self._btn_convert.setIcon(_cic)
            self._btn_convert.setIconSize(QSize(16, 16))
        self._btn_convert.setStyleSheet(f"""
            QPushButton {{ background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.08)};
                border-radius:6px; color:{_th.FG_BLUE_MUTED}; font-size:12px; padding:0 10px; }}
            QPushButton:hover {{ background:{_th.border_color(0.09)}; border-color:{_th.border_color(0.14)}; color:{_th.FG}; }}
            QPushButton:pressed {{ background:{_th.border_color(0.13)}; }}
        """)
        self._btn_convert.setToolTip("Convert video to images (frame by frame)")
        self._btn_convert.clicked.connect(self._open_convert)
        bl.addWidget(self._btn_convert)

        root.addWidget(bar)

        # ── info bar ──
        info = QWidget()
        info.setStyleSheet(f"background:{_th.BG2}; border:none; border-top:1px solid {_th.border_color(0.08)};")
        il = QHBoxLayout(info)
        il.setContentsMargins(12, 5, 12, 5)
        il.setSpacing(14)
        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        il.addWidget(self._info_lbl)
        il.addStretch()
        root.addWidget(info)

        self._update_info()
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(filepath)))

    # ── styles ──
    @staticmethod
    def _slider_ss():
        return f"""
            QSlider::groove:horizontal {{ height:4px; background:{_th.border_color(0.12)}; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:12px; height:12px; margin:-5px 0;
                background:{_th.accent_hex}; border-radius:6px; }}
            QSlider::sub-page:horizontal {{ background:{_th.accent(0.55)}; border-radius:2px; }}
        """

    @staticmethod
    def _flat_btn_ss():
        return f"""
            QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
                border-radius:6px; }}
            QPushButton:hover {{ background:{_th.border_color(0.07)}; border-color:{_th.border_color(0.14)}; }}
        """

    def _set_play_icon(self, playing: bool):
        name = "pause" if playing else "play"
        _ic = QIcon(icon(name))
        if not _ic.isNull():
            self._btn_play.setIcon(_ic)
            self._btn_play.setIconSize(QSize(16, 16))
            self._btn_play.setText("")
        else:
            self._btn_play.setText("⏸" if playing else "▶")

    def _set_mute_icon(self, muted: bool):
        name = "mute" if muted else "volume"
        _ic = QIcon(icon(name))
        if not _ic.isNull():
            self._btn_mute.setIcon(_ic)
            self._btn_mute.setIconSize(QSize(15, 15))
            self._btn_mute.setText("")
        else:
            self._btn_mute.setText("M" if muted else "V")

    # ── transport ──
    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _toggle_mute(self):
        muted = not self._audio_out.isMuted()
        self._audio_out.setMuted(muted)
        self._set_mute_icon(muted)

    def _on_volume(self, v):
        self._last_volume = v
        self._audio_out.setVolume(v / 100.0)
        if v > 0 and self._audio_out.isMuted():
            self._audio_out.setMuted(False)
            self._set_mute_icon(False)

    def _open_convert(self):
        # Pause dulu supaya playback ini tidak bersaing dgn player extractor.
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        from main_ext.class_VideoConvertDialog import VideoConvertDialog
        VideoConvertDialog.open(self, self.filepath)

    # ── seek ──
    def _on_seek_pressed(self):
        self._seeking = True

    def _on_seek_released(self):
        self._player.setPosition(self._seek.value())
        self._seeking = False

    def _on_seek_moved(self, v):
        self._cur_lbl.setText(_fmt_time(v))

    # ── player signals ──
    def _on_duration(self, dur):
        self._duration = dur
        self._seek.setRange(0, dur)
        self._tot_lbl.setText(_fmt_time(dur))
        self._update_info()

    def _on_position(self, pos):
        if not self._seeking:
            self._seek.setValue(pos)
            self._cur_lbl.setText(_fmt_time(pos))

    def _on_state(self, state):
        self._set_play_icon(state == QMediaPlayer.PlayingState)

    def _on_error(self, err, msg=""):
        if err == QMediaPlayer.NoError:
            return
        self._btn_play.setEnabled(False)
        self._seek.setEnabled(False)
        text = msg or "Codec video ini tidak didukung untuk diputar."
        self._err_lbl.setText(f"⚠ {text}")
        self._err_lbl.setVisible(True)
        self._err_lbl.raise_()

    # freeze saat dialog modal (blur fix)
    def event(self, e):


        from PySide6.QtCore import QEvent
        if e.type() == QEvent.WindowBlocked:
            self._enter_freeze()
        elif e.type() == QEvent.WindowUnblocked:
            self._exit_freeze()
        return super().event(e)

    def _enter_freeze(self):
        from PySide6.QtGui import QPixmap
        self._was_playing_before_block = (
            self._player.playbackState() == QMediaPlayer.PlayingState
        )
        if self._was_playing_before_block:
            self._player.pause()
        # Ambil frame terakhir on-demand (sekali, bukan tiap frame).
        try:
            frame = self._video.videoSink().videoFrame()
            img = frame.toImage() if frame.isValid() else None
        except Exception:
            img = None
        if img is not None and not img.isNull():
            pix = QPixmap.fromImage(img).scaled(
                self._stage.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._freeze_lbl.setPixmap(pix)
        self._freeze_lbl.setVisible(True)
        self._freeze_lbl.raise_()
        self._video.setVisible(False)   # sembunyikan native surface

    def _exit_freeze(self):
        self._video.setVisible(True)
        self._freeze_lbl.setVisible(False)
        self._freeze_lbl.clear()
        if self._was_playing_before_block:
            self._player.play()
        self._was_playing_before_block = False

    # ── info ──
    def _update_info(self):
        try:
            sz = os.path.getsize(self.filepath)
        except OSError:
            sz = 0
        fmt = os.path.splitext(self.filepath)[1].lstrip(".").upper() or "?"
        dur = _fmt_time(self._duration) if self._duration else "—"
        self._info_lbl.setText(
            f"{os.path.basename(self.filepath)}   ·   {dur}"
            f"   ·   {_human_size(sz)}   ·   {fmt}"
        )

    # ── cleanup ──
    def closeEvent(self, e):
        self._stop_player()
        super().closeEvent(e)

    def deleteLater(self):
        self._stop_player()
        super().deleteLater()

    def _stop_player(self):
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass
