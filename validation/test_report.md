# Test Report — Truckclaw_movable (Post-project Validation)

> 연구 종료 후(2026-10-01) 수행한 검증이다. 연구 당시의 CARLA·Docker 선두 교체 시연은 이번에 재실행하지 않았다.

## 환경

Linux 6.8, Python 3.10.12, pytest 8.4.2. CARLA, Docker, Discord는 사용하지 않았다.
baseline은 `research-baseline` = `59f35ea`(GitHub main 최신)이다.

## 결과 (최종)

| 전체 | PASS | XFAIL | FAILED |
|---|---|---|---|
| 23 | 22 | 1 (DEF-M09, DEFERRED) | 0 |

`python3 -m pytest -v` 결과는 `evidence/pytest_verbose.txt`에 있다. 10회 연속 실행에서 매회 `22 passed, 1 xfailed`(약 5.03 s)였다(`evidence/repeat_runs.txt`).

## 결함 이력 (git history)

| 단계 | commit | 결과 |
|---|---|---|
| 테스트 하네스 + 결함 9건 strict xfail (production 변경 0) | `d26fc46` | 9 passed, 9 xfailed |
| DEF-M01, M02: 선두 승격을 complete 시점으로 이동 | `0c4a530` | 11 passed, 7 xfailed |
| DEF-M03, M04, M05: rotation 상태 가드 | `237f619` | 14 passed, 4 xfailed |
| DEF-M06: 식별자 검증 (+ 순서 테스트 1건) | `35bd68e` | 16 passed, 3 xfailed |
| DEF-M07, M08: MIGRATE 실패/timeout 탈출 | `89237fd` | 18 passed, 1 xfailed |
| unit 4건 + CI | `05e1b5a` | 22 passed, 1 xfailed |
| validation 문서 + README | `86228a1` | – |
| FakeDocker가 `check=True`를 흉내 내도록 수정 (테스트 도구 충실도, 이 저장소의 결과 변화 없음) | `482cef6` | 22 passed, 1 xfailed |

## Baseline 교차 검증

현재 테스트를 baseline 소스에 대해 `--runxfail`로 실행했다(`evidence/baseline_crosscheck.txt`).

결과: 10 failed, 13 passed. 실패한 10건은 DEF-M01~M09와 순서 검증 테스트(TC-08)다. 나머지는 baseline에서도 통과했다.

## Negative control

- 결함 테스트 9건은 수정 전 commit(`d26fc46`)에서 실제로 실패했다. xfail strict 상태였다.
- `test_corrupted_session_artifact_fails_migration`은 원본에서도 통과했다. 그래서 결함으로 분류하지 않았다(억지 xfail 금지 원칙).

## 상속 결함 (Truckclaw-improve와 공통인 bridge 코드)

improve의 결함 스위트(`53dafd0`)를 이 저장소의 baseline bridge에 그대로 실행했다. 11/11이 재현되었다(`evidence/inherited_transfer_defects.txt`).

이 저장소에서는 **수정하지 않았다.** 이 저장소의 선두 교체 시나리오는 `/transfers`를 쓰지 않으며, 수정본은 improve에 있다.

## CI

`.github/workflows/test.yml`은 GitHub에서 아직 실행되지 않았다(push 안 함). 같은 명령을 로컬에서 실행한 결과: 문법 OK, unit 4 passed, integration 18 passed + 1 xfailed (`evidence/ci_steps_local.txt`).
