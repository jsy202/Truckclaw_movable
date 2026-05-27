"""Unit tests for small pure helpers in openclaw_migration/replicator.py (existing behavior)."""

from openclaw_migration import replicator


def test_gateway_port_maps_truck_index_to_port():
    assert replicator._default_gateway_port("truck0") == 18789
    assert replicator._default_gateway_port("truck1") == 18790
    assert replicator._default_gateway_port("truck2") == 18791


def test_gateway_port_falls_back_to_env_for_non_truck_id(monkeypatch):
    monkeypatch.setenv("OPENCLAW_GATEWAY_PORT", "19999")
    assert replicator._default_gateway_port("leader") == 19999


def test_strip_ansi_removes_color_codes():
    assert replicator._strip_ansi(replicator._c("green", "ok")) == "ok"


def test_migrator_default_container_names():
    m = replicator.LeaderMigrator(old_truck_id="truck0", new_truck_id="truck1")
    assert (m.old_container, m.new_container, m.gateway_port) == ("openclaw-truck0", "openclaw-truck1", 18790)
