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
