# SPDX-License-Identifier: MPL-2.0
"""Qt Quick integration for Quill's Python-hosted Rust editor."""
import weakref

from quill_native.editor import NativeEditor as QuillEditor


class NativeEditor(QuillEditor):
    def __init__(self, parent=None):
        super().__init__(parent)
        # PySide can lose bound-method connections made during QML type
        # construction. Route them through plain callables so timers and
        # geometry/focus notifications continue to reach the Python host.
        editor = weakref.ref(self)

        def reconnect(signal, method):
            signal.disconnect()

            def invoke():
                instance = editor()
                if instance is not None:
                    getattr(instance, method)()

            signal.connect(invoke)

        for timer, method in (
            (self._prepare_timer, "_prepare"),
            (self._lex_timer, "_prepare"),
            (self._smooth_timer, "_animate_smooth_scroll"),
            (self._blink, "_blink_caret"),
            (self._drag_timer, "_autoscroll"),
            (self._minimap_timer, "_rebuild_minimap"),
        ):
            reconnect(timer.timeout, method)
        reconnect(self.activeFocusChanged, "_focus_changed")
        reconnect(self.widthChanged, "_on_geometry_changed")
        reconnect(self.heightChanged, "_on_geometry_changed")
