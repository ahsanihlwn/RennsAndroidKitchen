# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MPL-2.0


from PySide6.QtCore import QObject, Signal, QTimer


class _FadeWaiterThread:

    pass


class LoadingManager(QObject):


    state_active   = Signal(list, list, list)  # (parts, pending_labels, history_lines)
    state_finished = Signal(str, list, bool)   # (text, history_lines, has_fail)
    state_failed   = Signal(str, list)         # (text, history_lines)


    state_clean    = Signal(list)              # (history_lines)

    # Delay sebelum bar fade ke clean setelah finished/failed (ms)
    FADE_DONE_MS   = 1400
    FADE_FAILED_MS = 2200

    def __init__(self, parent=None):
        super().__init__(parent)

        # Single-task state
        # Stack berisi tuple (name, kind) — kind dipakai untuk format text.
        self._tasks: list = []


        self._groups: dict = {}
        self._group_order: list = []   # urutan batch_id masuk
        self._next_batch_id = 0


        self._implicit: dict = {}


        self._SERVICE_KINDS = ("unpack", "repack", "convert", "extracting")
        self._LIGHT_KINDS   = ("moving", "merging")
        # region urutan tampil: heavy dulu? tidak — light & heavy berdampingan,
        # dirakit terpisah lalu digabung dgn "  ·  ".
        self._REGIONS = ("heavy", "light")


        self._history: list = []
        # Counter SESI utk pesan komposit "N finished, M failed".
        # Direset tiap mulai dari idle (sesi kerja baru), bukan saat clear.
        self._session_ok = 0
        self._session_fail = 0

        # Fade state
        self._fade_timer: QTimer | None = None
        self._fade_generation = 0  # bumped tiap ada perubahan state

        # Terminal state
        # 'idle' | 'active' | 'finished' | 'failed'
        self._state = "idle"

    def _debug(self, event: str, **data):
        bits = " | ".join(f"{k}={v}" for k, v in data.items())
        print(f"[LOADING_RING] {event}" + (f" | {bits}" if bits else ""), flush=True)


    def push_task(self, name: str):
        kind = self._classify(name)


        if (kind in self._SERVICE_KINDS or kind in self._LIGHT_KINDS) \
                and name not in self._implicit:
            label = self._pretty_action(kind)
            bid = self.start_batch(kind, 1, label=label,
                                   region=self._region_of_kind(kind))
            self._implicit[name] = bid
            self._note_member(self._groups[bid], name)
            self._debug("implicit_start", batch=bid, kind=kind, name=self._clean_member_name(name))
            self._cancel_fade()
            self._refresh()
            return
        self._tasks.append((name, kind))
        self._cancel_fade()
        self._refresh()

    def cancel_task(self, name: str):

        bid = self._implicit.pop(name, None)
        if bid is not None:
            self._debug("implicit_cancel", batch=bid, name=self._clean_member_name(name))
            self._drop_group(bid)
            if not (self._groups or self._tasks):
                # ga ada aktivitas lain → kalau sesi sempat punya hasil, tutup
                # komposit; kalau benar2 kosong tanpa hasil, balik clean.
                if self._session_ok or self._session_fail:
                    self._emit_session_terminal()
                else:
                    self.set_clean()
            else:
                self._refresh()
            return
        # task biasa → hapus tanpa efek lain
        for i, (n, _k) in enumerate(self._tasks):
            if n == name:
                self._tasks.pop(i)
                break
        self._refresh()

    def pop_task(self, name: str, ok: bool = True):
        # Grup implisit (single service) → selesaikan, ok/fail dari caller.
        bid = self._implicit.pop(name, None)
        if bid is not None:
            self.report_done(bid, ok, member_name=name)
            return
        # Task biasa — hapus satu entry pertama yg match name
        for i, (n, _k) in enumerate(self._tasks):
            if n == name:
                self._tasks.pop(i)
                break
        self._refresh()

    def set_finished(self, text: str = "Done"):

        if self._groups or self._tasks:
            return
        self._emit_finished(text)

    def set_failed(self, text: str = "Failed"):
        # Masih ada grup/task lain → biarkan yg terakhir yg manage.
        if self._groups or self._tasks:
            return


        if self._session_ok or self._session_fail:
            return
        # Murni non-service (mis. move/merge yg gagal sebelum jadi service) →
        # boleh tampil merah seperti biasa.
        self._emit_failed(text)

    def finish_session(self):

        if self._groups or self._tasks:
            return
        self._emit_session_terminal()

    def set_clean(self):
        self._cancel_fade()
        self._state = "idle"
        # Sesi selesai total → counter komposit fresh utk sesi berikutnya.
        # History TIDAK disentuh (persisten s/d clear_history()).
        self._session_ok = 0
        self._session_fail = 0
        # Bawa history: ••• tetap tampil di bar selama history belum di-clear.
        self.state_clean.emit(self._history_lines())

    def clear_all(self):

        self._tasks.clear()
        self._groups.clear()
        self._group_order.clear()
        self._implicit.clear()
        self._session_ok = 0
        self._session_fail = 0
        self.set_clean()

    def tasks_count(self) -> int:
        return len(self._tasks)

    def is_busy(self) -> bool:
        return bool(self._tasks) or bool(self._groups)


    def start_batch(self, action: str, total: int, label: str | None = None,
                    region: str | None = None) -> int:

        self._begin_session_if_idle()
        bid = self._next_batch_id
        self._next_batch_id += 1
        if not label:
            label = self._pretty_action(action)
        if region not in ("heavy", "light"):
            region = self._region_of_kind(self._classify(action))
        self._groups[bid] = {
            "action":  action,
            "total":   max(1, int(total)),
            "ok":      0,
            "fail":    0,
            "label":   label,
            "members": [],      # nama member yg sudah ke-report (utk tooltip)
            "started": False,   # True begitu member pertama selesai/aktif
            "region":  region,  # 'heavy' | 'light'
        }
        self._group_order.append(bid)
        self._debug("batch_start", batch=bid, action=action, region=region, total=self._groups[bid]["total"], label=label)
        self._cancel_fade()
        self._refresh()
        return bid

    def mark_group_active(self, batch_id: int, member_name: str | None = None):

        g = self._groups.get(batch_id)
        if not g:
            return
        if not g["started"]:
            g["started"] = True
            self._debug("batch_active", batch=batch_id, member=self._clean_member_name(member_name or ""))
            self._refresh()
        if member_name:
            self._note_member(g, member_name)

    def report_done(self, batch_id: int, ok: bool, member_name: str | None = None):

        g = self._groups.get(batch_id)
        if not g:
            return
        self._begin_session_if_idle()
        if not g["started"]:
            self._debug("batch_active", batch=batch_id, member=self._clean_member_name(member_name or ""))
        g["started"] = True
        if member_name:
            self._note_member(g, member_name)
        if ok:
            g["ok"] += 1
        else:
            g["fail"] += 1

        # Catat ke history + counter sesi (per member)
        self._record_history(member_name or g["label"], ok, g["label"])

        done = g["ok"] + g["fail"]
        total = g["total"]
        self._debug(
            "member_done",
            batch=batch_id,
            ok=bool(ok),
            done=f"{done}/{total}",
            fail=g["fail"],
            member=self._clean_member_name(member_name or g["label"]),
        )
        if done < total:
            # Masih jalan — refresh text x/n
            self._refresh()
            return

        # Grup selesai semua — keluarin
        self._drop_group(batch_id)
        self._debug("batch_done", batch=batch_id, ok=g["ok"], fail=g["fail"])

        # Kalau masih ada grup/task lain → JANGAN emit terminal.
        if self._groups or self._tasks:
            self._refresh()
            return

        # Semua settle → terminal komposit netral (failed TIDAK bikin merah)
        self._emit_session_terminal()

    def cancel_batch(self, batch_id: int):

        if batch_id in self._groups:
            self._debug("batch_cancel", batch=batch_id)
            self._drop_group(batch_id)
            self._refresh()


    def _drop_group(self, batch_id: int):
        self._groups.pop(batch_id, None)
        try:
            self._group_order.remove(batch_id)
        except ValueError:
            pass
        # Buang mapping implicit yg nunjuk ke grup ini (kalau ada)
        for nm, bid in list(self._implicit.items()):
            if bid == batch_id:
                self._implicit.pop(nm, None)

    def _clean_member_name(self, name: str) -> str:

        short = str(name)
        if ":" in short and "/" not in short.split(":", 1)[0] and "\\" not in short.split(":", 1)[0]:
            short = short.split(":", 1)[1]
        try:
            import os
            short = os.path.basename(short.rstrip("/\\")) or short
        except Exception:
            pass
        return short

    def _note_member(self, group: dict, name: str):
        short = self._clean_member_name(name)
        if short and short not in group["members"]:
            group["members"].append(short)


    def _begin_session_if_idle(self):

        if self._state in ("idle", "finished", "failed") and not self.is_busy():
            self._session_ok = 0
            self._session_fail = 0

    def _record_history(self, name: str, ok: bool, label: str):
        short = self._clean_member_name(name)
        self._history.append({"name": short, "ok": bool(ok), "label": label})
        if ok:
            self._session_ok += 1
        else:
            self._session_fail += 1

    def _history_lines(self) -> list:

        out = []
        for h in reversed(self._history):
            mark = "✓" if h["ok"] else "✗"   # ✓ / ✗
            lbl = h.get("label") or ""
            nm = h.get("name") or lbl
            out.append(f"{mark} {nm}" + (f"  ({lbl})" if lbl and lbl != nm else ""))
        return out

    def get_history(self) -> list:

        return list(self._history)

    def clear_history(self):

        self._history.clear()
        if self._tasks or self._groups:
            # Masih ada aktivitas → refresh text (••• tetap dgn history kosong).
            self._refresh()
        else:
            # Idle: _refresh() ga emit apa-apa. Kirim state_clean dgn history
            # kosong supaya bar hide ••• (Issue: ••• harus ilang pas di-clear).
            self.state_clean.emit(self._history_lines())

    def _emit_session_terminal(self):

        ok, fail = self._session_ok, self._session_fail
        if ok == 0 and fail == 0:
            msg = "Done"
        elif fail == 0:
            msg = "All tasks finished" if ok != 1 else "Task finished"
        else:
            fin_word = "task" if ok == 1 else "tasks"
            if ok == 0:
                msg = f"{fail} failed"
            else:
                msg = f"{ok} {fin_word} finished, {fail} failed"
        self._debug("session_terminal", ok=ok, fail=fail, text=msg)
        self._emit_finished(msg, has_fail=fail > 0)

    def _region_group_ids(self, region):

        return [b for b in self._group_order
                if self._groups.get(b, {}).get("region", "heavy") == region]

    def _active_group_id_in(self, region):

        ids = self._region_group_ids(region)
        for bid in ids:
            if self._groups[bid]["started"]:
                return bid
        return ids[0] if ids else None

    def _pending_group_ids(self, active_id):

        return [b for b in self._group_order if b != active_id]


    def _refresh(self, extra_text: str | None = None):

        if not self._tasks and not self._groups:
            if self._state == "active":
                self.set_clean()
            return

        parts: list = []
        pending_labels: list = []


        if self._groups:
            for region in self._REGIONS:
                ids = self._region_group_ids(region)
                if not ids:
                    continue
                active_id = self._active_group_id_in(region)
                region_pending = [b for b in ids if b != active_id]

                g = self._groups.get(active_id)
                if g is None:
                    continue
                done  = g["ok"] + g["fail"]
                total = g["total"]
                fail  = g["fail"]
                label = g["label"]

                base = (f"{label} {done}/{total} ({fail} failed)"
                        if fail > 0 else f"{label} {done}/{total}")

                if region_pending:
                    # grup aktif dianggap 1, total = aktif + pending region ini
                    pending_total = len(region_pending) + 1
                    parts.append(f"{base} - 1/{pending_total} Service Pending")
                else:
                    parts.append(base)

            # tooltip ••• — daftar SEMUA grup pending lintas region
            global_active = {self._active_group_id_in(r) for r in self._REGIONS}
            for b in self._group_order:
                if b in global_active:
                    continue
                pg = self._groups.get(b)
                if not pg:
                    continue
                memb = ", ".join(pg["members"][:3]) if pg["members"] else ""
                done = pg["ok"] + pg["fail"]
                pending_labels.append(
                    f"{pg['label']} {done}/{pg['total']}" + (f" — {memb}" if memb else "")
                )

        # SINGLE TASKS — group by kind biar text ga panjang
        if self._tasks:
            counter: dict = {}
            for _n, k in self._tasks:
                counter[k] = counter.get(k, 0) + 1
            order = ["unpack", "repack", "convert", "moving", "extracting", "preparing", "other"]
            label_map = {
                "unpack":     ("Unpacking", "Unpacking process"),
                "repack":     ("Repacking", "Repacking process"),
                "convert":    ("Converting", "Converting"),
                "moving":     ("Moving", "Moving"),
                "extracting": ("Extracting", "Extracting"),
                "preparing":  ("Preparing", "Preparing"),
                "other":      ("Working", "Working"),
            }
            for k in order:
                if k not in counter:
                    continue
                n = counter[k]
                single, multi = label_map[k]
                # Format konsisten "Label N/N" (bukan "Label (N)"). Single-task
                # stack ga punya 'done', jadi tampilkan N/N (semua sedang jalan).
                parts.append(f"{multi} {n}/{n}" if n > 1 else f"{single}..")

        if extra_text:
            parts.append(extra_text)

        self._state = "active"
        self.state_active.emit(parts, pending_labels, self._history_lines())

    def _emit_finished(self, text: str, has_fail: bool = False):
        self._cancel_fade()
        self._state = "finished"
        # has_fail → bar nampilin teks merah (warna doang; flow tetap finished).
        self.state_finished.emit(text, self._history_lines(), has_fail)
        # Ada fail → kasih waktu baca lebih lama (sama dgn fade failed).
        self._start_fade(self.FADE_FAILED_MS if has_fail else self.FADE_DONE_MS)

    def _emit_failed(self, text: str):
        self._cancel_fade()
        self._state = "failed"
        self.state_failed.emit(text, self._history_lines())
        self._start_fade(self.FADE_FAILED_MS)

    def _start_fade(self, delay_ms: int):
        self._cancel_fade()
        self._fade_generation += 1
        gen = self._fade_generation
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(lambda: self._on_fade_elapsed(gen))
        self._fade_timer.start(max(0, int(delay_ms)))

    def _cancel_fade(self):
        if hasattr(self, "_fade_timer") and self._fade_timer is not None:
            try:
                self._fade_timer.stop()
                self._fade_timer.deleteLater()
            except Exception:
                pass
            self._fade_timer = None
        # Bump generation supaya kalau ada callback stale dari timer lama, diabaikan
        self._fade_generation += 1

    def _on_fade_elapsed(self, gen: int):
        # Abaikan kalau bukan generation terakhir (state udah berubah)
        if gen != self._fade_generation:
            return
        # Hanya clean kalau memang masih di state terminal & ga ada aktivitas baru
        if self._state in ("finished", "failed") and not self.is_busy():
            self._state = "idle"
            # ••• tetap tampil kalau masih ada history (persisten s/d clear).
            self.state_clean.emit(self._history_lines())

    def _classify(self, name: str) -> str:
        n = (name or "").lower()
        # 'merg' nangkep moving-merge/merge/merging
        for k in ("unpack", "repack", "convert", "extracting",
                  "moving", "merging", "preparing"):
            probe = "merg" if k == "merging" else k
            if probe in n:
                return k
        return "other"

    def _region_of_kind(self, kind: str) -> str:
        return "light" if kind in self._LIGHT_KINDS else "heavy"

    def _pretty_action(self, action: str) -> str:
        mapping = {
            "unpack_erofs":   "Unpacking EROFS",
            "unpack_ext":     "Unpacking EXT",
            "repack_erofs":   "Repacking EROFS",
            "repack_ext":     "Repacking EXT",
            "convert_sparse": "Converting sparse",
            "moving":         "Moving",
            "merging":        "Merging",
        }
        return mapping.get(action, action.replace("_", " ").title())
