# SPDX-License-Identifier: MPL-2.0
import os
import shlex
import shutil
import sys


def _quote(path: str) -> str:
    if sys.platform == "win32":
        return '"' + path.replace('"', r'\"') + '"'
    return shlex.quote(path)


_WINDOWS_ONLY_EXTS = frozenset({".bat", ".cmd", ".vbs", ".ahk"})


def _python_cmd() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("python",)
    return ("python3",) if shutil.which("python3") else ("python",)


def _ps1_cmd() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("powershell", "-ExecutionPolicy", "Bypass", "-File")
    return ("pwsh", "-File")


RUNNABLE_EXT_COMMANDS = {
    ".py": ("python",),
    ".pyw": ("python",),
    ".js": ("node",),
    ".mjs": ("node",),
    ".cjs": ("node",),
    ".ts": ("npx", "ts-node"),
    ".php": ("php",),
    ".php3": ("php",),
    ".php4": ("php",),
    ".php5": ("php",),
    ".phtml": ("php",),
    ".rb": ("ruby",),
    ".pl": ("perl",),
    ".pm": ("perl",),
    ".lua": ("lua",),
    ".r": ("Rscript",),
    ".jl": ("julia",),
    ".sh": ("bash",),
    ".bash": ("bash",),
    ".zsh": ("zsh",),
    ".fish": ("fish",),
    ".ps1": ("powershell", "-ExecutionPolicy", "Bypass", "-File"),
    ".bat": (),
    ".cmd": (),
    ".vbs": ("cscript", "//nologo"),
    ".ahk": ("autohotkey",),
    ".dart": ("dart",),
    ".groovy": ("groovy",),
    ".scala": ("scala",),
    ".clj": ("clojure",),
    ".cljs": ("clojure",),
    ".exs": ("elixir",),
    ".erl": ("escript",),
    ".hs": ("runhaskell",),
    ".fsx": ("dotnet", "fsi"),
    ".ml": ("ocaml",),
    ".scm": ("guile",),
    ".lisp": ("sbcl", "--script"),
    ".cl": ("sbcl", "--script"),
    ".tcl": ("tclsh",),
    ".swift": ("swift",),
    ".kts": ("kotlinc", "-script"),
}


def is_runnable_file(filepath: str) -> bool:
    ext = os.path.splitext(filepath or "")[1].lower()
    if ext not in RUNNABLE_EXT_COMMANDS:
        return False
    if sys.platform != "win32" and ext in _WINDOWS_ONLY_EXTS:
        return False
    return True


def build_run_command(filepath: str) -> str | None:
    ext = os.path.splitext(filepath or "")[1].lower()
    if ext not in RUNNABLE_EXT_COMMANDS:
        return None
    if sys.platform != "win32" and ext in _WINDOWS_ONLY_EXTS:
        return None
    if ext in (".py", ".pyw"):
        prefix = _python_cmd()
    elif ext == ".ps1":
        prefix = _ps1_cmd()
    else:
        prefix = RUNNABLE_EXT_COMMANDS[ext]
    return " ".join([*prefix, _quote(os.path.abspath(filepath))]).strip()
