# SPDX-License-Identifier: MPL-2.0
print("[CORE STAGE] FLAG")
APP_NAME = "Renns Android Kitchen"
APP_VERSION = "1.0.0"
APP_VERSION_LABEL = "Public"
NEOCAT_VERSION = "Model 1.0"
NEOCAT_LABEL = f"Integrated with NEOCAT AI {NEOCAT_VERSION}"
APP_ICON = "icons/ic_launcher.png"

# Official needs rennsproject; absent bundles fall back to OPSC.
EDITION_OPENSOURCE = "OpenSource"
EDITION_OFFICIAL = "Official"
APP_EDITION = "Official"


UPDATE_CHANNEL_ALPHA = "alpha"
UPDATE_CHANNEL_BETA = "beta"
UPDATE_CHANNEL_PUBLIC = "public"

UPDATE_CHANNEL_MAP = {
    "Alpha": UPDATE_CHANNEL_ALPHA,
    "Beta": UPDATE_CHANNEL_BETA,
    "Public": UPDATE_CHANNEL_PUBLIC,
}

UPDATE_CHANNEL = UPDATE_CHANNEL_MAP.get(APP_VERSION_LABEL, UPDATE_CHANNEL_PUBLIC)


def parse_version(v: str):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0, 0, 0)


def compare_version(local: str, remote: str) -> int:
    l = parse_version(local)
    r = parse_version(remote)
    return (l > r) - (l < r)


def is_official():
    return APP_EDITION == EDITION_OFFICIAL


def is_opensource():
    return not is_official()


def edition_label():
    # A public checkout still works if someone selects Official without the bundle.
    from core.rennsproject_bridge import private_bundle_enabled
    return "OFCL" if private_bundle_enabled() else "OPSC"


def window_title():
    return f"{APP_NAME} {edition_label()} v{APP_VERSION} {APP_VERSION_LABEL}"


def popup_title():
    return f"{APP_NAME}"
