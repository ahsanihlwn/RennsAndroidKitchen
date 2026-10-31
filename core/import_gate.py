# SPDX-License-Identifier: MPL-2.0
"""Serialize imports during startup to avoid a CPython 3.14/PySide6 6.10.1 GC crash.

The observed 0xC0000005 occurs during concurrent first imports, before Python
exception hooks can run. A re-entrant lock serializes first imports;
fully initialized cached imports remain available to the GUI.
release() restores the original hook after startup.
"""

from __future__ import annotations

import builtins
import sys
import threading

_LOCK = threading.RLock()
_ORIG_IMPORT = builtins.__import__
_STATE = {"installed": False}


def _already_loaded(name, fromlist, level):
    if level:
        return False
    module = sys.modules.get(name)
    if module is None:
        return False
    # A cached import is safe only after all package initializers have finished.
    parts = name.split(".")
    for index in range(1, len(parts)+1):
        parent = sys.modules.get(".".join(parts[:index]))
        if parent is None or getattr(getattr(parent, "__spec__", None), "_initializing", False):
            return False
    # Missing from-items can trigger new imports. Avoid module __getattr__ here:
    # several application packages use it for lazy first imports.
    return not fromlist or all(item != "*" and item in vars(module) for item in fromlist)


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    # Qt invokes Python image providers/properties on the GUI thread while the
    # editor imports in the background. Waiting on the gate even for cached Qt
    # imports froze that GUI thread for the duration of an unrelated import.
    if _already_loaded(name, fromlist, level):
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)
    # Re-entrant: a module importing another module keeps the lock, so nested
    # imports on the same thread never deadlock.
    with _LOCK:
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)


def install() -> None:
    """Serialize first imports without blocking cached GUI imports (idempotent)."""
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
