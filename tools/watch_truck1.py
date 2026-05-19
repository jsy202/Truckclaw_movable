#!/usr/bin/env python3
"""
Terminal 1 — VEHICLE-TRUCK1 DinD 모니터 (구 선두 / TX side)

상태 전환:
  IDLE   : 시나리오 대기 중
  TELEM  : .transfer/telemetry.log 실시간 표시
  TX     : 복제 TX → .transfer/tx/.progress.log 스트리밍
  DELETE : 자리 체인지 완료 후 openclaw-truck0 삭제 표시

사용법:
  python3 tools/watch_truck1.py
"""
import os, sys, subprocess, time, shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TX_LOG    = PROJECT_ROOT / ".transfer" / "tx" / ".progress.log"
TELEM_LOG = PROJECT_ROOT / ".transfer" / "telemetry.log"
SCRIPT_START = time.time()

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

def colorize(line):
    for st, col in STATE_COLORS.items():
        tag = f"state={st}"
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
    ln = "─" * (w - 2)
    print(c("bold", f"┌{ln}┐"))
    print(c("bold", "│") +
          f"  VEHICLE-TRUCK1  [{c('cyan','구 선두')} {c('bold','│')} {c('cyan','DinD 모니터')}]")
    print(c("bold", f"└{ln}┘"))
    print()

def draw_inner_status():
    st = container_status("openclaw-truck0")
    dot = c("green", "●") if "Up" in st else c("yellow", "○")
    print(f"  {dot}  inner docker : {c('bold','openclaw-truck0')}  {c('dim', st)}")
    print()

def draw_telem_header():
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
    print(f"  {c('yellow','[TX]')} 시나리오 시작 대기 중...")

    state      = "idle"
    telem_pos  = 0
    tx_pos     = 0
    last_idle  = time.time()

    while True:
        # ── TX 로그에서 센티널 감지 → TX 모드 전환 ───────────────────────────
        if state not in ("tx", "delete") and TX_LOG.exists():
            try:
                if (TX_LOG.stat().st_mtime > SCRIPT_START and
                        "=== TX START ===" in TX_LOG.read_text(errors="replace")):
                    os.system("clear")
                    print()
                    draw_header()
                    draw_inner_status()
                    print(c("bold", "═" * 62))
                    print(c("bold", "  OpenClaw 이전 TX 시작  (truck0 ──▶ truck1)"))
                    print(c("bold", "═" * 62))
                    print()
                    state  = "tx"
                    tx_pos = 0
            except Exception:
                pass

        if state == "tx":
            chunk, tx_pos = tail_new(TX_LOG, tx_pos)
            if chunk:
                output = chunk.replace("=== TX START ===\n", "").replace("=== TX START ===", "")
                if output:
                    sys.stdout.write(output)
                    sys.stdout.flush()

            # DELETE 센티널 감지
            try:
                content = TX_LOG.read_text(errors="replace")
                if "=== DELETE START ===" in content:
                    print()
                    print(c("bold", "─" * 62))
                    print(f"  {c('yellow','[CLEANUP]')} 자리 체인지 완료 — 구 선두 docker 삭제 중...")
                    print(c("bold", "─" * 62))
                    state = "delete"
            except Exception:
                pass

        elif state == "delete":
            chunk, tx_pos = tail_new(TX_LOG, tx_pos)
            if chunk:
                output = chunk.replace("=== DELETE START ===\n", "").replace("=== DELETE START ===", "")
                if output:
                    for line in output.splitlines():
                        if line.strip():
                            print("  " + line)

        elif state == "idle":
            if (TELEM_LOG.exists() and TELEM_LOG.stat().st_size > 0 and
                    TELEM_LOG.stat().st_mtime > SCRIPT_START):
                os.system("clear")
                print()
                draw_header()
                draw_inner_status()
                draw_telem_header()
                state = "telem"
            elif time.time() - last_idle > 5:
                os.system("clear")
                print()
                draw_header()
                print(f"  {c('yellow','[TX]')} 시나리오 시작 대기 중...")
                last_idle = time.time()

        elif state == "telem":
            chunk, telem_pos = tail_new(TELEM_LOG, telem_pos)
            if chunk:
                for line in chunk.splitlines():
                    if line.strip():
                        print("  " + colorize(line))

        time.sleep(0.05)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[watch_truck1] 종료")
