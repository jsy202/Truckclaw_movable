"""Leader rotation: bridge /leader_rotation contract and scenario coordinator state machine.

Bridge side runs the real Handler in-process. The scenario coordinator is
imported with CARLA stubbed (tests/fakes/carla_stub.py) and driven with a real
LeaderMigrator whose docker calls go to FakeDocker.
"""

import time

import pytest

import platoon_bridge_server as bridge
from fakes.carla_stub import load_scenario_module

START = {"old_leader": "truck0", "new_leader": "truck1", "status": "started"}
COMPLETE = {"old_leader": "truck0", "new_leader": "truck1", "status": "complete"}
ORIGINAL = [("platoon_a_truck0", "leader"), ("platoon_a_truck1", "follower"), ("platoon_a_truck2", "follower")]
ROTATED = [("platoon_a_truck1", "leader"), ("platoon_a_truck2", "follower"), ("platoon_a_truck0", "follower")]


# ── bridge: happy path ───────────────────────────────────────────────────────

def test_rotation_start_and_complete_end_with_truck1_leading(client, rotation_receiver):
    assert client.post("/leader_rotation", START)[0] == 200
    assert rotation_receiver.count() == 1
    assert rotation_receiver.triggers[0] == {"old_leader": "truck0", "new_leader": "truck1"}
    status, body = client.post("/leader_rotation", COMPLETE)
    assert status == 200 and body["status"] == "complete"
    assert client.members() == ROTATED


# ── bridge: defects ──────────────────────────────────────────────────────────

def test_leader_not_promoted_before_physical_rotation_completes(client, rotation_receiver):
    # REQ: logical leader changes only after the physical rotation is reported complete.
    client.post("/leader_rotation", START)
    assert client.members() == ORIGINAL


def test_failed_rotation_keeps_original_leader(client, rotation_receiver):
    # REQ: a rotation that fails must not leave the bridge with a promoted leader
    # while truck0 still physically leads.
    client.post("/leader_rotation", START)
    client.post("/leader_rotation", {**START, "status": "failed"})
    assert client.members() == ORIGINAL


@pytest.mark.xfail(strict=True, reason="DEF-M03: a second 'started' sends a second CARLA :18803 trigger")
def test_duplicate_rotation_start_does_not_retrigger_carla(client, rotation_receiver):
    # REQ: one rotation = one CARLA trigger; a repeated 'started' while in progress is idempotent.
    client.post("/leader_rotation", START)
    status, _ = client.post("/leader_rotation", START)
    assert status == 200
    assert rotation_receiver.count() == 1


@pytest.mark.xfail(strict=True, reason="DEF-M04: 'complete' is accepted with 200 when no rotation was started")
def test_rotation_complete_requires_started(client, rotation_receiver):
    # REQ: 'complete' is only valid for a rotation that was started.
    status, _ = client.post("/leader_rotation", COMPLETE)
    assert status == 409
    assert client.members() == ORIGINAL


@pytest.mark.xfail(strict=True, reason="DEF-M05: any status string (e.g. 'banana') is accepted with 200")
def test_rejects_unknown_rotation_status(client, rotation_receiver):
    # REQ: status must be one of started / complete / failed.
    status, _ = client.post("/leader_rotation", {**START, "status": "banana"})
    assert status == 400


@pytest.mark.xfail(strict=True, reason="DEF-M06: unknown old_leader is accepted and CARLA is triggered")
def test_rejects_unknown_leader_identity(client, rotation_receiver):
    # REQ: old_leader must be the current leader of platoon_a and new_leader its next member.
    status, _ = client.post("/leader_rotation", {"old_leader": "truck9", "new_leader": "truck1", "status": "started"})
    assert status in (400, 404)
    assert rotation_receiver.count() == 0


# ── scenario coordinator (CARLA stubbed) ─────────────────────────────────────

@pytest.fixture
def scenario(monkeypatch):
    mod = load_scenario_module(monkeypatch)
    posts = []
    monkeypatch.setattr(mod, "_bridge_post", lambda path, body=None: posts.append((path, body)) or {"ok": True})
    mod._test_posts = posts
    return mod


def _coordinator(scenario, migrator):
    from unittest.mock import MagicMock
    coord = scenario.LeaderRotationCoordinator(MagicMock(), MagicMock(), MagicMock(), migrator=migrator)
    coord.trigger()
    return coord


def _pump(coord, seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if coord.state.name in ("GAP",) and until == "GAP":
            return
        if coord.state.name == "MIGRATE":
            coord.update(0)
        elif coord.state.name == "CRUISE" and coord.triggered:
            coord.update(0)
        else:
            return
        time.sleep(0.01)


def _migrator(replicator, migrator_dirs):
    return replicator.LeaderMigrator(
        old_truck_id="truck0", new_truck_id="truck1",
        old_openclaw_data_dir=migrator_dirs["old_data"],
        new_openclaw_data_dir=migrator_dirs["new_data"],
        new_agent_dir=migrator_dirs["new_agent"],
    )


def test_coordinator_advances_to_gap_after_successful_migration(scenario, replicator, migrator_dirs, fake_docker):
    coord = _coordinator(scenario, _migrator(replicator, migrator_dirs))
    _pump(coord, 3.0, until="GAP")
    assert coord.state.name == "GAP"
    assert ("/leader_rotation", START) in scenario._test_posts


@pytest.mark.xfail(strict=True, reason="DEF-M07: failed migration leaves the coordinator in MIGRATE forever (wait() is False for both 'running' and 'failed')")
def test_coordinator_leaves_migrate_when_migration_fails(scenario, replicator, migrator_dirs, fake_docker):
    # REQ: when agent migration fails, no physical rotation may start, and the
    # coordinator must not wait in MIGRATE forever; the failure is reported to the bridge.
    fake_docker.fail("run", stderr="port already in use")
    coord = _coordinator(scenario, _migrator(replicator, migrator_dirs))
    _pump(coord, 2.0)
    assert coord.state.name == "CRUISE"
    assert ("/leader_rotation", {**START, "status": "failed"}) in scenario._test_posts


@pytest.mark.xfail(strict=True, reason="DEF-M08: no MIGRATE timeout; a hung docker call blocks the rotation forever")
def test_coordinator_times_out_hung_migration(scenario, replicator, migrator_dirs, fake_docker, monkeypatch):
    # REQ: a migration that never finishes must not block the rotation forever.
    # MIGRATE_TIMEOUT_S is the proposed knob (default = LeaderMigrator.wait's existing 120 s).
    monkeypatch.setattr(scenario, "MIGRATE_TIMEOUT_S", 0.3, raising=False)
    fake_docker.hang("save", seconds=3.0)
    coord = _coordinator(scenario, _migrator(replicator, migrator_dirs))
    _pump(coord, 1.5)
    assert coord.state.name == "CRUISE"
    assert ("/leader_rotation", {**START, "status": "failed"}) in scenario._test_posts
