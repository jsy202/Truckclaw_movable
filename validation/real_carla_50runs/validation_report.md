# Leader Change Real-CARLA 50-Run Validation Report

Base commit: local `validation` commit `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64` on branch `validation-carla-50runs`.

동일한 고정 CARLA 조건에서 50회 반복 실행해 50/50 완료 및 logical/physical state consistency 50/50을 확인했다.

이 결과는 특정 고정 시뮬레이션 조건에서의 E2E 반복 완결성을 실증한 것으로, 일반적인 신뢰성(general reliability)이나 실차 신뢰성을 의미하지 않습니다.

## Result Summary

| Metric | Result | Note |
|---|---:|---|
| Runs | 50 | 고정 CARLA 조건 독립 프로세스 격리 실행 |
| Physical Leader Change success | 50/50 | `truck1` 선두 승계, `truck0` 감속 후 후미 합류 확인 |
| Final order correct | 50/50 | CARLA 공간 위치 기준 `[truck1, truck2, truck0]` 순서 검증 |
| Logical / physical consistency | 50/50 | 최종 멤버십 및 물리적 ordering 100% 일치 |
| OpenClaw migration success | Not tested | 환경 부재로 테스트 대상 제외 |
| Timeout | 0 | 0건 |
| Crash | 0 | 0건 (충돌 감지 센서 0건) |
| Cleanup failure | 0 | 0건 (포트 2000/2001/18801/18802/18803 프로세스 그룹 정리) |
| Duplicate trigger | 0 | 0건 (멱등 처리로 유효 trigger_count는 모든 run에서 1) |

## Timing Statistics (Raw Evidence Based)

| Phase | Mean | P50 (Median) | P95 | Max | Min |
|---|---:|---:|---:|---:|---:|
| `trigger → migrate` | 0.003606 s | 0.003538 s | 0.003976 s | 0.005020 s | 0.003212 s |
| `trigger → split` (logical_transition) | 0.010610 s | 0.010270 s | 0.012122 s | 0.013104 s | 0.009320 s |
| `trigger → lane_change` | 1.215890 s | 1.208961 s | 1.369022 s | 1.402861 s | 1.070818 s |
| `trigger → slowdown` | 8.267754 s | 8.226030 s | 8.959305 s | 9.635556 s | 7.685227 s |
| `trigger → rejoin` | 9.869590 s | 9.796522 s | 10.648178 s | 11.605610 s | 9.144819 s |
| `trigger → done` | 9.871707 s | 9.798514 s | 10.650220 s | 11.608280 s | 9.146655 s |

## Defect History

- **VAL-L01**: Continuous lane rejected at OpenDRIVE road-segment boundary
  - **Before**: 초기 smoke run에서 물리적 매뉴버가 정상 완료되었으나, CARLA OpenDRIVE 도로 세그먼트 경계(road 36 vs 1149)를 가로지르는 동일 차선(lane -3)을 evaluator가 서로 다른 차선으로 오인하여 실패 판정 (기록: `evidence_before_fix/leader_smoke_road_segment/`).
  - **Root Cause**: `same_final_lane` 검사 시 (road_id, lane_id) 쌍을 비교하여, 35m 길이의 플래툰이 단일 직선 차선 상에서 road 경계를 걸치고 있을 때 불일치로 판정함.
  - **Fix**: 연속된 도로 상의 차선 유지를 올바르게 반영하기 위해 lane_id를 비교하고, 물리적 횡방향 오차(< 0.8m) 및 종방향 ordering 검증을 결합하여 평가하도록 수정.
  - **Regression Test**: `test_same_lane_across_connected_road_segments_is_not_rejected`
  - **After**: 수정 후 본 50회 검증에서 50/50 정상 통과.
  - 상세 내역: [defects.md](defects.md)

## Separate CARLA-free regression baseline

검증 착수 전 기존 단위/회귀 테스트는 22 passed + 1 known gateway-health xfail였습니다. 이 테스트 결과는 실제 CARLA 50회 실행 통계에 합산하지 않습니다.

## Final Comparison

| Scenario | Repository | Real CARLA Runs | Verification |
|---|---|---:|---|
| Transfer | Truckclaw-improve | 10 | request → physical merge → logical membership |
| Split | Truckclaw_copyable | 50 | logical detach → physical separation → independent driving |
| Leader Change | Truckclaw_movable | 50 | leader/controller transition → physical maneuver → old leader rejoin |

## Limitations

- **Fixed condition**: 고정된 CARLA 시나리오 조건에서만 실행되었습니다.
- **Single scenario configuration**: 3대 플래툰 단일 설정입니다.
- **CARLA simulation**: CARLA 0.9.13 시뮬레이션 환경 검증이며 실차 검증이 아닙니다.
- **Off-screen rendering**: `-RenderOffScreen` 모드 시뮬레이션입니다.
- **carlacola fallback**: `vehicle.carlamotors.carlacola` 모델을 사용했습니다.
- **OpenClaw Migration Not tested**: 환경 부재로 미검증되었습니다.
- **Generalization 미검증**: 다양한 환경에 대한 일반화는 검증되지 않았습니다.
- 상세 내역: [limitations.md](limitations.md)
