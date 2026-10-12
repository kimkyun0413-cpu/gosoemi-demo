# 도메인(gosomiapp.com)·호스팅·메일 연결 준비 (2026-10-13, 아무것도 적용하지 않음)

**상태:** 도메인 구입 예정(미결제). DNS·로그인 연결 주소·운영 설정 변경 없음. 결제·계약 없음. AI 멈춤·이메일 신규 가입 중단 유지.
**서비스 상태:** 현재 무료 시험 운영, 향후 유료 판매 예정(가격 미정).

> **도메인만 사서는 사이트가 연결되지 않습니다.** 아래 1절처럼 지금 호스팅(Streamlit Community Cloud)은 내 도메인을 붙일 수 없어서, 주소창에 gosomiapp.com이 계속 보이게 하려면 호스팅을 옮기는 작업이 따로 필요합니다.

## 1. 지금 Streamlit 앱에 도메인을 연결할 수 있는가 — 공식 문서 확인

| 방식 | 가능? | 주소창 | 근거·문제 |
|---|---|---|---|
| **직접 연결**(gosomiapp.com에서 앱이 그대로 열림) | **불가** | — | 공식 문서는 `<원하는 이름>.streamlit.app` 형태의 하위 주소 변경만 안내함(6~63자). 내 도메인 연결 기능은 문서에 없음([App settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings), [Deploy](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)) |
| **이동(리디렉트)**: gosomiapp.com을 열면 지금 주소로 넘어감 | 가능(도메인 업체의 '포워딩' 기능) | **…streamlit.app으로 바뀜** | 앱은 그대로. 로그인 연결 주소도 그대로라 안전. 단 아래 약관 문제는 그대로 |
| **틀(iframe) 안에 넣기**: gosomiapp.com 페이지 안에 앱을 띄움 | 공개 앱은 `?embed=true`로 가능([Embed your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app/embed-your-app)) | gosomiapp.com 유지 | **추천하지 않음.** 고소미의 로그인 유지(브라우저 저장소)·Google·네이버 로그인이 다른 사이트 틀 안에서는 브라우저가 막을 수 있고(특히 Safari), 공식 문서도 이 부분을 다루지 않음. 약관 문제도 그대로 |
| (참고) 하위 주소만 바꾸기: 예) `gosomi.streamlit.app` | 가능 | …streamlit.app | 바꾸면 로그인 연결 주소 모두 바꿔야 함(4절) |

**유료 판매 전 꼭 알아야 할 것 — Streamlit 이용약관(2025-11 갱신, [Terms of use](https://streamlit.io/terms-of-use) Addendum 4):**
개인정보를 처리하는 경우 Community Cloud는 **개인적·비상업적 목적으로만** 쓸 수 있고, 사업과 관련해 쓸 수 없으며, 금융·건강·생체 정보 등 민감한 개인정보 처리를 금지합니다. 고소미는 회원·사건 정보를 처리하므로, **유료 판매를 시작하기 전에 호스팅을 옮겨야 합니다.** 지금의 무료 시험 운영도 이 조항에 맞는지는 전문가 검토 자료(부록 A8)에 질문으로 넣었습니다.
자원 한도(공식): 앱당 CPU 최대 2코어, 메모리 최대 2.7GB, 12시간 접속이 없으면 잠듦([Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)).

## 2. 호스팅: 지금 유지 vs 옮기기

| 안 | 월 비용(공식 가격, 2026-10-13 확인) | 작업량 | 장점 | 단점 |
|---|---|---|---|---|
| A. 지금 유지 + 도메인은 이동(리디렉트)만 | 0원(도메인 값 별도) | 30분(도메인 업체 화면) | 바로 가능, 앱·로그인 변경 없음 | 주소창은 streamlit.app. **유료 판매 불가(약관)** |
| B. **Render 웹 서비스 Standard**(1 CPU, 2GB) | **$25** ([요금](https://render.com/docs/compute-plans)) | Claude 1~2일 + 운영자 확인 반나절 | 내 도메인·인증서 기본 제공, 서버가 계속 켜져 있음, Git 연결 배포(지금과 비슷) | 서버 위치 선택지: 미국·독일·**싱가포르**(서울 없음) → 방침 국외 이전 표 수정 |
| B'. Render Starter(0.5 CPU, 512MB) | $7 | B와 같음 | 저렴 | **메모리 부족 위험**: '내 자료 모두 내려받기'가 최대 약 200MB를 메모리에서 묶음 → 추천 안 함 |
| C. Google Cloud Run(서울 리전) | 사용량 과금(초 단위, 서울은 별도 단가 — 가입 전 [요금](https://cloud.google.com/run/pricing) 확인) | Claude 2~3일 | 서버가 서울에 있음 | 연결 유지(WebSocket)가 '최선 노력'이고 최대 요청 시간 제한이 있어 Streamlit 화면이 끊길 수 있음([WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets)) — 설정·시험이 더 필요 |
| (무료 단계) Render Free | 0원 | — | — | 15분 접속이 없으면 꺼지고 Render가 **운영용으로 쓰지 말라**고 안내([Free](https://render.com/docs/free)) → 제외 |

**추천:**
1. **지금(무료 시험 운영):** 안 A. 도메인을 사면 '포워딩'으로 지금 주소로만 이동시키고, 앱·로그인 설정은 그대로 둡니다. (원하면 도메인은 메일용으로만 먼저 써도 됩니다.)
2. **유료 판매 전:** 안 B(Render Standard, 싱가포르)로 옮기고 `app.gosomiapp.com`(또는 gosomiapp.com)을 직접 연결. 옮기기 전에 방침의 위탁·국외 이전 표(Streamlit/Snowflake → Render)를 고치고 시행 절차를 거쳐야 합니다. Render와의 계약·결제는 운영자 승인 후.
- 어떤 안이든 **DB(Supabase, 서울)·AI(Gemini)는 그대로**입니다. 옮기는 것은 화면을 돌리는 서버뿐입니다.

## 3. 메일 — '보내는 서비스'와 '받는 메일함'은 다른 것

| 구분 | 용도 | 추천 | 비용(공식) | 필요한 것 |
|---|---|---|---|---|
| **보내는 서비스** | 가입 인증 메일, 비밀번호 찾기 메일(Supabase가 보냄) | **Resend** | Free: 월 3,000통·하루 100통·도메인 3개, Pro $20/월(월 5만 통) ([요금](https://resend.com/pricing)) | 도메인 DNS에 Resend가 알려 주는 기록 추가(인증) |
| **받는 메일함** | 이용자 문의(예: help@gosomiapp.com) | (가) **Cloudflare Email Routing** — 받은 메일을 운영자의 기존 메일함으로 전달 / (나) 유료 업무 메일함(Google Workspace 등) | (가) Free 요금제에서 사용 가능([문서](https://developers.cloudflare.com/email-routing/)) / (나) 업체 요금 확인 필요 | (가) 도메인 DNS를 Cloudflare에서 관리해야 함([문서](https://developers.cloudflare.com/email-routing/get-started/enable-email-routing/)). 답장은 기존 메일 주소로 나감 → help@ 주소로 답장하려면 (나) |

- **왜 지금 필요한가:** Supabase 기본 메일은 팀원 주소에만, 시간당 2통만 보냅니다. 이용자에게 인증·비밀번호 메일을 보내려면 외부 메일(SMTP) 연결이 필요합니다([Supabase SMTP](https://supabase.com/docs/guides/auth/auth-smtp)). 연결 후 Supabase 기본 제한은 시간당 30통(대시보드 Rate Limits에서 조정).
- **충돌 없게:** 보내는 기록(Resend)은 Resend가 안내하는 하위 주소용 기록, 받는 기록(MX)은 Cloudflare가 넣는 기록입니다. 서로 다른 기록이라 함께 쓸 수 있습니다(실제 값은 각 화면에서 복사).
- **방침 영향:** Resend(미국)는 이메일 주소·메일 내용을 처리하는 새 수탁자이고, Cloudflare(가)를 쓰면 문의 메일도 거칩니다. **켜기 전에 방침의 위탁·국외 이전 표에 추가**해야 합니다(v3 수정안에 아직 없음).

### Supabase에 넣을 값 (Resend 공식 안내, [문서](https://resend.com/docs/send-with-supabase-smtp))
| 칸 | 값 |
|---|---|
| Sender email | `noreply@gosomiapp.com` (예시, 도메인 인증 후) |
| Sender name | `고소미` |
| Host | `smtp.resend.com` |
| Port | `465` |
| Username | `resend` |
| Password | Resend에서 만든 **API 키**(Supabase 화면에만 붙여 넣기. 채팅·파일·저장소에 남기지 않기) |

## 4. 주소를 바꿀 때 함께 바꿔야 하는 곳 (호스팅을 옮기거나 하위 주소를 바꿀 때만, 지금은 바꾸지 않음)
| 위치 | 항목 |
|---|---|
| Streamlit(또는 새 호스팅) 설정 | `APP_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URL` |
| Supabase → Authentication → URL Configuration | Site URL, Redirect URLs(새 주소 추가, 옛 주소는 확인 후 삭제) |
| Supabase Edge Function `naver-auth` 비밀값 | `APP_BASE_URL` (네이버 로그인 후 돌아오는 주소) |
| 네이버 개발자센터 | 서비스 URL(콜백은 Supabase 함수 주소라 그대로) |
| Google Cloud → OAuth 동의 화면 | 앱 홈페이지·개인정보처리방침 링크, 승인된 도메인 |
| Supabase 메일 템플릿 | 링크가 새 주소로 가는지 |
| 방침·약관 | 위탁·국외 이전 표(호스팅 업체), 문의 이메일 |

## 5. 적용 순서 (각 단계 운영자 승인 후)
1. 도메인 구입(운영자) → 2. 도메인 이동(리디렉트) 설정(선택) → 3. Resend 가입·도메인 인증 → 4. 받는 메일함 연결 → 5. **방침 수정안에 Resend(·Cloudflare) 추가 → 전문가 검토 반영 후 시행** → 6. Supabase SMTP 저장, 시험 계정으로 메일 수신 확인 → 7. 비밀번호 찾기(`PASSWORD_RESET_ENABLED`)·이메일 가입(`EMAIL_SIGNUP_ENABLED`) 다시 켜기는 따로 결정 → 8. 유료 판매 전 호스팅 이전(2절 B)

## 출처 (2026-10-13 확인)
- Streamlit: [App settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings) · [Embed your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app/embed-your-app) · [Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status) · [Terms of use](https://streamlit.io/terms-of-use)
- Render: [Compute plans](https://render.com/docs/compute-plans) · [Free instances](https://render.com/docs/free)
- Google Cloud Run: [WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets) · [Pricing](https://cloud.google.com/run/pricing)
- Supabase: [Custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp) · Resend: [Pricing](https://resend.com/pricing), [Supabase SMTP](https://resend.com/docs/send-with-supabase-smtp)
- Cloudflare: [Email Routing](https://developers.cloudflare.com/email-routing/), [Enable Email Routing](https://developers.cloudflare.com/email-routing/get-started/enable-email-routing/)
