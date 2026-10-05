# 고소장 초안 저장 · 개인정보처리방침 개정 적용 절차

작성: 2026-10-06. 앱 코드는 main에 반영되어 있지만, 아래 설정을 하기 전까지 초안 저장 기능은 꺼져 있고 화면도 기존과 같습니다.

## 1. 운영 DB에 실행할 SQL (Supabase Dashboard > SQL Editor)

| 순서 | 파일 | 하는 일 |
|---|---|---|
| ① | [supabase/migrations/20261006000000_case_drafts.sql](../supabase/migrations/20261006000000_case_drafts.sql) | `public.case_drafts` 테이블과 본인 전용 접근 정책 생성. `public.cases`에 "사건 삭제 시 초안도 삭제" 트리거 추가 |
| ② | [supabase/migrations/20261013000000_update_consent_privacy_version_v3.sql](../supabase/migrations/20261013000000_update_consent_privacy_version_v3.sql) | `record_consent` 함수가 새 방침 버전 `2026-10-13`(과 기존 `2026-09-27`)을 받도록 교체 |
| ③ | [supabase/tests/case_drafts_access_test.sql](../supabase/tests/case_drafts_access_test.sql) | 테스트 계정 두 개로 계정 간 차단 확인 (변경 없이 되돌림). 기대 결과는 [tests/README.md](../supabase/tests/README.md) |

### 영향 범위
- 기존 사건·증거파일·동의 기록·계정 데이터는 한 건도 바꾸거나 지우지 않습니다.
- `cases` 테이블의 열·정책·권한은 그대로이고, 삭제 트리거 하나만 추가됩니다. 사건을 지울 때(앱 화면, 회원탈퇴 Edge Function 모두) 그 사건의 초안이 함께 지워집니다. 적용 직후에는 초안이 없으므로 실제로 지워지는 것은 없습니다.
- 회원탈퇴: `auth.users` 삭제 시 외래키(on delete cascade)로 초안이 삭제됩니다. 배포된 `delete-account` Edge Function은 바꿀 필요가 없습니다.
- `record_consent` 교체는 기존 버전도 계속 받으므로, 앱 설정을 바꾸기 전에 실행해도 동의 저장이 실패하지 않습니다.
- 트리거를 추가하는 순간 `cases`에 짧은 잠금이 걸립니다(보통 1초 미만).

### 되돌리기
[supabase/rollbacks/20261006000000_case_drafts_rollback.sql](../supabase/rollbacks/20261006000000_case_drafts_rollback.sql) — 앱의 `DRAFT_SAVE_ENABLED`를 먼저 끈 뒤 실행합니다. 저장된 초안만 삭제되고 다른 자료에는 영향이 없습니다.

## 2. 앱 설정 (Streamlit Cloud > App settings > Secrets)

| 시점 | 설정 | 효과 |
|---|---|---|
| SQL 실행 직후 (시행 전 안내) | `PRIVACY_POLICY_UPCOMING_DATE = "2026-10-13"` | 로그인 화면과 메인 화면에 "개인정보처리방침이 바뀝니다" 안내와 새 방침 전문이 보입니다 |
| 시행일(2026-10-13) | `PRIVACY_POLICY_EFFECTIVE_DATE = "2026-10-13"`, `DRAFT_SAVE_ENABLED = "true"` | 새 방침 적용. 모든 이용자가 다음 이용 때 한 번 다시 동의합니다. 초안 저장 기능이 나타납니다 |

- 시행일을 바꾸려면 ②번 SQL의 `'2026-10-13'`과 위 두 설정을 같은 날짜로 바꿉니다. 날짜는 2026-10-13 이후여야 새 방침 문구가 적용됩니다.
- 방침 변경을 얼마나 미리 알려야 하는지는 법률 검토가 필요합니다.

## 3. 개인정보처리방침 개정 내용 (v3, [policy_text.py](../policy_text.py))

| 보완한 내용 | 근거 |
|---|---|
| 고소장 초안 내용·저장 일시 | 이번 기능 |
| 동의한 약관·방침 버전, 만 14세 확인 여부·일시 | `consent_records`, 가입 시 `user_metadata`, Google `age_14_confirmed_at`, 네이버 `age_confirmed_at` |
| 로그인·인증 기록(접속 IP, 일시) | Supabase Auth 기록 |
| AI 상담 질문(저장하지 않음 명시) | 앱 동작 |
| 파기 절차·방법 | 개인정보 보호법 제30조 제1항 제3호의2 |
| 자동 수집 장치(localStorage, sessionStorage, Streamlit 보안 쿠키)와 거부 방법 | 같은 조 제7호, 앱 동작 |
| 처리 위탁에 Streamlit Community Cloud(Snowflake) 추가 | 앱 호스팅 (`*.streamlit.app`) |
| 국외 이전: 받는 자·연락처·국가·시기·방법·항목·목적·기간·거부 방법 | 개인정보 보호법 제28조의8 제2항 |
| 보호책임자 명칭, 권리 행사 방법(열람·정정·삭제·처리정지) | 같은 법 제30조 제1항 제5·6호 |

국외 이전 정보의 출처(2026-10-06 확인):
- Supabase: [Transfer Impact Assessment](https://supabase.com/downloads/docs/Supabase+TIA+250314.pdf) "Countries to which the personal data may be transferred: United States of America, Singapore", [개인정보처리방침](https://supabase.com/privacy) (Supabase Pte. Ltd., privacy@supabase.com), [하위처리자 목록 2026-06-01](https://supabase.com/legal/customer-resources/subprocessor-list)
- Google Gemini API: [추가 약관](https://ai.google.dev/gemini-api/terms) (유료 서비스 입력·응답 미학습, "Google 또는 그 대리인이 시설을 운영하는 국가"에 일시 저장·캐시), [오남용 모니터링](https://ai.google.dev/gemini-api/docs/usage-policies) (55일 보관), [Google 개인정보처리방침](https://policies.google.com/privacy?hl=ko) (googlekrsupport@google.com)
- Streamlit: [Streamlit 개인정보처리방침](https://streamlit.io/privacy-policy) (Snowflake 방침 적용), [Snowflake 개인정보처리방침](https://www.snowflake.com/en/legal/privacy/privacy-policy/) (주 서버 미국, privacy@snowflake.com)

Google은 처리 국가를 구체적으로 공개하지 않아, 방침에도 "구체적인 국가를 공개하지 않습니다"라고 적었습니다. 추측한 국가는 넣지 않았습니다.

## 4. 법률 검토가 필요한 부분
- 국외 이전을 "계약 이행을 위한 처리위탁·보관 + 처리방침 공개"(제28조의8 제1항 제3호 가목)로 처리하는 것이 적절한지, 아니면 별도 동의가 필요한지
- Google·Snowflake의 연락처로 공개 고객 문의 창구를 적은 것이 "이전받는 자의 연락처"로 충분한지
- 보호책임자 연락처를 이메일만 적어도 되는지(제30조 제1항 제6호 "전화번호 등 연락처")
- 방침 변경 사전 안내 기간, 재동의 방식
- 사업자등록 전 개인 운영자로서의 표시 방식
- Supabase 백업에 삭제된 정보가 얼마나 남는지(요금제별 백업 보관 기간)를 확인해 파기 항목에 적을지
