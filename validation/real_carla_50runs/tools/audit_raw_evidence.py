#!/usr/bin/env python3
"""Independent audit of the Leader Change real-CARLA 50-run raw evidence.

This script deliberately does NOT import leader_validation.py.  It re-derives
every verdict from the raw per-run JSON (and, when present, scenario.log /
bridge.log) and then cross-checks the runner-generated result.json / CSV
summaries against it.  Physical ordering is recomputed from the final actor
locations instead of trusting final_snapshot["physical_order"].

Usage:
    python3 tools/audit_raw_evidence.py [--root validation/real_carla_50runs]
                                        [--write] [--strict-exit]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import socket
import statistics
import sys
from pathlib import Path

EXPECTED_RUNS = 50
TRUCKS = ("truck0", "truck1", "truck2")
REQUIRED_FILES = (
    "ready.json",
    "initial_snapshot.json",
    "trigger_snapshot.json",
    "transition_events.json",
    "final_snapshot.json",
    "result.json",
)
# Production thresholds (scenario/examples/leader_rotation_scenario.py)
GAP_READY_M = 12.0            # _update_gap: gap >= 12.0
GAP_STABLE_TICKS = 10
LC_LATERAL_M = 3.0            # _update_lc: lat_dist > 3.0
BEHIND_TAIL_M = 25.0          # _update_slowdown: NORMAL_FOLLOW_GAP_M + 10.0
REJOIN_LATERAL_M = 0.8        # _update_rejoin: lat_off < 0.8
# Production state order: CRUISE -> MIGRATE -> GAP -> LC -> SLOWDOWN -> REJOIN -> DONE
EVENT_ORDER = ("trigger", "migrate", "logical_transition", "gap_ready",
               "lane_change_complete", "slowdown_complete", "rejoin", "done")
NO_ADJACENT_LANE_MARKER = "인접 차선 없음"
TRIGGER_HTTP_MARKER = "선두 교체 트리거 수신"
BRIDGE_ECHO_MARKER = "leader_rotation 트리거 전송"
TIMING_METRICS = (
    ("trigger_to_migrate_s", "migrate"),
    ("trigger_to_split_s", "logical_transition"),
    ("trigger_to_lane_change_s", "lane_change_complete"),
    ("trigger_to_slowdown_s", "slowdown_complete"),
    ("trigger_to_rejoin_s", "rejoin"),
    ("trigger_to_done_s", "done"),
)
PORTS = (2000, 2001, 18801, 18802, 18803)


def load(path):
    return json.loads(Path(path).read_text())


def first(events, name):
    return next((e for e in events if e.get("event") == name), None)


def percentile_linear(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def percentile_nearest_rank(values, q):
    values = sorted(values)
    rank = max(1, int(math.ceil(q * len(values))))
    return values[rank - 1]


def stats(values):
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 6),
        "p50": round(percentile_linear(values, 0.50), 6),
        "p95_linear": round(percentile_linear(values, 0.95), 6),
        "p95_nearest_rank": round(percentile_nearest_rank(values, 0.95), 6),
        "max": round(max(values), 6),
        "min": round(min(values), 6),
    }


def recompute_order(actors):
    """Order trucks front-to-back along truck1's heading; also return lateral offsets."""
    lead = actors["truck1"]
    yaw = math.radians(float(lead["rotation"]["yaw"]))
    fwd = (math.cos(yaw), math.sin(yaw))
    left = (-fwd[1], fwd[0])
    lon, lat = {}, {}
    for n in TRUCKS:
        dx = actors[n]["location"]["x"] - lead["location"]["x"]
        dy = actors[n]["location"]["y"] - lead["location"]["y"]
        lon[n] = dx * fwd[0] + dy * fwd[1]
        lat[n] = dx * left[0] + dy * left[1]
    return sorted(TRUCKS, key=lambda n: lon[n], reverse=True), lon, lat


def audit_run(run_dir):
    problems = []
    data = {}
    for name in REQUIRED_FILES:
        path = run_dir / name
        if not path.is_file():
            problems.append("missing:" + name)
            continue
        try:
            data[name] = load(path)
        except ValueError as exc:
            problems.append("corrupt:{}:{}".format(name, exc))
    if problems:
        return {"problems": problems, "checks": {}, "passed": False}

    initial = data["initial_snapshot.json"]
    trigger_snap = data["trigger_snapshot.json"]
    events = data["transition_events.json"]
    final = data["final_snapshot.json"]
    result = data["result.json"]
    log = (run_dir / "scenario.log").read_text(errors="replace") if (run_dir / "scenario.log").is_file() else None
    blog = (run_dir / "bridge.log").read_text(errors="replace") if (run_dir / "bridge.log").is_file() else None

    ai, af = initial.get("actors", {}), final.get("actors", {})
    ci, cf = initial.get("controllers", {}), final.get("controllers", {})
    ev = {n: first(events, n) for n in EVENT_ORDER}
    accepted_triggers = sum(1 for e in events if e.get("event") == "trigger" and e.get("accepted"))

    order_ok = all(ev[n] is not None for n in EVENT_ORDER)
    if order_ok:
        times = [ev[n]["since_trigger_s"] for n in EVENT_ORDER]
        order_ok = all(a <= b for a, b in zip(times, times[1:])) and times[0] == 0.0
    expected_states = {"trigger": "CRUISE", "migrate": "CRUISE", "logical_transition": "GAP", "gap_ready": "GAP",
                       "lane_change_complete": "SLOWDOWN", "slowdown_complete": "SLOWDOWN",
                       "rejoin": "REJOIN", "done": "DONE"}
    state_ok = all(ev[n] is not None and ev[n].get("scenario_state") == s for n, s in expected_states.items())

    order, lon, lat = recompute_order(af) if set(TRUCKS) <= set(af) else (None, {}, {})
    lanes = {af.get(n, {}).get("lane_id") for n in TRUCKS}
    lc, sd, rj, tr = ev["lane_change_complete"], ev["slowdown_complete"], ev["rejoin"], ev["logical_transition"]
    checks = {
        "initial_state": initial.get("scenario_state") == "CRUISE"
        and initial.get("main_members") == list(TRUCKS) and initial.get("leader") == "truck0"
        and set(ai) == set(TRUCKS) and all(ai[n].get("alive") for n in TRUCKS)
        and ci.get("truck0", {}).get("type") == "LeadNavigator"
        and all(ci.get(n, {}).get("type") == "FollowerController" for n in ("truck1", "truck2")),
        "trigger_snapshot_cruise": trigger_snap.get("scenario_state") == "CRUISE"
        and trigger_snap.get("leader") == "truck0",
        "single_accepted_trigger": accepted_triggers == 1 and final.get("trigger_count") == 1,
        "event_order": order_ok,
        "state_sequence": state_ok,
        "leader_transition": bool(tr) and tr.get("leader") == "truck1"
        and tr.get("main_members") == ["truck1", "truck2"] and tr.get("detached_members") == ["truck0"],
        "gap_ready": bool(ev["gap_ready"]) and ev["gap_ready"].get("gap_m", -1) >= GAP_READY_M
        and ev["gap_ready"].get("stable_ticks", -1) >= GAP_STABLE_TICKS,
        "lane_departure": bool(lc) and lc.get("lateral_m", -1) > LC_LATERAL_M and lc.get("forced") is False,
        "adjacent_lane_found": not bool(log and NO_ADJACENT_LANE_MARKER in log),
        "rear_position": bool(sd) and sd.get("behind_tail_m", -1) >= BEHIND_TAIL_M and sd.get("forced") is False,
        "rejoin_lateral": bool(rj) and rj.get("lateral_m", float("inf")) < REJOIN_LATERAL_M,
        "final_membership": final.get("main_members") == ["truck1", "truck2", "truck0"]
        and final.get("leader") == "truck1" and final.get("detached_members", []) == [],
        "controllers": cf.get("truck1", {}).get("type") == "LeadNavigator"
        and cf.get("truck2", {}).get("type") == "FollowerController"
        and cf.get("truck0", {}).get("type") == "FollowerController"
        and all(cf.get(n, {}).get("platoon") == "main" for n in TRUCKS),
        "same_final_lane_id": len(lanes) == 1 and None not in lanes,
        "physical_order_recomputed": order == ["truck1", "truck2", "truck0"],
        "physical_order_recorded_matches": final.get("physical_order") == order,
        "actors_alive": set(af) == set(TRUCKS) and all(af[n].get("alive") for n in TRUCKS),
        "vehicles_moving": all(af.get(n, {}).get("speed_kmh", 0) > 0 for n in TRUCKS),
        "collision_free": final.get("collisions") == [],
        "done": final.get("scenario_state") == "DONE" and ev["done"] is not None,
        "no_timeout": not result.get("timeout"),
        "no_crash": not result.get("crash"),
        "cleanup_ok": not result.get("cleanup_failure"),
    }
    timings = {k: (ev[e].get("since_trigger_s") if ev[e] else None) for k, e in TIMING_METRICS}
    return {
        "problems": problems,
        "checks": checks,
        "passed": all(checks.values()),
        "failed_checks": [k for k, v in checks.items() if not v],
        "runner_passed": bool(result.get("passed")),
        "runner_timings": result.get("timings", {}),
        "timeout": bool(result.get("timeout")),
        "crash": bool(result.get("crash")),
        "cleanup_failure": bool(result.get("cleanup_failure")),
        "accepted_triggers": accepted_triggers,
        "trigger_http_receipts": log.count(TRIGGER_HTTP_MARKER) if log is not None else None,
        "bridge_trigger_echoes": blog.count(BRIDGE_ECHO_MARKER) if blog is not None else None,
        "rejoin_lateral_m": rj.get("lateral_m") if rj else None,
        "lane_change_lateral_m": lc.get("lateral_m") if lc else None,
        "behind_tail_m": sd.get("behind_tail_m") if sd else None,
        "final_lanes": {n: (af.get(n, {}).get("road_id"), af.get(n, {}).get("lane_id")) for n in TRUCKS},
        "final_longitudinal_m": {n: round(v, 3) for n, v in lon.items()},
        "final_lateral_m": {n: round(v, 3) for n, v in lat.items()},
        "timings": timings,
    }


def read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def port_listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    evidence = root / "evidence"

    issues = []
    run_dirs = sorted(p for p in evidence.iterdir() if p.is_dir() and p.name.startswith("run"))
    expected_names = ["run{:03d}".format(i) for i in range(1, EXPECTED_RUNS + 1)]
    if [p.name for p in run_dirs] != expected_names:
        issues.append("run directory set != run001..run050: {}".format([p.name for p in run_dirs]))

    runs = {}
    for p in run_dirs:
        audited = audit_run(p)
        runs[int(p.name[3:])] = audited
        for prob in audited["problems"]:
            issues.append("{}: {}".format(p.name, prob))

    verdict_mismatch = [n for n, r in runs.items() if not r["problems"] and r["runner_passed"] != r["passed"]]
    timing_mismatch = [(n, k) for n, r in runs.items() for k, _ in TIMING_METRICS
                       if r.get("runner_timings", {}).get(k) != r.get("timings", {}).get(k)]

    csv_report = {}
    rs_path, ts_path, fl_path = root / "runs_summary.csv", root / "timing_summary.csv", root / "failures.csv"
    if rs_path.is_file():
        rows = read_csv(rs_path)
        bad = [row["run"] for row in rows if int(row["run"]) in runs and (
            (row["passed"] == "True") != runs[int(row["run"])]["runner_passed"]
            or (row["timeout"] == "True") != runs[int(row["run"])]["timeout"]
            or (row["crash"] == "True") != runs[int(row["run"])]["crash"]
            or (row["cleanup_failure"] == "True") != runs[int(row["run"])]["cleanup_failure"])]
        csv_report["runs_summary_rows"] = len(rows)
        csv_report["runs_summary_vs_result_json_mismatches"] = bad
        if len(rows) != EXPECTED_RUNS or bad:
            issues.append("runs_summary.csv inconsistent: rows={} mismatches={}".format(len(rows), bad))
    else:
        issues.append("runs_summary.csv missing")
    if ts_path.is_file():
        rows = read_csv(ts_path)
        bad = []
        for row in rows:
            r = runs.get(int(row["run"]))
            for key, _ in TIMING_METRICS:
                if r is None or abs(float(row[key]) - float(r["timings"][key])) > 1e-9:
                    bad.append((row["run"], key))
        csv_report["timing_summary_rows"] = len(rows)
        csv_report["timing_summary_vs_raw_events_mismatches"] = bad
        if len(rows) != EXPECTED_RUNS or bad:
            issues.append("timing_summary.csv inconsistent: rows={} mismatches={}".format(len(rows), bad))
    else:
        issues.append("timing_summary.csv missing")
    if fl_path.is_file():
        rows = read_csv(fl_path)
        runner_failed = sorted(n for n, r in runs.items() if not r["runner_passed"])
        csv_report["failures_csv_runs"] = [int(r["run"]) for r in rows]
        if sorted(int(r["run"]) for r in rows) != runner_failed:
            issues.append("failures.csv does not match runner result.json failures")

    audited_pass = sorted(n for n, r in runs.items() if r["passed"])
    audited_fail = sorted(n for n, r in runs.items() if not r["passed"])
    all_timings = {k: [r["timings"][k] for r in runs.values() if r["timings"].get(k) is not None] for k, _ in TIMING_METRICS}
    pass_timings = {k: [runs[n]["timings"][k] for n in audited_pass if runs[n]["timings"].get(k) is not None]
                    for k, _ in TIMING_METRICS}
    ports = {str(p): ("LISTENING" if port_listening(p) else "free") for p in PORTS}

    summary = {
        "scenario": "Leader Change",
        "root": str(root),
        "run_dirs_found": len(run_dirs),
        "result_json_parsed": sum(1 for r in runs.values() if not r["problems"]),
        "evidence_integrity_issues": issues,
        "runner_reported_pass": sum(r["runner_passed"] for r in runs.values()),
        "audited_pass": len(audited_pass),
        "audited_fail_runs": {n: runs[n]["failed_checks"] for n in audited_fail},
        "runner_vs_audit_verdict_mismatch_runs": verdict_mismatch,
        "result_json_timing_vs_raw_event_mismatches": timing_mismatch,
        "timeouts": sum(r.get("timeout", False) for r in runs.values()),
        "crashes": sum(r.get("crash", False) for r in runs.values()),
        "cleanup_failures": sum(r.get("cleanup_failure", False) for r in runs.values()),
        "duplicate_accepted_triggers": sum(max(0, r.get("accepted_triggers", 0) - 1) for r in runs.values()),
        "trigger_http_receipts_per_run": sorted({r.get("trigger_http_receipts") for r in runs.values()}, key=str),
        "bridge_trigger_echoes_per_run": sorted({r.get("bridge_trigger_echoes") for r in runs.values()}, key=str),
        "max_rejoin_lateral_m": max((r["rejoin_lateral_m"] for r in runs.values()
                                     if r.get("rejoin_lateral_m") is not None), default=None),
        "min_lane_change_lateral_m": min((r["lane_change_lateral_m"] for r in runs.values()
                                          if r.get("lane_change_lateral_m") is not None), default=None),
        "min_behind_tail_m": min((r["behind_tail_m"] for r in runs.values()
                                  if r.get("behind_tail_m") is not None), default=None),
        "csv": csv_report,
        "timing_all_runs": {k: stats(v) for k, v in all_timings.items()},
        "timing_audited_pass_runs": {k: stats(v) for k, v in pass_timings.items()},
        "ports_now": ports,
    }

    if args.write:
        out = root / "audit"
        out.mkdir(exist_ok=True)
        (out / "audit_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        check_names = list(next(iter(runs.values()))["checks"].keys())
        with open(out / "audit_runs.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["run", "audited_passed", "runner_passed", "failed_checks", "lon_t1", "lon_t2", "lon_t0",
                        "lat_t0", "final_lane_t0", "final_lane_t1", "final_lane_t2"] + check_names)
            for n in sorted(runs):
                r = runs[n]
                w.writerow([n, r["passed"], r["runner_passed"], ";".join(r["failed_checks"]),
                            r["final_longitudinal_m"].get("truck1"), r["final_longitudinal_m"].get("truck2"),
                            r["final_longitudinal_m"].get("truck0"), r["final_lateral_m"].get("truck0"),
                            r["final_lanes"]["truck0"], r["final_lanes"]["truck1"], r["final_lanes"]["truck2"]]
                           + [r["checks"][c] for c in check_names])
        with open(out / "audit_timing.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["population", "metric", "n", "mean", "p50", "p95_linear", "p95_nearest_rank", "max", "min"])
            for pop, table in (("all_50", summary["timing_all_runs"]), ("audited_pass", summary["timing_audited_pass_runs"])):
                for metric, s in table.items():
                    w.writerow([pop, metric, s["n"], s["mean"], s["p50"], s["p95_linear"], s["p95_nearest_rank"], s["max"], s["min"]])

    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    if args.strict_exit and (issues or timing_mismatch):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
