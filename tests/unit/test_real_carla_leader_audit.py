"""Regression tests pinning the independent audit of the committed Leader Change 50-run evidence."""

import json
from pathlib import Path

import pytest

from validation.real_carla_50runs.tools import audit_raw_evidence as audit
from validation.real_carla_50runs.tools.leader_validation import evaluate_leader

ROOT = Path(__file__).resolve().parents[2] / "validation" / "real_carla_50runs"
EVIDENCE = ROOT / "evidence"

pytestmark = pytest.mark.skipif(not (EVIDENCE / "run050").is_dir(), reason="50-run evidence not present")


def _load(run, name):
    return json.loads((EVIDENCE / "run{:03d}".format(run) / name).read_text())


def test_evaluator_and_independent_audit_agree_on_raw_evidence():
    for run in range(1, 51):
        verdict = evaluate_leader(_load(run, "initial_snapshot.json"),
                                  _load(run, "transition_events.json"),
                                  _load(run, "final_snapshot.json"))
        audited = audit.audit_run(EVIDENCE / "run{:03d}".format(run))
        assert verdict["passed"] is True, run
        assert audited["passed"] is True, (run, audited["failed_checks"])


def test_recorded_physical_order_matches_positions():
    for run in range(1, 51):
        order, lon, _ = audit.recompute_order(_load(run, "final_snapshot.json")["actors"])
        assert order == ["truck1", "truck2", "truck0"], run
        assert lon["truck0"] < lon["truck2"] < lon["truck1"] == 0.0


def test_audit_counts_one_accepted_trigger_despite_bridge_echo():
    runs = {n: audit.audit_run(EVIDENCE / "run{:03d}".format(n)) for n in range(1, 51)}
    assert all(r["accepted_triggers"] == 1 for r in runs.values())
    assert all(not r["problems"] for r in runs.values())
    assert sum(r["timeout"] + r["crash"] + r["cleanup_failure"] for r in runs.values()) == 0
