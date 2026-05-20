"""Agent migration (OpenClaw session move truck0 -> truck1) with Docker replaced by FakeDocker."""

import hashlib
import json
import tarfile

import pytest


def _migrator(replicator, d, **kw):
    return replicator.LeaderMigrator(
        old_truck_id="truck0", new_truck_id="truck1",
        old_openclaw_data_dir=d["old_data"], new_openclaw_data_dir=d["new_data"],
        new_agent_dir=d["new_agent"], **kw,
    )


def test_successful_migration_restores_session_and_starts_new_container(replicator, migrator_dirs, fake_docker):
    m = _migrator(replicator, migrator_dirs)
    m.migrate(blocking=True)
    assert m.wait(timeout=0) is True
    assert fake_docker.subcommands() == ["save", "load", "rm", "run"]
    run = fake_docker.calls[-1]
    assert run[run.index("--name") + 1] == "openclaw-truck1"
    assert "OPENCLAW_GATEWAY_PORT=18790" in run          # truck1 -> 18789 + 1
    new = migrator_dirs["new_data"]
    assert (new / "openclaw.json").read_text() == '{"agent": "truck0"}'
    assert (new / "notes.md").exists() and (new / "SOUL.md").read_text() == "truck1 soul"
    assert not (new / "cache.bin").exists()               # binaries are not carried


def test_session_tar_contains_agent_config_state_and_token_meta(replicator, migrator_dirs, fake_docker):
    assert replicator.create_session_tar("openclaw-truck0", migrator_dirs["old_data"], "truck1", migrator_dirs["new_agent"])
    with tarfile.open(replicator.SESSION_TAR_TX_PATH, "r:") as tar:
        names = set(tar.getnames())
        meta = json.loads(tar.extractfile("./migration_meta.json").read())
    assert {"./agent_config/SOUL.md", "./openclaw_data/openclaw.json", "./openclaw_data/notes.md",
            "./migration_meta.json", "./.env"} <= names
    assert "./openclaw_data/cache.bin" not in names
    assert meta["from_container"] == "openclaw-truck0" and meta["new_truck_id"] == "truck1"


def test_v2v_transfer_is_byte_identical(replicator, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(bytes(range(256)) * 1000)  # spans several 64 KiB chunks
    dst = tmp_path / "rx" / "dst.bin"
    replicator._v2v_transfer(src, dst, "test")
    assert hashlib.sha256(dst.read_bytes()).digest() == hashlib.sha256(src.read_bytes()).digest()


@pytest.mark.parametrize("failing", ["save", "run"])
def test_docker_failure_marks_migration_failed(replicator, migrator_dirs, fake_docker, failing):
    fake_docker.fail(failing)
    m = _migrator(replicator, migrator_dirs)
    m.migrate(blocking=True)
    assert m.wait(timeout=0) is False
    assert m._done_event.is_set()


def test_cleanup_old_removes_only_old_leader_container(replicator, fake_docker):
    replicator.LeaderMigrator().cleanup_old()
    assert fake_docker.calls == [["docker", "rm", "-f", "openclaw-truck0"]]


def test_corrupted_session_artifact_fails_migration(replicator, migrator_dirs, fake_docker, monkeypatch):
    # REQ: a session artifact damaged in V2V transfer must not produce a "successful" migration.
    real = replicator._v2v_transfer

    def truncating_transfer(src, dst, label):
        real(src, dst, label)
        if label == "openclaw_session.tar":
            data = dst.read_bytes()
            dst.write_bytes(data[: len(data) // 3])

    monkeypatch.setattr(replicator, "_v2v_transfer", truncating_transfer)
    m = _migrator(replicator, migrator_dirs)
    m.migrate(blocking=True)
    assert m.wait(timeout=0) is False
    assert "run" not in fake_docker.subcommands()


@pytest.mark.xfail(strict=True, reason="DEF-M09: success = `docker run -d` returncode 0; gateway health is never checked")
def test_migration_success_requires_new_agent_ready(replicator, migrator_dirs, fake_docker):
    # REQ: "migration succeeded" should mean the new agent's gateway is healthy,
    # not only that `docker run -d` returned 0 (container runs `... & tail -f /dev/null`,
    # so it stays up even if the gateway exits).
    m = _migrator(replicator, migrator_dirs)
    m.migrate(blocking=True)
    assert m.wait(timeout=0) is True
    assert any(c[1] in ("inspect", "exec") for c in fake_docker.calls) or getattr(m, "readiness_checked", False)
