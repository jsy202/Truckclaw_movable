#!/usr/bin/env python3
"""
Platoon Bridge Server  –  port 18801

REST API used by platoon_bridge_ctl.py (inside Docker agents) to negotiate
vehicle transfers between platoon A and platoon B.
"""

import json
import re
import threading
import urllib.error
import urllib.request
import uuid
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import copy

CARLA_TRIGGER_URL = "http://127.0.0.1:18802/start_merge"
ACTIVE_TRANSFER_STATUSES = ("pending", "accepted", "committed", "merging", "splitting")
FAILURE_TRANSFER_STATUSES = ("trigger_failed", "merge_failed")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "platoons.json")

def _load_initial_state():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"[bridge] Failed to load config from {CONFIG_PATH}: {e}")
    return {
        "platoon_a": {
            "platoon_id": "platoon_a",
            "destination_id": "dest_a",
            "status": "cruising",
            "members": [
                {"vehicle_id": "platoon_a_truck0", "role": "leader",   "destination_id": "dest_a"},
                {"vehicle_id": "platoon_a_truck1", "role": "follower", "destination_id": "dest_a"},
                {"vehicle_id": "platoon_a_truck2", "role": "follower", "destination_id": "dest_b"},
            ],
        },
        "platoon_b": {
            "platoon_id": "platoon_b",
            "destination_id": "dest_b",
            "status": "cruising",
            "members": [
                {"vehicle_id": "platoon_b_truck0", "role": "leader",   "destination_id": "dest_b"},
                {"vehicle_id": "platoon_b_truck1", "role": "follower", "destination_id": "dest_b"},
                {"vehicle_id": "platoon_b_truck2", "role": "follower", "destination_id": "dest_b"},
            ],
        },
    }

_lock = threading.Lock()
_platoons = _load_initial_state()
_transfers: dict = {}
_readiness: dict = {
    "status": "unknown",
    "merge_ready": False,
    "reason": "initial",
    "metrics": {},
    "updated_at": None,
}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _find_vehicle(vehicle_id: str):
    for p in _platoons.values():
        for m in p["members"]:
            if m["vehicle_id"] == vehicle_id:
                return p, m
    return None, None

def _active_transfer_for(platoon_id: str):
    for tid, t in _transfers.items():
        if t["status"] in ACTIVE_TRANSFER_STATUSES:
            if t["from_platoon_id"] == platoon_id or t["to_platoon_id"] == platoon_id:
                return tid
    return None

def _active_transfer_for_vehicle(vehicle_id: str):
    for tid, t in _transfers.items():
        if t["status"] in ACTIVE_TRANSFER_STATUSES and t["vehicle_id"] == vehicle_id:
            return tid
    return None

def _is_tail_member(platoon: dict, vehicle_id: str) -> bool:
    return bool(platoon["members"] and platoon["members"][-1]["vehicle_id"] == vehicle_id)

def _complete_logical_transfer(t: dict):
    vehicle_id = t["vehicle_id"]
    from_platoon = _platoons[t["from_platoon_id"]]
    to_platoon = _platoons[t["to_platoon_id"]]
    member = next((m for m in from_platoon["members"] if m["vehicle_id"] == vehicle_id), None)
    if member:
        was_leader = (member["role"] == "leader")
        from_platoon["members"].remove(member)
        member["role"] = "follower"
        to_platoon["members"].append(member)

        if not from_platoon["members"]:
            from_platoon["status"] = "dissolved"
            print(f"[bridge] Platoon {t['from_platoon_id']} dissolved (0 members)")
        elif was_leader and from_platoon["members"]:
            from_platoon["members"][0]["role"] = "leader"
            print(f"[bridge] {from_platoon['members'][0]['vehicle_id']} promoted to leader of {t['from_platoon_id']}")
            
    return from_platoon, to_platoon

USE_CARLA = os.environ.get("MOCK_CARLA", "false").lower() != "true" # Set MOCK_CARLA=true to simulate progress without CARLA

def _notify_carla_trigger(request_id: str):
    if not USE_CARLA:
        print(f"[bridge] Mock mode: CARLA trigger skipped for {request_id}. Simulating progress.")
        def _mock_progress():
            import time
            with _lock:
                t = _transfers.get(request_id)
                if not t: return
                t["status"] = "splitting"
                _readiness.update({"status": "splitting", "merge_ready": False, "reason": "Mocking gap opening"})
            
            time.sleep(2)
            with _lock:
                t = _transfers.get(request_id)
                if not t: return
                t["status"] = "merging"
                _readiness.update({"status": "merging", "merge_ready": False, "reason": "Mocking lane change"})
            
            time.sleep(2)
            with _lock:
                t = _transfers.get(request_id)
                if not t: return
                _complete_logical_transfer(t)
                t["status"] = "carla_complete"
                _readiness.update({"status": "idle", "merge_ready": True, "reason": "Mocking completed"})
                print(f"[bridge] Mock mode: Transfer {request_id} completed logically.")
        
        threading.Thread(target=_mock_progress, daemon=True).start()
        return

    def _do():
        try:
            payload = json.dumps({"request_id": request_id}).encode("utf-8")
            req = urllib.request.Request(CARLA_TRIGGER_URL, data=payload, method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2)
            print(f"[bridge] CARLA trigger sent ({request_id})")
        except Exception as e:
            print(f"[bridge] CARLA trigger failed: {e}")
    threading.Thread(target=_do, daemon=True).start()

def _transfer_candidates(platoon_id: str) -> list:
    p = _platoons.get(platoon_id)
    if not p: return []
    platoon_dest = p["destination_id"]
    candidates = []
    tail_id = p["members"][-1]["vehicle_id"] if p["members"] else None
    for m in p["members"]:
        if m["destination_id"] == platoon_dest: continue
        for peer_id, peer in _platoons.items():
            if peer_id != platoon_id and peer["destination_id"] == m["destination_id"]:
                candidates.append({
                    "vehicle_id": m["vehicle_id"],
                    "target_platoon_id": peer_id,
                    "requires_split": m["vehicle_id"] != tail_id
                })
    return candidates

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body, indent=2).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _ok(self, body): self._send(200, body)
    def _err(self, code, msg): self._send(code, {"error": msg})
    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode()) if length > 0 else {}

    def do_GET(self):
        global _platoons
        p = self.path.rstrip("/")
        with _lock:
            if p == "/health": self._ok({"ok": True})
            elif p == "/snapshot": self._ok({"platoons": _platoons, "transfers": _transfers, "readiness": _readiness})
            elif p == "/readiness": self._ok(_readiness)
            elif p.startswith("/platoons/"):
                pid = p.split("/")[-1]
                if pid.endswith("/transfer-candidates"):
                    pid = p.split("/")[-2]
                    self._ok({"candidates": _transfer_candidates(pid)})
                else:
                    self._ok(_platoons.get(pid, {}))
            else: self._err(404, "not found")

    def do_POST(self):
        global _platoons
        p = self.path.rstrip("/")
        try: body = self._read_body()
        except: return self._err(400, "bad json")
        
        with _lock:
            if p == "/readiness":
                _readiness.update(body); _readiness["updated_at"] = _now(); self._ok(_readiness)
            elif p == "/reload":
                _platoons = _load_initial_state(); self._ok({"ok": True})
            elif p == "/transfers":
                vid, fp, tp = body.get("vehicle_id"), body.get("from_platoon_id"), body.get("to_platoon_id")
                if not vid or not fp or not tp: return self._err(400, "missing fields")
                # Takeover logic
                active = _active_transfer_for_vehicle(vid)
                if active and _transfers[active]["status"] in ("pending", "accepted"):
                    _transfers[active]["status"] = "replaced"
                rid = "tr_" + uuid.uuid4().hex[:8]
                t = {"request_id": rid, "vehicle_id": vid, "from_platoon_id": fp, "to_platoon_id": tp, "status": "pending", "requires_split": not _is_tail_member(_platoons[fp], vid), "created_at": _now()}
                _transfers[rid] = t; self._ok(t)
            elif "/accept" in p:
                rid = p.split("/")[-2]; t = _transfers.get(rid)
                if t: t["status"] = "accepted"; self._ok(t)
                else: self._err(404, "not found")
            elif "/commit" in p:
                rid = p.split("/")[-2]; t = _transfers.get(rid)
                if t: t["status"] = "committed"; _notify_carla_trigger(rid); self._ok(t)
                else: self._err(404, "not found")
            elif "/merging" in p:
                rid = p.split("/")[-2]; t = _transfers.get(rid)
                if t: t["status"] = "merging"; self._ok(t)
            elif "/carla_complete" in p:
                rid = p.split("/")[-2]; t = _transfers.get(rid)
                if t: _complete_logical_transfer(t); t["status"] = "carla_complete"; self._ok(t)
            elif "/failed" in p:
                rid = p.split("/")[-2]; t = _transfers.get(rid)
                if t: t["status"] = "merge_failed"; t["error"] = body.get("reason", "unknown"); self._ok(t)
            else: self._err(404, "not found")

def main():
    server = HTTPServer(("0.0.0.0", 18801), Handler)
    print("[bridge] Started on 18801"); server.serve_forever()

if __name__ == "__main__":
    main()
