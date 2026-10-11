# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import re
import shlex
from typing import List, Tuple, Optional


class UnixBashConverter:


    # Common Unix env var mappings to Windows equivalents
    ENV_MAP = {
        "HOME": "%USERPROFILE%",
        "USER": "%USERNAME%",
        "USERNAME": "%USERNAME%",
        "PATH": "%PATH%",
        "PWD": "%CD%",
        "TMP": "%TEMP%",
        "TEMP": "%TEMP%",
    }

    # Regex for $VAR and ${VAR}
    ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    # Regex for Unix drive path patterns like /c/Users or /mnt/c/Users or c:/Users
    UNIX_DRIVE_RE = re.compile(r"^(?:/mnt)?/([a-zA-Z])(?:/(.*))?$")

    # Operators that chain commands
    CHAIN_OPERATORS = ["&&", "||", ";", "|"]

    @classmethod
    def convert(cls, bash_cmd: str) -> str:

        if not bash_cmd:
            return ""

        clean = bash_cmd.strip()
        if not clean:
            return ""

        # Ignore comments
        if clean.startswith("#"):
            return f"rem {clean[1:].strip()}"

        # Step 1: Pre-process global bash redirection constructs like /dev/null
        clean = cls._convert_redirections(clean)

        # Step 2: Split by command chain operators (&&, ||, ;, |) while respecting quotes
        segments = cls._split_chains(clean)

        # Step 3: Convert each segment individually
        converted_segments: List[str] = []
        for segment, op in segments:
            seg_trimmed = segment.strip()
            if not seg_trimmed:
                continue

            conv = cls._convert_single_command(seg_trimmed)
            if conv:
                # Semicolon ';' in bash becomes '&' in Windows cmd.exe
                win_op = "&" if op == ";" else op
                if win_op:
                    converted_segments.append(f"{conv} {win_op}")
                else:
                    converted_segments.append(conv)

        return " ".join(converted_segments)

    @classmethod
    def convert_script(cls, script_text: str) -> str:

        lines = script_text.splitlines()
        converted_lines = ["@echo off"]

        for line in lines:
            trimmed = line.strip()
            if not trimmed:
                converted_lines.append("")
                continue

            # Strip bash shebang
            if trimmed.startswith("#!"):
                continue

            # Comments
            if trimmed.startswith("#"):
                converted_lines.append(f"rem {trimmed[1:].strip()}")
                continue

            conv = cls.convert(trimmed)
            if conv:
                converted_lines.append(conv)

        return "\r\n".join(converted_lines)

    @classmethod
    def _convert_redirections(cls, text: str) -> str:

        # &> /dev/null -> > nul 2>&1
        text = re.sub(r"&>\s*/dev/null", "> nul 2>&1", text)
        # > /dev/null 2>&1 -> > nul 2>&1
        text = re.sub(r">\s*/dev/null\s+2>&1", "> nul 2>&1", text)
        # 2> /dev/null -> 2> nul
        text = re.sub(r"2>\s*/dev/null", "2> nul", text)
        # 1> /dev/null -> > nul
        text = re.sub(r"1?>\s*/dev/null", "> nul", text)
        # >> /dev/null -> >> nul
        text = re.sub(r">>\s*/dev/null", ">> nul", text)
        # < /dev/null -> < nul
        text = re.sub(r"<\s*/dev/null", "< nul", text)
        # General /dev/null replacement
        text = text.replace("/dev/null", "nul")
        text = text.replace("/dev/zero", "nul")
        text = text.replace("/dev/urandom", "nul")
        return text

    @classmethod
    def _split_chains(cls, cmd: str) -> List[Tuple[str, str]]:

        segments: List[Tuple[str, str]] = []
        n = len(cmd)
        i = 0
        current: List[str] = []
        in_single = False
        in_double = False
        paren_depth = 0

        while i < n:
            ch = cmd[i]

            # Toggle quotes
            if ch == "'" and not in_double:
                in_single = not in_single
                current.append(ch)
                i += 1
                continue
            elif ch == '"' and not in_single:
                in_double = not in_double
                current.append(ch)
                i += 1
                continue

            if in_single or in_double:
                current.append(ch)
                i += 1
                continue

            # Track parentheses
            if ch == '(':
                paren_depth += 1
                current.append(ch)
                i += 1
                continue
            elif ch == ')':
                paren_depth = max(0, paren_depth - 1)
                current.append(ch)
                i += 1
                continue

            # Check for 2-char operators outside quotes/parentheses
            if paren_depth == 0 and i + 1 < n:
                two = cmd[i:i + 2]
                if two in ("&&", "||"):
                    seg = "".join(current).strip()
                    segments.append((seg, two))
                    current.clear()
                    i += 2
                    continue

            # Check for 1-char operators outside quotes/parentheses
            if paren_depth == 0 and ch in (";", "|"):
                seg = "".join(current).strip()
                segments.append((seg, ch))
                current.clear()
                i += 1
                continue

            current.append(ch)
            i += 1

        if current:
            seg = "".join(current).strip()
            if seg:
                segments.append((seg, ""))

        return segments

    @classmethod
    def _convert_single_command(cls, raw: str) -> str:

        if not raw:
            return ""

        # Step A: Check for subshell ( cmd )
        if raw.startswith("(") and raw.endswith(")"):
            inner = raw[1:-1].strip()
            return f"({cls.convert(inner)})"

        # Step B: Parse tokens respecting quotes
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError:
            # Fallback if quotes are unbalanced
            tokens = raw.split()

        if not tokens:
            return ""

        # Step C: Extract inline environment variables (e.g. VAR1=val VAR2=val cmd arg1...)
        inline_vars: List[Tuple[str, str]] = []
        cmd_tokens: List[str] = []

        is_cmd_found = False
        var_assign_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

        for token in tokens:
            if not is_cmd_found:
                match = var_assign_re.match(token)
                if match and not token.startswith("-"):
                    k, v = match.group(1), match.group(2)
                    inline_vars.append((k, cls._convert_arg(v)))
                    continue
                else:
                    is_cmd_found = True
                    cmd_tokens.append(token)
            else:
                cmd_tokens.append(token)

        if not cmd_tokens:
            # Only variable assignments were provided (e.g. FOO=bar)
            var_cmds = [f'set "{k}={v}"' for k, v in inline_vars]
            return " & ".join(var_cmds)

        cmd_name = cmd_tokens[0]
        cmd_args = cmd_tokens[1:]

        # Step D: Command-specific translation
        translated_cmd = cls._translate_command_action(cmd_name, cmd_args)

        # Step E: Prepend inline variable assignments if any
        if inline_vars:
            var_prefixes = [f'set "{k}={v}"' for k, v in inline_vars]
            prefix_str = " && ".join(var_prefixes)
            return f"{prefix_str} && {translated_cmd}"

        return translated_cmd

    @classmethod
    def _translate_command_action(cls, cmd: str, args: List[str]) -> str:

        cmd_lower = cmd.lower()

        # 1. ls
        if cmd_lower == "ls":
            return cls._handle_ls(args)

        # 2. rm
        if cmd_lower == "rm":
            return cls._handle_rm(args)

        # 3. cp
        if cmd_lower == "cp":
            return cls._handle_cp(args)

        # 4. mv
        if cmd_lower == "mv":
            return cls._handle_mv(args)

        # 5. mkdir
        if cmd_lower == "mkdir":
            return cls._handle_mkdir(args)

        # 6. touch
        if cmd_lower == "touch":
            return cls._handle_touch(args)

        # 7. cat
        if cmd_lower == "cat":
            return cls._handle_cat(args)

        # 8. clear
        if cmd_lower == "clear":
            return "cls"

        # 9. pwd
        if cmd_lower == "pwd":
            return "cd"

        # 10. cd
        if cmd_lower == "cd":
            return cls._handle_cd(args)

        # 11. which
        if cmd_lower == "which":
            return cls._handle_which(args)

        # 12. grep
        if cmd_lower == "grep":
            return cls._handle_grep(args)

        # 13. find
        if cmd_lower == "find":
            return cls._handle_find(args)

        # 14. export
        if cmd_lower == "export":
            return cls._handle_export(args)

        # 15. unset
        if cmd_lower == "unset":
            return cls._handle_unset(args)

        # 16. env / printenv
        if cmd_lower in ("env", "printenv"):
            return "set"

        # 17. whoami
        if cmd_lower == "whoami":
            return "whoami"

        # 18. sleep
        if cmd_lower == "sleep":
            return cls._handle_sleep(args)

        # 19. uname
        if cmd_lower == "uname":
            return "ver"

        # 20. ps
        if cmd_lower == "ps":
            return "tasklist"

        # 21. kill
        if cmd_lower == "kill":
            return cls._handle_kill(args)

        # 22. killall
        if cmd_lower == "killall":
            return cls._handle_killall(args)

        # 23. chmod / chown
        if cmd_lower in ("chmod", "chown"):
            # Harmless comment in Windows so script chaining doesn't break
            arg_str = " ".join(args)
            return f"rem {cmd} {arg_str}"

        # 24. source or .
        if cmd_lower in ("source", "."):
            target = cls._convert_arg(args[0]) if args else ""
            return f'call "{target}"' if target else "rem source"

        # 25. true / false
        if cmd_lower == "true":
            return "(call )"
        if cmd_lower == "false":
            return "(call)"

        # 26. head
        if cmd_lower == "head":
            return cls._handle_head(args)

        # 27. tail
        if cmd_lower == "tail":
            return cls._handle_tail(args)

        # 28. diff
        if cmd_lower == "diff":
            conv_args = [cls._quote_path_if_needed(cls._convert_arg(a)) for a in args]
            return f"fc {' '.join(conv_args)}"

        # 29. alias
        if cmd_lower == "alias":
            if args:
                alias_str = " ".join(args).replace("'", "")
                return f"doskey {alias_str}"
            return "doskey /macros"

        # 30. echo
        if cmd_lower == "echo":
            conv_args = [cls._convert_arg(a) for a in args]
            return f"echo {' '.join(conv_args)}"

        # 31. Default / native executable pass-through
        # (rak, adb, fastboot, git, python, node, curl, tar, custom binaries)
        conv_cmd = cls._convert_arg(cmd)
        conv_args = [cls._convert_arg(a) for a in args]

        quoted_args = []
        for a in conv_args:
            quoted_args.append(cls._quote_path_if_needed(a))

        if quoted_args:
            return f"{conv_cmd} {' '.join(quoted_args)}"
        return conv_cmd


    @classmethod
    def _handle_ls(cls, args: List[str]) -> str:
        flags = ""
        paths = []
        for a in args:
            if a.startswith("-") and not a.startswith("--"):
                flags += a[1:]
            elif a.startswith("--"):
                if "all" in a:
                    flags += "a"
            else:
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        target = " ".join(paths) if paths else ""
        has_l = "l" in flags
        has_a = "a" in flags
        has_r = "r" in flags or "R" in flags
        has_1 = "1" in flags

        switches = []
        if has_a:
            switches.append("/a")
        if has_r:
            switches.append("/s")

        if has_l:
            switches_str = f" {' '.join(switches)}" if switches else ""
            return f"dir{switches_str} {target}".strip()
        elif has_1 or not switches:
            switches.append("/b")
            switches_str = f" {' '.join(switches)}"
            return f"dir{switches_str} {target}".strip()
        else:
            switches_str = f" {' '.join(switches)}"
            return f"dir{switches_str} {target}".strip()

    @classmethod
    def _handle_rm(cls, args: List[str]) -> str:
        is_recursive = False
        paths = []
        for a in args:
            if a.startswith("-"):
                if "r" in a or "R" in a:
                    is_recursive = True
            else:
                paths.append(cls._convert_arg(a))

        if not paths:
            return "rem rm (no path specified)"

        commands = []
        for p in paths:
            win_p = cls._quote_path_if_needed(p)
            raw_p = p.strip('"\'')
            if is_recursive:
                if "*" in raw_p or "?" in raw_p:
                    commands.append(f"del /f /q {win_p}")
                else:
                    # Windows cmd robust check: delete dir if dir, delete file if file
                    commands.append(
                        f'if exist "{raw_p}\\*" (rmdir /s /q "{raw_p}") else if exist "{raw_p}" (del /f /q "{raw_p}")'
                    )
            else:
                commands.append(f"del /f /q {win_p}")

        return " & ".join(commands)

    @classmethod
    def _handle_cp(cls, args: List[str]) -> str:
        is_recursive = False
        paths = []
        for a in args:
            if a.startswith("-"):
                if "r" in a or "R" in a or "a" in a:
                    is_recursive = True
            else:
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        if len(paths) < 2:
            return f"copy /y {' '.join(paths)}"

        src = paths[0]
        dst = paths[1]

        if is_recursive:
            return f"xcopy /e /i /h /y {src} {dst}"
        return f"copy /y {src} {dst}"

    @classmethod
    def _handle_mv(cls, args: List[str]) -> str:
        paths = []
        for a in args:
            if not a.startswith("-"):
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        if len(paths) >= 2:
            return f"move /y {paths[0]} {paths[1]}"
        return f"move /y {' '.join(paths)}"

    @classmethod
    def _handle_mkdir(cls, args: List[str]) -> str:
        paths = []
        for a in args:
            if not a.startswith("-"):
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        # Windows mkdir automatically creates intermediate directories like mkdir -p
        return f"mkdir {' '.join(paths)}" if paths else "mkdir"

    @classmethod
    def _handle_touch(cls, args: List[str]) -> str:
        paths = []
        for a in args:
            if not a.startswith("-"):
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        if not paths:
            return "rem touch"

        # In Windows cmd: type nul >> "file" creates an empty file if it doesn't exist
        cmds = [f"type nul >> {p}" for p in paths]
        return " & ".join(cmds)

    @classmethod
    def _handle_cat(cls, args: List[str]) -> str:
        paths = []
        for a in args:
            if not a.startswith("-"):
                paths.append(cls._quote_path_if_needed(cls._convert_arg(a)))

        return f"type {' '.join(paths)}" if paths else "type"

    @classmethod
    def _handle_cd(cls, args: List[str]) -> str:
        if not args:
            return "cd /d %USERPROFILE%"
        target = cls._quote_path_if_needed(cls._convert_arg(args[0]))
        return f"cd /d {target}"

    @classmethod
    def _handle_which(cls, args: List[str]) -> str:
        if not args:
            return "where"
        return f"where {cls._convert_arg(args[0])}"

    @classmethod
    def _handle_grep(cls, args: List[str]) -> str:
        switches = []
        pattern = ""
        files = []

        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("-"):
                if "i" in a:
                    switches.append("/i")
                if "r" in a or "R" in a:
                    switches.append("/s")
                if "v" in a:
                    switches.append("/v")
                if "n" in a:
                    switches.append("/n")
                if "c" in a:
                    switches.append("/c")
                if "m" in a or "l" in a:
                    switches.append("/m")
            else:
                if not pattern:
                    pattern = a
                else:
                    files.append(cls._quote_path_if_needed(cls._convert_arg(a)))
            i += 1

        switch_str = f" {' '.join(switches)}" if switches else ""
        file_str = f" {' '.join(files)}" if files else " *"
        return f'findstr{switch_str} "{pattern}"{file_str}'

    @classmethod
    def _handle_find(cls, args: List[str]) -> str:
        # Common: find . -name "*.apk" or find path -name pattern
        path = "."
        pattern = "*"
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-name", "-iname") and i + 1 < len(args):
                pattern = args[i + 1]
                i += 1
            elif not a.startswith("-"):
                path = cls._convert_arg(a)
            i += 1

        conv_path = cls._quote_path_if_needed(path)
        return f"dir /s /b {conv_path}\\{pattern}"

    @classmethod
    def _handle_export(cls, args: List[str]) -> str:
        if not args:
            return "set"
        target = " ".join(args)
        if "=" in target:
            var, val = target.split("=", 1)
            var = var.strip()
            val = cls._convert_arg(val.strip().strip("'\""))
            return f'set "{var}={val}"'
        else:
            return f"set {target.strip()}"

    @classmethod
    def _handle_unset(cls, args: List[str]) -> str:
        if not args:
            return "rem unset"
        var = args[0].strip()
        return f'set "{var}="'

    @classmethod
    def _handle_sleep(cls, args: List[str]) -> str:
        sec = "1"
        if args:
            try:
                # Remove s suffix if given (e.g. 2s)
                raw_sec = args[0].rstrip("s")
                sec = str(int(float(raw_sec)))
            except ValueError:
                sec = "1"
        return f"timeout /t {sec} /nobreak >nul"

    @classmethod
    def _handle_kill(cls, args: List[str]) -> str:
        force = False
        pid = ""
        for a in args:
            if a in ("-9", "-KILL", "-f"):
                force = True
            elif not a.startswith("-"):
                pid = a

        if not pid:
            return "taskkill"
        force_flag = "/f " if force else ""
        return f"taskkill {force_flag}/pid {pid}"

    @classmethod
    def _handle_killall(cls, args: List[str]) -> str:
        name = args[0] if args else ""
        if not name:
            return "taskkill"
        if not name.lower().endswith(".exe"):
            name = f"{name}.exe"
        return f"taskkill /f /im {name}"

    @classmethod
    def _handle_head(cls, args: List[str]) -> str:
        n = 10
        file_path = ""
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-n" and i + 1 < len(args):
                try:
                    n = int(args[i + 1])
                except ValueError:
                    pass
                i += 1
            elif not a.startswith("-"):
                file_path = cls._convert_arg(a)
            i += 1

        if not file_path:
            return "rem head"
        return f'powershell -NoProfile -Command "Get-Content -Path \'{file_path}\' -Head {n}"'

    @classmethod
    def _handle_tail(cls, args: List[str]) -> str:
        n = 10
        file_path = ""
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-n" and i + 1 < len(args):
                try:
                    n = int(args[i + 1])
                except ValueError:
                    pass
                i += 1
            elif not a.startswith("-"):
                file_path = cls._convert_arg(a)
            i += 1

        if not file_path:
            return "rem tail"
        return f'powershell -NoProfile -Command "Get-Content -Path \'{file_path}\' -Tail {n}"'


    @classmethod
    def _convert_arg(cls, arg: str) -> str:

        if not arg:
            return ""

        # Step 1: Expand environment variables ($VAR / ${VAR})
        result = cls.convert_env_vars(arg)

        # Step 2: Convert Unix path format
        result = cls.convert_path(result)

        return result

    @classmethod
    def convert_env_vars(cls, text: str) -> str:

        def _replace_var(match: re.Match) -> str:
            var_name = match.group(1) or match.group(2)
            upper_name = var_name.upper()
            if upper_name in cls.ENV_MAP:
                return cls.ENV_MAP[upper_name]
            return f"%{var_name}%"

        return cls.ENV_VAR_RE.sub(_replace_var, text)

    @classmethod
    def convert_path(cls, path_str: str) -> str:

        if not path_str:
            return ""

        # Ignore URLs
        if path_str.startswith(("http://", "https://", "ftp://")):
            return path_str

        # Tilde ~
        if path_str == "~":
            return "%USERPROFILE%"
        if path_str.startswith("~/"):
            path_str = "%USERPROFILE%/" + path_str[2:]

        # Unix drive paths: /c/Users or /mnt/c/Users
        drive_match = cls.UNIX_DRIVE_RE.match(path_str)
        if drive_match:
            drive_letter = drive_match.group(1).upper()
            subpath = drive_match.group(2) or ""
            if subpath:
                subpath = subpath.replace("/", "\\")
                return f"{drive_letter}:\\{subpath}"
            return f"{drive_letter}:\\"

        # Windows drive with forward slashes: c:/Users
        if len(path_str) >= 2 and path_str[1] == ":" and "/" in path_str:
            return path_str.replace("/", "\\")

        # Convert forward slashes to backslashes if this looks like a filesystem path
        if "/" in path_str and not path_str.startswith("-"):
            # Avoid breaking options that might use slash (e.g. /?)
            path_str = path_str.replace("/", "\\")

        # Strip trailing backslash for directories unless it's a drive root like C:\
        if len(path_str) > 1 and path_str.endswith("\\") and not (len(path_str) == 3 and path_str[1:3] == ":\\"):
            path_str = path_str.rstrip("\\")

        return path_str

    @classmethod
    def _quote_path_if_needed(cls, path_str: str) -> str:

        if not path_str:
            return '""'
        if path_str.startswith('"') and path_str.endswith('"'):
            return path_str
        # Never quote redirection operators or special target nul
        if path_str in (">", ">>", "<", "2>", "1>", "2>&1", "1>&2", "nul"):
            return path_str
        if re.match(r"^[0-9]*>&[0-9]*$", path_str):
            return path_str
        if any(c in path_str for c in (" ", "&", "^", "(", ")")):
            return f'"{path_str}"'
        return path_str
