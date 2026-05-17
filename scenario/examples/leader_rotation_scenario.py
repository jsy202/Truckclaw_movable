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

# ── CARLA 0.9.13 경로 ────────────────────────────────────────────────────────
CARLA_EGG = str(Path.home() / "carla-0.9.13/PythonAPI/carla/dist/carla-0.9.13-py3.7-linux-x86_64.egg")
CARLA_API = str(Path.home() / "carla-0.9.13/PythonAPI/carla")
for p in (CARLA_EGG, CARLA_API):
    if p not in sys.path: sys.path.insert(0, p)

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
SYNC_SPEED_KMH      = float(_spd["sync_speed_kmh"])          # 18
MERGE_MIN_SPEED_KMH = float(_spd["merge_min_speed_kmh"])     # 15
NORMAL_FOLLOW_GAP_M = float(_gap["normal_follow_gap_m"])     # 12
OPEN_GAP_M          = float(_gap["open_gap_m"])              # 20
OPEN_GAP_READY_M    = float(_gap["open_gap_ready_m"])        # 18
TARGET_GAP_M        = float(_gap["target_gap_m"])            # 13
PLATOON_SPACING_M   = float(_gap["platoon_spacing_m"])       # 18
LANE_STEP_COMPLETE_M = 0.9
GAP_STABLE_TICKS    = 50

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
    lat = {"K_P": 3.2, "K_I": 0.1,  "K_D": 0.25, "dt": DT}
    lon = {"K_P": 0.65, "K_I": 0.15, "K_D": 0.05, "dt": DT}
    return nav_controller.VehiclePIDController(
        carla_vehicle, args_lateral=lat, args_longitudinal=lon,
        max_brake=0.4, max_throttle=0.8,
    )

# ── 브리지 헬퍼 ───────────────────────────────────────────────────────────────
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
        self._v        = None    # truck0 (분리된 선두)
        self._pid      = None
        self._ticks    = 0
        self._gap_ok   = 0
        self._target_lane_wpt = None
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
            if self.migrator and self.migrator._done_event.is_set():
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
            # truck0 분리
            new_p, _ = self.platoon.split(0, 0)
            self._v = new_p[0]
            try: self.sim.platoons.remove(new_p)
            except ValueError: pass
            self._v.attach_controller(None)
            self._pid = _make_pid(self._v._carla_vehicle)

            # truck1이 새로운 선두가 됨 → LeadNavigator 부여
            truck1 = self.platoon[0]  # 이제 truck1이 lead_vehicle
            truck1_nav = LeadNavigator(truck1._carla_vehicle, initial_speed=SYNC_SPEED_KMH)
            truck1.attach_controller(truck1_nav)

            # ★ 중요: lead_waypoints 초기화 (truck0의 경로 제거)
            self.platoon.lead_waypoints.clear()
            # truck1의 현재 위치부터 새로운 경로 시작
            truck1_wpt = self.cmap.get_waypoint(truck1.get_location())
            if truck1_wpt:
                self.platoon.lead_waypoints.append(truck1_wpt)

            print(f"[rotation] truck0 분리 완료")
            print(f"[rotation] truck1 → LeadNavigator 부여 (독립 주행)")
            print(f"[rotation] truck2 → truck1 추종 (lead_waypoints 초기화)")
            print(f"[rotation] 잔여 군집: {len(self.platoon)}대")

            # 바로 LC 시작
            self._start_lc()

    def _update_gap(self):
        if self._v is None:
            # truck0 분리 (improve split 패턴)
            new_p, _ = self.platoon.split(0, 0)
            self._v = new_p[0]
            try: self.sim.platoons.remove(new_p)
            except ValueError: pass
            self._v.attach_controller(None)
            self._pid = _make_pid(self._v._carla_vehicle)

            # truck1을 독립 리더로 만들기 (LeadNavigator 부여)
            truck1 = self.platoon[0]
            truck1_nav = LeadNavigator(truck1._carla_vehicle, initial_speed=SYNC_SPEED_KMH)
            truck1.attach_controller(truck1_nav)
            print(f"[rotation] truck0 분리. truck1이 독립 리더로 전환 (LeadNavigator 부여)")
            print(f"[rotation] 잔여 군집: {len(self.platoon)}대")
            self.last_status = "gap_opening"
            return

        new_lead = self.platoon[0]
        gap = new_lead.distance_to(self._v)
        if gap >= OPEN_GAP_READY_M: self._gap_ok += 1
        else: self._gap_ok = 0

        ego_wpt = self.cmap.get_waypoint(
            self._v._carla_vehicle.get_location(),
            project_to_road=True, lane_type=carla.LaneType.Driving,
        )
        if ego_wpt:
            target_wpt = _advance_waypoint(ego_wpt, 20.0)
            # truck0 감속 (군집보다 느리게 → gap 확보)
            ctrl = self._pid.run_step(SYNC_SPEED_KMH * 0.6, target_wpt)
            ctrl.hand_brake = False
            self._v._carla_vehicle.apply_control(ctrl)

        self.last_status = f"gap={gap:.1f}/{OPEN_GAP_READY_M}m ok={self._gap_ok}"
        if self._gap_ok >= GAP_STABLE_TICKS:
            print(f"[rotation] 간격 {gap:.1f}m 확보 → LC 시작")
            self._start_lc()

    def _start_lc(self):
        ego_loc = self._v._carla_vehicle.get_location()
        ego_wpt = self.cmap.get_waypoint(
            ego_loc,
            project_to_road=True, lane_type=carla.LaneType.Driving,
        )

        # 목표: 오른쪽 차선으로 이동 (Y축 기준)
        # 현재 Y 좌표에서 오른쪽으로 3.5m 이동
        target_y = ego_loc.y + 3.5

        self._target_y = target_y
        self._original_y = ego_loc.y
        self._original_lane_wpt = ego_wpt

        print(f"[rotation] LC 시작: 현재 Y={ego_loc.y:.1f} → 목표 Y={target_y:.1f}")
        self._ticks = 0
        self.state = RotState.LC

    def _update_lc(self):
        if not self._v: return
        self._ticks += 1
        ego_loc = self._v._carla_vehicle.get_location()
        ego_transform = self._v._carla_vehicle.get_transform()
        yaw = math.radians(ego_transform.rotation.yaw)

        # Y축 기준 차선 변경
        current_y = ego_loc.y
        y_diff = abs(current_y - self._target_y)

        # 목표: 전방 20m 지점, Y좌표는 목표 Y로
        forward_x = ego_loc.x + 20.0 * math.cos(yaw)
        forward_z = ego_loc.z

        target_loc = carla.Location(x=forward_x, y=self._target_y, z=forward_z)
        target_wpt = self.cmap.get_waypoint(target_loc, project_to_road=True, lane_type=carla.LaneType.Driving)

        if not target_wpt:
            target_wpt = self.cmap.get_waypoint(ego_loc, project_to_road=True, lane_type=carla.LaneType.Driving)

        # 차선 변경 중에는 빠르게 이동
        ctrl = self._pid.run_step(SYNC_SPEED_KMH * 1.2, target_wpt)
        ctrl.hand_brake = False
        self._v._carla_vehicle.apply_control(ctrl)

        # 차선 변경 완료 판정: Y 좌표 차이가 0.8m 이내
        if y_diff < 0.8:
            print(f"[rotation] LC 완료 (Y={current_y:.1f}) → DONE (단순화)")
            print(f"[rotation] >>> 선두 교체 완료! truck1=선두, truck0=독립 주행")
            self.state = RotState.DONE
            self._ticks = 0
            _bridge_post("/leader_rotation", {"status":"complete"})
            return

        if self._ticks > 1000:
            print(f"[rotation] LC 타임아웃 (Y={current_y:.1f}, 목표={self._target_y:.1f}) → DONE")
            print(f"[rotation] >>> 선두 교체 완료! truck1=선두, truck0=독립 주행")
            self.state = RotState.DONE
            self._ticks = 0
            _bridge_post("/leader_rotation", {"status":"complete"})

        tail = self.platoon[-1]
        lat = signed_lateral_offset(tail, self._v)
        self.last_status = f"LC Y={current_y:.1f}/{self._target_y:.1f} diff={y_diff:.2f} ticks={self._ticks}"

    def _update_slowdown(self):
        if not self._v: return
        self._ticks += 1
        tail = self.platoon[-1]
        off  = signed_longitudinal_offset(tail, self._v)
        target_offset = -TARGET_GAP_M  # -13

        ego_wpt = self.cmap.get_waypoint(
            self._v._carla_vehicle.get_location(),
            project_to_road=True, lane_type=carla.LaneType.Driving,
        )
        if ego_wpt:
            target_wpt = _advance_waypoint(ego_wpt, 15.0)
            tail_spd = tail.speed * 3.6
            v_cmd = tail_spd - float(np.clip((off - target_offset) * 0.6, -5.0, 8.0))
            v_cmd = max(MERGE_MIN_SPEED_KMH * 0.6, v_cmd)
            ctrl = self._pid.run_step(float(v_cmd), target_wpt)
            ctrl.hand_brake = False
            self._v._carla_vehicle.apply_control(ctrl)

        self.last_status = f"SLOWDOWN off={off:.1f}"
        # off가 양수면 truck0이 truck2보다 뒤에 있음 (목표 달성)
        # 충분히 뒤로 갔으면 (예: 20m 이상) REJOIN 시작
        if off >= 20.0 or self._ticks > 3000:
            print(f"[rotation] 후방 이동 완료 off={off:.1f} → REJOIN")
            self._ticks = 0; self.state = RotState.REJOIN

    def _update_rejoin(self):
        if not self._v: return
        self._ticks += 1
        tail = self.platoon[-1]
        off  = signed_longitudinal_offset(tail, self._v)
        lat  = signed_lateral_offset(tail, self._v)

        # ego 현재 위치
        ego_wpt = self.cmap.get_waypoint(
            self._v._carla_vehicle.get_location(),
            project_to_road=True, lane_type=carla.LaneType.Driving,
        )
        tail_wpt = self.cmap.get_waypoint(
            tail._carla_vehicle.get_location(),
            project_to_road=True, lane_type=carla.LaneType.Driving,
        )

        # 같은 차선이면 tail 따라가기
        if ego_wpt and tail_wpt and _same_lane(ego_wpt, tail_wpt):
            target_wpt = _advance_waypoint(tail_wpt, 5.0)
        else:
            # 다른 차선: 원래 차선(저장해둔 차선)으로 복귀
            # LC에서 저장한 original_lane_wpt 사용
            ego_loc = self._v._carla_vehicle.get_location()
            ego_yaw = math.radians(self._v._carla_vehicle.get_transform().rotation.yaw)

            # 원래 차선의 전방 waypoint를 목표로
            if hasattr(self, '_original_lane_wpt') and self._original_lane_wpt:
                # 원래 차선을 ego 위치에서 다시 찾기 (업데이트)
                original_lane_updated = self.cmap.get_waypoint(
                    carla.Location(
                        x=ego_loc.x,
                        y=self._original_lane_wpt.transform.location.y,
                        z=ego_loc.z,
                    ),
                    project_to_road=True,
                    lane_type=carla.LaneType.Driving,
                )
                if original_lane_updated:
                    target_wpt = _advance_waypoint(original_lane_updated, 20.0)
                else:
                    # fallback: ego 전방 + 원래 차선 y좌표
                    target_wpt = self.cmap.get_waypoint(
                        carla.Location(
                            x=ego_loc.x + 20.0 * math.cos(ego_yaw),
                            y=self._original_lane_wpt.transform.location.y,
                            z=self._original_lane_wpt.transform.location.z,
                        ),
                        project_to_road=True,
                        lane_type=carla.LaneType.Driving,
                    )
            else:
                # fallback: tail 차선으로
                if tail_wpt:
                    target_wpt = self.cmap.get_waypoint(
                        carla.Location(
                            x=ego_loc.x + 20.0 * math.cos(ego_yaw),
                            y=tail_wpt.transform.location.y,
                            z=tail_wpt.transform.location.z,
                        ),
                        project_to_road=True,
                        lane_type=carla.LaneType.Driving,
                    )
                else:
                    target_wpt = _advance_waypoint(ego_wpt, 20.0) if ego_wpt else None

        if target_wpt:
            tail_spd = tail.speed * 3.6
            # tail보다 약간 빠르게
            v_cmd = tail_spd + 2.0
            v_cmd = max(MERGE_MIN_SPEED_KMH, min(v_cmd, SYNC_SPEED_KMH + 5.0))
            ctrl = self._pid.run_step(float(v_cmd), target_wpt)
            ctrl.hand_brake = False
            self._v._carla_vehicle.apply_control(ctrl)

        self.last_status = f"REJOIN off={off:.1f} lat={lat:.2f}"

        # 완료 조건: 같은 차선 + lateral < 0.9m
        joined = (
            ego_wpt and tail_wpt
            and _same_lane(ego_wpt, tail_wpt)
            and abs(lat) < LANE_STEP_COMPLETE_M
        )
        if joined or self._ticks > 4000:
            reason = "합류 완료" if joined else "타임아웃"
            print(f"[rotation] {reason}!")
            self._finalize_join()

    def _finalize_join(self):
        v = self._v
        self.platoon.attach_tail_vehicle(v)
        _set_gap(v, NORMAL_FOLLOW_GAP_M)
        v.attach_controller(PlatooningControllers.FollowerController(
            v, v_ref_cacc, self.platoon, dependencies=[-1, 0]
        ))
        _bridge_post("/leader_rotation", {"old_leader":"truck0","new_leader":"truck1","status":"complete"})
        _rotation_complete_event.set()
        self.state = RotState.DONE
        print("[rotation] >>> 선두 교체 완료! truck1=선두, truck0=후미")

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
            carla.Location(x=self.x, y=self.y, z=loc.z + 85),
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

    _bridge_reload()
    _start_trigger_servers()

    # CARLA 초기화 (improve 패턴)
    sim  = Core.Simulation(world="Town06", dt=DT, synchronous=True)
    # 어두운 석양 날씨
    sim.world.set_weather(carla.WeatherParameters.CloudySunset)

    cmap = sim.map
    bps  = sim.get_vehicle_blueprints()
    # CARLA 0.9.13에는 european_hgv 없음 → firetruck 사용
    bp   = bps.filter("vehicle.carlamotors.firetruck")[0]
    print(f"[main] blueprint: {bp.id}")

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
    _start_cleanup_watcher("openclaw-truck0")

    step = 0; auto_triggered = False

    def speeds(): return ", ".join(f"t{i}={v.speed*3.6:.1f}" for i, v in enumerate(platoon))
    def gaps():
        vs = list(platoon)
        return ", ".join(f"{vs[i].distance_to(vs[i+1]):.1f}" for i in range(len(vs)-1)) if len(vs) > 1 else "-"

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
                print(f"t={step*DT:6.1f}s speeds=({speeds()}) gaps=({gaps()}) state={coord.status_line()}")

            if coord.state == RotState.DONE:
                print("[main] 선두 교체 완료! 계속 주행...")
                time.sleep(5); break

            step += 1
    finally:
        kb.restore()
        sim.release_synchronous()

if __name__ == "__main__":
    main()
