# SPDX-License-Identifier: MPL-2.0
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RENDER_LOOP"] = "basic"

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent, QKeyEvent, QPainter, QImage
from quill_native.editor import NativeEditor
from quill_native._engine import Document

@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app

SAMPLE_CODE = """def check_inputs(self):
    name = self.input_name.text()
    p1 = self.input_port1.text()
    p2 = self.input_port2.text()

    if getattr(self, "_port_mode"):
        valid = bool(name and p1)
    else:
        valid = bool(name and p1 and p2)

    self.btn_create2.setEnabled(valid)

def update(self):
    pass
"""

def _create_editor(code=SAMPLE_CODE, filename="test.py"):
    editor = NativeEditor()
    editor.setWidth(800)
    editor.setHeight(600)
    editor._doc = Document(code, filename)
    editor._reset(filename)
    return editor

def test_editor_folding_detection(qapp):
    editor = _create_editor()
    editor._folds_dirty = True
    editor._compute_folding()

    # Function check_inputs starts at line 0, inner block at line 5 (if), line 7 (else), line 12 (update)
    assert editor.isFoldable(0) is True
    assert editor.isFoldable(5) is True  # if getattr
    assert editor.isFoldable(7) is True  # else
    assert editor.isFoldable(12) is True # def update

    # Line 1 (name = ...) and line 11 (blank line) are not block headers
    assert editor.isFoldable(1) is False
    assert editor.isFoldable(11) is False

def test_editor_bracket_folding(qapp):
    js_code = """function test() {
    let a = 1;
    if (a > 0) {
        console.log("hello");
    }
}
"""
    editor = _create_editor(js_code, "test.js")
    editor._folds_dirty = True
    editor._compute_folding()

    # Function test starts at line 0, if block at line 2
    assert editor.isFoldable(0) is True
    assert editor.isFoldable(2) is True
    assert editor._fold_ranges[0] == 5
    assert editor._fold_ranges[2] == 4

def test_editor_toggle_fold(qapp):
    editor = _create_editor()
    editor._changed(0)
    editor._prepare()

    total_lines = editor.lineCount
    assert total_lines == 15
    assert len(editor._visible_rows_map) == 15

    # Fold line 0 (check_inputs)
    editor.toggleFold(0)
    assert editor.isFolded(0) is True

    # End of check_inputs is line 10, so lines 1..10 are hidden
    for r in range(1, 11):
        assert r in editor._hidden_rows
    assert 0 not in editor._hidden_rows
    assert 11 in editor._visible_rows_map # blank line before update
    assert 12 in editor._visible_rows_map # def update

    # Unfold line 0
    editor.toggleFold(0)
    assert editor.isFolded(0) is False
    assert len(editor._hidden_rows) == 0
    assert len(editor._visible_rows_map) == 15

def test_editor_fold_all_unfold_all(qapp):
    editor = _create_editor()
    editor._changed(0)
    editor._prepare()

    editor.foldAll()
    assert editor.isFolded(0) is True
    assert editor.isFolded(12) is True
    assert len(editor._folded) >= 2
    assert len(editor._visible_rows_map) < editor.lineCount

    editor.unfoldAll()
    assert len(editor._folded) == 0
    assert len(editor._hidden_rows) == 0
    assert len(editor._visible_rows_map) == editor.lineCount

def test_editor_gutter_click_toggle_fold(qapp):
    editor = _create_editor()
    editor._changed(0)
    editor._prepare()

    # Fold button for line 0 is near (self._gutter - 12, 10)
    click_x = editor._gutter - 12
    click_y = 10.0 # line 0

    press_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(click_x, click_y),
        QPointF(click_x, click_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.mousePressEvent(press_event)
    assert editor.isFolded(0) is True

    # Clicking again unfolds it
    editor.mousePressEvent(press_event)
    assert editor.isFolded(0) is False

def test_editor_keyboard_nav_skips_hidden(qapp):
    editor = _create_editor()
    editor.hasActiveFocus = lambda: True
    editor._changed(0)
    editor._prepare()

    # Fold line 0 (lines 1..10 hidden)
    editor.toggleFold(0)
    editor._doc.set_cursor(0, 0, False)

    # Press Key_Down
    down_event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Down,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.keyPressEvent(down_event)

    caret_row, _ = editor._doc.caret()
    # Caret must not be inside hidden lines 1..10; it jumps to line 11 (the first visible line after fold)
    assert caret_row >= 11
    assert caret_row not in editor._hidden_rows

    # Press Key_Up: should jump back to line 0
    up_event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Up,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.keyPressEvent(up_event)
    caret_row, _ = editor._doc.caret()
    assert caret_row == 0

def test_editor_paint_rendering(qapp):
    # Verify that paint() draws without any errors
    editor = _create_editor()
    editor.hasActiveFocus = lambda: True
    editor._changed(0)
    editor._prepare()

    image = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    try:
        editor.paint(painter)
    finally:
        painter.end()

    assert editor._paint_count >= 1

    # Now fold line 0 and repaint
    editor.toggleFold(0)
    image2 = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    image2.fill(Qt.GlobalColor.black)
    painter2 = QPainter(image2)
    try:
        editor.paint(painter2)
    finally:
        painter2.end()

    assert editor._paint_count >= 2

def test_editor_minimap_cached_rendering(qapp):
    editor = _create_editor()
    editor.setWidth(800)
    editor.setHeight(600)
    editor._changed(0)
    editor._prepare()

    assert editor.minimapEnabled is True
    assert editor.minimapWidth == 76.0

    # Build minimap image
    editor._rebuild_minimap()
    assert editor._minimap_image is not None
    assert editor._minimap_image.width() == 76
    assert editor._minimap_image.height() >= 30 # 15 lines * 2px
    assert editor._minimap_dirty is False

def test_editor_minimap_click_and_drag_scroll(qapp):
    # Test scrolling with a tall document (e.g. 200 lines)
    tall_code = "\n".join([f"line_{i} = {i}" for i in range(200)])
    editor = _create_editor(tall_code, "test_tall.py")
    editor.setWidth(800)
    editor.setHeight(400)
    editor._changed(0)
    editor._prepare()

    assert editor.totalHeight > editor.height()
    max_scroll = editor.totalHeight - editor.height()

    # Click in minimap area (x = width - minimap_width/2)
    mm_x = editor.width() - editor.minimapWidth / 2.0

    # Click near top
    press_top = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(mm_x, 10),
        QPointF(mm_x, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.mousePressEvent(press_top)
    assert editor._dragging_minimap is True
    assert editor.scrollY < max_scroll * 0.2

    # Drag to bottom
    move_bottom = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(mm_x, editor.height() - 10),
        QPointF(mm_x, editor.height() - 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.mouseMoveEvent(move_bottom)
    assert editor.scrollY > max_scroll * 0.8

    # Release mouse
    release_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(mm_x, editor.height() - 10),
        QPointF(mm_x, editor.height() - 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    editor.mouseReleaseEvent(release_event)
    assert editor._dragging_minimap is False

def test_editor_minimap_hover_and_toggle(qapp):
    editor = _create_editor()
    editor.setWidth(800)
    editor.setHeight(600)
    editor._changed(0)
    editor._prepare()

    from PySide6.QtGui import QHoverEvent
    # Hover over editor text area
    hover_text = QHoverEvent(
        QHoverEvent.Type.HoverMove,
        QPointF(200, 100),
        QPointF(0, 0),
    )
    editor.hoverMoveEvent(hover_text)
    assert editor._hover_minimap is False

    # Hover over minimap
    mm_x = editor.width() - 20
    hover_mm = QHoverEvent(
        QHoverEvent.Type.HoverMove,
        QPointF(mm_x, 100),
        QPointF(200, 100),
    )
    editor.hoverMoveEvent(hover_mm)
    assert editor._hover_minimap is True

    # Disable minimap
    editor.minimapEnabled = False
    assert editor.minimapEnabled is False
    editor.hoverMoveEvent(hover_mm)
    assert editor._hover_minimap is False


def test_editor_minimap_initial_zero_geometry_and_adaptive_lines(qapp):
    from quill_native.editor import NativeEditor
    from PySide6.QtGui import QImage, QPainter
    editor = NativeEditor()
    assert editor.width() == 0.0
    editor.loadPath("build.py")
    # Simulate timer firing while width is still 0 (early in QML layout)
    editor._rebuild_minimap()
    assert editor._minimap_dirty is True
    assert editor._minimap_image is None

    # Now layout resolves
    editor.setWidth(800)
    editor.setHeight(600)
    assert editor._minimap_dirty is True

    # Paint triggers full rebuild
    target = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(target)
    editor.paint(painter)
    painter.end()

    assert editor._minimap_image is not None
    assert not editor._minimap_image.isNull()
    assert editor._minimap_image.height() >= 600
    assert editor._minimap_line_h >= 2.0


