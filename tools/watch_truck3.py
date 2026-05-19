#!/usr/bin/env python3
"""
Terminal 3 — VEHICLE-TRUCK3 더미 (CACC 팔로워 | 텔레메트리 전용)

표시 내용:
  - vehicle-truck3 컨테이너 배너 (OpenClaw 없음)
  - .transfer/telemetry.log 를 실시간 스트리밍

사용법:
  python3 tools/watch_truck3.py
"""
import os, sys, subprocess, time, shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TELEM_LOG    = PROJECT_ROOT / ".transfer" / "telemetry.log"

C = {
    "cyan":   "\033[36m",  "green": "\033[32m",
    "yellow": "\033[33m",  "red":   "\033[31m",
    "bold":   "\033[1m",   "dim":   "\033[2m",
    "reset":  "\033[0m",
}

def c(name, text):
    return f"{C.get(name,'')}{text}{C['reset']}"

STATE_COLORS = {
    "CRUISE":   "cyan",   "MIGRATE": "yellow",
    "GAP":      "yellow", "LC":      "yellow",
    "SLOWDOWN": "yellow", "REJOIN":  "yellow",
    "DONE":     "green",  "IDLE":    "dim",
}

def colorize_telem(line):
    for state, col in STATE_COLORS.items():
        tag = f"state={state}"
        if tag in line:
            line = line.replace(tag, c(col, tag))
            break
    return line

def container_status(name):
    try:
        r = subprocess.run(
            ["docker", "ps", "--all", "--filter", f"name=^/{name}$",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=2)
        s = r.stdout.strip()
        return s if s else "없음"
    except Exception:
        return "docker 응답 없음"

def draw_header():
    w = min(shutil.get_terminal_size((80, 24)).columns, 72)
    line = "─" * (w - 2)
    print(c("bold", f"┌{line}┐"))
    title = (
        f"  VEHICLE-TRUCK3  "
        f"[{c('yellow','CACC 팔로워')} {c('bold','│')} {c('dim','OpenClaw 없음')}]"
    )
    print(c("bold", "│") + title)
    print(c("bold", f"└{line}┘"))
    print()

def draw_static():
    st = container_status("vehicle-truck3")
    dot = c("green", "●") if "Up" in st else c("yellow", "○")
    print(f"  {dot}  vehicle-truck3  {c('dim', st)}")
    print(f"  {c('dim','○')}  inner docker  : {c('dim','none  (순수 CACC 팔로워, 복제 대상 아님)')}")
    print()
    w = min(shutil.get_terminal_size((80, 24)).columns, 72)
    print(c("bold", "─" * (w - 2)))
    print(f"  {c('cyan','[텔레메트리]')}  CARLA 시뮬레이션 제어값  (100 틱마다 갱신)")
    print(c("bold", "─" * (w - 2)))
    print()

def tail_new(path, pos):
    try:
        sz = path.stat().st_size
        if sz < pos:
            pos = 0
        if sz > pos:
            with open(path, errors="replace") as f:
                f.seek(pos)
                return f.read(), sz
    except Exception:
        pass
    return "", pos

def main():
    os.system("clear")
    print()
    draw_header()
    draw_static()

    last_pos     = 0
    waiting_hint = False

    while True:
        if TELEM_LOG.exists():
            chunk, last_pos = tail_new(TELEM_LOG, last_pos)
            if chunk:
                for line in chunk.splitlines():
                    if line.strip():
                        print("  " + colorize_telem(line))
        elif not waiting_hint:
            print(f"  {c('dim','시나리오 시작 대기 중...')}")
            print(f"  {c('dim', str(TELEM_LOG))}")
            waiting_hint = True

        time.sleep(0.05)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[watch_truck3] 종료")
