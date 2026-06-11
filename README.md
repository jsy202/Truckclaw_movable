# Truckclaw 🚛  — Leader Rotation Edition

**AI 에이전트가 협상하는 트럭 군집 + 선두 교체 시뮬레이션**

CARLA 자율주행 시뮬레이터(Town06 고속도로)에서 3대 트럭 군집이 실시간으로 주행하는 동안,
Discord AI 봇(OpenClaw)이 협상을 수행합니다.

이 프로젝트의 핵심 기능은 두 가지입니다.

1. **Leader Rotation** — 선두 차량(truck0)이 후미로 이동할 때, 선두에서 실행 중이던
   OpenClaw AI 봇 컨테이너를 다음 선두(truck1)로 **무중단 이전**합니다.
   봇 토큰은 단 1개만 사용하며, session tar 파일에 담겨 truck1으로 전달됩니다.

2. **CARLA 물리 이동** — truck0이 옆 차선으로 이동 → 감속 → truck2(후미) 뒤에 합류하는
   전체 물리 시나리오를 PID 컨트롤러로 자동 수행합니다.

> 이 README 하나만 읽으면 프로젝트의 구조, 동작 원리, 실행 방법, 코드 흐름을
> 모두 이해할 수 있도록 작성되었습니다.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [전체 아키텍처](#2-전체-아키텍처)
3. [디렉터리 구조 및 파일 역할](#3-디렉터리-구조-및-파일-역할)
4. [사전 요구사항](#4-사전-요구사항)
5. [환경 설정](#5-환경-설정)
6. [실행 방법 단계별](#6-실행-방법-단계별)
7. [Leader Rotation 동작 원리](#7-leader-rotation-동작-원리)
8. [코드 흐름 상세](#8-코드-흐름-상세)
9. [브리지 REST API](#9-브리지-rest-api)
10. [포트 정리](#10-포트-정리)
11. [주요 파라미터](#11-주요-파라미터)
12. [CARLA 없이 테스트](#12-carla-없이-테스트)
13. [트러블슈팅](#13-트러블슈팅)

---

## 시스템 개요

```
군집 구성 (3대)
  truck0 (현재 선두, OpenClaw 봇 실행)
  truck1 (다음 선두, 평소 봇 없음)
  truck2 (후미)

선두 교체 요청 발생 시:
  ① OpenClaw 이전  : truck0 세션 tar → truck1에서 봇 재기동 (봇 토큰 1개 이동)
  ② CARLA 물리 이동: truck0이 옆 차선 → 감속 → truck2 뒤에 합류
  ③ 정리           : truck0이 후미 합류 완료 → truck0의 openclaw 컨테이너 삭제
```

**봇 토큰은 단 1개.** session tar 안에 토큰이 포함되어 이동하므로
truck1에서 별도 토큰 설정 없이 동일 Discord 봇이 그대로 재기동됩니다.

---

## 아키텍처

### Docker-in-Docker (DinD) 구조

```
Host (Linux)
│
├── vehicle-truck0  (Docker 컨테이너 — DinD)
│   ├── /var/run/docker.sock 마운트 (Host Docker 소켓)
│   └── [내부 Docker] openclaw-truck0   ← 현재 선두 봇
│
├── vehicle-truck1  (Docker 컨테이너 — DinD)
│   ├── /var/run/docker.sock 마운트
│   └── [내부 Docker] openclaw-truck1   ← 교체 후 봇 (평소엔 없음)
│
└── bridge-server   (Docker 컨테이너)
    └── REST API (포트 18801)  ← 협상 상태 관리
```

### 전체 통신 흐름

```
Discord 채널
   │
   ▼
openclaw-truck0  ──[협상]──  (상대 봇)
   │
   ▼
브리지 서버 (18801)  ←──  POST /leader_rotation  (선두 교체 요청)
   │
   ├─→ POST :18803/leader_rotation  (CARLA 트리거)
   │
   ▼
CARLA 시뮬레이터 (18803 수신)
   │  ① 갭 확보 → ② 차선 변경 → ③ 감속 → ④ truck2 뒤 합류
   │
   └─→ delete_old_openclaw("openclaw-truck0")   ← 합류 완료 후
```

### OpenClaw session tar 이전 흐름

```
truck0 (구 선두)                             truck1 (신 선두)
──────────────────────────────────────────────────────────────
1. ensure_base_tar()                         [대기]
   openclaw:local 이미지 → openclaw_base.tar

2. create_session_tar()
   .openclaw-truck0/ + agents/truck1/ + .env
   → .transfer/tx/openclaw_session.tar

3. V2V 전송 (청크 복사)
   tx/openclaw_session.tar ─────────────────→ rx/openclaw_session.tar

4.                                            load_and_run_openclaw()
                                              ① base 이미지 로드 (없을 때만)
                                              ② session tar 압축 해제
                                                 - .env 에서 토큰 추출
                                                 - openclaw_data/ 복원
                                                 - agent_config/ 덮어쓰기
                                              ③ docker run openclaw-truck1
```

---

## 디렉터리 구조

```
Truckclaw-movable/
│
├── scenario/
│   └── examples/
│       ├── leader_rotation_scenario.py   ★ Leader Rotation CARLA 시나리오
│       └── two_platoon_truck_scenario.py   기존 2군집 이송 시나리오
│
├── openclaw_migration/
│   ├── replicator.py          ★ OpenClaw 이전 핵심 로직
│   └── test_migration.py      ★ CARLA 없이 이전 테스트
│
├── bridge/
│   ├── platoon_bridge_server.py   브리지 REST API (포트 18801)
│   │                              ★ /leader_rotation 엔드포인트 추가
│   └── platoon_bridge_ctl.py      브리지 CLI 클라이언트
│
├── agents/
│   ├── platoon-a/             기존 Platoon A 에이전트 설정
│   └── truck1/                ★ 신 선두(truck1)용 에이전트 설정
│       ├── AGENTS.md
│       ├── SOUL.md
│       ├── TOOLS.md
│       ├── data/vehicle_destinations.json
│       └── skills/platoon-negotiator/SKILL.md
│
├── vehicle/
│   ├── Dockerfile             DinD vehicle 컨테이너 이미지
│   └── entrypoint.sh          truck0만 초기 openclaw 기동
│
├── docker-compose.yml         ★ DinD + 단일 토큰 구조
├── .env.example               환경변수 예시 (토큰 1개)
├── .env                       실제 토큰 (git-ignored)
│
├── .transfer/                 V2V 전송 버퍼 (git-ignored)
│   ├── openclaw_base.tar      순정 이미지 (최초 1회 생성)
│   ├── tx/
│   │   └── openclaw_session.tar   전송 측 세션 tar
│   └── rx/
│       └── openclaw_session.tar   수신 측 세션 tar
│
├── .openclaw-truck0/          truck0 OpenClaw 워크스페이스 (git-ignored)
├── .openclaw-truck1/          truck1 OpenClaw 워크스페이스 (git-ignored)
│
├── platoon_destinations.json  차량 목적지 설정
└── config/
    └── simulation.json        속도/간격 파라미터
```

---

## 사전 요구사항

| 항목 | 버전/조건 |
|------|-----------|
| CARLA | 0.9.6 (Town06 맵 필요) |
| Python | 3.10+ |
| Docker | 24.0+ (Host에 설치) |
| Docker Compose | v2 |
| OpenClaw 이미지 | `openclaw:local` (Host Docker에 로드된 상태) |
| Discord 봇 토큰 | **1개** (truck0 → truck1으로 이전) |
| CARLA Python API | `/opt/carla-0.9.6/PythonAPI/carla` |

---

## 환경 설정

### 1) `.env` 파일 생성

```bash
cd /path/to/Truckclaw-movable
cp .env.example .env
```

`.env` 파일 편집:

```dotenv
# Discord 봇 토큰 (1개 — truck0에서 시작, 교체 시 truck1으로 이전)
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# OpenClaw 게이트웨이 토큰 (OpenClaw 설치에 따라 다름)
OPENCLAW_GATEWAY_TOKEN=your_openclaw_gateway_token_here

# OpenClaw Docker 이미지 태그
OPENCLAW_IMAGE=openclaw:local

# OpenAI API 키 (OpenClaw가 GPT를 사용하는 경우)
OPENAI_API_KEY=
```

> **⚠️ 보안 주의**: `.env` 파일은 `.gitignore`에 포함되어 있습니다. 절대 커밋하지 마세요.

### 2) OpenClaw 이미지 확인

```bash
docker image ls openclaw:local
# REPOSITORY   TAG     IMAGE ID   CREATED   SIZE
# openclaw     local   ...
```

이미지가 없으면 OpenClaw를 빌드/로드한 뒤 진행하세요.

### 3) OpenAI OAuth 로그인

OpenAI Codex OAuth는 OpenClaw의 model provider 인증입니다. Gateway 접속 토큰
(`OPENCLAW_GATEWAY_TOKEN`)과는 별개입니다.

먼저 OpenClaw gateway가 올바른 컨테이너 이름으로 떠 있는지 확인합니다.

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep openclaw
curl http://127.0.0.1:18789/healthz
```

truck0에서 OAuth 로그인을 진행하려면 다음 명령을 실행합니다.

```bash
docker exec -it openclaw-truck0 bash -lc \
  'HOME=/data/openclaw openclaw models auth login --provider openai-codex --set-default'
```

OpenClaw가 로그인 URL을 출력하면 호스트 브라우저에서 열고 OpenAI 로그인을 완료하세요.
헤드리스/원격 환경에서 로컬 callback을 받을 수 없으면, OpenClaw가 안내하는 대로
리디렉션된 URL 또는 code를 터미널에 붙여 넣으면 됩니다. OAuth 토큰은
`.openclaw-truck0/.openclaw/agents/<agentId>/agent/auth-profiles.json` 아래에 저장되고,
선두 교체 시 session tar에 포함되어 truck1로 이전됩니다.

로그인 후 확인:

```bash
docker exec -it openclaw-truck0 bash -lc \
  'HOME=/data/openclaw openclaw models status --probe'
```

---

## 실행 방법 (단계별)

### Step 1 — CARLA 시뮬레이터 실행

```bash
# 헤드리스 모드 (서버 환경)
/opt/carla-0.9.6/CarlaUE4.sh -RenderOffScreen

# GUI 모드 (로컬 테스트)
/opt/carla-0.9.6/CarlaUE4.sh
```

CARLA가 포트 2000에서 준비될 때까지 약 15~30초 대기합니다.

### Step 2 — Docker 컨테이너 실행 (봇 + 브리지)

```bash
docker compose up -d
```

실행되는 컨테이너:
- `platoon-bridge-server` — 브리지 REST API (포트 18801)
- `vehicle-truck0` — DinD, 내부에서 `openclaw-truck0` 자동 시작
- `vehicle-truck1` — DinD, 대기 상태 (봇 없음)

상태 확인:
```bash
docker compose ps
docker logs vehicle-truck0 -f
docker logs vehicle-truck1 -f
```

### Step 3 — OpenClaw 봇 상태 확인

```bash
# truck0 내부에서 openclaw 컨테이너 확인
docker exec vehicle-truck0 docker ps

# EXPECTED:
# CONTAINER ID   IMAGE           COMMAND   ... NAMES
# xxxxxxxxxxxx   openclaw:local  ...           openclaw-truck0
```

### Step 4 — CARLA Leader Rotation 시나리오 실행

```bash
export PYTHONPATH=$PYTHONPATH:/opt/carla-0.9.6/PythonAPI/carla

# 기본 실행 (수동 트리거 대기)
python3 scenario/examples/leader_rotation_scenario.py

# 옵션
python3 scenario/examples/leader_rotation_scenario.py \
    --host 127.0.0.1 \          # CARLA 서버 주소
    --port 2000 \               # CARLA 포트
    --auto-trigger-s 30 \       # 30초 후 자동으로 선두 교체 트리거
    --no-openclaw               # OpenClaw 이전 없이 CARLA 물리 이동만 테스트
```

시나리오가 시작되면 트럭 3대가 Town06에 스폰되어 군집 주행을 시작합니다.

### Step 5 — 선두 교체 트리거

**방법 A) HTTP 요청으로 직접 트리거:**
```bash
curl -s -X POST http://127.0.0.1:18803/leader_rotation \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

**방법 B) 브리지 서버를 통한 트리거:**
```bash
curl -s -X POST http://127.0.0.1:18801/leader_rotation \
  -H "Content-Type: application/json" \
  -d '{"status": "started", "old_truck_id": "truck0", "new_truck_id": "truck1"}' \
  | python3 -m json.tool
```

**방법 C) `--auto-trigger-s` 옵션 사용 (Step 4 참고)**

### Step 6 — 진행 상황 모니터링

```bash
# CARLA 시나리오 로그 (터미널에서 실시간 출력)

# 브리지 상태 확인
curl -s http://127.0.0.1:18801/leader_rotation | python3 -m json.tool

# truck1 OpenClaw 기동 확인
docker exec vehicle-truck1 docker ps
# openclaw-truck1 이 보이면 성공!

# truck1 봇 로그
docker exec vehicle-truck1 docker logs openclaw-truck1 -f
```

### Step 7 — 정리

```bash
docker compose down
docker volume prune -f
```

---

## Leader Rotation 상세

### CARLA 물리 시나리오 상태 머신

```
CRUISE → MIGRATE → GAP → LC → SLOWDOWN → REJOIN → DONE
  │         │        │     │      │          │
  │         │        │     │      │          └─ truck0이 truck2 뒤에 합류
  │         │        │     │      └─ truck0 감속 (truck2 속도 아래로)
  │         │        │     └─ truck0 차선 변경 (옆 차선, PID 조향)
  │         │        └─ truck0 분리(platoon.split), 갭 자연스럽게 생성
  │         └─ OpenClaw 이전 시작 (백그라운드 스레드)
  └─ 군집 정상 주행 중
```

#### 각 상태 설명

| 상태 | 동작 |
|------|------|
| `CRUISE` | 군집 정상 주행, 선두 교체 트리거 대기 |
| `MIGRATE` | `LeaderMigrator.migrate()` 호출 (백그라운드), CARLA는 주행 지속 |
| `GAP` | `platoon.split(0, 0)` — truck0 분리, truck1이 새 선두, 갭 자연 형성 |
| `LC` | PID 컨트롤러로 truck0을 옆 차선으로 이동 (횡방향 ≥ 2.5m 달성 시 완료) |
| `SLOWDOWN` | truck0이 truck2보다 느리게 감속 (truck2 뒤 목표 위치 확보) |
| `REJOIN` | PID 컨트롤러로 truck0을 원래 차선으로 복귀 (truck2 뒤) |
| `DONE` | `platoon.attach_tail_vehicle()` → FollowerController 재부착 → openclaw-truck0 삭제 |

### OpenClaw session tar 내용

```
openclaw_session.tar.gz
├── openclaw_data/           truck0 워크스페이스 전체 (.openclaw-truck0/)
│   ├── .openclaw/           OpenClaw 세션 파일
│   └── ...
├── agent_config/            신 선두(truck1)용 에이전트 설정
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── TOOLS.md
│   └── skills/platoon-negotiator/SKILL.md
├── migration_meta.json      이전 메타정보 (타임스탬프, 컨테이너명 등)
└── .env                     Discord 봇 토큰 (DISCORD_BOT_TOKEN 등)
```

### 토큰 이동 경로

```
.env 파일 (Host)
  │  DISCORD_BOT_TOKEN=xxx
  │
  ▼ create_session_tar()
openclaw_session.tar/.env  ← 토큰 포함
  │
  ▼ V2V 전송
rx/openclaw_session.tar
  │
  ▼ load_and_run_openclaw()  ← tar에서 .env 읽어 토큰 추출
docker run -e DISCORD_BOT_TOKEN=xxx openclaw:local
  │
  ▼
openclaw-truck1 기동 (동일 봇 토큰으로)
```

### LeaderMigrator API

```python
from openclaw_migration.replicator import LeaderMigrator

# 생성
migrator = LeaderMigrator(
    old_truck_id="truck0",
    new_truck_id="truck1",
    gateway_port=18789,
)

# 비동기 실행 (백그라운드 스레드)
migrator.migrate(blocking=False)

# 완료 대기 (최대 120초)
success = migrator.wait(timeout=120.0)

# CARLA 물리 합류 완료 후 구 선두 openclaw 삭제
migrator.cleanup_old()
```

**CLI 사용:**

```bash
# 선두 교체 (truck0 → truck1)
python3 openclaw_migration/replicator.py --old-truck truck0 --new-truck truck1

# base tar만 미리 생성 (최초 1회)
python3 openclaw_migration/replicator.py --ensure-base

# 구 선두 openclaw 컨테이너 삭제
python3 openclaw_migration/replicator.py --cleanup-old --old-truck truck0
```

---

## 브리지 REST API

### 기존 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|------------|--------|------|
| `/health` | GET | 서버 상태 확인 |
| `/snapshot` | GET | 전체 상태 조회 |
| `/platoons/{id}` | GET | 특정 군집 정보 |
| `/platoons/{id}/transfer-candidates` | GET | 이송 후보 목록 |
| `/transfers` | POST | 이송 요청 생성 |
| `/transfers/{id}/accept` | POST | 수락 |
| `/transfers/{id}/commit` | POST | 커밋 → CARLA 트리거 |
| `/transfers/{id}/carla_complete` | POST | 물리 합류 완료 보고 |
| `/reload` | POST | `platoon_destinations.json` 재로드 |

### Leader Rotation 엔드포인트 (신규)

| 엔드포인트 | 메서드 | 설명 |
|------------|--------|------|
| `/leader_rotation` | POST | 선두 교체 요청 생성 |
| `/leader_rotation` | GET | 현재 교체 상태 조회 |

**POST `/leader_rotation` 요청 예시:**

```bash
# 교체 시작 알림
curl -X POST http://127.0.0.1:18801/leader_rotation \
  -H "Content-Type: application/json" \
  -d '{
    "status": "started",
    "old_truck_id": "truck0",
    "new_truck_id": "truck1"
  }'

# 교체 완료 알림
curl -X POST http://127.0.0.1:18801/leader_rotation \
  -H "Content-Type: application/json" \
  -d '{
    "status": "complete",
    "old_truck_id": "truck0",
    "new_truck_id": "truck1"
  }'
```

**GET `/leader_rotation` 응답 예시:**

```json
{
  "status": "complete",
  "old_truck_id": "truck0",
  "new_truck_id": "truck1",
  "timestamp": "2026-05-17T10:30:00Z"
}
```

---

## 포트 정리

| 포트 | 용도 |
|------|------|
| 2000 | CARLA 시뮬레이터 |
| 18789 | truck0 OpenClaw 게이트웨이 |
| 18790 | truck1 OpenClaw 게이트웨이 |
| 18801 | 브리지 REST API |
| 18802 | CARLA 이송 트리거 (2군집 시나리오) |
| 18803 | CARLA Leader Rotation 트리거 |

---

## 주요 파라미터

### `config/simulation.json`

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `sync_speed_kmh` | 20 | 군집 기본 주행 속도 |
| `approach_fast_kmh` | 65 | 접근 시 최대 속도 |
| `target_gap_m` | 6 | 차선 변경 전 목표 갭 |
| `follow_dist_m` | 13 | CACC 추종 목표 거리 |
| `platoon_spacing_m` | 16 | 군집 내 차량 간격 |
| `merge_timeout_s` | 120 | 합류 타임아웃 (초) |

### Leader Rotation 시나리오 파라미터 (`leader_rotation_scenario.py`)

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `CRUISE_SPEED_KMH` | 30 | 군집 순항 속도 |
| `TARGET_GAP_M` | 8.0 | truck2 뒤에서 목표 거리 (m) |
| `LC_STEER` | 0.35 | 차선 변경 조향각 |
| `SLOWDOWN_TARGET_KMH` | 15 | 감속 목표 속도 |
| `TRIGGER_PORT` | 18803 | 선두 교체 트리거 수신 포트 |

---

## 테스트 (CARLA 없이)

### OpenClaw 이전 단독 테스트

CARLA 없이 Docker 이전 로직만 검증합니다.

```bash
# Docker 있는 환경에서 전체 이전 테스트
python3 openclaw_migration/test_migration.py

# Docker 없이 tar 생성/해제 로직만 테스트
python3 openclaw_migration/test_migration.py --no-docker

# 테스트 후 컨테이너 및 디렉터리 정리
python3 openclaw_migration/test_migration.py --reset
```

### 브리지 서버 단독 테스트

```bash
# Mock 모드 (CARLA 없이 상태 머신 전체 진행)
MOCK_CARLA=true python3 bridge/platoon_bridge_server.py

# 다른 터미널에서 API 테스트
curl -s http://127.0.0.1:18801/health
curl -s http://127.0.0.1:18801/snapshot | python3 -m json.tool
```

### session tar 내용 확인

```bash
# tar 내용 목록 출력
tar -tzvf .transfer/tx/openclaw_session.tar | head -30

# .env 추출하여 토큰 확인
python3 - <<'EOF'
import tarfile
with tarfile.open(".transfer/tx/openclaw_session.tar", "r:gz") as tar:
    env = next((m for m in tar.getmembers() if m.name in ("./.env", ".env")), None)
    if env:
        print(tar.extractfile(env).read().decode())
EOF
```

---

## 트러블슈팅

### openclaw-truck1이 시작되지 않음

```bash
# session tar가 정상적으로 전송되었는지 확인
ls -lh .transfer/rx/openclaw_session.tar

# truck1 vehicle 컨테이너 로그 확인
docker logs vehicle-truck1 --tail 50

# openclaw 이미지가 있는지 확인
docker exec vehicle-truck1 docker image ls openclaw:local
```

### 토큰이 비어있어 Discord 연결 실패

```bash
# session tar 안의 .env 토큰 확인
python3 openclaw_migration/replicator.py --ensure-base

# .env 파일이 올바른지 확인
cat .env | grep DISCORD_BOT_TOKEN
```

### CARLA 시나리오 연결 실패

```bash
# CARLA 서버 실행 중인지 확인
netstat -tlnp | grep 2000

# Python 경로 확인
echo $PYTHONPATH
python3 -c "import carla; print(carla.__version__)"
```

### 브리지 서버 포트 충돌

```bash
# 기존 프로세스 종료
kill $(lsof -ti:18801) 2>/dev/null
kill $(lsof -ti:18803) 2>/dev/null

# 재시작
docker compose restart bridge-server
```

### Docker 소켓 권한 오류 (DinD)

```bash
# vehicle 컨테이너가 Host docker.sock에 접근 가능한지 확인
docker exec vehicle-truck0 docker ps
# permission denied 오류 시:
sudo chmod 666 /var/run/docker.sock
```

### base tar 재생성

base tar가 손상되었거나 이미지가 업데이트된 경우:

```bash
rm .transfer/openclaw_base.tar
python3 openclaw_migration/replicator.py --ensure-base
```


---

## Post-project Agent Migration & Leader Rotation Verification

> 이 섹션은 **연구 종료 후(2026-10)** 수행한 검증 작업이다.
> 위의 내용(설계, 실행 방법, 시연 흐름)은 연구 당시 결과이며, 이번에 재실행하거나 수정하지 않았다.
> 아래에 적은 결함은 **이번 검증에서 처음 발견한 것**이다. 연구 당시 코드는 tag `research-baseline`(`59f35ea`)에 보존되어 있다.

### 검증 목적

선두 교체는 두 가지가 맞물려 일어난다.

- OpenClaw 세션 이전(Docker): 논리·소프트웨어 측
- CARLA 후미 합류: 물리 측

이 검증은 두 측면의 **상태가 어긋나지 않는지**, 그리고 **이전 실패, 지연, 중복 요청이 안전하게 처리되는지**를 CARLA와 Docker 없이 확인한다.

### 테스트 구조

```text
[Production]  scenario coordinator ─▶ LeaderMigrator ─▶ docker save/load/run
              agent/scenario ─▶ bridge /leader_rotation ─▶ CARLA :18803
[Test]        coordinator (carla 모듈 stub) ─▶ 실제 LeaderMigrator ─▶ FakeDocker (subprocess.run 대체)
              pytest ─▶ 실제 bridge Handler (in-process) ─▶ FakeRotationReceiver
```

production 코드에 테스트용 분기는 넣지 않았다. Docker 호출은 테스트에서만 `subprocess.run`을 monkeypatch해 가로챈다.

### 발견한 결함 (실제 실행으로 재현)

| ID | 결함 | 상태 |
|---|---|---|
| DEF-M01 | bridge가 `started` 시점에 truck1을 선두로 승격. 이 시점에는 물리 이동과 agent 이전이 아직 없음 | 수정 `0c4a530` |
| DEF-M02 | 선두 교체가 실패해도 bridge는 truck1을 선두로 유지 | 수정 `0c4a530` |
| DEF-M03 | `started`가 중복되면 CARLA trigger 중복. 문서화된 흐름(agent와 시나리오가 각각 started 전송)에서 항상 발생 | 수정 `237f619` |
| DEF-M04 / M05 | started 없이 `complete`를 받음, 임의 status 문자열을 받음 | 수정 `237f619` |
| DEF-M06 | 존재하지 않는 old_leader로도 CARLA trigger | 수정 `35bd68e` |
| DEF-M07 | OpenClaw 이전이 실패하면 coordinator가 MIGRATE에서 영구 대기. `wait(0)`이 "진행 중"과 "실패"를 구분하지 못함 | 수정 `89237fd` |
| DEF-M08 | MIGRATE timeout이 없어 docker 명령이 멈추면 영구 대기 | 수정 `89237fd` (기존 120 s 재사용) |
| DEF-M09 | 이전 "성공" 판정이 `docker run -d`의 returncode뿐이고 gateway health는 확인하지 않음 | **미수정, XFAIL (DEFERRED)** |

추가로 Truckclaw-improve에서 찾은 bridge transfer 결함 11건이 이 저장소에도 그대로 있다. 실행으로 재현했고, 이 저장소에서는 수정하지 않았다(`validation/evidence/inherited_transfer_defects.txt`).

### 결과 (2026-10-01, 로컬)

| 테스트 | PASS | XFAIL | FAILED |
|---|---|---|---|
| 23 | 22 | 1 (DEF-M09) | 0 |

- 10회 연속 실행에서 매회 같은 결과였다.
- 같은 테스트를 baseline 소스에 실행하면 10 failed, 13 passed다. 실패한 10건은 위의 결함 테스트들이다.

실행: `pip install -r requirements-test.txt && python3 -m pytest`

CI는 `.github/workflows/test.yml`에 있다. 아직 push하지 않아 **GitHub에서 실행된 기록은 없다.**

### Limitations

- CARLA 물리 단계(GAP 이후)는 실행하지 않았다. 실제 Docker 및 Discord와 함께 확인하지도 않았다.
- MIGRATE timeout 뒤에도 migration 스레드는 취소되지 않는다. 늦게 성공하면 신규 컨테이너가 기동될 수 있다.
- 세션 tar에는 토큰이 평문으로 들어간다(설계상 의도).
- 위 README의 `/leader_rotation` curl 예시는 `old_truck_id`/`new_truck_id` 키를 쓴다. bridge는 `old_leader`/`new_leader`를 읽기 때문에, 예시는 기본값(truck0→truck1)으로 동작하는 것이다(정적 확인).

문서: [validation/](validation/) — requirements, interface_spec, state_machine, test_cases, traceability_matrix, test_report, limitations

---

## Real CARLA 50-Run Leader Change Physical Validation (2026-10-03)

동일한 고정 CARLA 조건에서 50회 반복 실행해 50/50 완료 및 logical/physical state consistency 50/50을 확인했다.

### 검증 결과 요약

| 지표 | 결과 | 비고 |
|---|---:|---|
| Real CARLA Runs | 50 | 독립 CARLA 서버 프로세스 그룹 격리 실행 |
| 물리 선두 교체 완료 (PASS) | 50/50 | timeout 0, crash 0, cleanup failure 0 |
| Final order correct | 50/50 | CARLA 공간 위치 기준 `[truck1, truck2, truck0]` 순서 검증 |
| Logical / physical consistency | 50/50 | 선두 컨트롤러 승계 및 최종 대열 구성 일치 |
| OpenClaw Migration | Not tested | 이미지 및 Discord 게이트웨이 환경 부재로 미검증 |
| Duplicate trigger | 0 | 0건 (트리거 멱등 처리로 50회 모두 유효 트리거 1회) |

- **주요 타이밍 (raw evidence 기반 평균/중앙값/최대값)**:
  - `trigger → migrate`: mean 0.003606 s / p50 0.003538 s / p95 0.003976 s / max 0.005020 s
  - `trigger → split` (logical_transition): mean 0.010610 s / p50 0.010270 s / p95 0.012122 s / max 0.013104 s
  - `trigger → lane_change`: mean 1.215890 s / p50 1.208961 s / p95 1.369022 s / max 1.402861 s
  - `trigger → slowdown`: mean 8.267754 s / p50 8.226030 s / p95 8.959305 s / max 9.635556 s
  - `trigger → rejoin`: mean 9.869590 s / p50 9.796522 s / p95 10.648178 s / max 11.605610 s
  - `trigger → done`: mean 9.871707 s / p50 9.798514 s / p95 10.650220 s / max 11.608280 s

### Defect 및 수정 (VAL-L01)

- **결함 증상**: 초기 smoke run에서 선두 교체 및 재합류 매뉴버는 정상 완주했으나, 플래툰이 OpenDRIVE 세그먼트 경계(road 36 vs 1149)를 가로지를 때 동일 직선 차선(lane -3)임에도 evaluator가 차선 불일치로 오인하여 실패 판정.
- **수정**: evaluator의 `same_final_lane` 검사를 CARLA `lane_id` 비교와 물리적 횡방향 오차(< 0.8m) 및 종방향 ordering 검증으로 정밀화 (`evidence_before_fix/`에 원본 보존).
- **회귀 검증**: `test_same_lane_across_connected_road_segments_is_not_rejected` 통과 후 본 50회 검증에서 50/50 완료 확인.

### Comparison with Transfer / Split

| Scenario | Repository | Real CARLA Runs | Verification |
|---|---|---:|---|
| Transfer | Truckclaw-improve | 10 | request → physical merge → logical membership |
| Split | Truckclaw_copyable | 50 | logical detach → physical separation → independent driving |
| Leader Change | Truckclaw_movable | 50 | leader/controller transition → physical maneuver → old leader rejoin |

### Limitations

- **Fixed condition**: 고정된 CARLA 조건(Town06 고정 스폰, 단일 목표 속도, 단일 날씨)에서만 실행되었습니다.
- **Single scenario configuration**: 3대 플래툰 단일 설정입니다.
- **CARLA simulation**: CARLA 0.9.13 시뮬레이션 환경 검증이며 실차 검증이 아닙니다.
- **Off-screen rendering**: `-RenderOffScreen` 모드 시뮬레이션입니다.
- **carlacola fallback**: `vehicle.carlamotors.carlacola` 모델을 사용했습니다.
- **OpenClaw Migration Not tested**: 환경 부재로 미검증되었습니다.
- **Generalization 미검증**: 다양한 환경에 대한 일반화는 검증되지 않았습니다.
- CARLA-free 회귀 테스트 결과(기존 22 passed + 1 known gateway-health xfail)는 실제 CARLA 50회 실행 통계와 별개입니다.

상세 보고서: [validation/real_carla_50runs/validation_report.md](validation/real_carla_50runs/validation_report.md)

---

## 라이선스

[LICENSE](scenario/LICENSE) 참고
