#!/usr/bin/env python3
"""
OpenClaw Replicator — 선두 교체 시 세션 이전
==============================================
구조:
  Bundle 1: openclaw_base.tar   — 순정 OpenClaw 이미지 (고정, 한번만 배포)
  Bundle 2: openclaw_session.tar — 순정 제외 나머지 전부
                                   (AGENTS.md, SOUL.md, TOOLS.md, SKILL.md,
                                    vehicle_destinations.json,
                                    platoon_decision_context.json,
                                    .openclaw 워크스페이스 전체)

흐름:
  truck0(구 선두) → session tar 생성 → truck1(신 선두)로 전송
  truck1에서: base + session 로드 → openclaw 컨테이너 재기동
  truck0 CARLA 후미 합류 완료 후 → truck0의 openclaw 컨테이너 삭제
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT   = Path(__file__).parent.parent
BRIDGE_DIR     = str(PROJECT_ROOT / "bridge")
CHUNK_SIZE     = 64 * 1024

# .env 자동 로드
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

OPENCLAW_IMAGE      = os.environ.get("OPENCLAW_IMAGE", "openclaw:local")
BASE_TAR_PATH       = PROJECT_ROOT / ".transfer" / "openclaw_base.tar"
SESSION_TAR_TX_PATH = PROJECT_ROOT / ".transfer" / "tx" / "openclaw_session.tar"
SESSION_TAR_RX_PATH = PROJECT_ROOT / ".transfer" / "rx" / "openclaw_session.tar"

# ── 색상 출력 ─────────────────────────────────────────────────────────────────
def _c(color, text):
    colors = {
        "reset": "\033[0m", "bold": "\033[1m", "cyan": "\033[36m",
        "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"

# ── V2V 전송 (청크 복사 에뮬레이션) ──────────────────────────────────────────
def _v2v_transfer(src: Path, dst: Path, label: str):
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = src.stat().st_size
    transferred = 0
    with open(src, "rb") as sf, open(dst, "wb") as df:
        while True:
            chunk = sf.read(CHUNK_SIZE)
            if not chunk:
                break
            df.write(chunk)
            transferred += len(chunk)
            pct = transferred / total * 100
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            print(
                f"\r  [V2V] {label:28s} [{bar}] {pct:5.1f}%"
                f"  {transferred/1024/1024:.1f}/{total/1024/1024:.1f} MB",
                end="", flush=True,
            )
    print(
        f"\r  [V2V] {label:28s} [{'█'*20}] 100.0%"
        f"  {total/1024/1024:.1f} MB  {_c('green','✓')}"
    )

# ── Bundle 1: 순정 이미지 tar (최초 1회만 생성) ───────────────────────────────
def ensure_base_tar():
    """순정 OpenClaw 이미지를 한번만 저장. 이미 있으면 스킵."""
    BASE_TAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    if BASE_TAR_PATH.exists():
        print(f"  [base] 이미 존재: {BASE_TAR_PATH.name} ({BASE_TAR_PATH.stat().st_size/1024/1024:.1f} MB)")
        return True
    print(_c("cyan", f"\n[Bundle 1] 순정 이미지 저장 중 → {BASE_TAR_PATH.name}"))
    t = time.time()
    r = subprocess.run(
        ["docker", "save", OPENCLAW_IMAGE, "-o", str(BASE_TAR_PATH)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(_c("red", f"  docker save 실패: {r.stderr}"))
        return False
    print(f"  완료: {BASE_TAR_PATH.stat().st_size/1024/1024:.1f} MB ({time.time()-t:.1f}s)")
    return True

# ── Bundle 2: 세션 tar (선두 교체마다 생성) ───────────────────────────────────
def create_session_tar(
    container_name: str,
    openclaw_data_dir: Path,
    new_truck_id: str,
    new_agent_dir: Path,
):
    """
    현재 선두 컨테이너의 세션을 캡처해 tar로 묶는다.
    포함 내용:
      - 컨테이너 내 /data/openclaw 전체 (워크스페이스 + 세션)
      - agents/{new_truck_id}/ 의 AGENTS.md, SOUL.md, TOOLS.md, SKILL.md
    """
    SESSION_TAR_TX_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(_c("cyan", f"\n[Bundle 2] 세션 tar 생성 중 → {SESSION_TAR_TX_PATH.name}"))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1) 현재 컨테이너 data 디렉터리 복사
        data_dst = tmp / "openclaw_data"
        if openclaw_data_dir.exists():
            shutil.copytree(openclaw_data_dir, data_dst, dirs_exist_ok=True)
            print(f"  [data ] {openclaw_data_dir} → openclaw_data/")
        else:
            data_dst.mkdir(parents=True)
            print(f"  [data ] {openclaw_data_dir} 없음 — 빈 디렉터리 사용")

        # 2) 새 선두용 agent 파일 덮어쓰기
        agent_dst = tmp / "agent_config"
        if new_agent_dir.exists():
            shutil.copytree(new_agent_dir, agent_dst, dirs_exist_ok=True)
            print(f"  [agent] {new_agent_dir.name}/ → agent_config/")
        else:
            agent_dst.mkdir(parents=True)
            print(f"  [agent] {new_agent_dir} 없음 — 스킵")

        # 3) 메타정보 저장
        meta = {
            "from_container": container_name,
            "new_truck_id": new_truck_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "openclaw_image": OPENCLAW_IMAGE,
        }
        (tmp / "migration_meta.json").write_text(json.dumps(meta, indent=2))

        # 4) tar.gz 압축
        with tarfile.open(SESSION_TAR_TX_PATH, "w:gz") as tar:
            tar.add(tmpdir, arcname=".")

    size_kb = SESSION_TAR_TX_PATH.stat().st_size / 1024
    print(f"  완료: {size_kb:.1f} KB")
    return True

# ── 수신측: base + session 로드 후 openclaw 기동 ──────────────────────────────
def load_and_run_openclaw(
    new_container_name: str,
    new_openclaw_data_dir: Path,
    discord_token: str,
    gateway_token: str,
    openai_api_key: str = "",
    gateway_port: int = 18789,
):
    """
    신 선두 컨테이너에서 실행:
      1. base tar 로드 (이미지 없을 때만)
      2. session tar 압축 해제 → data dir 복원
      3. openclaw 컨테이너 기동
    """
    print(_c("cyan", f"\n[load] {new_container_name} openclaw 기동 중..."))

    # 1) base 이미지 로드 (이미 있으면 스킵)
    check = subprocess.run(
        ["docker", "image", "inspect", OPENCLAW_IMAGE],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        if SESSION_TAR_RX_PATH.parent.parent.exists():
            base_rx = SESSION_TAR_RX_PATH.parent.parent / "openclaw_base.tar"
        else:
            base_rx = BASE_TAR_PATH
        if base_rx.exists():
            print(f"  docker load base 이미지...")
            r = subprocess.run(
                ["docker", "load", "-i", str(base_rx)],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"  {_c('green', '이미지 로드 완료')}: {r.stdout.strip()}")
            else:
                print(_c("red", f"  이미지 로드 실패: {r.stderr.strip()}"))
        else:
            print(_c("yellow", f"  base tar 없음 ({base_rx}) — 이미지가 이미 있기를 기대"))

    # 2) session tar 압축 해제
    new_openclaw_data_dir.mkdir(parents=True, exist_ok=True)
    if SESSION_TAR_RX_PATH.exists():
        with tarfile.open(SESSION_TAR_RX_PATH, "r:gz") as tar:
            # openclaw_data/ → data dir
            members = [m for m in tar.getmembers() if m.name.startswith("./openclaw_data")]
            for m in members:
                m.name = m.name.replace("./openclaw_data", ".", 1)
            if members:
                tar.extractall(str(new_openclaw_data_dir), members=members)
                print(f"  [data ] session 복원 → {new_openclaw_data_dir}")

            # agent_config/ → data dir 위에 덮어쓰기
            tar2 = tarfile.open(SESSION_TAR_RX_PATH, "r:gz")
            members2 = [m for m in tar2.getmembers() if m.name.startswith("./agent_config")]
            for m in members2:
                m.name = m.name.replace("./agent_config", ".", 1)
            if members2:
                tar2.extractall(str(new_openclaw_data_dir), members=members2)
                print(f"  [agent] agent_config 복원 → {new_openclaw_data_dir}")
            tar2.close()
    else:
        print(_c("yellow", f"  session tar 없음 ({SESSION_TAR_RX_PATH}) — data dir만 사용"))

    # 3) 기존 컨테이너 제거 후 재기동
    subprocess.run(["docker", "rm", "-f", new_container_name], capture_output=True)
    cmd = [
        "docker", "run", "-d",
        "--name", new_container_name,
        "--network", "host",
        "-e", "HOME=/data/openclaw",
        "-e", f"DISCORD_BOT_TOKEN={discord_token}",
        "-e", f"OPENCLAW_GATEWAY_TOKEN={gateway_token}",
        "-e", f"OPENAI_API_KEY={openai_api_key}",
        "-e", f"OPENCLAW_GATEWAY_PORT={gateway_port}",
        "-v", f"{new_openclaw_data_dir.resolve()}:/data/openclaw",
        "-v", f"{BRIDGE_DIR}:/project/scripts:ro",
        OPENCLAW_IMAGE,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        print(f"  {_c('green', f'{new_container_name} 실행됨')} (id={r.stdout.strip()[:12]})")
        return True
    else:
        print(_c("red", f"  {new_container_name} 실행 실패: {r.stderr.strip()}"))
        return False

# ── 구 선두 openclaw 컨테이너 삭제 ───────────────────────────────────────────
def delete_old_openclaw(old_container_name: str):
    """CARLA 후미 합류 완료 후 호출: 구 선두의 openclaw 컨테이너 삭제."""
    print(_c("cyan", f"\n[cleanup] {old_container_name} 컨테이너 삭제 중..."))
    r = subprocess.run(
        ["docker", "rm", "-f", old_container_name],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print(f"  {_c('green', f'{old_container_name} 삭제 완료')}")
    else:
        print(_c("yellow", f"  {old_container_name} 삭제 실패 (이미 없을 수 있음): {r.stderr.strip()}"))

# ── 전체 복제 오케스트레이터 ─────────────────────────────────────────────────
class LeaderMigrator:
    """
    선두 교체 시 OpenClaw 이전을 담당하는 클래스.

    사용법:
        migrator = LeaderMigrator(
            old_truck_id="truck0",
            new_truck_id="truck1",
        )
        migrator.migrate()            # 비동기 (백그라운드 스레드)
        migrator.wait()               # 완료 대기
        migrator.cleanup_old()        # CARLA 합류 후 구 선두 삭제
    """

    def __init__(
        self,
        old_truck_id: str = "truck0",
        new_truck_id: str = "truck1",
        old_agent_dir: Path | None = None,
        new_agent_dir: Path | None = None,
        old_openclaw_data_dir: Path | None = None,
        new_openclaw_data_dir: Path | None = None,
        discord_token: str = "",
        gateway_token: str = "",
        openai_api_key: str = "",
        gateway_port: int = 18789,
    ):
        self.old_truck_id   = old_truck_id
        self.new_truck_id   = new_truck_id
        self.old_container  = f"openclaw-{old_truck_id}"
        self.new_container  = f"openclaw-{new_truck_id}"

        self.old_agent_dir  = old_agent_dir or (PROJECT_ROOT / "agents" / "platoon-a")
        self.new_agent_dir  = new_agent_dir or (PROJECT_ROOT / "agents" / "truck1")
        self.old_data_dir   = old_openclaw_data_dir or (PROJECT_ROOT / f".openclaw-{old_truck_id}")
        self.new_data_dir   = new_openclaw_data_dir or (PROJECT_ROOT / f".openclaw-{new_truck_id}")

        self.discord_token  = discord_token  or os.environ.get("TRUCK1_DISCORD_BOT_TOKEN", "")
        self.gateway_token  = gateway_token  or os.environ.get("TRUCK1_OPENCLAW_GATEWAY_TOKEN", "")
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.gateway_port   = gateway_port

        self._done_event    = threading.Event()
        self._success       = False

    def migrate(self, blocking: bool = False):
        """이전 실행. blocking=False이면 백그라운드 스레드로 실행."""
        if blocking:
            self._run()
        else:
            t = threading.Thread(target=self._run, daemon=True)
            t.start()

    def wait(self, timeout: float = 120.0) -> bool:
        """완료 대기. 성공 여부 반환."""
        return self._done_event.wait(timeout=timeout) and self._success

    def cleanup_old(self):
        """CARLA 합류 완료 후 호출: 구 선두 openclaw 컨테이너 삭제."""
        delete_old_openclaw(self.old_container)

    def _run(self):
        try:
            print(_c("bold", "\n" + "═" * 60))
            print(_c("bold", f"  OpenClaw 선두 이전: {self.old_truck_id} → {self.new_truck_id}"))
            print("═" * 60)

            # Step 1: 순정 base tar 확보 (최초 1회)
            if not ensure_base_tar():
                raise RuntimeError("base tar 생성 실패")

            # Step 2: 세션 tar 생성
            if not create_session_tar(
                container_name=self.old_container,
                openclaw_data_dir=self.old_data_dir,
                new_truck_id=self.new_truck_id,
                new_agent_dir=self.new_agent_dir,
            ):
                raise RuntimeError("session tar 생성 실패")

            # Step 3: V2V 전송
            print(_c("cyan", "\n[V2V] 세션 tar 전송 중..."))
            _v2v_transfer(
                SESSION_TAR_TX_PATH,
                SESSION_TAR_RX_PATH,
                "openclaw_session.tar",
            )

            # Step 4: 신 선두에서 openclaw 기동
            if not load_and_run_openclaw(
                new_container_name=self.new_container,
                new_openclaw_data_dir=self.new_data_dir,
                discord_token=self.discord_token,
                gateway_token=self.gateway_token,
                openai_api_key=self.openai_api_key,
                gateway_port=self.gateway_port,
            ):
                raise RuntimeError("openclaw 기동 실패")

            self._success = True
            print("\n" + "═" * 60)
            print(_c("green", "  OpenClaw 이전 완료 ✓"))
            print(_c("yellow", f"  구 선두({self.old_container})는 CARLA 합류 완료 후 삭제됩니다"))
            print("═" * 60 + "\n")

        except Exception as e:
            print(_c("red", f"\n  이전 실패: {e}"))
            self._success = False
        finally:
            self._done_event.set()


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw 선두 이전 도구")
    parser.add_argument("--old-truck",    default="truck0",         help="구 선두 truck ID")
    parser.add_argument("--new-truck",    default="truck1",         help="신 선두 truck ID")
    parser.add_argument("--new-agent",    default=None,             help="신 선두 agent 디렉터리")
    parser.add_argument("--old-data",     default=None,             help="구 선두 openclaw data 디렉터리")
    parser.add_argument("--new-data",     default=None,             help="신 선두 openclaw data 디렉터리")
    parser.add_argument("--ensure-base",  action="store_true",      help="base tar만 생성하고 종료")
    parser.add_argument("--cleanup-old",  action="store_true",      help="구 선두 컨테이너 삭제")
    args = parser.parse_args()

    if args.ensure_base:
        ensure_base_tar()
        return 0

    if args.cleanup_old:
        delete_old_openclaw(f"openclaw-{args.old_truck}")
        return 0

    m = LeaderMigrator(
        old_truck_id=args.old_truck,
        new_truck_id=args.new_truck,
        new_agent_dir=Path(args.new_agent) if args.new_agent else None,
        old_openclaw_data_dir=Path(args.old_data) if args.old_data else None,
        new_openclaw_data_dir=Path(args.new_data) if args.new_data else None,
    )
    m.migrate(blocking=True)
    return 0 if m._success else 1


if __name__ == "__main__":
    sys.exit(main())
