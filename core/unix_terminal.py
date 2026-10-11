# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtWidgets import QWidget
from core.terminal import Terminal, normalize_terminal_command
from core.unix_bash_converter import UnixBashConverter


class UnixBashTerminal(Terminal):


    def __init__(
        self,
        cwd: Optional[str] = None,
        bin_root: Optional[str] = None,
        spam_enter: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(cwd=cwd, bin_root=bin_root, spam_enter=spam_enter, bash_mode=True, parent=parent)
        self.converter = UnixBashConverter
        self.bash_mode = True

    def convert_bash_to_windows(self, bash_cmd: str) -> str:

        return self.converter.convert(bash_cmd)

    def run_bash_command(self, raw_bash_cmd: str, retries: int = 50) -> bool:

        if not raw_bash_cmd:
            return False

        # 1. Convert bash command into Windows native format
        win_cmd = self.converter.convert(raw_bash_cmd)
        if not win_cmd:
            return False

        # 2. Record first command name if needed
        self._record_first_command(raw_bash_cmd)

        # 3. Resolve any package.json binary path (e.g. adb, fastboot, rak)
        final_cmd = normalize_terminal_command(win_cmd, self.bin_root)

        # 4. Dispatch to PTY bridge
        self.bridge.run_command(final_cmd)
        return True

    def run_bash_script(self, script_text: str) -> bool:

        if not script_text:
            return False

        lines = script_text.splitlines()
        commands_to_run = []
        for line in lines:
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            win_line = self.converter.convert(trimmed)
            if win_line:
                final_line = normalize_terminal_command(win_line, self.bin_root)
                commands_to_run.append(final_line)

        if not commands_to_run:
            return False

        # Join commands with & so they execute sequentially in the active session
        batched = " & ".join(commands_to_run)
        self.bridge.run_command(batched)
        return True
