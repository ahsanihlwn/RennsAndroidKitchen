# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "impact"
LABEL = "Impact & readiness"
REQUIRES = ("system",)

_MAX_LIST = 8

# Kelas service yang ikut jalan di boot normal.
_BOOT_CLASSES = {"core", "main", "hal", "early_hal"}


def _service_index(ctx):

    out = {}
    for s in ctx.shared.get("init", {}).get("services", []):
        if s.path.startswith("/"):
            out.setdefault(s.path, s)
    return out


def _impact_chain(ctx, findings):

    linker = ctx.shared.get("linker")
    if not linker:
        return set()

    origins = linker.get("unresolved_origins") or {}
    if not origins:
        return set()

    services = _service_index(ctx)
    broken = {}                     # nama service -> set(soname)

    for soname, seeds in origins.items():
        for seed in seeds:
            svc = services.get(seed)
            if svc is not None:
                broken.setdefault(svc.name, set()).add(soname)

    if broken:
        crit, boot, other = [], [], []
        by_name = {s.name: s for s in ctx.shared.get("init", {}).get("services", [])}
        for name in sorted(broken):
            svc = by_name.get(name)
            if svc is None:
                other.append(name)
            elif svc.critical:
                crit.append(name)
            elif not svc.disabled and (not svc.klass or svc.klass in _BOOT_CLASSES):
                boot.append(name)
            else:
                other.append(name)

        detail_bits = []
        for name in (crit + boot)[:_MAX_LIST]:
            libs = sorted(broken[name])
            detail_bits.append(f"{name} <- {', '.join(libs[:2])}"
                               + (f" +{len(libs) - 2}" if len(libs) > 2 else ""))
        more = len(crit) + len(boot) - _MAX_LIST

        if crit or boot:
            findings.append(Finding(
                KEY, "services-killed", BLOCKER if crit else WARN,
                f"{len(crit) + len(boot)} boot service(s) cannot start because of "
                "missing libraries",
                "; ".join(detail_bits) + (f" (+{more} more)" if more > 0 else "")
                + ". This is the same missing-library finding from the linker "
                "stage, followed through to what actually breaks"
                + (f". {len(crit)} of them are marked critical, which makes init "
                   "reboot the device." if crit else "."),
                "Fix the libraries listed by the linker stage — each one here "
                "takes a whole service down with it.",
            ))


    return set(broken)


# prasyarat system_server
def _prereqs(ctx, killed):

    vfs = ctx.vfs
    init = ctx.shared.get("init", {})
    model = init.get("model")
    apex = ctx.apex
    out = []

    out.append(("init.rc parsed", bool(init.get("root_rc")),
                init.get("root_rc") or "no init.rc found"))


    started = model.started_services if model is not None else []
    zyg_prop = ctx.prop("ro.zygote")
    has_zyg = any(s.name.startswith("zygote") for s in started)
    zyg_basis = getattr(model, "vold_basis", "") if model is not None else ""
    if zyg_prop:
        out.append(("zygote service reached by trigger chain", has_zyg,
                    f"ro.zygote={zyg_prop}"
                    + ("" if has_zyg else " but no zygote service is ever "
                       "started by early-init/init/late-init or the "
                       "vold-decrypt gate")
                    + (f" ({zyg_basis})" if has_zyg and zyg_basis else "")))
    else:
        out.append(("zygote service reached by trigger chain", has_zyg,
                    "ro.zygote unknown (vendor not unpacked)"
                    + ("; a zygote service is reached anyway" if has_zyg else "")))

    app_proc = [p for p in ("/system/bin/app_process64", "/system/bin/app_process32",
                            "/system/bin/app_process") if vfs.is_file(p)]
    out.append(("app_process present", bool(app_proc),
                ", ".join(app_proc) or "no app_process binary"))

    for path, label in (("/system/bin/init", "init"),
                        ("/system/bin/servicemanager", "servicemanager"),
                        ("/system/bin/linkerconfig", "linkerconfig"),
                        ("/system/bin/apexd", "apexd")):
        if vfs.is_file(path):
            out.append((f"{label} present", True, path))
            continue


        pkg = next((k for k, paths in apex.files.items()
                    if f"bin/{label}" in paths), "")
        out.append((f"{label} present", bool(pkg),
                    f"{path} (in {pkg})" if pkg else path))

    out.append(("APEX available", apex.apex_count > 0,
                f"{apex.apex_count} APEX readable"))

    art = any(n.startswith("com.android.art") for n in
              {(i or {}).get("name", "") for i in apex.manifests.values()})
    out.append(("ART runtime APEX", art,
                "com.android.art present" if art else "no com.android.art APEX"))

    out.append(("framework-res.apk", vfs.is_file("/system/framework/framework-res.apk"),
                "/system/framework/framework-res.apk"))
    out.append(("services.jar", vfs.is_file("/system/framework/services.jar"),
                "/system/framework/services.jar"))

    # Library dari binary inti harus utuh — kalau zygote/servicemanager masuk
    # daftar yang mati karena library hilang, runtime tidak akan naik.
    core_killed = sorted(n for n in killed
                         if n.startswith("zygote") or n in ("servicemanager",
                                                            "hwservicemanager"))
    out.append(("core service libraries resolve", not core_killed,
                "broken: " + ", ".join(core_killed) if core_killed
                else "no core service blocked by a missing library"))

    return out


def run(ctx):
    findings = []
    killed = _impact_chain(ctx, findings)

    prereqs = _prereqs(ctx, killed)
    unmet = [(n, why) for n, ok, why in prereqs if not ok]
    met = len(prereqs) - len(unmet)

    if unmet:
        findings.append(Finding(
            KEY, "system-server-blocked", BLOCKER,
            f"system_server will not start — {len(unmet)} prerequisite(s) unmet",
            "; ".join(f"{n} ({why})" for n, why in unmet[:_MAX_LIST])
            + (f" (+{len(unmet) - _MAX_LIST} more)"
               if len(unmet) > _MAX_LIST else "")
            + f". {met} of {len(prereqs)} prerequisites are satisfied.",
            "Clear these first — anything that depends on the Java runtime "
            "being up cannot work until they are met.",
        ))
    else:
        findings.append(Finding(
            KEY, "system-server-ready", PASS,
            f"All {len(prereqs)} system_server prerequisites are satisfied",
            "init.rc parses, zygote and app_process are in place, the core "
            "daemons and ART APEX are present, and no core service is blocked "
            "by a missing library. Nothing VISIBLE stops the Java runtime from "
            "coming up — this is read from files, not from running anything, "
            "so it is the absence of known blockers, not a guarantee.",
        ))

    ctx.shared["readiness"] = {
        "ready": not unmet,
        "met": met,
        "total": len(prereqs),
        "unmet": [n for n, _ in unmet],
        "prereqs": [(n, ok, why) for n, ok, why in prereqs],
    }
    return findings
