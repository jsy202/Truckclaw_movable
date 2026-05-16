# Truckclaw-improve 🚛 — 선두 교체 & OpenClaw 이전 시스템

> **CARLA 자율주행 시뮬레이터** 환경에서 트럭 3대 군집의 **선두 차량이 교체될 때**,  
> 선두에서 실행 중이던 **OpenClaw AI 에이전트를 Docker 이미지(tar)로 다음 선두에 이전**하는 시스템입니다.

---

## 📌 핵심 아이디어

```
[기존 선두: truck0]          [교체 후]
truck0 (선두, OpenClaw 실행)  →  truck1 (신 선두, OpenClaw 이전받음)
truck1                        →  truck2
truck2 (후미)                 →  truck0 (후미로 합류)
```

선두가 바뀌어도 **AI 에이전트의 기억(세션)이 끊기지 않고** 다음 선두로 이어집니다.

---

## 🎯 주요 기능

### 1. OpenClaw 이미지 분리 전송
순정 이미지와 세션 데이터를 분리해 효율적으로 전송합니다.

```
Bundle 1: openclaw_base.tar    ← 순정 OpenClaw 이미지 (고정, 최초 1회만 생성)
Bundle 2: openclaw_session.tar ← 순정 제외 나머지 전부
                                  (AGENTS.md, SOUL.md, TOOLS.md, SKILL.md,
                                   vehicle_destinations.json,
                                   .openclaw 워크스페이스 전체 세션)
```

### 2. Docker-in-Docker (DinD) 구조
각 트럭 컨테이너 안에서 OpenClaw 컨테이너를 직접 실행·관리합니다.

```
호스트
├── vehicle-truck0 (Docker + /var/run/docker.sock 공유)
│     └── openclaw-truck0  ← 현재 선두 OpenClaw 실행 중
├── vehicle-truck1 (Docker + /var/run/docker.sock 공유)
│     └── openclaw-truck1  ← 선두 교체 후 세션 이전받아 기동
└── vehicle-truck2 (Docker)
      (truck2는 순수 CACC 팔로워 — OpenClaw 없음)
```

### 3. CARLA 물리 기동 — 선두의 후미 합류
선두(truck0)가 실제로 차선을 바꾸고 감속해 후미에 붙습니다.

```
CRUISE → GAP 확보 → 옆 차선 이동(LC) → 감속(SLOWDOWN) → 원래 차선 복귀 + 합류(REJOIN) → DONE
```

### 4. 합류 완료 후 구 선두 OpenClaw 자동 삭제
truck0이 truck2 뒤에 완전히 합류하면 `openclaw-truck0` 컨테이너가 자동으로 삭제됩니다.

---

## 🏗️ 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                        Discord 채널                          │
│   openclaw-truck0 (구 선두)  →  openclaw-truck1 (신 선두)    │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Bridge Server (port 18801)                      │
│   협상 상태 관리 + /leader_rotation 엔드포인트               │
└──────────────────────────────┬──────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         port 18802       port 18803       OpenClaw 이전
         merge 트리거    leader_rotation   replicator.py
                          트리거
              │                │
              └────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│         CARLA 시나리오 — leader_rotation_scenario.py         │
│   truck0: 옆 차선 → 감속 → truck2 뒤 합류                   │
│   truck1: 새 선두로 LeadNavigator 자동 승격                  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│              CARLA Town06 (port 2000)                        │
│         실제 트럭 3대 물리 시뮬레이션                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 📂 프로젝트 구조

```
Truckclaw-improve/
│
├── scenario/examples/
│   ├── leader_rotation_scenario.py   ★ 선두 교체 CARLA 시나리오 (핵심)
│   └── two_platoon_truck_scenario.py   기존 두 군집 이송 시나리오
│
├── openclaw_migration/               ★ OpenClaw 이전 패키지 (핵심)
│   ├── replicator.py                   LeaderMigrator — tar 생성·전송·기동·삭제
│   ├── monitor.py                      컨테이너 상태 실시간 모니터
│   ├── reset.py                        초기 상태 리셋
│   └── test_migration.py               CARLA 없이 이전 테스트
│
├── bridge/
│   ├── platoon_bridge_server.py      ★ /leader_rotation 엔드포인트 추가
│   ├── platoon_bridge_ctl.py           CLI 클라이언트
│   └── platoon_dialogue_guard.py       Discord 메시지 가드
│
├── agents/
│   ├── platoon-a/                      기존 Platoon A 에이전트
│   ├── platoon-b/                      기존 Platoon B 에이전트
│   └── truck1/                       ★ 신 선두(truck1) 에이전트 파일
│
├── vehicle/
│   ├── Dockerfile                    ★ DinD 기반 truck 컨테이너
│   └── entrypoint.sh                   openclaw 자동 기동 스크립트
│
├── config/
│   └── simulation.json                 속도/간격/스폰 파라미터
│
├── docker-compose.yml                ★ DinD 구조 (vehicle-truck0/1)
├── run_leader_rotation.sh            ★ 원클릭 실행 스크립트
├── carla_start.sh                      CARLA 서버 시작
├── carla_stop.sh                       전체 종료
├── platoon_destinations.json           군집 목적지 설정
└── .env.example                        환경변수 예시
```

---

## 🔄 선두 교체 전체 흐름

### Phase 1 — 트리거
```
키보드 'L' 입력
또는
curl -X POST http://127.0.0.1:18803/leader_rotation \
  -H "Content-Type: application/json" \
  -d '{"old_leader":"truck0","new_leader":"truck1","status":"started"}'
```

### Phase 2 — OpenClaw 세션 캡처 (백그라운드)
```
replicator.py 실행:
  1. openclaw_base.tar 확보 (최초 1회 — 순정 이미지)
  2. openclaw_session.tar 생성:
       - truck0의 /data/openclaw 전체 (워크스페이스 + 세션)
       - agents/truck1/ 에이전트 파일 (AGENTS.md, SOUL.md 등)
       - migration_meta.json (이전 메타정보)
  3. V2V 전송: .transfer/tx/ → .transfer/rx/
  4. truck1에서 base 이미지 로드 + session tar 압축 해제
  5. openclaw-truck1 컨테이너 기동 (port 18790)
```

### Phase 3 — CARLA 물리 기동 (동시 진행)
```
truck0 분리 (Core.Platoon.split(0,0))
    ↓
GAP 확보: truck1-truck0 간격 18m 이상 확보
    ↓
LC (차선 변경): truck0 → 옆 차선으로 PID 제어
    ↓
SLOWDOWN: 옆 차선에서 감속 → truck2 후방으로 이동
    ↓
REJOIN: 원래 차선 복귀 → truck2 뒤에 합류
    ↓
FollowerController 재장착 → 군집 복귀
```

### Phase 4 — 마무리
```
브리지 /leader_rotation {"status":"complete"} 호출
openclaw-truck0 컨테이너 자동 삭제
truck1이 새 선두로 Discord 협상 계속
```

---

## 🚀 실행 방법

### 사전 준비
```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에서 토큰 입력:
#   TRUCK0_DISCORD_BOT_TOKEN=...
#   TRUCK0_OPENCLAW_GATEWAY_TOKEN=...
#   TRUCK1_DISCORD_BOT_TOKEN=...
#   TRUCK1_OPENCLAW_GATEWAY_TOKEN=...

# 2. CARLA PythonAPI 경로 확인
# /opt/carla-0.9.6/PythonAPI/carla 에 설치되어 있어야 함
```

### 실행 순서
```bash
# 터미널 1: CARLA 서버
bash carla_start.sh

# 터미널 2: 브리지 서버
python3 bridge/platoon_bridge_server.py

# 터미널 3: 선두 교체 시나리오
bash run_leader_rotation.sh

# 또는 자동 트리거 (30초 후)
python3 scenario/examples/leader_rotation_scenario.py --auto-trigger-s 30

# OpenClaw 없이 CARLA만 테스트
python3 scenario/examples/leader_rotation_scenario.py --no-openclaw
```

### OpenClaw 이전만 단독 테스트 (CARLA 불필요)
```bash
python3 openclaw_migration/test_migration.py
```

---

## ⚙️ 설정 파라미터 (`config/simulation.json`)

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `sync_speed_kmh` | 18 km/h | 군집 기본 주행 속도 |
| `normal_follow_gap_m` | 12 m | 일반 차간 거리 |
| `open_gap_m` | 20 m | 선두 교체 전 확보 간격 |
| `open_gap_ready_m` | 18 m | 간격 확보 완료 판정 |
| `target_gap_m` | 13 m | 합류 목표 거리 |
| `platoon_spacing_m` | 18 m | 초기 스폰 간격 |

---

## 🔌 포트 정리

| 포트 | 용도 |
|---|---|
| 2000 | CARLA 서버 |
| 18801 | 브리지 서버 REST API |
| 18802 | CARLA merge 트리거 (기존 호환) |
| 18803 | 선두 교체 트리거 전용 |
| 18789 | openclaw-truck0 게이트웨이 |
| 18790 | openclaw-truck1 게이트웨이 |

---

## 🛠️ 브리지 API — 신규 엔드포인트

### `POST /leader_rotation`
선두 교체 시작/완료 알림
```json
// 시작
{"old_leader": "truck0", "new_leader": "truck1", "status": "started"}

// 완료
{"old_leader": "truck0", "new_leader": "truck1", "status": "complete"}
```

### `GET /leader_rotation`
현재 선두 교체 상태 조회
```json
{
  "old_leader": "truck0",
  "new_leader": "truck1",
  "status": "complete",
  "updated_at": "2026-05-17T04:00:00Z"
}
```

### `GET /snapshot`
전체 상태 조회 (기존 + `leader_rotation` 필드 추가)

---

## 🔁 리셋

```bash
# 컨테이너 제거 + 디렉터리 정리 + 브리지 초기화
python3 openclaw_migration/reset.py

# 또는 전체 종료
bash carla_stop.sh
```

---

## ⚠️ 주의 사항

- CARLA 0.9.6 + Town06 맵 필요
- Docker 데몬이 실행 중이어야 함 (`docker info` 확인)
- `openclaw:local` 이미지가 빌드되어 있어야 OpenClaw 이전 가능
- OpenClaw 없이 CARLA 물리 기동만 테스트하려면 `--no-openclaw` 옵션 사용
- `.transfer/` 디렉터리가 vehicle-truck0, vehicle-truck1 컨테이너에 공유 마운트됨

---

## 📊 기존 두 군집 시나리오와의 차이

| 항목 | 기존 (two_platoon) | 신규 (leader_rotation) |
|---|---|---|
| 군집 수 | 2개 (platoon_a, platoon_b) | 1개 (3대) |
| 이동 방향 | 군집 간 차량 이송 | 선두 → 후미 |
| OpenClaw | 각 군집 1개 | 선두 교체 시 이전 |
| Docker 구조 | 단순 컨테이너 | DinD (docker.sock 공유) |
| 이미지 전송 | 없음 | base.tar + session.tar |

---

*본 프로젝트는 [Truckclaw-improve](https://github.com/jsy202/Truckclaw-improve) 를 기반으로 선두 교체 기능을 추가한 버전입니다.*
