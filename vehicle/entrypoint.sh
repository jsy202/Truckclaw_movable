#!/bin/bash
# vehicle 컨테이너 시작 스크립트
# DinD 구조: 호스트의 도커 데몬을 사용하여 openclaw 컨테이너를 실행

TRUCK_ID=${TRUCK_ID:-truck0}
OPENCLAW_CONTAINER="openclaw-${TRUCK_ID}"
OPENCLAW_DATA_DIR=${OPENCLAW_DATA_DIR:-/data/openclaw}
OPENCLAW_HOST_DATA_DIR=${OPENCLAW_HOST_DATA_DIR:-${OPENCLAW_DATA_DIR}}
GATEWAY_PORT=${OPENCLAW_GATEWAY_PORT:-18789}
OPENCLAW_IMAGE=${OPENCLAW_IMAGE:-openclaw:local}

echo "[vehicle-${TRUCK_ID}] 시작 중..."

# truck0인 경우에만 초기 에이전트 실행
if [ "${TRUCK_ID}" = "truck0" ]; then
    docker rm -f ${OPENCLAW_CONTAINER} 2>/dev/null || true
    
    # ⚠️ 중요: CMD를 'openclaw gateway'로 명시하여 데몬 모드로 실행
    GATEWAY_CMD="openclaw gateway run --port ${GATEWAY_PORT} --allow-unconfigured & tail -f /dev/null"
    if [ -n "${OPENCLAW_GATEWAY_TOKEN}" ]; then
      GATEWAY_CMD="${GATEWAY_CMD} --token ${OPENCLAW_GATEWAY_TOKEN}"
    fi

    docker run -d \
      --name ${OPENCLAW_CONTAINER} \
      --network host \
      -e HOME=/data/openclaw \
      -e DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN} \
      -e OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN} \
      -e OPENAI_API_KEY=${OPENAI_API_KEY:-} \
      -e OPENCLAW_GATEWAY_PORT=${GATEWAY_PORT} \
      -v ${OPENCLAW_HOST_DATA_DIR}:/data/openclaw \
      -v /project/scripts:/project/scripts:ro \
      ${OPENCLAW_IMAGE} \
      "${GATEWAY_CMD}"

    echo "[vehicle-truck0] openclaw-truck0 가동됨 (Port: ${GATEWAY_PORT})"
else
    echo "[vehicle-${TRUCK_ID}] 대기 중 — 세션 수신 후 replicator가 기동합니다"
fi

# 컨테이너 유지
tail -f /dev/null
