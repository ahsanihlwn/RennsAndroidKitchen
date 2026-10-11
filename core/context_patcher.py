# SPDX-License-Identifier: MPL-2.0
# core/context_patcher.py
import os
import re
from collections import Counter
from core.aosp_context_database import lookup_aosp_context
from core.app_paths import NEOCAT_LEARNING_DIR, neocat_cache_dir
print("[CORE STAGE] neocat MODEL START")


KNOWN_PARTITIONS = (
    "system",
    "system_ext",
    "product",
    "vendor",
    "odm",
    "odm_dlkm",
)

DEFAULT_CTX_BY_DOMAIN = {
    "system":     "u:object_r:system_file:s0",
    "system_ext": "u:object_r:system_file:s0",
    "product":    "u:object_r:system_file:s0",
    "vendor":     "u:object_r:vendor_file:s0",
    "odm":        "u:object_r:vendor_file:s0",
    "odm_dlkm":   "u:object_r:vendor_file:s0",
}

def _normalize_neobranch_ctx_key(ctx_key):

    key = str(ctx_key).strip()
    for _ in range(4):
        if not key.startswith("/neobranch/"):
            return key
        parts = key.strip("/").split("/")
        if len(parts) < 5 or parts[0] != "neobranch":
            return None
        fs_root = parts[1]
        marker = parts[2]
        rest = parts[3:]
        if marker.startswith("branch_"):
            pass
        elif marker == "source":
            pass
        else:
            return None
        if not rest or rest[0] != fs_root:
            return None
        key = "/" + "/".join(rest)
    return None

def _domain_family(domain):

    if domain in ("vendor", "odm", "odm_dlkm", "vendor_ext"):
        return "vendor"
    return "system"


STRICT_CONTEXT_KEYWORDS = (
    "_exec",
    "_server",
    "_bin",
    "zygote",
    "init",
)

GENERIC_TOKENS = {
    "run", "test", "script", "exec", "bin",
    "tool", "helper", "tmp", "debug"
}

STRICT_SEMANTIC_RULES = [
    # Semua entry di sini adalah fallback SETELAH database AOSP miss.
    # Jangan tambahkan entry yang sudah di-cover database AOSP.

    # generic executables — OEM binary di /bin yang tidak dikenal database
    ("/bin",                   "u:object_r:system_file:s0",        "system"),
    ("/xbin",                  "u:object_r:system_file:s0",        "system"),

    # libraries
    ("/lib/hw",                "u:object_r:system_lib_file:s0",    "system"),
    ("/lib64/hw",              "u:object_r:system_lib_file:s0",    "system"),
    ("/lib",                   "u:object_r:system_lib_file:s0",    "system"),
    ("/lib64",                 "u:object_r:system_lib_file:s0",    "system"),

    # framework / java
    ("/framework",             "u:object_r:system_file:s0",        "system"),

    # etc subdirs
    ("/etc/init",              "u:object_r:system_file:s0",        "system"),
    ("/etc/permissions",       "u:object_r:system_file:s0",        "system"),
    ("/etc/selinux",           "u:object_r:system_file:s0",        "system"),

    # resources
    ("/fonts",                 "u:object_r:system_file:s0",        "system"),
    ("/media",                 "u:object_r:system_file:s0",        "system"),
    ("/usr",                   "u:object_r:system_file:s0",        "system"),
]

SEMANTIC_SYSTEM_RULES = [
    # init rc
    (lambda p: p.startswith("/etc/init/") and p.endswith(".rc"),
        "u:object_r:system_file:s0", "init-rc"),

    # permissions / xml
    (lambda p: p.startswith("/etc/permissions/"),
        "u:object_r:system_file:s0", "permissions"),

    # keylayout / keychars / idc
    (lambda p: p.startswith("/usr/keylayout/") or p.startswith("/usr/keychars/") or p.startswith("/usr/idc/"),
        "u:object_r:system_file:s0", "input"),

    # fonts / media
    (lambda p: p.startswith("/fonts/") or p.startswith("/media/"),
        "u:object_r:system_file:s0", "resource"),

    # privileged system apps
    (lambda p: p.startswith("/priv-app/"),
        "u:object_r:system_app_file:s0", "priv-app"),

    # normal system apps
    (lambda p: p.startswith("/app/"),
        "u:object_r:system_app_file:s0", "system-app"),

    # apex content — isi apex dapat system_file/system_lib_file
    (lambda p: p.startswith("/apex/") and ("/lib/" in p or "/lib64/" in p),
        "u:object_r:system_lib_file:s0", "apex-lib"),
]

SEMANTIC_VENDOR_RULES = [


    (lambda p: p.startswith("/bin/"),
        "u:object_r:vendor_exec:s0", "vendor-bin"),


    (lambda p: (p.startswith("/lib/") or p.startswith("/lib64/")) and p.endswith(".so"),
        "u:object_r:vendor_lib_file:s0", "shared-lib"),

    (lambda p: (p.startswith("/lib/") or p.startswith("/lib64/")),
        "u:object_r:vendor_lib_file:s0", "lib-generic"),


    (lambda p: "/firmware/" in p or p.endswith(".mbn") or p.endswith(".fw"),
        "u:object_r:vendor_firmware_file:s0", "firmware"),

    (lambda p: "/dsp/" in p,
        "u:object_r:vendor_firmware_file:s0", "dsp"),

    (lambda p: "/rfs/" in p or "/radio/" in p,
        "u:object_r:vendor_firmware_file:s0", "radio"),


    (lambda p: p.startswith("/etc/init/") and p.endswith(".rc"),
        "u:object_r:vendor_configs_file:s0", "init-rc"),


    (lambda p: p.startswith("/etc/"),
        "u:object_r:vendor_configs_file:s0", "config"),


    (lambda p: p.startswith("/app/") or p.startswith("/priv-app/"),
        "u:object_r:vendor_app_file:s0", "vendor-app"),

    (lambda p: p.startswith("/overlay/"),
        "u:object_r:vendor_overlay_file:s0", "overlay"),

    (lambda p: p.startswith("/framework/"),
        "u:object_r:vendor_framework_file:s0", "framework"),


    (lambda p: p.startswith("/apex/"),
        "u:object_r:vendor_apex_file:s0", "apex"),
]


class ContextPatcher:
    def __init__(self, project_base):

        self.base = project_base
        self.cfgdir = os.path.join(project_base, "config")
        self.neocat_out_dir = neocat_cache_dir(project_base)


        self.ctx_map = self._load_file_contexts()
        self._infer_cache = {}
        self._partition_cache = {}


        self.learning_base = NEOCAT_LEARNING_DIR
        self.learning_index = self._build_learning_index()
        self.learning_string_freq = self._build_string_frequency()


    def _load_file_contexts(self):
        ctx = {}
        if not os.path.isdir(self.cfgdir):
            return ctx

        for fn in os.listdir(self.cfgdir):
            if not fn.endswith("_file_contexts"):
                continue

            full = os.path.join(self.cfgdir, fn)
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) < 2:
                        continue
                    ctx[parts[0]] = parts[1]

        return ctx

    def _load_neocat_cache(self, fc_name):
        fc_path = os.path.join(self.neocat_out_dir, fc_name)
        data = {}

        if not os.path.isfile(fc_path):
            return data

        with open(fc_path, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                k, v = s.split(None, 1)
                data[k] = v

        return data

    def _load_all_neocat_cache(self):
        cache = {}

        if not os.path.isdir(self.neocat_out_dir):
            return cache

        for fn in os.listdir(self.neocat_out_dir):
            if not fn.endswith("_file_contexts"):
                continue

            full = os.path.join(self.neocat_out_dir, fn)
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    k, v = s.split(None, 1)
                    cache[k] = v

        return cache


    def _build_learning_index(self):

        index = {}

        if not os.path.isdir(self.learning_base):
            return index

        for vendor_dir in os.listdir(self.learning_base):
            full_vendor = os.path.join(self.learning_base, vendor_dir)
            if not os.path.isdir(full_vendor):
                continue

            data = {
                "hint": set(),
                "system": {},
                "vendor": {}
            }

            # LOAD HINT
            hint_dir = os.path.join(full_vendor, "hint")
            if os.path.isdir(hint_dir):
                for f in os.listdir(hint_dir):
                    with open(os.path.join(hint_dir, f), "r", encoding="utf-8", errors="ignore") as fh:
                        for ln in fh:
                            s = ln.strip()
                            if s:
                                data["hint"].add(s.lower())

            # LOAD SYSTEM/VENDOR FILE_CONTEXTS
            for domain in ("system", "vendor"):
                dom_dir = os.path.join(full_vendor, domain)
                if not os.path.isdir(dom_dir):
                    continue

                for fn in os.listdir(dom_dir):
                    if not fn.endswith("_file_contexts"):
                        continue

                    with open(os.path.join(dom_dir, fn), "r", encoding="utf-8", errors="ignore") as fh:
                        for ln in fh:
                            s = ln.strip()
                            if not s or s.startswith("#"):
                                continue
                            parts = s.split()
                            if len(parts) < 2:
                                continue
                            normalized_pattern = self.normalize_mount_path(parts[0].lstrip("/"))
                            data[domain][normalized_pattern] = parts[1]

            index[vendor_dir] = data

        return index

    def _score_hint_match(self, rel_path, hints):
        score = 0
        low = rel_path.lower()
        for h in hints:
            if h in low:
                score += 1
        return score

    def detect_partition_oem(self, abs_root: str) -> str | None:

        if not self.learning_index:
            return None

        cache_key = f"__oem__{abs_root}"
        if cache_key in self._partition_cache:
            return self._partition_cache[cache_key]

        # Kumpulkan semua basename dari path yang ada di ctx_map
        # yang berada di bawah abs_root
        rel_prefix = os.path.relpath(abs_root, self.base).replace("\\", "/")
        basenames = []
        for k in self.ctx_map:
            k_rel = k.lstrip("/")
            if k_rel.startswith(rel_prefix + "/") or k_rel == rel_prefix:
                basenames.append(os.path.basename(k_rel).lower())

        if not basenames:
            self._partition_cache[cache_key] = None
            return None

        # Score setiap vendor berdasarkan token match ke basenames
        scores = {}
        for vendor_name, counter in self.learning_string_freq.items():
            total_tokens = sum(counter.values()) or 1
            score = 0
            for basename in basenames:
                for token, count in counter.items():
                    if token in basename:
                        score += count / total_tokens
            if score > 0:
                scores[vendor_name] = score

        if not scores:
            self._partition_cache[cache_key] = None
            return None

        # Ambil yang tertinggi, tapi hanya kalau jelas dominan
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_name, top_score = ranked[0]

        # Confidence: top harus minimal 2x score kedua, atau sendirian
        if len(ranked) == 1 or top_score >= ranked[1][1] * 2.0:
            self._partition_cache[cache_key] = top_name
            return top_name

        self._partition_cache[cache_key] = None
        return None

    def _oem_from_filename(self, filename: str) -> str | None:

        low = filename.lower()
        best_vendor = None
        best_score = 0

        for vendor_name, data in self.learning_index.items():
            score = sum(1 for h in data["hint"] if h in low)
            if score > best_score:
                best_score = score
                best_vendor = vendor_name

        return best_vendor if best_score > 0 else None

    def infer_from_learning_data(self, rel_path, debug=False):

        if not self.learning_index:
            return None, None

        domain = self.detect_semantic_domain(rel_path)
        semantic = self.normalize_mount_path(rel_path)
        filename = os.path.basename(rel_path).lower()

        if debug:
            print("==================================================")
            print(f"[LEARN] TARGET  : {rel_path}")
            print(f"[LEARN] DOMAIN  : {domain}")


        _parts = rel_path.strip("/").split("/")
        _mount_abs = os.path.join(self.base, _parts[0]) if _parts else None
        partition_oem = self.detect_partition_oem(_mount_abs) if _mount_abs else None

        if partition_oem:
            if debug:
                print(f"[LEARN] PARTITION OEM: {partition_oem}")
            ctx = self._search_learning_vendor(partition_oem, domain, semantic, debug)
            if ctx:
                return ctx, f"learning:{partition_oem}:partition-oem"


        file_oem = self._oem_from_filename(filename)
        if file_oem and file_oem != partition_oem:
            if debug:
                print(f"[LEARN] FILENAME OEM: {file_oem}")
            ctx = self._search_learning_vendor(file_oem, domain, semantic, debug)
            if ctx:
                return ctx, f"learning:{file_oem}:filename-oem"


        ctx = self._structural_pattern_match(domain, semantic, debug)
        if ctx:
            return ctx, "learning:structural-pattern"


        target_tokens = self._extract_struct_tokens(os.path.basename(semantic))
        if len(target_tokens) >= 2:
            if debug:
                print("[LEARN] GLOBAL BRUTE SCAN (filtered)")
            for vendor_name in self.learning_index.keys():
                if vendor_name in (partition_oem, file_oem):
                    continue  # sudah dicek di stage 1/2
                ctx = self._search_learning_vendor(vendor_name, domain, semantic, debug)
                if ctx:
                    return ctx, f"learning:{vendor_name}:global"

        if debug:
            print("[LEARN] NO MATCH FOUND")
            print("==================================================")

        return None, None

    def _search_learning_vendor(self, vendor_name, domain, semantic, debug=False):
        data = self.learning_index.get(vendor_name)
        if not data:
            return None

        domain_map = data.get(domain, {})
        if not domain_map:
            return None

        if debug:
            print(f"[LEARN] ENTER {vendor_name}/{domain} ({len(domain_map)} patterns)")

        for pattern, ctx in domain_map.items():
            try:
                if re.fullmatch(pattern, semantic):
                    if debug:
                        print(f"[LEARN] MATCH REGEX -> {ctx}")
                    return ctx
            except re.error:
                continue

            if semantic == pattern:
                if debug:
                    print(f"[LEARN] MATCH EXACT -> {ctx}")
                return ctx

        return None

    def _build_string_frequency(self):

        freq = {}

        for vendor_name, data in self.learning_index.items():
            freq[vendor_name] = Counter()

            for domain in ("system", "vendor"):
                for pattern in data.get(domain, {}):
                    # ambil basename
                    name = os.path.basename(pattern).lower()
                    parts = re.split(r"[._\-@/]", name)

                    for p in parts:
                        if len(p) < 3:
                            continue
                        freq[vendor_name][p] += 1

        return freq

    def _extract_struct_tokens(self, name):

        name = name.lower()

        # buang extension
        name = name.replace(".so", "")

        parts = re.split(r"[._\-@/]", name)

        tokens = []
        for p in parts:
            if not p:
                continue
            if p.isdigit():
                continue
            if re.fullmatch(r"v?\d+", p):
                continue
            if len(p) < 3:
                continue
            tokens.append(p)

        return set(tokens)

    def _structural_pattern_match(self, domain, semantic, debug=False):


        target_name = os.path.basename(semantic)
        target_tokens = self._extract_struct_tokens(target_name)

        best_ctx = None
        best_score = 0

        for vendor_name, data in self.learning_index.items():
            domain_map = data.get(domain, {})

            for pattern, ctx in domain_map.items():
                base = os.path.basename(pattern)
                pattern_tokens = self._extract_struct_tokens(base)

                if not pattern_tokens:
                    continue

                overlap = target_tokens & pattern_tokens
                union = target_tokens | pattern_tokens

                confidence = len(overlap) / len(union)

                if confidence > best_score:
                    best_score = confidence
                    best_ctx = ctx

        # threshold lebih realistis
        if best_score >= 0.5:
            if debug:
                print(f"[LEARN] STRUCT MATCH confidence={best_score}")
            return best_ctx

        return None

    def detect_fs_partition(self, abs_root: str) -> str:

        import os

        def has_content(path):
            try:
                return bool(os.listdir(path))
            except Exception:
                return False

        # STEP 1: cek subfolder wrapper langsung
        for part in ("system_ext", "product", "vendor", "odm",
                     "vendor_dlkm", "odm_dlkm"):
            sub = os.path.join(abs_root, part)
            if os.path.isdir(sub) and has_content(sub):
                # ada wrapper subfolder yang berisi → partisinya itu
                # tapi kalau system juga ada, ini rootfs (lanjut step 2)
                sys_sub = os.path.join(abs_root, "system")
                if os.path.isdir(sys_sub) and has_content(sys_sub):
                    break  # lanjut step 2
                return part

        # STEP 2: cek rootfs signature
        # Rootfs: ada system/ PLUS minimal satu dari partisi dynamic lainnya
        _dynamic = ("system_ext", "product", "vendor", "odm")
        sys_sub = os.path.join(abs_root, "system")
        has_system = os.path.isdir(sys_sub) and has_content(sys_sub)
        has_dynamic = any(
            os.path.isdir(os.path.join(abs_root, p))
            for p in _dynamic
        )
        if has_system and has_dynamic:
            return "system"  # ini rootfs, partisi utama = system/

        # STEP 3: ada system/ sendirian (wrapped system tanpa dynamic)
        if has_system:
            return "system"

        # STEP 4: token-based name detection dari basename folder
        name = os.path.basename(abs_root).lower()
        # pecah: system_ext_a → ['system', 'ext', 'a']
        tokens = set(re.split(r'[_\-\s]+', name))

        # urutan penting: cek kombinasi dulu sebelum tunggal
        if "ext" in tokens and "system" in tokens:
            return "system_ext"
        if "product" in tokens:
            return "product"
        if "odm" in tokens and "dlkm" in tokens:
            return "odm_dlkm"
        if "vendor" in tokens and "dlkm" in tokens:
            return "vendor_dlkm"
        if "odm" in tokens:
            return "odm"
        if "vendor" in tokens:
            return "vendor"
        if "system" in tokens:
            return "system"

        # STEP 5: fallback majority vote dari ctx_map
        counts = {"system": 0, "vendor": 0}
        prefix = os.path.relpath(abs_root, self.base).replace("\\", "/") + "/"
        for k, v in self.ctx_map.items():
            if not k.startswith("/" + prefix):
                continue
            if v.startswith("u:object_r:vendor_") or v.startswith("u:object_r:same_process_hal"):
                counts["vendor"] += 1
            elif v.startswith("u:object_r:system_"):
                counts["system"] += 1

        if counts["vendor"] > counts["system"]:
            return "vendor"
        if counts["system"] > 0:
            return "system"

        return "unknown"

    def detect_vendor_subtree(self, rel_path):

        p = "/" + rel_path.strip("/")

        if p.startswith("/vendor/odm/") or p == "/vendor/odm":
            return "vendor_odm"

        return None

    def infer_vendor_odm_context(self, rel_path):
        p = "/" + rel_path.strip("/")


        if p.endswith("/bin/hw") or p.endswith("/bin/hw/"):
            return {
                "context": "u:object_r:vendor_file:s0",
                "mode": "predicted",
                "reason": "vendor-odm-hw-dir"
            }


        raw_key = "/" + rel_path.strip("/")
        if raw_key in self.ctx_map:
            return {
                "context": self.ctx_map[raw_key],
                "mode": "strict",
                "reason": "vendor-odm-existing"
            }


        if "/bin/" in p:
            return {
                "context": "u:object_r:vendor_exec:s0",
                "mode": "predicted",
                "reason": "vendor-odm-bin"
            }


        if "/etc/" in p:
            return {
                "context": "u:object_r:vendor_configs_file:s0",
                "mode": "predicted",
                "reason": "vendor-odm-config"
            }


        return {
            "context": "u:object_r:vendor_file:s0",
            "mode": "predicted",
            "reason": "vendor-odm-safe-fallback"
        }

    def detect_domain_by_majority(self, fs_root, samples=200):


        sys_count = 0
        ven_count = 0
        checked = 0

        abs_root = os.path.join(self.base, fs_root)
        if not os.path.isdir(abs_root):
            return None

        for root, _, files in os.walk(abs_root):
            for name in files:
                full = os.path.join(root, name)
                rel = "/" + os.path.relpath(full, self.base).replace("\\", "/")

                ctx = self.ctx_map.get(rel)
                if not ctx:
                    continue

                if ctx.startswith("u:object_r:system_"):
                    sys_count += 1
                elif ctx.startswith("u:object_r:vendor_"):
                    ven_count += 1

                checked += 1
                if checked >= samples:
                    break
            if checked >= samples:
                break

        if sys_count > ven_count:
            return "system"
        if ven_count > sys_count:
            return "vendor"

        return None

    def resolve_vendor_semantic_context(self, rel_path):
        semantic = self.normalize_semantic_path(rel_path)

        for rule, ctx, reason in SEMANTIC_VENDOR_RULES:
            try:
                if rule(semantic):
                    return ctx, reason
            except Exception:
                continue

        return None, None

    def normalize_semantic_path(self, rel_path):

        parts = rel_path.strip("/").split("/", 1)
        if len(parts) == 1:
            return "/"
        return "/" + parts[1]

    def normalize_mount_path(self, rel_path):

        parts = rel_path.strip("/").split("/")

        if not parts:
            return "/"

        # buang semua prefix partisi yang dikenal
        while parts and parts[0] in KNOWN_PARTITIONS:
            parts = parts[1:]

        return "/" + "/".join(parts) if parts else "/"

    def detect_semantic_domain(self, rel_path):
        root = rel_path.strip("/").split("/")[0]

        # Kalau root-nya persis nama partisi yang dikenal, return as-is
        # supaya fc_name jadi vendor_ext_file_contexts, product_file_contexts, dst.
        if root in KNOWN_PARTITIONS:
            return root

        # default: system-like
        return "system"

    def resolve_strict_context(self, rel_path):
        semantic = self.normalize_semantic_path(rel_path)
        domain = self.detect_semantic_domain(rel_path)
        family = _domain_family(domain)

        for prefix, ctx, ctx_domain in STRICT_SEMANTIC_RULES:
            if ctx_domain != family:
                continue
            if semantic == prefix or semantic.startswith(prefix + "/"):
                return ctx

        return None


    def escape_ctx_key(self, rel):
        parts = rel.replace("\\", "/").split("/")
        parts = [re.escape(p) for p in parts]
        return "/" + "/".join(parts)

    def is_context_locked(self, rel_path):

        key = "/" + rel_path.strip("/")
        # ctx_map disimpan dengan key escaped, coba keduanya
        ctx = self.ctx_map.get(key) or self.ctx_map.get(self.escape_ctx_key(rel_path))
        if not ctx:
            return False

        for kw in STRICT_CONTEXT_KEYWORDS:
            if kw in ctx:
                return True

        return False


    def _is_priv_app_path(self, rel_path):

        semantic = self.normalize_semantic_path(rel_path)
        return semantic.startswith("/priv-app/") or semantic == "/priv-app"

    def _has_priv_app_ctx(self, fs_root):

        cache_key = f"__privapp__{fs_root}"
        if cache_key in self._partition_cache:
            return self._partition_cache[cache_key]

        root_prefix = "/" + fs_root.strip("/") + "/"
        # Normalisasi: cari /priv-app/ setelah prefix partisi
        for raw_key in self.ctx_map:
            # raw_key bisa escaped atau tidak; kita cari substring saja
            # Cukup cek apakah path ini ada di bawah fs_root DAN mengandung /priv-app/
            if raw_key.startswith(root_prefix) and "/priv-app/" in raw_key:
                self._partition_cache[cache_key] = True
                return True

        self._partition_cache[cache_key] = False
        return False


    def _most_common(self, items):
        return Counter(items).most_common(1)[0][0] if items else None

    def _most_common_strict(self, items):

        if not items:
            return None

        cnt = Counter(items)
        ctx, count = cnt.most_common(1)[0]

        if count < 2:
            return None

        return ctx

    def _collect_children_ctx(self, rel_path):
        prefix = "/" + rel_path.strip("/")
        return [v for k, v in self.ctx_map.items() if k.startswith(prefix + "/")]

    def _collect_sibling_ctx(self, rel_path):
        parent = os.path.dirname(rel_path.strip("/"))
        if not parent:
            return []
        out = []
        for k, v in self.ctx_map.items():
            if os.path.dirname(k.strip("/")) == parent:
                out.append(v)
        return out

    def resolve_system_semantic_context(self, rel_path):
        semantic = self.normalize_semantic_path(rel_path)

        for rule, ctx, reason in SEMANTIC_SYSTEM_RULES:
            try:
                if rule(semantic):
                    return ctx, reason
            except Exception:
                continue

        return None, None


    def infer_context(self, rel_path):
        rel_path = rel_path.strip("/")


        raw_key = "/" + rel_path
        esc_key = self.escape_ctx_key(rel_path)


        cached = self._infer_cache.get(raw_key)
        if cached:
            return cached


        existing_ctx = self.ctx_map.get(esc_key)
        if existing_ctx is None:
            existing_ctx = self.ctx_map.get(raw_key)
        if existing_ctx is not None:
            if self.is_context_locked(rel_path):
                res = {
                    "context": existing_ctx,
                    "mode": "strict",
                    "reason": "locked-existing-context"
                }
                self._infer_cache[raw_key] = res
                return res

            res = {
                "context": existing_ctx,
                "mode": "strict",
                "reason": "existing-context"
            }
            self._infer_cache[raw_key] = res
            return res


        _ROOTFS_TOPS = frozenset((
            "apex", "bootstrap-apex", "bin", "bugreports", "charger", "config",
            "d", "data_mirror", "debug_ramdisk", "etc", "init",
            "linkerconfig", "lost+found", "mnt", "postinstall", "proc",
            "res", "sbin", "sdcard", "second_stage_resources", "sys",
            "system_dlkm", "tmp",
        ))
        _parts = rel_path.strip("/").split("/")
        if _parts and _parts[0] not in KNOWN_PARTITIONS and len(_parts) > 1:
            _mount_abs = os.path.join(self.base, _parts[0])
            if _mount_abs not in self._partition_cache:
                self._partition_cache[_mount_abs] = self.detect_fs_partition(_mount_abs)
            _domain = self._partition_cache[_mount_abs]
            if _domain == "unknown":
                _domain = self.detect_semantic_domain(rel_path)

            _stripped = _parts[1:]
            _stripped0 = _stripped[0] if _stripped else ""

            if _stripped0 in KNOWN_PARTITIONS:
                # wrapped: system_a/system/bin/vold → /system/bin/vold
                _aosp_path = "/" + "/".join(_stripped)
            elif _stripped0 in _ROOTFS_TOPS and _domain == "system":
                # rootfs entry, hanya valid kalau domain memang system (rootfs)
                # system_ext_a/apex → domain=system_ext → tidak masuk sini
                _aosp_path = "/" + "/".join(_stripped)
            else:


                _aosp_path = "/" + _domain + "/" + "/".join(_stripped)
        else:
            _aosp_path = "/" + "/".join(_parts)
        aosp_ctx = lookup_aosp_context(_aosp_path)
        if aosp_ctx:
            res = {
                "context": aosp_ctx,
                "mode": "strict",
                "reason": "aosp-hardcoded"
            }
            self._infer_cache[raw_key] = res
            return res

        strict_ctx = self.resolve_strict_context(rel_path)
        if strict_ctx:
            res = {
                "context": strict_ctx,
                "mode": "strict",
                "reason": "strict-semantic"
            }
            self._infer_cache[raw_key] = res
            return res


        ctx, reason = self.infer_from_learning_data(rel_path, debug=False)
        if ctx:
            res = {
                "context": ctx,
                "mode": "learning",
                "reason": reason
            }
            self._infer_cache[raw_key] = res
            return res


        subtree = self.detect_vendor_subtree(rel_path)
        if subtree == "vendor_odm":
            res = self.infer_vendor_odm_context(rel_path)
            self._infer_cache[raw_key] = res
            return res


        domain = self.detect_semantic_domain(rel_path)
        family = _domain_family(domain)
        if family == "system":
            ctx, reason = self.resolve_system_semantic_context(rel_path)
            if ctx:
                res = {
                    "context": ctx,
                    "mode": "predicted",
                    "reason": f"system-semantic:{reason}"
                }
                self._infer_cache[raw_key] = res
                return res


        if family == "vendor":
            ctx, reason = self.resolve_vendor_semantic_context(rel_path)
            if ctx:
                # Jangan override learning atau structural
                res = {
                    "context": ctx,
                    "mode": "predicted",
                    "reason": f"vendor-semantic:{reason}"
                }
                self._infer_cache[raw_key] = res
                return res


        ctx, reason = self.infer_by_filename_similarity(rel_path)
        if ctx:
            res = {
                "context": ctx,
                "mode": "predicted",
                "reason": reason
            }
            self._infer_cache[raw_key] = res
            return res


        domain = self.detect_semantic_domain(rel_path)
        family = _domain_family(domain)
        if family == "system":
            children = self._collect_children_ctx(rel_path)
            ctx = self._most_common_strict(children)

            if ctx:
                res = {
                    "context": ctx,
                    "mode": "predicted",
                    "reason": "child-statistic"
                }
                self._infer_cache[raw_key] = res
                return res


        if family == "system":
            siblings = self._collect_sibling_ctx(rel_path)
            ctx = self._most_common_strict(siblings)

            if ctx:
                res = {
                    "context": ctx,
                    "mode": "predicted",
                    "reason": "sibling-statistic"
                }
                self._infer_cache[raw_key] = res
                return res


        domain = self.detect_semantic_domain(rel_path)
        base_ctx = DEFAULT_CTX_BY_DOMAIN.get(domain, "u:object_r:system_file:s0")

        res = {
            "context": base_ctx,
            "mode": "predicted",
            "reason": "partition-fallback"
        }
        self._infer_cache[raw_key] = res
        return res

    def scan_tree(self, fs_root, mode="ai", debug=True, log_skipped=False):


        plain_ctx = None
        plain_reason = None

        if mode == "plain":
            plain_ctx, plain_reason = self.infer_plain_context(fs_root)
        if debug and (log_skipped or mode != "plain"):
            print(f"\n[START] scan fs_root='{fs_root}' mode='{mode}'")
            if mode == "plain":
                print(f"[PLAIN] base_context={plain_ctx} reason={plain_reason}")
        holes = []
        neocat_cache = self._load_all_neocat_cache()
        results = {}

        # auto detect domain by majority (boleh None)
        majority_domain = self.detect_domain_by_majority(fs_root)

        abs_root = os.path.join(self.base, fs_root)
        if not os.path.isdir(abs_root):
            return results


        root_rel = fs_root.strip("/")
        root_ctx_key = "/" + root_rel

        # family = untuk milih rule set (system vs vendor rules), berdasarkan vote
        # fc_name = nama folder as-is (dinosaurus_vendor_besar_file_contexts)
        family = majority_domain or _domain_family(self.detect_semantic_domain(root_rel))
        base_ctx = DEFAULT_CTX_BY_DOMAIN.get(family, "u:object_r:system_file:s0")

        results.setdefault(f"{root_rel}_file_contexts", []).append(
            (root_ctx_key, base_ctx, "predicted", "root-entry")
        )


        priv_app_labeled = self._has_priv_app_ctx(root_rel)
        if debug and not priv_app_labeled:
            print(f"[GUARD] priv-app: no source labels in ctx_map for '{root_rel}' — skipping all priv-app paths")


        for root, dirs, files in os.walk(abs_root):

            for d in dirs:
                full = os.path.join(root, d)
                rel = os.path.relpath(full, self.base).replace("\\", "/")
                ctx_key = self.escape_ctx_key(rel)

                # PRIV-APP GUARD: skip kalau sumber tidak punya label priv-app
                if not priv_app_labeled and self._is_priv_app_path(rel):
                    if debug and log_skipped:
                        print(f"[SKIP][PRIV-APP][DIR ] {rel}")
                    continue

                if (
                    mode == "ai"
                    and ctx_key not in self.ctx_map
                    and ctx_key not in neocat_cache
                    and not self.is_context_locked(rel)

                ):
                    holes.append(("dir", rel))
                    if debug:
                        print(f"[HOLE][DIR ] {rel}")

            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.base).replace("\\", "/")
                ctx_key = self.escape_ctx_key(rel)

                # PRIV-APP GUARD: skip kalau sumber tidak punya label priv-app
                if not priv_app_labeled and self._is_priv_app_path(rel):
                    if debug and log_skipped:
                        print(f"[SKIP][PRIV-APP][FILE] {rel}")
                    continue

                if (
                    mode == "ai"
                    and ctx_key not in self.ctx_map
                    and ctx_key not in neocat_cache
                    and not self.is_context_locked(rel)

                ):
                    holes.append(("file", rel))
                    if debug:
                        print(f"[HOLE][FILE] {rel}")


        fc_name_scan = f"{root_rel}_file_contexts"

        for kind, rel in holes:
            res = self.infer_context(rel)

            ctx_key = self.escape_ctx_key(rel)

            results.setdefault(fc_name_scan, []).append(
                (ctx_key, res["context"], res["mode"], res["reason"])
            )

            if debug:
                print(f"[AI ][{kind.upper():4}] {rel}")
                print(f"      -> ctx={res['context']}")
                print(f"      -> mode={res['mode']} reason={res['reason']}")

        return results

    def save_to_neocat_cache(self, scan_results):
        os.makedirs(self.neocat_out_dir, exist_ok=True)

        for fc_name, items in scan_results.items():
            fc_path = os.path.join(self.neocat_out_dir, fc_name)

            existing = {}
            if os.path.isfile(fc_path):
                with open(fc_path, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        s = ln.strip()
                        if not s or s.startswith("#"):
                            continue
                        k, v = s.split(None, 1)
                        k = _normalize_neobranch_ctx_key(k)
                        if not k:
                            continue
                        existing[k] = v

            added = {}

            for ctx_key, ctx, mode, reason in items:
                ctx_key = _normalize_neobranch_ctx_key(ctx_key)
                if not ctx_key:
                    continue
                if ctx_key in existing:
                    continue
                added[ctx_key] = ctx

            if not added:
                continue

            merged = dict(existing)
            merged.update(added)

            lines = [f"{k} {merged[k]}" for k in sorted(merged.keys())]

            with open(fc_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            print(f"[CACHE] {fc_name}: added {len(added)} entries")

    def apply_results(self, scan_results, dry_run=False):

        os.makedirs(self.cfgdir, exist_ok=True)

        for fc_name, items in scan_results.items():
            fc_path = os.path.join(self.cfgdir, fc_name)

            # load existing config
            existing = {}
            if os.path.isfile(fc_path):
                with open(fc_path, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        s = ln.strip()
                        if not s or s.startswith("#"):
                            continue
                        k, v = s.split(None, 1)
                        k = _normalize_neobranch_ctx_key(k)
                        if not k:
                            continue
                        existing[k] = v

            # START MERGE BASED ON EXISTING
            merged = dict(existing)

            changed = 0

            for ctx_key, ctx, mode, reason in items:
                ctx_key = _normalize_neobranch_ctx_key(ctx_key)
                if not ctx_key:
                    continue

                rel = ctx_key.lstrip("/")

                # skip locked
                if self.is_context_locked(rel):
                    continue

                # only count as change if different
                if merged.get(ctx_key) != ctx:
                    merged[ctx_key] = ctx
                    changed += 1

            if changed == 0:
                print(f"[SKIP] {fc_name}: no changes")
                continue

            # sort
            lines = [f"{k} {merged[k]}" for k in sorted(merged.keys())]

            if dry_run:
                print(f"\n>>> {fc_name} (DRY-RUN, changes={changed})")
                for ctx_key, ctx, mode, reason in items:
                    if existing.get(ctx_key) != ctx:
                        print(f"[UPDATE] {ctx_key}")
                        print(f"      ├─ context : {ctx}")
                        print(f"      ├─ mode    : {mode}")
                        print(f"      └─ reason  : {reason}")
            else:
                # newline="\n": mkfs.erofs/make_ext4fs parse strict, CRLF dari
                # text-mode default Windows bikin "Ill-formed line".
                with open(fc_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(lines) + "\n")

                print(f"[OK] {fc_name}: updated {changed} entries")

    def infer_plain_context(self, fs_root, samples=300):


        sys_count = 0
        ven_count = 0
        checked = 0

        abs_root = os.path.join(self.base, fs_root)
        if not os.path.isdir(abs_root):
            return "u:object_r:system_file:s0", "unknown-root"

        for root, _, files in os.walk(abs_root):
            for f in files:
                full = os.path.join(root, f)
                rel = "/" + os.path.relpath(full, self.base).replace("\\", "/")

                ctx = self.ctx_map.get(rel)
                if not ctx:
                    continue

                if ctx.startswith("u:object_r:system_"):
                    sys_count += 1
                elif ctx.startswith("u:object_r:vendor_"):
                    ven_count += 1

                checked += 1
                if checked >= samples:
                    break
            if checked >= samples:
                break

        if ven_count > sys_count:
            return "u:object_r:vendor_file:s0", "plain-majority-vendor"

        return "u:object_r:system_file:s0", "plain-majority-system"

    def infer_by_filename_similarity(self, rel_path):
        name = os.path.basename(rel_path)
        stem = name.replace(".sh", "").replace("-", "_")

        stem_parts = set(stem.split("_")) - GENERIC_TOKENS
        if len(stem_parts) < 2:
            return None, None  # terlalu umum, skip

        candidates = []

        for k, ctx in self.ctx_map.items():
            base = os.path.basename(k)
            base = base.replace(".sh", "").replace("-", "_")

            base_parts = set(base.split("_")) - GENERIC_TOKENS

            # hard containment
            if stem in base or base in stem:
                candidates.append(ctx)
                continue

            overlap = stem_parts & base_parts
            if len(overlap) >= 2:
                candidates.append(ctx)

        if not candidates:
            return None, None

        return self._most_common(candidates), "filename-similarity"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage:")
        print("  python context_patcher.py <base> <fs_root> [--plain] [--apply]")
        sys.exit(1)

    base = sys.argv[1]
    fs_root = sys.argv[2]

    mode = "plain" if "--plain" in sys.argv else "ai"
    apply = "--apply" in sys.argv

    print(f"neocat Model 1.0 - Context Patcher")
    print(f"CLOSED SOURCE - INTERNAL TESTING ONLY, DO NOT DISTRIBUTE!")
    print(f"Created by Renns Project - @ahsanihlwn")
    print(f"")
    print(f"[INFO] project_base = {base}")
    print(f"[INFO] fs_root      = {fs_root}")
    print(f"[INFO] mode         = {mode}")
    print(f"[INFO] apply        = {apply}")

    cp = ContextPatcher(base)
    result = cp.scan_tree(fs_root, mode=mode)

    if apply:


        cache_exists = False

        cache_files = [
            f for f in os.listdir(cp.neocat_out_dir)
            if f.endswith("_file_contexts")
        ]

        if cache_files:
            cache_exists = True
        else:
            cache_exists = False


        if cache_exists:
            print("[INFO] Loading from neocat_contexts_out (cache mode)")
            cached_results = {}

            for fc_name in os.listdir(cp.neocat_out_dir):
                if not fc_name.endswith("_file_contexts"):
                    continue

                cached_data = cp._load_neocat_cache(fc_name)
                cached_results[fc_name] = [
                    (k, v, "cached", "neocat-cache")
                    for k, v in cached_data.items()
                ]

            cp.apply_results(cached_results, dry_run=False)

        else:
            print("[INFO] No neocat cache found, running fresh inference")
            cp.apply_results(result, dry_run=False)

    else:


        cp.save_to_neocat_cache(result)
