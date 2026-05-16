#!/bin/bash
# vehicle 컨테이너 시작 스크립트
# Docker 소켓 공유로 내부에서 openclaw 컨테이너를 실행

TRUCK_ID=${TRUCK_ID:-truck0}
OPENCLAW_CONTAINER="openclaw-${TRUCK_ID}"
OPENCLAW_DATA_DIR=${OPENCLAW_DATA_DIR:-/data/openclaw}
GATEWAY_PORT=${OPENCLAW_GATEWAY_PORT:-18789}

echo "[vehicle-${TRUCK_ID}] 시작 중..."

# 기존 컨테이너 제거
docker rm -f ${OPENCLAW_CONTAINER} 2>/dev/null || true

# openclaw 컨테이너 실행
docker run -d \
  --name ${OPENCLAW_CONTAINER} \
  --network host \
  -e HOME=/data/openclaw \
  -e DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN} \
  -e OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN} \
  -e OPENAI_API_KEY=${OPENAI_API_KEY:-} \
  -e OPENCLAW_GATEWAY_PORT=${GATEWAY_PORT} \
  -v ${OPENCLAW_DATA_DIR}:/data/openclaw \
  -v /project/scripts:/project/scripts:ro \
  openclaw:local

echo "[vehicle-${TRUCK_ID}] ${OPENCLAW_CONTAINER} 컨테이너 시작됨 (port=${GATEWAY_PORT})"

# 컨테이너 살아있게 유지
tail -f /dev/null
