# SPDX-License-Identifier: MPL-2.0
import os

from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap, QIcon

from core.threader import run_in_pool
from main_ext.class_ImageViewer import RASTER_EXT, SVG_EXT

THUMB_EXT = frozenset(e.lower() for e in (RASTER_EXT + SVG_EXT))
_THUMB_PX = 48


def decode_thumb_png(ext, data=None, path=None):

    try:
        ext = (ext or "").lower()
        if ext in SVG_EXT:
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(QByteArray(data)) if data is not None else QSvgRenderer(path)
            if not renderer.isValid():
                return None
            img = QImage(_THUMB_PX, _THUMB_PX, QImage.Format_ARGB32)
            img.fill(0)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
        else:
            img = QImage()
            ok = img.loadFromData(data) if data is not None else img.load(path)
            if not ok or img.isNull():
                return None
            img = img.scaled(_THUMB_PX, _THUMB_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        buf = QByteArray()
        bio = QBuffer(buf)
        bio.open(QIODevice.WriteOnly)
        img.save(bio, "PNG")
        bio.close()
        return bytes(buf)
    except Exception:
        return None


_DRAIN_BATCH = 10   # berapa file yang di-"kick off" ke thread pool per tick


class ThumbnailMixin:


    def _thumb_init(self):
        self._thumb_cache = {}       # cache_key -> QIcon (hidup selama sesi tree ini)
        self._thumb_pending = set()  # cache_key yang lagi di-decode, cegah job dobel
        self._thumb_queue = []       # antrean callable murah, diisi sinkron
        self._thumb_draining = False

    def _thumb_enqueue(self, item, prepare_fn):

        self._thumb_queue.append((item, prepare_fn))
        if not self._thumb_draining:
            self._thumb_draining = True
            QTimer.singleShot(0, self._thumb_drain)

    def _thumb_drain(self):
        batch = self._thumb_queue[:_DRAIN_BATCH]
        del self._thumb_queue[:_DRAIN_BATCH]
        for item, prepare_fn in batch:
            try:
                # item bisa saja udah kehapus (folder di-refresh/collapse
                # cepat) sebelum antrean sempat diproses — skip diam-diam.
                if item.treeWidget() is None:
                    continue
            except RuntimeError:
                continue
            try:
                prepare_fn()
            except Exception:
                pass

        if self._thumb_queue:
            QTimer.singleShot(0, self._thumb_drain)
        else:
            self._thumb_draining = False

    def _thumb_apply_or_schedule(self, item, ext, cache_key, decode_fn):

        cached = self._thumb_cache.get(cache_key)
        if cached is not None:
            item.setIcon(0, cached)
            return
        if cache_key in self._thumb_pending:
            return
        self._thumb_pending.add(cache_key)

        def _job():
            return True, decode_fn()

        def _on_done(_token, ok, payload):
            self._thumb_pending.discard(cache_key)
            if not ok or not payload:
                return
            pix = QPixmap()
            if not pix.loadFromData(payload, "PNG") or pix.isNull():
                return
            ic = QIcon(pix)
            self._thumb_cache[cache_key] = ic
            try:
                item.setIcon(0, ic)
            except RuntimeError:
                # item sudah dihapus dari tree (folder di-refresh/file
                # dihapus) sebelum decode kelar — abaikan saja.
                pass

        run_in_pool(cache_key, _job, None, _on_done)

    def _schedule_file_thumbnail(self, item, phys_path, *, entry_info=None):

        ext = os.path.splitext(phys_path)[1].lower()
        if ext not in THUMB_EXT:
            return

        def _prepare():
            if entry_info is not None:
                if entry_info.get("mtime_ns") is None or entry_info.get("size") is None:
                    return
                cache_key = ("disk", phys_path, entry_info["mtime_ns"], entry_info["size"])
            else:
                try:
                    st = os.stat(phys_path)
                    cache_key = ("disk", phys_path, st.st_mtime_ns, st.st_size)
                except OSError:
                    cache_key = ("disk", phys_path, 0, 0)
            self._thumb_apply_or_schedule(
                item, ext, cache_key,
                lambda: decode_thumb_png(ext, path=phys_path),
            )

        self._thumb_enqueue(item, _prepare)

    def _schedule_mount_thumbnail(self, item, img_path, internal_path, ino):

        ext = os.path.splitext(internal_path)[1].lower()
        if ext not in THUMB_EXT:
            return

        def _prepare():
            cache_key = ("mount", img_path, internal_path, ino)

            def _decode():
                from core import imgfs
                try:
                    if ino is not None:
                        data = imgfs.read_ino(img_path, ino)
                    else:
                        data, _node = imgfs.read_path(img_path, internal_path)
                except Exception:
                    return None
                return decode_thumb_png(ext, data=data)

            self._thumb_apply_or_schedule(item, ext, cache_key, _decode)

        self._thumb_enqueue(item, _prepare)
