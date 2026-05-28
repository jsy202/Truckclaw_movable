# Test Cases — Truckclaw_movable

CARLA, Docker, Discord 없이 실행된다.

| 대체 대상 | 방법 |
|---|---|
| Bridge | 실제 Handler를 in-process로 실행 |
| CARLA :18803 | `FakeRotationReceiver` |
| Docker | `FakeDocker` (`subprocess.run`을 monkeypatch) |
| 시나리오 coordinator | `carla_stub`으로 CARLA 모듈을 대체한 뒤 import |

"Baseline" 열은 같은 테스트를 research-baseline(`59f35ea`) 소스에서 `--runxfail`로 실행한 결과다(`evidence/baseline_crosscheck.txt`).

## Bridge — leader rotation (`tests/integration/test_leader_rotation.py`)

| TC | DEF | 테스트 | 기대 | REQ | Baseline | Current |
|---|---|---|---|---|---|---|
| TC-01 | – | `test_rotation_start_and_complete_end_with_truck1_leading` | trigger 1회, complete 후 truck1 선두·truck0 후미 | LR-001 | PASS | PASS |
| TC-02 | M01 | `test_leader_not_promoted_before_physical_rotation_completes` | started 직후 멤버십 불변 | LR-002 | FAIL | PASS |
| TC-03 | M02 | `test_failed_rotation_keeps_original_leader` | started→failed 후 truck0 선두 | LR-003 | FAIL | PASS |
| TC-04 | M03 | `test_duplicate_rotation_start_does_not_retrigger_carla` | started 2회 → trigger 1회 | LR-004 | FAIL | PASS |
| TC-05 | M04 | `test_rotation_complete_requires_started` | started 없이 complete → 409, 멤버십 불변 | LR-005 | FAIL | PASS |
| TC-06 | M05 | `test_rejects_unknown_rotation_status` | status "banana" → 400 | LR-005 | FAIL | PASS |
| TC-07 | M06 | `test_rejects_unknown_leader_identity` | old_leader truck9 → 4xx, trigger 0회 | LR-006 | FAIL | PASS |
| TC-08 | – | `test_rejects_rotation_when_new_leader_is_not_next_in_line` | truck0→truck2 → 409 | LR-006 | FAIL | PASS |

## 시나리오 coordinator (CARLA stub, 실제 LeaderMigrator + FakeDocker)

| TC | DEF | 테스트 | 기대 | REQ | Baseline | Current |
|---|---|---|---|---|---|---|
| TC-09 | – | `test_coordinator_advances_to_gap_after_successful_migration` | MIGRATE→GAP, bridge에 started 보고 | LR-010 | PASS | PASS |
| TC-10 | M07 | `test_coordinator_leaves_migrate_when_migration_fails` | docker run 실패 → CRUISE + failed 보고 | LR-011 | FAIL (MIGRATE 고착) | PASS |
| TC-11 | M08 | `test_coordinator_times_out_hung_migration` | docker save가 3 s 동안 멈춤, timeout 0.3 s → CRUISE + failed | LR-012 | FAIL (MIGRATE 고착) | PASS |

## Agent migration (`tests/integration/test_agent_migration.py`)

| TC | DEF | 테스트 | 기대 | REQ | Baseline | Current |
|---|---|---|---|---|---|---|
| TC-12 | – | `test_successful_migration_restores_session_and_starts_new_container` | docker 호출 순서 save→load→rm→run, 포트 18790, 세션과 agent 설정 복원, 바이너리 제외 | AM-001, 005 | PASS | PASS |
| TC-13 | – | `test_session_tar_contains_agent_config_state_and_token_meta` | tar 멤버와 meta 확인 | AM-001 | PASS | PASS |
| TC-14 | – | `test_v2v_transfer_is_byte_identical` | sha256 일치 | AM-002 | PASS | PASS |
| TC-15 | – | `test_docker_failure_marks_migration_failed[save/run]` | `wait` False, done set | AM-003 | PASS | PASS |
| TC-16 | – | `test_cleanup_old_removes_only_old_leader_container` | `docker rm -f openclaw-truck0` 1회 | AM-006 | PASS | PASS |
| TC-17 | – | `test_corrupted_session_artifact_fails_migration` | 잘린 tar → 실패, docker run 없음 | AM-004 | PASS (결함 아님) | PASS |
| TC-18 | M09 | `test_migration_success_requires_new_agent_ready` | 성공 판정 전에 gateway readiness를 확인 | AM-007 | FAIL | **XFAIL (DEFERRED)** |

## Unit (`tests/unit/test_replicator_units.py`)

TC-19 ~ TC-22: gateway 포트 매핑, env fallback, ANSI 제거, 기본 컨테이너 이름(AM-005). Baseline PASS, Current PASS.

## 미실행 (CARLA·Docker·Discord 필요)

| ID | 내용 |
|---|---|
| ST-01 | 실제 CARLA에서 truck0 차선 변경 → 감속 → truck2 뒤 합류 |
| ST-02 | 실제 docker save/load/run으로 OpenClaw 컨테이너 이전, 같은 Discord 봇 재접속 |
| ST-03 | cleanup watcher가 실제 openclaw-truck0을 삭제 |
