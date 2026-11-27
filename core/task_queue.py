# SPDX-License-Identifier: MPL-2.0
import threading
import logging
from dataclasses import dataclass, field
from contextvars import copy_context
from typing import Callable, Any, Optional
from collections import deque

from core.threader_control import classify_lane, normalize_targets, threader_control


@dataclass
class TaskItem:
    task_id: str
    worker: Callable[[], Any]
    callback: Callable[[bool, str], None]
    on_promote: Optional[Callable[[], None]] = None
    halt_if: Optional[Callable[[bool, str], bool]] = None
    sequence: int = 0
    context: Any = field(default_factory=copy_context, repr=False)
    lane: Optional[str] = None
    targets: tuple[str, ...] = ()

    def __post_init__(self):
        self.targets = normalize_targets(self.targets)
        self.lane = classify_lane(self.task_id, lane=self.lane, targets=self.targets)


class TaskQueue:
    def __init__(
        self,
        run_in_thread_fn: Callable,
        on_queue_empty: Optional[Callable] = None,
        *,
        on_halted=None,
        controller=None,
        pass_task_id: bool = False,
    ):
        self._run = run_in_thread_fn
        self._on_empty = on_queue_empty
        self._controller = controller or threader_control
        self._pass_task_id = bool(pass_task_id)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._settled = deque(maxlen=256)
        self._ring1: Optional[TaskItem] = None
        self._ring2: Optional[TaskItem] = None
        self._ring3: deque[TaskItem] = deque()
        self._active_parallel: dict[str, TaskItem] = {}
        self._cancelled = False
        self._halted = False
        self._halt_reason = ''
        self._on_halted = on_halted
        self._recovery_active = False
        self._next_sequence = 0
        self._results = deque(maxlen=256)

    # PUBLIC

    def enqueue(self, task: TaskItem):
        with self._lock:
            if self._halted and self._on_halted is None:
                raise RuntimeError('Task queue paused after a failed source restore.\n' + self._halt_reason)
            recover = self._halted
            if recover:
                self._recovery_active = True
            self._cancelled = False
            self._next_sequence += 1
            task.sequence = self._next_sequence
            self._ring3.append(task)
            print(f"[TASK_QUEUE] enqueue → ring3  | id={task.task_id} | lane={task.lane} | r3={len(self._ring3)}")
        if recover:
            self._on_halted(self._halt_reason)
        else:
            self._dispatch()

    def cancel_all(self):
        with self._lock:
            ids = []
            if self._ring1:
                ids.append(self._ring1.task_id)
            ids.extend(self._active_parallel.keys())
            if self._ring2:
                ids.append(self._ring2.task_id)
            ids.extend(t.task_id for t in self._ring3)
            for task in (self._ring1, *self._active_parallel.values(), self._ring2, *self._ring3):
                if task is not None:
                    self._results.append((task.sequence, task.task_id, False, "Task cancelled"))
                    if task.lane == "heavy" and task in self._active_parallel.values():
                        self._controller.note_heavy_finished()
            if self._ring1 and self._ring1.lane == "heavy":
                self._controller.note_heavy_finished()
            self._cancelled = True
            self._recovery_active = False
            self._ring1 = None
            self._active_parallel.clear()
            self._ring2 = None
            self._ring3.clear()
            self._settled.extend(ids)
            self._condition.notify_all()
            print(f"[TASK_QUEUE] cancel_all      | count={len(ids)} | ids={ids}")

    def resume_after_recovery(self):
        with self._lock:
            self._halted = False
            self._recovery_active = False
            self._halt_reason = ''
        self._dispatch()
        if not self.is_busy() and self._on_empty:
            self._on_empty()

    def recovery_failed(self, reason):
        with self._lock:
            self._recovery_active = False
            self._halt_reason = str(reason)
            waiting = [task for task in (self._ring2, *self._ring3) if task is not None]
            self._ring2 = None
            self._ring3.clear()
            for task in waiting:
                self._results.append((task.sequence, task.task_id, False, 'Queue halted: ' + str(reason)))
        for task in waiting:
            try:
                task.context.copy().run(task.callback, False, 'Queue halted: ' + str(reason))
            except Exception:
                logging.exception('Halted task callback failed')
        with self._condition:
            self._settled.extend(task.task_id for task in waiting)
            self._condition.notify_all()
        if self._on_empty:
            try:
                self._on_empty()
            except Exception:
                logging.exception('Task queue empty callback failed')

    def pending_count(self) -> int:
        with self._lock:
            return (
                (1 if self._ring1 else 0) +
                len(self._active_parallel) +
                (1 if self._ring2 else 0) +
                len(self._ring3)
            )

    def is_busy(self) -> bool:
        return self.pending_count() > 0 or self._recovery_active

    def result_cursor(self) -> int:
        with self._lock:
            return self._next_sequence

    def results_since(self, cursor: int) -> list:
        with self._lock:
            return [result for result in self._results if result[0] > cursor]

    def wait_for_tasks(self, task_ids, timeout=None):
        """Wait for the requested callbacks/results, independently of other work."""
        wanted = set(task_ids)
        with self._condition:
            return self._condition.wait_for(lambda: wanted.issubset(self._settled), timeout)

    def record_failure(self, task_id: str, message: str, *, sequence: int):
        with self._lock:
            for index in range(len(self._results) - 1, -1, -1):
                result = self._results[index]
                if result[0] == sequence and result[1] == task_id:
                    self._results[index] = (sequence, task_id, False, message)
                    return

    def set_on_empty(self, fn: Callable):
        self._on_empty = fn

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ring1": self._ring1.task_id if self._ring1 else None,
                "ring2": self._ring2.task_id if self._ring2 else None,
                "ring3": [t.task_id for t in self._ring3],
            }

    def detailed_snapshot(self) -> dict:
        with self._lock:
            return {
                "ring1": self._ring1.task_id if self._ring1 else None,
                "ring2": self._ring2.task_id if self._ring2 else None,
                "ring3": [t.task_id for t in self._ring3],
                "parallel": list(self._active_parallel.keys()),
                "running": ([self._ring1.task_id] if self._ring1 else []) + list(self._active_parallel.keys()),
            }

    # INTERNAL

    def _active_descriptors_locked(self):
        items = []
        if self._ring1 is not None:
            items.append((self._ring1.lane or "serial", self._ring1.targets))
        for item in self._active_parallel.values():
            items.append((item.lane or "light", item.targets))
        return items

    def _dispatch(self):
        self._promote()
        self._kick()
        self._promote()
        self._kick_parallel()

    def _promote(self):
        task = None
        with self._lock:
            if self._halted:
                return
            if self._ring2 is not None or not self._ring3:
                return
            # Only promote serial-lane tasks or tasks when no parallel bypass is active,
            # preserving exact ring1 -> ring2 -> ring3 staging order for serial tasks.
            serial_index = next((idx for idx, item in enumerate(self._ring3) if item.lane == "serial"), None)
            if serial_index is None:
                # If no serial tasks are waiting, only stage into ring2 when ring1 is free
                # AND the head item cannot run in parallel right now (or let _kick_parallel handle it).
                return
            if serial_index == 0:
                task = self._ring3.popleft()
            else:
                items = list(self._ring3)
                task = items.pop(serial_index)
                self._ring3 = deque(items)
            self._ring2 = task
            print(f"[TASK_QUEUE] promote → ring2  | id={task.task_id} | r3={len(self._ring3)}")
        if task and task.on_promote:
            try:
                task.context.copy().run(task.on_promote)
            except Exception as e:
                logging.exception('Task promotion callback failed')
                print(f"[TASK_QUEUE] on_promote error: {e}")

    def _kick(self):
        task = None
        with self._lock:
            if self._halted:
                return
            if self._ring1 is not None or self._ring2 is None:
                return
            active = self._active_descriptors_locked()
            ok, _reason = self._controller.can_admit(
                self._ring2.lane or "serial",
                self._ring2.targets,
                active,
            )
            if not ok:
                return
            task = self._ring2
            self._ring1 = task
            self._ring2 = None
            if task.lane == "heavy":
                self._controller.note_heavy_started()
            print(f"[TASK_QUEUE] kick → ring1    | id={task.task_id}")
        if task:
            arg = task.task_id if self._pass_task_id else None
            task.context.copy().run(
                self._run,
                task.worker,
                arg,
                callback=lambda ok, msg, t=task: t.context.copy().run(self._on_done, t, ok, msg),
            )

    def _kick_parallel(self):
        to_launch: list[TaskItem] = []
        with self._lock:
            if self._halted or not self._ring3:
                return
            remaining = deque()
            earlier_pending = []
            if self._ring2 is not None:
                earlier_pending.append((self._ring2.lane or "serial", self._ring2.targets))

            while self._ring3:
                candidate = self._ring3.popleft()
                lane = candidate.lane or "serial"
                if lane == "serial":
                    remaining.append(candidate)
                    earlier_pending.append((lane, candidate.targets))
                    continue

                active = self._active_descriptors_locked()
                admitted, reason = self._controller.can_admit(
                    lane,
                    candidate.targets,
                    active,
                    earlier_pending,
                )
                if admitted:
                    self._active_parallel[candidate.task_id] = candidate
                    if lane == "heavy":
                        self._controller.note_heavy_started()
                    to_launch.append(candidate)
                    print(f"[TASK_QUEUE] kick → parallel | id={candidate.task_id} | lane={lane} | active={len(self._active_parallel)}")
                else:
                    remaining.append(candidate)
                    earlier_pending.append((lane, candidate.targets))
            self._ring3 = remaining

        for task in to_launch:
            if task.on_promote:
                try:
                    task.context.copy().run(task.on_promote)
                except Exception as e:
                    logging.exception('Task promotion callback failed')
                    print(f"[TASK_QUEUE] on_promote error: {e}")
            arg = task.task_id if self._pass_task_id else None
            task.context.copy().run(
                self._run,
                task.worker,
                arg,
                callback=lambda ok, msg, t=task: t.context.copy().run(self._on_done, t, ok, msg),
            )

    def _on_done(self, task: TaskItem, ok: bool, msg: str):
        ignored = False
        halted = False
        halted_tasks = []
        with self._lock:
            in_ring1 = self._ring1 is task
            in_parallel = self._active_parallel.get(task.task_id) is task
            if self._cancelled or (not in_ring1 and not in_parallel):
                ignored = True
            else:
                if in_ring1:
                    self._ring1 = None
                if in_parallel:
                    self._active_parallel.pop(task.task_id, None)
                if task.lane == "heavy":
                    self._controller.note_heavy_finished()
                self._results.append((task.sequence, task.task_id, bool(ok), msg if isinstance(msg, str) else str(msg)))
                if task.halt_if and task.halt_if(ok, msg):
                    halted = True
                    self._halted = True
                    self._halt_reason = str(msg)
                    if self._on_halted is not None:
                        self._recovery_active = True
                    else:
                        if self._ring2:
                            halted_tasks.append(self._ring2)
                        halted_tasks.extend(self._ring3)
                        for queued in halted_tasks:
                            self._results.append((queued.sequence, queued.task_id, False, f"Queue halted: {msg}"))
                        self._ring2 = None
                        self._ring3.clear()
            print(f"[TASK_QUEUE] done            | id={task.task_id} | ok={ok}")

        if ignored:
            print(f"[TASK_QUEUE] done ignored    | id={task.task_id}")
            return

        # callback task: pop_task, UI update, dll — tapi JANGAN set_loading_finished di sini
        try:
            task.callback(ok, msg)
        except Exception as e:
            from core.command_context import failure_from_exception
            self.record_failure(task.task_id, failure_from_exception(e), sequence=task.sequence)
            logging.exception('Task completion callback failed')
            print(f"[TASK_QUEUE] task callback error: {e}")

        with self._condition:
            self._settled.append(task.task_id)
            self._condition.notify_all()

        if halted:
            if self._on_halted is not None:
                self._on_halted(str(msg))
                return
            for queued in halted_tasks:
                try:
                    queued.context.copy().run(queued.callback, False, f"Queue halted: {msg}")
                except Exception as e:
                    logging.exception('Halted task callback failed')
                    print(f"[TASK_QUEUE] halted task callback error: {e}")
            with self._condition:
                self._settled.extend(queued.task_id for queued in halted_tasks)
                self._condition.notify_all()
            return

        # Dispatch next serial and/or parallel eligible tasks
        self._dispatch()

        # baru trigger finished kalau queue beneran kosong total
        if not self.is_busy() and self._on_empty:
            try:
                self._on_empty()
            except Exception as e:
                logging.exception('Task queue empty callback failed')
                print(f"[TASK_QUEUE] on_queue_empty error: {e}")
