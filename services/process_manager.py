# SPDX-License-Identifier: MPL-2.0
import os
import sys
import json
import time
import secrets
import hmac
import tempfile
import multiprocessing as mp

from services.protocol import HANDSHAKE_FILE_ENV, HANDSHAKE_TOKEN_ENV, handshake_mac


class ServiceProcessManager:
    def __init__(self):
        self.proc: mp.Process | None = None
        self.port: int | None = None
        self.pid: int | None = None
        self.token: str | None = None          # Shared IPC secret.
        self.handshake_file: str | None = None
        self._spawn_started: bool = False  # True kalau .start_process() udah dipanggil


    # PUBLIC

    def start_process(self):

        if self._spawn_started:
            return
        self._spawn_started = True

        # bikin handshake file unik
        fd, self.handshake_file = tempfile.mkstemp(prefix="rak_service_", suffix=".json")
        os.close(fd)

        os.environ[HANDSHAKE_FILE_ENV] = self.handshake_file


        self.token = secrets.token_hex(32)
        os.environ[HANDSHAKE_TOKEN_ENV] = self.token

        ctx = mp.get_context("spawn")
        from services.service_main import main as service_main
        self.proc = ctx.Process(target=service_main, daemon=True, name="RAK-ConfigService")
        self.proc.start()
        self.pid = self.proc.pid


    def poll_ready(self) -> int | None:

        if self.port is not None:
            return self.port
        if not self._spawn_started:
            return None
        if self.proc is not None and not self.proc.is_alive():
            raise RuntimeError(
                f"Service child process mati sebelum ready (exit code: {self.proc.exitcode})"
            )
        if self.handshake_file and os.path.isfile(self.handshake_file):
            try:
                with open(self.handshake_file, "r") as f:
                    data = json.load(f)
                port = int(data["port"])
                pid = int(data["pid"])
                version = int(data["version"])
                proof = str(data["proof"])
                expected = handshake_mac(self.token or "", port, pid, version)
                if (self.token and self.proc and pid == self.proc.pid and
                        1 <= port <= 65535 and hmac.compare_digest(proof, expected)):
                    self.port = port
                    return self.port
            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                PermissionError,
                FileNotFoundError,
            ):
                pass
        return None

    def wait_ready(self, timeout: float = 10.0) -> int:

        if self.port is not None:
            return self.port
        if not self._spawn_started:
            self.start_process()

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                port = self.poll_ready()
                if port is not None:
                    return port
            except RuntimeError:
                raise
            time.sleep(0.05)

        self.stop()
        raise RuntimeError(f"Service tidak ready dalam {timeout}s")

    def start(self, timeout: float = 10.0) -> int:

        self.start_process()
        return self.wait_ready(timeout)

    def stop(self, timeout: float = 2.0):

        if self.proc:
            try:
                if self.proc.is_alive():
                    self.proc.terminate()
                    self.proc.join(timeout=timeout)
                if self.proc.is_alive():
                    self.proc.kill()
                    self.proc.join(timeout=1.0)
            except Exception as e:
                print(f"[ServiceProcessManager] stop error: {e}")
        self.proc = None
        self.port = None
        self.pid = None
        # Clear the IPC secret after the service exits.
        self.token = None
        os.environ.pop(HANDSHAKE_TOKEN_ENV, None)
        os.environ.pop(HANDSHAKE_FILE_ENV, None)

        if self.handshake_file and os.path.exists(self.handshake_file):
            try:
                os.remove(self.handshake_file)
            except Exception:
                pass
        self.handshake_file = None

    def is_alive(self) -> bool:
        return bool(self.proc and self.proc.is_alive())

    def get_token(self) -> str | None:

        return self.token


_singleton: ServiceProcessManager | None = None


def get_manager() -> ServiceProcessManager:
    global _singleton
    if _singleton is None:
        _singleton = ServiceProcessManager()
    return _singleton
