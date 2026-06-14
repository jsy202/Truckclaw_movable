import json

import pytest
from unittest.mock import MagicMock

from fakes.carla_stub import load_scenario_module
from validation.real_carla_50runs.tools.scenario_recorder import ScenarioRecorder
from validation.real_carla_50runs.tools.leader_validation import (
    atomic_write_json,
    evaluate_leader,
    load_json,
    summarize_results,
)


def good_leader_evidence():
    initial = {
        "scenario_state": "CRUISE",
        "main_members": ["truck0", "truck1", "truck2"],
        "leader": "truck0",
        "actors": {name: {"alive": True} for name in ("truck0", "truck1", "truck2")},
        "controllers": {
            "truck0": {"type": "LeadNavigator", "platoon": "main"},
            "truck1": {"type": "FollowerController", "platoon": "main"},
            "truck2": {"type": "FollowerController", "platoon": "main"},
        },
    }
    events = [
        {"event": "trigger", "accepted": True, "trigger_count": 1, "since_trigger_s": 0.0},
        {"event": "migrate", "since_trigger_s": 0.01},
        {"event": "logical_transition", "main_members": ["truck1", "truck2"], "detached_members": ["truck0"], "leader": "truck1", "since_trigger_s": 0.2},
        {"event": "gap_ready", "gap_m": 12.2, "stable_ticks": 10, "since_trigger_s": 1.0},
        {"event": "lane_change_complete", "lateral_m": 3.2, "forced": False, "since_trigger_s": 2.0},
        {"event": "slowdown_complete", "behind_tail_m": 25.1, "forced": False, "since_trigger_s": 4.0},
        {"event": "rejoin", "lateral_m": 0.7, "since_trigger_s": 6.0},
        {"event": "done", "since_trigger_s": 6.1},
    ]
    final = {
        "scenario_state": "DONE",
        "main_members": ["truck1", "truck2", "truck0"],
        "leader": "truck1",
        "detached_members": [],
        "actors": {
            "truck0": {"alive": True, "road_id": 46, "lane_id": -3, "speed_kmh": 19.5},
            "truck1": {"alive": True, "road_id": 46, "lane_id": -3, "speed_kmh": 20.0},
            "truck2": {"alive": True, "road_id": 46, "lane_id": -3, "speed_kmh": 19.8},
        },
        "controllers": {
            "truck0": {"type": "FollowerController", "platoon": "main"},
            "truck1": {"type": "LeadNavigator", "platoon": "main"},
            "truck2": {"type": "FollowerController", "platoon": "main"},
        },
        "physical_order": ["truck1", "truck2", "truck0"],
        "collisions": [],
        "trigger_count": 1,
    }
    return initial, events, final


def test_evaluator_requires_transition_and_physical_order_not_done_alone():
    initial, events, final = good_leader_evidence()
    assert evaluate_leader(initial, events, final)["passed"] is True
    final["physical_order"] = ["truck1", "truck0", "truck2"]
    result = evaluate_leader(initial, events, final)
    assert result["passed"] is False
    assert result["checks"]["physical_order"] is False


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    [
        (lambda i, e, f: e.__setitem__(3, {**e[3], "gap_m": 11.99}), "gap_ready"),
        (lambda i, e, f: e.__setitem__(3, {**e[3], "stable_ticks": 9}), "gap_ready"),
        (lambda i, e, f: e.__setitem__(4, {**e[4], "lateral_m": 3.0}), "lane_departure"),
        (lambda i, e, f: e.__setitem__(4, {**e[4], "forced": True}), "no_forced_transition"),
        (lambda i, e, f: e.__setitem__(5, {**e[5], "behind_tail_m": 24.99}), "rear_position"),
        (lambda i, e, f: e.__setitem__(5, {**e[5], "forced": True}), "no_forced_transition"),
        (lambda i, e, f: e.__setitem__(6, {**e[6], "lateral_m": 0.8}), "rejoin"),
        (lambda i, e, f: f.update(main_members=["truck1", "truck0", "truck2"]), "logical_membership"),
        (lambda i, e, f: f["controllers"]["truck0"].update(platoon="detached"), "controller_references"),
        (lambda i, e, f: f["actors"]["truck0"].update(lane_id=-4), "same_final_lane"),
        (lambda i, e, f: f["collisions"].append({"actor": "truck0"}), "collision_free"),
        (lambda i, e, f: f.update(trigger_count=2), "single_trigger"),
    ],
)
def test_evaluator_rejects_each_fixed_leader_invariant(mutation, failed_check):
    initial, events, final = good_leader_evidence()
    mutation(initial, events, final)
    result = evaluate_leader(initial, events, final)
    assert result["passed"] is False
    assert result["checks"][failed_check] is False


def test_evaluator_requires_original_initial_roles():
    initial, events, final = good_leader_evidence()
    initial["leader"] = "truck1"
    result = evaluate_leader(initial, events, final)
    assert result["checks"]["initial_state"] is False


def test_same_lane_across_connected_road_segments_is_not_rejected():
    """Catch treating CARLA road-segment ids as part of lane identity for a long platoon."""
    initial, events, final = good_leader_evidence()
    final["actors"]["truck0"]["road_id"] = 36
    final["actors"]["truck1"]["road_id"] = 1149
    final["actors"]["truck2"]["road_id"] = 1149
    result = evaluate_leader(initial, events, final)
    assert result["checks"]["same_final_lane"] is True
    assert result["passed"] is True


def test_atomic_json_never_leaves_temporary_file(tmp_path):
    path = tmp_path / "evidence" / "result.json"
    atomic_write_json(path, {"passed": False, "run": 3})
    assert json.loads(path.read_text()) == {"passed": False, "run": 3}
    assert list(path.parent.glob("*.tmp")) == []


def test_missing_or_corrupt_json_is_an_explicit_error(tmp_path):
    with pytest.raises(ValueError, match="missing JSON"):
        load_json(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_json(bad)


def test_summary_preserves_failures_and_uses_interpolated_percentiles():
    results = [
        {"passed": True, "timings": {"trigger_to_done_s": 2.0}},
        {"passed": False, "timings": {"trigger_to_done_s": 4.0}, "failure_reasons": ["physical_order"]},
        {"passed": True, "timings": {"trigger_to_done_s": 6.0}},
    ]
    summary = summarize_results(results)
    assert summary["runs"] == 3 and summary["successes"] == 2 and summary["failures"] == 1
    assert summary["timing"] == {"mean": 4.0, "p50": 4.0, "p95": 5.8, "max": 6.0}


def test_coordinator_records_only_one_accepted_trigger(monkeypatch):
    scenario = load_scenario_module(monkeypatch)
    coord = scenario.LeaderRotationCoordinator(MagicMock(), MagicMock(), MagicMock(), migrator=None)
    recorder = MagicMock()
    coord.validation_recorder = recorder
    coord.trigger()
    coord.trigger()
    recorder.record.assert_called_once_with("trigger", accepted=True)


def test_recorder_preserves_rotation_milestones_and_waits_for_settle(tmp_path):
    initial, events, final = good_leader_evidence()
    current = dict(initial)
    recorder = ScenarioRecorder(tmp_path, lambda: current, ready_s=5.0, settle_s=2.0)
    recorder.capture_initial()
    assert recorder.maybe_ready(4.99) is False
    assert recorder.maybe_ready(5.0) is True
    for event in events:
        current.clear()
        current.update(final if event["event"] == "done" else initial)
        recorder.record(event["event"], **{k: v for k, v in event.items() if k not in ("event", "since_trigger_s")})
    assert recorder.poll(6.99) is False
    assert recorder.poll(7.0) is True
    saved = json.loads((tmp_path / "transition_events.json").read_text())
    assert [item["event"] for item in saved] == [item["event"] for item in events]
    assert json.loads((tmp_path / "result.json").read_text())["passed"] is True


def test_collision_event_makes_rotation_fail(tmp_path):
    initial, events, final = good_leader_evidence()
    current = dict(initial)
    recorder = ScenarioRecorder(tmp_path, lambda: current, ready_s=0.0, settle_s=2.0)
    recorder.capture_initial()
    recorder.record_collision("truck0", 44, 6.2)
    for event in events:
        current.clear()
        current.update(final if event["event"] == "done" else initial)
        recorder.record(event["event"], **{k: v for k, v in event.items() if k not in ("event", "since_trigger_s")})
    assert recorder.poll(2.0) is True
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["passed"] is False
    assert "collision_free" in result["failure_reasons"]
