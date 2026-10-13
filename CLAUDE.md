# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

고소미 (renamed from 고소음이 on 2026-10-11; technical identifiers such as the repo, app URL, `gosoemi_*` component/storage keys, backup file names and .ics UIDs keep `gosoemi`) is a Streamlit web app that helps crime/damage victims organize what happened and produce a reference draft of a criminal complaint (고소장). It is deployed on Streamlit Community Cloud and uses Supabase (auth, Postgres with RLS, Storage) and the Google Gemini API.

- [app.py](app.py) — the whole Streamlit app: auth (email, Naver, Google), consent/age gates, case writing, drafts, evidence files, account deletion.
- [case_guide.py](case_guide.py) — Streamlit-free data and helpers: damage types and official source links, field guides, draft prompt, draft checks, Word export.
- [policy_text.py](policy_text.py) — privacy policy text per version. Never edit a version users already consented to; add a new version instead. (Only exception so far: the 2026-10-11 service-name change, shown as "고소미(이전 서비스명: 고소음이)" in v2 and the terms.)
- [voice_input.py](voice_input.py) — voice input rules: allowed audio formats/limits, transcription and field-extraction prompts, checks that flag unstated amounts/dates/numbers. Audio is never stored.
- [chat_assistant.py](chat_assistant.py) — AI chat rules (system instructions, history trimming, duplicate-send guard, user-only text for case extraction) and the hands-free voice conversation browser component (`VOICE_CHAT_JS`: mic only after the user taps, end-of-speech detection, mic muted while the answer is read; on-device Korean voices by default, folded '목소리 설정' with voice/speed/preview saved in the browser). Optional Gemini TTS server voice (`tts_request`, `extract_tts_audio`) only when `VOICE_SERVER_TTS_ENABLED` and admin (until a policy version lists it); generated audio is never stored. Chat history is never stored in the DB.
- [precedent_search.py](precedent_search.py) — case-law search: link to the official 국가법령정보센터 search page with keyword suggestions; Open API client used only when `LAW_OPEN_API_OC` is set. The app never shows precedents it did not actually retrieve.
- [case_workspace.py](case_workspace.py) — rules for the per-case workspace (admin preview): missing-info questions, amount/date mismatch notes, timeline split from the user's own story (dates copied, never invented), evidence list with user-written notes (file contents are never analysed), draft rewording prompt and number-change check, bundled TXT/Word export.
- [calendar_ics.py](calendar_ics.py) — user-chosen schedules as an .ics file (Asia/Seoul, optional alarm). The app sends no reminders and never calculates legal deadlines.
- [data_export.py](data_export.py) — '내 자료 모두 내려받기' ZIP builder (Streamlit-free): per-case folders, readme with missing items, secrets excluded, other users' rows/paths filtered, part 1 = text + evidence list, evidence built one ~20MB part at a time on request (previous part dropped first, part dropped after download; no total cap). Export state is cleared on logout/account switch. The app collects data with the user's token only.
- [billing.py](billing.py) — paid-plan settings and display rules only. No payment provider is integrated; sales stay closed. DB/Edge Function drafts live in `supabase/proposals/billing/` and are not applied.
- [auth_ip.py](auth_ip.py) — when to forward the user's IP to Supabase Auth (Sb-Forwarded-For).
- [scripts/](scripts/) — Free-plan backup/restore, voice smoke test, privacy review packet builder. None of them run automatically.
- [docs/decisions-2026-10.md](docs/decisions-2026-10.md), [docs/decisions-2026-10-12.md](docs/decisions-2026-10-12.md) — options prepared for the owner; the 10-12 file is the current single list (Gemini free tier, age terms, mail, backup, unused secrets).
- [docs/domain-hosting-email-plan.md](docs/domain-hosting-email-plan.md) — gosomiapp.com, hosting move before paid sales (Streamlit Community Cloud terms allow only non-commercial use with personal data), Resend SMTP and inbox. Nothing applied.
- `Dockerfile`, `.dockerignore` (allow-list: add every new helper module), `fly.toml`, [deploy/](deploy/) — prepared hosting move (Fly.io or AWS Lightsail Seoul), not deployed anywhere; order and cutover in [docs/server-migration-plan.md](docs/server-migration-plan.md). Streamlit Cloud ignores these files.
- [docs/launch-status.md](docs/launch-status.md) — current launch blockers and next steps; read this first when resuming work.
- [supabase/](supabase/) — migrations, rollbacks, deployed Edge Function sources (`naver-auth`, `delete-account`), and access tests.
- `main.py`, `calendar_test.py` — an old personal CLI assistant, not used by the app.

## Running and testing

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests
deno test supabase/proposals/billing/functions/billing-webhook/handler_test.ts
streamlit run app.py
```

The app reads all secrets and feature flags from environment variables (Streamlit secrets in production): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `GEMINI_API_KEY`, `APP_BASE_URL`, `PRIVACY_*`, `EVIDENCE_UPLOAD_ENABLED`, `DRAFT_SAVE_ENABLED`, `VOICE_INPUT_ENABLED`, `AI_CHAT_V2_ENABLED`, `VOICE_CHAT_ENABLED`, `VOICE_SERVER_TTS_ENABLED` + optional `GEMINI_TTS_MODEL` (server voice; costs per answer), `ASSIST_V2_ENABLED`, `CASE_WORKSPACE_SAVE_ENABLED` (needs migration 20261012000000), `LAW_OPEN_API_OC`, `NAVER_*`, `GOOGLE_*`, `AI_CALLS_PER_HOUR`, `AI_CALLS_PER_HOUR_TOTAL`, `PASSWORD_RESET_ENABLED`, `EMAIL_SIGNUP_ENABLED` (default on; "false" pauses email sign-up while no SMTP is connected), `AI_FEATURES_PAUSED` ("true" stops every Gemini call with a notice; case saving still works), `MIN_USER_AGE` + `TERMS_EFFECTIVE_DATE_V2` (18+ mode, decided 2026-10-13 but not applied; apply the transition SQL in `supabase/proposals/age18/` first — order in [docs/age18-rollout-plan.md](docs/age18-rollout-plan.md)), `TERMS_V2_UPCOMING_DATE` (advance notice of the 18+ terms), `BILLING_MODE`, `BILLING_PLANS_JSON`, `ADMIN_USER_IDS` (operator-only voice test mode and diagnostics; Supabase user IDs, verified server-side against the token), `AUTH_IP_FORWARDING_ENABLED` + `SUPABASE_SECRET_KEY` (secret key used only for Auth requests, never for data; removed from production secrets on 2026-10-12 because forwarding is off). Never hardcode keys.

`supabase/tests/local_case_drafts_test.py`, `local_billing_test.py`, `local_case_workspace_edit_test.py` and `local_backup_restore_test.py` (runs the real backup/restore scripts) check migrations, RLS and backups against a local throwaway Postgres only and refuse to run against a Supabase host. Without Homebrew/Docker, use the EDB macOS binaries as described in [supabase/tests/README.md](supabase/tests/README.md).

## Rules that matter

- Real user data in production must not be read, changed or deleted while developing.
- Data access must use the user's token (`get_user_data_client`/`get_authed_client`); avoid `auth.set_session()` in per-page code because it calls Auth `/user` from the shared server IP.
- Do not change Supabase policies, schema, deployed Edge Functions, or Naver/Google console settings without the owner's explicit approval. Prepare SQL in `supabase/migrations/` with a matching file in `supabase/rollbacks/`.
- Legal guidance shown to users must come from verified official sources (link them). Do not invent statutes, deadlines, precedents or overseas-transfer facts.
- Drafts must not state that a crime was committed or predict outcomes; missing facts are marked `[확인 필요]`.
- Feature flags that collect new personal data must stay off until the privacy policy version that lists them is in effect (see `draft_save_available()`).
