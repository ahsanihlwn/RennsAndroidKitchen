# SPDX-License-Identifier: MPL-2.0
"""One effect-level control using the app's shared dropdown/morph behavior."""
from PySide6.QtCore import Slot
from app_ext.class_ChoiceDropdown import ChoiceDropdown, show_choice_popup
from main_ext.class_ThemeUi import theme


EFFECT_LEVELS = (("low", "Low"), ("medium", "Medium"), ("high", "High"))


class EffectLevelDropdown(ChoiceDropdown):
    def __init__(self, parent=None):
        super().__init__(dict(EFFECT_LEVELS)[theme.effect_level], parent, minimum_width=98)
        self.setObjectName("effect_level_dropdown")
        self.clicked.connect(self._open_menu)
        self._effect_listener = self._sync_level
        theme.on_effect_level_change(self._effect_listener)
        callback = self._effect_listener
        self.destroyed.connect(lambda: theme.remove_effect_level_listener(callback))

    @Slot()
    def _open_menu(self):
        show_choice_popup(self, EFFECT_LEVELS, theme.effect_level, theme.set_effect_level)

    def _sync_level(self, level):
        self.setCaption(dict(EFFECT_LEVELS)[level])
