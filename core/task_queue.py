# SPDX-License-Identifier: MPL-2.0
import threading
import logging
from dataclasses import dataclass, field
from contextvars import copy_context
from typing import Callable, Any, Optional
from collections import deque


@dataclass
class TaskItem:
    task_id: str
    worker: Callable[[], Any]
    callback: Callable[[bool, str], None]
    on_promote: Optional[Callable[[], None]] = None
    halt_if: Optional[Callable[[bool, str], bool]] = None
    sequence: int = 0
    context: Any = field(default_factory=copy_context, repr=False)


class TaskQueue:
    def __init__(self, run_in_thread_fn: Callable, on_queue_empty: Optional[Callable] = None, *, on_halted=None):
        self._run = run_in_thread_fn
        self._on_empty = on_queue_empty
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._settled = deque(maxlen=256)
        self._ring1: Optional[TaskItem] = None
        self._ring2: Optional[TaskItem] = None
        self._ring3: deque[TaskItem] = deque()
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
                raise RuntimeError('Task queue paused after a failed source restore.\n'+self._halt_reason)
            recover = self._halted
            if recover:
                self._recovery_active = True
            self._cancelled = False
            self._next_sequence += 1
            task.sequence = self._next_sequence
            self._ring3.append(task)
            print(f"[TASK_QUEUE] enqueue → ring3  | id={task.task_id} | r3={len(self._ring3)}")
        if recover:self._on_halted(self._halt_reason)
        else:self._promote();self._kick()

    def cancel_all(self):

        with self._lock:
            ids = []
            if self._ring1:
                ids.append(self._ring1.task_id)
            if self._ring2:
                ids.append(self._ring2.task_id)
            ids.extend(t.task_id for t in self._ring3)
            for task in (self._ring1, self._ring2, *self._ring3):
                if task is not None:
                    self._results.append((task.sequence, task.task_id, False, "Task cancelled"))
            self._cancelled = True
            self._recovery_active = False
            self._ring1 = None
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
        self._promote();self._kick();self._promote()
        if not self.is_busy() and self._on_empty:self._on_empty()

    def recovery_failed(self, reason):
        with self._lock:
            self._recovery_active = False
            self._halt_reason = str(reason)
            waiting = [task for task in (self._ring2,*self._ring3) if task is not None]
            self._ring2 = None;self._ring3.clear()
            for task in waiting:self._results.append((task.sequence,task.task_id,False,'Queue halted: '+str(reason)))
        for task in waiting:
            try:
                task.context.copy().run(task.callback, False, 'Queue halted: '+str(reason))
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
        wanted=set(task_ids)
        with self._condition:
            return self._condition.wait_for(lambda:wanted.issubset(self._settled),timeout)

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

    # INTERNAL

    def _promote(self):
        task = None
        with self._lock:
            if self._halted:return
            if self._ring2 is not None or not self._ring3:
                return
            task = self._ring3.popleft()
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
            if self._halted:return
            if self._ring1 is not None or self._ring2 is None:
                return
            task = self._ring2
            self._ring1 = task
            self._ring2 = None
            print(f"[TASK_QUEUE] kick → ring1    | id={task.task_id}")
        if task:
            task.context.copy().run(self._run, task.worker, None,
                callback=lambda ok, msg: task.context.copy().run(self._on_done, task, ok, msg))

    def _on_done(self, task: TaskItem, ok: bool, msg: str):
        ignored = False
        halted = False
        halted_tasks = []
        with self._lock:
            if self._cancelled or self._ring1 is not task:
                ignored = True
            else:
                self._ring1 = None
                self._results.append((task.sequence, task.task_id, bool(ok), msg if isinstance(msg,str) else str(msg)))
                if task.halt_if and task.halt_if(ok, msg):
                    halted = True
                    self._halted = True
                    self._halt_reason = str(msg)
                    if self._on_halted is not None:self._recovery_active = True
                    else:
                        if self._ring2:halted_tasks.append(self._ring2)
                        halted_tasks.extend(self._ring3)
                        for queued in halted_tasks:
                            self._results.append((queued.sequence, queued.task_id, False,f"Queue halted: {msg}"))
                        self._ring2 = None;self._ring3.clear()
            print(f"[TASK_QUEUE] done            | id={task.task_id} | ok={ok}")

        if ignored:
            print(f"[TASK_QUEUE] done ignored    | id={task.task_id}")
            return

        # callback task: pop_task, UI update, dll — tapi JANGAN set_loading_finished di sini
        try:
            task.callback(ok, msg)
        except Exception as e:
            from core.command_context import failure_from_exception
            self.record_failure(task.task_id,failure_from_exception(e),sequence=task.sequence)
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

        # lanjut ke berikutnya: promote dulu (jika ring2 kosong), kick ke ring1,
        # lalu promote lagi agar ring2 langsung terisi dari ring3 (pre-staging siap).
        self._promote()
        self._kick()
        self._promote()

        # baru trigger finished kalau queue beneran kosong total
        if not self.is_busy() and self._on_empty:
            try:
                self._on_empty()
            except Exception as e:
                logging.exception('Task queue empty callback failed')
                print(f"[TASK_QUEUE] on_queue_empty error: {e}")
