from PySide6.QtCore import QPoint, QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtTest import QSignalSpy, QTest
from quill_native._engine import Document
from quill_native.editor import NativeEditor


def press(surface, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(surface[1], key, modifiers)
    QTest.qWait(5)


def test_real_window_keyboard_undo_redo(editor, surface, app):
    editor.forceActiveFocus()
    press(surface, Qt.Key.Key_A)
    press(surface, Qt.Key.Key_B)
    press(surface, Qt.Key.Key_C)
    assert editor._doc.line(0) == "abc"
    press(surface, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor._doc.line(0) == ""
    press(surface, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert editor._doc.line(0) == "abc"
    press(surface, Qt.Key.Key_Return)
    assert editor.lineCount == 2
    press(surface, Qt.Key.Key_Backspace)
    assert editor.lineCount == 1


def test_mouse_hit_testing_selection_and_clipboard(editor, surface, app):
    editor._doc = Document("hello world\nsecond line", "a.py")
    editor._reset("")
    QTest.qWait(40)
    # Click uses actual window coordinates: header 107px, item text starts at 75px.
    QTest.mouseClick(surface[1], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(96, 120))
    assert editor._doc.caret()[0] == 0
    assert 1 <= editor._doc.caret()[1] <= 4
    press(surface, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    editor.copy()
    assert app.clipboard().text() == "hello world\nsecond line"
    editor.cut()
    assert editor._doc.char_count() == 0
    editor.paste()
    assert editor._doc.line(1) == "second line"


def test_ime_preedit_commit_and_replacement(editor, app):
    preedit = QInputMethodEvent("にほん", [])
    QCoreApplication.sendEvent(editor, preedit)
    assert editor._preedit == "にほん"
    assert editor._doc.char_count() == 0
    commit = QInputMethodEvent()
    commit.setCommitString("日本🦀")
    QCoreApplication.sendEvent(editor, commit)
    assert editor._doc.line(0) == "日本🦀"
    assert editor.inputMethodQuery(Qt.InputMethodQuery.ImCursorPosition) == 4
    replacement = QInputMethodEvent()
    replacement.setCommitString("語", -2, 2)
    QCoreApplication.sendEvent(editor, replacement)
    assert editor._doc.line(0) == "日本語"


def test_altgr_text_is_not_dropped(editor):
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_At, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier, "@")
    QCoreApplication.sendEvent(editor, event)
    assert editor._doc.line(0) == "@"


def test_unsaved_guard_cancel_discard_and_save(app, tmp_path):
    # Standalone item tests the real controller without opening QML dialogs.
    item = NativeEditor()
    item._doc.insert("unsaved")
    confirm = QSignalSpy(item.confirmRequested)
    item.request("new")
    assert confirm.count() == 1
    item.resolveUnsaved("cancel")
    assert item._doc.line(0) == "unsaved"
    item.request("new")
    item.resolveUnsaved("discard")
    assert item._doc.char_count() == 0
    item._doc.insert("save me")
    item.request("new")
    save_as = QSignalSpy(item.saveAsRequested)
    item.resolveUnsaved("save")
    assert save_as.count() == 1
    destination = tmp_path / "new.py"
    item.saveUrl(QUrl.fromLocalFile(str(destination)))
    assert destination.read_text() == "save me"
    assert item._doc.char_count() == 0


def test_cache_bounded_and_only_visible_lines_copied(editor, app):
    editor._doc = Document("let value = 42;\n" * 60000, "x.rs")
    editor._reset("")
    for row in range(0, 30000, 1000):
        editor.scrollY = row * editor._line_height
        editor._prepare()
    metrics = editor.diagnostics()
    assert metrics["cached_layouts"] <= 1024
    assert metrics["visible_rows"] <= 40
    assert editor.lineCount == 60001


def test_keyboard_block_indent_and_outdent(editor, surface, app):
    editor._doc = Document("one\ntwo\n", "x.py")
    editor._reset("")
    press(surface, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    press(surface, Qt.Key.Key_Tab)
    assert editor._doc.line(0) == "    one"
    assert editor._doc.line(1) == "    two"
    press(surface, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert editor._doc.line(0) == "one"
    assert editor._doc.line(1) == "two"


def test_right_click_context_menu_and_selection(editor, surface, app):
    editor._doc = Document("hello world\nsecond line", "test.py")
    editor._reset("")
    QTest.qWait(40)
    spy = QSignalSpy(editor.contextMenuRequested)

    # Right click without selection moves caret and emits contextMenuRequested
    QTest.mouseClick(surface[1], Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(96, 120))
    assert spy.count() == 1
    assert not editor.hasSelection

    # Select all text
    press(surface, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert editor.hasSelection
    assert editor.selectedText == "hello world\nsecond line"

    # Right click inside selection preserves selection and emits signal
    QTest.mouseClick(surface[1], Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(96, 120))
    assert spy.count() == 2
    assert editor.hasSelection
    assert editor.selectedText == "hello world\nsecond line"

