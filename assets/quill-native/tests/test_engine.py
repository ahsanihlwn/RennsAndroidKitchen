import random
from pathlib import Path

import pytest
from quill_native._engine import Document, languages


def full_text(doc):
    doc.select_all()
    return doc.selected_text()


def test_native_python_binding_and_unicode_history():
    doc = Document("a😀e\u0301\r\nx", "test.rs")
    doc.set_cursor(0, 5)
    doc.delete(True)
    assert doc.line(0) == "a😀"
    doc.move_cursor("right")
    assert doc.caret() == (1, 0)
    doc.delete(True)
    assert doc.line(0) == "a😀x"
    doc.undo()
    assert doc.line_count() == 2
    doc.redo()
    assert doc.line_count() == 1


def test_random_edits_against_independent_string():
    rng = random.Random(734891)
    reference = ""
    doc = Document()
    for _ in range(2500):
        a, b = sorted([rng.randrange(len(reference) + 1), rng.randrange(len(reference) + 1)])
        inserted = rng.choice(["", "x", "🦀", "abc", "é", "日本語"])
        # These tests remain on one line. Qt columns are UTF-16, Python indexes are scalars.
        offset = lambda i: len(reference[:i].encode("utf-16-le")) // 2
        doc.set_cursor(0, offset(a))
        doc.set_cursor(0, offset(b), True)
        doc.insert(inserted)
        reference = reference[:a] + inserted + reference[b:]
        assert doc.line(0) == reference
        assert doc.byte_count() == len(reference.encode())


def test_file_roundtrip_and_failed_load_preserves_document(tmp_path):
    path = tmp_path / "file.py"
    original = "\ufeffprint('😀')\r\n# mixed\n"
    path.write_bytes(original.encode())
    doc = Document()
    doc.load(str(path))
    assert doc.language() == "Python"
    assert not doc.dirty()
    doc.insert("#")
    assert doc.dirty()
    doc.save(str(path))
    assert path.read_bytes() == ("\ufeff#" + original[1:]).encode()
    assert not doc.dirty()
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(OSError):
        doc.load(str(bad))
    assert doc.line(0).startswith("#print")


def test_coloring_multiline_and_language_detection():
    for extension, language in [
        ("rs", "Rust"), ("py", "Python"), ("tsx", "TypeScript"), ("qml", "QML"),
        ("json", "JSON"), ("md", "Markdown"), ("s", "Assembly"), ("swift", "Swift"),
        ("kt", "Kotlin"), ("smali", "Smali"), ("diff", "Diff"), ("prop", "INI"),
        ("dts", "Device Tree"), ("bp", "Blueprint"), ("zig", "Zig"), ("proto", "Protobuf"),
        ("m", "Objective-C"), ("wat", "WAT"), ("scala", "Scala"), ("nim", "Nim")
    ]:
        assert Document("", f"x.{extension}").language() == language
    assert Document("", "Kconfig").language() == "Kconfig"
    assert Document("", "Android.bp").language() == "Blueprint"
    assert len(languages()) == 54
    doc = Document("/* outer\ncomment\n*/\nlet n = 42;", "x.rs")
    rows, ready = doc.viewport(0, 4)
    assert ready
    assert rows[1][1][0][2] == 3
    assert any(kind == 1 for _, _, kind in rows[3][1])
    assert any(kind == 4 for _, _, kind in rows[3][1])
    doc.set_cursor(0, 0)
    doc.set_cursor(0, 2, True)
    doc.insert("")
    rows, _ = doc.viewport(0, 4)
    assert not any(kind == 3 for _, _, kind in rows[1][1])


def test_long_line_preview_does_not_truncate_saved_file(tmp_path):
    value = "x" * 40000
    doc = Document(value, "long.js")
    rows, _ = doc.viewport(0, 1)
    assert len(rows[0][0]) == 8192
    assert not rows[0][1]
    path = tmp_path / "long.js"
    doc.save(str(path))
    assert path.read_text() == value


def test_budgeted_color_scan_and_streaming_find():
    doc = Document("let x = 1;\n" * 8000, "a.rs")
    _, ready = doc.viewport(7900, 20)
    assert not ready
    for _ in range(10):
        _, ready = doc.viewport(7900, 20)
        if ready:
            break
    assert ready
    doc.move_cursor("doc_end")
    assert doc.find("x = 1")
    assert doc.selected_text() == "x = 1"
    assert doc.caret()[0] == 0


def test_save_failure_does_not_clear_dirty(tmp_path):
    doc = Document("hello")
    doc.insert("X")
    with pytest.raises(OSError):
        doc.save(str(tmp_path / "missing" / "file.txt"))
    assert doc.dirty()


def test_delete_undo_restores_collapsed_cursor():
    doc = Document("hello")
    doc.set_cursor(0, 3)
    doc.delete(True)
    doc.undo()
    assert doc.caret() == (0, 3)
    assert not doc.has_selection()


def test_indent_and_outdent_use_core_actions_as_single_undo():
    doc = Document("alpha\r\nbeta\n", "file.py")
    doc.select_all()
    doc.indent(False)
    assert doc.line(0) == "    alpha"
    assert doc.line(1) == "    beta"
    doc.undo()
    assert full_text(doc) == "alpha\r\nbeta\n"
    doc.redo()
    doc.select_all()
    doc.indent(True)
    assert doc.line(0) == "alpha"
    assert doc.line(1) == "beta"


def test_history_trimming_preserves_recent_changes_and_redo():
    doc = Document()
    for _ in range(600):
        doc.insert("x")
    assert doc.history_info()[3] > 0
    count = 0
    while doc.undo():
        count += 1
    assert count == 128
    assert doc.line(0) == "x" * 472
    for _ in range(128):
        assert doc.redo()
    assert doc.line(0) == "x" * 600


def test_utf8_surrogate_and_invalid_load_edge_cases(tmp_path):
    doc = Document("😀🦀")
    doc.set_cursor(0, 1)
    assert doc.caret() == (0, 0)
    doc.set_cursor(0, 3)
    assert doc.caret() == (0, 2)
    path = tmp_path / "bare-cr.txt"
    path.write_bytes(b"a\rb")
    with pytest.raises(OSError, match="Bare CR"):
        doc.load(str(path))
    assert doc.line(0) == "😀🦀"


def test_random_undo_redo_branching_against_bounded_reference():
    rng = random.Random(815734)
    doc = Document()
    reference = ""
    undo, redo = [], []
    for _ in range(5000):
        command = rng.randrange(10)
        if command == 0:
            assert doc.undo() == bool(undo)
            if undo:
                redo.append(reference)
                reference = undo.pop()
        elif command == 1:
            assert doc.redo() == bool(redo)
            if redo:
                undo.append(reference)
                reference = redo.pop()
        else:
            a, b = sorted([rng.randrange(len(reference) + 1), rng.randrange(len(reference) + 1)])
            inserted = rng.choice(["", "x", "😀", "é", "日本語"])
            offset = lambda n: len(reference[:n].encode("utf-16-le")) // 2
            doc.set_cursor(0, offset(a))
            doc.set_cursor(0, offset(b), True)
            doc.insert(inserted)
            if a != b or inserted:
                undo.append(reference)
                undo = undo[-128:]
                redo.clear()
                reference = reference[:a] + inserted + reference[b:]
        assert doc.line(0) == reference
        assert doc.can_undo() == bool(undo)
        assert doc.can_redo() == bool(redo)


def test_outdent_without_indent_preserves_pristine_and_redo():
    doc = Document("hello")
    doc.indent(True)
    assert not doc.dirty()
    assert not doc.can_undo()
    doc.insert("x")
    doc.undo()
    doc.indent(True)
    assert doc.can_redo()
    doc.redo()
    assert doc.line(0) == "xhello"
