# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.stages import (
    s10_kernel,
    s0_device, s1_mount, s2_selinux, s3_vintf, s4_init, s11_avc, s5_linker,
    s6_runtime, s7_framework, s8_apex, s9_impact,
)

STAGES = (
    s10_kernel,
    s0_device,
    s1_mount,
    s2_selinux,
    s3_vintf,
    s4_init,
    s11_avc,
    s5_linker,
    s6_runtime,
    s7_framework,
    s8_apex,
    s9_impact,
)

__all__ = ["STAGES"]
