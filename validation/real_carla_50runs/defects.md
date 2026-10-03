# Validation Defect Record

## VAL-L01 — continuous lane rejected at an OpenDRIVE road-segment boundary

### Before

The first runner-owned real-CARLA Leader Change smoke completed the full production maneuver. Evidence is preserved in `evidence_before_fix/leader_smoke_road_segment/run001/`.

- state: `DONE`
- membership: `[truck1, truck2, truck0]`
- physical order: `[truck1, truck2, truck0]`
- lane IDs: all `-3`
- road IDs: truck0 `36`, truck1/truck2 `1149`
- recorded rejoin lateral offset: `0.798343 m`
- collisions: zero
- original verdict: FAIL, `same_final_lane`

### Root cause

The validation evaluator defined lane identity as the pair `(road_id, lane_id)`. In CARLA/OpenDRIVE, `road_id` changes at road-segment boundaries. A physically ordered 35 m platoon can therefore occupy one continuous lane while spanning two road IDs. The production rejoin criterion and requested invariant use lane ID plus lateral offset, not identical road segment IDs.

### Fix

`same_final_lane` compares non-null CARLA `lane_id` values. The unchanged rejoin milestone still requires the production lateral threshold `< 0.8 m`, and physical ordering remains independently required. No production scenario behavior, threshold, timeout, or spawn was changed.

### Regression test

`test_same_lane_across_connected_road_segments_is_not_rejected` uses the observed road IDs `36/1149` with common lane `-3`. It failed before the evaluator correction and passes after it.

### After

The runner-owned smoke is repeated from a fresh CARLA server after the unit and full regression suites pass.
