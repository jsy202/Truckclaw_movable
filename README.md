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

## 라이선스

[LICENSE](scenario/LICENSE) 참고
