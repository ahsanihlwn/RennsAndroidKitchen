# SPDX-License-Identifier: MPL-2.0
import unittest
from unittest.mock import Mock

from core.terminal_pty import PtyProcessManager, PtySignals, TerminalBridge


class TerminalGeometryTests(unittest.TestCase):
    def manager(self):
        manager = PtyProcessManager()
        manager._running = True
        manager.pty = Mock()
        manager._last_cols, manager._last_rows = 80, 24
        return manager

    def test_narrow_short_panes_keep_the_exact_frontend_grid(self):
        manager = self.manager()
        manager.resize(39, 7)
        manager.pty.set_size.assert_called_once_with(39, 7)
        manager.resize(39, 7)
        manager.pty.set_size.assert_called_once()

    def test_empty_geometry_still_uses_a_valid_pty_grid(self):
        manager = self.manager()
        manager.resize(0, 0)
        manager.pty.set_size.assert_called_once_with(2, 1)

    def test_failed_resize_can_retry_the_same_geometry(self):
        manager = self.manager()
        manager.pty.set_size.side_effect = [RuntimeError("resize failed"), None]
        manager.resize(39, 7)
        self.assertEqual((manager._last_cols, manager._last_rows), (80, 24))
        manager.resize(39, 7)
        self.assertEqual((manager._last_cols, manager._last_rows), (39, 7))
        self.assertEqual(manager.pty.set_size.call_count, 2)

    def test_bridge_exposes_the_backend_and_keeps_initial_geometry(self):
        manager = self.manager()
        manager.signals = PtySignals()
        manager.windows_pty = {"backend": "conpty", "buildNumber": 26100}
        bridge = TerminalBridge(manager)
        self.assertEqual(bridge.ptyOptions, manager.windows_pty)
        bridge.onTerminalReady(39, 7)
        self.assertTrue(bridge.is_ready)
        manager.pty.set_size.assert_called_once_with(39, 7)


if __name__ == "__main__":
    unittest.main()
