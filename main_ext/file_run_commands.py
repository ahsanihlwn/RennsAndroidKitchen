# SPDX-License-Identifier: MPL-2.0
import os


def _quote(path: str) -> str:
    return '"' + path.replace('"', r'\"') + '"'


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
    return os.path.splitext(filepath or "")[1].lower() in RUNNABLE_EXT_COMMANDS


def build_run_command(filepath: str) -> str | None:
    ext = os.path.splitext(filepath or "")[1].lower()
    prefix = RUNNABLE_EXT_COMMANDS.get(ext)
    if prefix is None:
        return None
    return " ".join([*prefix, _quote(os.path.abspath(filepath))]).strip()
