"""
Two-platoon truck transfer scenario  –  CARLA Town06
====================================================

  P1  Donor platoon    : 3 trucks, TM lead + CACC followers
  P2  Receiver platoon : 3 trucks, TM lead + CACC followers

Scripted flow:
  1. Two platoons spawn in the same parallel highway corridor with a large longitudinal offset.
  2. Press SPACE to start adaptive rendezvous.
  3. Once the platoons are aligned and close enough, P1's tail detaches.
  4. The detached truck opens a safe gap, changes lane, and reattaches behind P2 via CACC.
  5. Both platoons continue driving until interrupted.

Run:
  PYTHONPATH=/home/user/carla_source/PythonAPI/carla \
      python3 examples/two_platoon_truck_scenario.py
"""

import json
import os
import select
import sys
import termios
import threading
import tty
import urllib.error
import urllib.request
from collections import deque
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer

import carla
import numpy as np
from agents.navigation import controller as nav_controller

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from PlatooningSimulator import Core, PlatooningControllers


# ── configuration loading ─────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "simulation.json")

def load_sim_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

_cfg = load_sim_config()
_speeds = _cfg.get("speeds", {})
_gaps = _cfg.get("gaps", {})
_timeouts = _cfg.get("timeouts", {})
_spawns = _cfg.get("spawns", {})
_dests = _cfg.get("destinations", {})

DT = 0.01
SAMPLING_RATE = 10

PLATOON_SIZE = 3
PLATOON_SPACING_M = _gaps.get("platoon_spacing_m", 16.0)

SYNC_SPEED_KMH = _speeds.get("sync_speed_kmh", 20.0)
POST_MERGE_SPEED_KMH = _speeds.get("post_merge_speed_kmh", 50.0)
OPEN_GAP_DELTA_KMH = _speeds.get("open_gap_delta_kmh", 6.0)
LANE_CHANGE_BOOST_KMH = _speeds.get("lane_change_boost_kmh", 10.0)
CATCHUP_KMH = _speeds.get("catchup_kmh", 18.0)
MERGE_MIN_SPEED_KMH = _speeds.get("merge_min_speed_kmh", 15.0)

TARGET_GAP_M = _gaps.get("target_gap_m", 6.0)
STRAIGHT_M = 10.0
CONFIRM_TICKS = 8
FOLLOW_DIST_M = _gaps.get("follow_dist_m", 13.0)
TEMP_MERGE_FOLLOW_DIST_M = 6.0

INITIAL_LEAD_OFFSET_M = 60.0
AUTO_APPROACH_SECS = 2.0
MIN_TRIGGER_TIME_S = 0.0
MERGE_OFFSET_MIN_M = TARGET_GAP_M - 1.0
APPROACH_TARGET_OFFSET_M = -10.0
APPROACH_FAST_KMH = _speeds.get("approach_fast_kmh", 65.0)
MERGE_DISTANCE_LIMIT_M = _gaps.get("merge_distance_limit_m", 55.0)
MERGE_YAW_LIMIT_DEG = 15.0
MERGE_TIMEOUT_S = _timeouts.get("merge_timeout_s", 120.0)
LANE_CENTER_TOLERANCE_M = _gaps.get("lane_center_tolerance_m", 2.0)
PARALLEL_LOOKAHEAD_M = 800.0
LOG_MAX_STEPS = 60000
SHOW_PLOT = False

CAM_HEIGHT = 85.0
CAM_ALPHA = 0.03
CAM_EVERY = 1

# ── fixed spawn transforms (Town06) ──────────────────────────────────────────
_p1_s = _spawns.get("p1_spawn", {"x": 81.0, "y": 136.0, "z": 0.3, "pitch": 0.0, "yaw": 0.2, "roll": 0.0})
P1_SPAWN = carla.Transform(
    carla.Location(x=_p1_s["x"], y=_p1_s["y"], z=_p1_s["z"]),
    carla.Rotation(pitch=_p1_s["pitch"], yaw=_p1_s["yaw"], roll=_p1_s["roll"]),
)
_p2_s = _spawns.get("p2_spawn", {"x": 21.0, "y": 143.0, "z": 0.3, "pitch": 0.0, "yaw": 0.2, "roll": 0.0})
P2_SPAWN = carla.Transform(
    carla.Location(x=_p2_s["x"], y=_p2_s["y"], z=_p2_s["z"]),
    carla.Rotation(pitch=_p2_s["pitch"], yaw=_p2_s["yaw"], roll=_p2_s["roll"]),
)

# ── destination locations (Town06) ───────────────────────────────────────────
_da = _dests.get("dest_a", {"x": 599.1, "y": 241.2, "z": 0.0})
DEST_A = carla.Location(x=_da["x"], y=_da["y"], z=_da["z"])
_db = _dests.get("dest_b", {"x": 480.3, "y": 41.7, "z": 0.0})
DEST_B = carla.Location(x=_db["x"], y=_db["y"], z=_db["z"])

TRUCK_BLUEPRINT_PREFS = [
    "vehicle.carlamotors.european_hgv",
    "vehicle.mitsubishi.fusorosa",
    "vehicle.carlamotors.firetruck",
    "vehicle.mercedes.sprinter",
]

PLATOON_IDS = ("P1", "P2")

# ── bridge server ─────────────────────────────────────────────────────────────
BRIDGE_URL = "http://127.0.0.1:18801"
BRIDGE_POLL_TICKS = 50   # poll every 50 sampling ticks ≈ 5 s

def _transfer_matches_scenario(t):
    """Verify if the bridge transfer matches current simulation entities."""
    # Allow any vehicle that is currently a tail vehicle in P1/Platoon A
    return (
        t.get("from_platoon_id") == "platoon_a" and 
        t.get("to_platoon_id") == "platoon_b"
    )

def _bridge_get_ready_transfer(request_id=None):
    """Return a committed/merging bridge transfer matching this CARLA scenario."""
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/snapshot", timeout=2) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:
        return None, f"bridge snapshot unavailable: {exc}"

    ready = []
    for rid, t in data.get("transfers", {}).items():
        if request_id is not None and rid != request_id:
            continue
        if t.get("status") not in ("committed", "merging"):
            continue
        if not _transfer_matches_scenario(t):
            continue
        ready.append(t)

    if not ready:
        scope = f" for request_id={request_id}" if request_id else ""
        return None, f"no committed or merging transfer{scope}"
    return ready[0], None

# ── trigger server (receives POST /start_merge from OpenClaw bots) ────────────
TRIGGER_PORT = 18802
_merge_trigger_event = threading.Event()


def _send_trigger_response(handler, code, payload):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_trigger_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _start_trigger_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/start_merge":
                try:
                    body = _read_trigger_body(self)
                except json.JSONDecodeError as exc:
                    return _send_trigger_response(self, 400, {"ok": False, "error": f"invalid JSON body: {exc.msg}"})

                transfer, error = _bridge_get_ready_transfer(body.get("request_id"))
                if transfer is None:
                    return _send_trigger_response(self, 409, {"ok": False, "error": error})

                _merge_trigger_event.set()
                return _send_trigger_response(self, 200, {"ok": True, "request_id": transfer["request_id"]})
            else:
                return _send_trigger_response(self, 404, {"ok": False, "error": "not found"})

        def log_message(self, fmt, *args):
            pass  # suppress access log noise

    srv = HTTPServer(("0.0.0.0", TRIGGER_PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"[trigger] Listening for POST /start_merge on port {TRIGGER_PORT}")


def _bridge_has_active_transfer():
    """Return True if the bridge server has a transfer ready for CARLA (committed or merging)."""
    transfer, _ = _bridge_get_ready_transfer()
    return transfer is not None


def _bridge_get_committed_request_id():
    """Return the request_id of the first committed-or-merging transfer, or None."""
    transfer, _ = _bridge_get_ready_transfer()
    return transfer["request_id"] if transfer is not None else None


def _bridge_notify(request_id, event):
    """Notify bridge of a CARLA-side event: 'merging' or 'carla_complete'."""
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/transfers/{request_id}/{event}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
        print(f"[bridge] notified {event} for {request_id}")
    except Exception as e:
        print(f"[bridge] notify {event} failed: {e}")


def _bridge_update_readiness(status, merge_ready, reason, request_id=None, metrics=None, vehicle_id=None):
    payload = {
        "status": status,
        "merge_ready": bool(merge_ready),
        "reason": reason,
        "request_id": request_id,
        "vehicle_id": vehicle_id,
        "metrics": metrics or {},
    }
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/readiness",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass


def _bridge_fail(request_id, reason):
    if not request_id:
        return
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/transfers/{request_id}/failed",
            data=json.dumps({"reason": reason}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
        print(f"[bridge] notified failed for {request_id}: {reason}")
    except Exception as e:
        print(f"[bridge] notify failed failed: {e}")


def _auto_bridge_commit(tail_vehicle_id):
    """Create, accept, and commit a bridge transfer for spacebar-triggered merges."""
    try:
        def post(path, body):
            req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=2) as r: return json.loads(r.read().decode())
        
        res = post("/transfers", {"vehicle_id": tail_vehicle_id, "from_platoon_id": "platoon_a", "to_platoon_id": "platoon_b"})
        rid = res["request_id"]
        post(f"/transfers/{rid}/accept", {})
        post(f"/transfers/{rid}/commit", {})
        print(f"[test] Bridge auto-committed {rid} for {tail_vehicle_id}")
    except Exception as e:
        print(f"[test] Auto-commit failed: {e}")


def _readiness_reason(approach_started, donor_straight, receiver_straight, lat_adjacent, pair_metrics, time_s):
    if not approach_started:
        return "waiting for bridge commit/start_merge trigger"
    if not lat_adjacent:
        return "donor and receiver tails are not laterally adjacent"
    if pair_metrics["distance_m"] > MERGE_DISTANCE_LIMIT_M:
        return f"distance too large ({pair_metrics['distance_m']:.1f}m)"
    if pair_metrics["yaw_delta_deg"] > MERGE_YAW_LIMIT_DEG:
        return f"yaw mismatch too large ({pair_metrics['yaw_delta_deg']:.1f}deg)"
    if pair_metrics["offset_m"] < MERGE_OFFSET_MIN_M:
        return f"receiver gap is not open enough ({pair_metrics['offset_m']:.1f}m)"
    if time_s < MIN_TRIGGER_TIME_S:
        return "minimum trigger time has not elapsed"
    return "merge conditions satisfied"


def pick_truck_blueprint(bps):
    for name in TRUCK_BLUEPRINT_PREFS:
        found = bps.filter(name)
        if found:
            print(f"[bp] {found[0].id}")
            return found[0]
    raise RuntimeError("No truck blueprint found for the truck scenario.")


def _spawn_from_waypoint(wpt):
    t = wpt.transform
    return carla.Transform(
        carla.Location(x=t.location.x, y=t.location.y, z=t.location.z + 0.3),
        t.rotation,
    )


def _has_clear_road(wpt, clear_m):
    cur = wpt
    for _ in range(int(clear_m / 10.0)):
        nxt = cur.next(10.0)
        if not nxt or nxt[0].is_junction:
            return False
        cur = nxt[0]
    return True


def _has_history(wpt, history_m):
    return bool(wpt.previous(history_m))


def _advance_waypoint(wpt, distance_m):
    current = wpt
    remaining = float(distance_m)

    while remaining > 0.0:
        step = min(10.0, remaining)
        nxt = current.next(step)
        if not nxt:
            return None

        yaw = current.transform.rotation.yaw
        current = min(nxt, key=lambda cand: abs((cand.transform.rotation.yaw - yaw + 180.0) % 360.0 - 180.0))
        remaining -= step

    return current


def _retreat_waypoint(wpt, distance_m):
    current = wpt
    remaining = float(distance_m)

    while remaining > 0.0:
        step = min(10.0, remaining)
        prev = current.previous(step)
        if not prev:
            return None

        yaw = current.transform.rotation.yaw
        current = min(prev, key=lambda cand: abs((cand.transform.rotation.yaw - yaw + 180.0) % 360.0 - 180.0))
        remaining -= step

    return current


def signed_longitudinal_offset(reference_vehicle, other_vehicle):
    """Positive when other_vehicle is behind reference_vehicle along reference heading."""
    ref_loc = reference_vehicle._carla_vehicle.get_location()
    other_loc = other_vehicle._carla_vehicle.get_location()
    yaw = np.deg2rad(reference_vehicle._carla_vehicle.get_transform().rotation.yaw)
    fwd = np.array([np.cos(yaw), np.sin(yaw)])
    return float(np.dot(np.array([ref_loc.x - other_loc.x, ref_loc.y - other_loc.y]), fwd))


def signed_lateral_offset(reference_vehicle, other_vehicle):
    """Positive when other_vehicle is to the right of reference_vehicle."""
    ref_loc = reference_vehicle._carla_vehicle.get_location()
    other_loc = other_vehicle._carla_vehicle.get_location()
    yaw = np.deg2rad(reference_vehicle._carla_vehicle.get_transform().rotation.yaw)
    right_vec = np.array([np.sin(yaw), -np.cos(yaw)])
    return float(np.dot(np.array([other_loc.x - ref_loc.x, other_loc.y - ref_loc.y]), right_vec))


def yaw_delta_deg(vehicle_a, vehicle_b):
    yaw_a = vehicle_a._carla_vehicle.get_transform().rotation.yaw
    yaw_b = vehicle_b._carla_vehicle.get_transform().rotation.yaw
    return abs((yaw_a - yaw_b + 180.0) % 360.0 - 180.0)


def _matches_waypoint(candidate, waypoint):
    return (
        candidate is not None
        and candidate.lane_type == carla.LaneType.Driving
        and candidate.road_id == waypoint.road_id
        and candidate.lane_id == waypoint.lane_id
    )


def are_adjacent_waypoints(wpt_a, wpt_b):
    if _matches_waypoint(wpt_a.get_left_lane(), wpt_b) or _matches_waypoint(wpt_a.get_right_lane(), wpt_b):
        return True
    if _matches_waypoint(wpt_b.get_left_lane(), wpt_a) or _matches_waypoint(wpt_b.get_right_lane(), wpt_a):
        return True
    return False


def _same_direction_adjacent_lane_ids(wpt_a, wpt_b):
    if wpt_a.lane_id == 0 or wpt_b.lane_id == 0:
        return False
    if np.sign(wpt_a.lane_id) != np.sign(wpt_b.lane_id):
        return False
    return abs(abs(wpt_a.lane_id) - abs(wpt_b.lane_id)) == 1


def are_adjacent_lanes(carla_map, reference_vehicle, other_vehicle):
    ref_wpt = carla_map.get_waypoint(reference_vehicle._carla_vehicle.get_location())
    other_wpt = carla_map.get_waypoint(other_vehicle._carla_vehicle.get_location())

    if _matches_waypoint(ref_wpt.get_left_lane(), other_wpt) or _matches_waypoint(ref_wpt.get_right_lane(), other_wpt):
        return True
    if _matches_waypoint(other_wpt.get_left_lane(), ref_wpt) or _matches_waypoint(other_wpt.get_right_lane(), ref_wpt):
        return True
    return False


def _parallel_corridor_ok(receiver_aligned_wpt, donor_wpt, sample_ahead_m=200.0, step_m=20.0):
    # Check start point with physical distance only – lane/road IDs can be
    # renumbered at segment boundaries even on a straight highway, so
    # id-based checks produce false negatives when called with advanced waypoints.
    if not _lane_center_spacing_ok(receiver_aligned_wpt, donor_wpt):
        return False
    if _yaw_delta_waypoints_deg(receiver_aligned_wpt, donor_wpt) > MERGE_YAW_LIMIT_DEG:
        return False

    base = receiver_aligned_wpt
    adj = donor_wpt
    for _ in range(int(sample_ahead_m / step_m)):
        next_base = _advance_waypoint(base, step_m)
        next_adj = _advance_waypoint(adj, step_m)
        if next_base is None or next_adj is None:
            return False
        if next_base.is_junction or next_adj.is_junction:
            return False
        # Use physical distance check in the loop instead of lane-id matching.
        # Lane IDs can be renumbered at road-segment boundaries even on a straight
        # highway, so id-based checks produce false negatives there.
        # _lane_center_spacing_ok still catches real lane-merges (distance → 0)
        # and divergences (distance grows beyond tolerance).
        if not _lane_center_spacing_ok(next_base, next_adj):
            return False
        yaw_delta = abs((next_base.transform.rotation.yaw - next_adj.transform.rotation.yaw + 180.0) % 360.0 - 180.0)
        if yaw_delta > MERGE_YAW_LIMIT_DEG:
            return False
        base = next_base
        adj = next_adj
    return True


def _yaw_delta_waypoints_deg(wpt_a, wpt_b):
    return abs((wpt_a.transform.rotation.yaw - wpt_b.transform.rotation.yaw + 180.0) % 360.0 - 180.0)


def _lane_center_distance_xy(wpt_a, wpt_b):
    loc_a = wpt_a.transform.location
    loc_b = wpt_b.transform.location
    return float(np.hypot(loc_a.x - loc_b.x, loc_a.y - loc_b.y))


def _lane_center_spacing_ok(wpt_a, wpt_b):
    expected = 0.5 * (wpt_a.lane_width + wpt_b.lane_width)
    actual = _lane_center_distance_xy(wpt_a, wpt_b)
    return abs(actual - expected) <= LANE_CENTER_TOLERANCE_M


def _vehicles_laterally_adjacent(vehicle_a, vehicle_b, min_lat_m=2.0, max_lat_m=8.0):
    """True when two vehicles are laterally within [min_lat_m, max_lat_m].

    Covers both single-lane (≈3.5 m) and two-lane (≈7 m) separations so the
    scenario works for a 2-lane change (P1 lane=-3, P2 lane=-5).
    Uses vehicle_a's heading to project the inter-vehicle vector onto the
    lateral axis; avoids road/lane-ID matching which breaks at CARLA
    segment boundaries.
    """
    loc_a = vehicle_a._carla_vehicle.get_location()
    loc_b = vehicle_b._carla_vehicle.get_location()
    yaw = np.deg2rad(vehicle_a._carla_vehicle.get_transform().rotation.yaw)
    right = np.array([np.sin(yaw), -np.cos(yaw)])
    diff = np.array([loc_b.x - loc_a.x, loc_b.y - loc_a.y])
    return min_lat_m <= abs(float(np.dot(diff, right))) <= max_lat_m


def _align_reference_waypoint(reference_wpt, longitudinal_offset_m):
    if abs(longitudinal_offset_m) < 1e-3:
        return reference_wpt
    if longitudinal_offset_m > 0.0:
        return _retreat_waypoint(reference_wpt, longitudinal_offset_m)
    return _advance_waypoint(reference_wpt, -longitudinal_offset_m)


def compute_pair_metrics(carla_map, receiver_vehicle, donor_vehicle):
    receiver_wpt = carla_map.get_waypoint(receiver_vehicle._carla_vehicle.get_location())
    donor_wpt = carla_map.get_waypoint(donor_vehicle._carla_vehicle.get_location())
    longitudinal = signed_longitudinal_offset(receiver_vehicle, donor_vehicle)
    lateral = signed_lateral_offset(receiver_vehicle, donor_vehicle)
    distance = receiver_vehicle.distance_to(donor_vehicle)
    yaw_delta = yaw_delta_deg(receiver_vehicle, donor_vehicle)
    aligned_receiver_wpt = _align_reference_waypoint(receiver_wpt, longitudinal)

    if aligned_receiver_wpt is None:
        adjacent = False
        lane_center_dist = np.nan
        corridor_yaw_delta = np.nan
        physical_adjacent = False
    else:
        adjacent = are_adjacent_waypoints(aligned_receiver_wpt, donor_wpt)
        lane_center_dist = _lane_center_distance_xy(aligned_receiver_wpt, donor_wpt)
        corridor_yaw_delta = _yaw_delta_waypoints_deg(aligned_receiver_wpt, donor_wpt)
        physical_adjacent = (
            adjacent
            and _same_direction_adjacent_lane_ids(aligned_receiver_wpt, donor_wpt)
            and _lane_center_spacing_ok(aligned_receiver_wpt, donor_wpt)
        )

    return {
        "offset_m": longitudinal,
        "lateral_m": lateral,
        "distance_m": distance,
        "yaw_delta_deg": yaw_delta,
        "adjacent": adjacent,
        "lane_center_dist_m": lane_center_dist,
        "corridor_yaw_delta_deg": corridor_yaw_delta,
        "physical_adjacent": physical_adjacent,
    }


def find_parallel_platoon_spawns(carla_map, spawn_points, clear_m=400.0, history_m=50.0, lead_offset_m=INITIAL_LEAD_OFFSET_M):
    """Find one stable local corridor, then place both platoons directly on that corridor."""
    offset_candidates = []
    for candidate in (lead_offset_m, 50.0, 40.0, 30.0):
        if candidate not in offset_candidates:
            offset_candidates.append(candidate)

    rear_clearance_m = PLATOON_SPACING_M * (PLATOON_SIZE - 1) + 20.0

    for idx, sp in enumerate(spawn_points):
        base = carla_map.get_waypoint(sp.location)
        if base.lane_type != carla.LaneType.Driving or base.is_junction:
            continue
        if not _has_clear_road(base, clear_m) or not _has_history(base, rear_clearance_m):
            continue

        for adj in (base.get_right_lane(), base.get_left_lane()):
            if adj is None or adj.lane_type != carla.LaneType.Driving or adj.is_junction:
                continue
            if not _has_clear_road(adj, clear_m) or not _has_history(adj, rear_clearance_m):
                continue
            if not _same_direction_adjacent_lane_ids(base, adj):
                continue
            if not _lane_center_spacing_ok(base, adj):
                continue
            if _yaw_delta_waypoints_deg(base, adj) > MERGE_YAW_LIMIT_DEG:
                continue
            if not _parallel_corridor_ok(base, adj, sample_ahead_m=min(clear_m, STRAIGHT_M + 120.0)):
                continue

            for offset_m in offset_candidates:
                donor_lead = _advance_waypoint(adj, offset_m)
                receiver_match = _advance_waypoint(base, offset_m)
                if donor_lead is None or receiver_match is None:
                    continue
                if donor_lead.is_junction or receiver_match.is_junction:
                    continue
                # Use physical checks only – road/lane IDs often change at segment
                # boundaries after advancing the waypoint by offset_m.
                if not _lane_center_spacing_ok(receiver_match, donor_lead):
                    continue
                if _yaw_delta_waypoints_deg(receiver_match, donor_lead) > MERGE_YAW_LIMIT_DEG:
                    continue
                if not _parallel_corridor_ok(receiver_match, donor_lead, sample_ahead_m=STRAIGHT_M + 40.0):
                    continue
                if not _has_history(donor_lead, rear_clearance_m):
                    continue

                p1_sp = _spawn_from_waypoint(base)
                p2_sp = _spawn_from_waypoint(donor_lead)
                print(
                    f"[spawn] pair idx={idx}  "
                    f"P1 road={base.road_id} lane={base.lane_id}  "
                    f"P2 road={donor_lead.road_id} lane={donor_lead.lane_id}  "
                    f"offset={offset_m:.0f}m  clear={clear_m:.0f}m"
                )
                return p1_sp, p2_sp, base.lane_id, donor_lead.lane_id, offset_m

    raise RuntimeError("No stable parallel highway corridor found for two truck platoons.")



def v_ref_cacc(predecessor, ego):
    tau = 0.66
    h = getattr(ego, "h", 0.5)
    c = 2.0
    length = 5.0
    gap = ego.distance_to(predecessor)
    v_pre = predecessor.speed
    v_ego = ego.speed
    v_ref = (tau / h * (v_pre - v_ego + c * (gap - length - h * v_ego)) + v_ego) * 3.6
    # Lower bound: never brake below 60% of predecessor speed (prevents full stops
    # when gap closes briefly).  Upper bound: cap catchup to avoid overshooting.
    v_pre_kmh = v_pre * 3.6
    return float(np.clip(v_ref, max(v_pre_kmh * 0.6, 3.0), v_pre_kmh + CATCHUP_KMH * 3))


def v_ref_cacc_merge(predecessor, ego):
    """Proportional catchup controller for the merged vehicle.

    Replaces raw v_ref_cacc which becomes unstable at large gaps (e.g. 22 m
    right after lane change) because it computes 100-150 km/h, then commands
    an emergency stop when the ego overshoots the desired gap.

    This version uses a simple proportional law:
      target = v_predecessor + K * (gap - target_gap)
    capped at v_predecessor + 3*CATCHUP_KMH for very large gaps.
    Speed smoothly decreases to v_predecessor as the gap closes to FOLLOW_DIST_M.
    No sudden braking is required.
    """
    gap = ego.distance_to(predecessor)
    v_pre_kmh = predecessor.speed * 3.6

    if gap <= FOLLOW_DIST_M:
        return v_pre_kmh

    gap_surplus = gap - FOLLOW_DIST_M
    extra_kmh = min(gap_surplus * 3.0, CATCHUP_KMH * 3)
    return v_pre_kmh + extra_kmh


def configure_tm_vehicle(tm, vehicle, target_speed_kmh):
    tm.auto_lane_change(vehicle, False)
    tm.ignore_lights_percentage(vehicle, 100)
    tm.ignore_signs_percentage(vehicle, 100)
    tm.set_desired_speed(vehicle, float(target_speed_kmh))


def compute_lead_route(carla_map, start_location, distance_m=3000.0, step_m=5.0):
    """Pre-compute a straight-ahead route using correct yaw-based waypoint selection.

    LeadNavigator.find_waypoints_ahead() picks junction exits by maximum
    distance from the last waypoint, which can select an off-ramp instead of
    the highway continuation.  This function always picks the minimum
    yaw-difference candidate at every step (including inside junctions),
    producing a reliably straight-ahead route.

    The resulting deque is assigned to lead_ctrl.waypoints_ahead before the
    main loop so the navigator never needs to call find_waypoints_ahead()
    during the scenario run.
    """
    start_wpt = carla_map.get_waypoint(start_location)
    waypoints = deque()
    current = start_wpt
    traveled = 0.0
    while traveled < distance_m:
        next_wpts = current.next(step_m)
        if not next_wpts:
            break
        yaw = current.transform.rotation.yaw
        nxt = min(
            next_wpts,
            key=lambda x: abs((x.transform.rotation.yaw - yaw + 180.0) % 360.0 - 180.0),
        )
        waypoints.append(nxt)
        current = nxt
        traveled += step_m
    return waypoints


def build_truck_platoon(sim, bp, lead_spawn, label, lead_speed_kmh, tm, tm_port):
    platoon = Core.Platoon(sim)
    lead = platoon.add_lead_vehicle(bp, lead_spawn)
    sim.tick()

    lead_ctrl = PlatooningControllers.LeadNavigator(lead, initial_speed=lead_speed_kmh)
    lead.attach_controller(lead_ctrl)
    print(f"{label} lead LeadNavigator @ {lead_speed_kmh:.0f} km/h")

    anchor = lead
    anchor_wpt = sim.map.get_waypoint(lead_spawn.location)
    for follower_idx in range(PLATOON_SIZE - 1):
        follower_wpt = _retreat_waypoint(anchor_wpt, PLATOON_SPACING_M) if anchor_wpt is not None else None
        if follower_wpt is not None and follower_wpt.lane_type == carla.LaneType.Driving:
            follower_spawn = _spawn_from_waypoint(follower_wpt)
        else:
            follower_spawn = anchor.transform_ahead(-PLATOON_SPACING_M, force_straight=True)
        follower = platoon.add_follower_vehicle(bp, follower_spawn)
        controller = PlatooningControllers.FollowerController(
            follower, v_ref_cacc, platoon, dependencies=[-1, 0]
        )
        follower.attach_controller(controller)
        sim.tick()
        print(f"{label} follower {follower_idx + 1} CACC ON")
        anchor = follower
        anchor_wpt = follower_wpt if follower_wpt is not None else sim.map.get_waypoint(follower.get_location())

    # Seed the shared lead waypoint deque so the rear followers have something to track
    # before the lead has generated enough live samples.
    platoon.store_follower_waypoints()
    platoon.lead_waypoints.append(sim.map.get_waypoint(lead.get_location()))

    return platoon


def set_lead_speed(tm, platoon, speed_kmh):
    lead = platoon[0]
    if lead.controller is not None and hasattr(lead.controller, "set_target_speed"):
        lead.controller.set_target_speed(float(speed_kmh))
    else:
        tm.set_desired_speed(lead._carla_vehicle, float(speed_kmh))


def handoff_lead_to_navigator(platoon, tm_port, target_speed_kmh=None):
    lead = platoon[0]
    target_speed = lead.speed * 3.6 if target_speed_kmh is None else float(target_speed_kmh)

    if lead.autopilot:
        lead.set_autopilot(False, tm_port)
        lead_ctrl = PlatooningControllers.LeadNavigator(lead, initial_speed=target_speed)
        lead.attach_controller(lead_ctrl)
        return

    if lead.controller is not None and hasattr(lead.controller, "set_target_speed"):
        lead.controller.set_target_speed(target_speed)


class TransferState(Enum):
    CRUISE = auto()
    GAP_OPENING = auto()
    LANE_CHANGE = auto()
    FOLLOWING = auto()
    COMPLETE = auto()
    ABORT = auto()


class KeyInput:
    """Keyboard reader that works in both TTY and non-TTY environments.

    In TTY mode: puts stdin in cbreak so each keypress arrives immediately.
    In non-TTY mode: a background thread reads lines from stdin; a space or
    bare newline counts as a SPACE press. The scenario can also be triggered
    by typing Enter in a pipeline (e.g. ``echo '' | python3 scenario.py``).
    """

    def __init__(self):
        self._tty_active = False
        self._pending = threading.Event()
        self._fd = None
        self._old = None

        try:
            self._fd = sys.stdin.fileno()
            if os.isatty(self._fd):
                self._old = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
                self._tty_active = True
                print("[keys] Press SPACE to start rendezvous. Ctrl-C to quit.")
            else:
                raise termios.error("not a tty")
        except (termios.error, ValueError):
            print("[keys] Not a TTY – type SPACE + Enter (or send newline) to start rendezvous.")
            t = threading.Thread(target=self._stdin_reader, daemon=True)
            t.start()

    def _stdin_reader(self):
        """Block-read stdin lines; treat space or bare newline as SPACE."""
        try:
            for line in sys.stdin:
                stripped = line.rstrip("\n")
                if stripped == "" or " " in stripped:
                    self._pending.set()
                    return
        except Exception:
            pass

    def read(self):
        if self._tty_active:
            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if ch == "\x03":
                    self.restore()
                    raise KeyboardInterrupt
                return ch
            return ""
        # non-TTY path: poll the event set by the reader thread
        if self._pending.is_set():
            self._pending.clear()
            return " "
        return ""

    def restore(self):
        if self._tty_active and self._fd is not None and self._old is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


class TailTransferCoordinator:
    """Detach one donor tail vehicle, merge it behind another platoon, then reattach it."""

    def __init__(self, donor_id, donor_platoon, receiver_id, receiver_platoon, tm, tm_port, carla_map):
        self.donor_id = donor_id
        self.donor_platoon = donor_platoon
        self.receiver_id = receiver_id
        self.receiver_platoon = receiver_platoon
        self.tm = tm
        self.tm_port = tm_port
        self._map = carla_map

        self.state = TransferState.CRUISE
        self.detached_vehicle = None
        self.merge_complete = False
        self._lc_dir = None
        self._lc_confirm = 0
        self._sample_ticks = 0
        self._merge_pid = None

    def phase_name(self):
        return self.state.name

    def camera_target(self):
        if self.detached_vehicle is not None:
            return self.detached_vehicle._carla_vehicle
        return self.donor_platoon[-1]._carla_vehicle

    def current_receiver_gap(self):
        if self.detached_vehicle is None:
            return np.nan

        reference = self.receiver_platoon[-2] if self.state == TransferState.COMPLETE else self.receiver_platoon[-1]
        return reference.distance_to(self.detached_vehicle)

    def _receiver_tail(self):
        return self.receiver_platoon[-1]

    def _set_speed(self, kmh):
        self.tm.set_desired_speed(self.detached_vehicle._carla_vehicle, float(max(kmh, MERGE_MIN_SPEED_KMH)))

    def _ensure_merge_pid(self):
        if self._merge_pid is None:
            self.detached_vehicle.set_autopilot(False, self.tm_port)
            self._merge_pid = nav_controller.VehiclePIDController(
                self.detached_vehicle._carla_vehicle,
                args_lateral={"K_P": 1.95, "K_I": 0.05, "K_D": 0.2, "dt": DT},
                args_longitudinal={"K_P": 0.4, "K_I": 0.13, "K_D": 0.086, "dt": DT},
                max_throttle=1.0,
                max_brake=0.3,
                max_steering=0.8,
            )
        return self._merge_pid

    def _receiver_lane_target(self, lookahead_m=28.0):
        """Waypoint ahead of the detached truck, but centered on receiver lane."""
        receiver_wpt = self._map.get_waypoint(self._receiver_tail()._carla_vehicle.get_location())
        aligned = _align_reference_waypoint(receiver_wpt, self._behind_offset())
        if aligned is None:
            aligned = receiver_wpt
        target = _advance_waypoint(aligned, lookahead_m)
        return target if target is not None else aligned

    def _apply_manual_merge_control(self, target_speed_kmh, lookahead_m=28.0):
        pid = self._ensure_merge_pid()
        target = self._receiver_lane_target(lookahead_m=lookahead_m)
        control = pid.run_step(float(max(target_speed_kmh, MERGE_MIN_SPEED_KMH)), target)
        self.detached_vehicle._carla_vehicle.apply_control(control)

    def _is_straight(self, actor, ahead_m=STRAIGHT_M):
        wpt = self._map.get_waypoint(actor.get_location())
        for _ in range(int(ahead_m / 10.0)):
            nxt = wpt.next(10.0)
            if not nxt or nxt[0].is_junction:
                return False
            wpt = nxt[0]
        return True

    def _behind_offset(self):
        return signed_longitudinal_offset(self._receiver_tail(), self.detached_vehicle)

    def _resolve_lc_dir(self):
        receiver_loc = self._receiver_tail()._carla_vehicle.get_location()
        ego_loc = self.detached_vehicle._carla_vehicle.get_location()
        ego_yaw = np.deg2rad(self._map.get_waypoint(ego_loc).transform.rotation.yaw)

        right_vec = np.array([np.sin(ego_yaw), -np.cos(ego_yaw)])
        diff = np.array([receiver_loc.x - ego_loc.x, receiver_loc.y - ego_loc.y])
        lateral = float(np.dot(diff, right_vec))

        # CARLA TrafficManager.force_lane_change uses True=right, False=left.
        # On Town06's negative lane IDs, the receiver can be two physical lanes
        # away (e.g. -3 -> -5). Keep commanding the geometric direction until
        # the vehicle reaches the receiver's lane; do not flip just because a
        # single immediate adjacent lane lookup is missing/non-driving.
        direction = lateral > 0
        side = "RIGHT" if direction else "LEFT"
        print(f"    [LC dir] lateral={lateral:.1f}m -> {side}")

        return direction

    def try_start_transfer(self, step=0):
        if self.state != TransferState.CRUISE:
            return False

        # Fetch committed transfer from bridge to know WHICH vehicle to move
        transfer, err = _bridge_get_ready_transfer()
        if transfer is None:
            if step % 50 == 0:
                print(f"[transfer] Waiting for bridge: {err}")
            return False
        
        target_id = transfer["vehicle_id"]
        # Find the vehicle in the donor platoon
        donor_idx = -1
        ids = [v.id for v in self.donor_platoon]
        for i, v in enumerate(self.donor_platoon):
            if v.id == target_id:
                donor_idx = i
                break
        
        if step % 50 == 0:
            print(f"[debug] Donor Platoon: {ids}, Target: {target_id}, Found Index: {donor_idx}")
        
        if donor_idx == -1:
            if step % 50 == 0:
                print(f"[transfer] Error: target {target_id} not found in {self.donor_id}")
            return False

        # Leader handover is now supported by the improved Platoon.split()
        if donor_idx == 0:
            print("[transfer] Proceeding with leader handover.")

        # ── Double-Gap logic: Space in front AND behind ──────────────────────
        REQUIRED_GAP = 20.0
        
        # 1. Target vehicle opens gap with its predecessor
        target_v = self.donor_platoon[donor_idx]
        ready_front = True
        gap_front = 0.0
        if donor_idx > 0:
            predecessor = self.donor_platoon[donor_idx - 1]
            gap_front = predecessor.distance_to(target_v)
            ready_front = gap_front >= REQUIRED_GAP
            
            if not ready_front:
                if getattr(target_v, "h", 0.5) < 3.5:
                    print(f"\n[split] Target {target_id} opening gap with PREDECESSOR {predecessor.id}.")
                    target_v.h = 4.0 # Target drifts back
                elif step % 100 == 0:
                    print(f"[split] Front gap: {gap_front:.1f}m / {REQUIRED_GAP}m")
        
        # 2. Vehicle behind (if any) opens gap with target
        ready_behind = True
        if donor_idx < len(self.donor_platoon) - 1:
            follower_behind = self.donor_platoon[donor_idx + 1]
            gap_behind = target_v.distance_to(follower_behind)
            ready_behind = gap_behind >= REQUIRED_GAP
            if not ready_behind:
                if getattr(follower_behind, "h", 0.5) < 3.5:
                    print(f"[split] Successor {follower_behind.id} opening gap with TARGET {target_id}.")
                    follower_behind.h = 4.0 # Successor drifts back further
                elif step % 100 == 0:
                    print(f"[split] Behind gap: {gap_behind:.1f}m / {REQUIRED_GAP}m")
        
        if not (ready_front and ready_behind):
            return False

        # Both gaps ready!
        print(f"\n[split] Double-gap achieved (Front:{gap_front:.1f}m, Behind:{'N/A' if ready_behind and donor_idx==len(self.donor_platoon)-1 else gap_behind:.1f}m). Proceeding.")
        
        # Reset headway for the successor BEFORE it becomes the new follower of the predecessor
        if donor_idx < len(self.donor_platoon) - 1:
            self.donor_platoon[donor_idx + 1].h = 0.5 # Prepare to close gap after split
        
        donor_target_v = self.donor_platoon[donor_idx]
        pair_metrics = compute_pair_metrics(self._map, self._receiver_tail(), donor_target_v)
        lat_adj = _vehicles_laterally_adjacent(self._receiver_tail(), donor_target_v)
        
        if not lat_adj:
            if step % 50 == 0:
                print(f"[transfer] Waiting: Not laterally adjacent to receiver tail")
            return False
        
        if pair_metrics["distance_m"] > MERGE_DISTANCE_LIMIT_M:
            if step % 50 == 0:
                print(f"[transfer] Waiting: Too far from receiver tail ({pair_metrics['distance_m']:.1f}m > {MERGE_DISTANCE_LIMIT_M}m)")
            return False

        if pair_metrics["yaw_delta_deg"] > MERGE_YAW_LIMIT_DEG:
            if step % 50 == 0:
                print(f"[transfer] Waiting: Yaw mismatch ({pair_metrics['yaw_delta_deg']:.1f}deg)")
            return False

        set_lead_speed(self.tm, self.receiver_platoon, SYNC_SPEED_KMH)
        set_lead_speed(self.tm, self.donor_platoon, SYNC_SPEED_KMH)

        # Use split() to remove the vehicle at donor_idx. 
        new_platoons = self.donor_platoon.split(donor_idx, donor_idx)
        self.detached_vehicle = new_platoons[0][0] # The vehicle is now the leader of its own 1-car platoon
        
        self.detached_vehicle.attach_controller(None)
        configure_tm_vehicle(self.tm, self.detached_vehicle._carla_vehicle, SYNC_SPEED_KMH)
        self.tm.distance_to_leading_vehicle(self.detached_vehicle._carla_vehicle, TEMP_MERGE_FOLLOW_DIST_M)
        self.detached_vehicle.set_autopilot(True, self.tm_port)
        print(f"[transfer] Detached {target_id} from {self.donor_id} for transfer to {self.receiver_id}")

        # Always start LANE_CHANGE immediately – no separate GAP_OPENING phase.
        # If the gap is already sufficient, use a speed boost to catch the receiver;
        # otherwise use a slight undercut to open the gap while crossing lanes.
        # This way the truck keeps moving throughout the maneuver.
        offset = self._behind_offset()
        receiver_speed_kmh = self._receiver_tail().speed * 3.6
        self._lc_dir = self._resolve_lc_dir()
        if offset >= TARGET_GAP_M:
            self._set_speed(receiver_speed_kmh + LANE_CHANGE_BOOST_KMH)
        else:
            self._set_speed(max(receiver_speed_kmh - OPEN_GAP_DELTA_KMH, MERGE_MIN_SPEED_KMH))
        self.state = TransferState.LANE_CHANGE
        print(f"[transfer] gap={offset:.1f}m -> start LANE_CHANGE immediately (no stop)")

        self._sample_ticks = 0
        self._lc_confirm = 0
        return True

    def abort(self, reason="unknown"):
        if self.state == TransferState.ABORT:
            return
        
        print(f"[transfer] ABORTING: {reason}")
        self.state = TransferState.ABORT
        
        if self.detached_vehicle:
            # Safely stop the vehicle in its current lane
            self.detached_vehicle.set_autopilot(True, self.tm_port)
            configure_tm_vehicle(self.tm, self.detached_vehicle._carla_vehicle, 0.0)
            
        request_id = _bridge_get_committed_request_id()
        if request_id:
            _bridge_fail(request_id, reason)

    def update(self):
        if self.detached_vehicle is None or self.state in (TransferState.CRUISE, TransferState.COMPLETE):
            return

        self._sample_ticks += 1
        
        # Safety Check: Detect upcoming junctions (like the highway fork)
        if self._sample_ticks % 10 == 0:
            if not self._is_straight(self.detached_vehicle._carla_vehicle, ahead_m=30.0):
                self.abort("upcoming junction detected too close")
                return

        # Phase-specific timeouts
        if self.state == TransferState.LANE_CHANGE and self._sample_ticks > 1500: # 15s timeout
             self.abort("lane change timeout")
             return
        if self.state == TransferState.FOLLOWING and self._sample_ticks > 2500: # 25s timeout (cumulative)
             self.abort("following timeout")
             return

        if self.state == TransferState.LANE_CHANGE:
            receiver_tail = self._receiver_tail()
            offset = self._behind_offset()
            receiver_speed_kmh = receiver_tail.speed * 3.6
            # While gap is not yet safe, undercut receiver speed to drift back
            # while changing lanes simultaneously.  Once the gap is open, boost
            # to stay close and complete the lateral move.
            if offset < TARGET_GAP_M:
                lc_speed = max(receiver_speed_kmh - OPEN_GAP_DELTA_KMH, MERGE_MIN_SPEED_KMH)
            else:
                lc_speed = receiver_speed_kmh + LANE_CHANGE_BOOST_KMH
            self._apply_manual_merge_control(lc_speed)

            ego_loc = self.detached_vehicle._carla_vehicle.get_location()
            recv_loc = receiver_tail._carla_vehicle.get_location()
            ego_wpt = self._map.get_waypoint(ego_loc)
            receiver_wpt = self._map.get_waypoint(recv_loc)

            # Measure lateral separation in ego-heading frame (right-perpendicular).
            # This is more robust than raw Y-coordinate which only works on
            # near-horizontal roads and breaks on curved segments.
            # lane_id comparison is avoided because the junction at x≈658 flips
            # lane IDs (-3→+3) which would permanently stall the confirm counter.
            ego_yaw_rad = np.deg2rad(ego_wpt.transform.rotation.yaw)
            right_vec = np.array([np.sin(ego_yaw_rad), -np.cos(ego_yaw_rad)])
            diff_xy = np.array([recv_loc.x - ego_loc.x, recv_loc.y - ego_loc.y])
            lateral_sep = abs(float(np.dot(diff_xy, right_vec)))
            in_receiver_lane = lateral_sep < LANE_CENTER_TOLERANCE_M

            if in_receiver_lane:
                self._lc_confirm += 1
                if self._lc_confirm >= CONFIRM_TICKS:
                    gap = receiver_tail.distance_to(self.detached_vehicle)
                    self.tm.distance_to_leading_vehicle(self.detached_vehicle._carla_vehicle, TEMP_MERGE_FOLLOW_DIST_M)
                    self._set_speed(receiver_tail.speed * 3.6 + CATCHUP_KMH)
                    self.state = TransferState.FOLLOWING
                    print(f"[transfer] LANE_CHANGE -> FOLLOWING  gap={gap:.1f}m")
            else:
                self._lc_confirm = 0

            if self._sample_ticks % 20 == 0:
                print(
                    f"    [LC] donor lane={ego_wpt.lane_id}(y={ego_loc.y:.1f})  "
                    f"receiver lane={receiver_wpt.lane_id}(y={recv_loc.y:.1f})  "
                    f"lat_sep={lateral_sep:.1f}m  confirm={self._lc_confirm}/{CONFIRM_TICKS}"
                )

        elif self.state == TransferState.FOLLOWING:
            receiver_tail = self._receiver_tail()
            gap = receiver_tail.distance_to(self.detached_vehicle)
            receiver_speed_kmh = receiver_tail.speed * 3.6
            if gap > FOLLOW_DIST_M + 2.0:
                # Scale catchup speed with gap distance – faster when far away
                extra = min(CATCHUP_KMH * gap / FOLLOW_DIST_M, CATCHUP_KMH * 3)
                self._apply_manual_merge_control(receiver_speed_kmh + extra, lookahead_m=35.0)
            else:
                self._apply_manual_merge_control(receiver_speed_kmh, lookahead_m=25.0)
                offset = self._behind_offset()
                if offset >= 0.0:
                    self.merge_complete = True
                    print(f"[transfer] FOLLOWING -> COMPLETE_READY  gap={gap:.1f}m  offset={offset:.1f}m")

    def finalize_join(self):
        if not self.merge_complete or self.detached_vehicle is None:
            return False

        joined_vehicle = self.detached_vehicle
        # Capture predecessor BEFORE attach_tail_vehicle(); after that call,
        # receiver_platoon[-1] becomes joined_vehicle itself, making any
        # self._receiver_tail() call return the wrong vehicle (distance=0, own speed).
        prev_tail = self.receiver_platoon[-1]
        set_lead_speed(self.tm, self.receiver_platoon, SYNC_SPEED_KMH)
        set_lead_speed(self.tm, self.donor_platoon, SYNC_SPEED_KMH)
        joined_vehicle.set_autopilot(False, self.tm_port)
        self.receiver_platoon.attach_tail_vehicle(joined_vehicle)

        controller = PlatooningControllers.FollowerController(
            joined_vehicle, v_ref_cacc_merge, self.receiver_platoon, dependencies=[-1, 0]
        )
        # Seed target_speed so PID applies throttle from the first control step
        # rather than waiting for the next take_measurements() call.
        gap_at_join = prev_tail.distance_to(joined_vehicle)
        v_pre_kmh = prev_tail.speed * 3.6
        controller.target_speed = v_pre_kmh + min((gap_at_join - FOLLOW_DIST_M) * 3.0, CATCHUP_KMH * 3)
        joined_vehicle.attach_controller(controller)

        self.state = TransferState.COMPLETE
        print(
            f"\n>>> JOIN COMPLETE: {self.receiver_id}={len(self.receiver_platoon)} vehicles, "
            f"{self.donor_id}={len(self.donor_platoon)} vehicles\n"
        )
        return True


class SmoothCamera:
    def __init__(self, spectator, height=CAM_HEIGHT, alpha=CAM_ALPHA):
        self._spec = spectator
        self._h = height
        self._alpha = alpha
        self._x = self._y = self._z = None

    def update(self, target_actor):
        try:
            loc = target_actor.get_location()
            if self._x is None:
                self._x = loc.x
                self._y = loc.y
                self._z = loc.z + self._h

            self._x += self._alpha * (loc.x - self._x)
            self._y += self._alpha * (loc.y - self._y)

            self._spec.set_transform(
                carla.Transform(
                    carla.Location(x=self._x, y=self._y, z=self._z),
                    carla.Rotation(pitch=-90, yaw=0, roll=0),
                )
            )
        except Exception:
            pass


def main():
    print("\n=== Two Platoon Truck Transfer Scenario (Town06) ===\n")

    sim = Core.Simulation(world="Town06", dt=DT, large_map=False, render=True, synchronous=True)
    cmap = sim.map
    bps = sim.get_vehicle_blueprints()
    bp = pick_truck_blueprint(bps)
    # Fixed spawn transforms (Town06 highway, ~225 m before fork)
    p1_sp = P1_SPAWN
    p2_sp = P2_SPAWN
    p1_lane = cmap.get_waypoint(p1_sp.location).lane_id
    p2_lane = cmap.get_waypoint(p2_sp.location).lane_id
    actual_offset_m = abs(p1_sp.location.x - p2_sp.location.x)  # 60 m

    tm = sim.get_trafficmanager()
    tm.set_synchronous_mode(True)
    tm_port = tm.get_port()

    p1 = build_truck_platoon(sim, bp, p1_sp, PLATOON_IDS[0], SYNC_SPEED_KMH, tm, tm_port)
    p2 = build_truck_platoon(sim, bp, p2_sp, PLATOON_IDS[1], SYNC_SPEED_KMH, tm, tm_port)

    DEST_STOP_RADIUS_M = 20.0   # 목적지 반경 내 진입 시 해당 소대 정지
    p1_stopped = False
    p2_stopped = False

    # Pre-populate lead navigators with a pre-computed straight-ahead route.
    # LeadNavigator's junction exit heuristic (max distance) can select wrong
    # exits; our yaw-based selection reliably stays on the highway through
    # junctions and segment boundaries.
    for label, platoon in (("P1", p1), ("P2", p2)):
        route = compute_lead_route(cmap, platoon[0].get_location(), distance_m=3000.0)
        if route:
            platoon[0].controller.waypoints_ahead = route
        print(f"[route] {label} lead: {len(route)} waypoints pre-computed")

    vehicle_refs = {
        "P1-0": p1[0],
        "P1-1": p1[1],
        "P1-2": p1[2],
        "P2-0": p2[0],
        "P2-1": p2[1],
        "P2-2": p2[2],
    }

    coordinator = TailTransferCoordinator("P1", p1, "P2", p2, tm, tm_port, cmap)
    camera = SmoothCamera(sim.spectator)
    _start_trigger_server()
    kb = KeyInput()
    approach_started = False
    approach_start_time_s = None
    merge_failure_reported = False
    stable_reported = False
    post_merge_speed_set = False

    print(f"P1 donor    lane={p1_lane}  lead=({p1_sp.location.x:.1f}, {p1_sp.location.y:.1f})")
    print(f"P2 receiver lane={p2_lane}  lead=({p2_sp.location.x:.1f}, {p2_sp.location.y:.1f})")
    print(
        f"Initial cruise: both {SYNC_SPEED_KMH:.0f} km/h, "
        "after SPACE -> adaptive rendezvous control brings both platoons together, "
        f"initial lead offset={actual_offset_m:.0f}m\n"
    )

    actor_names = list(vehicle_refs.keys())
    log_time = deque(maxlen=LOG_MAX_STEPS)
    log_speed = {name: deque(maxlen=LOG_MAX_STEPS) for name in actor_names}
    log_sizes = {"P1": deque(maxlen=LOG_MAX_STEPS), "P2": deque(maxlen=LOG_MAX_STEPS)}
    log_gap = deque(maxlen=LOG_MAX_STEPS)
    log_state = deque(maxlen=LOG_MAX_STEPS)
    state_int = {
        TransferState.CRUISE: 0,
        TransferState.GAP_OPENING: 1,
        TransferState.LANE_CHANGE: 2,
        TransferState.FOLLOWING: 3,
        TransferState.COMPLETE: 4,
        TransferState.ABORT: 5,
    }
    step = 0

    def start_approach(time_s):
        nonlocal approach_started, approach_start_time_s
        if approach_started or coordinator.state != TransferState.CRUISE:
            return False

        lead_metrics = compute_pair_metrics(cmap, p2[0], p1[0])

        set_lead_speed(tm, p1, SYNC_SPEED_KMH)
        set_lead_speed(tm, p2, APPROACH_FAST_KMH)

        approach_started = True
        approach_start_time_s = time_s
        transfer, _ = _bridge_get_ready_transfer()
        if transfer and transfer.get("status") == "committed":
            _bridge_notify(transfer["request_id"], "merging")
        _bridge_update_readiness(
            "merging",
            False,
            "start_merge accepted; adaptive rendezvous started",
            request_id=transfer["request_id"] if transfer else None,
            metrics={"time_s": round(time_s, 1), "lead_offset_m": round(lead_metrics["offset_m"], 2)},
        )
        print(
            f"[approach] triggered at {time_s:.1f}s -> "
            "adaptive receiver/donor speed control "
            f"(donor behind receiver lead offset={lead_metrics['offset_m']:.1f}m)"
        )
        return True

    def update_approach_control(lead_metrics, tail_metrics):
        offset = tail_metrics["offset_m"]

        # Proportional control: drive donor-behind-receiver offset toward TARGET_OFFSET.
        TARGET_OFFSET = MERGE_OFFSET_MIN_M + 5.0   # aim slightly above minimum

        # Aligned and offset in target range → hold at cruise (ready for merge)
        if _vehicles_laterally_adjacent(p2[-1], p1[-1]) and MERGE_OFFSET_MIN_M <= offset <= TARGET_OFFSET + 5.0:
            set_lead_speed(tm, p1, SYNC_SPEED_KMH)
            set_lead_speed(tm, p2, SYNC_SPEED_KMH)
            return SYNC_SPEED_KMH, SYNC_SPEED_KMH, "merge-hold", "-"

        err = TARGET_OFFSET - offset
        K = 0.5
        if err > 0.0:
            # Donor is not far enough behind receiver: pull the receiver ahead.
            p1_spd = SYNC_SPEED_KMH
            p2_spd = float(np.clip(SYNC_SPEED_KMH + K * err, SYNC_SPEED_KMH, APPROACH_FAST_KMH))
            phase = "receiver-pull"
        else:
            # Donor is too far behind receiver: let donor close the gap.
            p1_spd = float(np.clip(SYNC_SPEED_KMH - K * err, SYNC_SPEED_KMH, APPROACH_FAST_KMH))
            p2_spd = SYNC_SPEED_KMH
            phase = "donor-close"
        behind = "P1"

        set_lead_speed(tm, p1, p1_spd)
        set_lead_speed(tm, p2, p2_spd)
        return p1_spd, p2_spd, phase, behind

    p1_cmd_speed = SYNC_SPEED_KMH
    p2_cmd_speed = SYNC_SPEED_KMH
    approach_phase = "idle"
    behind_label = "-"

    try:
        while True:
            is_sample = step % SAMPLING_RATE == 0
            time_s = step * DT

            if time_s > 200.0:
                print(f"\n[timeout] Failed to complete within 200s (state: {coordinator.state.name})")
                sys.exit(1)

            if coordinator.state == TransferState.COMPLETE:
                print(f"\n[success] Transfer completed at {time_s:.1f}s. Exiting.")
                sys.exit(0)

            if is_sample and not approach_started:
                key = kb.read()
                if key == " ":
                    target_v = p1[1] if len(p1) > 1 else p1[-1]
                    threading.Thread(target=_auto_bridge_commit, args=(target_v.id,), daemon=True).start()
                    start_approach(time_s)
                elif _merge_trigger_event.is_set():
                    _merge_trigger_event.clear()
                    print(f"[trigger] POST /start_merge received at {time_s:.1f}s → starting approach")
                    start_approach(time_s)

            if is_sample and coordinator.state == TransferState.CRUISE:
                # Target middle truck (index 1) for alignment metrics
                alignment_v = p1[1] if len(p1) > 1 else p1[-1]
                pair_metrics = compute_pair_metrics(cmap, p2[-1], alignment_v)
                lead_metrics = compute_pair_metrics(cmap, p2[0], p1[0])
                donor_straight_ready = coordinator._is_straight(alignment_v._carla_vehicle)
                receiver_straight_ready = coordinator._is_straight(p2[-1]._carla_vehicle)
                
                if approach_started:
                    p1_cmd_speed, p2_cmd_speed, approach_phase, behind_label = update_approach_control(
                        lead_metrics, pair_metrics
                    )
                else:
                    p1_cmd_speed = p2_cmd_speed = SYNC_SPEED_KMH
                    approach_phase = "idle"
                    behind_label = "-"

                lat_adj_now = _vehicles_laterally_adjacent(p2[-1], alignment_v)
                merge_ready = (
                    approach_started
                    and lat_adj_now
                    and pair_metrics["distance_m"] <= MERGE_DISTANCE_LIMIT_M
                    and pair_metrics["yaw_delta_deg"] <= MERGE_YAW_LIMIT_DEG
                    and pair_metrics["offset_m"] >= MERGE_OFFSET_MIN_M
                    and time_s >= MIN_TRIGGER_TIME_S
                )
                if step % BRIDGE_POLL_TICKS == 0:
                    request_id = _bridge_get_committed_request_id()
                    status = "ready" if merge_ready else ("merging" if approach_started else "waiting")
                    _bridge_update_readiness(
                        status,
                        merge_ready,
                        _readiness_reason(
                            approach_started,
                            donor_straight_ready,
                            receiver_straight_ready,
                            lat_adj_now,
                            pair_metrics,
                            time_s,
                        ),
                        request_id=request_id,
                        vehicle_id=alignment_v.id,
                        metrics={
                            "time_s": round(time_s, 1),
                            "state": coordinator.state.name,
                            "approach_started": approach_started,
                            "distance_m": round(pair_metrics["distance_m"], 2),
                            "offset_m": round(pair_metrics["offset_m"], 2),
                            "lateral_m": round(pair_metrics["lateral_m"], 2),
                            "yaw_delta_deg": round(pair_metrics["yaw_delta_deg"], 2),
                            "lat_adjacent": lat_adj_now,
                            "donor_straight": donor_straight_ready,
                            "receiver_straight": receiver_straight_ready,
                            "p1_cmd_kmh": round(p1_cmd_speed, 1),
                            "p2_cmd_kmh": round(p2_cmd_speed, 1),
                        },
                    )
                if merge_ready:
                    print(
                        "[merge] transfer conditions satisfied "
                        f"(offset={pair_metrics['offset_m']:.1f}m, "
                        f"lat={pair_metrics['lateral_m']:.1f}m, "
                        f"dist={pair_metrics['distance_m']:.1f}m, "
                        f"yaw={pair_metrics['yaw_delta_deg']:.1f}deg)"
                    )
                    coordinator.try_start_transfer(step)
                elif approach_started and step % 500 == 0:
                    print(
                        "[merge-wait] "
                        f"offset={pair_metrics['offset_m']:.1f}m >= {MERGE_OFFSET_MIN_M:.1f}m  "
                        f"lat_adj={int(lat_adj_now)}  "
                        f"dist={pair_metrics['distance_m']:.1f}m  "
                        f"yaw={pair_metrics['yaw_delta_deg']:.1f}deg"
                    )
                if (
                    approach_started
                    and approach_start_time_s is not None
                    and not merge_failure_reported
                    and (time_s - approach_start_time_s) > MERGE_TIMEOUT_S
                    and coordinator.state == TransferState.CRUISE
                ):
                    merge_failure_reported = True
                    reason = f"merge readiness timeout after {MERGE_TIMEOUT_S:.0f}s"
                    coordinator.abort(reason)
                    print(f"[timeout] {reason}")

            coordinator.update()

            if coordinator.merge_complete and coordinator.state != TransferState.COMPLETE:
                coordinator.finalize_join()

            # ── Destination arrival check (only after transfer complete) ───────
            transfer_done = coordinator.state == TransferState.COMPLETE
            if transfer_done and not p1_stopped:
                p1_lead_loc = p1[0]._carla_vehicle.get_location()
                if p1_lead_loc.distance(DEST_A) <= DEST_STOP_RADIUS_M:
                    print(f"[dest] P1 reached DEST_A at t={time_s:.1f}s — stopping platoon")
                    set_lead_speed(tm, p1, 0.0)
                    p1_stopped = True
            if transfer_done and not p2_stopped:
                p2_lead_loc = p2[0]._carla_vehicle.get_location()
                if p2_lead_loc.distance(DEST_B) <= DEST_STOP_RADIUS_M:
                    print(f"[dest] P2 reached DEST_B at t={time_s:.1f}s — stopping platoon")
                    set_lead_speed(tm, p2, 0.0)
                    p2_stopped = True
            # ──────────────────────────────────────────────────────────────────

            sim.run_step(mode="sample" if is_sample else "control")

            if step % CAM_EVERY == 0:
                camera.update(coordinator.camera_target())

            log_time.append(time_s)
            for name in actor_names:
                log_speed[name].append(vehicle_refs[name].speed * 3.6)
            log_sizes["P1"].append(len(p1))
            log_sizes["P2"].append(len(p2))
            log_gap.append(coordinator.current_receiver_gap())
            log_state.append(state_int[coordinator.state])

            sim.tick()

            if step % 500 == 0:
                if coordinator.state == TransferState.CRUISE:
                    pair_metrics = compute_pair_metrics(cmap, p2[-1], p1[-1])
                    lead_metrics = compute_pair_metrics(cmap, p2[0], p1[0])
                    p1_lead_wpt = cmap.get_waypoint(p1[0]._carla_vehicle.get_location())
                    p2_lead_wpt = cmap.get_waypoint(p2[0]._carla_vehicle.get_location())
                    offset_info = pair_metrics["offset_m"]
                    lead_offset_info = lead_metrics["offset_m"]
                    lateral_info = pair_metrics["lateral_m"]
                    adjacent_info = pair_metrics["physical_adjacent"]
                    lane_gap_info = pair_metrics["lane_center_dist_m"]
                    p1_lead_road = p1_lead_wpt.road_id
                    p1_lead_lane = p1_lead_wpt.lane_id
                    p2_lead_road = p2_lead_wpt.road_id
                    p2_lead_lane = p2_lead_wpt.lane_id
                else:
                    offset_info = np.nan
                    lead_offset_info = np.nan
                    lateral_info = np.nan
                    adjacent_info = False
                    lane_gap_info = np.nan
                    p1_lead_road = -1
                    p1_lead_lane = 0
                    p2_lead_road = -1
                    p2_lead_lane = 0
                print(
                    f"t={time_s:6.1f}s  P1={len(p1)}  P2={len(p2)}  "
                    f"P1lead={p1[0].speed*3.6:5.1f}  "
                    f"P2lead={p2[0].speed*3.6:5.1f}  "
                    f"P1tail={p1[-1].speed*3.6:5.1f}  "
                    f"P2tail={p2[-1].speed*3.6:5.1f}  "
                    f"P1cmd={p1_cmd_speed:5.1f}  "
                    f"P2cmd={p2_cmd_speed:5.1f}  "
                    f"P1rl={p1_lead_road}/{p1_lead_lane}  "
                    f"P2rl={p2_lead_road}/{p2_lead_lane}  "
                    f"lead_off={lead_offset_info:6.1f}m  "
                    f"offset={offset_info:6.1f}m  "
                    f"lat={lateral_info:5.1f}m  "
                    f"lane_gap={lane_gap_info:4.1f}m  "
                    f"adj={int(adjacent_info)}  "
                    f"behind={behind_label}  "
                    f"phase={approach_phase}  "
                    f"state={coordinator.state.name}"
                )

            if coordinator.state == TransferState.COMPLETE and not stable_reported:
                stable_reported = True
                print("[done] 4+2 formation achieved. Waiting for merged vehicle to close gap...")
                # Notify bridge server that CARLA completed the physical maneuver
                try:
                    snapshot_data = json.loads(
                        urllib.request.urlopen(f"{BRIDGE_URL}/snapshot", timeout=2).read()
                    )
                    committed_ids = [
                        tid for tid, t in snapshot_data.get("transfers", {}).items()
                        if t["status"] in ("committed", "merging") and _transfer_matches_scenario(t)
                    ]
                    for tid in committed_ids:
                        req = urllib.request.Request(
                            f"{BRIDGE_URL}/transfers/{tid}/carla_complete",
                            data=b"{}",
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            urllib.request.urlopen(req, timeout=2)
                        except Exception:
                            pass  # endpoint optional; bridge server may not implement it yet
                        _bridge_update_readiness(
                            "complete",
                            True,
                            "CARLA physical merge completed",
                            request_id=tid,
                            metrics={"time_s": round(time_s, 1), "state": coordinator.state.name},
                        )
                    print(f"[bridge] notified carla_complete for {committed_ids}")
                except Exception:
                    pass

            # Speed up only once the merged vehicle has actually closed the gap.
            # Speeding up immediately would widen the gap and defeat the purpose.
            if stable_reported and not post_merge_speed_set and len(p2) >= 2:
                tail_gap = p2[-2].distance_to(p2[-1])
                if tail_gap <= FOLLOW_DIST_M + 3.0:
                    post_merge_speed_set = True
                    set_lead_speed(tm, p1, POST_MERGE_SPEED_KMH)
                    set_lead_speed(tm, p2, POST_MERGE_SPEED_KMH)
            step += 1

    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        kb.restore()
        sim.release_synchronous()
    print("\n=== Done ===")

if __name__ == "__main__":
    main()

