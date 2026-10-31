# SPDX-License-Identifier: MPL-2.0
"""Original 48px image previews, decoded off the GUI thread without QPixmap."""
import os
import uuid

from PySide6.QtCore import QObject, Qt, QByteArray
from PySide6.QtGui import QImage, QPainter
from services.async_jobs import AsyncJobs
from core.file_types import IMAGE_EXT
from ui.image_viewer import _images, _lock


def decode_thumbnail(spec):
    path, mounted = spec
    data = None
    if mounted:
        from core import imgfs
        data, _ = imgfs.read_path(*mounted)
    if path.lower().endswith(('.svg', '.svgz')):
        from PySide6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(QByteArray(data)) if data is not None else QSvgRenderer(path)
        if not renderer.isValid():return True, QImage()
        image = QImage(48, 48, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
    else:
        image = QImage()
        if data is None:image.load(path)
        else:image.loadFromData(data)
        if not image.isNull():image = image.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return True, image


class ThumbnailCache(QObject):
    def __init__(self, parent, publish):
        super().__init__(parent)
        self._jobs = AsyncJobs(self, limit=4)
        self._publish = publish
        self._cache = {}
        self._pending = set()
        cache=self._cache
        def release_images():
            with _lock:
                for url in cache.values():_images.pop(url.rsplit('/',1)[-1],None)
            cache.clear()
        self.destroyed.connect(release_images)

    def request(self, path, *, info=None, mounted=None):
        if os.path.splitext(path)[1].lower() not in IMAGE_EXT:return None, ''
        try:
            if mounted or info is None:
                stat = os.stat(mounted[0] if mounted else path)
                stamp = stat.st_mtime_ns, stat.st_size
            else:stamp = info['mtime_ns'], info['size']
        except (OSError, KeyError):return None, ''
        key = (mounted or path, stamp)
        if key in self._cache:return key, self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            def finished(ok, image):
                self._pending.discard(key)
                url = ''
                if ok and not image.isNull():
                    identifier = 'thumb-' + uuid.uuid4().hex
                    with _lock:_images[identifier] = image
                    url = 'image://documents/' + identifier
                self._cache[key] = url
                self._publish(key, url)
            self._jobs.submit(key, decode_thumbnail, (path, mounted), finished)
        return key, ''

    def close(self):
        self._jobs.stop()
