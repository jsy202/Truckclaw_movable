#!/bin/bash
# vehicle 컨테이너 시작 스크립트
# truck0만 초기 실행 — truck1은 session tar 수신 후 replicator가 기동

TRUCK_ID=${TRUCK_ID:-truck0}
OPENCLAW_CONTAINER="openclaw-${TRUCK_ID}"
OPENCLAW_DATA_DIR=${OPENCLAW_DATA_DIR:-/data/openclaw}
GATEWAY_PORT=${OPENCLAW_GATEWAY_PORT:-18789}

echo "[vehicle-${TRUCK_ID}] 시작 중..."

# truck0만 초기 openclaw 실행
# truck1은 replicator.py가 session tar 수신 후 직접 docker run 호출
if [ "${TRUCK_ID}" = "truck0" ]; then
    docker rm -f ${OPENCLAW_CONTAINER} 2>/dev/null || true
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
    echo "[vehicle-truck0] openclaw-truck0 실행됨 (토큰 포함)"
else
    echo "[vehicle-${TRUCK_ID}] openclaw 대기 중 — session tar 수신 후 자동 기동"
fi

# 컨테이너 유지
tail -f /dev/null
