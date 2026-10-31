# SPDX-License-Identifier: MPL-2.0
"""Update QML rows by identity so data refreshes retain delegates and motion."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, Signal, Slot, Qt


class RowsModel(QAbstractListModel):
    motionChanged = Signal()
    aboutToReplace = Signal("QVariantList", "QVariantList", bool)
    replaced = Signal()
    cancelMotion = Signal()
    animationFinished = Signal()
    _ROLE = int(Qt.UserRole) + 1

    def __init__(self, rows=(), parent=None):
        super().__init__(parent)
        self._rows = [dict(row) for row in rows]
        self._motion = "data"
        self._animating = False
        self._pending = None

    @Slot(result="QVariantList")
    def snapshot(self):
        return [dict(row) for row in self._rows]

    def extend(self,rows):
        rows=[dict(row) for row in rows]
        if not rows:return
        start=len(self._rows)
        self.beginInsertRows(QModelIndex(),start,start+len(rows)-1)
        self._rows.extend(rows)
        self.endInsertRows()
        self.replaced.emit()

    @Slot(bool)
    def setAnimating(self, active):
        was_active = self._animating
        self._animating = active
        if not active and self._pending is not None:
            rows, animate = self._pending
            self._pending = None
            self.replace(rows, animate=animate)
        if was_active and not self._animating:
            self.animationFinished.emit()

    @property
    def animating(self):
        return self._animating

    def roleNames(self):
        return {self._ROLE: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if role == self._ROLE and index.isValid() and 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]

    @Property(str, notify=motionChanged)
    def motionKind(self):
        return self._motion

    @staticmethod
    def _keys(rows):
        counts, result = {}, []
        for row in rows:
            key = str(row.get("_trash_id") or row.get("key") or row.get("path", "")).casefold()
            number = counts.get(key, 0)
            counts[key] = number + 1
            result.append((key, number))
        return result

    def replace(self, rows, *, animate=True):
        rows = [dict(row) for row in rows]
        if self._animating:
            if animate:
                self._pending = rows, animate
                return
            self._pending = None
            self.cancelMotion.emit()
        old_keys, desired = self._keys(self._rows), self._keys(rows)
        removed, added = set(old_keys) - set(desired), set(desired) - set(old_keys)
        kind = "mixed" if removed and added else "remove" if removed else "insert" if added else "move" if old_keys != desired else "data"
        if kind != "data":
            self.aboutToReplace.emit(self.snapshot(), rows, animate)
        if kind != self._motion:
            self._motion = kind
            self.motionChanged.emit()
        for index in range(len(old_keys)-1, -1, -1):
            if old_keys[index] not in desired:
                self.beginRemoveRows(QModelIndex(), index, index)
                self._rows.pop(index)
                old_keys.pop(index)
                self.endRemoveRows()
        for index, (key, row) in enumerate(zip(desired, rows)):
            if key not in old_keys:
                self.beginInsertRows(QModelIndex(), index, index)
                self._rows.insert(index, row)
                old_keys.insert(index, key)
                self.endInsertRows()
            else:
                source = old_keys.index(key)
                if source != index:
                    self.beginMoveRows(QModelIndex(), source, source, QModelIndex(), index if source > index else index+1)
                    self._rows.insert(index, self._rows.pop(source))
                    old_keys.insert(index, old_keys.pop(source))
                    self.endMoveRows()
                if self._rows[index] != row:
                    self._rows[index] = row
                    self.dataChanged.emit(self.index(index), self.index(index), [self._ROLE])
        self.replaced.emit()
