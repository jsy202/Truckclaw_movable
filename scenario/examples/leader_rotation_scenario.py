#!/usr/bin/env python3
"""
Leader Rotation Scenario — CARLA 0.9.13 + OpenClaw 선두 이전
=============================================================
improve(two_platoon_truck_scenario.py) 로직 베이스.
vehicle: vehicle.carlamotors.european_hgv
spawn:   simulation.json p1_spawn (x=81, y=136)

군집: truck0(선두) → truck1 → truck2(후미)  3대

선두 교체 흐름:
  1. 키보드 'L' 또는 HTTP POST :18803/leader_rotation
  2. OpenClaw 세션 이전 시작 (백그라운드): truck0 → truck1
  3. CARLA: truck0 갭 확보 → 옆 차선 이동 → 감속 → truck2 뒤 합류
  4. 합류 완료 → 브리지 /leader_rotation complete 호출
  5. truck0 openclaw 컨테이너 삭제

포트:
  18801 — 브리지 서버
  18802 — merge 트리거 (호환)
  18803 — 선두 교체 트리거
"""
from __future__ import annotations

import glob, json, math, os, select, sys, termios, threading, tty
import urllib.error, urllib.request, time
from collections import deque
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── CARLA 0.9.6 경로 ─────────────────────────────────────────────────────────
# CARLA_EGG = "/opt/carla-0.9.6/PythonAPI/carla/dist/carla-0.9.6-py3.5-linux-x86_64.egg"
# CARLA_API = "/opt/carla-0.9.6/PythonAPI/carla"
# for p in (CARLA_EGG, CARLA_API):
#     if p not in sys.path: sys.path.insert(0, p)

import carla
import numpy as np
from agents.navigation import controller as nav_controller

_PROJECT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT / "scenario" / "src"))
sys.path.insert(0, str(_PROJECT / "openclaw_migration"))
from PlatooningSimulator import Core, PlatooningControllers
from PlatooningSimulator.PlatooningControllers import LeadNavigator, FollowerController

# ── config (improve simulation.json 그대로) ──────────────────────────────────
_CFG  = json.loads((_PROJECT / "config" / "simulation.json").read_text())
_spd  = _CFG["speeds"]; _gap = _CFG["gaps"]; _sp = _CFG["spawns"]

DT                  = 0.01
SAMPLING_RATE       = 10
PLATOON_SIZE        = 3
SYNC_SPEED_KMH      = 20.0
MERGE_MIN_SPEED_KMH = 15.0
NORMAL_FOLLOW_GAP_M = 15.0                                   # ⚠️ 12 -> 15 (고속 대응)
OPEN_GAP_M          = 25.0                                   # ⚠️ 30 -> 25 (단축)
OPEN_GAP_READY_M    = 20.0                                   # ⚠️ 25 -> 20 (단축)
TARGET_GAP_M        = 13.0
PLATOON_SPACING_M   = 20.0                                   # ⚠️ 18 -> 20
LANE_STEP_COMPLETE_M = 0.9
GAP_STABLE_TICKS    = 10                                     # ⚠️ 50 -> 10 (빠른 전환)

BRIDGE_URL      = "http://127.0.0.1:18801"
TRIGGER_PORT    = 18802
LEADER_ROT_PORT = 18803

# ── 스폰 (improve p1_spawn 그대로) ──────────────────────────────────────────
_p1 = _sp["p1_spawn"]
PLATOON_SPAWN = carla.Transform(
    carla.Location(x=_p1["x"], y=_p1["y"], z=_p1["z"]),
    carla.Rotation(pitch=_p1["pitch"], yaw=_p1["yaw"], roll=_p1["roll"]),
)

# ── 이벤트 ────────────────────────────────────────────────────────────────────
_leader_rotation_event  = threading.Event()
_rotation_complete_event = threading.Event()

# ── improve 헬퍼 함수 그대로 ──────────────────────────────────────────────────
def _yaw_diff(a, ref):
    return abs((a - ref + 180.0) % 360.0 - 180.0)

def _select_straight(cands, yaw):
    return min(cands, key=lambda w: _yaw_diff(w.transform.rotation.yaw, yaw)) if cands else None

def _advance_waypoint(wpt, dist):
    cur = wpt; rem = float(dist)
    while rem > 0.0:
        step = min(10.0, rem)
        nxt = cur.next(step)
        if not nxt: return cur
        cur = _select_straight(nxt, cur.transform.rotation.yaw)
        rem -= step
    return cur

def _retreat_waypoint(wpt, dist):
    cur = wpt; rem = float(dist)
    while rem > 0.0:
        step = min(10.0, rem)
        nxt = cur.previous(step)
        if not nxt: return None
        cur = _select_straight(nxt, cur.transform.rotation.yaw)
        rem -= step
    return cur

def _spawn_from_waypoint(wpt):
    t = wpt.transform
    return carla.Transform(
        carla.Location(x=t.location.x, y=t.location.y, z=t.location.z + 0.3),
        t.rotation,
    )

def _driving_adjacent_lanes(wpt):
    lanes = []
    for getter in (wpt.get_left_lane, wpt.get_right_lane):
        try: lane = getter()
        except RuntimeError: lane = None
        if lane and lane.lane_type == carla.LaneType.Driving:
            lanes.append(lane)
    return lanes

def _same_lane(a, b):
    return bool(a and b and a.road_id == b.road_id and a.lane_id == b.lane_id)

def _lane_distance(a, b):
    al = a.transform.location; bl = b.transform.location
    return float(np.hypot(al.x - bl.x, al.y - bl.y))

def signed_longitudinal_offset(ref, ego):
    rl = ref._carla_vehicle.get_location(); el = ego._carla_vehicle.get_location()
    yaw = math.radians(ref._carla_vehicle.get_transform().rotation.yaw)
    fwd = np.array([math.cos(yaw), math.sin(yaw)])
    return float(np.dot(np.array([rl.x - el.x, rl.y - el.y]), fwd))

def signed_lateral_offset(ref, ego):
    rl = ref._carla_vehicle.get_location(); el = ego._carla_vehicle.get_location()
    yaw = math.radians(ref._carla_vehicle.get_transform().rotation.yaw)
    side = np.array([math.sin(yaw), -math.cos(yaw)])
    return float(np.dot(np.array([el.x - rl.x, el.y - rl.y]), side))

def _set_gap(v, g): v.desired_gap_m = float(g)

def v_ref_cacc(pre, ego):
    gap = ego.distance_to(pre); vp = pre.speed; ve = ego.speed
    desired = getattr(ego, "desired_gap_m", NORMAL_FOLLOW_GAP_M)
    v = vp + 0.55 * (gap - desired) + 0.80 * (vp - ve)
    return float(np.clip(v * 3.6, max(5.0, vp * 3.6 - 14.0), vp * 3.6 + 20.0))

def compute_lead_route(cmap, loc, dist=3000.0, step=5.0):
    route = deque()
    cur = cmap.get_waypoint(loc, project_to_road=True, lane_type=carla.LaneType.Driving)
    if not cur: return route
    route.append(cur); rem = dist
    while rem > 0:
        nxt = cur.next(min(step, rem))
        if not nxt: break
        cur = _select_straight(nxt, cur.transform.rotation.yaw)
        route.append(cur); rem -= step
    return route

def _make_pid(carla_vehicle):
    lat = {"K_P": 5.5, "K_I": 0.2,  "K_D": 0.4, "dt": DT}   # ⚠️ K_P 4.5 -> 5.5 (더욱 강력한 조향)
    lon = {"K_P": 1.0, "K_I": 0.2,  "K_D": 0.05, "dt": DT}  # ⚠️ 종방향 응답성 더욱 강화
    return nav_controller.VehiclePIDController(
        carla_vehicle, args_lateral=lat, args_longitudinal=lon,
        max_brake=0.4, max_throttle=1.0,                    # ⚠️ 가속력 최대
    )


# ── 브리지 헬퍼 ───────────────────────────────────────────────────────────────
import subprocess

def _get_docker_status():
    """각 트럭별 docker 상태 반환."""
    result = {"truck0": "없음", "truck1": "없음"}
    try:
        for name in ("openclaw-truck0", "openclaw-truck1"):
            r = subprocess.run(
                ["docker", "ps", "--all", "--filter", f"name=^/{name}$",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=1)
            s = r.stdout.strip()
            key = "truck0" if "truck0" in name else "truck1"
            result[key] = s if s else "없음"
    except Exception:
        pass
    return result

def _bridge_post(path, body=None):
    try:
        req = urllib.request.Request(
            BRIDGE_URL + path,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception: return None

def _bridge_reload():
    res = _bridge_post("/reload", {})
    print("[bridge] 상태 리셋 완료" if res else "[bridge] 서버 응답 없음")

# ── HTTP 트리거 서버 ──────────────────────────────────────────────────────────
def _start_trigger_servers():
    class MergeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.end_headers()
            print("\n[18802] merge 트리거 (무시 — leader_rotation 시나리오)")
        def log_message(self, *a): pass

    class LeaderRotHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.end_headers()
            if self.path.rstrip("/") in ("/leader_rotation", "/start_merge"):
                _leader_rotation_event.set()
                print("\n[18803] 선두 교체 트리거 수신!")
            elif "/complete" in self.path:
                _rotation_complete_event.set()
        def log_message(self, *a): pass

    for port, handler in [(TRIGGER_PORT, MergeHandler), (LEADER_ROT_PORT, LeaderRotHandler)]:
        def _serve(p=port, h=handler):
            try: HTTPServer(("0.0.0.0", p), h).serve_forever()
            except OSError as e: print(f"[trigger] 포트 {p} 실패: {e}")
        threading.Thread(target=_serve, daemon=True).start()
    print(f"[trigger] 18802 + {LEADER_ROT_PORT} 대기 중")

# ── 군집 빌드 (improve build_truck_platoon 그대로) ────────────────────────────
def build_platoon(sim, bp, spawn):
    p = Core.Platoon(sim)

    # truck0: 선두 (LeadNavigator)
    lead = p.add_lead_vehicle(bp, spawn); sim.tick()
    lead.attach_controller(PlatooningControllers.LeadNavigator(lead, initial_speed=SYNC_SPEED_KMH))
    anchor_wpt = sim.map.get_waypoint(
        lead.get_location(), project_to_road=True, lane_type=carla.LaneType.Driving
    )

    # truck1: FollowerController (일단 truck0 추종)
    fwpt = _retreat_waypoint(anchor_wpt, PLATOON_SPACING_M)
    fsp  = _spawn_from_waypoint(fwpt) if fwpt else spawn
    truck1 = p.add_follower_vehicle(bp, fsp)
    _set_gap(truck1, NORMAL_FOLLOW_GAP_M)
    truck1.attach_controller(PlatooningControllers.FollowerController(
        truck1, v_ref_cacc, p, dependencies=[-1, 0]
    ))
    sim.tick()

    # truck2: FollowerController (truck1 추종)
    fwpt2 = _retreat_waypoint(anchor_wpt, PLATOON_SPACING_M * 2)
    fsp2  = _spawn_from_waypoint(fwpt2) if fwpt2 else spawn
    truck2 = p.add_follower_vehicle(bp, fsp2)
    _set_gap(truck2, NORMAL_FOLLOW_GAP_M)
    truck2.attach_controller(PlatooningControllers.FollowerController(
        truck2, v_ref_cacc, p, dependencies=[-1, 0]
    ))
    sim.tick()

    p.store_follower_waypoints()
    return p
    p.lead_waypoints.append(anchor_wpt)
    return p

# ── 선두 교체 상태 머신 ───────────────────────────────────────────────────────
class RotState(Enum):
    CRUISE   = auto()
    MIGRATE  = auto()
    GAP      = auto()
    LC       = auto()
    SLOWDOWN = auto()
    REJOIN   = auto()
    DONE     = auto()

class LeaderRotationCoordinator:
    """
    improve의 BidirectionalTransferCoordinator 패턴을 따름.
    truck0(선두) → 후미 이동.
    CRUISE → MIGRATE → GAP → LC → SLOWDOWN → REJOIN → DONE
    """
    def __init__(self, platoon, cmap, sim, migrator=None):
        self.platoon   = platoon
        self.cmap      = cmap
        self.sim       = sim
        self.migrator  = migrator
        self.state     = RotState.CRUISE
        self.triggered = False
        self._v         = None    # truck0 (분리된 선두)
        self._v_platoon = None    # truck0를 담은 임시 군집
        self._pid       = None
        self._ticks     = 0
        self._gap_ok    = 0
        self._original_lane_id = None
        self._target_lane_id = None
        self._target_lane_wpt = None
        self._rejoin_target_lane_wpt = None
        self.last_status = "idle"

    def trigger(self):
        if self.state == RotState.CRUISE:
            self.triggered = True

    def camera_target(self):
        if self._v and self.state not in (RotState.CRUISE, RotState.MIGRATE):
            return self._v._carla_vehicle
        return self.platoon[0]._carla_vehicle

    def update(self, step):
        if self.state == RotState.CRUISE:
            if self.triggered: self._start_migrate()
        elif self.state == RotState.MIGRATE:
            if self.migrator and self.migrator.wait(timeout=0):
                print("[rotation] OpenClaw 이전 완료 → GAP 확보")
                self.state = RotState.GAP
            elif self.migrator is None:
                self.state = RotState.GAP
        elif self.state == RotState.GAP:    self._update_gap()
        elif self.state == RotState.LC:     self._update_lc()
        elif self.state == RotState.SLOWDOWN: self._update_slowdown()
        elif self.state == RotState.REJOIN: self._update_rejoin()

    def _start_migrate(self):
        print("\n[rotation] 선두 교체 트리거!")
        _bridge_post("/leader_rotation", {"old_leader":"truck0","new_leader":"truck1","status":"started"})
        
        if self.migrator:
            self.migrator.migrate(blocking=False)
            self.state = RotState.MIGRATE
        else:
            print("[rotation] migrator 없음 — OpenClaw 이전 스킵")
            self.state = RotState.GAP

    def _update_gap(self):
        if self._v is None:
            # truck0 분리 (index 0)
            self._v_platoon, _ = self.platoon.split(0, 0)
            self._v = self._v_platoon[0]
            
            # 시뮬레이션 루프에서 중복 제어 방지 (경고 제거)
            self.sim.remove_platoon(self._v_platoon)
            
            # truck0 제어를 위해 PID 생성
            self._v.attach_controller(None)
            self._pid = _make_pid(self._v._carla_vehicle)
            
            # truck1(새 리더) 경로 재설정 — 속도 유지 (truck0만 감속)
            new_lead = self.platoon[0]
            if not isinstance(new_lead.controller, LeadNavigator):
                nav = LeadNavigator(new_lead._carla_vehicle, initial_speed=SYNC_SPEED_KMH)
                new_lead.attach_controller(nav)
            else:
                new_lead.controller.set_target_speed(SYNC_SPEED_KMH)

            new_lead.controller.waypoints_ahead = compute_lead_route(self.cmap, new_lead.get_location())

            print(f"[rotation] truck0 분리. truck1이 새로운 리더로 승격됨 (정속 유지).")
            self.last_status = "gap_opening"
            return

        # truck1(새 리더)과의 간격 확보
        new_lead = self.platoon[0]
        gap = new_lead.distance_to(self._v)
        
        if gap >= 12.0: self._gap_ok += 1                    # ⚠️ 20m -> 12m (너무 멀어지지 않게)
        else: self._gap_ok = 0

        ego_wpt = self.cmap.get_waypoint(self._v.get_location())
        if ego_wpt:
            target_wpt = _advance_waypoint(ego_wpt, 10.0)
            # 가속해서 차선 변경 공간 확보
            v_cmd = SYNC_SPEED_KMH * 1.3 
            ctrl = self._pid.run_step(v_cmd, target_wpt)
            self._v.apply_control(ctrl)

        self.last_status = f"gap={gap:.1f}/12.0m ok={self._gap_ok}"
        if self._gap_ok >= GAP_STABLE_TICKS:
            print(f"[rotation] 간격 확보 완료 ({gap:.1f}m) → LC 시작")
            self._start_lc()

    def _start_lc(self):
        ego_wpt = self.cmap.get_waypoint(self._v.get_location())
        self._original_lane_id = (ego_wpt.road_id, ego_wpt.lane_id)
        
        adj = _driving_adjacent_lanes(ego_wpt)
        if adj:
            self._target_lane_wpt = adj[0]
            self._target_lane_id = (self._target_lane_wpt.road_id, self._target_lane_wpt.lane_id)
            print(f"[rotation] LC 시작: {self._original_lane_id} → {self._target_lane_id}")
        else:
            print("[rotation] 에러: 인접 차선 없음! 제자리에서 SLOWDOWN 시도")
            self._target_lane_wpt = ego_wpt
            self._target_lane_id = self._original_lane_id

        self._ticks = 0
        self.state = RotState.LC

    def _update_lc(self):
        self._ticks += 1
        ego_loc = self._v.get_location()
        ego_wpt = self.cmap.get_waypoint(ego_loc)
        
        target_wpt = _advance_waypoint(self._target_lane_wpt, 8.0)  # ⚠️ 15m -> 8m (더욱 급격하게 꺾음)
        self._target_lane_wpt = _advance_waypoint(self._target_lane_wpt, self._v.speed * DT)
        
        v_cmd = SYNC_SPEED_KMH * 1.5                               # ⚠️ 1.3 -> 1.5 (최대한 빠르게 이탈)
        ctrl = self._pid.run_step(v_cmd, target_wpt)
        self._v.apply_control(ctrl)

        # ⚠️ 차선 변경 완료 판정: 3.5m 정도면 충분히 옆 차선 안착
        lat_dist = abs(signed_lateral_offset(self.platoon[0], self._v))
        
        if (ego_wpt.road_id == self._target_lane_id[0] and 
            ego_wpt.lane_id == self._target_lane_id[1] and 
            lat_dist > 3.0):                                 # ⚠️ 3.5 -> 3.0 (빠른 판정)
            print(f"[rotation] 차선 변경 완전 완료 (lat={lat_dist:.1f}m) → SLOWDOWN")
            
            # ⚠️ 여기서 속도 제한 해제하면 안됨 (합류할 때까지 천천히 가야 함)
            
            self.state = RotState.SLOWDOWN
            self._ticks = 0
            return

        # 타임아웃 대폭 확대 (충분히 기다림)
        if self._ticks > 5000:
            print(f"[rotation] LC 타임아웃 경고 → SLOWDOWN 강제 전환")
            self.state = RotState.SLOWDOWN
            self._ticks = 0

        self.last_status = f"LC lat={lat_dist:.1f} ticks={self._ticks}"

    def _update_slowdown(self):
        self._ticks += 1
        tail = self.platoon[-1]
        off = signed_longitudinal_offset(tail, self._v)

        target_wpt = _advance_waypoint(self._target_lane_wpt, 20.0)
        self._target_lane_wpt = _advance_waypoint(self._target_lane_wpt, self._v.speed * DT)
        
        # 확실하게 뒤로 처지도록 최소속도로 감속
        v_cmd = max(5.0, tail.speed * 3.6 * 0.2)
        ctrl = self._pid.run_step(float(v_cmd), target_wpt)
        self._v.apply_control(ctrl)

        self.last_status = f"SLOWDOWN off={off:.1f}m lat={abs(signed_lateral_offset(tail, self._v)):.1f}"

        # 충분한 여유 간격 확보 후 합류 (NORMAL_FOLLOW_GAP_M + 여유)
        if off >= NORMAL_FOLLOW_GAP_M + 10.0 or self._ticks > 8000:
            print(f"[rotation] 후방 위치 확보 (off={off:.1f}m) → REJOIN 시작")
            self._ticks = 0
            self.state = RotState.REJOIN

    def _update_rejoin(self):
        self._ticks += 1
        tail = self.platoon[-1]
        ego_loc = self._v.get_location()
        ego_wpt = self.cmap.get_waypoint(ego_loc)
        
        # ⚠️ 위치 오차 계산 (v_cmd 결정에 필요)
        lat_off = abs(signed_lateral_offset(tail, self._v))
        off = signed_longitudinal_offset(tail, self._v)

        # ⚠️ 동적 타겟팅: 현재 위치 기준으로 목표 차선의 전방 지점 추적
        adj = _driving_adjacent_lanes(ego_wpt)
        target_lane_wpt = ego_wpt
        for a in adj:
            if a.lane_id == self._original_lane_id[1]:
                target_lane_wpt = a
                break
        
        target_wpt = _advance_waypoint(target_lane_wpt, 15.0) # ⚠️ 합류 시 더 급격한 조향
        
        # 목표 간격(NORMAL_FOLLOW_GAP_M) 기준 속도 조절
        # off > target: 가속 / off < target: 감속 (충돌 방지)
        speed_bonus = np.clip((off - NORMAL_FOLLOW_GAP_M) * 1.5, -SYNC_SPEED_KMH * 0.8, SYNC_SPEED_KMH * 2.0)
        v_cmd = max(3.0, tail.speed * 3.6 + speed_bonus)
        
        ctrl = self._pid.run_step(float(v_cmd), target_wpt)
        self._v.apply_control(ctrl)

        self.last_status = f"REJOIN off={off:.1f}m lat={lat_off:.1f}"

        # 원래 차선 복귀 완료 판정 (횡방향 오차 0.8m 이내로 강화 - 안정성 확보)
        if (ego_wpt.lane_id == self._original_lane_id[1] and lat_off < 0.8):
            print(f"[rotation] 원래 차선 복귀 완료 (lat={lat_off:.1f}m) → FINALIZING")
            self._finalize_join()

    def _finalize_join(self):
        self.platoon.merge(self._v_platoon)
        
        # 합류 완료 후 전체 대열 속도 정상화
        self.platoon[0].controller.set_target_speed(SYNC_SPEED_KMH)
        
        new_follower = self.platoon[-1]
        new_follower.attach_controller(FollowerController(
            new_follower, v_ref_cacc, self.platoon, dependencies=[-1, 0]
        ))
        
        _bridge_post("/leader_rotation", {"old_leader":"truck0","new_leader":"truck1","status":"complete"})
        _rotation_complete_event.set()
        self.state = RotState.DONE
        print("[rotation] >>> 선두 교체 및 후미 합류 완료!")

    def status_line(self):
        return f"{self.state.name} {self.last_status}"

# ── openclaw cleanup ─────────────────────────────────────────────────────────
def _start_cleanup_watcher(old_container):
    def _watch():
        _rotation_complete_event.wait()
        time.sleep(2.0)
        try:
            from replicator import delete_old_openclaw
            delete_old_openclaw(old_container)
        except Exception as e:
            print(f"[cleanup] 실패: {e}")
    threading.Thread(target=_watch, daemon=True).start()

# ── SmoothCamera (improve 그대로) ────────────────────────────────────────────
class SmoothCamera:
    def __init__(self, s): self.s = s; self.x = self.y = None
    def update(self, t):
        loc = t.get_location()
        if self.x is None: self.x, self.y = loc.x, loc.y
        self.x += 0.05 * (loc.x - self.x); self.y += 0.05 * (loc.y - self.y)
        self.s.set_transform(carla.Transform(
            carla.Location(x=self.x, y=self.y, z=loc.z + 60),
            carla.Rotation(pitch=-90),
        ))

# ── KeyInput ─────────────────────────────────────────────────────────────────
class KeyInput:
    def __init__(self):
        self._active = False
        try:
            self._fd = sys.stdin.fileno(); self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd); self._active = True
            print("[keys] 'L'=선두교체  Ctrl-C=종료")
        except termios.error:
            print("[keys] TTY 없음 — 키보드 비활성화")
    def read(self):
        if not self._active: return ""
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\x03": self.restore(); raise KeyboardInterrupt
            return ch
        return ""
    def restore(self):
        if self._active:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old); self._active = False

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--no-openclaw",    action="store_true")
    p.add_argument("--auto-trigger-s", type=float, default=0.0)
    args = p.parse_args()

    # ── watch 스크립트 로그 초기화 (이전 실행 잔존 방지) ─────────────────────
    _watch_logs = [
        _PROJECT / ".transfer" / "telemetry.log",
        _PROJECT / ".transfer" / "tx" / ".progress.log",
        _PROJECT / ".transfer" / "rx" / ".progress.log",
    ]
    for _p in _watch_logs:
        try:
            if _p.exists():
                _p.write_text("")
        except Exception:
            pass

    _bridge_reload()
    _start_trigger_servers()

    # CARLA 초기화 (improve 패턴)
    sim  = Core.Simulation(world="Town06", dt=DT, synchronous=True)
    # 어두운 석양 날씨
    sim.world.set_weather(carla.WeatherParameters.CloudySunset)

    cmap = sim.map
    bps  = sim.get_vehicle_blueprints()
    
    # ⚠️ 철통 로직: 여러 후보 중 존재하는 첫 번째 트럭 모델 선택
    truck_candidates = [
        "vehicle.carlamotors.european_hgv",
        "vehicle.mercedes-benz.actros",
        "vehicle.carlamotors.carlacola",
        "vehicle.carlamotors.firetruck"
    ]
    
    bp = None
    for cand in truck_candidates:
        found = bps.filter(cand)
        if found:
            bp = found[0]
            break
            
    if not bp:
        # 최후의 수단: 이름에 'truck'이 들어간 아무 모델이나 선택
        bp = bps.filter("*truck*")[0]
        
    print(f"[main] blueprint selected: {bp.id}")

    platoon = build_platoon(sim, bp, PLATOON_SPAWN)
    platoon[0].controller.waypoints_ahead = compute_lead_route(cmap, platoon[0].get_location())
    print(f"[main] 군집 생성: {PLATOON_SIZE}대  spawn=({_p1['x']},{_p1['y']})")
    print(f"  truck0(선두) → truck1 → truck2(후미)")

    # OpenClaw LeaderMigrator
    migrator = None
    if not args.no_openclaw:
        try:
            from replicator import LeaderMigrator
            migrator = LeaderMigrator(
                old_truck_id="truck0", new_truck_id="truck1",
                new_agent_dir=_PROJECT / "agents" / "truck1",
                old_openclaw_data_dir=_PROJECT / ".openclaw-truck0",
                new_openclaw_data_dir=_PROJECT / ".openclaw-truck1",
            )
            print("[main] LeaderMigrator 준비 완료")
        except ImportError:
            print("[main] replicator 없음 — OpenClaw 스킵")

    coord  = LeaderRotationCoordinator(platoon, cmap, sim, migrator=migrator)
    camera = SmoothCamera(sim.spectator)
    kb     = KeyInput()

    step = 0; auto_triggered = False; cleanup_done = False

    def speeds():
        # 분리 전후 모두 안전하게 출력되도록 수정
        parts = []
        if coord._v:
            parts.append(f"t0={coord._v.speed*3.6:.1f}")
        
        # 현재 메인 platoon에 속한 차량들 출력 (truck1, truck2 등)
        for i, v in enumerate(coord.platoon):
            idx = i + 1 if coord._v else i
            parts.append(f"t{idx}={v.speed*3.6:.1f}")
            
        return ", ".join(parts) if parts else "idle"

    def gaps():
        # 분리된 truck0와 새 리더(truck1) 사이의 간격 포함
        parts = []
        if coord._v and len(coord.platoon) > 0:
            parts.append(f"{coord.platoon[0].distance_to(coord._v):.1f}*")
        
        vs = list(coord.platoon)
        for i in range(len(vs)-1):
            parts.append(f"{vs[i].distance_to(vs[i+1]):.1f}")
            
        return ", ".join(parts) if parts else "-"

    try:
        while True:
            if step * DT > 600.0: break

            key = kb.read()
            if key.lower() == "l" and coord.state == RotState.CRUISE:
                print("\n[키] L — 선두 교체 트리거"); coord.trigger()

            if (args.auto_trigger_s > 0 and not auto_triggered
                    and step * DT >= args.auto_trigger_s and coord.state == RotState.CRUISE):
                print(f"\n[auto] {args.auto_trigger_s}s — 자동 트리거")
                coord.trigger(); auto_triggered = True

            if _leader_rotation_event.is_set() and coord.state == RotState.CRUISE:
                _leader_rotation_event.clear(); coord.trigger()

            coord.update(step)
            sim.run_step(mode="sample" if step % SAMPLING_RATE == 0 else "control")
            sim.tick()
            camera.update(coord.camera_target())

            if step % 100 == 0:
                d_status = _get_docker_status()
                t0_doc = "Up" if "Up" in d_status["truck0"] else "없음"
                t1_doc = "Up" if "Up" in d_status["truck1"] else "없음"
                rot_state = coord.state.name
                telem = (
                    f"t={step*DT:6.1f}s "
                    f"speeds=({speeds()}) "
                    f"gaps=({gaps()}) "
                    f"truck1=[openclaw-truck0:{t0_doc}] "
                    f"truck2=[openclaw-truck1:{t1_doc}] "
                    f"truck3=[none] "
                    f"state={rot_state}"
                )
                print(telem)
                _telem_log = _PROJECT / ".transfer" / "telemetry.log"
                _telem_log.parent.mkdir(parents=True, exist_ok=True)
                with open(_telem_log, "a") as _tf:
                    _tf.write(telem + "\n")

            if coord.state == RotState.DONE and not auto_triggered:
                print("[main] 선두 교체 완료! 계속 주행...")
                auto_triggered = True

            # ── 자리 체인지 완료 후 구 선두 docker 삭제 ────────────────────
            if coord.state == RotState.DONE and not cleanup_done:
                cleanup_done = True
                tx_log = _PROJECT / ".transfer" / "tx" / ".progress.log"
                tx_log.parent.mkdir(parents=True, exist_ok=True)
                with open(tx_log, "a") as _tf:
                    _tf.write("\n=== DELETE START ===\n")
                    _tf.write("[cleanup] openclaw-truck0 컨테이너 삭제 중...\n")
                try:
                    from replicator import delete_old_openclaw
                    delete_old_openclaw("openclaw-truck0")
                    with open(tx_log, "a") as _tf:
                        _tf.write("[cleanup] openclaw-truck0 삭제 완료 ✓\n")
                except Exception as _e:
                    with open(tx_log, "a") as _tf:
                        _tf.write(f"[cleanup] 삭제 실패: {_e}\n")

            step += 1
    finally:
        kb.restore()
        sim.release_synchronous()
        # watch 스크립트 로그 초기화 (Ctrl+C 후 재시작 시 잔존 방지)
        for _p in _watch_logs:
            try:
                if _p.exists():
                    _p.write_text("")
            except Exception:
                pass

if __name__ == "__main__":
    main()
