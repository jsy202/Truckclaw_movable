# Limitations

- **Fixed condition**: 동일한 고정 CARLA 조건(Town06 고정 스폰, 단일 목표 속도, 단일 날씨)에서만 실행되었습니다.
- **Single scenario configuration**: 3대 플래툰(truck0, truck1, truck2)의 단일 시나리오 설정으로 측정되었습니다.
- **CARLA simulation**: CARLA 0.9.13 시뮬레이션 환경에서의 검증이며, 실차 검증이 아닙니다.
- **Off-screen rendering**: `-RenderOffScreen` 모드로 디스플레이 렌더링 없이 수행된 시뮬레이션입니다.
- **carlacola fallback**: `european_hgv` blueprint 부재로 기존 fallback 모델인 `vehicle.carlamotors.carlacola`를 사용했습니다.
- **OpenClaw Migration Not tested**: 실행 환경에 `openclaw:local` 이미지, 인증 정보, Discord gateway 및 소스 에이전트 환경이 부재하여 OpenClaw migration은 `Not tested`로 분리되었습니다.
- **Generalization 미검증**: 다양한 교통량, 도로 기하구조, 기상 조건 및 센서 노이즈에 대한 일반화는 검증되지 않았습니다.
- CARLA-free 회귀 테스트 결과(기존 22 passed + 1 known gateway-health xfail)는 실제 CARLA 50회 실행 결과와 별개로 보고됩니다.
