#!/usr/bin/env bash
# 맥에서 실행: 앱 파일(.dockerignore 허용 목록)만 Lightsail 서버로 복사하고 다시 빌드·실행한다. 비밀값은 복사하지 않는다.
# 사용: deploy/lightsail/deploy.sh ubuntu@서버IP ~/.ssh/lightsail-key.pem
set -euo pipefail
HOST="${1:?ubuntu@서버IP}"; KEY="${2:?SSH 키 파일 경로}"
cd "$(dirname "$0")/../.."
FILES=$(sed -n 's/^!//p' .dockerignore)
rsync -az --relative -e "ssh -i $KEY" $FILES Dockerfile .dockerignore deploy/compose.yaml deploy/Caddyfile "$HOST":/opt/gosomi/
ssh -i "$KEY" "$HOST" 'cd /opt/gosomi && test -f .env && docker compose --env-file .env -f deploy/compose.yaml up -d --build && docker compose --env-file .env -f deploy/compose.yaml ps'
