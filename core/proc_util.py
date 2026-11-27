# SPDX-License-Identifier: MPL-2.0
import os
import signal
import subprocess
import sys

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
            return

        # POSIX (macOS & Linux): prefer psutil to terminate the full descendant tree.
        try:
            import psutil

            proc = psutil.Process(int(pid))
            children = proc.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            return
        except ImportError:
            pass
        except Exception:
            pass

        # Fallback without psutil: try process group if it differs from ours, then direct PID.
        try:
            pgid = os.getpgid(int(pid))
            if pgid != os.getpgrp():
                os.killpg(pgid, signal.SIGKILL)
                return
        except (OSError, AttributeError):
            pass
        os.kill(int(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as e:
        print(f"[PROC_UTIL] kill_process_tree({pid}) failed: {e}")
