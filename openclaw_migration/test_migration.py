#!/usr/bin/env python3
"""
OpenClaw 선두 이전 테스트 — CARLA 없이 실행 가능
truck0(현재 선두) → truck1(신 선두) OpenClaw 세션 이전 전 과정 테스트

실행:
  cd /home/jsy202/Downloads/Truckclaw-improve
  python3 openclaw_migration/test_migration.py
  python3 openclaw_migration/test_migration.py --reset
"""
from __future__ import annotations

import os, sys, time, threading, subprocess, shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openclaw_migration.replicator import LeaderMigrator, ensure_base_tar
from openclaw_migration.monitor    import ContainerMonitor
from openclaw_migration.reset      import reset

# .env 로드
for name in [".env.leader-rotation", ".env"]:
    p = PROJECT_ROOT / name
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
        break

TRUCK0_DISCORD = os.environ.get("TRUCK0_DISCORD_BOT_TOKEN", "")
TRUCK0_GATEWAY = os.environ.get("TRUCK0_OPENCLAW_GATEWAY_TOKEN", "")
TRUCK1_DISCORD = os.environ.get("TRUCK1_DISCORD_BOT_TOKEN", "")
TRUCK1_GATEWAY = os.environ.get("TRUCK1_OPENCLAW_GATEWAY_TOKEN", "")
OPENAI_KEY     = os.environ.get("OPENAI_API_KEY", "")


def _c(color, text):
    c = {"green":"\033[32m","yellow":"\033[33m","cyan":"\033[36m",
         "bold":"\033[1m","reset":"\033[0m","red":"\033[31m"}
    return f"{c.get(color,'')}{text}{c['reset']}"


def print_banner():
    print(_c("bold", "\n" + "═"*60))
    print(_c("bold", "  OpenClaw 선두 이전 테스트  (CARLA 없이 실행)"))
    print("═"*60)
    print("  테스트 순서:")
    print("  1. openclaw-truck0 실행 (현재 선두)")
    print("  2. 컨테이너 모니터 시작")
    print("  3. 선두 교체 시뮬레이션 (3초 후 자동)")
    print("  4. Bundle 1: openclaw_base.tar (순정, 최초 1회)")
    print("  5. Bundle 2: openclaw_session.tar (세션+에이전트 파일)")
    print("  6. V2V 전송 (tx → rx)")
    print("  7. openclaw-truck1 실행 확인")
    print("  8. openclaw-truck0 삭제 (합류 완료 시뮬레이션)")
    print("  --reset 옵션으로 초기화\n")


def start_truck0_openclaw():
    """truck0 openclaw 시작 (테스트용 — vehicle 컨테이너 생략)"""
    data_dir = PROJECT_ROOT / ".openclaw-truck0"
    data_dir.mkdir(parents=True, exist_ok=True)

    # agents/platoon-a 복사
    src = PROJECT_ROOT / "agents" / "platoon-a"
    if src.exists() and not (data_dir / "SOUL.md").exists():
        shutil.copytree(src, data_dir, dirs_exist_ok=True)

    subprocess.run(["docker", "rm", "-f", "openclaw-truck0"], capture_output=True)

    # openclaw:local 이미지 존재 확인
    check = subprocess.run(
        ["docker", "image", "inspect", "openclaw:local"],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"  {_c('yellow','!')} openclaw:local 이미지 없음 — 컨테이너 시작 스킵 (파일 복사만 수행)")
        return False

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
        "openclaw:local",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        print(f"  {_c('green','✓')} openclaw-truck0 시작됨")
        return True
    else:
        print(f"  {_c('yellow','!')} openclaw-truck0 시작 실패: {r.stderr.strip()[:80]}")
        return False


def simulate_carla_join_complete(migrator: LeaderMigrator):
    """CARLA 합류 완료 시뮬레이션: 5초 후 truck0 openclaw 삭제"""
    def _delayed():
        time.sleep(5)
        print(f"\n[test] CARLA 합류 완료 시뮬레이션 → truck0 openclaw 삭제")
        migrator.cleanup_old()
    threading.Thread(target=_delayed, daemon=True).start()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset",        action="store_true", help="완료 후 리셋")
    parser.add_argument("--skip-truck0",  action="store_true", help="truck0 시작 생략")
    parser.add_argument("--no-cleanup",   action="store_true", help="truck0 openclaw 삭제 스킵")
    args = parser.parse_args()

    print_banner()

    # ── 1. truck0 openclaw 시작 ──────────────────────────────────────────────
    if not args.skip_truck0:
        print("[1] openclaw-truck0 시작 중...")
        start_truck0_openclaw()
        time.sleep(2)
    else:
        print("[1] truck0 시작 스킵")

    # ── 2. 컨테이너 모니터 시작 ──────────────────────────────────────────────
    monitor = ContainerMonitor(poll_interval=1.0)
    print("\n[2] 컨테이너 모니터 시작")
    time.sleep(1)
    print("\n현재 상태:")
    monitor.print_status()

    # ── 3. 선두 교체 대기 ────────────────────────────────────────────────────
    print(f"\n[3] 3초 후 선두 교체 시뮬레이션 시작...")
    for i in range(3, 0, -1):
        print(f"    {i}...", end="\r")
        time.sleep(1)

    print(f"\n{'─'*60}")
    print("  선두 교체 감지: truck0 → truck1")
    print(f"{'─'*60}\n")

    # ── 4~7. LeaderMigrator 실행 ─────────────────────────────────────────────
    migrator = LeaderMigrator(
        old_truck_id="truck0",
        new_truck_id="truck1",
        new_agent_dir=PROJECT_ROOT / "agents" / "truck1",
        old_openclaw_data_dir=PROJECT_ROOT / ".openclaw-truck0",
        new_openclaw_data_dir=PROJECT_ROOT / ".openclaw-truck1",
        discord_token=TRUCK1_DISCORD,
        gateway_token=TRUCK1_GATEWAY,
        openai_api_key=OPENAI_KEY,
        gateway_port=18790,
    )
    migrator.migrate(blocking=True)

    # ── 8. 합류 완료 시뮬레이션 → truck0 openclaw 삭제 ──────────────────────
    if not args.no_cleanup:
        print("\n[8] CARLA 후미 합류 완료 시뮬레이션 → truck0 openclaw 삭제")
        if migrator._success:
            simulate_carla_join_complete(migrator)
            time.sleep(7)

    # ── 결과 확인 ────────────────────────────────────────────────────────────
    print("\n[결과] 최종 컨테이너 상태:")
    monitor.print_status()

    print("\n" + "─"*60)
    if migrator._success:
        print(_c("green", "  ✓ 테스트 성공!"))
        print("  - openclaw-truck1 실행 중 (신 선두)")
        print("  - openclaw-truck0 삭제됨 (구 선두)")
    else:
        print(_c("red", "  ✗ 테스트 실패"))
    print("─"*60)

    # ── 리셋 ─────────────────────────────────────────────────────────────────
    if args.reset:
        input("\n  Enter를 누르면 리셋합니다...")
        reset()
    else:
        print("\n  리셋: python3 openclaw_migration/test_migration.py --reset")
        print("  또는: python3 openclaw_migration/reset.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n중단됨. 리셋: python3 openclaw_migration/reset.py")
