# 서버 이전 준비 (2026-10-14) — 아무것도 만들거나 바꾸지 않음

**목표:** 화면을 돌리는 서버만 Streamlit Community Cloud에서 Fly.io 또는 AWS Lightsail(서울)로 옮기고 `gosomi.ai.kr` 주소를 붙인다.
**옮기지 않는 것:** 이용자 계정·사건·초안·증거파일(모두 Supabase 서울에 그대로), 코드·기능, AI 멈춤 등 운영 설정 값.
**왜 옮기나:** [hosting-cost-comparison.md](hosting-cost-comparison.md) 1절(Streamlit 약관: 개인정보 처리 시 비상업적 이용만, 민감정보 금지), 내 도메인 연결 불가, 로그 확인 어려움.

## 1. 준비한 것 (저장소에 있음, 어디에도 배포 안 함)
| 파일 | 하는 일 |
|---|---|
| `Dockerfile` | 서버 이미지(두 업체 공용). Python 3.13, 같은 requirements. 서버용 Streamlit 설정(파일 감시 끔 → 10-06·10-13 같은 '예전 모듈' 장애가 생기지 않음, 업로드 상한 10MB), 관리자 권한 없는 사용자로 실행, 상태 확인 |
| `.dockerignore` | **허용 목록 방식.** 앱 파일 12개 + 사용량 보고 스크립트 + 화면 테마만. `credentials.json`·`token.json`·`cases.json`·`memory.json`·`schedules.json`·`.env`·비밀 설정·예전 개인 비서 코드·백업·문서·테스트는 들어가지 않음 |
| `.env.example` | 새 서버 설정 **하나의 목록**(코드가 읽는 45개 전부). 비밀값은 비어 있고, AI·일반 공개·새 DB 기능은 '꺼짐'으로 시작 |
| `deploy/compose.yaml` + `deploy/Caddyfile` | Docker 서버(Lightsail 등): 앱 + HTTPS 자동 발급 Caddy, 앱 포트 비공개, 권한 축소, 메모리 상한 768MB, 로그 크기 제한, 자동 재시작, 보안 헤더 |
| `deploy/lightsail/` | 서버 첫 설정 `setup.sh`(Docker·자동 보안 업데이트·스왑 1GB), 맥에서 올리는 `deploy.sh`(허용 목록 파일만 복사, 비밀값은 복사 안 함) |
| `fly.toml` | Fly.io 설정안: 도쿄, 1GB, 꺼지지 않게, 상태 확인 |
| `.github/workflows/verify.yaml` | PR마다 GitHub에서 전체 테스트 + SQL 시험 + **실제 Docker 빌드 + 이미지 실행·상태 확인** |
| `tests/test_deploy_files.py` | 허용 목록에 필요한 모듈이 다 있고 비밀 파일은 없는지, 설정 목록이 코드와 맞고 비밀값이 비었는지, compose·스크립트 경로가 맞는지 |

**확인한 것:** 자동 테스트 254개 통과(2026-10-14 ChatGPT 수정안 PR #1과 통합 후). 허용 목록 파일만 빈 폴더에 복사해 앱을 실행(가짜 Supabase) → 로그인 화면·로그인 뒤 첫 화면 오류 없음, 모든 모듈을 그 폴더에서 불러옴. `fly.toml`·`docker-compose.yml` 문법, 셸 스크립트 문법.
**확인 못 한 것:** 이 맥에는 Docker가 없어 빌드는 GitHub CI에서 확인(PR #1 검사 결과 참고), Fly·Lightsail 실제 동작, HTTPS 인증서 발급, 리눅스에서의 메모리 사용량 → 서버를 만든 뒤 4단계에서 확인.

## 2. 먼저 정할 것 (운영자)
1. **업체:** Fly.io(도쿄, 1GB 월 약 $8.8, 관리 쉬움) / AWS Lightsail(서울, 1GB 월 $7, 서버 관리 부담)
2. **주소:** `gosomi.ai.kr`(앱), 시험은 `test.gosomi.ai.kr`. 도메인 가격은 운영자 확인 1년 15,400원(구매 전)
3. 도메인 `gosomi.ai.kr` 구입 — 메일 연결과 같이 씀

## 3. 개인정보처리방침 (실제 이용자가 새 서버를 쓰기 전에 꼭)
새 서버 업체는 새 수탁자입니다. 방침 v3 수정안의 위탁·국외 이전 표에서 Streamlit(Snowflake, 미국)을 새 업체로 바꾸고 전문가 검토·시행 절차를 거쳐야 합니다.
- Fly.io: 회사가 미국에 있고 서버가 도쿄 → **국외 이전 항목**으로 적어야 함
- Lightsail 서울: 서버가 서울 → 국외 이전은 아니어도 **위탁 항목**에는 적어야 함(회사·연락처는 정한 뒤 공식 자료로 확인)
- 업체를 정하시면 Claude가 문구를 준비합니다. 시험 배포(시험 계정만)는 이 시행 전에도 할 수 있습니다.

## 4. 순서 (각 단계 운영자 승인 후)
**A. 서버 만들기 — 운영자(결제)**
- Fly.io: 가입·결제 수단 등록 → 이후 명령은 Claude가 안내(`fly apps create`, `fly secrets import < .env`, `fly deploy`)
- Lightsail: 콘솔 → 인스턴스 만들기 → 지역 **서울**, Linux **Ubuntu 24.04**, **1GB** → 만든 뒤 **고정 IP 연결**, 방화벽에 **HTTPS(443)** 추가, SSH 키 내려받기

**B. 시험 배포 — Claude(운영자가 지켜봄)**
1. 운영자가 서버에서 `.env`(`.env.example` 복사)를 만들어 Streamlit Secrets 값을 옮김(값은 따옴표 없이 `이름=값`, 파일 권한 600). 값은 채팅에 붙이지 않음
2. 시험 주소 연결: 도메인 DNS에 `test.gosomi.ai.kr` 추가(Lightsail은 A 레코드 → 고정 IP, Fly는 안내되는 값)
3. 배포 → 상태 확인 주소·로그인 화면 확인
4. **로그인 시험을 하려면** Supabase Authentication → URL Configuration → Redirect URLs에 시험 주소를 **추가**(삭제 없음, 운영자 승인). 네이버 로그인은 돌아오는 주소가 하나뿐이라 시험 주소에서는 시험하지 않고 전환 때 확인
5. 시험 계정 A로: 로그인, 사건 보기·저장, 증거파일, '내 자료 모두 내려받기'(1·2번 묶음) — AI는 멈춘 그대로
6. **메모리 측정:** 내려받는 동안 서버 메모리(Lightsail `docker stats`, Fly 대시보드). 1GB에서 여유가 충분한지 확인

**C. 전환 — 이용자가 적은 시간, 30분 안에 연달아**
| 순서 | 어디 | 무엇을 |
|---|---|---|
| 1 | 도메인 DNS | `gosomi.ai.kr` → 새 서버 |
| 2 | 새 서버 설정 | `APP_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URL` = `https://gosomi.ai.kr` |
| 3 | Supabase → URL Configuration | Site URL을 새 주소로, Redirect URLs에 새 주소 추가(옛 주소는 바로 지우지 않음) |
| 4 | Supabase Edge Function `naver-auth` 비밀값 | `APP_BASE_URL` = 새 주소 (네이버 로그인 뒤 돌아오는 곳) |
| 5 | 네이버 개발자센터 | 서비스 URL을 새 주소로(콜백은 Supabase 함수 주소라 그대로) |
| 6 | Google Cloud OAuth 동의 화면 | 홈페이지·개인정보처리방침 링크, 승인된 도메인에 `gosomi.ai.kr` |
| 7 | 확인 | 시험 계정으로 이메일·Google·네이버 로그인, 사건·내려받기, 새로고침 뒤 로그인 유지 |
- 브라우저에 저장된 로그인은 주소별이라 **이용자는 새 주소에서 한 번 다시 로그인**해야 함(계정·자료는 그대로)

**D. 옛 Streamlit 앱 — 바로 지우지 않음**
- 약 2주 그대로 두고, 옛 주소로 들어온 이용자에게 새 주소를 알리는 안내를 띄울지 결정(작은 코드 추가 필요, 아직 안 함)
- 그 뒤 Streamlit 앱 정지, Supabase Redirect URLs에서 옛 주소 삭제(운영자 승인)

## 5. 되돌리는 방법
- 전환 뒤 문제가 생기면: 도메인 DNS를 되돌리거나 이용자에게 옛 주소를 안내하고, Supabase Site URL·`naver-auth`의 `APP_BASE_URL`·네이버 서비스 URL을 옛 주소로 되돌림. 옛 Streamlit 앱은 그대로 돌고 있으므로 바로 다시 쓸 수 있음
- 자료를 옮기지 않으므로 되돌려도 잃는 자료 없음

## 6. 비용 (참고, [hosting-cost-comparison.md](hosting-cost-comparison.md))
- Fly.io 도쿄 1GB 약 $8.8/월 · Lightsail 서울 1GB $7/월(+부가세 여부 미확인)
- 시험 배포 기간에는 옛 앱(무료)과 새 서버가 함께 돌므로 새 서버 요금만 추가

## 7. AI 사용량 기록 DB 연결 준비 (2026-10-14, 적용 안 함 — 운영자 승인 후)
사용량 기록은 **전용 최소 권한 계정 `gosomi_ai_usage`**로만 씁니다(예약·완료 두 함수만 가능, 사건·계정·파일·사용량 표 읽기 불가). Supabase 관리자 키(service_role)는 서버에 두지 않습니다.

**운영자가 할 일 (모두 Supabase 대시보드, 순서대로)**
1. **DB 변경 적용(승인 후):** SQL Editor에서 `supabase/migrations/20261014000000_ai_usage.sql` 실행 → 사용량 표·함수·전용 계정이 만들어짐(되돌리기: `supabase/rollbacks/20261014000000_ai_usage_rollback.sql`, 사건·계정은 그대로)
2. **비밀번호 정하기:** SQL Editor에서 `alter role gosomi_ai_usage password '<긴 무작위 값>';` — 값은 서버 `.env`에만 적고, 채팅·메일·저장소에 남기지 않음
3. **암호화 강제:** Database Settings → SSL Configuration → **Enforce SSL on incoming connections** 켜기(DB가 잠깐 재시작됨, 작은 프로젝트는 수 초)
4. **CA 인증서 내려받기:** 같은 화면의 SSL Configuration에서 인증서 파일을 받아 서버의 `/opt/gosomi/certs/supabase-ca.crt`로 저장(공개 인증서라 비밀은 아님. Fly는 fly.toml 주석대로 비밀값→파일)
5. **연결 주소 만들기:** 대시보드 **Connect → Transaction pooler** 주소에서 사용자만 바꿔 서버 `.env`에 적기
   `AI_USAGE_DB_URL=postgresql://gosomi_ai_usage.<프로젝트ref>:<비밀번호>@<지역>.pooler.supabase.com:6543/postgres?sslmode=verify-full&sslrootcert=/run/certs/supabase-ca.crt`
   - 사용자 이름은 반드시 `gosomi_ai_usage.<프로젝트ref>`(ref를 빼면 'tenant or user not found')
6. **연결 점검(Claude가 함께, 읽기만):** `docker compose --env-file .env -f deploy/compose.yaml exec app python scripts/check_ai_usage_db.py`
   → 비밀번호 로그인·암호화(TLS)·인증서·호스트 확인, 틀린 비밀번호 거부, 비암호화 연결 거부, 권한(예약·완료만) 14개 항목이 모두 '통과'여야 함
7. 통과한 뒤에만 `AI_USAGE_DB_ENABLED=true` (AI 멈춤·일반 공개 설정은 별도 결정)

**적용 전 점검(읽기만, SQL Editor):** 새 계정도 모든 역할에 공통인 PUBLIC 권한을 받으므로, PUBLIC이 실행할 수 있는 public 함수 중 로그인 확인 없이 자료를 다루는 것이 없는지 확인
```sql
select p.proname, p.prosecdef from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public' and has_function_privilege('public', p.oid, 'execute') order by 1;
```

**30일 지난 기록 정리 (무료 요금제에서도 동작)**
- 기본: **AI 요청을 예약할 때마다 30일 지난 기록을 함께 지움**(DB 함수 안, 키·예약 작업 불필요)
- 추가(가능하면): Integrations → Cron에서 pg_cron을 켤 수 있으면 매일 정리
  `select cron.schedule('gosomi-prune-ai-usage', '30 18 * * *', $$select public.prune_ai_usage()$$);` (매일 03:30 한국, 되돌리기 `select cron.unschedule('gosomi-prune-ai-usage');`)
- pg_cron을 쓸 수 없으면: 운영자가 **주 1회** SQL Editor에서 `select public.prune_ai_usage();`(AI 이용이 뜸한 기간에도 30일을 넘기지 않게). 무료 요금제의 pg_cron 사용 가능 여부는 공식 문서에서 확인하지 못함

**보고:** SQL Editor에서 `select public.ai_usage_report();`(계정 식별자·내용 없음)

**로컬에서 확인한 것(2026-10-14):** 암호화·비밀번호만 받는 시험 서버(자체 시험 CA)에서 점검 스크립트 14/14 통과(TLS 1.3), 다른 CA 인증서·인증서에 없는 호스트 이름·`sslmode=require` 모두 거부, 앱 저장소로 실제 예약·완료 성공. **운영 Supabase 풀러 접속과 실제 CA 인증서는 미확인**(적용 후 6단계에서 확인)
