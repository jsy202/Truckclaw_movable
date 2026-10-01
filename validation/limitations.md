# Limitations — Truckclaw_movable Validation

| 항목 | 상태 |
|---|---|
| CARLA 물리 단계(GAP→LC→SLOWDOWN→REJOIN) | 미검증. coordinator는 MIGRATE 경계까지만 테스트했다(CARLA stub) |
| 실제 Docker save/load/run, DinD 구조 | 미검증. FakeDocker는 명령 순서와 returncode만 흉내 낸다 |
| 실제 Discord 봇 재접속(토큰 이동) | 미검증 |
| 신규 agent readiness (DEF-M09) | **DEFERRED / XFAIL**. 실제 gateway 기동 시간과 `/healthz` 응답을 측정하지 못해, 대기 시간을 정할 근거가 없다. 기존 docker run 경로를 바꾸지 않았다 |
| MIGRATE timeout 이후의 migration 스레드 | 취소되지 않는다. 늦게 성공하면 `openclaw-truck1`이 기동될 수 있고, 그동안 truck0도 같은 토큰으로 살아 있을 수 있다. 테스트하지 않았다 |
| docker save/load timeout | replicator에는 없다(baseline과 같음). coordinator timeout으로만 보완했다 |
| 세션 tar 보안 | `.env`와 `migration_meta.json`에 Discord/Gateway/OpenAI 토큰이 평문으로 들어간다(설계상 의도, B). 수정하지 않았다 |
| tar 추출 경로 검증 | `extractall`은 member 이름 prefix만 필터링한다. 신뢰할 수 없는 tar에 대한 path traversal 방어는 없다(B). 미검증 |
| 상속 transfer 결함 11건 | 재현됨(A), 미수정 |
| `_promote_new_leader`의 platoon_a 하드코딩 | 원본 설계를 유지했다. 다른 platoon의 선두 교체는 지원하지 않는다 |

## 동작 변화 (실제 시연에서 확인 필요)

| 변화 | 연구 당시 | 현재 |
|---|---|---|
| bridge 선두 표시 시점 | `started` 즉시 truck1 | `complete`(물리 합류 완료) 후 truck1. 교체 진행 중 `/snapshot`을 읽는 agent는 truck0을 선두로 본다 |
| migration 실패 | MIGRATE 영구 대기 | CRUISE 복귀 + bridge `failed`. main loop가 CRUISE에서 'L' 키를 받으므로(L676) 재시도 가능하다(B, 미실행) |
