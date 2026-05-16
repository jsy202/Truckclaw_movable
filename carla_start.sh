#!/bin/bash
# ============================================================
#  carla_start.sh — CARLA 0.9.6 Town06 원클릭 실행
# ============================================================
CARLA_BIN="/opt/carla-0.9.6/CarlaUE4.sh"
CARLA_PORT=2000
CARLA_LOG="$HOME/carla_server.log"
CARLA_PID_FILE="/tmp/carla_server.pid"
PYAPI="/opt/carla-0.9.6/PythonAPI/carla/dist/carla-0.9.6-py3.5-linux-x86_64.egg:/opt/carla-0.9.6/PythonAPI/carla"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${CYAN}[carla]${NC} $1"; }
ok()   { echo -e "${GREEN}[carla] ✓${NC} $1"; }
warn() { echo -e "${YELLOW}[carla] !${NC} $1"; }
err()  { echo -e "${RED}[carla] ✗${NC} $1"; exit 1; }

# 기존 프로세스 정리
if [ -f "$CARLA_PID_FILE" ]; then
    OLD_PID=$(cat $CARLA_PID_FILE)
    kill $OLD_PID 2>/dev/null && warn "기존 CARLA 프로세스($OLD_PID) 종료"
    sleep 1
fi
pkill -f "CarlaUE4-Linux-Shipping" 2>/dev/null; sleep 1

log "CARLA 0.9.6 서버 시작 중... (포트 $CARLA_PORT)"
DISPLAY=:0 \
SDL_VIDEODRIVER=x11 \
    "$CARLA_BIN" \
    -windowed -ResX=1280 -ResY=720 \
    -nosound \
    -carla-server \
    -world-port=$CARLA_PORT \
    > "$CARLA_LOG" 2>&1 &
echo $! > $CARLA_PID_FILE
CARLA_PID=$(cat $CARLA_PID_FILE)
log "PID: $CARLA_PID"

log "포트 $CARLA_PORT 열릴 때까지 대기 중..."
until ss -tlnp 2>/dev/null | grep -q ":$CARLA_PORT"; do
    sleep 3
    if ! ps -p $CARLA_PID > /dev/null 2>&1; then
        err "CARLA 프로세스 종료됨\n$(tail -10 $CARLA_LOG)"
    fi
    echo -n "."
done
echo ""
ok "CARLA 서버 준비 완료! (포트 $CARLA_PORT)"

log "Town06 맵 로드 중..."
PYTHONPATH="$PYAPI" python3.7 -c "
import carla, time
client = carla.Client('localhost', $CARLA_PORT)
client.set_timeout(30.0)
client.load_world('Town06')
time.sleep(2)
print('  맵:', client.get_world().get_map().name)
" && ok "Town06 로드 완료" || warn "Town06 로드 실패"

echo ""
echo -e "${BOLD}════════════════════════════════════════════${NC}"
ok "CARLA 0.9.6 실행 완료!"
echo -e "  포트:     ${BOLD}$CARLA_PORT${NC}"
echo -e "  맵:       ${BOLD}Town06${NC}"
echo -e "  로그:     ${CYAN}tail -f $CARLA_LOG${NC}"
echo -e "  종료:     ${CYAN}bash carla_stop.sh${NC}"
echo -e "  시나리오: ${CYAN}bash run_leader_rotation.sh${NC}"
echo -e "${BOLD}════════════════════════════════════════════${NC}"
