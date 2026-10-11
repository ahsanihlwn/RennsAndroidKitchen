# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "apex"
LABEL = "APEX integrity"
REQUIRES = ("system",)

_MAX_LIST = 10


def _apex_pkg_and_rel(vpath):

    parts = vpath.strip("/").split("/", 2)
    if len(parts) < 2 or parts[0] != "apex":
        return None, ""
    return parts[1], (parts[2] if len(parts) > 2 else "")


def run(ctx):
    findings = []
    apex = ctx.apex

    if not apex.apex_count:
        return [Finding(
            KEY, "no-apex", INFO,
            "No APEX could be read",
            "Either this ROM predates APEX, or none of the .apex files could be "
            "opened. Checks in this stage are skipped."
            + (f" {len(apex.failed)} failed to open." if apex.failed else ""),
        )]

    findings.append(Finding(
        KEY, "indexed", INFO,
        f"{apex.apex_count} APEX read, {len(apex.manifests)} with a manifest",
        f"{len(apex.libs)} libraries and {len(apex.public_libs)} LL-NDK/VNDK-SP "
        "entries come from inside APEX."
        + (f" {len(apex.failed)} could not be opened." if apex.failed else ""),
    ))

    # ── 1) nama paket ganda ──
    by_name = {}
    for apex_file, info in apex.manifests.items():
        name = info.get("name")
        if name:
            by_name.setdefault(name, []).append(apex_file)
    dupes = {n: f for n, f in by_name.items() if len(f) > 1}
    if dupes:
        items = "; ".join(f"{n} <- {', '.join(sorted(v))}"
                          for n, v in list(dupes.items())[:_MAX_LIST])
        findings.append(Finding(
            KEY, "duplicate-name", BLOCKER,
            f"{len(dupes)} APEX package name(s) declared more than once",
            items + ". apexd refuses to activate when two packages claim the "
            "same name, and it runs before zygote — the device will not boot.",
            "Keep only one .apex per package name.",
        ))
    else:
        findings.append(Finding(
            KEY, "names-unique", PASS,
            f"All {len(by_name)} APEX package names are unique",
        ))

    # ── 2) VNDK apex vs ro.vndk.version ──
    vndk = ctx.prop("ro.vndk.version")
    vndk_names = [n for n in by_name if n.startswith("com.android.vndk.")]
    if vndk and vndk_names:
        wanted = f"com.android.vndk.v{vndk}"
        if wanted in by_name:
            findings.append(Finding(
                KEY, "vndk-apex-ok", PASS,
                f"VNDK APEX matches ro.vndk.version ({vndk})",
                f"{wanted} is present.",
            ))
        else:
            findings.append(Finding(
                KEY, "vndk-apex-mismatch", BLOCKER,
                f"Vendor asks for VNDK {vndk}, APEX provides {', '.join(sorted(vndk_names))}",
                f"No APEX declares {wanted}. Every vendor HAL that links against "
                "VNDK resolves through that path, so they all fail to start.",
                f"Use a system whose VNDK APEX is v{vndk}, or a vendor built "
                "against the VNDK version actually shipped here.",
            ))
    elif vndk_names and not vndk:
        findings.append(Finding(
            KEY, "vndk-apex", INFO,
            f"VNDK APEX present: {', '.join(sorted(vndk_names))}",
            "ro.vndk.version is unknown (vendor not unpacked), so no comparison "
            "was made.",
        ))


    init_model = ctx.shared.get("init", {}).get("model")
    services = init_model.started_services if init_model is not None else []
    checked, missing, unknown = 0, [], []
    for s in services:
        pkg, rel = _apex_pkg_and_rel(s.path)
        if not pkg or not rel:
            continue
        if pkg not in apex.files:
            unknown.append((s, pkg))
            continue
        checked += 1
        if not apex.has_path(pkg, rel):
            missing.append((s, pkg, rel))

    if missing:
        items = "; ".join(f"{s.name} ({s.path})" for s, _p, _r in missing[:_MAX_LIST])
        crit = [m for m in missing if m[0].critical]
        findings.append(Finding(
            KEY, "apex-service-missing", BLOCKER if crit else WARN,
            f"{len(missing)} init service(s) point inside an APEX that does not contain them",
            items + (f" (+{len(missing) - _MAX_LIST} more)"
                     if len(missing) > _MAX_LIST else "")
            + ". The APEX is present but the binary is not inside it"
            + (f", and {len(crit)} of them are marked critical, which makes init "
               "reboot the device." if crit else ", so the service never starts."),
            "Use the APEX build that ships this binary, or drop the service "
            "block that refers to it.",
        ))
    elif checked:
        findings.append(Finding(
            KEY, "apex-service-ok", PASS,
            f"All {checked} init services inside APEX were found",
            "These were skipped by the init stage on purpose — APEX contents are "
            "only mounted after apexd runs. They are verified here instead.",
        ))

    if unknown:
        pkgs = sorted({p for _s, p in unknown})
        findings.append(Finding(
            KEY, "apex-service-unknown", INFO,
            f"{len(unknown)} init service(s) refer to an APEX that is not present",
            "Referenced but not found: " + ", ".join(pkgs[:_MAX_LIST])
            + ". These may ship as updatable APEX from Play, or the package is "
            "genuinely absent — not judged either way.",
        ))

    return findings
