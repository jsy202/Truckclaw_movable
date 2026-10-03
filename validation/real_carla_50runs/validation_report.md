# Leader Change Real-CARLA 50-Run Validation Report

## 1. Objective

본 검증의 목적은 3대 트럭 군집(platoon) 주행 중 선두 차량 교체(Leader Change / Rotation) 시나리오에 대해, 실제 CARLA 시뮬레이터 환경에서 프로세스 격리 기반의 50회 연속 반복 실행을 수행하여 상태 머신 완결성 및 논리적/물리적 상태 일관성(logical & physical state consistency)을 실증적으로 검증하는 것이다.

## 2. System Under Test

- **Repository**: `Truckclaw_movable` (https://github.com/jsy202/Truckclaw_movable)
- **Branch**: `validation-carla-50runs`
- **Base Commit**: `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64`
- **Target Component**:
  - `leader_rotation_scenario.py`: 선두 교체 조율자(`LeaderRotationCoordinator`) 및 차량 제어 로직
  - `platoon_bridge_server.py`: 시나리오와 외부 에이전트 간 선두 교체 상태 통신 브리지 (`/leader_rotation`)

## 3. Environment

- **Simulator**: CARLA 0.9.13
- **Host OS**: Ubuntu 22.04 LTS (Linux 6.8.0-138-generic)
- **Python Runtime**: Python 3.7.17 (CARLA 클라이언트 환경), Python 3.10.12 (검증 runner 환경)
- **GPU Hardware**: NVIDIA GeForce RTX 3060 (Driver: 535.183.01)
- **Rendering**: `-RenderOffScreen` (실제 CARLA 물리 엔진 시뮬레이션이며 화면 렌더링 파이프라인만 생략)
- **Map**: Town06
- **Vehicle Blueprint**: `vehicle.carlamotors.carlacola` (`european_hgv` blueprint 부재에 따른 fallback)

## 4. Fixed Scenario Conditions

- **Platoon Configuration**: 3대 트럭 대열 (`truck0` 초기 선두, `truck1` 중간, `truck2` 후미)
- **Initial Spawn**: Town06 고속도로 구간 (x=81.0, y=136.0, yaw=0.0 deg)
- **Target Speed**: 20.0 km/h (`SYNC_SPEED_KMH`)
- **Simulation Time Step**: `dt = 0.01 s` (100 Hz fixed time step)
- **Weather**: CloudySunset
- **Trigger Method**: HTTP POST `:18803/leader_rotation` 수신 시 선두 교체 시퀀스 개시

## 5. State Machine

선두 교체 시나리오는 다음과 같은 단계로 전이된다:

```
CRUISE → MIGRATE → GAP → LC → SLOWDOWN → REJOIN → DONE
```

1. `CRUISE`: 초기 3대 대열(`truck0` 리더) 정속 주행
2. `MIGRATE`: OpenClaw 에이전트 마이그레이션 단계 (본 환경에서는 미배치로 skip 처리되어 즉시 GAP 전이)
3. `GAP`: `truck0` 분리 및 `truck1`이 선두 컨트롤러(`LeadNavigator`)로 승격. `truck0` 가속을 통해 12.0 m 간격 확보 및 10 ticks 안정 유지
4. `LC`: `truck0`이 인접 차선(`lane -4`)으로 차선 변경 이탈 (lateral distance > 3.0 m)
5. `SLOWDOWN`: `truck0`이 감속하여 군집 후미(`truck2`) 뒤쪽 25.0 m 이상 위치 확보
6. `REJOIN`: `truck0`이 원래 차선(`lane -3`)으로 복귀 (lateral offset < 0.8 m)
7. `DONE`: `truck0`을 군집 최후미로 merge하고 `FollowerController` 재부착 후 최종 완료 확정

## 6. Validation Invariants

Leader Change 성공 판정(PASS)은 단순 `DONE` 상태 도달이 아닌 아래 invariant를 모두 만족해야 한다:

1. **Initial State Invariant**: 시작 시 CRUISE 상태, 멤버 `[truck0, truck1, truck2]`, `truck0` 리더, 컨트롤러 정상 배정
2. **Single Accepted Trigger**: 50회 중 유효 트리거가 정확히 1회 수락 및 반영 (trigger_count: 1)
3. **Logical Transition**: `truck1` 리더 승격, 주 군집 `[truck1, truck2]`, 분리 차량 `[truck0]` 분리 관측
4. **Gap Ready**: `gap_ready` 관측 시 간격 >= 12.0 m 및 10 ticks 안정 유지
5. **Lane Departure**: 차선 변경 완료 시 횡방향 이탈 거리 > 3.0 m 및 비강제(forced=False) 전이
6. **Rear Position**: 후방 위치 확보 시 tail 후방 거리 >= 25.0 m 및 비강제 전이
7. **Rejoin Precision**: 차선 복귀 시 횡방향 오차 < 0.8 m
8. **Final Membership & Leader**: 최종 플래툰 `[truck1, truck2, truck0]`, 리더 `truck1`, 분리 플래툰 없음
9. **Physical Longitudinal Ordering**: CARLA 3D 좌표 공간상 종방향 위치가 `truck1 → truck2 → truck0` 순서로 일치
10. **Same Final Lane**: 복귀 후 3대 차량이 모두 동일한 CARLA lane_id 상에 정렬
11. **Liveness & Collision-Free**: 전 차량 actor 생존(alive=True), 속도 > 0 km/h, 충돌 이벤트 수 0건
12. **State Completion**: 최종 시나리오 상태 `DONE` 정상 도달

## 7. Runner / Lifecycle

- 각 실행(run)마다 독립된 CARLA 서버 프로세스 그룹(`CarlaUE4.sh`)을 백그라운드로 기동
- 프로세스 그룹 분리(`setsid` / `preexec_fn=os.setsid`)를 통해 시나리오 및 서버 프로세스 수명 주기 완전 격리
- 시나리오 종료 후 포트(2000, 2001, 18801, 18802, 18803) 해제 및 잔류 프로세스 여부를 검사하여 cleanup 확인

## 8. 50-run Result

동일한 고정 CARLA 조건에서 50회 반복 실행한 결과는 다음과 같다:

| Metric | Result | Note |
|---|---:|---|
| Real CARLA Runs | 50 | 고정 CARLA 조건 독립 격리 실행 |
| Physical Leader Change Success | 50/50 | 50/50 successful runs under the tested fixed CARLA condition |
| Final Order Correct | 50/50 | CARLA 공간 위치 기준 `[truck1, truck2, truck0]` 순서 전수 일치 |
| Logical / Physical Consistency | 50/50 | Logical and physical state consistency was observed in all 50 runs |
| OpenClaw Migration Success | Not tested | 환경 부재(미배치)로 테스트 제외 |
| Timeout | 0 | 0건 |
| Crash | 0 | 0건 (충돌 감지 센서 관측 0건) |
| Cleanup Failure | 0 | 0건 (포트 점유 잔류 0건) |
| Duplicate Trigger | 0 | 0건 (트리거 멱등 처리로 50회 모두 유효 트리거 1회) |

## 9. Timing Statistics

50개 실행의 원본 evidence(`transition_events.json`)에서 직접 계산한 단계별 소요 시간 통계:

| 구간 (Phase) | Mean | P50 (Median) | P95 | Max | Min |
|---|---:|---:|---:|---:|---:|
| `trigger → migrate` | 0.003606 s | 0.003538 s | 0.003976 s | 0.005020 s | 0.003212 s |
| `trigger → split` (logical_transition) | 0.010610 s | 0.010270 s | 0.012122 s | 0.013104 s | 0.009320 s |
| `trigger → lane_change` | 1.215890 s | 1.208961 s | 1.369022 s | 1.402861 s | 1.070818 s |
| `trigger → slowdown` | 8.267754 s | 8.226030 s | 8.959305 s | 9.635556 s | 7.685227 s |
| `trigger → rejoin` | 9.869590 s | 9.796522 s | 10.648178 s | 11.605610 s | 9.144819 s |
| `trigger → done` | 9.871707 s | 9.798514 s | 10.650220 s | 11.608280 s | 9.146655 s |

## 10. Defect Found During Validation

### VAL-L01: Continuous Lane Rejected at OpenDRIVE Road-Segment Boundary

- **Before Symptom**: 초기 smoke run에서 선두 교체 및 재합류 기동이 정상 완료되었으나, 플래툰 차량이 OpenDRIVE 도로 세그먼트 경계(road 36 vs 1149)를 가로질러 주행할 때 동일한 물리적 직선 차선(lane -3)임에도 평가자가 차선 불일치로 오인하여 FAIL 판정함. 원본 증적은 `evidence_before_fix/leader_smoke_road_segment/run001/`에 보존됨.
- **Root Cause**: 평가자의 `same_final_lane` 검증 로직이 `(road_id, lane_id)` 쌍의 엄격한 일치를 요구함. CARLA OpenDRIVE 도로망에서 단일 연속 차선이라도 road segment boundary에서 road_id가 변경될 수 있음을 감안하지 못함.
- **Fix**: 동일 차선 검증을 CARLA `lane_id` 비교, 복귀 후 횡방향 오차(< 0.8 m), 3차원 공간 종방향 ordering 검증의 결합 조건으로 정밀화함.
- **After**: 수정 후 실제 CARLA 50회 본 실행에서 50/50 정상 PASS를 확인하였음.

## 11. Regression Test

- `tests/unit/test_real_carla_leader_runner.py`: 자식 프로세스 정리 및 러너 프로세스 수명 주기 검증
- `tests/unit/test_real_carla_leader_validation.py`: VAL-L01 세그먼트 경계 허용 검증 (`test_same_lane_across_connected_road_segments_is_not_rejected`) 및 불변식 위반 거부 테스트
- `tests/unit/test_real_carla_leader_audit.py`: 원본 JSON 증적에 대한 독립 감사 일치성 검증
- **결과**: 52 passed, 1 known gateway-health xfail (DEF-M09) (CARLA-free baseline과 별도로 관리됨)

## 12. Independent Audit

- **대상 데이터**: `evidence/run001` ~ `evidence/run050` (50개 디렉토리 전수)
- **필수 파일 점검**: `ready.json`, `initial_snapshot.json`, `trigger_snapshot.json`, `transition_events.json`, `final_snapshot.json`, `result.json` 누락 0건
- **요약 대조**: `runs_summary.csv` (50 rows), `timing_summary.csv` (50 rows)와 원본 JSON 전수 일치
- **불변식 위반**: 0건 (최대 rejoin 횡방향 오차: 0.7999 m < 0.8 m, 최소 lane_change 횡방향 거리: 3.0001 m > 3.0 m, 최소 slowdown 거리: 25.0000 m >= 25.0 m)
- **포트 정리 상태**: 감사 시점 포트 2000, 2001, 18801, 18802, 18803 모두 해제(free) 확인 완료
- **감사 출력물**: `validation/real_carla_50runs/audit/` (`audit_summary.json`, `audit_runs.csv`, `audit_timing.csv`)

## 13. Limitations

- **Fixed Condition Validation**: Town06 고정 스폰, 단일 목표 속도, 단일 날씨 조건에 한정된 검증입니다.
- **Single Scenario Configuration**: 3대 플래툰 단일 구성으로 진행되었습니다.
- **CARLA Simulation**: CARLA 0.9.13 가상 환경 검증이며 실차 검증이 아닙니다.
- **Off-screen Rendering**: `-RenderOffScreen` 모드 시뮬레이션입니다.
- **Vehicle Fallback**: `vehicle.carlamotors.carlacola` 모델을 사용했습니다.
- **OpenClaw Migration Not tested**: 실행 환경에 `openclaw:local` 이미지, Discord gateway, 소스 에이전트 미제공으로 마이그레이션 검증은 제외되었습니다.
- **Generalization 미검증**: 다양한 도로 기하, 기상 조건, 센서 노이즈에 대한 일반화는 검증되지 않았습니다.

### Cross-Scenario Comparison

| Scenario | Repository | Real CARLA Runs | Verification |
|---|---|---:|---|
| Transfer | Truckclaw-improve | 10 | request → physical merge → logical membership |
| Split | Truckclaw_copyable | 50 | logical detach → physical separation → independent driving |
| Leader Change | Truckclaw_movable | 50 | leader/controller transition → physical maneuver → old leader rejoin |

## 14. Evidence Location

- **최종 검증 증적**: `validation/real_carla_50runs/evidence/run001/` ~ `run050/`
- **결함 수정 전 증적**: `validation/real_carla_50runs/evidence_before_fix/leader_smoke_road_segment/run001/`
- **실행 요약 CSV**: `validation/real_carla_50runs/runs_summary.csv`
- **타이밍 요약 CSV**: `validation/real_carla_50runs/timing_summary.csv`
- **독립 감사 스크립트**: `validation/real_carla_50runs/tools/audit_raw_evidence.py`
