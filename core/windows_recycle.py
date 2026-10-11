# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import ctypes as ct
import ntpath
import os
import re
import sys
import uuid
from contextlib import contextmanager


class RecycleError(OSError):
    pass


class _GUID(ct.Structure):
    _fields_ = [("data", ct.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le


_SHELL_ITEM = "43826d1e-e718-42ee-bc55-a1e261c37bfe"
_FILE_OPERATION = "947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8"
_SINK = "04b0f1a7-9490-44bc-96e1-4296a31252e2"
_BIN = "::{645ff040-5081-101b-9f08-00aa002f954e}"
_HR = ct.c_int32
_DWORD = ct.c_uint32
_PTR = ct.c_void_p
_WSTR = ct.c_wchar_p
_FAIL = -2147467259


def _check(hr):
    if hr < 0:
        raise RecycleError(f"Windows Recycle Bin operation failed (0x{hr & 0xffffffff:08X}).")


def _call(pointer, slot, argtypes, *args):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(_PTR))).contents
    method = ct.WINFUNCTYPE(_HR, _PTR, *argtypes)(table[slot])
    return method(pointer, *args)


def _release(pointer):
    if pointer:
        _call(pointer, 2, [])


@contextmanager
def _sta():
    if sys.platform != "win32":
        raise RecycleError("Windows Recycle Bin is only available on Windows.")
    ole = ct.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [_PTR, _DWORD]
    ole.CoInitializeEx.restype = _HR
    _check(ole.CoInitializeEx(None, 2))
    try:
        yield ole
    finally:
        ole.CoUninitialize()


def _shell_item(name):
    shell = ct.WinDLL("shell32")
    shell.SHCreateItemFromParsingName.argtypes = [_WSTR, _PTR, ct.POINTER(_GUID), ct.POINTER(_PTR)]
    shell.SHCreateItemFromParsingName.restype = _HR
    item = _PTR()
    _check(shell.SHCreateItemFromParsingName(name, None, ct.byref(_GUID(_SHELL_ITEM)), ct.byref(item)))
    return item


def _display_name(pointer, kind):
    value = _PTR()
    _check(_call(pointer, 5, [_DWORD, ct.POINTER(_PTR)], kind, ct.byref(value)))
    try:
        return ct.wstring_at(value)
    finally:
        ole = ct.WinDLL("ole32")
        ole.CoTaskMemFree.argtypes = [_PTR]
        ole.CoTaskMemFree(value)


def _recycled_item(path):
    # Obtain the actual virtual-bin item rather than a generic filesystem item.
    root, enumeration = _shell_item(_BIN), _PTR()
    try:
        _check(_call(root, 3, [_PTR, ct.POINTER(_GUID), ct.POINTER(_GUID), ct.POINTER(_PTR)],
                    None, ct.byref(_GUID("94f60519-2850-4924-aa5a-d15e84868039")),
                    ct.byref(_GUID("70629033-e363-4a28-a567-0db78006e6d7")), ct.byref(enumeration)))
        while True:
            item, fetched = _PTR(), _DWORD()
            _check(_call(enumeration, 3, [_DWORD, ct.POINTER(_PTR), ct.POINTER(_DWORD)],
                        1, ct.byref(item), ct.byref(fetched)))
            if not fetched.value:
                break
            try:
                if ntpath.normcase(_display_name(item, 0x80058000)) == ntpath.normcase(path):
                    result, item = item, None
                    return result
            finally:
                _release(item)
        raise RecycleError("This project is no longer in Windows Recycle Bin.")
    finally:
        _release(enumeration)
        _release(root)


def validate_handle(handle):

    path = ntpath.normpath(str(handle.get("filesystem_path", "")))
    parsing = str(handle.get("parsing_name", ""))
    if not re.fullmatch(r"[A-Za-z]:\\\$Recycle\.Bin\\S-1-[0-9-]+\\\$R[^\\]+", path, re.IGNORECASE):
        raise RecycleError("Invalid Windows Recycle Bin item.")
    # Windows may return either the bin namespace or the physical parsing name.
    if not (parsing.casefold().startswith(_BIN.casefold() + "\\")
            or ntpath.normcase(ntpath.normpath(parsing)) == ntpath.normcase(path)):
        raise RecycleError("Invalid Windows Recycle Bin reference.")
    return path


class _ProgressSink:
    def __init__(self, mode, on_recycled=None):
        self.mode = mode
        self.on_recycled = on_recycled
        self.result = None
        self.error = None
        self.operation_hr = None
        self.refs = 1
        self.callbacks = []
        signatures = [
            [ct.POINTER(_GUID), ct.POINTER(_PTR)], [], [], [], [_HR],
            [_DWORD, _PTR, _WSTR], [_DWORD, _PTR, _WSTR, _HR, _PTR],
            [_DWORD, _PTR, _PTR, _WSTR], [_DWORD, _PTR, _PTR, _WSTR, _HR, _PTR],
            [_DWORD, _PTR, _PTR, _WSTR], [_DWORD, _PTR, _PTR, _WSTR, _HR, _PTR],
            [_DWORD, _PTR], [_DWORD, _PTR, _HR, _PTR],
            [_DWORD, _PTR, _WSTR], [_DWORD, _PTR, _WSTR, _WSTR, _DWORD, _HR, _PTR],
            [_DWORD, _DWORD], [], [], [],
        ]
        handlers = {0: self.query, 1: self.add_ref, 2: self.release,
                    8: self.post_move, 11: self.pre_delete, 12: self.post_delete}
        for index, signature in enumerate(signatures):
            handler = handlers.get(index, lambda *_args: 0)

            def callback(_this, *args, handler=handler):
                try:
                    return handler(*args)
                except Exception as exc:
                    self.error = exc
                    return _FAIL

            self.callbacks.append(ct.WINFUNCTYPE(_HR, _PTR, *signature)(callback))
        self.table = (_PTR * len(self.callbacks))(*[
            ct.cast(callback, _PTR).value for callback in self.callbacks
        ])
        self.interface = ct.pointer(ct.cast(self.table, ct.POINTER(_PTR)))
        self.pointer = ct.cast(self.interface, _PTR)

    def query(self, iid, output):
        supported = (uuid.UUID(_SINK).bytes_le, uuid.UUID("00000000-0000-0000-c000-000000000046").bytes_le)
        if bytes(iid.contents.data) not in supported:
            output[0] = None
            return -2147467262
        output[0] = self.pointer
        self.refs += 1
        return 0

    def add_ref(self):
        self.refs += 1
        return self.refs

    def release(self):
        self.refs -= 1
        return self.refs

    def pre_delete(self, flags, _item):
        # Reject a hard-delete request when this operation was meant to recycle.
        if self.mode == "recycle" and not flags & 0x80:  # TSF_DELETE_RECYCLE_IF_POSSIBLE
            self.error = RecycleError("Windows cannot recycle this folder. The project was kept.")
            return _FAIL
        return 0

    def post_delete(self, _flags, _item, hr, new_item):
        self.operation_hr = hr
        if hr >= 0 and self.mode == "recycle":
            if not new_item:
                raise RecycleError("Windows did not return a recoverable Recycle Bin item.")
            self.result = {"parsing_name": _display_name(new_item, 0x80028000),
                           "filesystem_path": _display_name(new_item, 0x80058000)}
            validate_handle(self.result)
            if self.on_recycled:
                self.on_recycled(self.result)
        return 0

    def post_move(self, _flags, _item, _destination, _name, hr, _new_item):
        self.operation_hr = hr
        return 0


def _operate(mode, source, *, destination=None, name=None, on_recycled=None):
    with _sta() as ole:
        operation, item, parent = _PTR(), None, None
        sink = _ProgressSink(mode, on_recycled)
        ole.CoCreateInstance.argtypes = [ct.POINTER(_GUID), _PTR, _DWORD, ct.POINTER(_GUID), ct.POINTER(_PTR)]
        ole.CoCreateInstance.restype = _HR
        try:
            _check(ole.CoCreateInstance(ct.byref(_GUID("3ad05575-8857-4850-9277-11b85bdb8e09")),
                                       None, 1, ct.byref(_GUID(_FILE_OPERATION)), ct.byref(operation)))
            # Silent progress/errors, fail early; never auto-rename or merge a restore.
            flags = 0x4 | 0x10 | 0x400 | 0x00100000
            if mode == "recycle":


                flags |= 0x00080000 | 0x4000  # RECYCLEONDELETE / WANTNUKEWARNING
            _check(_call(operation, 5, [_DWORD], flags))
            item = _shell_item(source) if mode == "recycle" else _recycled_item(source)
            if mode == "restore":
                parent = _shell_item(destination)
                _check(_call(operation, 14, [_PTR, _PTR, _WSTR, _PTR], item, parent, name, sink.pointer))
            else:
                _check(_call(operation, 18, [_PTR, _PTR], item, sink.pointer))
            hr = _call(operation, 21, [])
            if sink.error:
                raise sink.error
            _check(hr)
            aborted = ct.c_int32()
            _check(_call(operation, 22, [ct.POINTER(ct.c_int32)], ct.byref(aborted)))
            if sink.error:
                raise sink.error
            if aborted.value:
                raise RecycleError("Windows Recycle Bin operation was cancelled.")
            if sink.operation_hr is None:
                raise RecycleError("Windows did not complete the Recycle Bin operation.")
            _check(sink.operation_hr)
            return sink.result
        finally:
            _release(parent)
            _release(item)
            _release(operation)


def recycle(path, *, on_recycled=None):
    path = os.path.abspath(path)
    drive, tail = ntpath.splitdrive(path)
    if sys.platform != "win32" or not drive or drive.startswith("\\\\") or tail in ("", "\\"):
        raise RecycleError("Windows Recycle Bin is unavailable for this project location.")
    kernel = ct.WinDLL("kernel32")
    kernel.GetDriveTypeW.argtypes = [_WSTR]
    kernel.GetDriveTypeW.restype = _DWORD
    if kernel.GetDriveTypeW(drive + "\\") != 3:
        raise RecycleError("Windows Recycle Bin is unavailable for this drive. The project was kept.")
    if not os.path.isdir(path) or os.path.islink(path):
        raise RecycleError("The project folder is missing or is a symbolic link.")
    return _operate("recycle", path, on_recycled=on_recycled)


def restore(handle, original_path):
    validate_handle(handle)
    original_path = os.path.abspath(original_path)
    if os.path.lexists(original_path):
        raise RecycleError("The original project location is already occupied. Move or rename that folder first.")
    parent = os.path.dirname(original_path)
    if not os.path.isdir(parent):
        raise RecycleError("The original parent folder is unavailable. Reconnect the drive or recreate the parent folder.")
    _operate("restore", handle["filesystem_path"], destination=parent, name=os.path.basename(original_path))
    if not os.path.isdir(original_path):
        raise RecycleError("Windows could not restore the project folder.")


def purge(handle):
    validate_handle(handle)
    _operate("purge", handle["filesystem_path"])
    if os.path.lexists(handle["filesystem_path"]):
        raise RecycleError("Windows could not permanently remove this Recycle Bin item.")
