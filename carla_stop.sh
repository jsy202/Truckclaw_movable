#!/bin/bash
GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()  { echo -e "${GREEN}[stop] ✓${NC} $1"; }
log() { echo -e "${CYAN}[stop]${NC} $1"; }

log "CARLA 및 관련 프로세스 종료 중..."
pkill -f "CarlaUE4-Linux-Shipping" 2>/dev/null && ok "CARLA 서버 종료" || true
pkill -f "platoon_bridge_server.py" 2>/dev/null && ok "브리지 서버 종료" || true
docker rm -f openclaw-truck0 openclaw-truck1 openclaw-truck2 \
             vehicle-truck0 vehicle-truck1 vehicle-truck2 \
             platoon-bridge-server 2>/dev/null | while read l; do ok "$l 제거"; done
rm -f /tmp/carla_server.pid
ok "모두 종료 완료"
