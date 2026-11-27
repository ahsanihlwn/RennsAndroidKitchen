# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "hardware"
LABEL = "Hardware bring-up"

_MAX_LIST = 8
_RE_FEATURE = re.compile(r"""<feature\s+[^>]*name\s*=\s*["']([^"']+)["']""")

_PERM_DIRS = (
    "/system/etc/permissions",
    "/system_ext/etc/permissions",
    "/product/etc/permissions",
    "/vendor/etc/permissions",
    "/odm/etc/permissions",
)

_SOUNDFX_DIRS = (
    "/vendor/lib64/soundfx", "/vendor/lib/soundfx",
    "/odm/lib64/soundfx", "/odm/lib/soundfx",
    "/system/lib64/soundfx", "/system/lib/soundfx",
    "/system_ext/lib64/soundfx", "/system_ext/lib/soundfx",
    "/product/lib64/soundfx", "/product/lib/soundfx",
    "/vendor/lib64", "/vendor/lib",
    "/system/lib64", "/system/lib",
)

_EGL_DIRS = (
    "/vendor/lib64/egl", "/vendor/lib/egl",
    "/odm/lib64/egl", "/odm/lib/egl",
    "/system/lib64/egl", "/system/lib/egl",
    "/vendor/lib64/hw", "/vendor/lib/hw",
)

_HAL_BIN_DIRS = (
    "/vendor/bin/hw", "/odm/bin/hw", "/system/bin/hw", "/system_ext/bin/hw",
    "/vendor/bin", "/odm/bin", "/system/bin",
)

_HAL_LIB_DIRS = (
    "/vendor/lib64/hw", "/vendor/lib/hw",
    "/odm/lib64/hw", "/odm/lib/hw",
    "/system/lib64/hw", "/system/lib/hw",
)


def _collect_features(vfs):
    feats = set()
    for d in _PERM_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if not name.endswith(".xml"):
                continue
            txt = vfs.read_text(posixpath.join(d, name), 131072)
            if not txt:
                continue
            for m in _RE_FEATURE.finditer(txt):
                feats.add(m.group(1).strip())
    return feats


def _collect_vintf_hals(ctx):
    """Collect declared HAL names from s3_vintf shared data or VINTF XML manifests."""
    hals = set()
    vintf = ctx.shared.get("vintf") or {}
    for k in ("device_hals", "framework_hals", "hals", "manifest_hals"):
        val = vintf.get(k)
        if isinstance(val, dict):
            hals.update(str(x) for x in val.keys())
        elif isinstance(val, (list, set, tuple)):
            for item in val:
                if isinstance(item, str):
                    hals.add(item)
                elif isinstance(item, dict) and item.get("name"):
                    hals.add(str(item["name"]))
    if hals:
        return hals

    # Fallback direct lightweight scan of vendor/odm VINTF manifest XMLs
    for root in (
        "/vendor/etc/vintf", "/vendor/etc/vintf/manifest",
        "/odm/etc/vintf", "/odm/etc/vintf/manifest",
        "/system/etc/vintf", "/system/etc/vintf/manifest",
    ):
        if not ctx.vfs.is_dir(root):
            continue
        for name in ctx.vfs.listdir(root):
            if not name.endswith(".xml"):
                continue
            txt = ctx.vfs.read_text(posixpath.join(root, name), 262144)
            if not txt:
                continue
            for m in re.finditer(r"<name>\s*([^<\s]+)\s*</name>", txt):
                hals.add(m.group(1).strip())
    return hals


def _collect_hal_files(vfs):
    bins = set()
    libs = set()
    for d in _HAL_BIN_DIRS:
        if vfs.is_dir(d):
            for name in vfs.listdir(d):
                bins.add(name.lower())
    for d in _HAL_LIB_DIRS:
        if vfs.is_dir(d):
            for name in vfs.listdir(d):
                libs.add(name.lower())
    return bins, libs


def _has_hal(vintf_hals, hal_bins, hal_libs, prefixes, bin_tokens=()):
    for h in vintf_hals:
        hl = h.lower()
        if any(hl.startswith(p) for p in prefixes):
            return True
    for b in hal_bins:
        if any(tok in b for tok in bin_tokens):
            return True
    for lib in hal_libs:
        if any(tok in lib for tok in bin_tokens):
            return True
    return False


def _audit_display(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings):
    if not ctx.has_vendor:
        return {"status": "na", "detail": "vendor not mounted"}

    init_model = (ctx.shared.get("init") or {}).get("model")
    started_svcs = list(getattr(init_model, "started_services", None) or ())
    vintf_map = (ctx.shared.get("vintf") or {}).get("provided_hals") or {}

    # ── Layer 1: Hardware Composer (IComposer / HWC) ──
    has_hwc = _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.graphics.composer",),
        ("graphics.composer", "hwcomposer"),
    )
    hwc_svcs = [
        s for s in started_svcs
        if any(k in (s.name + " " + s.path).lower() for k in ("composer", "hwcomposer", "hwc"))
    ]

    # ── Layer 2: Graphics Allocator (IAllocator) ──
    has_allocator = _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.graphics.allocator",),
        ("graphics.allocator", "gralloc"),
    )
    alloc_svcs = [
        s for s in started_svcs
        if any(k in (s.name + " " + s.path).lower() for k in ("allocator", "gralloc"))
        and "hidl.allocator" not in s.name.lower()
    ]

    # ── Layer 3: Graphics Mapper (IMapper 5.0 native or 4.0/3.0/2.0 HIDL) ──
    mapper_libs = []
    for d in ("/vendor/lib64/hw", "/vendor/lib/hw", "/odm/lib64/hw", "/vendor/lib64", "/vendor/lib"):
        if not vfs.is_dir(d):
            continue
        for nm in vfs.listdir(d):
            low = nm.lower()
            if low.endswith(".so") and (
                low.startswith("mapper.") or "graphics.mapper@" in low or low.startswith("gralloc.")
            ):
                mapper_libs.append(posixpath.join(d, nm))
    mapper_info = vintf_map.get("mapper") or {}
    mapper_fqnames = list(mapper_info.get("fqnames") or ())
    mapper5_suffixes = [fq.split("/", 1)[1] for fq in mapper_fqnames if "/" in fq]
    has_mapper = bool(mapper_libs) or _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.graphics.mapper", "mapper"),
        ("graphics.mapper", "mapper."),
    )

    # ── Layer 4: EGL / GLES Driver (ro.hardware.egl -> monolithic or split libs) ──
    egl_files = []
    egl64_bns = set()
    egl32_bns = set()
    for d in _EGL_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            low = name.lower()
            if low.endswith(".so") and (
                low.startswith("libegl_") or low.startswith("libgles")
                or low.startswith("libgl_") or "vulkan" in low
            ):
                vp = posixpath.join(d, name)
                egl_files.append(vp)
                if "/lib64/" in d:
                    egl64_bns.add(low)
                elif "/lib/" in d:
                    egl32_bns.add(low)

    egl_prop = (ctx.prop("ro.hardware.egl") or "").strip()
    vk_prop = (ctx.prop("ro.hardware.vulkan") or "").strip()
    zygote_prop = (ctx.prop("ro.zygote") or "").strip().lower()
    issues = []
    blockers = []

    if not has_hwc:
        blockers.append("graphics.composer HAL missing")
        issues.append("graphics.composer HAL missing")
        findings.append(Finding(
            KEY, "hw-display-composer-missing", BLOCKER,
            "Display Hardware Composer (HWC) HAL not found",
            "Neither android.hardware.graphics.composer in VINTF nor hwcomposer/composer binary in /vendor/bin/hw exists. "
            "SurfaceFlinger cannot initialize the display pipeline.",
            "Ensure vendor ships android.hardware.graphics.composer3/2.x HAL binary and VINTF manifest.",
            "/vendor/bin/hw",
        ))

    if not has_allocator:
        blockers.append("graphics.allocator HAL missing")
        issues.append("graphics.allocator HAL missing")
        findings.append(Finding(
            KEY, "hw-display-allocator-missing", BLOCKER,
            "Graphics Allocator (IAllocator / gralloc) HAL not found",
            "No android.hardware.graphics.allocator declaration or binary found in vendor. "
            "SurfaceFlinger and BootAnimation cannot allocate GraphicBuffers.",
            "Ship android.hardware.graphics.allocator AIDL/HIDL service and VINTF fragment in /vendor.",
            "/vendor/bin/hw",
        ))

    if not has_mapper:
        blockers.append("graphics.mapper HAL missing")
        issues.append("graphics.mapper HAL missing")
        findings.append(Finding(
            KEY, "hw-display-mapper-missing", BLOCKER,
            "Graphics Mapper (IMapper) library not found",
            "No mapper.<suffix>.so or android.hardware.graphics.mapper@*-impl.so found in /vendor/lib*/hw.",
            "Ship the matching Mapper 5.0 / 4.0 library in /vendor/lib64/hw.",
            "/vendor/lib64/hw",
        ))
    elif mapper5_suffixes:
        # Verify every declared Mapper 5.0 `@5.0/<suffix>` has `/vendor/lib64/hw/mapper.<suffix>.so`
        missing_m5 = []
        lib64_hw_bns = {posixpath.basename(p).lower() for p in mapper_libs if "/lib64/" in p}
        for suf in mapper5_suffixes:
            want = f"mapper.{suf.lower()}.so"
            if want not in lib64_hw_bns and not vfs.is_file(f"/vendor/lib64/hw/{want}"):
                missing_m5.append(f"{want} (for VINTF mapper@5.0/{suf})")
        if missing_m5:
            blockers.append("Mapper 5.0 library mismatch")
            issues.append("Mapper 5.0 library mismatch")
            findings.append(Finding(
                KEY, "hw-mapper5-lib-missing", BLOCKER,
                f"VINTF declares native Mapper 5.0, but library is missing: {', '.join(missing_m5)}",
                "GraphicBufferMapper::preloadHal() loads /vendor/lib64/hw/mapper.<suffix>.so matching "
                "VINTF/IAllocator::getIMapperLibrarySuffix(). Missing this .so crashes SurfaceFlinger immediately.",
                "Place the matching mapper.<suffix>.so in /vendor/lib64/hw or align the VINTF manifest.",
                "/vendor/lib64/hw",
            ))

    egl_resolved = ""
    if egl_prop and egl_prop.lower() not in ("emulation", "swiftshader", "angle"):
        suf = egl_prop.lower()
        found_basenames = {posixpath.basename(p).lower() for p in egl_files}
        mono = f"libgles_{suf}.so"
        split_set = {f"libegl_{suf}.so", f"libglesv1_cm_{suf}.so", f"libglesv2_{suf}.so"}
        if mono in found_basenames:
            egl_resolved = mono
        elif split_set.issubset(found_basenames):
            egl_resolved = f"libEGL_{egl_prop}.so + libGLESv1_CM/v2"
        elif any(suf in b for b in found_basenames):
            egl_resolved = ", ".join(sorted(b for b in found_basenames if suf in b)[:3])
        else:
            if egl_files:
                issues.append(f"ro.hardware.egl={egl_prop} driver mismatch")
                findings.append(Finding(
                    KEY, "hw-egl-driver-mismatch", WARN,
                    f"ro.hardware.egl={egl_prop} does not match vendor EGL driver files",
                    f"Expected libGLES_{egl_prop}.so or libEGL_{egl_prop}.so + libGLESv2_{egl_prop}.so; found: "
                    + ", ".join(sorted(found_basenames)[:8]),
                    "Match ro.hardware.egl to the actual GPU driver library shipped in /vendor/lib64/egl.",
                    "/vendor/lib64/egl",
                ))
        if "32" in zygote_prop and egl64_bns and not egl32_bns:
            issues.append("32-bit EGL drivers missing for zygote64_32")
            findings.append(Finding(
                KEY, "hw-egl-32bit-missing", WARN,
                f"ro.zygote={zygote_prop} enables 32-bit apps, but /vendor/lib/egl has no 32-bit GPU drivers",
                "64-bit EGL drivers exist in /vendor/lib64/egl, but 32-bit apps will crash on EGL init without /vendor/lib/egl.",
                "Ship matching 32-bit EGL/GLES libraries in /vendor/lib/egl or switch to zygote64-only.",
                "/vendor/lib/egl",
            ))

    if not egl_files:
        issues.append("no vendor EGL/GLES driver")
        findings.append(Finding(
            KEY, "hw-egl-missing", WARN,
            "No vendor GPU EGL/GLES driver (.so) found in /vendor/lib*/egl",
            "Graphics rendering will fall back to software or crash SurfaceFlinger/HWUI.",
            "Include vendor GPU drivers in /vendor/lib64/egl and /vendor/lib/egl.",
            "/vendor/lib64/egl",
        ))

    # ── Layer 5: Vulkan ICD (ro.hardware.vulkan -> vulkan.<val>.so) ──
    vk_libs = [p for p in egl_files if "vulkan" in posixpath.basename(p).lower()]
    vk_bns = {posixpath.basename(p).lower() for p in vk_libs}
    wants_vulkan = any(f.startswith("android.hardware.vulkan") for f in features)
    if vk_prop and vk_prop.lower() not in ("pastel", "swiftshader"):
        want_vk = f"vulkan.{vk_prop.lower()}.so"
        if want_vk not in vk_bns and not any(vk_prop.lower() in b for b in vk_bns):
            issues.append(f"ro.hardware.vulkan={vk_prop} ICD missing")
            findings.append(Finding(
                KEY, "hw-vulkan-driver-mismatch", WARN,
                f"ro.hardware.vulkan={vk_prop} expects {want_vk}, which is absent",
                f"Found Vulkan ICDs: {', '.join(sorted(vk_bns)) or '(none)'}. "
                "RenderEngine/SkiaVK/Vulkan apps will fail to load the GPU ICD.",
                f"Ship /vendor/lib64/hw/{want_vk} or align ro.hardware.vulkan in /vendor/build.prop.",
                "/vendor/lib64/hw",
            ))
    elif wants_vulkan and not vk_libs:
        issues.append("vulkan driver missing")
        findings.append(Finding(
            KEY, "hw-vulkan-missing", WARN,
            "ROM declares Vulkan feature XML, but no vulkan.*.so driver found",
            "Permissions declare android.hardware.vulkan.*, yet /vendor/lib*/hw/vulkan.*.so is absent.",
            "Ship vendor vulkan.<platform>.so in /vendor/lib64/hw or remove Vulkan feature XML.",
            "/vendor/lib64/hw",
        ))

    gfx_stack = {
        "egl_prop": egl_prop,
        "egl_resolved": egl_resolved or (", ".join(sorted(egl64_bns)[:3]) if egl64_bns else ""),
        "vulkan_prop": vk_prop,
        "vulkan_libs": sorted(vk_bns),
        "has_composer": has_hwc,
        "composer_services": [s.name for s in hwc_svcs],
        "has_allocator": has_allocator,
        "allocator_services": [s.name for s in alloc_svcs],
        "has_mapper": has_mapper,
        "mapper5_suffixes": mapper5_suffixes,
        "mapper_libs": [posixpath.basename(p) for p in mapper_libs[:8]],
    }
    if not issues:
        stack_desc = (
            f"EGL={egl_prop or '?'} ({gfx_stack['egl_resolved'] or 'ok'}) · "
            f"Vulkan={vk_prop or (sorted(vk_bns)[0] if vk_bns else 'none')} · "
            f"HWC={','.join(gfx_stack['composer_services'][:2]) or 'present'} · "
            f"Allocator={','.join(gfx_stack['allocator_services'][:2]) or 'present'} · "
            f"Mapper={','.join(mapper5_suffixes) or (gfx_stack['mapper_libs'][0] if gfx_stack['mapper_libs'] else 'ok')}"
        )
        findings.append(Finding(
            KEY, "hw-graphics-stack-ok", PASS,
            "5-layer Graphics stack coherent (EGL · Vulkan · HWC · Allocator · Mapper)",
            stack_desc,
            "", "/vendor/lib64/egl",
        ))
        return {"status": "ok", "detail": stack_desc, "graphics_stack": gfx_stack}
    return {"status": "fail" if blockers else "warn", "detail": "; ".join(issues), "graphics_stack": gfx_stack}


def _audit_audio(ctx, vfs, vintf_hals, hal_bins, hal_libs, findings):
    if not ctx.has_vendor:
        return {"status": "na", "detail": "vendor not mounted"}

    has_audio_hal = _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.audio",),
        ("hardware.audio", "audio.service", "audio.primary"),
    )

    # Check audio_policy_configuration*.xml
    policy_paths = []
    for d in ("/vendor/etc", "/vendor/etc/audio", "/odm/etc", "/system/etc"):
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if name.startswith("audio_policy_configuration") and name.endswith(".xml"):
                policy_paths.append(posixpath.join(d, name))

    # Build soundfx .so inventory
    soundfx_sos = set()
    for d in _SOUNDFX_DIRS:
        if vfs.is_dir(d):
            for name in vfs.listdir(d):
                if name.endswith(".so"):
                    soundfx_sos.add(name)

    # Parse audio_effects*.xml and verify referenced <library path="..."/>
    effects_paths = []
    for d in ("/vendor/etc", "/odm/etc", "/system/etc"):
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if name.startswith("audio_effects") and name.endswith(".xml"):
                effects_paths.append(posixpath.join(d, name))

    missing_fx = []
    fx_checked = 0
    for ep in effects_paths:
        raw = vfs.read_text(ep, 262144)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw.lstrip("\ufeff"))
        except ET.ParseError:
            # Syntax error itself is reported by s15_configs; track here for dashboard
            missing_fx.append(f"{ep} (malformed XML)")
            continue
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag != "library":
                continue
            path_attr = (elem.attrib.get("path") or "").strip()
            if not path_attr or not path_attr.endswith(".so"):
                continue
            fx_checked += 1
            base = posixpath.basename(path_attr)
            if base not in soundfx_sos and not vfs.is_file(path_attr):
                missing_fx.append(f"{base} (from {ep})")

    issues = []
    if not policy_paths and has_audio_hal:
        issues.append("audio_policy_configuration.xml missing")
        findings.append(Finding(
            KEY, "hw-audio-policy-missing", WARN,
            "Audio HAL present, but no audio_policy_configuration*.xml found",
            "audioserver needs audio_policy_configuration.xml (or AIDL audio_policy_engine_configuration) to route playback/capture.",
            "Verify audio policy XML files under /vendor/etc.",
            "/vendor/etc",
        ))
    if missing_fx:
        issues.append(f"{len(missing_fx)} missing audio effect lib(s)")
        sample = "; ".join(missing_fx[:_MAX_LIST])
        more = f" (+{len(missing_fx) - _MAX_LIST} more)" if len(missing_fx) > _MAX_LIST else ""
        findings.append(Finding(
            KEY, "hw-audio-effect-missing", WARN,
            f"{len(missing_fx)} audio effect librar(ies) referenced by audio_effects.xml are missing",
            f"{sample}{more}. audioserver will log EffectFactory load failures and drop DSP/eq effects.",
            "Copy the missing .so libraries into /vendor/lib64/soundfx (and /vendor/lib/soundfx if 32-bit), or remove stale entries from audio_effects.xml.",
            effects_paths[0] if effects_paths else "/vendor/etc",
        ))

    if issues:
        return {"status": "warn", "detail": "; ".join(issues)}
    if not has_audio_hal and not policy_paths and not effects_paths:
        return {"status": "na", "detail": "no audio config"}
    return {
        "status": "ok",
        "detail": f"Audio HAL OK · {len(policy_paths)} policy XML · {fx_checked} soundfx lib(s) verified",
    }


def _audit_camera_media(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings):
    if not ctx.has_vendor:
        return {"status": "na", "detail": "vendor not mounted"}

    issues = []
    wants_cam = any(
        f in features for f in ("android.hardware.camera", "android.hardware.camera.any")
    )
    has_cam_hal = _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.camera.provider",),
        ("camera.provider", "camerahalserver", "camera@"),
    )
    if wants_cam and not has_cam_hal:
        issues.append("camera.provider HAL missing")
        findings.append(Finding(
            KEY, "hw-camera-hal-missing", WARN,
            "Camera feature declared, but no camera.provider HAL found",
            "Permissions declare android.hardware.camera*, yet no android.hardware.camera.provider HAL exists in VINTF or /vendor/bin/hw.",
            "Include vendor camera.provider HAL service and VINTF manifest.",
            "/vendor/bin/hw",
        ))

    # Media codecs & <Include href="..."> integrity
    codec_xmls = []
    for d in ("/vendor/etc", "/odm/etc", "/system/etc"):
        p = posixpath.join(d, "media_codecs.xml")
        if vfs.is_file(p):
            codec_xmls.append(p)

    missing_includes = []
    for cp in codec_xmls:
        raw = vfs.read_text(cp, 262144)
        if not raw:
            continue
        cdir = posixpath.dirname(cp)
        try:
            root = ET.fromstring(raw.lstrip("\ufeff"))
        except ET.ParseError:
            continue
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag != "Include":
                continue
            href = (elem.attrib.get("href") or "").strip()
            if not href:
                continue
            # Android MediaCodecList searches same dir, /vendor/etc, /odm/etc, /system/etc, /apex/com.android.media.swcodec/etc
            candidates = (
                [href] if href.startswith("/") else [
                    posixpath.join(cdir, href),
                    f"/vendor/etc/{href}",
                    f"/odm/etc/{href}",
                    f"/system/etc/{href}",
                    f"/apex/com.android.media.swcodec/etc/{href}",
                ]
            )
            if not any(vfs.is_file(c) for c in candidates):
                # Also check inside media.swcodec APEX files list if indexed
                sw_files = ctx.apex.files.get("com.android.media.swcodec") or ()
                if not any(f.endswith("/" + href) or f == f"etc/{href}" for f in sw_files):
                    missing_includes.append(f"{href} (included by {cp})")

    if missing_includes:
        issues.append(f"{len(missing_includes)} missing media_codecs include(s)")
        findings.append(Finding(
            KEY, "hw-media-codecs-include-missing", WARN,
            f"{len(missing_includes)} media_codecs.xml <Include> target(s) not found",
            "; ".join(missing_includes[:_MAX_LIST]),
            "Copy the missing media_codecs_*.xml include file into /vendor/etc or remove the broken <Include>.",
            codec_xmls[0] if codec_xmls else "/vendor/etc/media_codecs.xml",
        ))

    # media.settings.xml property target check (Android falls back to /vendor/etc/media_profiles_V1_0.xml or media_profiles.xml)
    media_settings = (ctx.prop("media.settings.xml") or "").strip()
    if media_settings and media_settings.startswith("/") and not vfs.is_file(media_settings):
        has_fallback_profile = any(
            vfs.is_file(p) for p in (
                "/vendor/etc/media_profiles_V1_0.xml",
                "/vendor/etc/media_profiles.xml",
                "/odm/etc/media_profiles_V1_0.xml",
                "/system/etc/media_profiles_V1_0.xml",
            )
        )
        if not has_fallback_profile:
            issues.append(f"media.settings.xml missing ({media_settings})")
            findings.append(Finding(
                KEY, "hw-media-profiles-missing", WARN,
                f"media.settings.xml points to missing file: {media_settings}",
                "CamcorderProfile / MediaProfiles will fail to load device recording profiles (no fallback media_profiles*.xml found).",
                "Place the media_profiles XML at the configured path or update media.settings.xml.",
                media_settings,
            ))

    if issues:
        return {"status": "warn", "detail": "; ".join(issues)}
    return {
        "status": "ok",
        "detail": ("Camera HAL OK · " if has_cam_hal else "") + f"{len(codec_xmls)} media_codecs.xml verified",
    }


def _audit_ril(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings):
    if not ctx.has_vendor:
        return {"status": "na", "detail": "vendor not mounted"}

    ril_lib = (ctx.prop("vendor.rild.libpath") or ctx.prop("rild.libpath") or "").strip()
    wants_tel = "android.hardware.telephony" in features
    has_radio = _has_hal(
        vintf_hals, hal_bins, hal_libs,
        ("android.hardware.radio", "vendor.mediatek.hardware.mtkradioex", "vendor.qti.hardware.radio"),
        ("rild", "radio.service", "mtkrild", "qcrild"),
    )
    if not wants_tel and not ril_lib and not has_radio:
        return {"status": "na", "detail": "Wi-Fi-only / no telephony"}

    issues = []
    # Modern Qualcomm/AIDL devices use qcrild/radio HAL directly and ignore legacy rild.libpath if /vendor/bin/rild is unused.
    uses_qcrild = any("qcrild" in b or "qcril" in b for b in hal_bins) or any(
        vfs.is_file(p) for p in ("/vendor/lib64/libqcrilNr.so", "/vendor/bin/hw/qcrild", "/vendor/bin/qcrild")
    )
    if ril_lib and ril_lib.startswith("/") and not vfs.is_file(ril_lib) and not (has_radio and uses_qcrild):
        issues.append(f"rild.libpath missing ({ril_lib})")
        findings.append(Finding(
            KEY, "hw-ril-lib-missing", WARN,
            f"RIL library configured by rild.libpath does not exist: {ril_lib}",
            "rild cannot dlopen the vendor modem library — cellular/SIM/baseband will stay dead.",
            "Copy the vendor RIL .so to the configured path or fix vendor.rild.libpath / rild.libpath.",
            ril_lib,
        ))
    if wants_tel and not has_radio and not ril_lib:
        issues.append("radio HAL missing")
        findings.append(Finding(
            KEY, "hw-radio-hal-missing", WARN,
            "Telephony feature declared, but no Radio/RIL HAL found",
            "android.hardware.telephony is enabled in permissions, yet neither android.hardware.radio VINTF HAL nor rild binary exists.",
            "Include vendor Radio HAL (qcrild/mtkrild/android.hardware.radio) or disable telephony feature.",
            "/vendor/bin/hw",
        ))

    if issues:
        return {"status": "warn", "detail": "; ".join(issues)}
    return {"status": "ok", "detail": "Radio/RIL HAL OK" + (f" ({posixpath.basename(ril_lib)})" if ril_lib else "")}


def _audit_connectivity_biometrics(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings):
    if not ctx.has_vendor:
        return (
            {"status": "na", "detail": "vendor not mounted"},
            {"status": "na", "detail": "vendor not mounted"},
        )

    conn_issues = []
    if "android.hardware.wifi" in features:
        has_wifi = _has_hal(
            vintf_hals, hal_bins, hal_libs,
            ("android.hardware.wifi",),
            ("hardware.wifi", "wpa_supplicant", "hostapd", "wificond"),
        )
        if not has_wifi:
            conn_issues.append("Wi-Fi HAL missing")
            findings.append(Finding(
                KEY, "hw-wifi-hal-missing", WARN,
                "Wi-Fi feature declared, but no Wi-Fi HAL / wpa_supplicant found",
                "android.hardware.wifi feature XML is present without an android.hardware.wifi HAL or wpa_supplicant binary.",
                "Ensure vendor Wi-Fi HAL and wpa_supplicant are installed.",
                "/vendor/bin/hw",
            ))

    if "android.hardware.bluetooth" in features:
        has_bt = _has_hal(
            vintf_hals, hal_bins, hal_libs,
            ("android.hardware.bluetooth",),
            ("hardware.bluetooth", "bluetooth@"),
        )
        if not has_bt:
            conn_issues.append("Bluetooth HAL missing")
            findings.append(Finding(
                KEY, "hw-bluetooth-hal-missing", WARN,
                "Bluetooth feature declared, but no Bluetooth HAL found",
                "android.hardware.bluetooth feature XML is present without an android.hardware.bluetooth HAL.",
                "Ensure vendor Bluetooth HAL service/library is installed.",
                "/vendor/bin/hw",
            ))

    bio_issues = []
    if "android.hardware.fingerprint" in features:
        has_fp = _has_hal(
            vintf_hals, hal_bins, hal_libs,
            ("android.hardware.biometrics.fingerprint",),
            ("fingerprint", "biometrics.fingerprint"),
        )
        if not has_fp:
            bio_issues.append("Fingerprint HAL missing")
            findings.append(Finding(
                KEY, "hw-fingerprint-hal-missing", WARN,
                "Fingerprint feature declared, but no Fingerprint HAL found",
                "android.hardware.fingerprint feature XML is declared without an android.hardware.biometrics.fingerprint HAL.",
                "Ship vendor fingerprint HAL or remove android.hardware.fingerprint.xml.",
                "/vendor/bin/hw",
            ))

    conn_res = (
        {"status": "warn", "detail": "; ".join(conn_issues)}
        if conn_issues
        else {"status": "ok", "detail": "Wi-Fi & Bluetooth HALs OK"}
    )
    bio_res = (
        {"status": "warn", "detail": "; ".join(bio_issues)}
        if bio_issues
        else (
            {"status": "ok", "detail": "Biometrics HAL OK"}
            if "android.hardware.fingerprint" in features
            else {"status": "na", "detail": "not declared"}
        )
    )
    return conn_res, bio_res


def _gpu_family_from_userspace(ctx, vfs):
    fams = set()
    egl_prop = (ctx.prop("ro.hardware.egl") or "").strip().lower()
    if "adreno" in egl_prop:
        fams.add("adreno")
    elif any(t in egl_prop for t in ("mali", "meow")):
        fams.add("mali")
    elif "powervr" in egl_prop or "pvr" in egl_prop:
        fams.add("powervr")

    for d in ("/vendor/lib64/egl", "/vendor/lib/egl", "/odm/lib64/egl"):
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            low = name.lower()
            if not low.endswith(".so"):
                continue
            if "adreno" in low:
                fams.add("adreno")
            elif "mali" in low or "meow" in low:
                fams.add("mali")
            elif "powervr" in low or "pvr" in low:
                fams.add("powervr")
    return fams


def _gpu_family_from_kernel(ctx):
    fams = set()
    khw = ctx.shared.get("kernel_hardware") or {}
    for c in (khw.get("dtb_compatibles") or ()):
        cl = str(c).lower()
        if "adreno" in cl or "kgsl" in cl:
            fams.add("adreno")
        elif "mali" in cl or "mtk,mali" in cl:
            fams.add("mali")
        elif "powervr" in cl or "img,gpu" in cl:
            fams.add("powervr")
    for kn in (khw.get("ko_names") or ()):
        kl = str(kn).lower()
        if "kgsl" in kl or "adreno" in kl or "msm_kgsl" in kl:
            fams.add("adreno")
        elif "mali" in kl or "ged" == kl or "mtk_gpu" in kl:
            fams.add("mali")
        elif "pvrsrvkm" in kl:
            fams.add("powervr")
    return fams


def _audit_kernel_hw_coherence(ctx, vfs, features, vintf_hals, findings):
    """Cross-check userspace HAL/features against kernel IKCFG, DTB/DTBO compatibles & .ko drivers."""
    kcfg_wrap = ctx.shared.get("kernel_config") or {}
    kcfg = kcfg_wrap.get("flags") or {}
    kcfg_total = int(kcfg_wrap.get("total") or 0)
    khw = ctx.shared.get("kernel_hardware") or {}
    ko_names = {str(n).lower() for n in (khw.get("ko_names") or ())}
    has_kcfg = kcfg_total >= 200 and bool(kcfg)

    if not has_kcfg and not khw:
        return {"status": "na", "detail": "no kernel/DLKM data"}

    issues = []
    fatal = False

    # 1) GPU family coherence: Userspace EGL driver vs Kernel DTB/.ko driver
    us_gpu = _gpu_family_from_userspace(ctx, vfs)
    kn_gpu = _gpu_family_from_kernel(ctx)
    if us_gpu and kn_gpu and us_gpu.isdisjoint(kn_gpu):
        fatal = True
        issues.append(f"GPU mismatch (userspace={','.join(sorted(us_gpu))} vs kernel={','.join(sorted(kn_gpu))})")
        findings.append(Finding(
            KEY, "hw-gpu-kernel-mismatch", BLOCKER,
            f"Userspace GPU driver ({', '.join(sorted(us_gpu))}) conflicts with kernel/DTB GPU ({', '.join(sorted(kn_gpu))})",
            "Vendor EGL/GLES libraries target a different GPU architecture than the kernel/DTB/.ko graphics driver. "
            "SurfaceFlinger and HWUI will crash immediately during EGL initialization.",
            "Match vendor EGL blobs and ro.hardware.egl to the device SoC GPU.",
            "/vendor/lib64/egl",
        ))

    def _cfg_off(key):
        if not has_kcfg:
            return False
        v = kcfg.get(key)
        return v == "n" or v is None

    # 2) Wi-Fi kernel stack vs android.hardware.wifi
    if has_kcfg and "android.hardware.wifi" in features:
        has_wifi_ko = any(
            any(tok in kn for tok in ("cfg80211", "wlan", "wmt_drv", "qca_cld", "cnss", "wifi", "bcmdhd"))
            for kn in ko_names
        )
        if _cfg_off("CONFIG_CFG80211") and not has_wifi_ko:
            issues.append("Wi-Fi kernel driver missing (CONFIG_CFG80211=n)")
            findings.append(Finding(
                KEY, "hw-wifi-kernel-missing", WARN,
                "ROM declares Wi-Fi feature, but kernel lacks CONFIG_CFG80211 and Wi-Fi .ko",
                "IKCFG has CONFIG_CFG80211 disabled and no cfg80211/wlan/wmt_drv .ko module was found.",
                "Enable CONFIG_CFG80211=y/m in kernel or ship vendor Wi-Fi .ko modules.",
                "boot.img",
            ))

    # 3) Bluetooth kernel stack vs android.hardware.bluetooth
    if has_kcfg and "android.hardware.bluetooth" in features:
        has_bt_ko = any(
            any(tok in kn for tok in ("bluetooth", "bt_drv", "btfm", "btqca", "bt_mtk", "wmt_drv", "hci_"))
            for kn in ko_names
        )
        if _cfg_off("CONFIG_BT") and not has_bt_ko:
            issues.append("Bluetooth kernel stack missing (CONFIG_BT=n)")
            findings.append(Finding(
                KEY, "hw-bt-kernel-missing", WARN,
                "ROM declares Bluetooth feature, but kernel lacks CONFIG_BT and BT .ko",
                "IKCFG has CONFIG_BT disabled and no Bluetooth driver module was found in ramdisk/DLKM.",
                "Enable CONFIG_BT=y/m in kernel or include vendor BT .ko modules.",
                "boot.img",
            ))

    # 4) Audio kernel ALSA/ASoC vs Audio HAL
    has_audio_hal = any(h.lower().startswith("android.hardware.audio") for h in vintf_hals)
    if has_kcfg and has_audio_hal:
        has_snd_ko = any(
            any(tok in kn for tok in ("snd", "audio", "asoc", "adsp", "afe", "mtk_afe", "q6asm"))
            for kn in ko_names
        )
        if _cfg_off("CONFIG_SND") and _cfg_off("CONFIG_SOUND") and not has_snd_ko:
            issues.append("Audio kernel stack missing (CONFIG_SND=n)")
            findings.append(Finding(
                KEY, "hw-audio-kernel-missing", WARN,
                "Audio HAL present, but kernel lacks CONFIG_SND/CONFIG_SOUND and audio .ko",
                "audioserver cannot open /dev/snd/* PCM devices without ALSA/ASoC support in kernel.",
                "Enable CONFIG_SOUND=y and CONFIG_SND=y in kernel defconfig.",
                "boot.img",
            ))

    # 5) Camera V4L2/Media vs android.hardware.camera
    if has_kcfg and ("android.hardware.camera" in features or "android.hardware.camera.any" in features):
        has_cam_ko = any(
            any(tok in kn for tok in ("camera", "cam_", "videodev", "v4l2", "imgsensor", "isp", "seninf"))
            for kn in ko_names
        )
        if _cfg_off("CONFIG_MEDIA_SUPPORT") and _cfg_off("CONFIG_VIDEO_DEV") and not has_cam_ko:
            issues.append("Camera kernel V4L2/Media missing")
            findings.append(Finding(
                KEY, "hw-camera-kernel-missing", WARN,
                "Camera feature declared, but kernel lacks CONFIG_MEDIA_SUPPORT/CONFIG_VIDEO_DEV and camera .ko",
                "Camera HAL requires V4L2/ISP kernel drivers to access /dev/video* and /dev/media*.",
                "Enable CONFIG_MEDIA_SUPPORT=y and CONFIG_VIDEO_DEV=y in kernel defconfig.",
                "boot.img",
            ))

    if issues:
        return {"status": "fail" if fatal else "warn", "detail": "; ".join(issues)}

    bits = []
    if khw.get("ko_total"):
        bits.append(f"{khw['ko_total']} .ko")
    if khw.get("of_alias_count"):
        bits.append(f"{khw['of_alias_count']} OF aliases")
    if khw.get("matched_of_compatibles"):
        bits.append(f"{len(khw['matched_of_compatibles'])} DTB↔.ko matched")
    if has_kcfg:
        bits.append("IKCFG HW coherent")
    return {"status": "ok", "detail": " · ".join(bits) or "Kernel hardware coherent"}


def _audit_ueventd(ctx, vfs, findings):
    """Audit ueventd.rc syntax, device node permissions & GPU/Binder device rules."""
    ue_paths = []
    for p in ("/system/etc/ueventd.rc", "/vendor/etc/ueventd.rc", "/odm/etc/ueventd.rc"):
        if vfs.is_file(p):
            ue_paths.append(p)
    if not ue_paths:
        return {"status": "na", "detail": "no ueventd.rc found"}

    total_rules = 0
    bad_lines = []
    conflicts = []
    dev_nodes = set()

    for up in ue_paths:
        txt = vfs.read_text(up, 262144)
        if not txt:
            continue
        if "<<<<<<<" in txt and ">>>>>>>" in txt:
            conflicts.append(up)
            continue
        for lineno, raw in enumerate(txt.splitlines(), start=1):
            s = raw.split("#", 1)[0].strip()
            if not s:
                continue
            parts = s.split()
            if parts[0].startswith("/dev/") or parts[0].startswith("/sys/"):
                total_rules += 1
                dev_nodes.add(parts[0])
                # Standard ueventd rule: <path> <mode> <uid> <gid> (or /sys/... <attr> <mode> <uid> <gid>)
                mode_idx = 2 if parts[0].startswith("/sys/") and len(parts) >= 5 else 1
                if len(parts) < mode_idx + 3:
                    bad_lines.append(f"{up}:{lineno} (incomplete rule: {s[:48]})")
                    continue
                mode = parts[mode_idx]
                if not re.fullmatch(r"0?[0-7]{3,4}", mode):
                    bad_lines.append(f"{up}:{lineno} (invalid octal mode '{mode}')")

    issues = []
    if conflicts:
        issues.append("merge conflict in ueventd.rc")
        findings.append(Finding(
            KEY, "hw-ueventd-conflict", BLOCKER,
            f"Git merge conflict markers found in {', '.join(conflicts)}",
            "ueventd will fail to parse device node permissions, breaking GPU/audio/binder/sensor access.",
            "Resolve merge conflicts in ueventd.rc.",
            conflicts[0],
        ))
    if bad_lines:
        issues.append(f"{len(bad_lines)} invalid ueventd.rc rule(s)")
        findings.append(Finding(
            KEY, "hw-ueventd-syntax", WARN,
            f"{len(bad_lines)} malformed device permission rule(s) in ueventd.rc",
            "; ".join(bad_lines[:_MAX_LIST]),
            "Fix the mode/uid/gid columns in ueventd.rc (format: /dev/node 0660 user group).",
            ue_paths[0],
        ))

    if issues:
        return {"status": "fail" if conflicts else "warn", "detail": "; ".join(issues)}
    return {
        "status": "ok",
        "detail": f"{len(ue_paths)} ueventd.rc verified · {total_rules} device/sysfs rules",
    }


def run(ctx):
    findings = []
    vfs = ctx.vfs
    if not ctx.has_vendor and not ctx.has_system:
        return [Finding(KEY, "no-partitions", INFO, "No system/vendor partitions to audit hardware bring-up")]

    ctx.note("Auditing hardware bring-up (Display, Audio, Camera, RIL, Kernel HW, ueventd)…")
    features = _collect_features(vfs)
    vintf_hals = _collect_vintf_hals(ctx)
    hal_bins, hal_libs = _collect_hal_files(vfs)

    display = _audit_display(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings)
    audio = _audit_audio(ctx, vfs, vintf_hals, hal_bins, hal_libs, findings)
    cam_media = _audit_camera_media(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings)
    ril = _audit_ril(ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings)
    connectivity, biometrics = _audit_connectivity_biometrics(
        ctx, vfs, features, vintf_hals, hal_bins, hal_libs, findings
    )
    kernel_drivers = _audit_kernel_hw_coherence(ctx, vfs, features, vintf_hals, findings)
    ueventd = _audit_ueventd(ctx, vfs, findings)

    subsystems = {
        "display": display,
        "audio": audio,
        "camera_media": cam_media,
        "ril": ril,
        "connectivity": connectivity,
        "biometrics": biometrics,
        "kernel_drivers": kernel_drivers,
        "ueventd": ueventd,
    }
    ctx.shared["hardware"] = {
        "subsystems": subsystems,
        "features_count": len(features),
        "vintf_hals_count": len(vintf_hals),
    }

    bad = [k for k, v in subsystems.items() if v["status"] in ("fail", "warn")]
    ok_list = [k for k, v in subsystems.items() if v["status"] == "ok"]
    if not bad and ok_list:
        summary = " · ".join(f"{k}: {subsystems[k]['detail']}" for k in ok_list[:4])
        findings.append(Finding(
            KEY, "hw-bringup-ok", PASS,
            f"Hardware bring-up audit passed ({len(ok_list)} subsystem(s) verified)",
            summary,
        ))
    elif not bad and not ok_list:
        findings.append(Finding(
            KEY, "hw-bringup-na", INFO,
            "Hardware bring-up audit skipped (vendor partition not mounted)",
        ))

    return findings
