# SPDX-License-Identifier: MPL-2.0
"""Present a frozen Qt page with Skia directly into Qt's OpenGL framebuffer.

Only the short, opaque entry cover uses this path. Qt rasterizes every glyph
in the snapshot; animation frames neither recapture widgets nor read back GPU
pixels. The existing QWidget snapshot path handles unavailable GL contexts.
"""
from PySide6.QtGui import QImage, QSurfaceFormat, QOpenGLContext
from PySide6.QtCore import QTimer
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.skia_gpu import skia, _SkiaGpuRenderer, _TEXTURE_BUDGET


class SnapshotGpuFrame(QOpenGLWidget):
    def __init__(self, owner, snapshot, background):
        super().__init__(owner)
        self._owner = owner
        self._snapshot = snapshot.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
        self._background = background
        self._gpu = None
        self._image = None
        self._surface = None
        self._target = None
        self._target_key = None
        self.error = None
        self.frames = 0
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setStencilBufferSize(8)
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)
        self.setGeometry(owner.rect())

    def initializeGL(self):
        if self._owner._stopped:
            return
        try:
            self.context().aboutToBeDestroyed.connect(self.shutdown)
            self._gpu = skia.GrDirectContext.MakeGL()
            if self._gpu is None:
                raise RuntimeError("Skia could not create the snapshot GPU context")
            self._gpu.setResourceCacheLimit(_TEXTURE_BUDGET)
            image = self._snapshot
            if image is None:
                image = self._owner._snapshot.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
            if image.sizeInBytes() > _TEXTURE_BUDGET:
                raise RuntimeError("Snapshot exceeds the GPU texture budget")
            info = skia.ImageInfo.Make(image.width(), image.height(),
                skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType)
            raster = skia.Image.MakeRasterCopy(skia.Pixmap(info, image.constBits(), image.bytesPerLine()))
            self._image = raster.makeTextureImage(self._gpu)
            if self._image is None:
                raise RuntimeError("Skia could not upload the Qt page snapshot")
            self._snapshot = None
        except Exception as error:
            self.error = str(error)

    def resizeGL(self, width, height):
        self._surface = self._target = self._target_key = None

    def paintGL(self):
        if self.error or self._gpu is None or self._image is None:
            return
        try:
            ratio = self.devicePixelRatioF()
            physical_size = self.size() * ratio
            width, height = physical_size.width(), physical_size.height()
            fbo = self.defaultFramebufferObject()
            key = (width, height, fbo)
            # Qt's compositor also uses this GL context, so refresh Skia's
            # cached GL state before drawing into Qt's currently bound FBO.
            self._gpu.resetContext()
            if key != self._target_key:
                self._surface = None
                self._target = skia.GrBackendRenderTarget(width, height, 0, 8,
                    skia.GrGLFramebufferInfo(fbo, 0x8058))  # GL_RGBA8
                self._surface = skia.Surface.MakeFromBackendRenderTarget(self._gpu,
                    self._target, skia.kBottomLeft_GrSurfaceOrigin,
                    skia.ColorType.kRGBA_8888_ColorType, None)
                if self._surface is None:
                    raise RuntimeError("Skia could not wrap Qt's snapshot framebuffer")
                self._target_key = key
            canvas = self._surface.getCanvas()
            canvas.clear(_SkiaGpuRenderer.color(self._background))
            # Qt rounds framebuffer/snapshot dimensions at fractional DPR.
            # Present physical pixels 1:1 instead of shrinking that rounded
            # texture into width*DPR, which would resample native glyphs.
            canvas.drawImage(self._image, 0, 0,
                             skia.SamplingOptions(skia.FilterMode.kNearest))
            saved = canvas.save()
            try:
                canvas.scale(ratio, ratio)
                for (top, bottom), alpha in zip(*self._owner._cover_state()):
                    if alpha:
                        color = self._background.toRgb()
                        color.setAlpha(alpha)
                        canvas.drawRect(skia.Rect.MakeXYWH(0, top, self.width(), bottom - top),
                                        skia.Paint(Color4f=_SkiaGpuRenderer.color(color)))
            finally:
                canvas.restoreToCount(saved)
            self._gpu.flushAndSubmit()
            self.frames += 1
        except Exception as error:
            self.error = str(error)
            QTimer.singleShot(0, self._owner, self._owner._snapshot_gpu_failed)

    def shutdown(self):
        if self._gpu is None:
            return
        context = self.context()
        if context is not None and context.isValid():
            self.makeCurrent()
        current = context is not None and QOpenGLContext.currentContext() == context
        if current:
            self._surface = self._target = self._image = None
            self._gpu.releaseResourcesAndAbandonContext()
            self.doneCurrent()
        else:
            self._gpu.abandonContext()
            self._surface = self._target = self._image = None
        self._target_key = None
        self._gpu = None
