import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle


@pytest.fixture(scope="session")
def app():
    instance = QGuiApplication.instance() or QGuiApplication([])
    instance.setQuitOnLastWindowClosed(False)
    QQuickStyle.setStyle("Basic")
    return instance


@pytest.fixture(scope="session")
def surface(app):
    from quill_native.app import create_window
    engine, window, editor = create_window(app)
    window.requestActivate()
    yield engine, window, editor
    window.setProperty("allowClose", True)
    window.close()
    app.processEvents()


@pytest.fixture
def editor(surface, app):
    _, _, item = surface
    item._doc = __import__("quill_native._engine", fromlist=["Document"]).Document()
    item._reset("")
    app.processEvents()
    return item
