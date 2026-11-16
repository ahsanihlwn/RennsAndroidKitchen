"""Reproducible measurements; run after installing the native extension.

python benchmark.py --headless --output BENCHMARK.json
No Chromium baseline is measured or inferred by this script.
"""
import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from quill_native._engine import Document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--output", default="BENCHMARK.json")
    args = parser.parse_args()
    env = os.environ.copy()
    if args.headless:
        env.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    report = {"platform": platform.platform(), "python": platform.python_version(), "renderer": "offscreen/software" if args.headless else "platform default", "ui": [], "core": {}}
    with tempfile.TemporaryDirectory(prefix="quill-bench-") as directory:
        small = Path(directory) / "small.py"
        small.write_text("def greeting(name):\n    # Native editor\n    return f'Hello {name}'\n", encoding="utf-8")
        large = Path(directory) / "large.rs"
        source = "pub fn calculate(value: usize) -> usize { value * 42 + 7 } // example\n"
        with large.open("w", encoding="utf-8", newline="") as handle:
            for _ in range(160000):
                handle.write(source)
        for name, path in [("small_python", small), ("10_mib_rust", large)]:
            result = subprocess.run([sys.executable, "-m", "quill_native", str(path), "--probe"], env=env, capture_output=True, text=True, timeout=30, check=True)
            metrics = json.loads(result.stdout.strip().splitlines()[-1])
            metrics["case"] = name
            metrics["input_mib"] = round(path.stat().st_size / 1024**2, 2)
            report["ui"].append(metrics)
            if result.stderr.strip():
                report.setdefault("ui_stderr", []).append(result.stderr.strip())
        doc = Document()
        start = time.perf_counter()
        doc.load(str(large))
        load_ms = (time.perf_counter() - start) * 1000
        durations = []
        for iteration in range(1000):
            doc.set_cursor((iteration * 7919) % (doc.line_count() - 1), 10)
            before = time.perf_counter_ns()
            doc.insert("x")
            durations.append((time.perf_counter_ns() - before) / 1e6)
            doc.undo()
        durations.sort()
        report["core"] = {
            "file_mib": round(large.stat().st_size / 1024**2, 2), "lines": doc.line_count(),
            "stream_load_ms": round(load_ms, 3), "edit_count": len(durations),
            "insert_p50_ms": round(statistics.median(durations), 4),
            "insert_p95_ms": round(durations[int(len(durations) * .95)], 4),
            "notes": "Backend insertion timings include PyO3 call, rope edit and history bookkeeping. They exclude UI layout/paint, coloring, disk I/O and undo timing. One warmed process; no cross-editor comparison.",
        }
    report["caveats"] = ["RSS includes shared mapped libraries and differs across OS/GPU/backend.", "Headless/software results do not predict Windows GPU memory usage.", "first_frame_ms measures initialization through first frame, observation_ms includes an intentional 800 ms settling timer."]
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
