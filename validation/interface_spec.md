# Interface Specification — Truckclaw_movable

transfer 관련 엔드포인트는 Truckclaw-improve의 bridge와 같다(baseline 기준, 미수정). 이 문서는 선두 교체 관련 계약만 다룬다.
**[changed]** 표시는 research-baseline(`59f35ea`)과 달라진 부분이다.

## `POST /leader_rotation` (bridge :18801)

| 필드 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `old_leader` | – | `truck0` | `platoon_a_<old_leader>`가 현재 선두여야 함 **[changed]** |
| `new_leader` | – | `truck1` | 현재 선두 바로 다음 차량이어야 함 **[changed]** |
| `status` | – | `started` | `started` / `complete` / `failed` 중 하나 **[changed]** |

| 상황 | 응답 | 부수 효과 |
|---|---|---|
| `started` (진행 중 아님, 식별자 유효) | 200 + rotation 객체 | CARLA :18803 trigger 1회. **논리 선두는 아직 바뀌지 않음 [changed]** (baseline: 이 시점에 승격) |
| `started` (이미 started) | 200 + 현재 객체 | **재trigger 없음 [changed]** (baseline: 재trigger) |
| `complete` (started 상태) | 200 | **`_promote_new_leader` 실행 [changed]**: new_leader 승격, old_leader는 후미로 |
| `failed` (started 상태) | 200 | 상태만 기록. 멤버십 불변 |
| `complete`/`failed` (started 아님) | 409 **[changed]** (baseline: 200) | – |
| 알 수 없는 status | 400 **[changed]** (baseline: 200) | – |
| 알 수 없는 차량 | 404 **[changed]** (baseline: 200 + trigger) | – |
| 선두/순서 불일치 | 409 **[changed]** | – |

`GET /leader_rotation`은 현재 rotation 객체 `{old_leader, new_leader, status, updated_at}`를 반환하며, `/snapshot`에도 포함된다. `/reload`하면 비워진다.

## Bridge → 시나리오 (`LEADER_ROTATION_URL`, 기본 `http://127.0.0.1:18803/leader_rotation`)

`POST {"old_leader": "...", "new_leader": "..."}`, timeout 2 s, 실패해도 로그만 남긴다(baseline과 같음).

시나리오의 :18803 handler는 `/leader_rotation`이나 `/start_merge` 요청을 받으면 이벤트를 set한다. coordinator는 CRUISE 상태일 때만 이 이벤트에 반응한다.

## 시나리오 → bridge

| 시점 | 호출 |
|---|---|
| `_start_migrate` | `POST /leader_rotation {status: started}` |
| `_abort_migration` **[changed, 신규]** | `POST /leader_rotation {status: failed}` |
| `_finalize_join` | `POST /leader_rotation {status: complete}` |

## Agent migration (Python API, `openclaw_migration/replicator.py`)

| API | 의미 |
|---|---|
| `LeaderMigrator.migrate(blocking)` | TX(base tar 생성 → session tar 생성 → V2V 복사) 후 RX(docker load → 세션 복원 → docker run) |
| `LeaderMigrator.wait(timeout)` | 완료되었고 **성공**했으면 True. 진행 중이거나 실패했으면 False |
| `LeaderMigrator.finished()` **[changed, 신규]** | 성공 여부와 무관하게 종료되었으면 True |
| `LeaderMigrator.cleanup_old()` | `docker rm -f openclaw-<old>` |

### 외부 명령 (FakeDocker로 대체되는 경계)

| 명령 | timeout |
|---|---|
| `docker save <image> -o base.tar` | **없음** |
| `docker load -i base.tar` | **없음** |
| `docker rm -f openclaw-<new>` | 없음 |
| `docker run -d --name openclaw-<new> ...` | 15 s |

save와 load에 timeout이 없어서 coordinator 쪽 `MIGRATE_TIMEOUT_S`로 보완했다.

### 세션 tar 형식 (`openclaw_session.tar`, 압축 없음)

```text
./agent_config/...          agents/<new_truck>/ 전체
./openclaw_data/*           old data dir의 *.json *.md *.txt *.yaml *.yml .env*  (바이너리 제외)
./migration_meta.json       from_container, new_truck_id, timestamp, image, discord/gateway token
./.env                      DISCORD_BOT_TOKEN, OPENCLAW_GATEWAY_TOKEN, OPENAI_API_KEY (평문)
```
