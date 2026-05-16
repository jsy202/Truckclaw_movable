FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Docker CLI + Python + 기본 도구
RUN apt-get update && apt-get install -y \
    docker.io \
    python3 \
    python3-pip \
    curl \
    tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /vehicle

COPY bridge/ /project/scripts/
COPY openclaw_migration/ /project/openclaw_migration/

COPY vehicle/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
