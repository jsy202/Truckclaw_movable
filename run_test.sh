#!/bin/bash

echo "=== CARLA + Movable 테스트 스크립트 ==="

# 1. 기존 프로세스 정리
echo "[1/4] 기존 프로세스 정리 중..."
pkill -9 -f CarlaUE4 2>/dev/null
pkill -9 -f python3 2>/dev/null
sleep 3

# 2. CARLA 시작
echo "[2/4] CARLA 서버 시작 중... (15초 대기)"
cd ~/carla-0.9.13
DISPLAY=:0 ./CarlaUE4.sh -windowed -ResX=800 -ResY=600 -quality-level=Low > /tmp/carla.log 2>&1 &
CARLA_PID=$!
sleep 15

# 3. CARLA 실행 확인
if ps -p $CARLA_PID > /dev/null; then
    echo "[3/4] CARLA 준비 완료!"
else
    echo "[ERROR] CARLA 시작 실패. /tmp/carla.log 확인"
    exit 1
fi

# 4. 시나리오 실행
echo "[4/4] 시나리오 실행 중..."
cd /home/user/Downloads/Truckclaw_movable
python3 scenario/examples/leader_rotation_scenario.py --no-openclaw

# 종료 시 정리
echo ""
echo "=== 종료 중... ==="
pkill -9 -f CarlaUE4 2>/dev/null
echo "완료!"
== 종료 중... ==="
pkill -9 -f CarlaUE4 2>/dev/null
echo "완료!"
