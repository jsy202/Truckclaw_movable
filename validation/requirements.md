# Requirements — Truckclaw_movable (Leader Rotation / Agent Migration)

> 연구 종료 후(2026-10) 수행한 Post-project Validation 산출물이다. 연구 당시 요구사항 문서는 없었다.
> 아래 항목은 현재 코드와 README에서 근거를 찾아 정리한 것이다.
>
> - 출처: **Existing** = README/코드에 명시됨, **Derived** = 기존 설계에서 도출됨, **Proposed** = 이번에 새로 제안함
> - 증거 등급: **A** = 자동화 테스트로 실행 확인, **B** = 정적 분석, **C** = 제안 또는 미검증

## Leader rotation — bridge (`POST /leader_rotation`)

| ID | 요구사항 | 출처 | 근거 | 검증 |
|---|---|---|---|---|
| REQ-LR-001 | 정상 흐름(started → complete)이 끝나면 truck1이 선두가 되고 truck0은 후미 follower가 된다. | Existing | README 시스템 개요 ①–③ | A |
| REQ-LR-002 | bridge의 논리 선두는 **물리 후미 합류가 완료(`complete`)된 뒤에만** 바뀐다. | Derived | 시나리오가 `_finalize_join`에서 `complete`를 보고함 | A |
| REQ-LR-003 | 실패한 선두 교체는 bridge 논리 상태를 바꾸지 않는다(truck0이 선두로 유지됨). | Derived | – | A |
| REQ-LR-004 | 진행 중에 `started`가 다시 오면 멱등으로 처리한다. CARLA trigger는 1회만 보낸다. | Derived | 문서화된 흐름에서는 agent와 시나리오가 각각 `started`를 보냄 | A |
| REQ-LR-005 | `complete`/`failed`는 진행 중(started)인 교체에만 유효하다. status는 started/complete/failed 중 하나여야 한다. | Derived | – | A |
| REQ-LR-006 | `old_leader`는 platoon_a의 현재 선두, `new_leader`는 그 바로 다음 차량이어야 한다. 아니면 CARLA를 trigger하지 않는다. | Derived | `_promote_new_leader`의 의미(idx+1 승격) | A |

## Leader rotation — 시나리오 coordinator (`LeaderRotationCoordinator`)

| ID | 요구사항 | 출처 | 검증 |
|---|---|---|---|
| REQ-LR-010 | OpenClaw 이전이 성공해야만 물리 단계(GAP)로 진행한다. | Existing (MIGRATE → GAP 설계) | A |
| REQ-LR-011 | 이전이 실패하면 MIGRATE에서 무한 대기하지 않고 교체를 중단한다(CRUISE로 복귀, bridge에 `failed` 보고). | Derived | A |
| REQ-LR-012 | 이전이 `MIGRATE_TIMEOUT_S`(120 s, `LeaderMigrator.wait()` 기본값) 안에 끝나지 않으면 중단한다. | Derived | A |

## Agent migration (`openclaw_migration/replicator.py`)

| ID | 요구사항 | 출처 | 검증 |
|---|---|---|---|
| REQ-AM-001 | 세션 tar에는 agent 설정, 상태 파일(json/md/txt/yaml/.env), 메타데이터, 토큰(.env)이 포함된다. 바이너리는 제외된다. | Existing (docstring, README) | A |
| REQ-AM-002 | V2V 전송(청크 복사 에뮬레이션)은 원본과 바이트 단위로 동일하다. | Derived | A |
| REQ-AM-003 | docker save/run이 실패하면 이전은 실패(`wait()` False, 완료 이벤트 set)로 끝난다. | Existing | A |
| REQ-AM-004 | 전송 중 손상(잘린 tar)된 세션 artifact로는 신규 컨테이너를 기동하지 않는다. | Derived | A (원본에서도 충족) |
| REQ-AM-005 | 신규 컨테이너는 `openclaw-truck1`, gateway 포트는 18789 + truck index다. | Existing | A |
| REQ-AM-006 | cleanup은 구 선두 컨테이너(`openclaw-truck0`)만 삭제한다. | Existing | A |
| REQ-AM-007 | "이전 성공"은 신규 agent gateway가 healthy함을 의미해야 한다. | Proposed | **XFAIL, DEFERRED** |

## Proposed / DEFERRED

| ID | 내용 | 상태 |
|---|---|---|
| REQ-AM-P01 | 세션 artifact의 tx/rx 체크섬 검증 | C — 현재 전송은 로컬 복사 에뮬레이션이라 손상 경로가 없음. 실제 V2V 링크가 생기면 필요 |
| REQ-AM-P02 | 세션 tar 안의 토큰 보호(평문 `.env`, `migration_meta.json`의 discord 토큰) | C — 설계상 토큰을 함께 옮기는 구조. 보안 개선 제안만 기록 |
| REQ-LR-P01 | MIGRATE timeout 이후 아직 실행 중인 migration 스레드를 취소 | C — 취소 경로가 없어 늦게 성공하면 `openclaw-truck1`이 기동될 수 있음 |
| REQ-TR-* | improve에서 찾은 transfer 결함 11건 | 이 저장소에서도 재현됨(A, `evidence/inherited_transfer_defects.txt`), **미수정** |
