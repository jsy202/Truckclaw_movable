#!/usr/bin/env python3
"""
ContainerMonitor — truck0/truck1/truck2 컨테이너 상태 실시간 폴링
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import List


@dataclass
class ContainerInfo:
    name: str
    status: str

    @property
    def is_running(self) -> bool:
        return "Up" in self.status

    @property
    def icon(self) -> str:
        if "Up" in self.status:       return "✓"
        if "starting" in self.status.lower(): return "↺"
        return "✗"

    def __str__(self) -> str:
        short = self.status[:30] if len(self.status) > 30 else self.status
        return f"{self.name}: {short} {self.icon}"


class ContainerMonitor:
    TRUCK_IDS = ["truck0", "truck1", "truck2"]

    def __init__(self, poll_interval: float = 1.0) -> None:
        self._poll_interval = poll_interval
        self._status: dict[str, List[ContainerInfo]] = {t: [] for t in self.TRUCK_IDS}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _query(self, truck_id: str) -> List[ContainerInfo]:
        try:
            result = subprocess.run(
                ["docker", "ps", "-a",
                 "--filter", f"name={truck_id}",
                 "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True, text=True, timeout=2,
            )
            containers = []
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    containers.append(ContainerInfo(name=parts[0], status=parts[1]))
            return containers
        except Exception:
            return []

    def _poll_loop(self) -> None:
        while True:
            for tid in self.TRUCK_IDS:
                c = self._query(tid)
                with self._lock:
                    self._status[tid] = c
            time.sleep(self._poll_interval)

    def snapshot(self) -> dict[str, List[ContainerInfo]]:
        with self._lock:
            return {k: list(v) for k, v in self._status.items()}

    def display_lines(self) -> List[str]:
        snap = self.snapshot()
        lines = []
        for tid in self.TRUCK_IDS:
            containers = snap[tid]
            if containers:
                for c in containers:
                    lines.append(f"  [vehicle-{tid}]  {c}")
            else:
                lines.append(f"  [vehicle-{tid}]  (컨테이너 없음)")
        return lines

    def print_status(self) -> None:
        for line in self.display_lines():
            print(line)


if __name__ == "__main__":
    print("컨테이너 모니터 시작 (Ctrl-C로 종료)")
    monitor = ContainerMonitor()
    try:
        while True:
            print("\033[2J\033[H", end="")
            print("─" * 55)
            monitor.print_status()
            print("─" * 55)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
