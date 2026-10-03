# Traceability Matrix — Truckclaw_movable

Requirement → Production code (validation 브랜치 라인) → Test → Baseline/Current 결과(실제 실행) → Fix commit

| REQ | Production code | Test | Baseline | Current | Commit |
|---|---|---|---|---|---|
| LR-001 | `bridge/platoon_bridge_server.py` `/leader_rotation` L307–, `_promote_new_leader` L148 | TC-01 | PASS | PASS | – |
| LR-002 | `_promote_new_leader` 호출을 complete 분기로 이동 L339 | TC-02 | FAIL | PASS | `0c4a530` |
| LR-003 | 같은 변경 (failed는 승격하지 않음) | TC-03 | FAIL | PASS | `0c4a530` |
| LR-004 | 멱등 started L316 | TC-04 | FAIL | PASS | `237f619` |
| LR-005 | status 검증 L314, complete/failed 가드 L326 | TC-05, TC-06 | FAIL, FAIL | PASS, PASS | `237f619` |
| LR-006 | 식별자 검증 L318–324 | TC-07, TC-08 | FAIL, FAIL | PASS, PASS | `35bd68e` |
| LR-010 | `scenario/examples/leader_rotation_scenario.py` `update()` MIGRATE 분기 | TC-09 | PASS | PASS | – |
| LR-011 | `update()` L320 `migrator.finished()` → `_abort_migration` L341. `replicator.py` `finished()` L422 | TC-10 | FAIL | PASS | `89237fd` |
| LR-012 | `MIGRATE_TIMEOUT_S` L64, L322, `_migrate_started` L334 | TC-11 | FAIL | PASS | `89237fd` |
| AM-001 | `replicator.py` `create_session_tar` L142 | TC-12, TC-13 | PASS | PASS | – |
| AM-002 | `_v2v_transfer` L93 | TC-14 | PASS | PASS | – |
| AM-003 | `LeaderMigrator._run` L430 (예외 → `_success=False`) | TC-15 | PASS | PASS | – |
| AM-004 | `load_and_run_openclaw` L211 (tarfile 예외 전파) | TC-17 | PASS | PASS | – |
| AM-005 | `_default_gateway_port`, `LeaderMigrator.__init__` | TC-12, TC-19–22 | PASS | PASS | – |
| AM-006 | `delete_old_openclaw` L352 | TC-16 | PASS | PASS | – |
| AM-007 | `load_and_run_openclaw` L343 (`docker run` returncode만 확인) | TC-18 | FAIL | XFAIL | 미수정 (DEFERRED) |
| REQ-TR-* (상속) | bridge `/transfers*` | Truckclaw-improve 결함 스위트 | 11/11 재현 | 미수정 | – (`evidence/inherited_transfer_defects.txt`) |
