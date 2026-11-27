# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import re

print("[CORE STAGE] VDEV SELINUX")

# Direktori tempat sepolicy tinggal, urut prioritas baca.
_POLICY_DIRS = (
    "/system/etc/selinux",
    "/system_ext/etc/selinux",
    "/product/etc/selinux",
    "/vendor/etc/selinux",
    "/odm/etc/selinux",
)

_CIL_SUFFIX = "_sepolicy.cil"
_PUB_CIL = "plat_pub_versioned.cil"
_FC_SUFFIX = "_file_contexts"

_BOOL_HEADS = ("and", "or", "not", "all", "xor", "eq", "neq")


def _parse_line(line):

    if not line or line[0] != "(":
        return None
    toks = line.replace("(", " ( ").replace(")", " ) ").split()
    stack = [[]]
    for t in toks:
        if t == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(t)
    top = stack[0]
    return top[0] if top and isinstance(top[0], list) else None


class Policy:


    def __init__(self):
        self.types = set()          # (type X)
        self.attributes = set()     # (typeattribute X)
        self.attr_expr = {}         # attr -> DAFTAR ekspresi keanggotaan (mentah)
        self.allow = {}             # (src, tgt, cls) -> set(perm)
        self.dontaudit = {}         # idem
        self.typetrans = {}         # (src, tgt, cls) -> hasil
        self.sid_context = {}       # nama SID awal -> context (mis. kernel)
        self.class_perms = {}       # cls -> set(perm) (sudah digabung common)
        self._common = {}           # nama common -> set(perm)
        self._class_common = {}     # cls -> nama common
        self.sources = []           # berkas cil yang benar-benar terbaca
        self.vendor_vers = ""       # versi plat policy yang diminta vendor
        self._members_cache = {}    # attr -> set(type)
        self._covers_cache = {}     # type -> set(nama yang mencakupnya)
        self._type_attrs = None     # type -> set(attr), dibangun malas
        self._native = None         # NativeSelinuxPolicy (Rust) bila aktif

    # ── baca ──
    @classmethod
    def load(cls, vfs):

        pol = cls()

        vers = ""
        for cand in ("/vendor/etc/selinux/plat_sepolicy_vers.txt",
                    "/odm/etc/selinux/plat_sepolicy_vers.txt"):
            text = vfs.read_text(cand, 64)
            if text:
                vers = text.strip().split()[0] if text.strip() else ""
                if vers:
                    break

        cil_texts = []

        def _take(vpath):
            text = vfs.read_text(vpath)
            if not text:
                return
            cil_texts.append(text)
            pol.sources.append(vpath)

        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if name.endswith(_CIL_SUFFIX) or name == _PUB_CIL:
                    _take(d + "/" + name)
            # mapping untuk versi yang diminta vendor
            if vers:
                mdir = d + "/mapping"
                if vfs.is_dir(mdir):
                    for fname in (vers + ".cil", vers + ".compat.cil"):
                        if fname in (vfs.listdir(mdir) or []):
                            _take(mdir + "/" + fname)

        if not pol.sources:
            return None
        pol.vendor_vers = vers

        use_rust = False
        try:
            from core import native_backend
            if native_backend._get_native() is not None:
                native = native_backend.vdev_selinux_policy()
                native.load_texts(cil_texts)
                pol._native = native
                use_rust = True
        except Exception:
            use_rust = False

        if not use_rust:
            for text in cil_texts:
                pol._absorb(text)
            pol._finish_classes()
        return pol

    def _absorb(self, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line[0] != "(":
                continue
            # Saring cepat sebelum parsing penuh — mayoritas baris tidak
            # kita perlukan, dan parsing 5 MB S-expression itu mahal.
            head = line[1:line.find(" ")] if " " in line else ""
            if head not in ("allow", "dontaudit", "typetransition", "type",
                            "typeattribute", "typeattributeset", "class",
                            "common", "classcommon", "sidcontext"):
                continue
            node = _parse_line(line)
            if not node:
                continue
            self._add(node)

    def _add(self, node):
        head = node[0]

        if head == "type" and len(node) == 2 and isinstance(node[1], str):
            self.types.add(node[1])
        elif head == "typeattribute" and len(node) == 2 and isinstance(node[1], str):
            self.attributes.add(node[1])
        elif head == "typeattributeset" and len(node) == 3:


            self.attributes.add(node[1])
            self.attr_expr.setdefault(node[1], []).append(node[2])
        elif head in ("allow", "dontaudit") and len(node) == 4:
            src, tgt, spec = node[1], node[2], node[3]
            if not (isinstance(src, str) and isinstance(tgt, str)):
                return
            if not (isinstance(spec, list) and len(spec) == 2
                    and isinstance(spec[0], str) and isinstance(spec[1], list)):
                return
            cls_name = spec[0]
            perms = {p for p in spec[1] if isinstance(p, str)}
            table = self.allow if head == "allow" else self.dontaudit
            table.setdefault((src, tgt, cls_name), set()).update(perms)
        elif head == "typetransition":
            # (typetransition SRC TGT CLASS RESULT)
            # (typetransition SRC TGT CLASS "nama" RESULT)  <- bentuk bernama
            parts = [p for p in node[1:] if isinstance(p, str)]
            if len(parts) == 4:
                src, tgt, cls_name, result = parts
                self.typetrans[(src, tgt, cls_name)] = result
        elif head == "class" and len(node) == 3 and isinstance(node[2], list):
            self.class_perms.setdefault(node[1], set()).update(
                p for p in node[2] if isinstance(p, str))
        elif head == "common" and len(node) == 3 and isinstance(node[2], list):
            self._common.setdefault(node[1], set()).update(
                p for p in node[2] if isinstance(p, str))
        elif head == "classcommon" and len(node) == 3:
            self._class_common[node[1]] = node[2]
        elif head == "sidcontext" and len(node) == 3 and isinstance(node[2], list):


            parts = [x for x in node[2] if isinstance(x, str)]
            if len(parts) >= 3:
                self.sid_context[node[1]] = ":".join(parts[:3])

    def _finish_classes(self):
        for cls_name, common_name in self._class_common.items():
            extra = self._common.get(common_name)
            if extra:
                self.class_perms.setdefault(cls_name, set()).update(extra)

    # ── set-aljabar atribut ──
    def _eval(self, node, seen):
        if isinstance(node, str):
            return self._members(node, seen)
        if not node:
            return set()
        head = node[0]
        if isinstance(head, str) and head in _BOOL_HEADS:
            if head == "all":
                return set(self.types)
            if head == "not":
                inner = set()
                for x in node[1:]:
                    inner |= self._eval(x, seen)
                return self.types - inner
            if head == "and":
                out = None
                for x in node[1:]:
                    got = self._eval(x, seen)
                    out = got if out is None else (out & got)
                return out or set()
            if head == "or":
                out = set()
                for x in node[1:]:
                    out |= self._eval(x, seen)
                return out
            # xor/eq/neq: jarang di policy Android; tidak ditebak-tebak.
            return set()
        out = set()
        for x in node:
            out |= self._eval(x, seen)
        return out

    def _members(self, name, seen):

        if name in self._members_cache:
            return self._members_cache[name]
        if name not in self.attr_expr:
            # Tipe konkret (atau nama tak dikenal — diperlakukan apa adanya,
            # bukan diasumsikan kosong).
            return {name}
        if name in seen:
            return set()                      # penjaga siklus
        deeper = seen | {name}
        got = set()
        for expr in self.attr_expr[name]:
            got |= self._eval(expr, deeper)
        self._members_cache[name] = got
        return got

    def members(self, name):
        if self._native is not None:
            return set(self._native.resolve_attr(name))
        return self._members(name, frozenset())

    def _build_type_attrs(self):
        if self._type_attrs is not None:
            return
        m = {}
        for attr in self.attr_expr:
            for t in self.members(attr):
                m.setdefault(t, set()).add(attr)
        self._type_attrs = m

    def covers(self, type_name):

        hit = self._covers_cache.get(type_name)
        if hit is not None:
            return hit
        self._build_type_attrs()
        out = {type_name} | self._type_attrs.get(type_name, set())
        self._covers_cache[type_name] = out
        return out

    # ── tanya ──
    def _lookup(self, table, src, tgt, cls_name, perm):
        for s in self.covers(src):
            for t in self.covers(tgt):
                perms = table.get((s, t, cls_name))
                if perms and perm in perms:
                    return True
        return False

    def allowed(self, src, tgt, cls_name, perm):
        if self._native is not None:
            return bool(self._native.allows(src, tgt, cls_name, perm))
        return self._lookup(self.allow, src, tgt, cls_name, perm)

    def silenced(self, src, tgt, cls_name, perm):

        if self._native is not None:
            return bool(self._native.silenced(src, tgt, cls_name, perm))
        return self._lookup(self.dontaudit, src, tgt, cls_name, perm)

    def domain_for_exec(self, src_domain, exec_type):

        if self._native is not None:
            return self._native.domain_for_exec(src_domain, exec_type)
        for s in self.covers(src_domain):
            for t in self.covers(exec_type):
                got = self.typetrans.get((s, t, "process"))
                if got:
                    return got
        return ""

    def sid_domain(self, sid_name):

        if self._native is not None:
            return self._native.sid_domain(sid_name)
        ctx = self.sid_context.get(sid_name, "")
        if not ctx:
            return ""
        parts = ctx.split(":")
        return parts[2] if len(parts) >= 3 else ""

    def knows_type(self, name):
        if self._native is not None:
            return bool(self._native.is_known(name))
        return name in self.types or name in self.attributes


_REGEX_META = re.compile(r"[\\\[\]().*+?{}|^$]")


def _literal_prefix(pattern: str) -> str:
    m = _REGEX_META.search(pattern)
    return pattern[:m.start()] if m else pattern


class FileContexts:


    def __init__(self):
        self.exact = {}     # path literal -> context
        self.rules = []     # [pattern_str, literal_prefix, len_pat, context, compiled_or_None]
        self.sources = []
        self._lookup_cache = {}

    @classmethod
    def load(cls, vfs):
        fc = cls()
        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if not name.endswith(_FC_SUFFIX):
                    continue
                text = vfs.read_text(d + "/" + name)
                if not text:
                    continue
                fc._absorb(text)
                fc.sources.append(d + "/" + name)
        return fc if fc.sources else None

    def _absorb(self, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            path_re = parts[0]
            ctx = parts[-1]
            if ":" not in ctx:
                continue
            if not _REGEX_META.search(path_re):
                self.exact[path_re] = ctx
                continue
            self.rules.append([path_re, _literal_prefix(path_re), len(path_re), ctx, None])

        self.rules.sort(key=lambda r: -r[2])
        self._lookup_cache.clear()

    def lookup(self, path):
        hit = self.exact.get(path)
        if hit:
            return hit
        cached = self._lookup_cache.get(path)
        if cached is not None:
            return cached
        for rule in self.rules:
            prefix = rule[1]
            if prefix and not path.startswith(prefix):
                continue
            rx = rule[4]
            if rx is None:
                try:
                    rx = re.compile(rule[0] + r"\Z")
                except re.error:
                    rx = False
                rule[4] = rx
            if rx and rx.match(path):
                self._lookup_cache[path] = rule[3]
                return rule[3]
        self._lookup_cache[path] = ""
        return ""


class UnpackContexts:


    def __init__(self):
        self.exact = {}
        self.rules = []     # (regex, panjang pola, context) — sama seperti FileContexts
        self.sources = []

    @classmethod
    def load(cls, layout):
        import os as _os
        from core.vdev.mounts import partition_mount_point

        uc = cls()
        cfg = _os.path.join(layout.port_dir, "config")
        if not _os.path.isdir(cfg):
            return None

        for name, host in (layout.partitions or {}).items():
            folder = _os.path.basename(_os.path.normpath(host))
            path = _os.path.join(cfg, folder + "_file_contexts")
            if not _os.path.isfile(path):
                continue
            mp = partition_mount_point(layout, name)
            prefix = "/" + folder
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                p, ctx = parts[0], parts[-1]
                if ":" not in ctx:
                    continue
                if p == prefix:
                    rel = ""
                elif p.startswith(prefix + "/"):
                    rel = p[len(prefix):]
                else:
                    continue
                vpath = (rel if mp == "/" else mp + rel) or "/"
                if not re.search(r"[\\\[\]().*+?{}|^$]", p):
                    uc.exact.setdefault(vpath, ctx)
                    continue
                try:
                    uc.rules.append((re.compile(vpath + r"\Z"), len(vpath), ctx))
                except re.error:
                    continue
            uc.sources.append(path)
        if not uc.sources:
            return None
        # Kekhususan sama seperti FileContexts: pola terpanjang menang.
        uc.rules.sort(key=lambda r: -r[1])
        return uc

    def lookup(self, path):
        hit = self.exact.get(path)
        if hit:
            return hit
        for rx, _n, ctx in self.rules:
            if rx.match(path):
                return ctx
        return ""


def type_of(context):

    if not context:
        return ""
    parts = context.split(":")
    return parts[2] if len(parts) >= 3 else ""


def _read_selinux_vers(vfs) -> str:
    for cand in ("/vendor/etc/selinux/plat_sepolicy_vers.txt",
                 "/odm/etc/selinux/plat_sepolicy_vers.txt"):
        text = vfs.read_text(cand, 64)
        if text and text.strip():
            return text.strip().split()[0]
    return ""


def collect_cil_files(vfs) -> tuple[list[tuple[str, str]], str]:
    """Return ([(vpath, text), ...], vendor_vers) matching secilc's assembly set."""
    vers = _read_selinux_vers(vfs)
    out = []
    seen = set()

    def _take(vpath):
        if vpath in seen:
            return
        seen.add(vpath)
        text = vfs.read_text(vpath)
        if text:
            out.append((vpath, text))

    for d in _POLICY_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in sorted(vfs.listdir(d) or []):
            if name.endswith(_CIL_SUFFIX) or name == _PUB_CIL:
                _take(d + "/" + name)
        if vers:
            mdir = d + "/mapping"
            if vfs.is_dir(mdir):
                for fname in (vers + ".cil", vers + ".compat.cil"):
                    if fname in (vfs.listdir(mdir) or []):
                        _take(mdir + "/" + fname)
    return out, vers


_CIL_DECL_RE = re.compile(r"^\(\s*(type|typeattribute|typealias|roletype)\s+([A-Za-z0-9_.\-]+)")
_CIL_ALLOW_RE = re.compile(r"^\(\s*(?:allow|dontaudit|auditallow)\s+([A-Za-z0-9_.\-]+)\s+([A-Za-z0-9_.\-]+)\s+\(")
_CIL_TRANS_RE = re.compile(r"^\(\s*typetransition\s+([A-Za-z0-9_.\-]+)\s+([A-Za-z0-9_.\-]+)\s+[A-Za-z0-9_.\-]+\s+(?:\"[^\"]*\"\s+)?([A-Za-z0-9_.\-]+)\s*\)")
_CIL_ATTRSET_RE = re.compile(r"^\(\s*typeattributeset\s+([A-Za-z0-9_.\-]+)\s+")
_CIL_BUILTIN_SYMS = frozenset({"self", "all", "and", "or", "not", "xor", "eq", "neq", "object_r"})


def audit_cil_symbols(vfs) -> dict:
    """Static secilc symbol-resolution audit across all assembled CIL files.

    Detects:
      1. Duplicate concrete `(type X)` definitions across CIL files (secilc fatal error).
      2. Unresolved type/attribute names referenced in `(allow ...)`, `(typetransition ...)`,
         or `(typeattributeset ...)` outside `(optional ...)` blocks.
    """
    cil_files, vers = collect_cil_files(vfs)
    if not cil_files:
        return {"files": [], "vers": vers, "type_count": 0, "attr_count": 0,
                "duplicate_types": [], "unresolved_symbols": []}

    declared_types = {}       # name -> first vpath
    declared_attrs = {}       # name -> first vpath
    declared_aliases = set()
    type_attr_collisions = []  # [(name, type_vpath, attr_vpath)] — fatal even under `secilc -m`
    refs = []                 # [(sym, vpath, kind)]

    for vpath, text in cil_files:
        opt_depth = 0
        for raw in text.splitlines():
            s = raw.strip()
            if not s or s.startswith(";"):
                continue
            # Track (optional ...) nesting so optional-block symbols never trigger false errors
            if s.startswith("(optional ") or s == "(optional":
                opt_depth += s.count("(") - s.count(")")
                if opt_depth <= 0:
                    opt_depth = 0
                continue
            if opt_depth > 0:
                opt_depth += s.count("(") - s.count(")")
                if opt_depth < 0:
                    opt_depth = 0
                continue

            md = _CIL_DECL_RE.match(s)
            if md:
                kind, sym = md.group(1), md.group(2)
                if kind == "type":
                    declared_types.setdefault(sym, vpath)
                elif kind == "typeattribute":
                    declared_attrs.setdefault(sym, vpath)
                elif kind == "typealias":
                    declared_aliases.add(sym)
                continue

            ma = _CIL_ALLOW_RE.match(s)
            if ma:
                refs.append((ma.group(1), vpath, "allow-src"))
                refs.append((ma.group(2), vpath, "allow-tgt"))
                continue

            mt = _CIL_TRANS_RE.match(s)
            if mt:
                refs.append((mt.group(1), vpath, "trans-src"))
                refs.append((mt.group(2), vpath, "trans-tgt"))
                refs.append((mt.group(3), vpath, "trans-res"))
                continue

            ms = _CIL_ATTRSET_RE.match(s)
            if ms:
                refs.append((ms.group(1), vpath, "typeattributeset"))

    # Under Android's `secilc -m` (--multiple-decls), repeated identical `(type X)` across
    # plat_sepolicy.cil and plat_pub_versioned.cil is legal, but declaring the same name
    # as BOTH a concrete `(type X)` AND a `(typeattribute X)` is a fatal secilc compile error.
    for sym in sorted(set(declared_types.keys()) & set(declared_attrs.keys())):
        type_attr_collisions.append((sym, declared_types[sym], declared_attrs[sym]))

    known = set(declared_types.keys()) | set(declared_attrs.keys()) | declared_aliases | _CIL_BUILTIN_SYMS
    seen_unres = set()
    unresolved = []
    for sym, vpath, kind in refs:
        if sym in known or sym in seen_unres:
            continue
        if sym.startswith("(") or sym.endswith(")"):
            continue
        seen_unres.add(sym)
        unresolved.append((sym, vpath, kind))

    return {
        "files": [vp for vp, _ in cil_files],
        "vers": vers,
        "type_count": len(declared_types),
        "attr_count": len(declared_attrs),
        "duplicate_types": type_attr_collisions,
        "unresolved_symbols": unresolved,
    }


class ServiceContexts:
    """Merged Android service_contexts / hwservice_contexts lookup table."""

    def __init__(self, kind: str = "service"):
        self.kind = kind
        self.exact = {}         # key -> (context, source)
        self.prefixes = []      # [(prefix, context, source)] longest-first
        self.default_ctx = ""
        self.sources = []

    @classmethod
    def load(cls, vfs, kind: str = "service"):
        suffix = "_hwservice_contexts" if kind == "hwservice" else "_service_contexts"
        sc = cls(kind=kind)
        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if not name.endswith(suffix):
                    continue
                vpath = d + "/" + name
                text = vfs.read_text(vpath)
                if not text:
                    continue
                sc.sources.append(vpath)
                for raw in text.splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    key, ctx_s = parts[0], parts[-1]
                    if ":" not in ctx_s:
                        continue
                    if key == "*":
                        sc.default_ctx = ctx_s
                    elif key.endswith("*"):
                        sc.prefixes.append((key[:-1], ctx_s, vpath))
                    else:
                        sc.exact[key] = (ctx_s, vpath)
                        sc.prefixes.append((key, ctx_s, vpath))
        if not sc.sources:
            return None
        sc.prefixes.sort(key=lambda r: -len(r[0]))
        return sc

    def lookup(self, name: str) -> tuple[str, str, bool]:
        """Return (context, source, is_default_fallback)."""
        hit = self.exact.get(name)
        if hit:
            return hit[0], hit[1], False
        for pref, ctx_s, src in self.prefixes:
            if name.startswith(pref):
                return ctx_s, src, False
        if self.default_ctx:
            return self.default_ctx, "default(*)", True
        return "", "", True


class PropertyContexts:
    """Merged Android property_contexts lookup table."""

    def __init__(self):
        self.exact = {}         # key -> (context, source)
        self.prefixes = []      # [(prefix, context, source)] longest-first
        self.default_ctx = "u:object_r:default_prop:s0"
        self.sources = []

    @classmethod
    def load(cls, vfs):
        pc = cls()
        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if not name.endswith("_property_contexts"):
                    continue
                vpath = d + "/" + name
                text = vfs.read_text(vpath)
                if not text:
                    continue
                pc.sources.append(vpath)
                for raw in text.splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    key = parts[0]
                    ctx_s = next((p for p in parts[1:] if p.startswith("u:")), "")
                    if not ctx_s:
                        continue
                    is_exact = "exact" in parts[2:]
                    if key == "*":
                        pc.default_ctx = ctx_s
                    elif is_exact:
                        pc.exact[key] = (ctx_s, vpath)
                    else:
                        pc.prefixes.append((key, ctx_s, vpath))
        if not pc.sources:
            return None
        pc.prefixes.sort(key=lambda r: -len(r[0]))
        return pc

    def lookup(self, prop_name: str) -> tuple[str, str]:
        hit = self.exact.get(prop_name)
        if hit:
            return hit
        for pref, ctx_s, src in self.prefixes:
            if prop_name.startswith(pref):
                return ctx_s, src
        return self.default_ctx, "default(*)"
