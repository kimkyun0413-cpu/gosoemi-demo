"""증거파일(Storage 'case-evidence') 수동 백업·복구. DB 백업(backup_supabase.sh)과 함께 쓴다.

- 모든 이용자 파일을 읽으려면 secret 키가 필요하다. 키는 화면에 보이지 않게 입력하고 이 스크립트는 출력·저장하지 않는다.
- 결과는 암호화해 이 컴퓨터에만 둔다. 외부 저장소에 올리지 않는다.

백업:
    read -s SUPABASE_URL && read -s SUPABASE_SECRET_KEY && export SUPABASE_URL SUPABASE_SECRET_KEY
    python3 scripts/backup_storage.py backup
복구(새 프로젝트로):
    python3 scripts/backup_storage.py restore ~/gosoemi-backups/gosoemi-files-YYYYMMDD-HHMMSS.tar.gz.enc
"""

import getpass
import os
import subprocess
import sys
import tarfile
import tempfile
import time

BUCKET = "case-evidence"
PRODUCTION_REF = "hnaobxrfapsuiygggdgx"


def list_all(bucket, prefix=""):
    """폴더를 따라 내려가며 모든 파일 경로를 돌려준다. 100개씩 끊어 읽는다."""
    paths, offset = [], 0
    while True:
        entries = bucket.list(prefix, {"limit": 100, "offset": offset}) or []
        for entry in entries:
            name = entry["name"] if isinstance(entry, dict) else entry.name
            item_id = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
            path = f"{prefix}/{name}" if prefix else name
            if item_id:
                paths.append(path)
            else:
                paths.extend(list_all(bucket, path))
        if len(entries) < 100:
            return paths
        offset += 100


def _client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL과 SUPABASE_SECRET_KEY를 read -s로 입력해 주세요.")
    return url, create_client(url, key).storage.from_(BUCKET)


def _openssl(args, password):
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", *args, "-pass", "stdin"],
                   input=(password + "\n").encode(), check=True)


def backup():
    _, bucket = _client()
    password = getpass.getpass("백업 암호: ")
    if password != getpass.getpass("백업 암호 확인: ") or len(password) < 12:
        sys.exit("암호가 다르거나 12자보다 짧습니다.")
    out_dir = os.environ.get("BACKUP_DIR", os.path.expanduser("~/gosoemi-backups"))
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory() as work:
        paths = list_all(bucket)
        for path in paths:
            target = os.path.join(work, "files", path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(bucket.download(path))
        archive = os.path.join(work, "files.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(os.path.join(work, "files"), arcname="files")
        out = os.path.join(out_dir, f"gosoemi-files-{stamp}.tar.gz.enc")
        _openssl(["-salt", "-in", archive, "-out", out], password)
    print(f"증거파일 {len(paths)}개 백업 완료: {out}")


def restore(path):
    url, bucket = _client()
    if PRODUCTION_REF in url:
        sys.exit("운영 프로젝트 주소입니다. 새 프로젝트에 먼저 복구해 주세요.")
    password = getpass.getpass("백업 암호: ")
    with tempfile.TemporaryDirectory() as work:
        archive = os.path.join(work, "files.tar.gz")
        _openssl(["-d", "-in", path, "-out", archive], password)
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        root = os.path.join(work, "files")
        count = 0
        for folder, _, names in os.walk(root):
            for name in names:
                full = os.path.join(folder, name)
                key = os.path.relpath(full, root)
                with open(full, "rb") as f:
                    bucket.upload(key, f.read(), {"upsert": "false"})
                count += 1
    print(f"증거파일 {count}개 복구 완료")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "backup":
        backup()
    elif len(sys.argv) >= 3 and sys.argv[1] == "restore":
        restore(sys.argv[2])
    else:
        print(__doc__)
