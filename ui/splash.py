# SPDX-License-Identifier: MPL-2.0
"""Normalize the original SVG outline once for native Qt Quick Shape paths."""
from functools import lru_cache
import re
import xml.etree.ElementTree as ET

from PySide6.QtGui import QPainterPath, QTransform


def _read_path(data):
    tokens = re.findall(r"[MmLlCcQqZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", data)
    path, index, command = QPainterPath(), 0, None
    x = y = start_x = start_y = 0.
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command in ("Z", "z"):
            path.closeSubpath()
            x, y, command = start_x, start_y, None
            continue
        count = {"m": 2, "l": 2, "c": 6, "q": 4}.get((command or "").lower())
        if count is None:
            raise ValueError("Unsupported splash SVG command")
        values = list(map(float, tokens[index:index + count]))
        index += count
        if command.islower():
            values = [value + (x if offset % 2 == 0 else y) for offset, value in enumerate(values)]
        if command.lower() == "m":
            path.moveTo(*values)
            start_x, start_y = values
            command = "l" if command == "m" else "L"
        elif command.lower() == "l":
            path.lineTo(*values)
        elif command.lower() == "c":
            path.cubicTo(*values)
        else:
            path.quadTo(*values)
        x, y = values[-2:]
    return path


def _outline():
    from icons.handler import icon
    root = ET.parse(icon("splash")).getroot()
    vx, vy, vw, _ = map(float, root.attrib["viewBox"].split())
    group = next(node for node in root.iter() if node.get("id") == "object-1")
    translate = re.search(r"translate\(\s*([-+\d.]+)[ ,]+([-+\d.]+)\s*\)", group.get("transform", ""))
    tx, ty = map(float, translate.groups()) if translate else (0., 0.)
    paths = [_read_path(node.attrib["d"]) for node in group if node.tag.rsplit("}", 1)[-1] == "path"]
    path = paths[0]
    for part in paths[1:]:
        path = path.united(part)
    scale = 68 / vw
    return QTransform(scale, 0, 0, scale, (tx - vx)*scale + 6, (ty - vy)*scale + 6).map(path)


@lru_cache(maxsize=1)
def outline_svg():
    path, commands, index = _outline(), [], 0
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.type == QPainterPath.MoveToElement:
            commands.append(f"M {element.x:.5f} {element.y:.5f}")
        elif element.type == QPainterPath.LineToElement:
            commands.append(f"L {element.x:.5f} {element.y:.5f}")
        elif element.type == QPainterPath.CurveToElement:
            second, end = path.elementAt(index + 1), path.elementAt(index + 2)
            commands.append(f"C {element.x:.5f} {element.y:.5f} {second.x:.5f} {second.y:.5f} {end.x:.5f} {end.y:.5f}")
            index += 2
        index += 1
    return " ".join(commands)
