# SPDX-License-Identifier: MPL-2.0
"""Test suite for native QtTerm QML terminal backed by Rust termcore."""
import os
import sys
import time
import unittest
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QColor, QKeyEvent, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtCore import QUrl, QTimer, Qt, QPointF, QPoint, QRectF

from core.qtterm import register_qml_types, Terminal


class QtTermNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        register_qml_types()

    def setUp(self):
        self.engine = QQmlApplicationEngine()

    def tearDown(self):
        self.engine = None

    def test_qml_registration_and_properties(self):
        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 800; height: 600
            Terminal {
                id: term
                objectName: "testTerm"
                autoStart: false
                program: "cmd.exe"
                arguments: ["/c", "dir"]
                fontFamily: "Consolas"
                fontPixelSize: 16
                cursorShape: "beam"
                padding: 10
                foreground: "#ffffff"
                background: "#000000"
                cursorColor: "#ff0000"
                selectionColor: "#00ff00"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])

        root = comp.create()
        self.assertIsNotNone(root)
        term = root.findChild(Terminal, "testTerm")
        self.assertIsNotNone(term)

        self.assertEqual(term.program, "cmd.exe")
        self.assertEqual(term.arguments, ["/c", "dir"])
        self.assertEqual(term.fontPixelSize, 16)
        self.assertEqual(term.cursorShape, "beam")
        self.assertEqual(term.padding, 10)
        self.assertEqual(term.foreground.name(), "#ffffff")
        self.assertEqual(term.background.name(), "#000000")
        self.assertEqual(term.cursorColor.name(), "#ff0000")
        self.assertEqual(term.selectionColor.name(), "#00ff00")
        self.assertFalse(term.running)

    def test_terminal_interactive_execution_and_output(self):
        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 720; height: 480
            Terminal {
                id: term
                objectName: "liveTerm"
                autoStart: true
                program: "cmd.exe"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])

        root = comp.create()
        term = root.findChild(Terminal, "liveTerm")
        self.assertIsNotNone(term)
        self.assertTrue(term.running)

        # Wait for shell prompt
        found_prompt = False
        for _ in range(30):
            self.app.processEvents()
            text = term.screenText()
            if ">" in text or "Microsoft" in text:
                found_prompt = True
                break
            time.sleep(0.05)
        self.assertTrue(found_prompt, f"Shell prompt not detected: {term.screenText()!r}")

        # Send command into interactive terminal
        term.sendText("echo TERMINAL_INPUT_VERIFIED_12345\r\n")

        # Verify output appears on the terminal screen
        found_output = False
        for _ in range(40):
            self.app.processEvents()
            text = term.screenText()
            if "TERMINAL_INPUT_VERIFIED_12345" in text:
                found_output = True
                break
            time.sleep(0.05)
        self.assertTrue(found_output, f"Echoed output not captured in screen: {term.screenText()!r}")

        # Test selection & copy
        term.selectAll()
        self.assertTrue(term.hasSelection)
        term.copy()
        cb_text = QGuiApplication.clipboard().text()
        self.assertIn("TERMINAL_INPUT_VERIFIED_12345", cb_text)

        term.stop()
        self.assertFalse(term.running)

    def test_terminal_feed_and_methods(self):
        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 600; height: 400
            Terminal {
                id: term
                objectName: "feedTerm"
                autoStart: true
                program: "cmd.exe"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])

        root = comp.create()
        term = root.findChild(Terminal, "feedTerm")
        self.assertIsNotNone(term)

        # Direct feed text to VT emulator
        term.feedText("\x1b[31mRED_ALERT_TEST\x1b[0m\r\n")
        self.app.processEvents()
        self.assertIn("RED_ALERT_TEST", term.screenText())

        # Test debug info
        dbg = term.debugInfo()
        self.assertIn("cols=", dbg)
        self.assertIn("rows=", dbg)

        term.stop()

    def test_wheel_scroll_direction(self):
        """Verify wheel event with positive delta (wheel up) calls term_scroll with positive lines."""
        from unittest.mock import patch

        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 400; height: 300
            Terminal {
                id: term
                objectName: "wheelTerm"
                autoStart: true
                program: "cmd.exe"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        root = comp.create()
        term = root.findChild(Terminal, "wheelTerm")

        with patch("core.qtterm._termcore.term_scroll") as mock_scroll:
            we = QWheelEvent(
                QPointF(50, 50), QPointF(50, 50),
                QPoint(0, 0), QPoint(0, 120),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False
            )
            term.wheelEvent(we)
            mock_scroll.assert_called_once()
            args = mock_scroll.call_args[0]
            lines = args[1]
            self.assertEqual(lines, 3, "Wheel up (+120) must produce lines=+3 for upward scrolling")

        term.stop()

    def test_backspace_single_character(self):
        """Verify backspace key deletes a single character in interactive shell, not the whole word."""
        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 720; height: 480
            Terminal {
                id: term
                objectName: "bsTerm"
                autoStart: true
                program: "cmd.exe"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        root = comp.create()
        term = root.findChild(Terminal, "bsTerm")
        self.assertTrue(term.running)

        for _ in range(30):
            self.app.processEvents()
            if ">" in term.screenText():
                break
            time.sleep(0.05)

        for ch in "abcde":
            ke = QKeyEvent(QKeyEvent.Type.KeyPress, ord(ch.upper()), Qt.KeyboardModifier.NoModifier, ch)
            term.keyPressEvent(ke)
        time.sleep(0.15)
        self.app.processEvents()

        bs_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier, "\x08")
        term.keyPressEvent(bs_event)
        time.sleep(0.2)
        self.app.processEvents()

        text = term.screenText()
        self.assertIn("abcd", text, f"Expected 'abcd' after deleting 1 char, but got: {text!r}")
        self.assertNotIn("abcde", text)

        term.stop()

    def test_cursor_shape_properties(self):
        """Verify cursorShape property supports beam, underline, block."""
        qml = """
        import QtQuick
        import QtTerm 1.0

        Item {
            width: 400; height: 300
            Terminal {
                id: term
                objectName: "cursorTerm"
                autoStart: false
                cursorShape: "beam"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        root = comp.create()
        term = root.findChild(Terminal, "cursorTerm")
        self.assertEqual(term.cursorShape, "beam")
        self.assertEqual(term._cursor_shape_int(), 2)

        term.cursorShape = "underline"
        self.assertEqual(term.cursorShape, "underline")
        self.assertEqual(term._cursor_shape_int(), 1)

        term.cursorShape = "block"
        self.assertEqual(term.cursorShape, "block")
        self.assertEqual(term._cursor_shape_int(), 0)

        # cursorWidth property test
        self.assertEqual(term.cursorWidth, 5.5)
        term.cursorWidth = 6.0
        self.assertEqual(term.cursorWidth, 6.0)

    def test_resize_debouncing(self):
        """Verify resize timer debounces rapid geometric changes."""
        from unittest.mock import patch

        qml = """
        import QtQuick
        import QtTerm 1.0

        Terminal {
            id: term
            width: 400; height: 300
            autoStart: true
            program: "cmd.exe"
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        term = comp.create()
        self.assertTrue(term.running)

        with patch("core.qtterm._termcore.term_resize") as mock_resize:
            for w in range(420, 500, 20):
                term.setWidth(w)
            self.assertEqual(mock_resize.call_count, 0)
            time.sleep(0.1)
            self.app.processEvents()
            self.assertEqual(mock_resize.call_count, 1)

        term.stop()

    def test_cursor_crossfade_cycle(self):
        """Verify cursor crossfade fast transition and hold delay timing."""
        term = Terminal()
        term._cursor_blink = True
        # Mock focus
        term.hasActiveFocus = lambda: True

        # At t = 0 (start of fade-in)
        now = time.perf_counter()
        term._cursor_cycle_start = now
        term._on_anim_frame()
        self.assertAlmostEqual(term._cursor_opacity, 0.0, delta=0.05)

        # At t = 0.04s (mid fade-in: smoothstep at 50% is 0.5)
        term._cursor_cycle_start = now - 0.04
        term._on_anim_frame()
        self.assertAlmostEqual(term._cursor_opacity, 0.5, delta=0.1)

        # At t = 0.20s (solid ON hold)
        term._cursor_cycle_start = now - 0.20
        term._on_anim_frame()
        self.assertEqual(term._cursor_opacity, 1.0)
        self.assertTrue(term._cursor_on)

        # At t = 0.54s (mid fade-out: 40ms into 80ms fade-out)
        term._cursor_cycle_start = now - 0.54
        term._on_anim_frame()
        self.assertAlmostEqual(term._cursor_opacity, 0.5, delta=0.1)

        # At t = 0.70s (solid OFF hold)
        term._cursor_cycle_start = now - 0.70
        term._on_anim_frame()
        self.assertEqual(term._cursor_opacity, 0.0)
        self.assertFalse(term._cursor_on)

    def test_cursor_smooth_gliding_ease_out(self):
        """Verify cursor glides smoothly to next input coordinate using non-linear cubic ease-out."""
        term = Terminal()
        term._cols = 80
        term._rows = 24
        term._cell_w = 10.0
        term._cell_h = 20.0
        term._padding = 8.0
        term._info.cols = 80
        term._info.rows = 24
        term._info.cursor_col = 0
        term._info.cursor_row = 0
        term.hasActiveFocus = lambda: True

        # Initialize cursor position
        term._update_cursor_target(snap=True)
        self.assertEqual(term._cur_render_x, 8.0)
        self.assertEqual(term._cur_render_y, 8.0)

        # Move cursor to col = 5 (delta X = 50px)
        term._info.cursor_col = 5
        term._update_cursor_target(snap=False)
        self.assertTrue(term._cur_move_start > 0)
        self.assertEqual(term._cur_target_x, 8.0 + 5 * 10.0)

        # At t = 0.5 * duration (half time):
        # Quadratic ease-out: 1 - (1 - 0.5)^2 = 1 - 0.25 = 0.75
        now = time.perf_counter()
        term._cur_move_start = now - (0.5 * term._cur_move_duration)
        term._on_anim_frame()

        expected_x = 8.0 + 50.0 * 0.75  # 45.5px
        self.assertAlmostEqual(term._cur_render_x, expected_x, delta=0.5)

        # Non-linear check: significantly further along than linear 0.5 (which would be 33.0)
        self.assertGreater(term._cur_render_x, 8.0 + 50.0 * 0.5)

        # At t >= duration (finish):
        term._cur_move_start = now - (1.1 * term._cur_move_duration)
        term._on_anim_frame()
        self.assertEqual(term._cur_render_x, term._cur_target_x)
        self.assertEqual(term._cur_move_start, 0.0)

    def test_cursor_adapts_speed_and_prevents_lag_on_rapid_typing_and_backspace(self):
        """Verify rapid typing/auto-repeat scales down glide duration and caps lag on type and backspace."""
        term = Terminal()
        term._cols = 80
        term._rows = 24
        term._cell_w = 10.0
        term._cell_h = 20.0
        term._padding = 8.0
        term._info.cols = 80
        term._info.rows = 24
        term._info.cursor_col = 0
        term._info.cursor_row = 0
        term.hasActiveFocus = lambda: True

        term._update_cursor_target(snap=True)
        base_now = time.perf_counter()

        # First normal single step: smooth duration
        term._cur_last_target_time = base_now - 0.50
        term._info.cursor_col = 1
        term._update_cursor_target(snap=False)
        normal_dur = term._cur_move_duration
        self.assertGreaterEqual(normal_dur, 0.05)

        # Simulate rapid auto-repeat typing at 30ms intervals
        term._last_repeat_key_time = time.perf_counter()
        for col in range(2, 16):
            time.sleep(0.028)
            term._last_repeat_key_time = time.perf_counter()
            term._info.cursor_col = col
            term._update_cursor_target(snap=False)
            self.assertLessEqual(term._cur_move_duration, 0.024)
            self.assertLessEqual(abs(term._cur_target_x - term._cur_render_x), term._cell_w * 1.06)
            # Advance 16ms: cursor must keep up and finish within repeat interval
            term._advance_cursor_motion(term._cur_move_start + 0.022)
            self.assertAlmostEqual(term._cur_render_x, term._cur_target_x, delta=0.5)

        # Simulate rapid Backspace auto-repeat: no trailing space gap (> 0.90 cell) even before next frame
        for col in range(14, 4, -1):
            time.sleep(0.025)
            term._last_repeat_key_time = time.perf_counter()
            term._info.cursor_col = col
            term._update_cursor_target(snap=False)
            self.assertLessEqual(term._cur_render_x - term._cur_target_x, term._cell_w * 0.91)
            term._advance_cursor_motion(term._cur_move_start + 0.022)
            self.assertAlmostEqual(term._cur_render_x, term._cur_target_x, delta=0.5)


if __name__ == "__main__":
    unittest.main()
