# SPDX-License-Identifier: MPL-2.0
"""Generate a tiny video locally for native playback/frame extraction QA."""
import subprocess
from pathlib import Path


def build_fixture(directory):
    import imageio_ffmpeg
    path = Path(directory) / 'native_video.avi'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc=duration=1.2:size=128x96:rate=5',
        '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', '-y', str(path)], check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=20)
    return path
