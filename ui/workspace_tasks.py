# SPDX-License-Identifier: MPL-2.0
"""Original kitchen queue and loading state, without widget presentation."""
from core.command_context import CommandFailure, current_command, failure_from_exception
from PySide6.QtCore import QObject, Signal

from core.task_queue import TaskItem, TaskQueue
from main_ext.class_LoadingManager import LoadingManager
from services.async_jobs import AsyncJobs


class WorkspaceTasks(QObject):
    changed = Signal()
    drained = Signal()
    recoveryFinished = Signal(bool, str)

    def __init__(self, parent=None, *, runner=None, recovery=None):
        super().__init__(parent)
        self.jobs = AsyncJobs(self, limit=1)
        self.loading = LoadingManager(self)
        self._recovery = recovery
        self._recovering = False
        self._recovery_batch = None
        self._halt_reason = ''
        self._external=set()
        self._external_results={}
        self.queue = TaskQueue(self._run, self._queue_empty, on_halted=self._begin_recovery if recovery is not None else None)
        if recovery is not None:
            recovery.finished.connect(self._recovered)
            recovery.progress.connect(self._recovery_progress)
        self._runner = runner
        self._serial = 0
        self._entries = {}
        self._closed = False
        self.halted = False
        self.text = ''
        self.state = 'idle'
        self.pending = []
        self.history_lines = []
        self.loading.state_active.connect(self._active)
        self.loading.state_finished.connect(self._finished)
        self.loading.state_failed.connect(lambda text, history: self._set(text, 'error', [], history))
        self.loading.state_clean.connect(lambda history: self._set('', 'idle', [], history))

    @property
    def count(self):
        return len(self._entries)

    def _set(self, text, state, pending, history):
        self.text, self.state = text, state
        self.pending, self.history_lines = list(pending), list(history)
        self.changed.emit()

    def _active(self, parts, pending, history):
        self._set('  ·  '.join(parts), 'active', pending, history)

    def _finished(self, text, history, failed):
        self._set(text, 'error' if failed else 'done', [], history)

    def _run(self, worker, arg, callback):
        key = self.queue.snapshot()['ring1']
        self._start(key,worker,arg,callback)

    def _queue_empty(self):
        if not self._external:self.drained.emit()

    @property
    def busy(self):return self.queue.is_busy() or bool(self._external)

    def _start(self,key,worker,arg,callback):
        entry = self._entries[key]
        self.loading.mark_group_active(entry['batch'], entry['label'])
        if entry.get('starter') is not None:
            settled=[False]
            def completed(ok,result):
                if settled[0]:return
                settled[0]=True
                if not ok and current_command() is not None and not isinstance(result,CommandFailure):result=CommandFailure(result)
                callback(ok,result)
            try:
                entry['starter'](completed,lambda percent,stage='':self.loading.update_progress(entry['batch'],percent,stage))
            except Exception as error:completed(False,failure_from_exception(error))
            return
        if self._runner:
            self._runner(worker, arg, callback=callback)
        else:
            self.jobs.submit(key, lambda _=None: worker(), None, callback)

    def enqueue(self, label, worker, callback, *, batch=None, starter=None, immediate=False):
        if self._closed:
            return None
        if self.halted and self._recovery is None and not immediate:
            raise RuntimeError('Task queue paused after a failed source restore.\n'+self._halt_reason)
        self._serial += 1
        key = 'workspace:' + str(self._serial)
        # One batch per command retains the source manager's pending/history,
        # composite completion messages and 4-second result hold before cleanup.
        if batch is None:batch = self.loading.start_batch(label, 1, label=label)
        self._entries[key] = {'label': label, 'batch': batch, 'starter':starter}

        def run():
            try:
                result = worker()
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
                    if not result[0] and current_command() is not None and not isinstance(result[1], CommandFailure):
                        return False, CommandFailure(result[1])
                    return result
                return True, result
            except Exception as error:
                return False, failure_from_exception(error)

        def finished(ok, result):
            entry = self._entries.pop(key, None)
            if entry is None or self._closed:
                return
            was_external=key in self._external
            if was_external:
                self._external.discard(key)
                self._external_results[key]=(bool(ok),str(result))
            self.loading.report_done(batch, ok, member_name=label,
                                     detail=str(result or "") if not ok else None,task_id=key)
            from core.error_reporting import error_checkpoint, error_reported_since, report_fatal
            checkpoint = error_checkpoint()
            try:
                callback(ok, result)
            finally:
                if not ok and not error_reported_since(checkpoint, result) and str(result) != 'Cancelled' and not str(result).startswith('Queue halted:'):
                    report_fatal(label+'\n\n'+str(result), owner=self,
                                 application_error=getattr(result,'application_error',False))
            self.changed.emit()
            if was_external and not self.busy:self.drained.emit()
            # TaskQueue deliberately does not call on_empty after a halt.
            if not self._entries and self.halted and self._recovery is None:
                self.drained.emit()

        def halt(ok, result):
            self.halted=not ok and ('NEOBRANCH_RESTORE_FAILED' in str(result) or
                                   str(result).startswith('NeoBranch restore failed:'))
            if self.halted:self._halt_reason = str(result)
            return self.halted

        if immediate:
            self._external.add(key)
            self._start(key,run,None,finished)
        else:self.queue.enqueue(TaskItem(key, run, finished, halt_if=halt))
        self.changed.emit()
        return key

    def enqueue_external(self,label,starter,callback):
        """An event-driven GUI process shares the normal task/loading lifetime."""
        return self.enqueue(label,None,callback,starter=starter,immediate=True)

    def activity_snapshot(self):
        queue=self.queue.snapshot()
        groups=self.loading.get_activity()
        by_batch={row['batch_id']:row for row in groups}
        tasks=[]
        for key in [queue['ring1'],queue['ring2'],*queue['ring3'],*sorted(self._external)]:
            if key is None:continue
            entry=self._entries.get(key,{})
            group=by_batch.get(entry.get('batch'),{})
            tasks.append({'id':key,'label':entry.get('label',key),'state':'running' if key==queue['ring1'] or key in self._external else 'queued',
                          'batch_id':entry.get('batch'),'progress':group.get('progress',-1),
                          'stage':group.get('stage',''),'elapsed':group.get('elapsed',0)})
        results={row[1]:row for row in self.queue.results_since(0)}
        history=self.loading.get_history()
        for row in history:
            result=results.get(row['id'])
            if result:row['output']=str(result[3])
            elif row['id'] in self._external_results:row['output']=self._external_results[row['id']][1]
        return {'state':'recovering' if self._recovering else 'halted' if self.halted else 'running' if self.busy else 'idle',
                'busy':self.busy,'status':self.text,'halt_reason':self._halt_reason,
                'tasks':tasks,'activity':groups,'history':list(reversed(history))}

    def enqueue_batch(self,label,items):
        """One source loading group, with independently queued partition jobs."""
        items=list(items)
        if self._closed or not items:return []
        if self.halted and self._recovery is None:raise RuntimeError('Task queue paused after a failed source restore.\n'+self._halt_reason)
        batch=self.loading.start_batch('extracting',len(items),label=label)
        return [self.enqueue(member,worker,callback,batch=batch) for member,worker,callback in items]

    def clear_history(self):
        self.loading.clear_history()
        self._external_results.clear()

    def _begin_recovery(self, reason):
        if self._closed or self._recovering:return
        self.halted = True;self._recovering = True;self._halt_reason = reason
        self._recovery_batch = self.loading.start_batch('source-recovery',1,label='Recovering Source')
        self.loading.mark_group_active(self._recovery_batch,'Recovering Source')
        self._recovery.start()

    def _recovery_progress(self, text):
        if self._recovering:self._set(str(text),'active',self.pending,self.history_lines)

    def _recovered(self, ok, result):
        if self._closed or not self._recovering:return
        self._recovering = False
        self.loading.report_done(self._recovery_batch,ok,member_name='Recovering Source',detail=None if ok else str(result))
        self._recovery_batch = None
        if ok:
            self.halted = False;self._halt_reason = ''
            self.queue.resume_after_recovery()
        else:
            self._halt_reason = str(result)
            self.queue.recovery_failed(result)
            from core.error_reporting import report_fatal
            report_fatal('Source recovery failed\n\n'+str(result),owner=self,application_error=getattr(result,'application_error',False))
        self.recoveryFinished.emit(ok,str(result));self.changed.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.queue.cancel_all()
        self.jobs.stop()
        self._entries.clear()
        self._external.clear();self._external_results.clear()
        self.loading.clear_all()
