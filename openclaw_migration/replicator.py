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
import re
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

def _default_gateway_port(truck_id: str) -> int:
    """Map truck0 -> 18789, truck1 -> 18790, etc."""
    if truck_id.startswith("truck"):
        suffix = truck_id.removeprefix("truck")
        if suffix.isdigit():
            return 18789 + int(suffix)
    return int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789"))

# ── thread-local 로깅 (watch 스크립트용) ─────────────────────────────────────
_tlog = threading.local()

def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)

def _log(msg: str = "", end: str = "\n") -> None:
    lf = getattr(_tlog, 'logfile', None)
    if lf:
        try:
            lf.write(_strip_ansi(msg) + end)
            lf.flush()
        except Exception:
            pass

def _print(msg: str = "", end: str = "\n") -> None:
    """watch 모드: 로그 파일에만 출력. standalone: 터미널 출력."""
    if getattr(_tlog, 'logfile', None):
        _log(msg, end)
    else:
        print(msg, end=end, flush=True)

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
            line = (
                f"\r  [V2V] {label:28s} [{bar}] {pct:5.1f}%"
                f"  {transferred/1024/1024:.1f}/{total/1024/1024:.1f} MB"
            )
            lf = getattr(_tlog, 'logfile', None)
            if lf:
                lf.write(line); lf.flush()
            else:
                print(line, end="", flush=True)
    done_line = (
        f"\r  [V2V] {label:28s} [{'█'*20}] 100.0%"
        f"  {total/1024/1024:.1f} MB  {_c('green','✓')}\n"
    )
    lf = getattr(_tlog, 'logfile', None)
    if lf:
        lf.write(done_line); lf.flush()
    else:
        print(done_line, end="", flush=True)

# ── Bundle 1: 이미지 tar 저장 (매 이전마다 재생성) ──────────────────────────
def ensure_base_tar():
    """OpenClaw 이미지를 tar로 저장. 항상 재생성."""
    BASE_TAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _print(_c("cyan", f"\n[Bundle 1] 이미지 저장 중 → {BASE_TAR_PATH.name}"))
    t = time.time()
    r = subprocess.run(
        ["docker", "save", OPENCLAW_IMAGE, "-o", str(BASE_TAR_PATH)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        _print(_c("red", f"  docker save 실패: {r.stderr}"))
        return False
    _print(f"  완료: {BASE_TAR_PATH.stat().st_size/1024/1024:.1f} MB ({time.time()-t:.1f}s)")
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
    _print(_c("cyan", f"\n[Bundle 2] 세션 tar 생성 중 → {SESSION_TAR_TX_PATH.name}"))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1) 에이전트 설정 파일만 포함 (워크스페이스 전체 복사 안 함 — base tar에 이미 포함)
        agent_dst = tmp / "agent_config"
        if new_agent_dir.exists():
            shutil.copytree(new_agent_dir, agent_dst, dirs_exist_ok=True)
            _print(f"  [agent] {new_agent_dir.name}/ → agent_config/ {_c('green', '✓')}")
        else:
            agent_dst.mkdir(parents=True)
            _print(f"  [agent] {new_agent_dir} 없음 — 스킵")

        # 2) 호스트 data 디렉터리에서 상태 JSON만 선택적으로 복사 (대용량 바이너리 제외)
        data_dst = tmp / "openclaw_data"
        data_dst.mkdir(parents=True, exist_ok=True)
        state_patterns = ["*.json", "*.md", "*.txt", "*.yaml", "*.yml", ".env*"]
        if openclaw_data_dir.exists():
            for pattern in state_patterns:
                for f in openclaw_data_dir.glob(pattern):
                    if f.is_file():
                        shutil.copy2(f, data_dst / f.name)
            _print(f"  [data ] 상태 파일 복사 완료 ({len(list(data_dst.iterdir()))}개)")
        else:
            _print(f"  [data ] {openclaw_data_dir.name} 없음 — 스킵")

        # 3) 토큰 + 메타정보
        discord_token  = os.environ.get("DISCORD_BOT_TOKEN", "")
        gateway_token  = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
        openai_key     = os.environ.get("OPENAI_API_KEY", "")
        meta = {
            "from_container": container_name,
            "new_truck_id": new_truck_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "openclaw_image": OPENCLAW_IMAGE,
            "discord_bot_token": discord_token,
            "openclaw_gateway_token": gateway_token,
        }
        (tmp / "migration_meta.json").write_text(json.dumps(meta, indent=2))
        (tmp / ".env").write_text(
            f"DISCORD_BOT_TOKEN={discord_token}\n"
            f"OPENCLAW_GATEWAY_TOKEN={gateway_token}\n"
            f"OPENAI_API_KEY={openai_key}\n"
        )
        _print(f"  [token] 토큰 포함 완료")

        # 4) tar 패킹 (압축 없음 — 속도 우선)
        with tarfile.open(SESSION_TAR_TX_PATH, "w:") as tar:
            tar.add(tmpdir, arcname=".")

    size_kb = SESSION_TAR_TX_PATH.stat().st_size / 1024
    _print(f"  완료: {size_kb:.0f} KB")
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
    _print(_c("cyan", f"\n[load] {new_container_name} openclaw 기동 중..."))

    # 1) base 이미지 로드 (항상 재로드 — 전송된 rx tar 우선)
    base_rx = SESSION_TAR_RX_PATH.parent / "openclaw_base.tar"
    if not base_rx.exists():
        base_rx = BASE_TAR_PATH
    if base_rx.exists():
        _print(f"  [image] docker load 중 ({base_rx.stat().st_size/1024/1024:.0f} MB)...")
        r = subprocess.run(
            ["docker", "load", "-i", str(base_rx)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            _print(f"  {_c('green', '이미지 로드 완료')}: {r.stdout.strip()}")
        else:
            _print(_c("red", f"  이미지 로드 실패: {r.stderr.strip()}"))
    else:
        _print(_c("yellow", f"  base tar 없음 ({base_rx})"))

    # 2) session tar 압축 해제
    new_openclaw_data_dir.mkdir(parents=True, exist_ok=True)
    if SESSION_TAR_RX_PATH.exists():
        with tarfile.open(SESSION_TAR_RX_PATH, "r:") as tar:
            # ① 루트 .env 추출 → 토큰 읽기
            env_member = next(
                (m for m in tar.getmembers() if m.name in ("./.env", ".env")), None
            )
            if env_member:
                f = tar.extractfile(env_member)
                if f:
                    for line in f.read().decode().splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip()
                        if k == "DISCORD_BOT_TOKEN" and v:
                            discord_token = v
                        elif k == "OPENCLAW_GATEWAY_TOKEN" and v:
                            gateway_token = v
                        elif k == "OPENAI_API_KEY" and v:
                            openai_api_key = v
                    _print(f"  [token] session tar .env에서 토큰 로드 완료 (len={len(discord_token)})")

            # ② openclaw_data/ → data dir
            members = [m for m in tar.getmembers() if m.name.startswith("./openclaw_data")]
            if members:
                # [수정] 호스트에서 직접 압축해제 시 기존 root 소유 파일들과 충돌 가능.
                # 임시 디렉토리에 풀고 docker cp로 밀어넣거나, 
                # 여기서는 안전하게 임시 디렉토리에 풀고 shutil로 복사하되 실패 시 권한 수정 시도
                with tempfile.TemporaryDirectory() as extract_tmp:
                    tar.extractall(extract_tmp, members=members)
                    src_data = Path(extract_tmp) / "openclaw_data"
                    # 권한 문제 회피를 위해 목적지 디렉토리의 파일들을 미리 정리하거나 chown 시도할 수 있지만,
                    # 가장 확실한 건 목적지를 비우거나 docker cp 활용.
                    # 여기서는 간단하게 개별 파일 복사 시도 (shutil.copytree dirs_exist_ok=True)
                    try:
                        shutil.copytree(src_data, new_openclaw_data_dir, dirs_exist_ok=True)
                        _print(f"  [data ] session 복원 → {new_openclaw_data_dir} {_c('green', '✓')}")
                    except Exception as e:
                        _print(_c("yellow", f"  [data ] 복사 중 오류(권한 등) 발생 -> 도커를 이용해 강제 복사 시도..."))
                        # 신규 컨테이너가 아직 안떴으므로, 임시 컨테이너로 볼륨 권한 수정 및 복사
                        subprocess.run([
                            "docker", "run", "--rm",
                            "-v", f"{new_openclaw_data_dir.resolve()}:/dst",
                            "-v", f"{src_data.resolve()}:/src",
                            "busybox", "sh", "-c", "cp -af /src/. /dst/ && chown -R 1000:1000 /dst"
                        ])
                        _print(f"  [data ] session 강제 복원 완료")

        # ③ agent_config/ → data dir 위에 덮어쓰기 (별도 open — members 재사용 불가)
        with tarfile.open(SESSION_TAR_RX_PATH, "r:") as tar2:
            members2 = [m for m in tar2.getmembers() if m.name.startswith("./agent_config")]
            if members2:
                with tempfile.TemporaryDirectory() as agent_tmp:
                    tar2.extractall(agent_tmp, members=members2)
                    src_agent = Path(agent_tmp) / "agent_config"
                    try:
                        shutil.copytree(src_agent, new_openclaw_data_dir, dirs_exist_ok=True)
                        _print(f"  [agent] agent_config 복원 → {new_openclaw_data_dir} {_c('green', '✓')}")
                    except Exception as e:
                        _print(_c("yellow", f"  [agent] 복사 중 오류 발생 -> 도커를 이용해 강제 복사 시도..."))
                        subprocess.run([
                            "docker", "run", "--rm",
                            "-v", f"{new_openclaw_data_dir.resolve()}:/dst",
                            "-v", f"{src_agent.resolve()}:/src",
                            "busybox", "sh", "-c", "cp -af /src/. /dst/ && chown -R 1000:1000 /dst"
                        ])
                        _print(f"  [agent] agent_config 강제 복원 완료")
    else:
        _print(_c("yellow", f"  session tar 없음 ({SESSION_TAR_RX_PATH}) — data dir만 사용"))

    # 3) 기존 컨테이너 제거 후 재기동
    subprocess.run(["docker", "rm", "-f", new_container_name], capture_output=True)
    
    # [수정] OpenAI Codex 모델 설정 명령 추가 (fix_summary 반영)
    model_setup = "openclaw models set openai-codex/gpt-5.4"
    gateway_run = f"openclaw gateway run --port {gateway_port} --allow-unconfigured"
    if gateway_token:
        gateway_run += f" --token {gateway_token}"
    
    # 봇이 죽지 않도록 tail -f /dev/null 유지
    entrypoint_cmd = f"bash -lc '{model_setup} && {gateway_run}' & tail -f /dev/null"

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
        entrypoint_cmd,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        _print(f"  {_c('green', f'{new_container_name} 실행됨')} (id={r.stdout.strip()[:12]})")
        return True
    else:
        _print(_c("red", f"  {new_container_name} 실행 실패: {r.stderr.strip()}"))
        return False

# ── 구 선두 openclaw 컨테이너 삭제 ───────────────────────────────────────────
def delete_old_openclaw(old_container_name: str):
    """CARLA 후미 합류 완료 후 호출: 구 선두의 openclaw 컨테이너 삭제."""
    _print(_c("cyan", f"\n[cleanup] {old_container_name} 컨테이너 삭제 중..."))
    r = subprocess.run(
        ["docker", "rm", "-f", old_container_name],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        _print(f"  {_c('green', f'{old_container_name} 삭제 완료')}")
    else:
        _print(_c("yellow", f"  {old_container_name} 삭제 실패 (이미 없을 수 있음): {r.stderr.strip()}"))

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
        gateway_port: int | None = None,
    ):
        self.old_truck_id   = old_truck_id
        self.new_truck_id   = new_truck_id
        self.old_container  = f"openclaw-{old_truck_id}"
        self.new_container  = f"openclaw-{new_truck_id}"

        self.old_agent_dir  = old_agent_dir or (PROJECT_ROOT / "agents" / "platoon-a")
        self.new_agent_dir  = new_agent_dir or (PROJECT_ROOT / "agents" / "truck1")
        self.old_data_dir   = old_openclaw_data_dir or (PROJECT_ROOT / f".openclaw-{old_truck_id}")
        self.new_data_dir   = new_openclaw_data_dir or (PROJECT_ROOT / f".openclaw-{new_truck_id}")

        self.discord_token  = discord_token  or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.gateway_token  = gateway_token  or os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.gateway_port   = gateway_port if gateway_port is not None else _default_gateway_port(new_truck_id)

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
        tx_path = PROJECT_ROOT / ".transfer" / "tx" / ".progress.log"
        rx_path = PROJECT_ROOT / ".transfer" / "rx" / ".progress.log"
        tx_path.parent.mkdir(parents=True, exist_ok=True)
        rx_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # ── TX 페이즈: 저장 + 전송 ────────────────────────────────────────
            with open(tx_path, "a") as tx_f:
                _tlog.logfile = tx_f
                _log("=== TX START ===")
                _print(_c("bold", "\n" + "═" * 60))
                _print(_c("bold", f"  OpenClaw 선두 이전: {self.old_truck_id} → {self.new_truck_id}"))
                _print("═" * 60)

                if not ensure_base_tar():
                    raise RuntimeError("base tar 생성 실패")

                if not create_session_tar(
                    container_name=self.old_container,
                    openclaw_data_dir=self.old_data_dir,
                    new_truck_id=self.new_truck_id,
                    new_agent_dir=self.new_agent_dir,
                ):
                    raise RuntimeError("session tar 생성 실패")

                # base 이미지 tar V2V 전송
                base_rx_path = PROJECT_ROOT / ".transfer" / "rx" / "openclaw_base.tar"
                _print(_c("cyan", "\n[V2V] 베이스 이미지 tar 전송 중..."))
                _v2v_transfer(BASE_TAR_PATH, base_rx_path, "openclaw_base.tar")

                # 세션 tar V2V 전송
                _print(_c("cyan", "\n[V2V] 세션 tar 전송 중..."))
                _v2v_transfer(SESSION_TAR_TX_PATH, SESSION_TAR_RX_PATH, "openclaw_session.tar")
                _print(_c("green", "\n  [TX] 전송 완료 ✓"))
                _tlog.logfile = None

            # ── RX 페이즈: 로드 + 기동 ────────────────────────────────────────
            with open(rx_path, "a") as rx_f:
                _tlog.logfile = rx_f
                _log("=== RX START ===")

                if not load_and_run_openclaw(
                    new_container_name=self.new_container,
                    new_openclaw_data_dir=self.new_data_dir,
                    discord_token=self.discord_token,
                    gateway_token=self.gateway_token,
                    openai_api_key=self.openai_api_key,
                    gateway_port=self.gateway_port,
                ):
                    raise RuntimeError("openclaw 기동 실패")

                _print(f"  {_c('green', f'{self.new_container} 시작됨 ✓')}")
                _tlog.logfile = None

            self._success = True
            print("\n" + "═" * 60)
            print(_c("green", "  OpenClaw 이전 완료 ✓"))
            print(_c("yellow", f"  구 선두({self.old_container})는 CARLA 합류 완료 후 삭제됩니다"))
            print("═" * 60 + "\n")

        except Exception as e:
            _tlog.logfile = None
            print(_c("red", f"\n  이전 실패: {e}"))
            self._success = False
        finally:
            _tlog.logfile = None
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
    parser.add_argument("--gateway-port", type=int, default=None,   help="신 선두 OpenClaw gateway 포트")
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
        gateway_port=args.gateway_port,
    )
    m.migrate(blocking=True)
    return 0 if m._success else 1


if __name__ == "__main__":
    sys.exit(main())
