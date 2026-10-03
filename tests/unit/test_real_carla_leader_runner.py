import csv
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from validation.real_carla_50runs.tools.run_leader_change_validation import (
    ensure_port_free,
    execute_runs,
    stop_process,
    write_outputs,
)


def test_foreign_listener_is_refused_before_server_start():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        with pytest.raises(RuntimeError, match="already occupied"):
            ensure_port_free("127.0.0.1", listener.getsockname()[1])
    finally:
        listener.close()


def test_owned_process_group_is_stopped():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    stop_process(process, timeout=1.0)
    assert process.poll() is not None


def test_owned_child_is_killed_after_wrapper_exits_on_term():
    child = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
    process = subprocess.Popen(["/bin/sh", "-c", "{} -c '{}' & wait".format(sys.executable, child)], start_new_session=True)
    time.sleep(0.1)
    stop_process(process, timeout=0.2)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_failure_is_recorded_and_later_runs_continue(tmp_path):
    called = []

    def run_one(number, run_dir):
        called.append(number)
        if number == 2:
            raise RuntimeError("merge exploded")
        return {"passed": True, "timings": {"trigger_to_done_s": float(number)}}

    results = execute_runs(3, tmp_path / "evidence", run_one)
    assert called == [1, 2, 3]
    assert [item["passed"] for item in results] == [True, False, True]
    failed = json.loads((tmp_path / "evidence" / "run002" / "result.json").read_text())
    assert failed["crash"] is True and "merge exploded" in failed["exception"]


def test_timeout_is_distinct_from_crash(tmp_path):
    def run_one(number, run_dir):
        raise TimeoutError("fixed 300 second timeout")

    result = execute_runs(1, tmp_path / "evidence", run_one)[0]
    assert result["timeout"] is True and result["crash"] is False and result["passed"] is False


def test_outputs_have_one_row_per_run_and_preserve_failure(tmp_path):
    results = []
    for number in range(1, 51):
        results.append({
            "run": number,
            "passed": number != 23,
            "timeout": False,
            "crash": False,
            "cleanup_failure": False,
            "failure_reasons": [] if number != 23 else ["physical_order"],
            "checks": {"logical_membership": True, "physical_order": number != 23},
            "timings": {"trigger_to_done_s": float(number)},
        })
    write_outputs(tmp_path, results)
    with (tmp_path / "runs_summary.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    with (tmp_path / "failures.csv").open(newline="") as handle:
        failures = list(csv.DictReader(handle))
    assert len(rows) == 50 and rows[22]["passed"] == "False"
    assert len(failures) == 1 and failures[0]["run"] == "23"
    assert (tmp_path / "timing_summary.csv").is_file()
    assert "49/50" in (tmp_path / "validation_report.md").read_text()
