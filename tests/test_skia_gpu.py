# SPDX-License-Identifier: MPL-2.0
"""GPU backend lifecycle, Qt pixel upload and composition regressions.

Set RAK_TEST_SKIA_GPU=1 with a native Qt platform for the hardware check.
Portable checks inject the GL lifecycle and keep user preferences untouched.
"""
import os
import types
import unittest
from unittest.mock import Mock, patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core import skia_gpu as gpu


class FakeContext:
    current = None
    instances = []
    create_ok = True
    current_ok = True

    def __init__(self, _parent=None):
        self.fmt = QSurfaceFormat()
        self.target = None
        self.done_calls = 0
        self.instances.append(self)

    @classmethod
    def currentContext(cls):
        return cls.current

    def setFormat(self, value):
        self.fmt = value

    def format(self):
        return self.fmt

    def create(self):
        return self.create_ok

    def makeCurrent(self, surface):
        if not self.current_ok:
            return False
        self.target = surface
        FakeContext.current = self
        return True

    def surface(self):
        return self.target

    def doneCurrent(self):
        self.done_calls += 1
        if FakeContext.current is self:
            FakeContext.current = None

    def functions(self):
        return types.SimpleNamespace(glGetString=lambda _enum: "test GPU")


class FakeSurface:
    instances = []
    valid = True

    def __init__(self):
        self.destroyed = False
        self.instances.append(self)

    def setParent(self, _parent):
        pass

    def setFormat(self, _format):
        pass

    def create(self):
        pass

    def isValid(self):
        return self.valid

    def destroy(self):
        self.destroyed = True


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeContext.current = None
        FakeContext.instances = []
        FakeContext.create_ok = True
        FakeContext.current_ok = True
        FakeSurface.instances = []
        FakeSurface.valid = True
        self.renderers = []

    def tearDown(self):
        for renderer in self.renderers:
            renderer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def make_renderer(self):
        with patch.object(gpu, "QOpenGLContext", FakeContext):
            renderer = gpu._SkiaGpuRenderer(self.app)
        renderer._surface = FakeSurface()
        renderer._gpu = Mock()
        self.renderers.append(renderer)
        return renderer

    def previous_context(self):
        previous = FakeContext()
        previous.current_ok = True
        previous.makeCurrent(object())
        return previous

    def test_partial_initialization_failure_releases_resources_and_restores_context(self):
        fake_skia = types.SimpleNamespace(
            SamplingOptions=lambda *_: object(), FilterMode=types.SimpleNamespace(kLinear=1),
        )
        for phase in ("context", "surface", "skia", "cache"):
            with self.subTest(phase=phase):
                FakeContext.instances = []
                FakeSurface.instances = []
                FakeContext.create_ok = phase != "context"
                FakeSurface.valid = phase != "surface"
                previous = self.previous_context()
                direct = Mock()
                if phase == "cache":
                    direct.setResourceCacheLimit.side_effect = RuntimeError("cache initialization failed")
                fake_skia.GrDirectContext = types.SimpleNamespace(
                    MakeGL=lambda: None if phase == "skia" else direct,
                )
                # Retain Qt wrappers: otherwise children() can return a fresh
                # wrapper with a different Python id on the next call.
                old_children = self.app.children()
                old_child_ids = {id(child) for child in old_children}
                with patch.object(gpu, "skia", fake_skia), \
                     patch.object(gpu, "QOpenGLContext", FakeContext), \
                     patch.object(gpu, "QOffscreenSurface", FakeSurface), \
                     patch.object(gpu, "_RENDERER", None), \
                     patch.object(gpu, "_UNAVAILABLE", None), \
                     patch.object(self.app, "platformName", return_value="windows"), \
                     patch.dict(os.environ, {"RAK_DISABLE_SKIA_GPU": "0"}), \
                     self.assertLogs(gpu._LOG, level="WARNING"):
                    self.assertIsNone(gpu._renderer())
                    self.assertIsNotNone(gpu._UNAVAILABLE)
                    candidates = [child for child in self.app.children()
                                  if isinstance(child, gpu._SkiaGpuRenderer)
                                  and id(child) not in old_child_ids]
                    self.assertEqual(len(candidates), 1)
                    self.assertTrue(candidates[0]._closed)
                self.assertIs(FakeContext.current, previous)
                self.assertTrue(all(surface.destroyed for surface in FakeSurface.instances))
                if phase == "cache":
                    direct.releaseResourcesAndAbandonContext.assert_called_once()
                else:
                    direct.releaseResourcesAndAbandonContext.assert_not_called()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertFalse(isValid(candidates[0]))
        FakeContext.create_ok = True
        FakeSurface.valid = True

    @unittest.skipIf(gpu.skia is None, "Skia optional dependency is unavailable")
    def test_frame_failure_restores_context_and_next_frame_starts_clear(self):
        renderer = self.make_renderer()
        previous = self.previous_context()
        surface = gpu.skia.Surface.MakeRasterN32Premul(14, 10)
        with patch.object(gpu, "QOpenGLContext", FakeContext), \
             patch.object(renderer, "_render_target", return_value=surface):
            def fail(canvas, _bridge):
                canvas.translate(9, 0)
                canvas.clear(gpu.skia.ColorRED)
                raise RuntimeError("draw failed")

            with self.assertLogs(gpu._LOG, level="WARNING"):
                self.assertIsNone(renderer.render(QSize(7, 5), 2.0, fail))
            self.assertIs(FakeContext.current, previous)
            self.assertFalse(renderer._rendering)

            def draw(canvas, _bridge):
                self.assertIsNone(renderer.render(QSize(7, 5), 2.0, lambda *_: None))
                canvas.drawRect(gpu.skia.Rect.MakeXYWH(0, 0, 2, 5),
                                gpu.skia.Paint(Color=gpu.skia.ColorGREEN))

            output = renderer.render(QSize(7, 5), 2.0, draw)
            self.assertIsNotNone(output)
            self.assertEqual(output.size(), QSize(14, 10))
            self.assertEqual(output.devicePixelRatio(), 2.0)
            self.assertEqual(output.format(), QImage.Format_RGBA8888_Premultiplied)
            self.assertEqual(output.pixelColor(1, 2), QColor(0, 255, 0, 255))
            self.assertEqual(output.pixelColor(10, 2).alpha(), 0)
            self.assertIs(FakeContext.current, previous)

    def test_shutdown_is_idempotent_and_handles_lost_context(self):
        for available in (True, False):
            with self.subTest(current_available=available):
                renderer = self.make_renderer()
                previous = self.previous_context()
                renderer._context.current_ok = available
                direct = renderer._gpu
                released_under = []
                direct.releaseResourcesAndAbandonContext.side_effect = \
                    lambda: released_under.append(FakeContext.current)
                renderer._textures[1] = (object(), 4)
                renderer._surfaces[1] = (object(), 4)
                with patch.object(gpu, "QOpenGLContext", FakeContext):
                    renderer.close()
                    renderer.close()
                self.assertIs(FakeContext.current, previous)
                self.assertTrue(renderer._surface.destroyed)
                self.assertEqual(len(renderer._textures), 0)
                self.assertEqual(len(renderer._surfaces), 0)
                if available:
                    self.assertEqual(released_under, [renderer._context])
                    direct.releaseResourcesAndAbandonContext.assert_called_once()
                    direct.abandonContext.assert_not_called()
                else:
                    direct.abandonContext.assert_called_once()
                    direct.releaseResourcesAndAbandonContext.assert_not_called()

    @unittest.skipIf(gpu.skia is None, "Skia optional dependency is unavailable")
    def test_padded_qt_image_upload_and_texture_cache_bounds(self):
        renderer = self.make_renderer()
        native_skia = gpu.skia

        class RasterUpload:
            def __init__(self, image):
                self.image = image

            def makeTextureImage(self, _context):
                return self.image

        proxy = types.SimpleNamespace(**{name: getattr(native_skia, name)
                                        for name in dir(native_skia) if not name.startswith("__")})
        proxy.Image = types.SimpleNamespace(
            MakeRasterCopy=lambda pixmap: RasterUpload(native_skia.Image.MakeRasterCopy(pixmap)),
        )
        padded = bytearray([255, 0, 0, 255, 99, 99, 99, 99,
                           0, 255, 0, 255, 99, 99, 99, 99])
        source = QImage(padded, 1, 2, 8, QImage.Format_RGBA8888_Premultiplied)
        with patch.object(gpu, "skia", proxy), patch.object(gpu, "_TEXTURE_BUDGET", 16):
            texture = renderer.image(source)
            pixels = QImage(1, 2, QImage.Format_RGBA8888_Premultiplied)
            info = native_skia.ImageInfo.Make(1, 2, native_skia.ColorType.kRGBA_8888_ColorType,
                                              native_skia.AlphaType.kPremul_AlphaType)
            self.assertTrue(texture.readPixels(None, info, pixels.bits(), pixels.bytesPerLine()))
            self.assertEqual(pixels.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
            self.assertEqual(pixels.pixelColor(0, 1).getRgb(), (0, 255, 0, 255))
            self.assertIs(renderer.image(source), texture)
            self.assertEqual(renderer._uploads, 1)
            for color in (Qt.blue, Qt.yellow):
                replacement = QImage(2, 2, QImage.Format_RGBA8888_Premultiplied)
                replacement.fill(color)
                renderer.image(replacement)
            self.assertEqual(len(renderer._textures), 1)
            self.assertEqual(renderer._texture_bytes, 16)
            with self.assertRaisesRegex(RuntimeError, "budget"):
                renderer.image(QImage(3, 3, QImage.Format_RGBA8888_Premultiplied))
            self.assertEqual(renderer._texture_bytes, 16)
        renderer._textures.clear()
        renderer._texture_bytes = 0
        with patch.object(gpu, "skia", proxy):
            for value in range(130):
                tiny = QImage(1, 1, QImage.Format_RGBA8888_Premultiplied)
                tiny.fill(QColor(value, 0, 0))
                renderer.image(tiny)
            self.assertEqual(len(renderer._textures), 128)
            self.assertEqual(renderer._texture_bytes, 128 * 4)

    @unittest.skipIf(gpu.skia is None, "Skia optional dependency is unavailable")
    def test_surface_cache_reuses_targets_and_bounds_bytes_and_entry_count(self):
        renderer = self.make_renderer()
        native_skia = gpu.skia
        proxy = types.SimpleNamespace(**{name: getattr(native_skia, name)
                                        for name in dir(native_skia) if not name.startswith("__")})
        proxy.Surface = types.SimpleNamespace(MakeRenderTarget=lambda *_: object())
        with patch.object(gpu, "skia", proxy), patch.object(gpu, "_SURFACE_BUDGET", 80):
            first = renderer._render_target(2, 2)
            self.assertIs(renderer._render_target(2, 2), first)
            renderer._render_target(3, 3)
            renderer._render_target(4, 4)
            self.assertEqual(renderer._surface_bytes, 64)
            self.assertEqual(len(renderer._surfaces), 1)
            self.assertIsNone(renderer.render(QSize(20, 20), 1.0, lambda *_: None))
        renderer._surfaces.clear()
        renderer._surface_bytes = 0
        with patch.object(gpu, "skia", proxy):
            for width in range(1, 20):
                renderer._render_target(width, 1)
            self.assertEqual(len(renderer._surfaces), 16)
            self.assertNotIn((1, 1), renderer._surfaces)


@unittest.skipUnless(_NATIVE, "set RAK_TEST_SKIA_GPU=1 for native GPU proof")
class NativeContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_gpu_restores_external_context_and_uploads_padded_premultiplied_image(self):
        context = QOpenGLContext()
        self.assertTrue(context.create())
        surface = QOffscreenSurface()
        surface.setFormat(context.format())
        surface.create()
        self.assertTrue(context.makeCurrent(surface))
        renderer = None
        try:
            with patch.object(gpu, "_RENDERER", None), patch.object(gpu, "_UNAVAILABLE", None):
                renderer = gpu._renderer()
                self.assertIsNotNone(renderer, str(gpu.gpu_backend_status()))
                self.assertIs(QOpenGLContext.currentContext(), context)
                # Qt premultiplied RGBA row data, with deliberate stride padding.
                pixels = bytearray([64, 0, 0, 128, 77, 77, 77, 77,
                                    0, 128, 0, 128, 77, 77, 77, 77])
                source = QImage(pixels, 1, 2, 8, QImage.Format_RGBA8888_Premultiplied)
                def draw(canvas, bridge):
                    bridge.draw_image(canvas, source, QRectF(0, 0, 1, 2))

                output = gpu.render_gpu(QSize(3, 2), 1.5, draw)
                self.assertIsNotNone(output, str(gpu.gpu_backend_status()))
                self.assertEqual(output.size(), QSize(5, 3))
                self.assertEqual(output.devicePixelRatio(), 1.5)
                self.assertEqual(output.pixelColor(4, 0).alpha(), 0)
                self.assertIs(QOpenGLContext.currentContext(), context)
                before = renderer._uploads
                second = gpu.render_gpu(QSize(1, 2), 1.0, draw)
                self.assertEqual(renderer._uploads, before)
                for row in (0, 1):
                    self.assertEqual(second.pixelColor(0, row), source.pixelColor(0, row))
                renderer.close()
                self.assertIs(QOpenGLContext.currentContext(), context)
                self.assertIsNone(renderer.render(QSize(1, 2), 1.0, draw))
        finally:
            if renderer is not None and not renderer._closed:
                renderer.close()
            context.doneCurrent()
            surface.destroy()


if __name__ == "__main__":
    unittest.main()
