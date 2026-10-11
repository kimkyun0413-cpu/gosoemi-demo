#!/usr/bin/env bash
# 고소미 DB 수동 백업 (Supabase Free: 자동 백업 없음).
# - Docker 없이 PostgreSQL 클라이언트(pg_dump)로 백업하고, 결과를 암호화해 이 컴퓨터에만 둔다.
# - 백업에는 회원·사건 등 개인정보가 들어 있다. 외부 저장소(클라우드 드라이브 등)에 올리지 않는다.
# - 증거파일(Storage)은 DB 백업에 포함되지 않는다: scripts/backup_storage.py를 따로 실행한다.
#
# 준비(한 번): brew install libpq && brew link --force libpq
# 실행:
#   read -s SUPABASE_DB_URL && export SUPABASE_DB_URL   # Dashboard > Connect > Session pooler 연결 문자열(비밀번호 포함)
#   bash scripts/backup_supabase.sh                      # 암호 두 번 입력
#   unset SUPABASE_DB_URL
set -euo pipefail

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
  echo "SUPABASE_DB_URL이 없습니다. read -s로 입력해 주세요." >&2; exit 2
fi
for tool in pg_dump psql openssl; do
  command -v "$tool" >/dev/null || { echo "$tool이 없습니다. 준비 단계를 먼저 해 주세요." >&2; exit 2; }
done

server_major=$(psql "$SUPABASE_DB_URL" -Atc "show server_version_num" | cut -c1-2)
client_major=$(pg_dump --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')
if (( client_major < server_major )); then
  echo "pg_dump($client_major)가 서버($server_major)보다 오래됐습니다. 같은 버전 이상을 설치해 주세요." >&2; exit 2
fi

stamp=$(date +%Y%m%d-%H%M%S)
out_dir="${BACKUP_DIR:-$HOME/gosoemi-backups}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
umask 077
mkdir -p "$out_dir"

# 앱이 만든 스키마 구조(표·함수·트리거·RLS 정책·권한). Supabase가 관리하는 스키마는 덤프하지 않는다.
pg_dump "$SUPABASE_DB_URL" --schema-only --no-owner \
  --schema=public --schema=naver_private --file="$work/schema.sql"
# 자료: 앱 스키마 + 로그인 계정(auth.users, auth.identities).
# storage.objects(증거파일 목록)는 넣지 않는다: 파일을 복구할 때 Storage API가 목록을 다시 만든다.
pg_dump "$SUPABASE_DB_URL" --data-only --no-owner \
  --schema=public --schema=naver_private \
  --table=auth.users --table=auth.identities \
  --file="$work/data.sql"

{
  echo "created_at=$stamp"
  echo "server_version=$(psql "$SUPABASE_DB_URL" -Atc 'show server_version')"
  psql "$SUPABASE_DB_URL" -Atc "select 'rows:' || relname || '=' || n_live_tup from pg_stat_user_tables where schemaname in ('public','naver_private') order by 1"
} > "$work/manifest.txt"

tar -C "$work" -czf "$work/backup.tar.gz" schema.sql data.sql manifest.txt
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$work/backup.tar.gz" -out "$out_dir/gosoemi-db-$stamp.tar.gz.enc"
echo "백업 완료: $out_dir/gosoemi-db-$stamp.tar.gz.enc"
echo "암호를 잃어버리면 복구할 수 없습니다. 오래된 백업은 운영 원칙(보관 기간)에 따라 지워 주세요."
