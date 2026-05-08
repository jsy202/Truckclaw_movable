#!/usr/bin/env python3
"""Preflight checks for the TruckClaw demo stack.

This script is intentionally read-only. It checks whether the local services
needed for a demo are reachable after a reboot.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request


CHECKS = [
    ("CARLA server", "127.0.0.1", 2000),
    ("Bridge server", "127.0.0.1", 18801),
    ("OpenClaw Platoon A gateway", "127.0.0.1", 18789),
    ("OpenClaw Platoon B gateway", "127.0.0.1", 18790),
]


def tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode()
        parsed = json.loads(body)
        return True, json.dumps(parsed, ensure_ascii=False)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, str(exc)


def docker_running(name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.stdout.strip() == "true"


def main() -> int:
    failures = 0

    print("TruckClaw preflight")
    print("===================")

    for label, host, port in CHECKS:
        ok = tcp_open(host, port)
        print(f"{'OK' if ok else 'FAIL'}  {label:<28} {host}:{port}")
        failures += 0 if ok else 1

    for label, url in [
        ("Bridge health", "http://127.0.0.1:18801/health"),
        ("Platoon A health", "http://127.0.0.1:18789/healthz"),
        ("Platoon B health", "http://127.0.0.1:18790/healthz"),
    ]:
        ok, detail = http_json(url)
        print(f"{'OK' if ok else 'FAIL'}  {label:<28} {detail}")
        failures += 0 if ok else 1

    for name in ("openclaw-platoon-a", "openclaw-platoon-b"):
        ok = docker_running(name)
        print(f"{'OK' if ok else 'FAIL'}  docker {name}")
        failures += 0 if ok else 1

    if failures:
        print(f"\n{failures} check(s) failed.")
        return 1

    print("\nAll preflight checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
