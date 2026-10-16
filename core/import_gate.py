# SPDX-License-Identifier: MPL-2.0
"""Serialize imports during startup to avoid a CPython 3.14/PySide6 6.10.1 GC crash.

The observed 0xC0000005 occurs during concurrent first imports, before Python
exception hooks can run. A re-entrant lock prevents overlapping import
machinery; release() restores the original hook after startup.
"""

from __future__ import annotations

import builtins
import threading

_LOCK = threading.RLock()
_ORIG_IMPORT = builtins.__import__
_STATE = {"installed": False}


def _guarded_import(*args, **kwargs):
    # Re-entrant: a module importing another module keeps the lock, so nested
    # imports on the same thread never deadlock.
    with _LOCK:
        return _ORIG_IMPORT(*args, **kwargs)


def install() -> None:
    """Route every ``import`` through the shared lock (idempotent)."""
    if _STATE["installed"]:
        return
    builtins.__import__ = _guarded_import
    _STATE["installed"] = True


def release() -> None:
    """Restore the original ``__import__`` once startup is over (idempotent).

    Only restores the symbol if the gate is still the installed hook, so a
    later patch by some other library is never clobbered.
    """
    if not _STATE["installed"]:
        return
    _STATE["installed"] = False
    if builtins.__import__ is _guarded_import:
        builtins.__import__ = _ORIG_IMPORT


def is_installed() -> bool:
    return _STATE["installed"]
