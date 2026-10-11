# SPDX-License-Identifier: MPL-2.0
import sys as _sys
import io as _io


class _SafeTextStream:


    __slots__ = ("_w", "_enc")

    def __init__(self, wrapped):
        self._w = wrapped
        enc = (getattr(wrapped, "encoding", None) or "utf-8")
        self._enc = enc

    def write(self, s):
        try:
            return self._w.write(s)
        except UnicodeEncodeError:
            # paksa lolos lewat encoding stream dgn replace
            try:
                safe = s.encode(self._enc, errors="replace").decode(self._enc, errors="replace")
                return self._w.write(safe)
            except Exception:
                try:
                    return self._w.write(s.encode("ascii", errors="replace").decode("ascii"))
                except Exception:
                    return 0
        except Exception:
            # stream tutup / error lain → jangan pernah propagate dari logging
            return 0

    def writelines(self, lines):
        for ln in lines:
            self.write(ln)

    def flush(self):
        try:
            self._w.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._w, name)


def _wrap_stream(name):
    stream = getattr(_sys, name, None)
    if stream is None:
        return
    if isinstance(stream, _SafeTextStream):
        return  # sudah dibungkus (idempotent)
    # Coba reconfigure dulu (kalau didukung) supaya encoding underlying utf-8.
    try:
        rc = getattr(stream, "reconfigure", None)
        if rc is not None:
            rc(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        setattr(_sys, name, _SafeTextStream(stream))
    except Exception:
        pass


def _install_safe_stdio():
    for _name in ("stdout", "stderr"):
        _wrap_stream(_name)


_install_safe_stdio()
