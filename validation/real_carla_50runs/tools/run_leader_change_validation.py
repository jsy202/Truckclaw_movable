#!/usr/bin/env python3
"""Run fixed-condition Leader Change validation against real CARLA 0.9.13."""

from __future__ import annotations

import argparse, csv, json, math, os, signal, socket, statistics, subprocess, sys, time, urllib.request
from pathlib import Path

try:
    from .leader_validation import atomic_write_json, load_json
except ImportError:
    from leader_validation import atomic_write_json, load_json

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "validation" / "real_carla_50runs"
CARLA = Path.home() / "carla-0.9.13"
CARLA_EGG = CARLA / "PythonAPI" / "carla" / "dist" / "carla-0.9.13-py3.7-linux-x86_64.egg"
SCENARIO_ENV = dict(os.environ)
SCENARIO_ENV["PYTHONPATH"] = ":".join((str(CARLA_EGG), str(CARLA / "PythonAPI" / "carla"), str(ROOT / "scenario" / "src")))
FIXED_RUN_TIMEOUT_S = 300.0


def port_open(host, port):
    with socket.socket() as client:
        client.settimeout(0.5)
        return client.connect_ex((host, port)) == 0


def ensure_port_free(host, port):
    if port_open(host, port):
        raise RuntimeError("{}:{} is already occupied; refusing to start a second server".format(host, port))


def stop_process(process, timeout=20.0):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL); process.wait()


def wait_until(predicate, timeout, description, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(interval)
    raise TimeoutError(description)


def http_json(method, url, body=None, timeout=5.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode()
        return response.status, json.loads(payload) if payload else None


def carla_rpc_ready():
    check = subprocess.run(
        ["python3.7", "-c", "import carla;c=carla.Client('127.0.0.1',2000);c.set_timeout(10);print(c.get_server_version())"],
        env=SCENARIO_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20,
    )
    return check.returncode == 0 and check.stdout.strip() == "0.9.13"


def start_carla(log_path):
    ensure_port_free("127.0.0.1", 2000)
    handle = open(str(log_path), "w")
    process = subprocess.Popen(
        [str(CARLA / "CarlaUE4.sh"), "-RenderOffScreen", "-quality-level=Low", "-carla-rpc-port=2000"],
        cwd=str(CARLA), stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)

    def ready():
        if process.poll() is not None: raise RuntimeError("CARLA exited during startup with code {}".format(process.returncode))
        return port_open("127.0.0.1", 2000) and carla_rpc_ready()
    wait_until(ready, 120.0, "CARLA readiness timeout")
    return process, handle


def start_bridge(log_path):
    ensure_port_free("127.0.0.1", 18801)
    handle = open(str(log_path), "w")
    env = dict(os.environ); env["PLATOON_DESTINATIONS_PATH"] = str(ROOT / "platoon_destinations.json")
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "bridge" / "platoon_bridge_server.py")],
                               cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)

    def ready():
        if process.poll() is not None: raise RuntimeError("bridge exited during startup with code {}".format(process.returncode))
        try: return http_json("GET", "http://127.0.0.1:18801/health", timeout=1)[0] == 200
        except Exception: return False
    wait_until(ready, 15.0, "bridge readiness timeout")
    return process, handle


def start_scenario(run_dir):
    ensure_port_free("127.0.0.1", 18802); ensure_port_free("127.0.0.1", 18803)
    handle = open(str(run_dir / "scenario.log"), "w")
    command = ["python3.7", "-u", str(ROOT / "scenario" / "examples" / "leader_rotation_scenario.py"),
               "--no-openclaw", "--validation-evidence-dir", str(run_dir),
               "--validation-ready-s", "5.0", "--validation-settle-s", "2.0"]
    process = subprocess.Popen(command, cwd=str(ROOT), env=SCENARIO_ENV, stdout=handle,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    return process, handle


def _close(handle):
    if handle: handle.close()


def run_real_once(number, run_dir, use_existing_carla=False):
    carla_process = bridge_process = scenario_process = None
    carla_log = bridge_log = scenario_log = None
    cleanup_failure = False
    try:
        if use_existing_carla:
            if not port_open("127.0.0.1", 2000) or not carla_rpc_ready():
                raise RuntimeError("smoke requested existing CARLA, but no ready 0.9.13 server owns port 2000")
        else:
            carla_process, carla_log = start_carla(run_dir / "carla_server.log")
        bridge_process, bridge_log = start_bridge(run_dir / "bridge.log")
        scenario_process, scenario_log = start_scenario(run_dir)

        def scenario_ready():
            if scenario_process.poll() is not None: raise RuntimeError("scenario exited before ready with code {}".format(scenario_process.returncode))
            return (run_dir / "ready.json").is_file()
        wait_until(scenario_ready, 180.0, "scenario readiness timeout")
        status, _ = http_json("POST", "http://127.0.0.1:18803/leader_rotation", {})
        if status != 200: raise RuntimeError("trigger returned HTTP {}".format(status))

        def result_ready():
            if (run_dir / "result.json").is_file(): return True
            if scenario_process.poll() is not None: raise RuntimeError("scenario exited before result with code {}".format(scenario_process.returncode))
            return False
        wait_until(result_ready, FIXED_RUN_TIMEOUT_S, "fixed {} second run timeout".format(FIXED_RUN_TIMEOUT_S))
        result = load_json(run_dir / "result.json")
    finally:
        stop_process(scenario_process); stop_process(bridge_process)
        if not use_existing_carla: stop_process(carla_process, timeout=30.0)
        _close(scenario_log); _close(bridge_log); _close(carla_log)
        if not use_existing_carla:
            try: wait_until(lambda: not port_open("127.0.0.1", 2000), 30.0, "CARLA port remained occupied", 0.5)
            except TimeoutError: cleanup_failure = True
        if any(port_open("127.0.0.1", p) for p in (18801, 18802, 18803)): cleanup_failure = True
    result["run"] = number; result.setdefault("timeout", False); result.setdefault("crash", False)
    result["cleanup_failure"] = cleanup_failure
    if cleanup_failure:
        result["passed"] = False; result.setdefault("failure_reasons", []).append("cleanup_failure")
    atomic_write_json(run_dir / "result.json", result)
    return result


def _failure_result(number, exc):
    timeout = isinstance(exc, TimeoutError)
    return {"run": number, "passed": False, "timeout": timeout, "crash": not timeout,
            "cleanup_failure": False, "failure_reasons": ["timeout" if timeout else "crash"],
            "exception": "{}: {}".format(type(exc).__name__, exc), "checks": {}, "timings": {}}


def execute_runs(run_count, evidence_root, run_one):
    evidence_root = Path(evidence_root); evidence_root.mkdir(parents=True, exist_ok=True); results = []
    for number in range(1, run_count + 1):
        run_dir = evidence_root / "run{:03d}".format(number); run_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = run_one(number, run_dir); result.setdefault("run", number)
            result.setdefault("timeout", False); result.setdefault("crash", False); result.setdefault("cleanup_failure", False)
        except Exception as exc:
            result = _failure_result(number, exc)
        atomic_write_json(run_dir / "result.json", result); results.append(result)
    return results


def _percentile(values, fraction):
    values = sorted(values); position = (len(values) - 1) * fraction
    low, high = int(math.floor(position)), int(math.ceil(position))
    return values[low] if low == high else values[low] + (values[high] - values[low]) * (position - low)


def _timing_stats(results, key):
    values = [float(r.get("timings", {}).get(key)) for r in results if r.get("timings", {}).get(key) is not None]
    if not values: return {"mean": None, "p50": None, "p95": None, "max": None}
    return {"mean": round(statistics.mean(values), 3), "p50": round(_percentile(values, .5), 3),
            "p95": round(_percentile(values, .95), 3), "max": round(max(values), 3)}


def write_outputs(output, results):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    fields = ["run", "passed", "timeout", "crash", "cleanup_failure", "final_state", "failure_reasons", "logical_physical_consistency"]
    with (output / "runs_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for r in results:
            checks = r.get("checks", {}); consistency = all(checks.get(k, False) for k in ("logical_membership", "controller_references", "physical_order", "same_final_lane", "done"))
            writer.writerow({"run": r.get("run"), "passed": r.get("passed", False), "timeout": r.get("timeout", False),
                             "crash": r.get("crash", False), "cleanup_failure": r.get("cleanup_failure", False),
                             "final_state": "DONE" if checks.get("done") else "", "failure_reasons": ";".join(r.get("failure_reasons", [])),
                             "logical_physical_consistency": consistency})
    timing_keys = ["ready_to_trigger_s", "trigger_to_migrate_s", "trigger_to_split_s", "trigger_to_lane_change_s", "trigger_to_slowdown_s", "trigger_to_rejoin_s", "trigger_to_done_s"]
    with (output / "timing_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run"] + timing_keys); writer.writeheader()
        for r in results: writer.writerow({"run": r.get("run"), **{key: r.get("timings", {}).get(key) for key in timing_keys}})
    failure_fields = ["run", "failure_reasons", "exception", "timeout", "crash", "cleanup_failure"]
    with (output / "failures.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=failure_fields); writer.writeheader()
        for r in results:
            if not r.get("passed"):
                writer.writerow({"run": r.get("run"), "failure_reasons": ";".join(r.get("failure_reasons", [])),
                                 "exception": r.get("exception", ""), "timeout": r.get("timeout", False),
                                 "crash": r.get("crash", False), "cleanup_failure": r.get("cleanup_failure", False)})
    successes = sum(bool(r.get("passed")) for r in results)
    consistency = sum(all(r.get("checks", {}).get(k, False) for k in ("logical_membership", "controller_references", "physical_order", "same_final_lane", "done")) for r in results)
    final_order = sum(bool(r.get("checks", {}).get("physical_order")) for r in results)
    timing = _timing_stats(results, "trigger_to_done_s")
    report = """# Leader Change Real-CARLA 50-Run Validation Report

Base: local `validation` commit `2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64` on branch `validation-carla-50runs`.

This report measures repeated E2E completion only under the fixed CARLA condition described in the design. It does not claim general reliability.

| Metric | Result |
|---|---:|
| Runs | {runs} |
| Physical Leader Change success | {successes}/{runs} |
| Final order correct | {order}/{runs} |
| Logical / physical consistency | {consistency}/{runs} |
| OpenClaw migration success | Not tested |
| Timeout | {timeouts} |
| Crash | {crashes} |
| Cleanup failure | {cleanup} |
| Mean trigger → DONE | {mean} |
| P50 | {p50} |
| P95 | {p95} |
| Max | {maxv} |

## Separate CARLA-free regression baseline

Before this work, 22 tests passed with one known gateway-health xfail. These tests are not included in the real-CARLA run count.

## Final Comparison

| Scenario | Repository | Real CARLA Runs | Main Verification |
|---|---|---:|---|
| Transfer | Truckclaw-improve | 10 | request → physical merge → logical membership |
| Split | Truckclaw_copyable | pending separate report | platoon detach → independent driving |
| Leader Change | Truckclaw_movable | {runs} | leader transition → old leader physical rejoin |
""".format(runs=len(results), successes=successes, order=final_order, consistency=consistency,
           timeouts=sum(bool(r.get("timeout")) for r in results), crashes=sum(bool(r.get("crash")) for r in results),
           cleanup=sum(bool(r.get("cleanup_failure")) for r in results), mean=timing["mean"], p50=timing["p50"], p95=timing["p95"], maxv=timing["max"])
    (output / "validation_report.md").write_text(report)


def archive_generated_outputs(output):
    generated = ["evidence", "runs_summary.csv", "timing_summary.csv", "failures.csv", "validation_report.md", "run.log", "environment.json"]
    existing = [output / name for name in generated if (output / name).exists()]
    if not existing: return
    archive = output / "archive" / time.strftime("%Y%m%d_%H%M%S"); archive.mkdir(parents=True)
    for path in existing: path.rename(archive / path.name)


def write_environment(output, smoke):
    atomic_write_json(output / "environment.json", {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "carla": "0.9.13", "python_scenario": "3.7.17",
        "python_runner": sys.version.split()[0], "map": "Town06", "dt_s": 0.01,
        "vehicle_blueprint": "vehicle.carlamotors.carlacola", "rendering": "-RenderOffScreen -quality-level=Low",
        "no_rendering_mode": False, "openclaw_migration": "Not tested", "runner_owns_carla": not smoke,
        "base_commit": "2afe22213b9a7c69f5fe11a1dc2f73920fcd1f64"})
    (output / "limitations.md").write_text(
        "# Limitations\n\n- One fixed Town06 spawn/speed/vehicle condition only.\n"
        "- OpenClaw Migration was not tested because the image, credentials, and source agent were unavailable.\n"
        "- Off-screen rendering was used; this is CARLA simulation, not a real-vehicle test.\n"
        "- CARLA-free regression counts are reported separately.\n")


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--smoke", action="store_true"); parser.add_argument("--allow-existing-carla-for-smoke", action="store_true")
    args = parser.parse_args()
    if args.runs < 1: parser.error("--runs must be positive")
    if args.allow_existing_carla_for_smoke and not args.smoke: parser.error("--allow-existing-carla-for-smoke requires --smoke")
    if args.smoke and args.runs != 1: parser.error("smoke mode requires --runs 1")
    archive_generated_outputs(OUTPUT); write_environment(OUTPUT, args.allow_existing_carla_for_smoke)
    evidence = OUTPUT / "evidence"; log_path = OUTPUT / "run.log"

    def one(number, run_dir):
        started = time.time(); result = run_real_once(number, run_dir, use_existing_carla=args.allow_existing_carla_for_smoke)
        with log_path.open("a") as log:
            log.write("run={:03d} passed={} wall_s={:.2f} failures={}\n".format(number, result.get("passed"), time.time() - started, ";".join(result.get("failure_reasons", []))))
        return result
    results = execute_runs(args.runs, evidence, one); write_outputs(OUTPUT, results)
    return 0 if all(r.get("passed") for r in results) else 1


if __name__ == "__main__": raise SystemExit(main())
