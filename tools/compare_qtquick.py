# SPDX-License-Identifier: MPL-2.0
"""Measure original/Quick screenshot differences; this is not a parity verdict."""
import argparse
import json
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage


def pixels(path):
    image = QImage(str(path)).convertToFormat(QImage.Format_RGB888)
    if image.isNull():
        raise ValueError(f"Missing or invalid screenshot: {path}")
    return np.frombuffer(image.bits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine())[:, :image.width()*3].reshape(image.height(), image.width(), 3).copy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("build/qtquick_migration/baseline"))
    parser.add_argument("--quick", type=Path, default=Path("build/qtquick_migration/quick"))
    args = parser.parse_args()
    results = {}
    for name in ("projects", "create", "create_dual", "settings", "settings_bottom", "theme_picker"):
        original, quick = pixels(args.baseline / f"{name}.png"), pixels(args.quick / f"{name}.png")
        if original.shape != quick.shape:
            raise ValueError(f"Different capture sizes for {name}: {original.shape} / {quick.shape}")
        difference = np.abs(original.astype(np.int16) - quick.astype(np.int16))
        report = {"mean_channel_error_255": round(float(difference.mean()), 4),
                  "percent_pixels_over_16": round(float((difference.max(axis=2) > 16).mean()*100), 4)}
        if name != "theme_picker":
            report["footer_channel_error_255"] = round(float(difference[-80:].mean()), 4)
        results[name] = report
    destination = args.quick / "visual_difference.json"
    destination.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
