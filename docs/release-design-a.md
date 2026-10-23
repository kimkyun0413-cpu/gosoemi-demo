# A안 운영 반영 순서 (2026-10-24 준비, 운영자 승인 후 실행)

브랜치 `feat/design-a` → main. 바뀌는 것: 메뉴·첫 화면·새 사건 4단계(모든 이용자), 사건 4단계 화면(관리자, 기존 제한),
이전 초안 보관·이용 후기(새 표 2개, v3 전에는 관리자만 쓰기).

## 마지막 검사 (2026-10-24)
- 자동 테스트 277개 통과. 로컬 Postgres DB 시험 9개 모두 통과(새 표 19/19·27/27, 운영 반영 리허설 17/17, 기존 6개 145항목).
- 리허설(`supabase/tests/local_release_design_a_test.py`): Supabase처럼 슈퍼유저가 아닌 역할로 아래 SQL을 그대로 실행 →
  적용 전 점검 10/10, 적용 후 점검 15/15, 기존 사건·초안·작업 공간 개수와 내용 그대로, 되돌리기 후에도 그대로.

## 파일
| 순서 | 파일 | 하는 일 |
|---|---|---|
| 점검 | `supabase/checks/preflight_design_a.sql` | 읽기만. ok 모두 true + 개수 기록 |
| 적용 | `supabase/release/2026-10-24_design_a_apply.sql` | 새 표·함수·트리거만 추가(begin…commit 한 덩어리) |
| 운영자 | `supabase/release/2026-10-24_review_admin.sql` | `<운영자 user id>`를 ADMIN_USER_IDS 값으로 바꿔 실행 |
| 점검 | `supabase/checks/postapply_design_a.sql` | 읽기만. ok 모두 true + 개수가 적용 전과 같음 |
| 되돌리기 | `supabase/release/2026-10-24_design_a_rollback.sql` | 새 표만 삭제(보관 초안·후기 사라짐, 기존 자료 그대로) |

## 순서
1. **백업(운영자, 터미널)** — Supabase Free는 자동 백업이 없다. 이번 변경은 기존 표를 고치지 않지만 만일을 위해 한다.
   ```bash
   export PATH="/private/tmp/claude-501/-Users-libubu-Documents-ai-secretary/184e0136-35a6-4046-87d5-2f66519f8f45/scratchpad/pgtest/bin_root/pgsql/bin:$PATH"
   cd /Users/libubu/Documents/ai-secretary
   read -s SUPABASE_DB_URL && export SUPABASE_DB_URL
   bash scripts/backup_supabase.sh
   unset SUPABASE_DB_URL
   ```
   - 연결 문자열: Supabase Dashboard → Connect → Session pooler(비밀번호 포함). 붙여 넣어도 화면에 보이지 않는다.
   - 백업 암호를 두 번 묻는다. 암호는 비밀번호 관리자에만 저장. 결과는 `~/gosoemi-backups/`(이 Mac에만).
   - 위 PATH의 임시 폴더는 Mac을 다시 시작하면 지워질 수 있으니 재시작 전에 한다.
2. **SQL Editor(운영자)**: 점검 → 개수 적기 → 적용 → 운영자 지정 → 적용 후 점검. 하나라도 false면 멈추고 알린다.
   - 앱에서 후기 함수가 '찾을 수 없음'으로 나오면 SQL Editor에서 `notify pgrst, 'reload schema';` 한 번.
3. **코드 반영(Claude, 승인 후)**: main에 합치기(fast-forward) → Streamlit이 1~2분 뒤 새 코드로 다시 시작. 설정을 넣기 전이라 이전 초안·후기는 숨겨져 있다.
4. **설정(운영자)**: Streamlit → Settings → Secrets 맨 아래에 두 줄 추가 → Save.
   ```toml
   DRAFT_ARCHIVE_ENABLED = "true"
   REVIEWS_ENABLED = "true"
   ```
5. **운영 화면 확인(Claude)**: 관리자 계정으로 메뉴·첫 화면·사건 4단계·시험 사건의 '새로 만들기'와 되돌리기·후기 쓰기 → 후기 검토에서 게시 → 확인 뒤 시험 후기 삭제.
   일반 계정으로 첫 화면·기존 사건 열기·저장만 확인(실제 이용자 자료는 열지 않음).

## 문제가 생기면
1. 설정 두 줄을 지우고 Save → 이전 초안·후기 기능이 바로 숨겨진다(자료는 남음).
2. 화면 자체에 문제가 있으면 main을 이전 커밋(`dbceaeb`)으로 되돌린다(운영자 승인 후 Claude).
3. 새 표를 없애야 하면 되돌리기 SQL. 기존 사건·초안·작업 공간은 그대로다(리허설로 확인).
4. 기존 자료 자체가 상했을 때만 1단계 백업을 새 연습용 프로젝트에 복구해 확인한다(`docs/backup-restore-drill-plan.md`, 운영 주소로는 스크립트가 복구를 거부).
