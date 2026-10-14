# SPDX-License-Identifier: MPL-2.0
"""Skia GPU composition of Qt-rendered UI assets.

Qt remains the font/icon rasterizer and native window compositor. Solid widget
backgrounds and rounded borders are Skia vectors (skia_button/skia_widgets);
native item content and unsupported custom assets still come from Qt.
One private OpenGL context renders transformations, clips and blends on GPU
surfaces. Reading the small UI surfaces back to QImage deliberately preserves
QWidget.grab(), graphics-effect ownership and translucent Windows popups; it
does not introduce native GL children or change their stacking/input behavior.
There is no Skia raster or text-rendering path. Unsupported machines use Qt.
The short frozen entry cover may use core/skia_snapshot's direct framebuffer
path when Qt Quick already uses a compatible OpenGL compositor.
"""
from collections import OrderedDict
import atexit
import logging
import math
import os

from PySide6.QtCore import QObject, QPoint, QRectF, QThread, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext,
    QPixmap, QSurfaceFormat,
)
from PySide6.QtWidgets import QGraphicsEffect, QGraphicsOpacityEffect

try:
    import skia
except ImportError:
    skia = None

_LOG = logging.getLogger(__name__)
_TEXTURE_BUDGET = 32 * 1024 * 1024
_SURFACE_BUDGET = 24 * 1024 * 1024
_RENDERER = None
_UNAVAILABLE = None


class _SkiaGpuRenderer(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._closed = False
        self._rendering = False
        self._textures = OrderedDict()
        self._surfaces = OrderedDict()
        self._texture_bytes = 0
        self._surface_bytes = 0
        self._frames = 0
        self._uploads = 0
        self._blur_frames = 0
        self._rust_gaussian = None
        self._last_error = None
        self._gpu = None
        self._sampling = skia.SamplingOptions(skia.FilterMode.kLinear)
        self._context = QOpenGLContext(self)
        self._surface = None

    def initialize(self):
        # Initialize separately so the factory can dispose a partially-created
        # Qt/Skia context if any driver or allocation step fails.
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setStencilBufferSize(8)
        fmt.setAlphaBufferSize(8)
        self._context.setFormat(fmt)
        if not self._context.create():
            raise RuntimeError("Qt could not create an OpenGL context")
        self._surface = QOffscreenSurface()
        self._surface.setParent(self)
        self._surface.setFormat(self._context.format())
        self._surface.create()
        if not self._surface.isValid():
            raise RuntimeError("Qt could not create an offscreen OpenGL surface")
        previous = QOpenGLContext.currentContext()
        previous_surface = previous.surface() if previous is not None else None
        try:
            if not self._context.makeCurrent(self._surface):
                raise RuntimeError("Qt could not activate the OpenGL context")
            self._gpu = skia.GrDirectContext.MakeGL()
            if self._gpu is None:
                raise RuntimeError("Skia could not create its GPU context")
            self._gpu.setResourceCacheLimit(_TEXTURE_BUDGET + _SURFACE_BUDGET)
            functions = self._context.functions()
            self._renderer_name = str(functions.glGetString(0x1F01))
            self._vendor = str(functions.glGetString(0x1F00))
            actual = self._context.format()
            self._version = f"{actual.majorVersion()}.{actual.minorVersion()}"
        finally:
            self._context.doneCurrent()
            if previous is not None and previous_surface is not None:
                previous.makeCurrent(previous_surface)
        self.parent().aboutToQuit.connect(self.close)

    @staticmethod
    def color(value):
        value = QColor(value)
        return skia.Color4f(value.redF(), value.greenF(), value.blueF(), value.alphaF())

    def image(self, source):
        """Upload a Qt raster asset once per cache key, retaining Qt glyphs."""
        if source.isNull():
            return None
        key = (type(source), source.cacheKey())
        cached = self._textures.pop(key, None)
        if cached is not None:
            self._textures[key] = cached
            return cached[0]
        qt_image = source.toImage() if isinstance(source, QPixmap) else source
        qt_image = qt_image.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
        size = qt_image.width() * qt_image.height() * 4
        if size > _TEXTURE_BUDGET:
            raise RuntimeError("Qt UI texture exceeds the GPU upload budget")
        info = skia.ImageInfo.Make(qt_image.width(), qt_image.height(),
                                  skia.ColorType.kRGBA_8888_ColorType,
                                  skia.AlphaType.kPremul_AlphaType)
        raster = skia.Image.MakeRasterCopy(skia.Pixmap(
            info, qt_image.constBits(), qt_image.bytesPerLine(),
        ))
        texture = raster.makeTextureImage(self._gpu)
        if texture is None:
            raise RuntimeError("Skia could not upload a Qt UI texture")
        self._textures[key] = (texture, size)
        self._texture_bytes += size
        self._uploads += 1
        while self._texture_bytes > _TEXTURE_BUDGET or len(self._textures) > 128:
            _, (_, old_size) = self._textures.popitem(last=False)
            self._texture_bytes -= old_size
        return texture

    def draw_image(self, canvas, source, destination, opacity=1.0):
        if opacity <= 0.0 or source.isNull():
            return
        texture = self.image(source)
        destination = QRectF(destination)
        paint = skia.Paint(Alphaf=max(0.0, min(1.0, float(opacity))), AntiAlias=True)
        canvas.drawImageRect(
            texture,
            skia.Rect.MakeXYWH(destination.x(), destination.y(),
                               destination.width(), destination.height()),
            self._sampling, paint,
        )

    def _render_target(self, width, height):
        key = (width, height)
        cached = self._surfaces.pop(key, None)
        if cached is not None:
            self._surfaces[key] = cached
            return cached[0]
        info = skia.ImageInfo.Make(
            width, height, skia.ColorType.kRGBA_8888_ColorType,
            skia.AlphaType.kPremul_AlphaType,
        )
        surface = skia.Surface.MakeRenderTarget(self._gpu, skia.Budgeted.kYes, info)
        if surface is None:
            raise RuntimeError("Skia could not allocate a GPU UI surface")
        size = width * height * 4
        self._surfaces[key] = (surface, size)
        self._surface_bytes += size
        while self._surface_bytes > _SURFACE_BUDGET or len(self._surfaces) > 16:
            _, (_, old_size) = self._surfaces.popitem(last=False)
            self._surface_bytes -= old_size
        return surface

    def render(self, logical_size, ratio, draw):
        if self._closed or self._rendering:
            return None
        if hasattr(logical_size, "width"):
            logical_width, logical_height = logical_size.width(), logical_size.height()
        else:
            logical_width, logical_height = logical_size
        if logical_width <= 0 or logical_height <= 0 or not math.isfinite(ratio) or ratio <= 0:
            return None
        width = max(1, math.ceil(logical_width * ratio))
        height = max(1, math.ceil(logical_height * ratio))
        if width * height * 4 > _SURFACE_BUDGET:
            return None
        previous = QOpenGLContext.currentContext()
        previous_surface = previous.surface() if previous is not None else None
        self._rendering = True
        try:
            if not self._context.makeCurrent(self._surface):
                raise RuntimeError("Qt could not activate the GPU UI context")
            surface = self._render_target(width, height)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            saved = canvas.save()
            try:
                canvas.scale(ratio, ratio)
                draw(canvas, self)
            finally:
                canvas.restoreToCount(saved)
            self._gpu.flushAndSubmit()
            output = QImage(width, height, QImage.Format_RGBA8888_Premultiplied)
            if not surface.readPixels(surface.imageInfo(), output.bits(), output.bytesPerLine()):
                raise RuntimeError("Skia could not read back its GPU UI surface")
            output.setDevicePixelRatio(ratio)
            self._frames += 1
            return output
        except Exception as exc:
            message = str(exc)
            if message != self._last_error:
                _LOG.warning("Skia GPU UI frame failed; using Qt: %s", message)
            self._last_error = message
            return None
        finally:
            self._context.doneCurrent()
            if previous is not None and previous_surface is not None:
                previous.makeCurrent(previous_surface)
            self._rendering = False

    def gaussian(self, source, sigma):
        if self._closed or self._rendering or source.width()*source.height()*4 > _SURFACE_BUDGET:
            return None
        previous = QOpenGLContext.currentContext()
        previous_surface = previous.surface() if previous is not None else None
        self._rendering = True
        try:
            if not self._context.makeCurrent(self._surface):
                return None
            self._gpu.flushAndSubmit()
            if self._rust_gaussian is None:
                from core.rust_blur import RustGaussian
                self._rust_gaussian = RustGaussian()
            return self._rust_gaussian.blur(source, sigma)
        finally:
            # Raw GL is external to Ganesh's state cache. Re-establish its
            # state before any button/card/Qt-font asset composition.
            self._gpu.resetContext()
            self._context.doneCurrent()
            if previous is not None and previous_surface is not None:
                previous.makeCurrent(previous_surface)
            self._rendering = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        previous = QOpenGLContext.currentContext()
        previous_surface = previous.surface() if previous is not None else None
        current = self._surface is not None and self._context.makeCurrent(self._surface)
        try:
            if self._rust_gaussian is not None:
                self._rust_gaussian.close()
                self._rust_gaussian = None
            self._textures.clear()
            self._surfaces.clear()
            if self._gpu is not None:
                if current:
                    self._gpu.releaseResourcesAndAbandonContext()
                else:
                    self._gpu.abandonContext()
                self._gpu = None
        finally:
            self._context.doneCurrent()
            if self._surface is not None:
                self._surface.destroy()
            if previous is not None and previous_surface is not None and previous is not self._context:
                previous.makeCurrent(previous_surface)


def _renderer():
    global _RENDERER, _UNAVAILABLE
    app = QGuiApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return None
    if _RENDERER is not None:
        return None if _RENDERER._closed else _RENDERER
    if _UNAVAILABLE is not None:
        return None
    if skia is None:
        _UNAVAILABLE = "skia-python is not installed"
    elif os.environ.get("RAK_DISABLE_SKIA_GPU") == "1":
        _UNAVAILABLE = "disabled by RAK_DISABLE_SKIA_GPU"
    elif app.platformName() in ("offscreen", "minimal"):
        _UNAVAILABLE = f"{app.platformName()} Qt platform has no native GPU surface"
    else:
        candidate = None
        try:
            candidate = _SkiaGpuRenderer(app)
            candidate.initialize()
            _RENDERER = candidate
        except Exception as exc:
            _UNAVAILABLE = str(exc)
            _LOG.warning("Skia GPU UI unavailable; using Qt: %s", exc)
            if candidate is not None:
                candidate.close()
                candidate.deleteLater()
    return _RENDERER


def render_gpu(logical_size, device_pixel_ratio, draw):
    """Return a GPU-rendered, DPR-tagged QImage, or None for Qt fallback."""
    renderer = _renderer()
    if renderer is None:
        return None
    return renderer.render(logical_size, float(device_pixel_ratio), draw)


def gpu_available():
    """Initialize once on the GUI thread and report GPU composition support."""
    return _renderer() is not None


def blur_gpu(source, radius=18.0):
    """Custom Rust Gaussian on the shared GPU context, in logical pixels.

    Rust's separable GLSL shader runs on the existing OpenGL context. Only the
    finished, frozen backdrop is read back for Qt's native window compositor;
    fades reuse that image rather than re-running the filter every frame.
    None requests the Qt fallback when a GPU/context/allocation is unavailable.
    """
    renderer = _renderer()
    radius = float(radius)
    if renderer is None or source.isNull() or not math.isfinite(radius):
        return None
    ratio = source.devicePixelRatioF()
    size = (source.width() / ratio, source.height() / ratio)
    sigma = max(0.0, radius) * 0.5
    result = renderer.gaussian(source, sigma*ratio)
    if result is not None:
        renderer._blur_frames += 1
        return result

    def draw(canvas, bridge):
        texture = bridge.image(source)
        paint = skia.Paint()
        if sigma > 0:
            paint.setImageFilter(skia.ImageFilters.Blur(sigma, sigma, skia.TileMode.kClamp))
        canvas.drawImageRect(texture, skia.Rect.MakeWH(*size), bridge._sampling, paint)

    result = renderer.render(size, ratio, draw)
    if result is not None:
        renderer._blur_frames += 1
    return result


class GpuOpacityEffect(QGraphicsOpacityEffect):
    """Qt-compatible opacity effect composed on the shared Skia GPU surface."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_key = None
        self._frame = None

    def draw(self, painter):
        if self.opacity() <= 0.0:
            return
        if self.opacity() == 1.0 and self.opacityMask().style() == Qt.NoBrush:
            # Vector widget surfaces already use Skia. Fully opaque content
            # needs no extra source capture, texture upload or GPU readback.
            self.drawSource(painter)
            return
        if self.opacityMask().style() != Qt.NoBrush:
            super().draw(painter)
            return
        if not gpu_available():
            super().draw(painter)
            return
        offset = QPoint()
        source = self.sourcePixmap(Qt.LogicalCoordinates, offset, QGraphicsEffect.NoPad)
        if source.isNull():
            return
        ratio = source.devicePixelRatioF()
        size = source.deviceIndependentSize()
        key = (source.cacheKey(), ratio, self.opacity())
        if key != self._frame_key:
            def draw(canvas, bridge):
                bridge.draw_image(canvas, source,
                                  QRectF(0, 0, size.width(), size.height()), self.opacity())
            self._frame = render_gpu(size, ratio, draw)
            self._frame_key = key if self._frame is not None else None
        if self._frame is None:
            super().draw(painter)
        else:
            painter.drawImage(offset, self._frame)


def gpu_backend_status():
    """Read-only diagnostics; does not create a context just to inspect it."""
    renderer = _RENDERER
    if renderer is None or renderer._closed:
        return {"backend": "qt", "reason": _UNAVAILABLE or "GPU renderer not initialized"}
    return {
        "backend": "skia-gpu", "renderer": renderer._renderer_name,
        "vendor": renderer._vendor, "opengl": renderer._version,
        "frames": renderer._frames, "texture_uploads": renderer._uploads,
        "blur_frames": renderer._blur_frames,
        "blur_backend": "rust-gaussian-opengl" if renderer._rust_gaussian is not None and renderer._rust_gaussian.frames else "skia-gaussian-opengl",
        "rust_blur_frames": renderer._rust_gaussian.frames if renderer._rust_gaussian is not None else 0,
        "rust_blur_error": renderer._rust_gaussian.error if renderer._rust_gaussian is not None else None,
        "texture_bytes": renderer._texture_bytes, "surface_bytes": renderer._surface_bytes,
        "last_error": renderer._last_error,
    }


def _shutdown():
    renderer = _RENDERER
    if renderer is not None and not renderer._closed:
        try:
            renderer.close()
        except RuntimeError:
            pass  # Qt may already have destroyed its application at exit.


atexit.register(_shutdown)
