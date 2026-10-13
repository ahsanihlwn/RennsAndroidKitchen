# SPDX-License-Identifier: MPL-2.0
"""Shared Settings dropdown: modal parenting, native input, persistence and cleanup."""
import ast
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, Qt, Slot
from PySide6.QtGui import QIcon, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QScrollArea
from shiboken6 import isValid

from app_ext.class_ChoiceDropdown import ChoiceDropdown, show_choice_popup
from app_ext.class_ChoiceMorphPopup import ChoiceMorphPopup
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, CrossfadeOverlay
from app_ext.class_ButtonPressAnimation import install_button_press_animations
from core.fonts import apply_app_font
from core.theme_binding import refresh_theme
from core.language_binding import bind_text, refresh_language
from core import ui_lang as _ul
from core.ui_lang import t
from main_ext.class_ThemeUi import theme as _th
from main_ext.class_ToggleSwitch import build_card, build_level_slider


def production_method(filename, cls_name, name):
    path = Path(__file__).resolve().parent.parent / filename
    module = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == cls_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = dict(globals())
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class ChoiceDropdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_app_font(cls.app)
        install_button_press_animations(cls.app)

    def setUp(self):
        self.patches = [patch.object(_th, "_animations_enabled", False),
                        patch.object(_th, "_blur_enabled", False)]
        for item in self.patches:
            item.start()
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(700, 500)
        self.root.show()
        self.dialog = None
        self.errors = []

    def tearDown(self):
        for window in (self.dialog, self.root):
            if window is not None and isValid(window):
                window.close()
                window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def act_on_popup(self, root, action):
        timer = QTimer(self.root)
        timer.setSingleShot(True)
        def perform():
            popup = getattr(root, "_choice_popup", None)
            try:
                self.assertIsInstance(popup, ChoiceMorphPopup)
                action(popup)
            except BaseException as exc:
                self.errors.append(exc)
            finally:
                if popup is not None and isValid(popup) and not popup._closed:
                    popup._finalizing = True
                    popup.close()
        timer.timeout.connect(perform)
        timer.start(50)
        return timer

    def check_errors(self):
        if self.errors:
            raise self.errors[0]

    def terminal_root_fixture(self):
        root = self.root
        for name in ("_terminal_root_caption", "_update_root_selector_display",
                     "_terminal_root_items", "_terminal_root_base", "update_root_selector",
                     "show_root_menu", "switch_root", "_on_theme_changed"):
            setattr(root, name, types.MethodType(production_method("main.py", "Main", name), root))
        root.tree = types.SimpleNamespace(base=os.path.join("C:\\", "terminal_fixture", "system_port"))
        root.tree2 = types.SimpleNamespace(base=os.path.join("C:\\", "terminal_fixture", "vendor_port"))
        root.tree_mirror = types.SimpleNamespace(base=root.tree.base)
        root.p1, root.p2 = root.tree.base, root.tree2.base
        root.active_root_id = root._linux_source_root_id = "tree1"
        root._wsl_ready = True
        root.terminal_sessions = {key: [object()] for key in ("tree1", "tree2", "linux")}
        root._refresh_wsl_terminal_status = lambda: None
        root.root_label = QLabel(root)
        root.tab_refreshes = []
        root.refresh_terminal_tabs = lambda: root.tab_refreshes.append(root.active_root_id)
        root.root_selector = ChoiceDropdown(parent=root, minimum_width=44)
        root.root_selector.setObjectName("terminalRootDropdown")
        root.root_selector.move(400, 300)
        root.root_selector.clicked.connect(root.show_root_menu)
        root.update_root_selector()
        self.app.processEvents()
        return root

    def test_terminal_menu_uses_shared_popup_and_keeps_compact_caption_through_morph(self):
        root = self.terminal_root_fixture()
        _th._animations_enabled = True
        ending = []

        def choose(popup):
            popup._animation.stop()
            popup._set_progress(1)
            self.app.processEvents()
            self.assertEqual([b.text() for b in popup.buttons], ["system_port", "vendor_port", "Linux"])
            self.assertIs(popup._current_button, popup.buttons[0])
            QTest.mouseClick(popup.buttons[1], Qt.LeftButton)
            self.assertEqual(root.root_selector.currentText(), "2")
            self.assertEqual(root.active_root_id, "tree1")  # Commit follows closing.
            morph = popup._closing_morph
            morph._group.pause()
            morph._advance(1)
            rect = popup._source_rect
            ratio = popup.devicePixelRatioF()
            ending.append(morph.grab().toImage().copy(QRect(rect.topLeft() * ratio, rect.size() * ratio)))
            popup._animation_finished()

        timer = self.act_on_popup(root, choose)
        with patch.object(root.root_selector.caption, "setText", wraps=root.root_selector.caption.setText) as text:
            QTest.mouseClick(root.root_selector, Qt.LeftButton)
            self.assertEqual([call.args[0] for call in text.call_args_list], ["2"])
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "tree2")
        self.assertEqual(root.root_label.text(), "vendor_port")
        self.assertEqual(root.tab_refreshes, ["tree2"])
        self.assertIsNone(root.root_selector.graphicsEffect())
        self.assertEqual(ending[0].convertToFormat(QImage.Format_ARGB32_Premultiplied),
                         root.root_selector.grab().toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied))

    def test_terminal_menu_escape_preserves_root_and_linux_keeps_source_root(self):
        root = self.terminal_root_fixture()
        root.switch_root("tree2")
        root.tab_refreshes.clear()
        timer = self.act_on_popup(root, lambda popup: QTest.keyClick(popup, Qt.Key_Escape))
        root.show_root_menu()
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "tree2")
        self.assertEqual(root.root_selector.currentText(), "2")
        self.assertEqual(root.tab_refreshes, [])
        timer = self.act_on_popup(root, lambda popup: QTest.mouseClick(popup.buttons[-1], Qt.LeftButton))
        root.show_root_menu()
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "linux")
        self.assertEqual(root._linux_source_root_id, "tree2")
        self.assertEqual(root.root_selector.currentText(), "Linux")
        self.assertEqual(root.root_label.text(), "vendor_port")

    def test_terminal_menu_reconciles_caption_when_root_disappears_or_session_fails(self):
        root = self.terminal_root_fixture()
        _th._animations_enabled = True

        def disappear(popup):
            popup._animation.stop()
            popup._set_progress(1)
            popup.buttons[1].click()
            root.tree2.base = root.tree.base

        timer = self.act_on_popup(root, disappear)
        root.show_root_menu()
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "tree1")
        self.assertEqual(root.root_selector.currentText(), "1")
        root.tree2.base = root.p2
        root.terminal_sessions["tree2"] = []
        root.create_terminal_session = lambda: None
        timer = self.act_on_popup(root, lambda popup: popup.buttons[1].click())
        root.show_root_menu()
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "tree1")
        self.assertEqual(root.root_selector.currentText(), "1")
        self.assertEqual(root.tab_refreshes, ["tree2", "tree1"])

    def test_terminal_selector_theme_refresh_uses_global_style(self):
        root = self.terminal_root_fixture()
        settings_button = ChoiceDropdown("English", root)
        with patch.object(_th, "FG_BLUE_MUTED", "#123456"):
            root._on_theme_changed()
            refresh_theme(settings_button)
            self.assertEqual(root.root_selector.styleSheet(), settings_button.styleSheet())
            self.assertEqual(root.root_selector.caption.styleSheet(), settings_button.caption.styleSheet())
            self.assertIn("#123456", root.root_selector.caption.styleSheet())
        self.assertEqual(root.root_selector.height(), settings_button.height())

    def test_terminal_duplicate_folder_names_morph_from_the_clicked_row(self):
        root = self.terminal_root_fixture()
        root.tree2.base = os.path.join("C:\\", "other_terminal_fixture", "system_port")
        _th._animations_enabled = True

        def choose(popup):
            popup._animation.stop()
            popup._set_progress(1)
            clicked = popup.buttons[1]
            center = clicked.mapTo(popup, clicked.rect().center()).toPointF()
            clicked.click()
            self.assertEqual(popup._closing_morph._caption_start, center)
            self.assertEqual(popup.buttons[0].text(), "system_port")

        timer = self.act_on_popup(root, choose)
        root.show_root_menu()
        timer.stop()
        self.check_errors()
        self.assertEqual(root.active_root_id, "tree2")
        self.assertEqual(root.root_selector.currentText(), "2")

    def test_modal_popup_uses_dialog_bounds_and_applies_native_keyboard_choice(self):
        self.dialog = QDialog(self.root)
        self.dialog.setModal(True)
        self.dialog.resize(310, 240)
        button = ChoiceDropdown("lz4", self.dialog, minimum_width=92)
        button.move(205, 195)
        self.dialog.show()
        self.app.processEvents()
        selected = []
        def choose(popup):
            self.assertIs(popup._root, self.dialog)
            self.assertIs(popup.parentWidget(), self.dialog)
            self.assertTrue(popup._upward)
            bounds = self.dialog.rect().translated(self.dialog.mapToGlobal(self.dialog.rect().topLeft()))
            self.assertTrue(bounds.contains(popup.geometry()))
            QTest.keyClick(popup.buttons[0], Qt.Key_End)
            QTest.keyClick(popup.buttons[-1], Qt.Key_Return)
        timer = self.act_on_popup(self.dialog, choose)
        show_choice_popup(button, ((v, v) for v in ("lz4", "lz4hc", "lzma", "deflate", "zstd")), "lz4", selected.append)
        timer.stop()
        self.check_errors()
        self.assertEqual(selected, ["zstd"])
        self.assertIsNone(self.dialog._choice_popup)
        self.assertIsNone(button.graphicsEffect())

    def test_app_adapter_cancellation_keeps_value_and_reuses_shared_popup(self):
        button = ChoiceDropdown("English", self.root)
        button.move(400, 300)
        button.show()
        selected = []
        timer = self.act_on_popup(self.root, lambda popup: QTest.keyClick(popup, Qt.Key_Escape))
        show = production_method("app.py", "AppUI", "_show_choice_popup")
        show(self.root, button, (("en", "English"), ("id", "Indonesia")), "en", selected.append)
        timer.stop()
        self.check_errors()
        self.assertFalse(selected)
        self.assertEqual(button.currentText(), "English")
        self.assertIsNone(self.root._choice_popup)
        self.assertIsNone(button.graphicsEffect())

    def test_closing_dialog_during_selection_morph_does_not_apply_a_late_value(self):
        self.dialog = QDialog(self.root)
        self.dialog.setModal(True)
        self.dialog.resize(310, 240)
        button = ChoiceDropdown("lz4", self.dialog, minimum_width=92)
        button.move(180, 100)
        self.dialog.show()
        self.app.processEvents()
        selected = []
        def choose_then_close(popup):
            self.assertIs(popup._root, self.dialog)
            self.assertIsNotNone(button.graphicsEffect())
            popup.buttons[-1].click()
            self.dialog.close()
        timer = self.act_on_popup(self.dialog, choose_then_close)
        with patch.object(_th, "_animations_enabled", True):
            show_choice_popup(button, (("lz4", "lz4"), ("zstd", "zstd")), "lz4", selected.append)
        timer.stop()
        self.check_errors()
        self.assertFalse(selected)
        self.assertIsNone(self.dialog._choice_popup)
        self.assertIsNone(button.graphicsEffect())

    def test_theme_and_long_caption_refresh_both_contexts_without_losing_qt_font(self):
        self.dialog = QDialog(self.root)
        app_button = ChoiceDropdown("Tree", self.root, minimum_width=100)
        main_button = ChoiceDropdown("lz4", self.dialog, minimum_width=92)
        font = app_button.caption.font().toString()
        with patch.object(_th, "FG_BLUE_MUTED", "#123456"):
            refresh_theme(self.root)
            self.assertEqual(app_button.styleSheet(), main_button.styleSheet())
            self.assertIn("#123456", app_button.caption.styleSheet())
            self.assertEqual(app_button.caption.styleSheet(), main_button.caption.styleSheet())
        app_button.setCaption("A much longer translated option")
        self.assertGreater(app_button.width(), 100)
        self.assertEqual(app_button.accessibleName(), app_button.currentText())
        self.assertEqual(app_button.caption.font().toString(), font)

    def test_main_settings_applies_selected_method_and_cancel_does_not_save(self):
        settings = {"erofs": {"default": False, "method": "lz4", "level": 6}}
        self.root.ensure_project_settings = lambda: settings
        self.root.sync_card = types.MethodType(production_method("main.py", "Main", "sync_card"), self.root)
        self.root._is_spam_enter_enabled = lambda: False
        open_settings = production_method("main.py", "Main", "open_project_settings")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "settings.json")
            self.root.project_settings_path = lambda: str(path)
            def exec_settings(dialog):
                self.dialog = dialog
                dialog.show()
                self.app.processEvents()
                button = dialog.findChild(ChoiceDropdown, "compressionMethodDropdown")
                self.assertIsNotNone(button)
                timer = self.act_on_popup(dialog, lambda popup: QTest.mouseClick(popup.buttons[-1], Qt.LeftButton))
                QTest.mouseClick(button, Qt.LeftButton)
                timer.stop()
                self.check_errors()
                self.assertEqual(button.currentText(), "zstd")
                apply_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("main_btn_apply"))
                QTest.mouseClick(apply_button, Qt.LeftButton)
                return QDialog.Accepted
            with patch.object(QDialog, "exec", exec_settings):
                open_settings(self.root)
            saved = path.read_text()
            self.assertEqual(json.loads(saved)["erofs"], {"default": False, "method": "zstd", "level": 6})
            def cancel_settings(dialog):
                self.dialog = dialog
                dialog.show()
                self.app.processEvents()
                button = dialog.findChild(ChoiceDropdown, "compressionMethodDropdown")
                timer = self.act_on_popup(dialog, lambda popup: QTest.keyClick(popup, Qt.Key_Escape))
                QTest.mouseClick(button, Qt.LeftButton)
                timer.stop()
                self.check_errors()
                self.assertEqual(button.currentText(), "lz4")
                dialog.reject()
                return QDialog.Rejected
            with patch.object(QDialog, "exec", cancel_settings):
                open_settings(self.root)
            self.assertEqual(path.read_text(), saved)

    def test_selected_qt_caption_survives_shared_morph_and_anchor_stays_hidden(self):
        _th._animations_enabled = True
        button = ChoiceDropdown("Old", self.root)
        button.move(400, 300)
        button.show()
        self.app.processEvents()
        def choose(popup):
            popup._animation.stop()
            popup._set_progress(1)
            QTest.mouseClick(popup.buttons[1], Qt.LeftButton)
            from app_ext.class_ButtonCardMorph import ButtonCardMorph
            morph = popup._closing_morph
            self.assertIsInstance(morph, ButtonCardMorph)
            self.assertTrue(morph._defer_source_handoff)
            morph._group.pause()
            for progress in (0.05, 0.2, 0.4, 0.8):
                morph._advance(progress)
                image, rect = morph._qt_caption()
                self.assertFalse(image.isNull())
                self.assertGreater(max(bytes(image.constBits())[3::4]), 0)
                self.assertEqual(button.graphicsEffect().opacity(), 0)
                self.assertEqual(morph._caption_opacity, 1)
        timer = self.act_on_popup(self.root, choose)
        show_choice_popup(button, (("Old", "Old"), ("New", "New")), "Old", button.setCaption)
        timer.stop()
        self.check_errors()
        self.assertEqual(button.currentText(), "New")
        self.assertIsNone(button.graphicsEffect())
        self.assertFalse(button.grab().isNull())

    def test_option_glyph_pixels_survive_hover_held_press_and_release(self):
        _th._animations_enabled = True
        button = ChoiceDropdown("English", self.root)
        button.move(400, 200)
        button.show()
        self.app.processEvents()

        def check(popup):
            popup._animation.stop()
            popup._set_progress(1)
            self.app.processEvents()
            item = popup.buttons[1]
            rect = QRect(item.mapTo(popup, QPoint()), item.size())

            def glyph_pixels():
                image = popup.grab().toImage().copy(QRect(
                    rect.topLeft() * popup.devicePixelRatioF(),
                    rect.size() * popup.devicePixelRatioF()))
                return sum(image.pixelColor(x, y).lightness() > 130
                           for y in range(image.height()) for x in range(image.width()))

            QTest.mouseMove(item, item.rect().center())
            baseline = glyph_pixels()
            self.assertGreater(baseline, 40)
            QTest.mousePress(item, Qt.LeftButton)
            QTest.qWait(130)
            self.assertGreater(glyph_pixels(), baseline * 0.5)
            # Inspect the released/hovered frame before choosing closes it.
            with patch.object(popup, "_pick"):
                QTest.mouseRelease(item, Qt.LeftButton)
                QTest.qWait(30)
                self.assertGreater(glyph_pixels(), baseline * 0.5)
                QTest.qWait(190)
                self.assertGreater(glyph_pixels(), baseline * 0.5)
            item.click()

        timer = self.act_on_popup(self.root, check)
        labels = {"en": "English", "id": "Indonesia"}
        show_choice_popup(button, labels.items(), "en", lambda code: button.setCaption(labels[code]))
        timer.stop()
        self.check_errors()
        self.assertEqual(button.currentText(), "Indonesia")

    def test_language_refresh_excludes_dropdown_and_commits_caption_before_fade(self):
        button = ChoiceDropdown("English", self.root)
        row = QWidget(self.root)
        row.setGeometry(20, 40, 500, 50)
        row_layout = QHBoxLayout(row)
        header = bind_text(QLabel(row), lambda: _ul.t("hdr_language"))
        row_layout.addWidget(header, 1)
        row_layout.addWidget(button)
        bind_text(button.caption, lambda: _ul.SUPPORTED[_ul._current_lang])
        row.show()
        button.show()
        mode = ChoiceDropdown("Tree", self.root)
        self.root._language_button = button
        self.root._project_mode_button = mode
        self.root._persistent_dialogs = []
        self.root._clear_page_content_reveal = lambda: None
        self.root._update_project_drive_layout = lambda: None
        self.root._refresh_settings_badge = lambda: None
        self.root._refresh_language_ui = types.MethodType(
            production_method("app.py", "AppUI", "_refresh_language_ui"), self.root)
        on_change = production_method("app.py", "AppUI", "_on_lang_changed")
        self.app.processEvents()
        # This binding must not even be evaluated by the page refresh.
        button.caption._language_bindings["setText"] = lambda: self.fail("language dropdown refreshed twice")
        with patch.object(_ul, "_current_lang", "id"), \
                patch.object(button, "setCaption", wraps=button.setCaption) as set_caption, \
                patch.object(button, "fit_width", wraps=button.fit_width) as fit_width, \
                patch.object(button.caption, "setText", wraps=button.caption.setText) as set_text:
            on_change(self.root)
            overlay = self.root._lang_xfade
            self.assertIsNotNone(overlay)
            self.assertEqual(button.currentText(), _ul.SUPPORTED["id"])
            rect = QRect(button.mapTo(self.root, QPoint()), button.size())
            self.assertFalse(overlay.mask().intersects(rect))
            self.assertTrue(overlay.mask().contains(self.root.rect().topLeft()))
            self.app.processEvents()
            self.assertEqual(header.text(), _ul.t("hdr_language"))
            set_caption.assert_called_once()
            fit_width.assert_called_once()
            set_text.assert_called_once()
            overlay.deleteLater()

    def test_close_handoff_keeps_caption_and_matches_live_button_pixels(self):
        _th._animations_enabled = True
        for choose in (False, True):
            with self.subTest(choose=choose):
                button = ChoiceDropdown("English", self.root)
                button.move(400, 200)
                button.show()
                self.app.processEvents()
                ending = []
                painted = []
                repaint = self.root.repaint

                def repaint_before_hide(*args):
                    popup = self.root._choice_popup
                    painted.append((popup.isVisible(), button.currentText(), button.graphicsEffect()))
                    repaint(*args)

                def close(popup):
                    popup._animation.stop()
                    popup._set_progress(1)
                    self.app.processEvents()
                    if choose:
                        QTest.mouseClick(popup.buttons[1], Qt.LeftButton)
                    else:
                        QTest.keyClick(popup, Qt.Key_Escape)
                    morph = popup._closing_morph
                    morph._group.pause()
                    morph._advance(1)
                    ratio = popup.devicePixelRatioF()
                    rect = popup._source_rect
                    self.assertTrue(morph.rect().contains(rect))
                    ending.append(morph.grab().toImage().copy(QRect(
                        rect.topLeft() * ratio, rect.size() * ratio)))
                    self.assertEqual(button.graphicsEffect().opacity(), 0)
                    popup._animation_finished()

                labels = {"en": "English", "id": "Indonesia"}
                with patch.object(self.root, "repaint", side_effect=repaint_before_hide), \
                        patch.object(button.caption, "setText", wraps=button.caption.setText) as set_text:
                    timer = self.act_on_popup(self.root, close)
                    show_choice_popup(button, labels.items(), "en", lambda code: button.setCaption(labels[code]))
                    timer.stop()
                    self.check_errors()
                    values = [call.args[0] for call in set_text.call_args_list]
                    self.assertEqual(values, ["Indonesia"] if choose else [])
                self.app.processEvents()
                live = button.grab().toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
                final = ending[0].convertToFormat(QImage.Format_ARGB32_Premultiplied)
                self.assertEqual(final, live)
                self.assertEqual(painted, [(True, "Indonesia" if choose else "English", None)])
                button.hide()
                button.deleteLater()

    def test_language_snapshot_uses_current_qt_pixels_and_exclusion_follows_layout(self):
        self.root.setStyleSheet("QWidget {background:#172431;}")
        button = ChoiceDropdown("Indonesia", self.root)
        button.move(300, 120)
        button.show()
        overlay = CrossfadeOverlay(self.root, exclude_widgets=(button,))
        # A screen grab would return unrelated/stale compositor pixels here.
        self.assertEqual(overlay._pix.toImage().pixelColor(5, 5), QColor("#172431"))
        before = QRect(button.pos(), button.size())
        button.move(380, 180)
        overlay.refresh_exclusions()
        self.assertTrue(overlay.mask().intersects(before))
        self.assertFalse(overlay.mask().intersects(QRect(button.pos(), button.size())))
        overlay.close()
        overlay.deleteLater()


if __name__ == "__main__":
    unittest.main()
