# SPDX-License-Identifier: MPL-2.0
"""Hardware-aware concurrency governor and target-conflict scheduler (threader_control)."""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import os
import threading
import time
from typing import Callable, Iterable, Optional

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None


VALID_LANES = frozenset({"serial", "light", "mount_write", "heavy", "exclusive"})

_LIGHT_KEYWORDS = (
    "move", "copy", "delete", "remove", "rename", "create ", "duplicate",
    "symlink", "selinux", "permissions", "mount image", "unmount",
    "space usage", "agent edit", "rak mv", "rak cp", "rak rm",
    "rak mkdir", "rak touch", "rak chmod", "rak ln", "rak sed",
)
_MOUNT_WRITE_KEYWORDS = (
    "save ", "mounted metadata", "compact ",
)
_EXCLUSIVE_KEYWORDS = (
    "neobranch", "recovering source", "preparing-neobranch", "checkout neobranch",
)


@dataclass(frozen=True)
class HardwareSnapshot:
    logical_cores: int
    physical_cores: int
    cpu_percent: float
    ram_total_mb: float
    ram_available_mb: float
    ram_percent: float
    disk_read_mbps: float
    disk_write_mbps: float
    disk_busy: bool
    timestamp: float


@dataclass(frozen=True)
class ConcurrencyLimits:
    pool_threads: int
    heavy_slots: int
    light_slots: int
    mount_write_slots: int
    cpu_pressure: str  # "low" | "moderate" | "high"


def normalize_target(target: object) -> str:
    """Normalize a workspace, host, or mount:// target path for conflict matching."""
    if not target:
        return ""
    text = str(target).strip()
    if not text:
        return ""
    if text == "*":
        return "*"
    if text.startswith("mount://"):
        image, _, _internal = text[8:].partition("//")
        text = image
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(text)))
    except (ValueError, OSError):
        return os.path.normcase(text)


def normalize_targets(targets: Optional[Iterable[object]]) -> tuple[str, ...]:
    if not targets:
        return ()
    if isinstance(targets, (str, os.PathLike)):
        targets = (targets,)
    seen = []
    for item in targets:
        norm = normalize_target(item)
        if norm and norm not in seen:
            seen.append(norm)
    return tuple(seen)


def _drive_key(path: str) -> str:
    if not path or path == "*":
        return ""
    drive, _ = os.path.splitdrive(path)
    if drive:
        return drive.upper()
    return "/"


def _is_rotational(path: str) -> bool:
    if not path or path == "*":
        return False
    try:
        from core.storage_detector import is_rotational_drive
        return bool(is_rotational_drive(path))
    except Exception:
        return False


def paths_overlap(path_a: str, path_b: str) -> bool:
    """Return True if two normalized paths are identical or ancestor/descendant."""
    if not path_a or not path_b:
        return False
    if path_a == "*" or path_b == "*":
        return True
    if path_a == path_b:
        return True
    try:
        common = os.path.commonpath((path_a, path_b))
    except ValueError:
        return False
    return common == path_a or common == path_b


def targets_conflict(targets_a: tuple[str, ...], targets_b: tuple[str, ...]) -> bool:
    """Return True if two target sets touch overlapping files, folders, or images."""
    if not targets_a or not targets_b:
        return False
    for a in targets_a:
        for b in targets_b:
            if paths_overlap(a, b):
                return True
    return False


def classify_lane(label: str, *, lane: Optional[str] = None, targets: tuple[str, ...] = ()) -> str:
    """Determine execution lane from explicit lane or operation label + targets."""
    if lane is not None:
        if lane not in VALID_LANES:
            raise ValueError(f"Unknown threader_control lane: {lane}")
        return lane
    low = (label or "").strip().lower()
    if any(tok in low for tok in _EXCLUSIVE_KEYWORDS):
        return "exclusive"
    if any(tok in low for tok in _MOUNT_WRITE_KEYWORDS):
        return "mount_write"
    if targets:
        if any(low.startswith(tok) or tok in low for tok in _LIGHT_KEYWORDS):
            return "light"
        return "heavy"
    return "serial"


class ThreaderController:
    """Central hardware-aware concurrency & dependency controller for RAK."""

    def __init__(self, *, cache_ttl: float = 0.35):
        self._lock = threading.Lock()
        self._cache_ttl = float(cache_ttl)
        self._last_snapshot: Optional[HardwareSnapshot] = None
        self._last_disk_io = None
        self._last_disk_ts = 0.0
        self._override_limits: Optional[ConcurrencyLimits] = None
        self._pool_sync_hook = None
        self._active_heavy_lock = threading.Lock()
        self._active_heavy_count = 0

        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    def register_pool_hook(self, fn) -> None:
        with self._lock:
            self._pool_sync_hook = fn

    def set_override_limits(self, limits: Optional[ConcurrencyLimits]) -> None:
        """Testing / deterministic override for concurrency limits (None = auto)."""
        with self._lock:
            self._override_limits = limits
        self._notify_pool()

    def hardware_snapshot(self, *, force: bool = False) -> HardwareSnapshot:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._last_snapshot is not None
                and (now - self._last_snapshot.timestamp) < self._cache_ttl
            ):
                return self._last_snapshot

            logical = max(1, os.cpu_count() or 2)
            physical = max(1, logical // 2)
            cpu_pct = 0.0
            ram_total_mb = 4096.0
            ram_avail_mb = 2048.0
            ram_pct = 50.0
            read_mbps = 0.0
            write_mbps = 0.0
            disk_busy = False

            if psutil is not None:
                try:
                    physical = max(1, psutil.cpu_count(logical=False) or physical)
                except Exception:
                    pass
                try:
                    cpu_pct = float(psutil.cpu_percent(interval=None))
                except Exception:
                    cpu_pct = 0.0
                try:
                    vm = psutil.virtual_memory()
                    ram_total_mb = vm.total / (1024.0 * 1024.0)
                    ram_avail_mb = vm.available / (1024.0 * 1024.0)
                    ram_pct = float(vm.percent)
                except Exception:
                    pass
                try:
                    io_now = psutil.disk_io_counters()
                    if io_now is not None:
                        if self._last_disk_io is not None and self._last_disk_ts > 0.0:
                            dt = max(0.05, now - self._last_disk_ts)
                            d_read = max(0, io_now.read_bytes - self._last_disk_io.read_bytes)
                            d_write = max(0, io_now.write_bytes - self._last_disk_io.write_bytes)
                            read_mbps = (d_read / (1024.0 * 1024.0)) / dt
                            write_mbps = (d_write / (1024.0 * 1024.0)) / dt
                            busy_ms_prev = getattr(self._last_disk_io, "busy_time", None)
                            busy_ms_now = getattr(io_now, "busy_time", None)
                            if busy_ms_prev is not None and busy_ms_now is not None:
                                util = (max(0, busy_ms_now - busy_ms_prev) / 1000.0) / dt
                                disk_busy = util >= 0.85
                            else:
                                disk_busy = (read_mbps + write_mbps) >= 350.0
                        self._last_disk_io = io_now
                        self._last_disk_ts = now
                except Exception:
                    pass

            snap = HardwareSnapshot(
                logical_cores=logical,
                physical_cores=physical,
                cpu_percent=cpu_pct,
                ram_total_mb=ram_total_mb,
                ram_available_mb=ram_avail_mb,
                ram_percent=ram_pct,
                disk_read_mbps=round(read_mbps, 2),
                disk_write_mbps=round(write_mbps, 2),
                disk_busy=disk_busy,
                timestamp=now,
            )
            self._last_snapshot = snap
            return snap

    def compute_limits(self, snapshot: Optional[HardwareSnapshot] = None) -> ConcurrencyLimits:
        with self._lock:
            if self._override_limits is not None:
                return self._override_limits
        snap = snapshot or self.hardware_snapshot()
        logical = snap.logical_cores
        physical = snap.physical_cores

        # Base heavy slots by physical/logical CPU count
        if physical <= 2:
            heavy = 1
        elif physical <= 4:
            heavy = 2
        elif physical <= 8:
            heavy = 3
        else:
            heavy = 4

        light = min(8, max(2, logical // 2))
        mount_write = min(4, max(2, physical // 2))
        pool = min(16, max(4, logical))

        pressure = "low"
        if snap.cpu_percent >= 88.0 or snap.ram_available_mb < 1024.0 or snap.ram_percent >= 92.0:
            pressure = "high"
            heavy = 1
            light = min(light, 2)
            mount_write = min(mount_write, 2)
            pool = max(2, min(pool, max(2, logical // 2)))
        elif snap.cpu_percent >= 70.0 or snap.ram_available_mb < 2048.0 or snap.disk_busy:
            pressure = "moderate"
            heavy = max(1, heavy // 2)
            light = max(2, min(light, 4))
            pool = max(4, min(pool, max(4, (logical * 3) // 4)))

        return ConcurrencyLimits(
            pool_threads=pool,
            heavy_slots=heavy,
            light_slots=light,
            mount_write_slots=mount_write,
            cpu_pressure=pressure,
        )

    def pool_limit(self) -> int:
        return self.compute_limits().pool_threads

    def _notify_pool(self) -> None:
        with self._lock:
            hook = self._pool_sync_hook
        if hook is not None:
            try:
                hook(self.pool_limit())
            except Exception:
                pass

    def sync_pool(self) -> int:
        limit = self.pool_limit()
        with self._lock:
            hook = self._pool_sync_hook
        if hook is not None:
            try:
                hook(limit)
            except Exception:
                pass
        return limit

    def note_heavy_started(self) -> None:
        with self._active_heavy_lock:
            self._active_heavy_count += 1

    def note_heavy_finished(self) -> None:
        with self._active_heavy_lock:
            self._active_heavy_count = max(0, self._active_heavy_count - 1)

    def recommend_tool_threads(
        self,
        path: Optional[str] = None,
        *,
        active_heavy: Optional[int] = None,
        max_cap: int = 64,
        hdd_cap: int = 4,
    ) -> int:
        """Compute optimal worker thread count for native/external tools (erofs, sdat, etc.)."""
        snap = self.hardware_snapshot()
        logical = snap.logical_cores
        if path and _is_rotational(path):
            cap = min(max_cap, hdd_cap)
        else:
            cap = min(max_cap, logical)

        if active_heavy is None:
            with self._active_heavy_lock:
                concurrent = max(1, self._active_heavy_count)
        else:
            concurrent = max(1, int(active_heavy))

        budget = max(1, cap // concurrent)
        if snap.cpu_percent >= 85.0:
            budget = max(1, budget // 2)
        elif snap.cpu_percent >= 65.0 and budget > 2:
            budget = max(2, (budget * 3) // 4)
        return max(1, min(cap, budget))

    def can_admit(
        self,
        lane: str,
        targets: tuple[str, ...],
        active_items: Iterable[tuple[str, tuple[str, ...]]],
        earlier_pending: Iterable[tuple[str, tuple[str, ...]]] = (),
    ) -> tuple[bool, str]:
        """Decide if a queued task can start immediately given active/pending tasks & hardware."""
        active_list = list(active_items)

        # Exclusive tasks wait until nothing else is running (or nothing conflicting if scoped).
        if lane == "exclusive":
            if not targets:
                return (len(active_list) == 0, "exclusive-global")
            for _act_lane, act_targets in active_list:
                if not act_targets or targets_conflict(targets, act_targets):
                    return (False, "exclusive-conflict")
            # Also check if an untargeted exclusive is active
            for act_lane, _act_targets in active_list:
                if act_lane == "exclusive" and not _act_targets:
                    return (False, "exclusive-active")
            return (True, "admitted")

        # If a global exclusive is running, wait for it.
        for act_lane, act_targets in active_list:
            if act_lane == "exclusive" and (not act_targets or targets_conflict(targets, act_targets)):
                return (False, "blocked-by-exclusive")

        # Legacy untargeted serial tasks serialize strictly with each other.
        if lane == "serial":
            for act_lane, act_targets in active_list:
                if act_lane == "serial":
                    return (False, "serial-busy")
                if targets and act_targets and targets_conflict(targets, act_targets):
                    return (False, "target-conflict")
            return (True, "admitted")

        # Check path/image conflicts against active tasks.
        for act_lane, act_targets in active_list:
            if targets and act_targets and targets_conflict(targets, act_targets):
                return (False, "target-conflict")

        # Preserve ordering among conflicting queued tasks so a later delete/move
        # does not leapfrog an earlier queued operation on the exact same path.
        if targets:
            for pend_lane, pend_targets in earlier_pending:
                if pend_lane == "exclusive" and not pend_targets:
                    return (False, "queued-exclusive-ahead")
                if pend_targets and targets_conflict(targets, pend_targets):
                    return (False, "queued-conflict-ahead")

        limits = self.compute_limits()
        if lane == "light":
            active_light = sum(1 for l, _ in active_list if l == "light")
            if active_light >= limits.light_slots:
                return (False, "light-limit")
            return (True, "admitted")

        if lane == "mount_write":
            active_mw = sum(1 for l, _ in active_list if l == "mount_write")
            if active_mw >= limits.mount_write_slots:
                return (False, "mount-write-limit")
            return (True, "admitted")

        if lane == "heavy":
            active_heavy = [(l, t) for l, t in active_list if l == "heavy"]
            if len(active_heavy) >= limits.heavy_slots:
                return (False, "heavy-limit")
            # Avoid HDD seek thrashing: at most 1 heavy job per rotational drive.
            for target in targets:
                if _is_rotational(target):
                    drive = _drive_key(target)
                    for _hl, ht in active_heavy:
                        if any(_drive_key(x) == drive for x in ht):
                            return (False, "hdd-heavy-busy")
            return (True, "admitted")

        return (True, "admitted")

    def diagnostics(self) -> dict:
        snap = self.hardware_snapshot(force=True)
        limits = self.compute_limits(snap)
        return {
            "hardware": {
                "logical_cores": snap.logical_cores,
                "physical_cores": snap.physical_cores,
                "cpu_percent": snap.cpu_percent,
                "ram_total_mb": round(snap.ram_total_mb, 1),
                "ram_available_mb": round(snap.ram_available_mb, 1),
                "ram_percent": snap.ram_percent,
                "disk_read_mbps": snap.disk_read_mbps,
                "disk_write_mbps": snap.disk_write_mbps,
                "disk_busy": snap.disk_busy,
            },
            "limits": {
                "pool_threads": limits.pool_threads,
                "heavy_slots": limits.heavy_slots,
                "light_slots": limits.light_slots,
                "mount_write_slots": limits.mount_write_slots,
                "cpu_pressure": limits.cpu_pressure,
            },
        }


threader_control = ThreaderController()


def get_threader_control() -> ThreaderController:
    return threader_control


_PROGRESS_SINK: contextvars.ContextVar[Optional[Callable[[int, str], None]]] = contextvars.ContextVar(
    "rak_progress_sink", default=None
)


@contextmanager
def progress_scope(sink: Optional[Callable[[int, str], None]]):
    """Bind a realtime progress callback `(percent, stage)` for the current worker execution."""
    if sink is None:
        yield
        return
    token = _PROGRESS_SINK.set(sink)
    try:
        yield
    finally:
        _PROGRESS_SINK.reset(token)


def report_progress(percent: int, stage: str = "") -> None:
    """Emit a realtime 0..100 progress update to the active worker's LoadingManager."""
    sink = _PROGRESS_SINK.get()
    if sink is None:
        return
    clamped = max(0, min(100, int(percent)))
    try:
        sink(clamped, str(stage or ""))
    except Exception:
        pass


def get_progress_reporter() -> Optional[Callable[[int, str], None]]:
    """Return a thread-safe progress callback bound to the active worker, or None."""
    sink = _PROGRESS_SINK.get()
    if sink is None:
        return None
    def _emit(percent: int, stage: str = "") -> None:
        clamped = max(0, min(100, int(percent)))
        try:
            sink(clamped, str(stage or ""))
        except Exception:
            pass
    return _emit
