# 계정 간 접근 차단 테스트

[cross_account_access_test.sql](cross_account_access_test.sql)은 로그인한 사용자가 다른 사용자의 사건(`public.cases`)과 증거파일(`case-evidence` 저장소)에 접근할 수 없는지 DB의 RLS 정책 수준에서 확인한다.

## 동작 방식

- SQL Editor 안에서 역할을 `authenticated`로 바꾸고 JWT의 `sub`를 테스트 계정 A로 설정해 A로 로그인한 것처럼 실행한 뒤, B로 바꿔 같은 시도를 반복한다. 마지막으로 비로그인(`anon`) 접근도 확인한다.
- 수정·삭제·생성 시도는 각각 하위 트랜잭션 안에서 실행하고 일부러 예외를 일으켜 **항상 되돌린다.** 성공·실패와 관계없이 DB에 남는 변경이 없다.
- 로그인 토큰이나 비밀번호는 쓰지 않는다.

## 실행 방법

1. 기존 사용자 자료를 건드리지 않도록 **테스트 전용 계정 두 개**를 만든다. 각 계정으로 사건 1건을 저장하고 증거파일 1개를 올린다.
2. Supabase Dashboard > SQL Editor에서 테스트 계정 ID를 조회한다. 예를 들어 테스트 직전 시각 이후 가입한 계정만 고른다.
   ```sql
   select id, created_at from auth.users where created_at > '<테스트 시작 시각>' order by created_at;
   ```
   정확히 두 계정만 나오는지 확인한다.
3. 스크립트 전체를 붙여 넣고 `__A__`, `__B__`를 두 ID로 모두 바꾼 뒤 Run을 누른다.
4. Results 표의 `expected`와 `actual`을 비교한다.
5. 테스트가 끝나면 두 테스트 계정은 앱의 회원탈퇴로 삭제한다.

## 기대 결과

| 항목 | 정상 결과 |
|---|---|
| 0 본인 사건 / 증거파일 조회 (대조군) | 1 이상. 0이면 테스트 자료가 없어 결과를 믿을 수 없음 |
| 1 다른 사용자 사건 읽기 | 0 |
| 2 다른 사용자 사건 수정 | 거부: `permission denied for table cases`. `20261012000000_case_workspace_and_case_edit` 적용 후에는 본인 사건만 고칠 수 있어 **0건**(역시 차단)이 정상 |
| 3 다른 사용자 사건 삭제 | 0건 |
| 4 다른 사용자 이름으로 사건 생성 | 거부: `new row violates row-level security policy` |
| 5 다른 사용자 증거파일 조회 | 0 |
| 6 다른 사용자 증거파일 수정 | 0건 |
| 7 다른 사용자 증거파일 삭제 | 거부: `Direct deletion from storage tables is not allowed` (아래 한계 참고) |
| 8 다른 사용자 폴더에 업로드 | 거부: `new row violates row-level security policy` |
| 9 비로그인 사건 읽기 / 증거파일 조회 | 0 또는 거부 |

## 2026-10-04 실행 결과

- 대상: 이날 새로 만든 네이버 로그인 테스트 계정 두 개(`088bd25e-…`, `fd875954-…`). 각 계정에 사건 1건, 증거파일 1개가 있었다. 기존 사용자 3명은 대상에서 제외했다.
- 결과: A→B, B→A, 비로그인 모든 항목이 기대 결과와 같았다. 결과 표는 프로젝트 관리자가 Dashboard에서 직접 읽고 확인했다.
- 같은 날 조회한 정책 상태:
  - `public.cases`: RLS 사용과 강제 적용이 켜져 있다. 조회·삭제·생성은 `auth.uid() = user_id`일 때만 허용되고, UPDATE 권한과 정책은 없다. anon 권한도 없다.
  - `case-evidence`: 비공개 버킷이다. 조회·업로드·삭제는 경로 첫 폴더가 `auth.uid()`일 때만 허용되고, UPDATE 정책은 없다.

## 직접 검증하지 않은 것

- **다른 사용자 증거파일 삭제(7번):** SQL로 직접 삭제하면 Supabase Storage의 보호 장치가 RLS보다 먼저 거부한다. 그래서 이 항목의 "거부"는 RLS가 막았다는 증거가 아니다. 다른 사용자 파일 삭제 차단은 정책 검토(본인 폴더만 삭제 허용)와 앱 코드 검토로만 판단했다. Storage API로 다른 사용자 파일 삭제를 요청하는 실제 테스트는 하지 않았다.
- **실제 다운로드·서명 URL 요청:** Storage API로 다른 사용자 파일을 다운로드하거나 서명 URL을 만드는 요청은 직접 보내지 않았다. Storage가 다운로드할 때 `storage.objects` 조회 정책을 적용하므로, 5번 조회 결과(0건)를 근거로 차단된다고 판단했다.
- 위 두 항목을 실제로 검증하려면 두 테스트 계정의 로그인 세션으로 Storage API를 호출하는 별도 테스트가 필요하다.

# 고소장 초안(case_drafts) 테스트

## 로컬 테스트 (운영 DB 접속 없음)

[local_case_drafts_test.py](local_case_drafts_test.py)는 빈 로컬 PostgreSQL에 Supabase의 auth 스키마·역할·`cases` 정책을 흉내 낸 DB를 만들고, [초안 마이그레이션](../migrations/20261006000000_case_drafts.sql)·[동의 버전 변경](../migrations/20261013000000_update_consent_privacy_version_v3.sql)·[되돌리기](../rollbacks/20261006000000_case_drafts_rollback.sql)를 차례로 적용해 23개 항목을 확인한다. Supabase 주소로는 실행되지 않게 막혀 있다.

```bash
LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" python3 supabase/tests/local_case_drafts_test.py
```

### 2026-10-06 실행 결과 (PostgreSQL 16.4, 로컬 임시 서버)

23/23 통과: 본인 사건 초안 저장·수정, 다른 사용자 사건·이름으로 생성 차단, 없는 사건 차단, 다른 사용자 초안 읽기·수정·삭제 0건, 다른 사건으로 옮기기 차단, 빈 초안·20,000자 초과 차단, 비로그인 거부, 사건 삭제 시 초안 삭제(트리거), 탈퇴 흐름(cases → consent_records → auth.users) 후 초안 0건, 사용자 삭제 cascade, 동의 함수 새·이전 버전 허용과 잘못된 버전 거부, 되돌리기 후 테이블·트리거 제거.

## 운영 DB 확인 (마이그레이션 적용 후)

[case_drafts_access_test.sql](case_drafts_access_test.sql)을 위 계정 간 접근 차단 테스트와 같은 방법(테스트 전용 계정 두 개, `__A__`·`__B__` 바꾸기)으로 실행한다. 모든 쓰기는 되돌려지고, 상대 계정의 초안은 관리자 권한으로 잠시 만든 뒤 함께 되돌리므로 테스트 계정에 미리 초안을 만들 필요가 없다.

| 항목 | 정상 결과 |
|---|---|
| 0 본인 사건 초안 저장(대조) | 1건 |
| 1 다른 사용자 초안 읽기 | 0 |
| 4 다른 사용자 초안 수정 | 0건 |
| 5 다른 사용자 초안 삭제 | 0건 |
| 2 다른 사용자 사건에 초안 생성 | 거부: `new row violates row-level security policy` |
| 3 다른 사용자 이름으로 초안 생성 | 거부: `new row violates row-level security policy` |
| 6 비로그인 초안 읽기 | 거부: `permission denied for table case_drafts` |

로컬에서 같은 SQL을 실행해 위 결과를 확인했고, RLS를 끈 상태에서는 1·4·5번이 1건으로 바뀌어 검사가 실제로 차단 여부를 잡아내는 것도 확인했다.

### 2026-10-06 운영 DB 실행 결과

- 대상: 테스트 계정 A(`53bf9e60-…`, 2026-10-06 가입, 사건 3건)와 B(`088bd25e-…`, 2026-10-04 테스트 계정, 사건 2건). 실행 전 두 계정 모두 초안 0건임을 조회로 확인했다. 다른 사용자 3명은 대상에서 제외했다.
- 결과: A→B, B→A, 비로그인 13개 항목이 모두 기대 결과와 같았다. 상대 계정 초안을 임시로 만든 대조 조건에서도 읽기·수정·삭제가 0건이었다.
- 실행 후 초안 0건, 두 계정의 사건 수(3건, 2건)와 전체 사용자 수(5명)가 그대로임을 확인했다. 실행한 SQL은 저장소 파일과 SHA-256이 같음을 확인했다.


## 로컬 임시 Postgres 검사 (운영 DB에 접속하지 않음)

`local_*_test.py`는 로컬 Postgres에 Supabase 비슷한 역할·auth 스키마를 만들고 마이그레이션을 적용해 접근 제한·삭제·되돌리기를 검사한다.
Homebrew·Docker가 없는 Mac에서도 아래처럼 할 수 있다(2026-10-11 이 방식으로 실행).

1. EDB가 배포하는 PostgreSQL macOS 바이너리(zip, postgresql.org 다운로드 안내의 공식 배포처)를 빈 임시 폴더에 받아 푼다.
   `https://get.enterprisedb.com/postgresql/postgresql-17.6-1-osx-binaries.zip`
2. `pgsql/bin/initdb -D <임시폴더>/pgdata -U postgres --auth=trust -E UTF8 --locale=C`
3. `pgsql/bin/pg_ctl -D <임시폴더>/pgdata -o "-p 54329 -k '' -c listen_addresses=127.0.0.1" -l <임시폴더>/pg.log start`
   (`-k ''`: 임시 폴더 경로가 길면 유닉스 소켓을 만들 수 없어 TCP만 쓴다)
4. `python3 -m venv <임시폴더>/venv && <임시폴더>/venv/bin/pip install "psycopg[binary]"`
5. `LOCAL_PG="host=127.0.0.1 port=54329 user=postgres" <임시폴더>/venv/bin/python supabase/tests/local_case_workspace_edit_test.py`
6. 끝나면 `pg_ctl -D <임시폴더>/pgdata stop`

| 스크립트 | 검사 대상 | 2026-10-11 결과 |
|---|---|---|
| local_case_drafts_test.py | 초안 표·동의 함수 | 23/23 |
| local_billing_test.py | 결제 제안(미적용) | 30/30 |
| local_case_workspace_edit_test.py | 사건 수정·작업 공간 저장(미적용): 본인만, 고칠 수 없는 열, 동시 수정 충돌, 사건 삭제·탈퇴 시 삭제, 되돌리기·재적용 | 33/33 |

주의: 로컬 `cases` 표는 운영 표를 흉내 낸 것이다(열·정책은 기존 시험과 같은 가정). 운영 적용 전에 운영 `cases`의 실제 열·정책을 한 번 더 확인한다.
