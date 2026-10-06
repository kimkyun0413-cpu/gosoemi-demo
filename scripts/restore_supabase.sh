#!/usr/bin/env bash
# 백업 복구. 기본은 '새 Supabase 프로젝트'에 복구하는 연습/재해 복구용이다.
# 운영 프로젝트에 직접 덮어쓰지 않는다(기존 자료와 충돌). 운영 복구가 필요하면 먼저 새 프로젝트에서 확인한다.
#
#   read -s TARGET_DB_URL && export TARGET_DB_URL    # 복구할 새 프로젝트의 Session pooler 연결 문자열
#   bash scripts/restore_supabase.sh ~/gosoemi-backups/gosoemi-db-YYYYMMDD-HHMMSS.tar.gz.enc
set -euo pipefail
file="${1:?복구할 .enc 파일 경로를 적어 주세요}"
[[ -n "${TARGET_DB_URL:-}" ]] || { echo "TARGET_DB_URL이 없습니다." >&2; exit 2; }
case "$TARGET_DB_URL" in *hnaobxrfapsuiygggdgx*) echo "운영 프로젝트 주소입니다. 새 프로젝트에 먼저 복구해 주세요." >&2; exit 2;; esac

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT; umask 077
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$file" -out "$work/backup.tar.gz"
tar -C "$work" -xzf "$work/backup.tar.gz"
cat "$work/manifest.txt"

# 계정이 먼저 있어야 사건 등의 외래키가 맞는다. 트리거는 잠시 끄고(replica) 자료를 넣는다.
psql "$TARGET_DB_URL" --single-transaction --variable ON_ERROR_STOP=1 \
  --file "$work/schema.sql" \
  --command 'SET session_replication_role = replica' \
  --file "$work/data.sql"
echo "DB 복구 완료. 증거파일은 scripts/backup_storage.py --restore로 따로 올립니다."
echo "확인: 앱 설정을 새 프로젝트로 바꾸기 전에 manifest의 행 수와 새 프로젝트의 행 수를 비교하세요."
