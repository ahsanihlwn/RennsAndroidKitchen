# SPDX-License-Identifier: MPL-2.0
"""Bounded, cursor-based Kitchen events with condition-driven waiting."""
from collections import deque
from copy import deepcopy
import threading
import time
import uuid


class AgentEvents:
    def __init__(self, capacity=512):
        self.session_id=uuid.uuid4().hex
        self._condition=threading.Condition()
        self._events=deque(maxlen=capacity)
        self._serial=0
        self._closed=False

    def publish(self, kind, data):
        with self._condition:
            if self._closed:return
            self._serial+=1
            self._events.append({'id':self._serial,'type':kind,'time':time.time(),'data':deepcopy(data)})
            self._condition.notify_all()

    def read(self, since=0, *, limit=100, types=(), wait=0):
        end=time.monotonic()+wait
        with self._condition:
            while True:
                reset=since>self._serial
                rows=[row for row in self._events if row['id']>(0 if reset else since) and
                      (not types or any(row['type']==kind or row['type'].startswith(kind+'.') for kind in types))]
                gap=reset or bool(self._events and since<self._events[0]['id']-1)
                if rows or gap or self._closed or time.monotonic()>=end:break
                self._condition.wait(end-time.monotonic())
            rows=rows[:limit]
            return {'version':1,'session_id':self.session_id,'events':deepcopy(rows),
                    'cursor':rows[-1]['id'] if rows else self._serial,'latest':self._serial,
                    'gap':gap,'closed':self._closed}

    def close(self):
        with self._condition:
            self._closed=True
            self._condition.notify_all()
