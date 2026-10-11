# SPDX-License-Identifier: MPL-2.0
import sys
import subprocess

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def kill_process_tree(pid):

    if not pid:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            import signal
            import os
            os.kill(pid, signal.SIGKILL)
    except Exception as e:
        print(f"[PROC_UTIL] kill_process_tree({pid}) failed: {e}")
