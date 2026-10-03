# Leader Change Real-CARLA 50-Run Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add validation-only observation and lifecycle automation, then execute and report 50 fixed-condition real-CARLA Leader Change runs.

**Architecture:** The production scenario receives an opt-in recorder hook; pure validation code serializes direct membership/controller/actor state and evaluates fixed invariants. A separate runner exclusively owns a fresh CARLA server, bridge, and scenario process per measured run and aggregates preserved evidence.

**Tech Stack:** Python 3.7 scenario/CARLA client, Python 3.10 orchestration and pytest, CARLA 0.9.13, stdlib JSON/CSV/subprocess/HTTP.

**Spec:** `docs/superpowers/specs/2026-10-03-real-carla-50runs-design.md`

## Global Constraints

- Run the production state machine; validation hooks are inactive without the validation CLI flag.
- Fixed production thresholds: GAP 12.0 m for 10 ticks, lane departure lateral offset over 3.0 m, rear position 25.0 m without forced timeout, rejoin lateral offset below 0.8 m, settle 2.0 simulation seconds.
- `DONE` alone is never sufficient.
- OpenClaw is `Not tested`; measured physical runs use `--no-openclaw`.
- Each measured run uses a runner-owned fresh off-screen CARLA server and must leave port 2000 free.
- Existing 22 passed + one known xfail remain separate from real-CARLA counts.
- Preserve failures and never tune thresholds, timeouts, spawns, or PASS criteria after observing results.

## Review Focus

- `DONE` with wrong controller reference, membership, lane, or projected order must fail independently.
- Forced LC/SLOWDOWN timeout transitions must fail even if the state later reaches `DONE`.
- `Core.Platoon.merge()` must safely handle the temporary platoon lifecycle actually used by the scenario.
- Occupied port 2000 must prevent a second server; owned processes must always be cleaned up.
- Crash/timeout and mixed successful/failed runs must produce complete honest summaries.

---

### Task 1: Pure snapshot and result evaluator

**Files:**
- Create: `validation/real_carla_50runs/tools/leader_validation.py`
- Create: `tests/unit/test_real_carla_leader_validation.py`

**Interfaces:**
- Produces: `atomic_write_json(path, value)`, `actor_snapshot(vehicle, cmap)`, `platoon_snapshot(coord, sim, names)`, `evaluate_leader(initial, events, final) -> dict`, and `summarize_results(results) -> dict`.
- Consumes: plain dict snapshots; CARLA objects only in serializer functions.

- [ ] **Step 1: Write failing evaluator and atomic-write tests** asserting every fixed logical/controller/physical invariant, forced-transition rejection, order projection, missing evidence behavior, percentiles, and failure preservation.
- [ ] **Step 2: Run `python3 -m pytest tests/unit/test_real_carla_leader_validation.py -q` and verify failure because the module is absent.**
- [ ] **Step 3: Implement the minimal pure serializer/evaluator module.**
- [ ] **Step 4: Run the focused test and full `python3 -m pytest -q`; expect all tests green with the inherited xfail unchanged.**
- [ ] **Step 5: Commit `test(validation): add Leader Change evidence evaluator`.**

### Task 2: Opt-in production-scenario recorder

**Files:**
- Create: `validation/real_carla_50runs/tools/scenario_recorder.py`
- Modify: `scenario/examples/leader_rotation_scenario.py`
- Modify: `tests/unit/test_real_carla_leader_validation.py`

**Interfaces:**
- Consumes: Task 1 serializers/evaluator.
- Produces: `ScenarioRecorder`; CLI flags `--validation-evidence-dir`, `--validation-ready-s`, and `--validation-settle-s`; direct JSON artifacts and collision events.

- [ ] **Step 1: Add failing stub-CARLA tests for unchanged normal behavior, trigger counting, split/promotion, lane departure, slowdown, rejoin, controller references, collision handling, and settle finalization.**
- [ ] **Step 2: Run focused tests and verify expected missing-hook failures.**
- [ ] **Step 3: Implement the recorder and smallest scenario hook, including collision-sensor cleanup.**
- [ ] **Step 4: Run focused tests and full suite; expect inherited 22 pass + one xfail plus new tests.**
- [ ] **Step 5: Commit `feat(validation): instrument production Leader Change scenario`.**

### Task 3: Exclusive CARLA lifecycle runner and report generation

**Files:**
- Create: `validation/real_carla_50runs/tools/run_leader_change_validation.py`
- Create: `tests/unit/test_real_carla_leader_runner.py`
- Create/update: `validation/real_carla_50runs/environment.json`, `limitations.md`

**Interfaces:**
- Consumes: scenario artifacts and Task 1 summarizer.
- Produces: CLI `--runs`, `--smoke`, `--allow-existing-carla-for-smoke`, foreign-port refusal, per-run evidence, `runs_summary.csv`, `timing_summary.csv`, `failures.csv`, `run.log`, and report rendering.

- [ ] **Step 1: Write failing lifecycle tests for foreign-port refusal, owned cleanup, failure continuation, crash/timeout synthesis, and 50-row aggregation.**
- [ ] **Step 2: Run focused tests and verify missing-runner failures.**
- [ ] **Step 3: Implement process-group lifecycle, readiness probes, one HTTP trigger, cleanup verification, aggregation, and environment capture.**
- [ ] **Step 4: Run focused tests and full suite; expect all green with the inherited xfail.**
- [ ] **Step 5: Commit `feat(validation): automate isolated Leader Change CARLA runs`.**

### Task 4: Smoke, defect gate, 50-run measurement, and report

**Files:**
- Create: `validation/real_carla_50runs/evidence/run001` through `run050`
- Create/update: `validation/real_carla_50runs/validation_report.md`, CSV summaries, `environment.json`, `limitations.md`, `run.log`
- If confirmed: minimal production fix plus regression test and `evidence/before_fix/`.

**Interfaces:**
- Consumes: Task 3 runner.
- Produces: measured artifacts and local branch commit.

- [ ] **Step 1: Run one smoke run and inspect direct state/controller/physical artifacts.**
- [ ] **Step 2: If a defect occurs, preserve Before evidence, use systematic debugging, add a RED regression, minimally fix, prove GREEN/full-suite, and repeat smoke.**
- [ ] **Step 3: Stop the manual CARLA server, verify port 2000 free, then run `python3 validation/real_carla_50runs/tools/run_leader_change_validation.py --runs 50`.**
- [ ] **Step 4: Verify exactly 50 results/directories, recompute metrics, inspect failures, and render the report without unsupported claims.**
- [ ] **Step 5: Run full pytest, syntax checks, `git diff --check`, evidence consistency audit, and commit `validation(real-carla): record 50 Leader Change runs`.**
