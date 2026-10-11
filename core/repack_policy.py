# SPDX-License-Identifier: MPL-2.0
import os
import json
from core.app_paths import PROJECT_DATA_DIR
print("[CORE STAGE] GET REQUESTED REPACK POLICY")
PROJECT_DIR = PROJECT_DATA_DIR

SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")


VALID_METHODS = {
    "lz4",
    "lz4hc",
    "lzma",
    "deflate",
    "zstd",
}

LEVEL_RANGE = range(0, 10)


DEFAULT_METHOD = "lz4hc"
DEFAULT_LEVEL  = 0


def _log(msg):
    print(f"[REPACK_POLICY] {msg}")


def load_project_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_z_arg(method: str, level: int = None):

    if level is not None and level > 0:
        return f"-z{method},{level}"
    return f"-z{method}"


def build_t_arg(timestamp: int = 0):
    return f"-T {timestamp}"


def get_erofs_args():


    try:
        cfg = load_project_settings()
        erofs = cfg.get("erofs")

        if not isinstance(erofs, dict):
            raise ValueError("section 'erofs' bukan dict")

        default = erofs.get("default", True)


        if default is True:
            _log("Mode DEFAULT (json default=true)")
            return (
                build_z_arg(DEFAULT_METHOD),
                build_t_arg(DEFAULT_LEVEL),
                "default",
            )


        method = erofs.get("method")
        level  = erofs.get("level")

        if method not in VALID_METHODS:
            raise ValueError(f"method invalid: {method}")

        if not isinstance(level, int) or level not in LEVEL_RANGE:
            raise ValueError(f"level invalid: {level}")

        _log(f"Mode CUSTOM → method={method}, level={level}")
        return (
            build_z_arg(method, level),
            build_t_arg(DEFAULT_LEVEL),
            "custom",
        )

    except Exception as e:


        _log("⚠ JSON ERROR / INVALID → FALLBACK DEFAULT")
        _log(str(e))
        return (
            build_z_arg(DEFAULT_METHOD),
            build_t_arg(DEFAULT_LEVEL),
            "fallback",
        )

SELINUX_NONE  = "none"
SELINUX_AI    = "ai"
SELINUX_PLAIN = "plain"

def get_selinux_mode():

    try:
        cfg = load_project_settings()


        selinux = cfg.get("selinux")
        if isinstance(selinux, dict):
            enabled = selinux.get("enabled", False)

            if not enabled:
                _log("SELINUX PATCH = DISABLED (new format)")
                return SELINUX_NONE

            # AI neocat Model 1.0 is disabled -> always use plain context
            _log("SELINUX MODE = PLAIN (new format)")
            return SELINUX_PLAIN


        if not cfg.get("selinux_patch", True):
            _log("SELINUX PATCH = DISABLED (old format)")
            return SELINUX_NONE

        _log("SELINUX MODE = PLAIN (old format)")
        return SELINUX_PLAIN

    except Exception as e:
        _log("⚠ SELINUX MODE ERROR → FALLBACK PLAIN")
        _log(str(e))
        return SELINUX_PLAIN
