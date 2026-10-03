"""Pure evidence serialization and verdict logic for Leader Change validation."""

from __future__ import annotations

import json
import math
import os
import statistics
from pathlib import Path


TRUCKS = ("truck0", "truck1", "truck2")


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(str(temporary), str(path))


def load_json(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("missing JSON: {}".format(path))
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("invalid JSON: {}: {}".format(path, exc))


def _xyz(value):
    return {"x": round(float(value.x), 4), "y": round(float(value.y), 4), "z": round(float(value.z), 4)}


def actor_snapshot(vehicle, cmap):
    actor = vehicle._carla_vehicle
    location = actor.get_location()
    transform = actor.get_transform()
    velocity = actor.get_velocity()
    waypoint = cmap.get_waypoint(location, project_to_road=True)
    speed = 3.6 * math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
    return {
        "actor_id": int(actor.id),
        "type_id": actor.type_id,
        "alive": bool(actor.is_alive),
        "location": _xyz(location),
        "rotation": {
            "pitch": round(float(transform.rotation.pitch), 4),
            "yaw": round(float(transform.rotation.yaw), 4),
            "roll": round(float(transform.rotation.roll), 4),
        },
        "velocity": _xyz(velocity),
        "speed_kmh": round(speed, 4),
        "road_id": int(waypoint.road_id) if waypoint else None,
        "lane_id": int(waypoint.lane_id) if waypoint else None,
    }


def platoon_snapshot(coord, sim, names):
    def name(vehicle):
        return names[id(vehicle)]

    main = list(coord.platoon)
    detached = []
    if getattr(coord, "_v_platoon", None) is not None:
        detached = [v for v in coord._v_platoon if v not in main]
    all_vehicles = {name(v): v for v in list(main) + detached}
    controllers = {}
    for truck, vehicle in all_vehicles.items():
        controller = getattr(vehicle, "controller", None)
        reference = getattr(controller, "platoon", None)
        controllers[truck] = {
            "type": type(controller).__name__ if controller is not None else None,
            "platoon": "main" if reference is coord.platoon or vehicle in main else "detached",
        }
    return {
        "scenario_state": coord.state.name,
        "main_members": [name(v) for v in main],
        "detached_members": [name(v) for v in detached],
        "leader": name(main[0]) if main else None,
        "actors": {truck: actor_snapshot(vehicle, coord.cmap) for truck, vehicle in all_vehicles.items()},
        "controllers": controllers,
        "simulation_platoon_count": len(sim.platoons),
    }


def _first(events, event):
    return next((item for item in events if item.get("event") == event), None)


def evaluate_leader(initial, events, final):
    actors_i = initial.get("actors", {})
    actors_f = final.get("actors", {})
    controllers_i = initial.get("controllers", {})
    controllers_f = final.get("controllers", {})
    trigger = _first(events, "trigger")
    transition = _first(events, "logical_transition")
    gap = _first(events, "gap_ready")
    lane = _first(events, "lane_change_complete")
    slowdown = _first(events, "slowdown_complete")
    rejoin = _first(events, "rejoin")
    done = _first(events, "done")
    # CARLA road_id identifies OpenDRIVE road segments, not a whole continuous
    # lane corridor. A 35 m platoon can straddle a segment boundary while all
    # actors occupy lane -3, as observed in the pre-fix real-CARLA smoke run.
    lanes = {actors_f.get(n, {}).get("lane_id") for n in TRUCKS}
    checks = {
        "initial_state": initial.get("scenario_state") == "CRUISE"
        and initial.get("main_members") == list(TRUCKS)
        and initial.get("leader") == "truck0"
        and set(actors_i) == set(TRUCKS)
        and all(actors_i[n].get("alive") for n in TRUCKS)
        and controllers_i.get("truck0", {}).get("type") == "LeadNavigator"
        and all(controllers_i.get(n, {}).get("type") == "FollowerController" for n in ("truck1", "truck2")),
        "single_trigger": bool(trigger and trigger.get("accepted"))
        and final.get("trigger_count") == 1
        and sum(1 for e in events if e.get("event") == "trigger" and e.get("accepted")) == 1,
        "logical_transition": bool(transition)
        and transition.get("main_members") == ["truck1", "truck2"]
        and transition.get("detached_members") == ["truck0"]
        and transition.get("leader") == "truck1",
        "gap_ready": bool(gap) and gap.get("gap_m", -1) >= 12.0 and gap.get("stable_ticks", -1) >= 10,
        "lane_departure": bool(lane) and lane.get("lateral_m", -1) > 3.0,
        "rear_position": bool(slowdown) and slowdown.get("behind_tail_m", -1) >= 25.0,
        "no_forced_transition": bool(lane and slowdown) and not lane.get("forced", False) and not slowdown.get("forced", False),
        "rejoin": bool(rejoin) and rejoin.get("lateral_m", float("inf")) < 0.8,
        "logical_membership": final.get("main_members") == ["truck1", "truck2", "truck0"]
        and final.get("leader") == "truck1"
        and final.get("detached_members", []) == [],
        "controller_references": controllers_f.get("truck1", {}).get("type") == "LeadNavigator"
        and controllers_f.get("truck0", {}).get("type") == "FollowerController"
        and controllers_f.get("truck2", {}).get("type") == "FollowerController"
        and all(controllers_f.get(n, {}).get("platoon") == "main" for n in TRUCKS),
        "actors_alive": set(actors_f) == set(TRUCKS) and all(actors_f[n].get("alive") for n in TRUCKS),
        "vehicles_moving": all(actors_f.get(n, {}).get("speed_kmh", 0) > 0 for n in TRUCKS),
        "same_final_lane": len(lanes) == 1 and None not in lanes,
        "physical_order": final.get("physical_order") == ["truck1", "truck2", "truck0"],
        "collision_free": final.get("collisions") == [],
        "done": final.get("scenario_state") == "DONE" and done is not None,
    }
    reasons = [name for name, passed in checks.items() if not passed]
    timings = {}
    for name, event_name in (
        ("trigger_to_migrate_s", "migrate"),
        ("trigger_to_split_s", "logical_transition"),
        ("trigger_to_lane_change_s", "lane_change_complete"),
        ("trigger_to_slowdown_s", "slowdown_complete"),
        ("trigger_to_rejoin_s", "rejoin"),
        ("trigger_to_done_s", "done"),
    ):
        event = _first(events, event_name)
        timings[name] = event.get("since_trigger_s") if event else None
    return {"passed": not reasons, "checks": checks, "failure_reasons": reasons, "timings": timings}


def _percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize_results(results):
    values = [r.get("timings", {}).get("trigger_to_done_s") for r in results]
    values = [float(v) for v in values if v is not None]
    timing = {
        "mean": round(statistics.mean(values), 3) if values else None,
        "p50": round(_percentile(values, 0.50), 3) if values else None,
        "p95": round(_percentile(values, 0.95), 3) if values else None,
        "max": round(max(values), 3) if values else None,
    }
    return {
        "runs": len(results),
        "successes": sum(bool(r.get("passed")) for r in results),
        "failures": sum(not bool(r.get("passed")) for r in results),
        "timeouts": sum(bool(r.get("timeout")) for r in results),
        "crashes": sum(bool(r.get("crash")) for r in results),
        "cleanup_failures": sum(bool(r.get("cleanup_failure")) for r in results),
        "timing": timing,
    }
