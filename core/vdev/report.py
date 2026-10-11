# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

print("[CORE STAGE] VDEV REPORT")

# severity
BLOCKER = "blocker"
WARN    = "warn"
PASS    = "pass"
INFO    = "info"

_SEV_ORDER = {BLOCKER: 0, WARN: 1, INFO: 2, PASS: 3}


@dataclass
class Finding:

    stage: str
    code: str
    severity: str
    title: str
    detail: str = ""
    hint: str = ""            # apa yang harus dilakukan user
    path: str = ""            # path virtual yang bersangkutan, kalau ada

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StageResult:
    key: str
    label: str
    findings: list = field(default_factory=list)
    ran: bool = False
    skipped_reason: str = ""
    duration_ms: int = 0

    @property
    def blockers(self) -> int:
        return sum(1 for f in self.findings if f.severity == BLOCKER)

    @property
    def warns(self) -> int:
        return sum(1 for f in self.findings if f.severity == WARN)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "duration_ms": self.duration_ms,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class Report:
    port_dir: str = ""
    profile: dict = field(default_factory=dict)   # hasil stage 0 (device info)
    mounts: list = field(default_factory=list)    # ringkasan mount table
    stages: list = field(default_factory=list)    # list[StageResult]
    options: dict = field(default_factory=dict)   # vbmeta_enabled, deep_scan, …
    error: str = ""                               # fatal — analisa gak jalan
    # {"ready": bool, "met": int, "total": int} dari stage impact — dipakai
    # panel shell untuk menentukan apakah prompt interaktif boleh dibuka.
    readiness: dict = field(default_factory=dict)

    # ── agregat ──
    @property
    def findings(self) -> list:
        out = []
        for s in self.stages:
            out.extend(s.findings)
        return out

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def blockers(self) -> int:
        return self.count(BLOCKER)

    @property
    def warns(self) -> int:
        return self.count(WARN)

    @property
    def passes(self) -> int:
        return self.count(PASS)

    def verdict(self) -> str:

        if self.error:
            return "Static checks failed to run"
        if self.blockers:
            n = self.blockers
            plural = "s" if n > 1 else ""
            return f"{n} blocker{plural} found by static checks"
        if self.warns:
            return f"No static blockers, {self.warns} item(s) need a manual look"
        return "No static blockers — not a boot guarantee"

    def sorted_findings(self) -> list:
        return sorted(
            self.findings,
            key=lambda f: (_SEV_ORDER.get(f.severity, 9), f.stage, f.code),
        )

    def to_dict(self) -> dict:
        return {
            "port_dir": self.port_dir,
            "profile": self.profile,
            "mounts": self.mounts,
            "options": self.options,
            "error": self.error,
            "summary": {
                "blocker": self.blockers,
                "warn": self.warns,
                "pass": self.passes,
                "verdict": self.verdict(),
            },
            "stages": [s.to_dict() for s in self.stages],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
