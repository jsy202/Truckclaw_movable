#!/bin/bash
# ============================================================
#  run_leader_rotation.sh — 선두 교체 시나리오 원클릭 실행
#  truck0(선두) → truck1 OpenClaw 이전 + CARLA 후미 합류
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CARLA_PORT=2000
BRIDGE_PORT=18801
LEADER_ROT_PORT=18803
PYAPI=""
PYAPI_DIR="/home/user/carla_source/PythonAPI/carla"
ENV_FILE="$SCRIPT_DIR/.env"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${CYAN}[truckclaw]${NC} $1"; }
ok()   { echo -e "${GREEN}[truckclaw] ✓${NC} $1"; }
warn() { echo -e "${YELLOW}[truckclaw] !${NC} $1"; }
err()  { echo -e "${RED}[truckclaw] ✗${NC} $1"; exit 1; }

cd "$SCRIPT_DIR"

# ── 1. CARLA 서버 확인 ────────────────────────────────────────────────────────
log "CARLA 서버 확인 중 (포트 $CARLA_PORT)..."
if ! ss -tlnp 2>/dev/null | grep -q ":$CARLA_PORT" && \
   ! netstat -tlnp 2>/dev/null | grep -q ":$CARLA_PORT"; then
    warn "CARLA 서버가 실행 중이 아닙니다"
    if [ -f "$SCRIPT_DIR/carla_start.sh" ]; then
        bash "$SCRIPT_DIR/carla_start.sh" || warn "CARLA 시작 실패 — 계속 진행"
    fi
else
    ok "CARLA 서버 실행 중"
fi

# ── 2. PYTHONPATH 설정 ────────────────────────────────────────────────────────
export PYTHONPATH="$PYAPI:$PYAPI_DIR:$SCRIPT_DIR/scenario/src:$SCRIPT_DIR/openclaw_migration:$PYTHONPATH"
log "PYTHONPATH 설정 완료"

# ── 3. 브리지 서버 확인 / 시작 ───────────────────────────────────────────────
log "브리지 서버 확인 중 (포트 $BRIDGE_PORT)..."
if curl -s http://127.0.0.1:$BRIDGE_PORT/health &>/dev/null; then
    ok "브리지 서버 실행 중"
else
    log "브리지 서버 시작 중..."
    python3 "$SCRIPT_DIR/bridge/platoon_bridge_server.py" &
    BRIDGE_PID=$!
    sleep 2
    if curl -s http://127.0.0.1:$BRIDGE_PORT/health &>/dev/null; then
        ok "브리지 서버 시작됨 (PID=$BRIDGE_PID)"
    else
        warn "브리지 서버 시작 실패 — OpenClaw 없이 진행"
    fi
fi

# ── 4. Docker 상태 + vehicle-truck0 시작 ─────────────────────────────────────
echo ""
echo -e "${BOLD}── Docker 상태 ──────────────────────────────────${NC}"
if ! command -v docker &>/dev/null; then
    echo -e "  ${RED}✗${NC} Docker 미설치"
elif ! docker info &>/dev/null 2>&1; then
    echo -e "  ${RED}✗${NC} Docker 데몬 응답 없음"
else
    # base tar 최초 생성
    if [ ! -f "$SCRIPT_DIR/.transfer/openclaw_base.tar" ]; then
        log "순정 base tar 최초 생성 중..."
        python3 "$SCRIPT_DIR/openclaw_migration/replicator.py" --ensure-base && \
            ok "base tar 생성 완료" || warn "base tar 생성 실패"
    else
        ok "base tar 존재 (.transfer/openclaw_base.tar)"
    fi

    if [ ! -f "$ENV_FILE" ]; then
        echo -e "  ${YELLOW}!${NC} .env 없음 → cp .env.example .env 후 토큰 설정"
    else
        # vehicle-truck0 실행
        if docker ps --format '{{.Names}}' | grep -q "^vehicle-truck0$"; then
            ok "vehicle-truck0 실행 중"
        else
            log "vehicle-truck0 시작 중..."
            docker compose --env-file "$ENV_FILE" up -d vehicle-truck0 2>/dev/null && \
                ok "vehicle-truck0 시작됨" || warn "vehicle-truck0 시작 실패"
        fi
        # openclaw-truck0 상태
        if docker ps --format '{{.Names}}' | grep -q "^openclaw-truck0$"; then
            ok "openclaw-truck0 실행 중 (현재 선두)"
        fi
    fi

    # 현재 컨테이너 목록
    docker ps -a --format "  {{.Names}}\t{{.Status}}" \
        --filter "name=truck0" \
        --filter "name=truck1" \
        --filter "name=truck2" 2>/dev/null | while IFS= read -r line; do
        echo -e "  ${GREEN}▶${NC} $line"
    done
fi
echo -e "${BOLD}────────────────────────────────────────────────${NC}"

# ── 5. 시나리오 실행 ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
ok "Leader Rotation 시나리오 시작!"
echo -e "  키:  ${BOLD}'L'${NC}=선두교체 트리거  ${BOLD}Ctrl-C${NC}=종료"
echo -e "  자동: --auto-trigger-s 30  (30초 후 자동 트리거)"
echo -e "  브리지:  ${CYAN}http://127.0.0.1:$BRIDGE_PORT/snapshot${NC}"
echo -e "  트리거:  POST http://127.0.0.1:$LEADER_ROT_PORT/leader_rotation"
echo -e "  OpenClaw 없이 테스트: ${CYAN}--no-openclaw${NC}"
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo ""

# 인수 전달 (--no-openclaw, --auto-trigger-s 등)
python3 "$SCRIPT_DIR/scenario/examples/leader_rotation_scenario.py" "$@"
