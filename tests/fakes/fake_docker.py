"""FakeDocker: replaces subprocess.run for `docker ...` commands in tests.

openclaw_migration/replicator.py shells out to docker (save / load / run / rm).
Tests monkeypatch `subprocess.run` with an instance of this class so no real
Docker daemon is touched. Non-docker commands are rejected loudly.

Behavior per docker sub-command is configurable:
    fake = FakeDocker()
    fake.fail("save", stderr="no such image")      # returncode 1
    fake.hang("load", seconds=5)                    # blocks (simulates a stuck daemon)
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path


class FakeDocker:
    def __init__(self):
        self.calls: list[list[str]] = []
        self._fail: dict[str, str] = {}
        self._hang: dict[str, float] = {}
        self._lock = threading.Lock()

    def fail(self, sub: str, stderr: str = "fake docker failure"):
        self._fail[sub] = stderr

    def hang(self, sub: str, seconds: float):
        self._hang[sub] = seconds

    def subcommands(self) -> list[str]:
        with self._lock:
            return [c[1] for c in self.calls]

    def __call__(self, cmd, *args, **kwargs):
        if not isinstance(cmd, (list, tuple)) or not cmd or cmd[0] != "docker":
            raise AssertionError(f"unexpected non-docker subprocess call in test: {cmd!r}")
        cmd = list(cmd)
        sub = cmd[1]
        with self._lock:
            self.calls.append(cmd)
        if sub in self._hang:
            time.sleep(self._hang[sub])
        if sub in self._fail:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self._fail[sub])
        if sub == "save" and "-o" in cmd:
            # emulate an image archive so later size/stat calls work
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-image-tar" * 64)
        stdout = "Loaded image: openclaw:local" if sub == "load" else ("0123456789abcdef" if sub == "run" else "")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
