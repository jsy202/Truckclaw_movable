# Leader Change Real-CARLA 50-Run Validation Design

## Purpose

Validate whether the production Leader Change/Rotation scenario completes consistently across 50 repetitions under one fixed CARLA condition. The result is limited to this condition and must be reported as “X of 50 runs succeeded under the fixed CARLA condition.”

## Validation target

- Base branch: local `validation`
- Base commit: `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64`
- Production scenario: `scenario/examples/leader_rotation_scenario.py`
- Actual state machine: `CRUISE → MIGRATE → GAP → LC → SLOWDOWN → REJOIN → DONE`
- CARLA: 0.9.13, Town06, synchronous `dt=0.01`, `vehicle.carlamotors.carlacola`
- Server flags: `-RenderOffScreen -quality-level=Low`
- OpenClaw: `Not tested`; physical runs use the existing `--no-openclaw` path

At the first `GAP` update, truck0 is split into a temporary platoon and truck1 receives a leader controller. At `REJOIN`, the temporary platoon is merged to form `[truck1, truck2, truck0]`. Logical promotion, lane departure, slowdown/rear positioning, lane return, and physical final order are distinct observations.

## Architecture

The production scenario remains the executable under test. A validation-only hook, enabled only by a CLI evidence-directory option, records direct in-process state without changing the normal path. It records state transitions, `Core.Platoon` membership, leader/follower controller references, actor identity and kinematics, waypoint road/lane, collision events, signed longitudinal/lateral relationships, and monotonic timing.

An external runner owns every CARLA process used by the suite. For each run it verifies port 2000 is free, starts CARLA, waits for RPC readiness, starts the bridge and production scenario, waits for the ready artifact, sends exactly one HTTP trigger, watches the bounded run, saves evidence, terminates the scenario and bridge, stops CARLA, verifies the port is free, and continues after a non-fatal run failure.

The manually started CARLA process may be used only for the smoke run. Before the 50-run suite, it must be stopped and port 2000 must be verified free. The suite refuses to start a server when the port is occupied by a process it does not own.

## Evidence and data flow

Each `evidence/runNNN/` contains `initial_snapshot.json`, `trigger_snapshot.json`, `transition_events.json`, `final_snapshot.json`, `result.json`, and compact scenario/bridge/CARLA logs. Actor names map from direct Python vehicle objects. Atomic writes prevent partial evidence reads. Timings use monotonic wall time from accepted trigger, with simulation time retained for diagnosis.

## Fixed PASS invariants

A run passes only when all of these hold:

1. Initial: exactly three actors are alive in `[truck0, truck1, truck2]`; truck0 is leader, truck1 follows truck0, and truck2 follows truck1 through the production platoon/controller structure.
2. Trigger: the runner sends one trigger and the scenario records one accepted trigger.
3. Transition: truck0 leaves the main membership in `GAP`, truck1 becomes the actual main leader with `LeadNavigator`, and truck2 remains its follower with the correct platoon reference.
4. Maneuver: the production 12.0 m GAP criterion is observed for 10 ticks; truck0 reaches the adjacent lane with lateral offset greater than 3.0 m; `SLOWDOWN` reaches at least `NORMAL_FOLLOW_GAP_M + 10.0 = 25.0 m` behind truck2 without the 8000-tick forced path; `REJOIN` returns to the original lane with lateral offset below 0.8 m.
5. Final logical state: membership is `[truck1, truck2, truck0]`; truck1 is leader; truck2 and truck0 have follower controllers referencing that platoon.
6. Final physical state: all actors are alive on the same road/lane, forward-axis projections give strict order `truck1 → truck2 → truck0`, all remain moving after a fixed 2.0 simulation-second settle interval, and no collision is recorded.
7. Final state: scenario state is `DONE`; no timeout, crash, forced state transition, or validation abort occurred.

`DONE` alone never passes a run. Thresholds above are production constants and are not changed based on results.

## Timing

Record trigger→MIGRATE, trigger→logical split/promotion, trigger→lane-change complete, trigger→slowdown complete, trigger→rejoin, and trigger→DONE. OpenClaw Migration duration is `Not tested`.

## Failure handling

A failed run retains its last valid transition, logical membership/controller state, exception/timeout, collision data, and final physical snapshot. Cleanup then runs and the next run proceeds. If a production defect is confirmed, preserve Before evidence, write a failing regression test, make the smallest production correction, prove RED→GREEN, and restart the 50-run measurement from run 1.

## Outputs

`validation/real_carla_50runs/` contains `validation_report.md`, `runs_summary.csv`, `timing_summary.csv`, `failures.csv`, `environment.json`, `limitations.md`, `run.log`, the baseline record, tools, tests, and 50 evidence directories. Existing CARLA-free regression results remain separate and never count as real-CARLA runs.

## Non-goals

No claim is made about other maps, vehicles, speeds, spawn points, render modes, hardware, real vehicles, production reliability, or failure probability. OpenClaw Migration is not exercised in this environment.
