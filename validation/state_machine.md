# State Machines — Truckclaw_movable

## 1. 시나리오 `LeaderRotationCoordinator` (`RotState`)

상태 이름은 모두 연구 당시 코드에 있던 것이다. 새 상태는 추가하지 않았다.

```text
CRUISE ──trigger──▶ MIGRATE ──migration success──▶ GAP ─▶ LC ─▶ SLOWDOWN ─▶ REJOIN ─▶ DONE
   ▲                   │
   └── migration failed / MIGRATE_TIMEOUT_S 초과 (bridge에 status=failed 보고) [changed]
```

| 상태 | Entry | 다음 상태 | Timeout | Failure | 비고 |
|---|---|---|---|---|---|
| CRUISE | 시작, 또는 이전 중단 | trigger 시 MIGRATE (migrator가 없으면 GAP) | – | – | – |
| MIGRATE | `_start_migrate` (bridge에 started 보고, 백그라운드 migrate) | 성공 시 GAP | **120 s [changed]** | **실패 또는 timeout 시 CRUISE + bridge failed [changed]** | baseline: 실패하면 MIGRATE에서 영구 대기 |
| GAP / LC / SLOWDOWN / REJOIN | 물리 기동 | 순서대로 진행 | 없음 | 없음 | CARLA 필요. 이번 검증 범위 밖(B) |
| DONE | `_finalize_join` (bridge에 complete 보고, cleanup watcher가 openclaw-truck0 삭제) | – | – | – | – |

## 2. Bridge rotation status (`_leader_rotation["status"]`)

```text
(empty) ──started──▶ started ──complete──▶ complete   (promotion happens here [changed])
                        │ └──started──▶ started (idempotent, no re-trigger) [changed]
                        └──failed────▶ failed      (membership unchanged)
complete/failed ──started (valid identities)──▶ started
```

| 현재 상태 | started | complete | failed | 그 외 status |
|---|---|---|---|---|
| (empty) / complete / failed | 식별자가 유효하면 started + trigger | 409 | 409 | 400 |
| started | 200, 상태 유지(멱등) | complete + 선두 승격 | failed | 400 |

baseline에서는 모든 요청이 200이었다. `started`에서 바로 승격했고, 어떤 상태에서든 `complete`를 받았다.

## 3. Migration (`LeaderMigrator`)

```text
idle ──migrate()──▶ running ──(save → session tar → V2V → load → restore → run) ok──▶ done/success
                       └── 어느 단계든 예외 또는 returncode≠0 ──────────────────────▶ done/failed
```

`wait(0)`는 done/success일 때만 True를 반환한다. `finished()`는 done이면 성공 여부와 관계없이 True다.
