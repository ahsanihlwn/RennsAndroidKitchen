# SPDX-License-Identifier: MPL-2.0
"""Original image pixel operations, independent of widget presentation."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter

def _qimage_to_ndarray(img: QImage):
    import numpy as np
    if img.format() != QImage.Format_ARGB32:
        img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = (img.width(), img.height())
    bpl = img.bytesPerLine()
    ptr = img.bits()
    buf = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
    arr = buf.reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
    return (arr, img)

def _human_size(n: int) -> str:
    f = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if f < 1024.0 or unit == 'GB':
            return f'{f:.0f} {unit}' if unit == 'B' else f'{f:.1f} {unit}'
        f /= 1024.0
    return f'{f:.1f} GB'

def _crop_canvas(src, w, h):
    out = QImage(w, h, QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    p = QPainter(out)
    x = (w - src.width()) // 2
    y = (h - src.height()) // 2
    p.drawImage(x, y, src)
    p.end()
    return out

def _scanline_fill(match, sx, sy):
    import numpy as np
    h, w = match.shape
    filled = np.zeros((h, w), dtype=bool)
    if not (0 <= sx < w and 0 <= sy < h) or not match[sy, sx]:
        return filled
    stack = [(sx, sy)]
    while stack:
        x, y = stack.pop()
        row = match[y]
        if filled[y, x] or not row[x]:
            continue
        left_part = row[:x]
        lf = np.flatnonzero(~left_part)
        xl = lf[-1] + 1 if lf.size else 0
        right_part = row[x + 1:]
        rf = np.flatnonzero(~right_part)
        xr = x + int(rf[0]) if rf.size else w - 1
        filled[y, xl:xr + 1] = True
        for ny in (y - 1, y + 1):
            if ny < 0 or ny >= h:
                continue
            seedable = match[ny, xl:xr + 1] & ~filled[ny, xl:xr + 1]
            idx = np.flatnonzero(seedable)
            if idx.size:
                starts = idx[np.concatenate(([True], np.diff(idx) > 1))]
                for sxi in starts:
                    stack.append((xl + int(sxi), ny))
    return filled

def _defringe(img, alpha_thr):
    arr, _ref = _qimage_to_ndarray(img)
    a = arr[:, :, 3]
    mask = (a > 0) & (a < alpha_thr)
    arr[mask] = 0
