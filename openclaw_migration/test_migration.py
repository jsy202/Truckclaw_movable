#!/usr/bin/env python3
"""
OpenClaw 선두 이전 테스트 — CARLA 없이 실행 가능
=================================================
테스트 순서:
  1. openclaw-truck0 컨테이너 실행 (구 선두)
  2. 컨테이너 모니터 시작
  3. 선두 교체 시뮬레이션 (5초 후 자동)
  4. Bundle 1: openclaw_base.tar (최초 1회)
  5. Bundle 2: openclaw_session.tar (세션 캡처 + V2V 전송)
  6. openclaw-truck1 실행 확인 (신 선두)
  7. openclaw-truck0 삭제 확인

실행:
  cd /home/jsy202/Downloads/Truckclaw-improve
  python3 openclaw_migration/test_migration.py
  python3 openclaw_migration/test_migration.py --reset
  python3 openclaw_migration/test_migration.py --no-docker  # docker 없이 로직만 테스트
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 로드
def _load_env():
    for name in [".env.leader-rotation", ".env"]:
        p = PROJECT_ROOT / name
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            break
_load_env()

TRUCK0_DISCORD = os.environ.get("TRUCK0_DISCORD_BOT_TOKEN", "")
TRUCK0_GATEWAY = os.environ.get("TRUCK0_OPENCLAW_GATEWAY_TOKEN", "")
TRUCK1_DISCORD = os.environ.get("TRUCK1_DISCORD_BOT_TOKEN", "")
TRUCK1_GATEWAY = os.environ.get("TRUCK1_OPENCLAW_GATEWAY_TOKEN", "")
OPENAI_KEY     = os.environ.get("OPENAI_API_KEY", "")
OPENCLAW_IMAGE = os.environ.get("OPENCLAW_IMAGE", "openclaw:local")


def _c(color, text):
    c = {
        "green":  "\033[32m", "yellow": "\033[33m", "cyan":  "\033[36m",
        "red":    "\033[31m", "bold":   "\033[1m",  "reset": "\033[0m",
    }
    return f"{c.get(color,'')}{text}{c['reset']}"


def print_banner():
    print(_c("bold", "\n" + "═" * 62))
    print(_c("bold", "  OpenClaw 선두 이전 테스트  (CARLA 없이 실행)"))
    print("═" * 62)
    print("  테스트 순서:")
    print("  1. openclaw-truck0 실행 (구 선두)")
    print("  2. 컨테이너 모니터 시작")
    print("  3. 5초 후 선두 교체 시뮬레이션")
    print("  4. Bundle 1: openclaw_base.tar (순정 이미지)")
    print("  5. Bundle 2: openclaw_session.tar (세션 이전)")
    print("  6. openclaw-truck1 실행 확인 (신 선두)")
    print("  7. openclaw-truck0 삭제 확인")
    print("  Ctrl-C = 중단\n")


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def image_exists(image: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def start_truck0_openclaw():
    """truck0의 openclaw 컨테이너를 직접 실행 (테스트용)."""
    data_dir = PROJECT_ROOT / ".openclaw-truck0"
    data_dir.mkdir(parents=True, exist_ok=True)

    # agents/platoon-a 복사 (없으면)
    src = PROJECT_ROOT / "agents" / "platoon-a"
    if src.exists() and not (data_dir / "SOUL.md").exists():
        shutil.copytree(src, data_dir, dirs_exist_ok=True)
        print(f"  agents/platoon-a → {data_dir.name}/ 복사 완료")

    subprocess.run(["docker", "rm", "-f", "openclaw-truck0"], capture_output=True)
    cmd = [
        "docker", "run", "-d",
        "--name", "openclaw-truck0",
        "--network", "host",
        "-e", "HOME=/data/openclaw",
        "-e", f"DISCORD_BOT_TOKEN={TRUCK0_DISCORD}",
        "-e", f"OPENCLAW_GATEWAY_TOKEN={TRUCK0_GATEWAY}",
        "-e", f"OPENAI_API_KEY={OPENAI_KEY}",
        "-e", "OPENCLAW_GATEWAY_PORT=18789",
        "-v", f"{data_dir.resolve()}:/data/openclaw",
        "-v", f"{str(PROJECT_ROOT/'bridge')}:/project/scripts:ro",
        OPENCLAW_IMAGE,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        print(f"  {_c('green','✓')} openclaw-truck0 시작됨")
        return True
    else:
        print(f"  {_c('yellow','!')} openclaw-truck0: {r.stderr.strip()[:100]}")
        return False


def test_no_docker():
    """Docker 없이 replicator 로직만 검증."""
    print(_c("cyan", "\n[no-docker] 로직 검증 모드"))

    from openclaw_migration.replicator import (
        LeaderMigrator, SESSION_TAR_TX_PATH, SESSION_TAR_RX_PATH
    )

    # 더미 데이터 디렉터리 생성
    old_data = PROJECT_ROOT / ".openclaw-truck0"
    old_data.mkdir(parents=True, exist_ok=True)
    (old_data / "SOUL.md").write_text("# Dummy SOUL for truck0\n")
    (old_data / "session_state.json").write_text('{"tick": 42, "status": "cruising"}\n')
    print(f"  더미 data 생성: {old_data}")

    new_agent = PROJECT_ROOT / "agents" / "truck1"

    # session tar 생성 테스트 (docker commit 없이 파일만)
    from openclaw_migration.replicator import create_session_tar, _v2v_transfer
    print(_c("cyan", "\n[test] session tar 생성..."))
    create_session_tar(
        container_name="openclaw-truck0",
        openclaw_data_dir=old_data,
        new_truck_id="truck1",
        new_agent_dir=new_agent,
    )

    print(_c("cyan", "\n[test] V2V 전송..."))
    _v2v_transfer(SESSION_TAR_TX_PATH, SESSION_TAR_RX_PATH, "openclaw_session.tar")

    # tar 내용 검증
    import tarfile
    print(_c("cyan", "\n[test] tar 내용 검증:"))
    with tarfile.open(SESSION_TAR_RX_PATH, "r:gz") as tar:
        for m in tar.getmembers()[:15]:
            print(f"  {m.name}")

    print(_c("green", "\n✓ 로직 검증 완료 (Docker 없이)"))
    print(f"  session tar: {SESSION_TAR_RX_PATH}")


def run_full_test(auto_reset: bool = False):
    """Docker가 있는 환경에서 전체 이전 테스트."""
    from openclaw_migration.monitor import ContainerMonitor
    from openclaw_migration.replicator import LeaderMigrator, ensure_base_tar
    from openclaw_migration.reset import reset

    # ── 1. truck0 openclaw 실행 ──────────────────────────────────────────────
    print(f"\n{_c('bold','[1] openclaw-truck0 시작 중...')}")
    if image_exists(OPENCLAW_IMAGE):
        started = start_truck0_openclaw()
        if started:
            time.sleep(2)
    else:
        print(f"  {_c('yellow','!')} 이미지 {OPENCLAW_IMAGE} 없음 — 컨테이너 시작 스킵")

    # ── 2. 컨테이너 모니터 ───────────────────────────────────────────────────
    monitor = ContainerMonitor(poll_interval=1.0)
    print(f"\n{_c('bold','[2] 컨테이너 상태:')}")
    time.sleep(1)
    monitor.print_status()

    # ── 3. 대기 ─────────────────────────────────────────────────────────────
    print(f"\n{_c('bold','[3] 5초 후 선두 교체 시뮬레이션...')}")
    for i in range(5, 0, -1):
        print(f"    {i}...", end="\r", flush=True)
        time.sleep(1)
    print()
    print(f"{'─'*62}")
    print("  선두 교체 트리거: truck0 → truck1")
    print(f"{'─'*62}\n")

    # ── 4 & 5. 이전 실행 ────────────────────────────────────────────────────
    migrator = LeaderMigrator(
        old_truck_id="truck0",
        new_truck_id="truck1",
        new_agent_dir=PROJECT_ROOT / "agents" / "truck1",
        old_openclaw_data_dir=PROJECT_ROOT / ".openclaw-truck0",
        new_openclaw_data_dir=PROJECT_ROOT / ".openclaw-truck1",
        discord_token=TRUCK1_DISCORD,
        gateway_token=TRUCK1_GATEWAY,
        openai_api_key=OPENAI_KEY,
    )
    migrator.migrate(blocking=True)

    # ── 6. 결과 확인 ────────────────────────────────────────────────────────
    print(f"\n{_c('bold','[6] 최종 컨테이너 상태:')}")
    time.sleep(3)
    monitor.print_status()

    # ── 7. 구 선두 삭제 시뮬레이션 ──────────────────────────────────────────
    print(f"\n{_c('bold','[7] CARLA 합류 완료 시뮬레이션 → truck0 openclaw 삭제')}")
    time.sleep(1)
    migrator.cleanup_old()

    print(f"\n{_c('bold','[최종] 컨테이너 상태:')}")
    time.sleep(2)
    monitor.print_status()

    # ── 결과 요약 ────────────────────────────────────────────────────────────
    print(f"\n{'═'*62}")
    if migrator._success:
        print(_c("green", "  ✓ 테스트 성공!"))
        print("  - openclaw-truck0 → session tar 캡처 완료")
        print("  - V2V 전송 완료")
        print("  - openclaw-truck1 기동 완료")
        print("  - openclaw-truck0 삭제 완료")
    else:
        print(_c("red", "  ✗ 테스트 실패"))
    print("═" * 62)

    # ── 리셋 ────────────────────────────────────────────────────────────────
    if auto_reset:
        print()
        reset()
    else:
        print(f"\n  리셋: python3 openclaw_migration/test_migration.py --reset")
        print(f"  모니터: python3 openclaw_migration/monitor.py")

    return migrator._success


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 선두 이전 테스트")
    parser.add_argument("--reset",     action="store_true", help="테스트 후 또는 단독으로 리셋")
    parser.add_argument("--no-docker", action="store_true", help="Docker 없이 로직만 테스트")
    args = parser.parse_args()

    if args.reset and not args.no_docker:
        from openclaw_migration.reset import reset
        reset()
        return

    print_banner()

    if args.no_docker:
        test_no_docker()
        return

    if not docker_available():
        print(_c("yellow", "Docker 데몬 없음 — --no-docker 모드로 전환"))
        test_no_docker()
        return

    try:
        run_full_test(auto_reset=args.reset)
    except KeyboardInterrupt:
        print("\n\n중단됨. 리셋: python3 openclaw_migration/test_migration.py --reset")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단됨.")
