"""
Bidirectional Multi-Trigger Platoon Scenario (A<->B, Tail/Mid)
============================================================
Triggers: 1 (A-tail->B), 2 (A-mid->B), 3 (B-tail->A), 4 (B-mid->A)
"""

import json, os, select, sys, termios, threading, tty, urllib.error, urllib.request, time
from collections import deque
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer
import glob

try:
    sys.path.append(glob.glob('/home/user/carla_source/PythonAPI/carla/dist/carla-*%d.%d-%s.egg' % (
        sys.version_info.major, sys.version_info.minor, 'win-amd64' if os.name == 'nt' else 'linux-x86_64'))[0])
except IndexError: pass
sys.path.append('/home/user/carla_source/PythonAPI/carla')

import carla
import numpy as np
from agents.navigation import controller as nav_controller

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from PlatooningSimulator import Core, PlatooningControllers

# ── config ──
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "simulation.json")
def load_sim_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f: return json.load(f)
    return {}
_cfg = load_sim_config(); _speeds = _cfg.get("speeds", {}); _gaps = _cfg.get("gaps", {}); _spawns = _cfg.get("spawns", {})

DT = 0.01
SAMPLING_RATE = 10
PLATOON_SIZE = 3
PLATOON_SPACING_M = float(_gaps.get("platoon_spacing_m", 18.0))
# CARLA center-to-center gap settles about 1 m above this controller target.
NORMAL_FOLLOW_GAP_M = float(_gaps.get("normal_follow_gap_m", 12.0))
OPEN_GAP_M = float(_gaps.get("open_gap_m", 20.0))
OPEN_GAP_READY_M = float(_gaps.get("open_gap_ready_m", 18.0))
SYNC_SPEED_KMH = float(_speeds.get("sync_speed_kmh", 18.0))
APPROACH_FAST_KMH = float(_speeds.get("approach_fast_kmh", 38.0))
MERGE_MIN_SPEED_KMH = float(_speeds.get("merge_min_speed_kmh", 15.0))
TARGET_GAP_M = float(_gaps.get("target_gap_m", 13.0))
FOLLOW_DIST_M = float(_gaps.get("follow_dist_m", 13.0))
LANE_CENTER_TOLERANCE_M = 1.0 # Stricter for PID
MERGE_TIMEOUT_S = 400.0
READY_OFFSET_TOL_M = 12.0
ADJACENT_LANE_MAX_M = 9.0
LANE_STEP_COMPLETE_M = 0.9

# ── spawns ──
_p1_s = _spawns.get("p1_spawn", {"x": -4000.0, "y": 136.0, "z": 0.3, "pitch": 0.0, "yaw": 0.2, "roll": 0.0})
P1_SPAWN = carla.Transform(carla.Location(x=_p1_s["x"], y=_p1_s["y"], z=_p1_s["z"]), carla.Rotation(pitch=_p1_s["pitch"], yaw=_p1_s["yaw"], roll=_p1_s["roll"]))
_p2_s = _spawns.get("p2_spawn", {"x": -4060.0, "y": 143.0, "z": 0.3, "pitch": 0.0, "yaw": 0.2, "roll": 0.0})
P2_SPAWN = carla.Transform(carla.Location(x=_p2_s["x"], y=_p2_s["y"], z=_p2_s["z"]), carla.Rotation(pitch=_p2_s["pitch"], yaw=_p2_s["yaw"], roll=_p2_s["roll"]))

BRIDGE_URL = "http://127.0.0.1:18801"
PLATOON_IDS = ("platoon_a", "platoon_b")

# ── helpers ──
def _bridge_get_ready_transfer():
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/snapshot", timeout=1) as r:
            data = json.loads(r.read().decode())
            ready = [t for t in data.get("transfers", {}).values() if t.get("status") in ("committed", "merging")]
            if not ready: return None
            ready.sort(key=lambda t: t.get("created_at", ""))
            return ready[0]
    except Exception: return None

def _bridge_get_negotiating_transfer():
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/snapshot", timeout=1) as r:
            data = json.loads(r.read().decode())
            active = [
                t for t in data.get("transfers", {}).values()
                if t.get("status") in ("pending", "accepted", "committed", "merging")
            ]
            if not active: return None
            active.sort(key=lambda t: t.get("created_at", ""))
            return active[0]
    except Exception: return None

_merge_trigger_event = threading.Event()
def _start_trigger_server():
    class H(BaseHTTPRequestHandler):
        def do_POST(self): _merge_trigger_event.set(); self.send_response(200); self.end_headers()
        def log_message(self, *a): pass
    threading.Thread(target=HTTPServer(("0.0.0.0", 18802), H).serve_forever, daemon=True).start()

def _auto_bridge_commit(vid, fp, tp):
    try:
        def post(p, b):
            req = urllib.request.Request(f"{BRIDGE_URL}{p}", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=1) as r: return json.loads(r.read().decode())
        res = post("/transfers", {"vehicle_id": vid, "from_platoon_id": fp, "to_platoon_id": tp})
        rid = res["request_id"]; post(f"/transfers/{rid}/accept", {}); post(f"/transfers/{rid}/commit", {})
    except Exception: pass

def _bridge_post(path, body=None):
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}{path}",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def _bridge_reload():
    res = _bridge_post("/reload", {})
    if res:
        print("[bridge] reset state for new CARLA scenario run")
    else:
        print("[bridge] reset skipped; bridge server unavailable")
    return res

def _spawn_from_waypoint(wpt):
    t = wpt.transform; return carla.Transform(carla.Location(x=t.location.x, y=t.location.y, z=t.location.z + 0.3), t.rotation)

def _yaw_diff(yaw_a, yaw_ref):
    return abs((yaw_a - yaw_ref + 180.0) % 360.0 - 180.0)

def _select_straight_waypoint(candidates, yaw_ref):
    if not candidates: return None
    return min(candidates, key=lambda w: _yaw_diff(w.transform.rotation.yaw, yaw_ref))

def _retreat_waypoint(wpt, distance_m):
    curr = wpt; rem = float(distance_m)
    while rem > 0.0:
        step = min(10.0, rem)
        nxt = curr.previous(step)
        if not nxt: return None
        curr = _select_straight_waypoint(nxt, curr.transform.rotation.yaw)
        rem -= step
    return curr

def _advance_waypoint(wpt, dist):
    curr = wpt; rem = float(dist)
    while rem > 0.0:
        step = min(10.0, rem)
        nxt = curr.next(step)
        if not nxt: return None
        curr = _select_straight_waypoint(nxt, curr.transform.rotation.yaw)
        rem -= step
    return curr

def _same_lane(a, b):
    return bool(a and b and a.road_id == b.road_id and a.lane_id == b.lane_id)

def _driving_adjacent_lanes(wpt):
    lanes = []
    for getter in (wpt.get_left_lane, wpt.get_right_lane):
        try:
            lane = getter()
        except RuntimeError:
            lane = None
        if lane and lane.lane_type == carla.LaneType.Driving:
            lanes.append(lane)
    return lanes

def _lane_distance(a, b):
    al = a.transform.location; bl = b.transform.location
    return float(np.hypot(al.x - bl.x, al.y - bl.y))

def _one_lane_step_target(cmap, ego, receiver_ref_wpt, signed_offset_m):
    ego_wpt = cmap.get_waypoint(ego._carla_vehicle.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving)
    receiver_target = _align_reference_waypoint(receiver_ref_wpt, signed_offset_m) or receiver_ref_wpt
    if not ego_wpt or not receiver_target:
        return receiver_target
    if _same_lane(ego_wpt, receiver_target):
        return receiver_target

    adjacent = _driving_adjacent_lanes(ego_wpt)
    if not adjacent:
        return ego_wpt
    step_lane = min(adjacent, key=lambda lane: _lane_distance(lane, receiver_target))
    return _align_reference_waypoint(step_lane, signed_offset_m) or step_lane

def _align_reference_waypoint(reference_wpt, signed_offset_m):
    if signed_offset_m > 0.0:
        return _retreat_waypoint(reference_wpt, signed_offset_m)
    return _advance_waypoint(reference_wpt, -signed_offset_m)

def compute_lead_route(cmap, start_location, distance_m=3000.0, step_m=5.0):
    route = deque()
    curr = cmap.get_waypoint(start_location, project_to_road=True, lane_type=carla.LaneType.Driving)
    if curr is None: return route
    route.append(curr)
    remaining = float(distance_m)
    while remaining > 0.0:
        nxt = curr.next(min(step_m, remaining))
        if not nxt: break
        curr = _select_straight_waypoint(nxt, curr.transform.rotation.yaw)
        route.append(curr)
        remaining -= step_m
    return route

def signed_longitudinal_offset(ref, ego):
    rl = ref._carla_vehicle.get_location(); el = ego._carla_vehicle.get_location()
    yaw = np.deg2rad(ref._carla_vehicle.get_transform().rotation.yaw)
    fwd = np.array([np.cos(yaw), np.sin(yaw)])
    return float(np.dot(np.array([rl.x - el.x, rl.y - el.y]), fwd))

def signed_lateral_offset(ref, ego):
    rl = ref._carla_vehicle.get_location(); el = ego._carla_vehicle.get_location()
    yaw = np.deg2rad(ref._carla_vehicle.get_transform().rotation.yaw)
    side = np.array([np.sin(yaw), -np.cos(yaw)])
    return float(np.dot(np.array([el.x - rl.x, el.y - rl.y]), side))

def compute_pair_metrics(cmap, rec, don):
    off = signed_longitudinal_offset(rec, don); lat = signed_lateral_offset(rec, don)
    rec_wpt = cmap.get_waypoint(rec._carla_vehicle.get_location())
    # Aligned waypoint on receiver lane
    if off > 0: al_rec_wpt = _retreat_waypoint(rec_wpt, off)
    else: al_rec_wpt = _advance_waypoint(rec_wpt, -off)
    
    physical_adj = False
    if al_rec_wpt:
        don_wpt = cmap.get_waypoint(don._carla_vehicle.get_location())
        dist = float(np.hypot(al_rec_wpt.transform.location.x - don_wpt.transform.location.x, al_rec_wpt.transform.location.y - don_wpt.transform.location.y))
        physical_adj = dist < 6.0
    return {"offset_m": off, "lateral_m": lat, "physical_adjacent": physical_adj, "distance_m": rec.distance_to(don)}

def _set_follow_gap(vehicle, gap_m):
    vehicle.desired_gap_m = float(gap_m)

def v_ref_cacc(pre, ego):
    gap = ego.distance_to(pre)
    vp = pre.speed
    ve = ego.speed
    desired_gap = getattr(ego, "desired_gap_m", NORMAL_FOLLOW_GAP_M)
    gap_err = gap - desired_gap
    v_ref_mps = vp + 0.55 * gap_err + 0.80 * (vp - ve)
    v_ref_kmh = v_ref_mps * 3.6
    return float(np.clip(v_ref_kmh, max(5.0, vp * 3.6 - 14.0), vp * 3.6 + 20.0))

class KeyInput:
    def __init__(self):
        self._active = False
        try:
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._active = True
            print("[keys] Press 1/2/3/4 to trigger transfers. Ctrl-C to quit.")
        except termios.error:
            print("[keys] Not a TTY; keyboard triggers disabled.")

    def read(self):
        if not self._active: return ""
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x03":
                self.restore()
                raise KeyboardInterrupt
            return ch
        return ""

    def restore(self):
        if self._active:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            self._active = False

def build_truck_platoon(sim, bp, sp, label, speed, tm, tm_port):
    p = Core.Platoon(sim); lead = p.add_lead_vehicle(bp, sp); sim.tick()
    lead.attach_controller(PlatooningControllers.LeadNavigator(lead, initial_speed=speed))
    anchor = lead; awpt = sim.map.get_waypoint(sp.location)
    for i in range(PLATOON_SIZE-1):
        fwpt = _retreat_waypoint(awpt, PLATOON_SPACING_M)
        f_sp = _spawn_from_waypoint(fwpt) if fwpt else anchor.transform_ahead(-PLATOON_SPACING_M)
        f = p.add_follower_vehicle(bp, f_sp); _set_follow_gap(f, NORMAL_FOLLOW_GAP_M)
        f.attach_controller(PlatooningControllers.FollowerController(f, v_ref_cacc, p, dependencies=[-1, 0]))
        sim.tick(); anchor = f; awpt = fwpt
    p.store_follower_waypoints(); p.lead_waypoints.append(sim.map.get_waypoint(lead.get_location()))
    return p

class TransferState(Enum): CRUISE=auto(); GAP=auto(); LC=auto(); FOLLOW=auto(); DONE=auto(); ABORT=auto()

class BidirectionalTransferCoordinator:
    def __init__(self, platoons, tm, tm_port, cmap):
        self.platoons = platoons; self.tm = tm; self.tm_port = tm_port; self.cmap = cmap
        self.donor_id=None; self.rec_id=None; self.donor_p=None; self.rec_p=None; self.state=TransferState.CRUISE
        self.detached_v=None; self.detached_p=None; self.complete=False; self.ticks=0; self.pid=None; self.request_id=None; self.last_wait_reason=None

    def camera_target(self): return self.detached_v._carla_vehicle if self.detached_v else self.platoons["platoon_a"][0]._carla_vehicle

    def try_start(self, step):
        if self.state != TransferState.CRUISE: return False
        t = _bridge_get_ready_transfer()
        if not t: return False
        self.donor_id, self.rec_id = t["from_platoon_id"], t["to_platoon_id"]
        self.request_id = t.get("request_id")
        self.donor_p, self.rec_p = self.platoons[self.donor_id], self.platoons[self.rec_id]
        idx = -1
        for i,v in enumerate(self.donor_p):
            if f"{self.donor_id}_truck{i}" == t["vehicle_id"]: idx = i; break
        if idx == -1:
            self.last_wait_reason = f"vehicle {t['vehicle_id']} not in {self.donor_id}"
            return False

        # Double-Gap
        target = self.donor_p[idx]; rf=True; rb=True
        if idx > 0:
            gf = self.donor_p[idx-1].distance_to(target)
            rf = gf >= OPEN_GAP_READY_M
            _set_follow_gap(target, OPEN_GAP_M if not rf else NORMAL_FOLLOW_GAP_M)
        if idx < len(self.donor_p)-1:
            gb = target.distance_to(self.donor_p[idx+1])
            rb = gb >= OPEN_GAP_READY_M
            _set_follow_gap(self.donor_p[idx+1], OPEN_GAP_M if not rb else NORMAL_FOLLOW_GAP_M)
        if not (rf and rb):
            self.last_wait_reason = f"opening gap front={gf if idx > 0 else -1:.1f} rear={gb if idx < len(self.donor_p)-1 else -1:.1f}"
            return False

        # Physical readiness
        pm = compute_pair_metrics(self.cmap, self.rec_p[-1], target)
        ready_offset = abs(pm["offset_m"] - TARGET_GAP_M) <= READY_OFFSET_TOL_M
        ready_lateral = 2.0 <= abs(pm["lateral_m"]) <= ADJACENT_LANE_MAX_M
        if not (ready_offset and ready_lateral):
            self.last_wait_reason = f"waiting adjacent offset={pm['offset_m']:.1f} lat={pm['lateral_m']:.1f} dist={pm['distance_m']:.1f}"
            return False

        # Split
        _set_follow_gap(target, NORMAL_FOLLOW_GAP_M)
        if idx < len(self.donor_p)-1:
            _set_follow_gap(self.donor_p[idx+1], NORMAL_FOLLOW_GAP_M)
        _bridge_post(f"/transfers/{self.request_id}/splitting")
        new_ps, _ = self.donor_p.split(idx, idx)
        self.detached_p = new_ps; self.detached_v = new_ps[0]; self.detached_v.attach_controller(None)
        self.donor_p.simulation.remove_platoon(new_ps)
        self.detached_p = None
        self.detached_v.set_autopilot(False, self.tm_port)
        self.detached_v._carla_vehicle.apply_control(carla.VehicleControl(throttle=0.25, brake=0.0, hand_brake=False))
        self.pid = nav_controller.VehiclePIDController(self.detached_v._carla_vehicle, 
            args_lateral={"K_P": 3.2, "K_I": 0.1, "K_D": 0.25, "dt": DT},
            args_longitudinal={"K_P": 0.65, "K_I": 0.15, "K_D": 0.05, "dt": DT})
        _bridge_post(f"/transfers/{self.request_id}/merging")
        self.state = TransferState.LC; self.ticks = 0; print(f"[transfer] Split target {t['vehicle_id']} -> LC started")
        return True

    def update(self):
        if not self.detached_v or self.state in (TransferState.CRUISE, TransferState.DONE): return
        self.ticks += 1
        tail = self.rec_p[-1]; off = signed_longitudinal_offset(tail, self.detached_v); rs = tail.speed * 3.6
        
        if self.state == TransferState.LC:
            # Move at most one lane per step; never aim diagonally across multiple lanes.
            rec_wpt = self.cmap.get_waypoint(tail._carla_vehicle.get_location())
            target_wpt = _one_lane_step_target(self.cmap, self.detached_v, rec_wpt, off)
            if not target_wpt: target_wpt = rec_wpt
            target_wpt = _advance_waypoint(target_wpt, 18.0)
            
            v_cmd = rs + float(np.clip((off - TARGET_GAP_M) * 0.85, -10.0, 16.0))
            v_cmd = max(MERGE_MIN_SPEED_KMH, v_cmd)
            control = self.pid.run_step(float(v_cmd), target_wpt)
            control.hand_brake = False
            self.detached_v._carla_vehicle.apply_control(control)
            
            rec_now = self.cmap.get_waypoint(tail._carla_vehicle.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving)
            ego_now = self.cmap.get_waypoint(self.detached_v._carla_vehicle.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving)
            if _same_lane(ego_now, rec_now) and abs(signed_lateral_offset(tail, self.detached_v)) < LANE_STEP_COMPLETE_M and off > 5.0:
                print(f"[transfer] LC complete -> JOINING off={off:.1f}m")
                self.finalize_join()

        elif self.state == TransferState.FOLLOW:
            rec_wpt = self.cmap.get_waypoint(tail._carla_vehicle.get_location())
            target_wpt = _align_reference_waypoint(rec_wpt, off)
            if not target_wpt: target_wpt = rec_wpt
            target_wpt = _advance_waypoint(target_wpt, 18.0)
            v_cmd = rs + float(np.clip((off - TARGET_GAP_M) * 0.9, -10.0, 16.0))
            v_cmd = max(MERGE_MIN_SPEED_KMH, v_cmd)
            control = self.pid.run_step(float(v_cmd), target_wpt)
            control.hand_brake = False
            self.detached_v._carla_vehicle.apply_control(control)
            if off >= TARGET_GAP_M - 1.0 and tail.distance_to(self.detached_v) < TARGET_GAP_M + 2.0:
                self.finalize_join()

    def finalize_join(self):
        v = self.detached_v; self.rec_p.attach_tail_vehicle(v)
        _set_follow_gap(v, NORMAL_FOLLOW_GAP_M)
        v.attach_controller(PlatooningControllers.FollowerController(v, v_ref_cacc, self.rec_p, dependencies=[-1, 0]))
        if self.detached_p: self.rec_p.simulation.remove_platoon(self.detached_p)
        if self.request_id: _bridge_post(f"/transfers/{self.request_id}/carla_complete")
        self.state = TransferState.DONE; print(">>> JOIN COMPLETE")

    def status_line(self):
        if self.state == TransferState.DONE:
            return "DONE"
        if self.state == TransferState.CRUISE:
            return self.last_wait_reason or "idle"
        if self.detached_v and self.rec_p:
            tail = self.rec_p[-1]
            off = signed_longitudinal_offset(tail, self.detached_v)
            lat = signed_lateral_offset(tail, self.detached_v)
            return f"{self.state.name} off={off:.1f} lat={lat:.1f} dist={tail.distance_to(self.detached_v):.1f}"
        return self.state.name

def main():
    _bridge_reload()
    sim = Core.Simulation(world="Town06", dt=DT, synchronous=True); cmap = sim.map; bps = sim.get_vehicle_blueprints(); bp = bps.filter("vehicle.carlamotors.european_hgv")[0]
    tm = sim.get_trafficmanager(); tm.set_synchronous_mode(True); tm_port = tm.get_port()
    p1 = build_truck_platoon(sim, bp, P1_SPAWN, "platoon_a", SYNC_SPEED_KMH, tm, tm_port)
    p2 = build_truck_platoon(sim, bp, P2_SPAWN, "platoon_b", SYNC_SPEED_KMH, tm, tm_port)
    for p in (p1, p2): p[0].controller.waypoints_ahead = compute_lead_route(cmap, p[0].get_location())
    coord = BidirectionalTransferCoordinator({"platoon_a": p1, "platoon_b": p2}, tm, tm_port, cmap)
    _start_trigger_server(); kb = KeyInput(); step = 0; camera = SmoothCamera(sim.spectator)
    active_transfer = None

    def platoon_speeds(platoon):
        return ",".join(f"{v.speed*3.6:4.1f}" for v in platoon)

    def platoon_gaps(platoon):
        vehicles = list(platoon)
        if len(vehicles) < 2:
            return "-"
        return ",".join(f"{vehicles[i].distance_to(vehicles[i+1]):4.1f}" for i in range(len(vehicles)-1))

    def apply_approach_control():
        if not active_transfer or coord.state != TransferState.CRUISE:
            return
        donor = coord.platoons[active_transfer["from"]]
        receiver = coord.platoons[active_transfer["to"]]
        idx = min(active_transfer["idx"], len(donor) - 1)
        target = donor[idx]
        pm = compute_pair_metrics(cmap, receiver[-1], target)
        err = TARGET_GAP_M - pm["offset_m"]
        donor_speed = SYNC_SPEED_KMH
        receiver_speed = SYNC_SPEED_KMH
        if err > READY_OFFSET_TOL_M:
            receiver_speed = APPROACH_FAST_KMH
        elif err < -READY_OFFSET_TOL_M:
            donor_speed = APPROACH_FAST_KMH
        else:
            donor_speed = receiver_speed = SYNC_SPEED_KMH
        donor[0].controller.set_target_speed(donor_speed)
        receiver[0].controller.set_target_speed(receiver_speed)

    def active_transfer_from_bridge(t):
        if not t:
            return None
        fp = t.get("from_platoon_id")
        tp = t.get("to_platoon_id")
        vid = t.get("vehicle_id")
        if fp not in coord.platoons or tp not in coord.platoons or not vid:
            return None
        for i, _ in enumerate(coord.platoons[fp]):
            if f"{fp}_truck{i}" == vid:
                return {"vehicle_id": vid, "from": fp, "to": tp, "idx": i}
        return None
    
    try:
        while True:
            if step * DT > 600.0: break
            key = kb.read()
            if key in ("1","2","3","4") and coord.state == TransferState.CRUISE:
                m = {"1":("platoon_a_truck2","platoon_a","platoon_b",2), "2":("platoon_a_truck1","platoon_a","platoon_b",1), "3":("platoon_b_truck2","platoon_b","platoon_a",2), "4":("platoon_b_truck1","platoon_b","platoon_a",1)}
                vid, fp, tp, idx = m[key]; _auto_bridge_commit(vid, fp, tp)
                active_transfer = {"vehicle_id": vid, "from": fp, "to": tp, "idx": idx}
                print(f"[trigger] {key}: {vid} 이송 시작")

            if coord.state == TransferState.CRUISE:
                if _merge_trigger_event.is_set():
                    _merge_trigger_event.clear()
                    print("[trigger] bridge HTTP trigger received")
                if not active_transfer:
                    bridge_transfer = active_transfer_from_bridge(_bridge_get_negotiating_transfer())
                    if bridge_transfer:
                        active_transfer = bridge_transfer
                        print(f"[trigger] bridge negotiation detected: {active_transfer['vehicle_id']} pre-align started")

            apply_approach_control()
            coord.try_start(step); coord.update()
            sim.run_step(mode="sample" if step % SAMPLING_RATE == 0 else "control")
            sim.tick()
            camera.update(coord.camera_target()) # Restore Follow Cam
            if step % 100 == 0:
                print(
                    f"t={step * DT:6.1f}s "
                    f"A=({platoon_speeds(p1)}) "
                    f"Agap=({platoon_gaps(p1)}) "
                    f"B=({platoon_speeds(p2)}) "
                    f"Bgap=({platoon_gaps(p2)}) "
                    f"state={coord.status_line()}"
                )
            step += 1
    finally:
        kb.restore()
        sim.release_synchronous()

class SmoothCamera:
    def __init__(self, s): self.s = s; self.x=self.y=None
    def update(self, t):
        loc = t.get_location()
        if self.x is None: self.x, self.y = loc.x, loc.y
        self.x += 0.05 * (loc.x - self.x); self.y += 0.05 * (loc.y - self.y)
        self.s.set_transform(carla.Transform(carla.Location(x=self.x, y=self.y, z=loc.z+85), carla.Rotation(pitch=-90)))

if __name__ == "__main__": main()
