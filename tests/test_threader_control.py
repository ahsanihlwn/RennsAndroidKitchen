# SPDX-License-Identifier: MPL-2.0
"""Tests for hardware-aware threader_control and conflict-aware parallel TaskQueue."""
import time
import unittest
from pathlib import Path
import tempfile

from PySide6.QtCore import QCoreApplication

from core.task_queue import TaskItem, TaskQueue
from core.threader_control import (
    ConcurrencyLimits,
    HardwareSnapshot,
    ThreaderController,
    classify_lane,
    normalize_targets,
    targets_conflict,
)
from ui.workspace_tasks import WorkspaceTasks


class ThreaderControlUnitTests(unittest.TestCase):
    def test_paths_conflict_detects_same_and_ancestor_descendant(self):
        a = normalize_targets(["/proj/system"])
        b = normalize_targets(["/proj/system/app/Test.apk"])
        c = normalize_targets(["/proj/vendor/app/Other.apk"])
        m1 = normalize_targets(["mount:///proj/system.img//etc/build.prop"])
        m2 = normalize_targets(["/proj/system.img"])

        self.assertTrue(targets_conflict(a, b))
        self.assertTrue(targets_conflict(b, a))
        self.assertFalse(targets_conflict(a, c))
        self.assertTrue(targets_conflict(m1, m2))

    def test_adaptive_limits_scale_down_under_pressure(self):
        ctrl = ThreaderController()
        low = HardwareSnapshot(
            logical_cores=16,
            physical_cores=8,
            cpu_percent=15.0,
            ram_total_mb=16384.0,
            ram_available_mb=10240.0,
            ram_percent=37.5,
            disk_read_mbps=5.0,
            disk_write_mbps=2.0,
            disk_busy=False,
            timestamp=1.0,
        )
        high = HardwareSnapshot(
            logical_cores=16,
            physical_cores=8,
            cpu_percent=94.0,
            ram_total_mb=16384.0,
            ram_available_mb=600.0,
            ram_percent=96.0,
            disk_read_mbps=100.0,
            disk_write_mbps=100.0,
            disk_busy=True,
            timestamp=2.0,
        )
        low_limits = ctrl.compute_limits(low)
        high_limits = ctrl.compute_limits(high)

        self.assertGreaterEqual(low_limits.heavy_slots, 3)
        self.assertGreaterEqual(low_limits.light_slots, 4)
        self.assertEqual(low_limits.cpu_pressure, "low")

        self.assertEqual(high_limits.heavy_slots, 1)
        self.assertLessEqual(high_limits.light_slots, 2)
        self.assertEqual(high_limits.cpu_pressure, "high")

    def test_classify_lane_routes_light_and_heavy_with_targets(self):
        t = normalize_targets(["/proj/foo"])
        self.assertEqual(classify_lane("Move", targets=t), "light")
        self.assertEqual(classify_lane("Delete", targets=t), "light")
        self.assertEqual(classify_lane("Unpack EROFS", targets=t), "heavy")
        self.assertEqual(classify_lane("Save etc/build.prop", targets=t), "mount_write")
        self.assertEqual(classify_lane("Checkout NeoBranch main", targets=t), "exclusive")
        self.assertEqual(classify_lane("Extracting fixture", targets=()), "serial")


class ParallelQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.ctrl = ThreaderController()
        self.ctrl.set_override_limits(
            ConcurrencyLimits(
                pool_threads=8,
                heavy_slots=2,
                light_slots=4,
                mount_write_slots=2,
                cpu_pressure="low",
            )
        )
        self.running = []

    def test_move_and_delete_run_in_parallel_while_unrelated_heavy_runs(self):
        q = TaskQueue(
            lambda worker, arg, callback: self.running.append((worker, callback)),
            controller=self.ctrl,
        )
        t_heavy = TaskItem(
            "unpack:system.img",
            lambda: "heavy_done",
            lambda ok, m: None,
            lane="heavy",
            targets=("/proj/system.img", "/proj/system"),
        )
        t_move = TaskItem(
            "Move",
            lambda: "move_done",
            lambda ok, m: None,
            lane="light",
            targets=("/proj/custom_a", "/proj/custom_b"),
        )
        t_del = TaskItem(
            "Delete",
            lambda: "del_done",
            lambda ok, m: None,
            lane="light",
            targets=("/proj/temp_junk",),
        )
        q.enqueue(t_heavy)
        q.enqueue(t_move)
        q.enqueue(t_del)

        # All 3 should launch immediately without move/delete waiting for heavy unpack!
        self.assertEqual(len(self.running), 3)
        snap = q.detailed_snapshot()
        self.assertEqual(set(snap["running"]), {"unpack:system.img", "Move", "Delete"})

        while self.running:
            w, cb = self.running.pop(0)
            cb(True, w())
        self.assertFalse(q.is_busy())

    def test_conflicting_delete_waits_for_active_heavy_on_same_subtree(self):
        q = TaskQueue(
            lambda worker, arg, callback: self.running.append((worker, callback)),
            controller=self.ctrl,
        )
        t_heavy = TaskItem(
            "unpack:system.img",
            lambda: "heavy_done",
            lambda ok, m: None,
            lane="heavy",
            targets=("/proj/system.img", "/proj/system"),
        )
        t_conflict_del = TaskItem(
            "Delete",
            lambda: "del_done",
            lambda ok, m: None,
            lane="light",
            targets=("/proj/system/priv-app",),
        )
        t_unrelated_del = TaskItem(
            "Delete unrelated",
            lambda: "del_other_done",
            lambda ok, m: None,
            lane="light",
            targets=("/proj/unrelated",),
        )

        q.enqueue(t_heavy)
        q.enqueue(t_conflict_del)
        q.enqueue(t_unrelated_del)

        # Conflicting delete must wait; unrelated delete bypasses and runs immediately!
        snap = q.detailed_snapshot()
        self.assertEqual(set(snap["running"]), {"unpack:system.img", "Delete unrelated"})
        self.assertEqual(snap["ring3"], ["Delete"])

        # Complete heavy unpack -> conflicting delete starts
        w0, cb0 = self.running.pop(0)
        cb0(True, w0())
        snap2 = q.detailed_snapshot()
        self.assertIn("Delete", snap2["running"])

        while self.running:
            w, cb = self.running.pop(0)
            cb(True, w())
        self.assertFalse(q.is_busy())

    def test_independent_heavy_jobs_respect_hardware_heavy_slots(self):
        q = TaskQueue(
            lambda worker, arg, callback: self.running.append((worker, callback)),
            controller=self.ctrl,
        )
        for part in ("system", "vendor", "product"):
            q.enqueue(
                TaskItem(
                    f"unpack:{part}",
                    lambda p=part: p,
                    lambda ok, m: None,
                    lane="heavy",
                    targets=(f"/proj/{part}.img", f"/proj/{part}"),
                )
            )
        # heavy_slots=2 -> system & vendor run concurrently, product waits in ring3
        snap = q.detailed_snapshot()
        self.assertEqual(set(snap["running"]), {"unpack:system", "unpack:vendor"})
        self.assertEqual(snap["ring3"], ["unpack:product"])

        # Finish system -> product starts
        w0, cb0 = self.running.pop(0)
        cb0(True, w0())
        snap2 = q.detailed_snapshot()
        self.assertEqual(set(snap2["running"]), {"unpack:vendor", "unpack:product"})

        while self.running:
            w, cb = self.running.pop(0)
            cb(True, w())
        self.assertFalse(q.is_busy())

    def test_mounted_image_writes_serialize_per_image_but_parallel_across_images(self):
        q = TaskQueue(
            lambda worker, arg, callback: self.running.append((worker, callback)),
            controller=self.ctrl,
        )
        t1 = TaskItem("Save sys1", lambda: "1", lambda ok, m: None, lane="mount_write", targets=("/proj/system.img",))
        t2 = TaskItem("Save sys2", lambda: "2", lambda ok, m: None, lane="mount_write", targets=("/proj/system.img",))
        t3 = TaskItem("Save ven1", lambda: "3", lambda ok, m: None, lane="mount_write", targets=("/proj/vendor.img",))
        q.enqueue(t1)
        q.enqueue(t2)
        q.enqueue(t3)

        snap = q.detailed_snapshot()
        self.assertEqual(set(snap["running"]), {"Save sys1", "Save ven1"})
        self.assertEqual(snap["ring3"], ["Save sys2"])

        while self.running:
            w, cb = self.running.pop(0)
            cb(True, w())
        self.assertFalse(q.is_busy())

    def test_workspace_tasks_runs_parallel_light_and_heavy_with_real_pool(self):
        tasks = WorkspaceTasks()
        self.addCleanup(tasks.close)
        started = []
        done_results = []
        with tempfile.TemporaryDirectory() as tmp:
            p1 = str(Path(tmp) / "heavy.img")
            p2 = str(Path(tmp) / "folder_a")

            def heavy_work():
                started.append("heavy")
                time.sleep(0.08)
                return True, "heavy-ok"

            def light_work():
                started.append("light")
                return True, "light-ok"

            tasks.enqueue("Unpack EROFS", heavy_work, lambda ok, r: done_results.append(("heavy", ok, r)), lane="heavy", targets=(p1,))
            tasks.enqueue("Move", light_work, lambda ok, r: done_results.append(("light", ok, r)), lane="light", targets=(p2,))

            deadline = time.monotonic() + 5.0
            while len(done_results) < 2 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)

            self.assertEqual(len(done_results), 2)
            # Light task should finish without waiting for heavy task
            self.assertEqual(done_results[0][0], "light")
            self.assertEqual(done_results[1][0], "heavy")


if __name__ == "__main__":
    unittest.main()
