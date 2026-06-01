# Validation Branch Baseline

Captured before creating `validation-carla-50runs` on 2026-10-03 (Asia/Seoul).

| Item | Value |
|---|---|
| Repository | `Truckclaw_movable` |
| Starting branch | local `validation` |
| Base commit | `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64` |
| `origin/main` | `59f35ea60cedd00ea2cb897ae1e6c7dae192ce32` |
| Start status | clean (`## validation`) |
| New branch | `validation-carla-50runs` |
| Baseline tests | 22 passed, 1 known gateway-health xfail in 5.15 s |

## Existing validation changes over `origin/main`

The base contains nine local validation commits: a CARLA/Docker-free Leader Rotation and migration harness; fixes that delay logical promotion until physical completion, enforce and validate the bridge rotation contract, and leave failed/timed-out migration; replicator tests and CI; the FakeDocker fidelity correction; and validation documentation. Its diff from `origin/main` is 28 files, 1084 insertions, and three deletions.

These inherited regression results and fixes are prerequisites, not outcomes of the real-CARLA 50-run measurement. The known xfail concerns missing post-launch gateway-health verification and is not counted as a real-CARLA failure.
