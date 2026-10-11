# SPDX-License-Identifier: MPL-2.0
import os
import queue
import threading
from PySide6.QtCore import QObject, QUrl, QTimer, Signal
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QAudioOutput


class FrameExtractor(QObject):
    # Sinyal internal supaya callback worker-thread aman di-route ke main thread.
    _saved_one = Signal()

    def __init__(self, src, out_dir, mode="all", target_fps=30.0,
                 fmt="png", jpg_quality=92,
                 on_progress=None, on_done=None, parent=None):
        super().__init__(parent)
        self._src = src
        self._out_dir = out_dir
        self._mode = mode
        self._target_fps = max(0.1, float(target_fps))
        self._fmt = (fmt or "png").lower().lstrip(".")
        self._jpg_quality = int(jpg_quality)
        self._on_progress = on_progress
        self._on_done = on_done

        self._enqueued = 0           # frame dimasukkan ke antrian
        self._saved = 0              # frame benar-benar ditulis ke disk
        self._seen = 0
        self._duration = 0
        self._next_us = 0.0
        self._step_us = 1_000_000.0 / self._target_fps
        self._cancelled = False
        self._finished = False
        self._capture_done = False   # playback selesai, tinggal habisin antrian

        self._sink = QVideoSink(self)
        self._audio = QAudioOutput(self)
        self._audio.setMuted(True)
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.setAudioOutput(self._audio)

        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.durationChanged.connect(self._on_duration)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)
        self._saved_one.connect(self._on_saved_one)

        # Watchdog: frame berhenti datang N detik tanpa EndOfMedia (Qt FFmpeg
        # kadang stall di EOF) → anggap capture selesai.
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)

        # Save worker thread + antrian.
        self._q = queue.Queue()
        self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
        self._save_err = None

    # ── public ──
    def start(self):
        try:
            os.makedirs(self._out_dir, exist_ok=True)
        except Exception as e:
            self._fail(f"Gagal buat folder output: {e}")
            return
        self._save_thread.start()
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(self._src)))
        self._player.setPlaybackRate(1.0)   # >1× bikin Qt FFmpeg stall/drop
        self._player.play()
        self._arm_watchdog()

    def cancel(self):
        if self._finished:
            return
        self._cancelled = True
        self._stop_capture()
        self._q.put(None)                   # bangunin save-thread biar exit
        self._emit_done(True, "Dibatalkan")

    # ── capture (main thread) ──
    def _on_duration(self, dur):
        if dur > 0:
            self._duration = dur

    def _on_frame(self, frame):
        if self._cancelled or self._capture_done:
            return
        if not frame.isValid():
            return
        self._seen += 1
        ts = frame.startTime()
        if ts < 0:
            ts = int(self._seen * 33333)

        if self._mode == "fps":
            if ts + 1 < self._next_us:
                self._arm_watchdog()
                return
            self._next_us += self._step_us

        img = frame.toImage()
        if img.isNull():
            return
        # Copy supaya buffer frame tidak dipakai lintas thread (aman di-save).
        self._enqueued += 1
        self._q.put((self._enqueued, img.copy()))
        self._arm_watchdog()

    # ── save worker (background thread) ──
    def _save_loop(self):
        while True:
            item = self._q.get()
            if item is None:            # sentinel: stop
                break
            idx, img = item
            try:
                name = f"frame_{idx:05d}.{self._fmt}"
                path = os.path.join(self._out_dir, name)
                if self._fmt in ("jpg", "jpeg"):
                    img.save(path, "JPG", self._jpg_quality)
                else:
                    img.save(path)
            except Exception as e:
                self._save_err = str(e)
            self._saved_one.emit()      # route ke main thread

    def _on_saved_one(self):
        self._saved += 1
        if self._on_progress:
            self._on_progress(self._saved, self._duration, 0)
        # Kalau capture sudah selesai & antrian habis → done.
        if self._capture_done and self._saved >= self._enqueued:
            self._finalize()

    # ── end-of-capture handling ──
    def _on_status(self, status):
        if self._cancelled or self._finished:
            return
        if status == QMediaPlayer.EndOfMedia:
            self._end_capture()

    def _on_watchdog(self):
        if self._cancelled or self._finished:
            return
        self._end_capture()

    def _on_error(self, err, msg=""):
        if err == QMediaPlayer.NoError or self._finished:
            return
        # Error tapi sudah ada frame ke-capture → tetap finalize sebagai sukses
        # parsial; kalau belum ada frame sama sekali → gagal.
        self._stop_capture()
        if self._enqueued > 0:
            self._end_capture()
        else:
            self._q.put(None)
            self._emit_done(False, msg or "Codec video tidak didukung")

    def _end_capture(self):
        if self._capture_done:
            return
        self._capture_done = True
        self._stop_capture()
        # Semua frame sudah masuk antrian? Tunggu save-thread habisin sisa.
        if self._saved >= self._enqueued:
            self._finalize()
        # else: _on_saved_one akan finalize saat antrian habis.

    def _finalize(self):
        self._q.put(None)               # stop save-thread
        if self._enqueued == 0:
            self._emit_done(False, "Tidak ada frame yang bisa diekstrak")
        elif self._save_err:
            self._emit_done(False, f"Gagal simpan: {self._save_err}")
        else:
            self._emit_done(True, f"{self._saved} frame tersimpan")

    # ── helpers ──
    def _arm_watchdog(self):
        self._watchdog.start(3000)      # 3s tanpa frame baru → selesai

    def _stop_capture(self):
        try:
            self._watchdog.stop()
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:
            pass

    def _fail(self, msg):
        self._emit_done(False, msg)

    def _emit_done(self, ok, msg):
        if self._finished:
            return
        self._finished = True
        if self._on_done:
            self._on_done(ok, self._saved, msg)
