# SPDX-License-Identifier: MPL-2.0
try:
    import shiboken6
    def _alive(obj):
        return shiboken6.isValid(obj)
except Exception:  # pragma: no cover - fallback kalau shiboken ga ada
    def _alive(obj):
        return obj is not None


class DeferredCloseViewer:


    def _ensure_dcv_state(self):
        if not hasattr(self, "_dcv_pending"):
            self._dcv_pending = 0
            self._dcv_close_requested = False

    # dipanggil viewer pas enqueue task
    def mark_task_started(self):
        self._ensure_dcv_state()
        self._dcv_pending += 1

    def has_pending_task(self) -> bool:
        self._ensure_dcv_state()
        return self._dcv_pending > 0

    # dipanggil UnifiedTabContainer pas mau close tapi task masih jalan
    def request_close_when_idle(self):

        self._ensure_dcv_state()
        self._dcv_close_requested = True
        # Edge case: race — task keburu beres sebelum flag ke-set.
        if self._dcv_pending <= 0:
            self._dcv_self_destruct()

    # bungkus callback task biar aman dari widget yang udah mati
    def guard_task_callback(self, fn):

        def _wrapped(ok, msg):
            self._ensure_dcv_state()
            try:
                if _alive(self):
                    fn(ok, msg)
                # kalau widget udah mati: task tetep dianggap beres (file udah
                # ke-extract di worker), cuma UI refresh-nya yang di-skip.
            finally:
                self._dcv_pending -= 1
                if self._dcv_pending <= 0 and self._dcv_close_requested:
                    self._dcv_self_destruct()
        return _wrapped

    def _dcv_self_destruct(self):
        if getattr(self, "_dcv_destroyed", False):
            return
        self._dcv_destroyed = True
        try:
            parent_stack = getattr(self, "_dcv_owner_stack", None)
            if parent_stack is not None and _alive(parent_stack):
                parent_stack.removeWidget(self)
        except Exception:
            pass
        try:
            self.deleteLater()
        except Exception:
            pass
