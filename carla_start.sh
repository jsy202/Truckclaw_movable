#!/bin/bash
# ============================================================
#  carla_start.sh — CARLA 0.9.13 Town06 원클릭 실행
# ============================================================
CARLA_BIN="$HOME/carla-0.9.13/CarlaUE4/Binaries/Linux/CarlaUE4-Linux-Shipping"
CARLA_PORT=2000
CARLA_LOG="$HOME/carla_server.log"
CARLA_PID_FILE="/tmp/carla_server.pid"
PYAPI="$HOME/carla-0.9.13/PythonAPI/carla/dist/carla-0.9.13-py3.7-linux-x86_64.egg:$HOME/carla-0.9.13/PythonAPI/carla"

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

# CARLA 서버 시작 (창모드, 마우스 캡처 최소화)
log "CARLA 0.9.13 서버 시작 중... (포트 $CARLA_PORT)"
DISPLAY=:0 \
SDL_VIDEODRIVER=x11 \
SDL_VIDEO_X11_NODIRECTCOLOR=1 \
SDL_MOUSE_RELATIVE=0 \
SDL_MOUSE_FOCUS_CLICKTHROUGH=1 \
    "$CARLA_BIN" \
    -windowed \
    -ResX=1280 -ResY=720 \
    -nosound \
    -quality-level=Medium \
    -carla-server \
    -world-port=$CARLA_PORT \
    > "$CARLA_LOG" 2>&1 &
echo $! > $CARLA_PID_FILE
CARLA_PID=$(cat $CARLA_PID_FILE)
log "PID: $CARLA_PID"

# 포트 대기
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

# 마우스 캡처 해제
log "마우스 캡처 해제 중..."
(
    sleep 4
    WIN_ID=$(DISPLAY=:0 xdotool search --name "CarlaUE4" 2>/dev/null | head -1)
    if [ -n "$WIN_ID" ]; then
        DISPLAY=:0 wmctrl -i -r "$WIN_ID" -b add,below 2>/dev/null
        DISPLAY=:0 xdotool windowfocus --sync $(DISPLAY=:0 xdotool getactivewindow 2>/dev/null) 2>/dev/null || true
        DISPLAY=:0 xdotool key Escape 2>/dev/null || true
        log "CARLA 창 포커스 해제 완료 (WIN_ID=$WIN_ID)"
    else
        warn "CARLA 창을 찾지 못함 — 수동으로 Alt+Tab 으로 포커스 전환하세요"
    fi
) &

# Town06 로드
log "Town06 맵 로드 중..."
PYTHONPATH="$PYAPI" python3.7 -c "
import carla, time
client = carla.Client('localhost', $CARLA_PORT)
client.set_timeout(30.0)
client.load_world('Town06')
time.sleep(3)
world = client.get_world()
print('  맵:', world.get_map().name)
world.set_weather(carla.WeatherParameters.ClearNoon)
time.sleep(1)
world.set_weather(carla.WeatherParameters.ClearNoon)
print('  날씨: ClearNoon 적용 완료')
" && ok "Town06 로드 + 날씨 적용 완료" || warn "Town06 로드 실패 (기본 맵으로 진행)"

# Town06 로드 완료 후 한 번 더 마우스 해제 시도
sleep 2
WIN_ID=$(DISPLAY=:0 xdotool search --name "CarlaUE4" 2>/dev/null | head -1)
if [ -n "$WIN_ID" ]; then
    DISPLAY=:0 xdotool key --window "$WIN_ID" Escape 2>/dev/null || true
    TERM_WIN=$(DISPLAY=:0 xdotool search --name "Terminal\|terminal\|bash\|konsole\|gnome-terminal" 2>/dev/null | head -1)
    [ -n "$TERM_WIN" ] && DISPLAY=:0 xdotool windowfocus "$TERM_WIN" 2>/dev/null || true
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════${NC}"
ok "CARLA 0.9.13 실행 완료!"
echo -e "  포트:   ${BOLD}$CARLA_PORT${NC}"
echo -e "  맵:     ${BOLD}Town06${NC}"
echo -e "  로그:   ${CYAN}tail -f $CARLA_LOG${NC}"
echo -e "  종료:   ${CYAN}pkill -f CarlaUE4-Linux-Shipping${NC}"
echo -e "  시나리오: ${CYAN}./run_truckclaw.sh${NC}"
echo -e "  마우스:  CARLA 창 클릭 시 ${YELLOW}Alt+Tab${NC} 으로 포커스 전환"
echo -e "${BOLD}════════════════════════════════════════════${NC}"
