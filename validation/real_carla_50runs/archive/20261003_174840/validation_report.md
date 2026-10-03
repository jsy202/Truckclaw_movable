# Leader Change Real-CARLA 50-Run Validation Report

Base: local `validation` commit `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64` on branch `validation-carla-50runs`.

This report measures repeated E2E completion only under the fixed CARLA condition described in the design. It does not claim general reliability.

| Metric | Result |
|---|---:|
| Runs | 1 |
| Physical Leader Change success | 1/1 |
| Final order correct | 1/1 |
| Logical / physical consistency | 1/1 |
| OpenClaw migration success | Not tested |
| Timeout | 0 |
| Crash | 0 |
| Cleanup failure | 0 |
| Mean trigger → DONE | 9.301 |
| P50 | 9.301 |
| P95 | 9.301 |
| Max | 9.301 |

## Separate CARLA-free regression baseline

Before this work, 22 tests passed with one known gateway-health xfail. These tests are not included in the real-CARLA run count.

## Final Comparison

| Scenario | Repository | Real CARLA Runs | Main Verification |
|---|---|---:|---|
| Transfer | Truckclaw-improve | 10 | request → physical merge → logical membership |
| Split | Truckclaw_copyable | pending separate report | platoon detach → independent driving |
| Leader Change | Truckclaw_movable | 1 | leader transition → old leader physical rejoin |
