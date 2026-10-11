# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import re

print("[CORE STAGE] VDEV INITMODEL")

_PROP_RE = re.compile(r"\$\{([^}]+)\}")

# Tiga trigger yang diantre init sendiri di SecondStageMain(), berurutan.
BUILTIN_TRIGGERS = ("early-init", "init", "late-init")

_MAX_DEPTH = 24


class Service:
    __slots__ = ("name", "path", "args", "origin", "options", "klass",
                 "started_by", "seclabel")

    def __init__(self, name, path, args, origin):
        self.name = name
        self.path = path
        self.args = args
        self.origin = origin
        self.options = set()
        self.klass = ""          # kelas pertama (init mengizinkan banyak)
        self.started_by = set()  # trigger yang menyebabkan dia start


        self.seclabel = ""

    @property
    def critical(self):
        return "critical" in self.options

    @property
    def disabled(self):
        return "disabled" in self.options

    @property
    def oneshot(self):
        return "oneshot" in self.options

    def __repr__(self):
        return f"<Service {self.name} {self.path}>"


class Action:
    __slots__ = ("trigger", "commands", "origin")

    def __init__(self, trigger, origin):
        self.trigger = trigger          # teks mentah sesudah "on "
        self.commands = []              # [(nama, [arg, ...])]
        self.origin = origin

    @property
    def event(self):

        t = self.trigger.strip()
        if t.startswith("property:"):
            return ""
        # "late-fs && property:ro.x=1" -> event-nya tetap late-fs
        head = t.split("&&", 1)[0].strip()
        return "" if head.startswith("property:") else head

    @property
    def conditions(self):

        return [p.strip()[len("property:"):]
                for p in self.trigger.split("&&")
                if p.strip().startswith("property:")]

    def __repr__(self):
        return f"<Action on {self.trigger} ({len(self.commands)} cmd)>"


def expand(text, props):

    unknown = set()

    def sub(m):
        expr = m.group(1)
        default = None
        if ":-" in expr:
            expr, default = expr.split(":-", 1)
        val = props.get(expr.strip())
        if val:
            return val
        if default is not None:
            return default
        unknown.add(expr.strip())
        return m.group(0)

    return _PROP_RE.sub(sub, text), unknown


def parse_rc(text, origin, props):

    services, actions, imports = [], [], []
    unknown = set()
    current_service = None
    current_action = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        starts_block = not line[:1].isspace()

        if starts_block and stripped.startswith("import "):
            target, unk = expand(stripped[7:].strip(), props)
            unknown |= unk
            imports.append((target, unk))
            current_service = current_action = None
            continue

        if starts_block and stripped.startswith("service "):
            parts = stripped.split()
            current_action = None
            current_service = None
            if len(parts) >= 3:
                path, unk = expand(parts[2], props)
                unknown |= unk
                current_service = Service(parts[1], path, parts[3:], origin)
                services.append(current_service)
            continue

        if starts_block and stripped.startswith("on "):
            current_service = None
            trigger, unk = expand(stripped[3:].strip(), props)
            unknown |= unk
            current_action = Action(trigger, origin)
            actions.append(current_action)
            continue

        if starts_block:
            # Baris tak berindentasi yang bukan pembuka blok mengakhiri blok.
            current_service = current_action = None
            continue

        # ── isi blok ──
        parts = stripped.split()
        if not parts:
            continue
        if current_service is not None:
            current_service.options.add(parts[0])
            if parts[0] == "class" and len(parts) > 1 and not current_service.klass:
                current_service.klass = parts[1]
            elif parts[0] == "seclabel" and len(parts) > 1:
                current_service.seclabel = parts[1]
        elif current_action is not None:
            expanded, unk = expand(stripped, props)
            unknown |= unk
            bits = expanded.split()
            current_action.commands.append((bits[0], bits[1:]))

    return services, actions, imports, unknown


class InitModel:


    def __init__(self, props):
        self.props = props
        self.services = []
        self.actions = []
        self.missing_imports = []
        self.unknown_props = set()
        self.files = []
        # hasil penelusuran
        self.executed = []          # [(trigger, Action)] urut jalan
        self.conditional = []       # [(trigger, Action)] tidak pasti jalan
        self.mount_all = []         # [(fstab_arg, trigger)]
        self.class_starts = []      # [(kelas, trigger)]
        self.explicit_starts = []   # [(nama_service, trigger)]

    # ── penelusuran antrean ──
    def run_triggers(self):

        by_event = {}
        for a in self.actions:
            ev = a.event
            if ev:
                by_event.setdefault(ev, []).append(a)

        seen = set()
        queue = list(BUILTIN_TRIGGERS)
        depth = 0
        while queue and depth < _MAX_DEPTH * 64:
            depth += 1
            event = queue.pop(0)
            if event in seen:
                continue
            seen.add(event)
            for action in by_event.get(event, []):
                certain = self._conditions_hold(action)
                (self.executed if certain else self.conditional).append(
                    (event, action))
                if not certain:
                    continue
                for name, args in action.commands:
                    if name == "trigger" and args:
                        queue.append(args[0])
                    elif name == "mount_all" and args:
                        self.mount_all.append((args[0], event))
                    elif name == "class_start" and args:
                        self.class_starts.append((args[0], event))
                    elif name in ("start", "exec_start") and args:
                        self.explicit_starts.append((args[0], event))
        self._converge_vold_decrypt()
        self._mark_started()
        return self.executed

    def _converge_vold_decrypt(self):

        groups = {}
        for a in self.actions:
            ev = a.trigger.strip()
            if ev == "nonencrypted" or ev.startswith("property:vold.decrypt="):
                # Beberapa berkas .rc boleh menambah ke trigger yang sama —
                # semuanya jalan, jadi digabung, bukan cuma yang terakhir.
                bucket = groups.setdefault(ev, set())
                bucket.update((name, tuple(args)) for name, args in a.commands)
        if not groups:
            return

        data_flags = self._data_fstab_flags()
        is_fbe = any("fileencryption=" in f for f in data_flags)
        is_legacy_fde = (not is_fbe and
                        any(f.startswith(("forceencrypt=", "encryptable="))
                            for f in data_flags))

        if is_legacy_fde:
            terminal = [v for k, v in groups.items() if k != "nonencrypted"
                       and "shutdown" not in k]
            basis = "fstab /data uses legacy full-disk encryption"
        else:
            # FBE, atau tidak terenkripsi sama sekali — dua-duanya lewat
            # jalur "nonencrypted".
            terminal = [groups["nonencrypted"]] if "nonencrypted" in groups else []
            basis = ("fstab /data uses File-Based Encryption (fileencryption=)"
                    if is_fbe else "fstab /data has no encryption flag")

        if not terminal:
            return
        common = terminal[0]
        for t in terminal[1:]:
            common &= t
        if not common:
            return

        self.vold_basis = basis
        synth = Action(f"vold-decrypt ({basis})", "vold-convergence")
        synth.commands = list(common)
        self.executed.append(("vold-decrypt", synth))
        for name, args in common:
            if name == "class_start" and args:
                self.class_starts.append((args[0], "vold-decrypt"))
            elif name in ("start", "exec_start") and args:
                self.explicit_starts.append((args[0], "vold-decrypt"))

    def _data_fstab_flags(self):

        return getattr(self, "_data_flags", [])

    def set_fstab(self, entries):

        self._data_flags = []
        for e in entries or []:
            mp = e.mount_point.strip("/")
            if mp == "data":
                self._data_flags = list(e.fs_mgr_flags)
                break

    def _conditions_hold(self, action):

        for cond in action.conditions:
            if "=" not in cond:
                return False
            key, want = cond.split("=", 1)
            have = self.props.get(key.strip())
            if have is None:
                return False
            if want.strip() not in ("*", have):
                return False
        return True

    def _mark_started(self):
        started_classes = {c for c, _t in self.class_starts}
        by_name = {s.name: s for s in self.services}
        for cls, trig in self.class_starts:
            for s in self.services:
                if s.klass == cls and not s.disabled:
                    s.started_by.add(trig)
        for name, trig in self.explicit_starts:
            svc = by_name.get(name)
            if svc is not None:
                svc.started_by.add(trig)
        self.started_classes = started_classes

    # ── ringkasan ──
    @property
    def started_services(self):

        return [s for s in self.services if s.started_by]

    @property
    def never_started(self):

        return [s for s in self.services if not s.started_by]
