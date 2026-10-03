"""Opt-in evidence recorder used by the production Leader Change scenario."""

from __future__ import annotations

import math
import time
from copy import deepcopy
from pathlib import Path

try:
    from .leader_validation import atomic_write_json, evaluate_leader
except ImportError:  # imported from the tools directory by the Python 3.7 scenario
    from leader_validation import atomic_write_json, evaluate_leader


class ScenarioRecorder:
    def __init__(self, evidence_dir, snapshot_fn, ready_s=5.0, settle_s=2.0):
        self.evidence_dir = Path(evidence_dir)
        self.snapshot_fn = snapshot_fn
        self.ready_s = float(ready_s)
        self.settle_s = float(settle_s)
        self.events = []
        self.collisions = []
        self.trigger_count = 0
        self.trigger_monotonic = None
        self.ready_monotonic = None
        self.current_sim_time = 0.0
        self.done_sim_time = None
        self.initial_snapshot = None
        self.finished = False
        self._ready = False
        self._sensors = []

    def capture_initial(self):
        self.initial_snapshot = deepcopy(self.snapshot_fn())
        atomic_write_json(self.evidence_dir / "initial_snapshot.json", self.initial_snapshot)

    def maybe_ready(self, sim_time):
        self.current_sim_time = float(sim_time)
        if not self._ready and self.current_sim_time >= self.ready_s:
            self._ready = True
            self.ready_monotonic = time.monotonic()
            atomic_write_json(self.evidence_dir / "ready.json", {
                "ready": True,
                "simulation_time_s": self.current_sim_time,
                "snapshot": deepcopy(self.snapshot_fn()),
            })
        return self._ready

    def record(self, event, **data):
        snapshot = deepcopy(self.snapshot_fn())
        now = time.monotonic()
        if event == "trigger":
            self.trigger_count += 1
            if self.trigger_monotonic is None:
                self.trigger_monotonic = now
            atomic_write_json(self.evidence_dir / "trigger_snapshot.json", snapshot)
        entry = {
            "event": event,
            "wall_monotonic_s": now,
            "simulation_time_s": self.current_sim_time,
            "since_trigger_s": round(now - self.trigger_monotonic, 6) if self.trigger_monotonic is not None else None,
        }
        for key in ("scenario_state", "main_members", "detached_members", "leader"):
            if key in snapshot:
                entry[key] = snapshot[key]
        entry.update(data)
        self.events.append(entry)
        atomic_write_json(self.evidence_dir / "transition_events.json", self.events)
        if event == "done":
            self.done_sim_time = self.current_sim_time

    def record_collision(self, actor, other_actor_id, impulse):
        self.collisions.append({
            "actor": actor,
            "other_actor_id": int(other_actor_id),
            "impulse": round(float(impulse), 6),
            "simulation_time_s": self.current_sim_time,
        })

    def attach_collision_sensors(self, world, vehicles, carla_module):
        blueprint = world.get_blueprint_library().find("sensor.other.collision")
        for name, vehicle in vehicles.items():
            sensor = world.spawn_actor(blueprint, carla_module.Transform(), attach_to=vehicle._carla_vehicle)

            def callback(event, truck=name):
                impulse = event.normal_impulse
                magnitude = math.sqrt(impulse.x ** 2 + impulse.y ** 2 + impulse.z ** 2)
                self.record_collision(truck, event.other_actor.id, magnitude)

            sensor.listen(callback)
            self._sensors.append(sensor)

    @staticmethod
    def _physical_order(final):
        actors = final.get("actors", {})
        leader = actors.get("truck1", {})
        location = leader.get("location", {})
        yaw = math.radians(float(leader.get("rotation", {}).get("yaw", 0.0)))
        forward = (math.cos(yaw), math.sin(yaw))

        def projection(name):
            other = actors.get(name, {}).get("location", {})
            return ((float(other.get("x", 0.0)) - float(location.get("x", 0.0))) * forward[0]
                    + (float(other.get("y", 0.0)) - float(location.get("y", 0.0))) * forward[1])

        return sorted(("truck0", "truck1", "truck2"), key=projection, reverse=True)

    def poll(self, sim_time):
        self.current_sim_time = float(sim_time)
        if self.finished or self.done_sim_time is None or self.current_sim_time - self.done_sim_time < self.settle_s:
            return self.finished
        final = deepcopy(self.snapshot_fn())
        final["collisions"] = list(self.collisions)
        final["trigger_count"] = self.trigger_count
        final.setdefault("physical_order", self._physical_order(final))
        atomic_write_json(self.evidence_dir / "final_snapshot.json", final)
        result = evaluate_leader(self.initial_snapshot, self.events, final)
        if self.ready_monotonic is not None and self.trigger_monotonic is not None:
            result["timings"]["ready_to_trigger_s"] = round(self.trigger_monotonic - self.ready_monotonic, 6)
        atomic_write_json(self.evidence_dir / "result.json", result)
        self.finished = True
        return True

    def abort(self, reason):
        if self.finished:
            return
        final = deepcopy(self.snapshot_fn())
        final["collisions"] = list(self.collisions)
        final["trigger_count"] = self.trigger_count
        final.setdefault("physical_order", self._physical_order(final))
        atomic_write_json(self.evidence_dir / "final_snapshot.json", final)
        result = evaluate_leader(self.initial_snapshot or {}, self.events, final)
        result["abort_reason"] = reason
        atomic_write_json(self.evidence_dir / "result.json", result)
        self.finished = True

    def close(self):
        for sensor in self._sensors:
            try:
                sensor.stop()
            except Exception:
                pass
            try:
                sensor.destroy()
            except Exception:
                pass
        self._sensors = []
