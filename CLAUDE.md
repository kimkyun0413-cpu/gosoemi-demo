# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

고소음이(gosoemi) is a Streamlit web app that helps crime/damage victims organize what happened and produce a reference draft of a criminal complaint (고소장). It is deployed on Streamlit Community Cloud and uses Supabase (auth, Postgres with RLS, Storage) and the Google Gemini API.

- [app.py](app.py) — the whole Streamlit app: auth (email, Naver, Google), consent/age gates, case writing, drafts, evidence files, account deletion.
- [case_guide.py](case_guide.py) — Streamlit-free data and helpers: damage types and official source links, field guides, draft prompt, draft checks, Word export.
- [policy_text.py](policy_text.py) — privacy policy text per version. Never edit a version users already consented to; add a new version instead.
- [voice_input.py](voice_input.py) — voice input rules: allowed audio formats/limits, transcription and field-extraction prompts, checks that flag unstated amounts/dates/numbers. Audio is never stored.
- [chat_assistant.py](chat_assistant.py) — AI chat rules (system instructions, history trimming, duplicate-send guard, user-only text for case extraction) and the hands-free voice conversation browser component (`VOICE_CHAT_JS`: mic only after the user taps, end-of-speech detection, mic muted while the answer is read, on-device Korean voices only). Chat history is never stored in the DB.
- [precedent_search.py](precedent_search.py) — case-law search: link to the official 국가법령정보센터 search page with keyword suggestions; Open API client used only when `LAW_OPEN_API_OC` is set. The app never shows precedents it did not actually retrieve.
- [billing.py](billing.py) — paid-plan settings and display rules only. No payment provider is integrated; sales stay closed. DB/Edge Function drafts live in `supabase/proposals/billing/` and are not applied.
- [auth_ip.py](auth_ip.py) — when to forward the user's IP to Supabase Auth (Sb-Forwarded-For).
- [scripts/](scripts/) — Free-plan backup/restore, voice smoke test, privacy review packet builder. None of them run automatically.
- [docs/decisions-2026-10.md](docs/decisions-2026-10.md) — options prepared for the owner (mail, backup, auth limits, billing, voice test).
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

The app reads all secrets and feature flags from environment variables (Streamlit secrets in production): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `GEMINI_API_KEY`, `APP_BASE_URL`, `PRIVACY_*`, `EVIDENCE_UPLOAD_ENABLED`, `DRAFT_SAVE_ENABLED`, `VOICE_INPUT_ENABLED`, `AI_CHAT_V2_ENABLED`, `VOICE_CHAT_ENABLED`, `ASSIST_V2_ENABLED`, `LAW_OPEN_API_OC`, `NAVER_*`, `GOOGLE_*`, `AI_CALLS_PER_HOUR`, `AI_CALLS_PER_HOUR_TOTAL`, `PASSWORD_RESET_ENABLED`, `BILLING_MODE`, `BILLING_PLANS_JSON`, `ADMIN_USER_IDS` (operator-only voice test mode and diagnostics; Supabase user IDs, verified server-side against the token), `AUTH_IP_FORWARDING_ENABLED` + `SUPABASE_SECRET_KEY` (secret key used only for Auth requests, never for data). Never hardcode keys.

`supabase/tests/local_case_drafts_test.py` and `local_billing_test.py` check the `case_drafts` migration and RLS against a local throwaway Postgres only. It refuses to run against a Supabase host.

## Rules that matter

- Real user data in production must not be read, changed or deleted while developing.
- Data access must use the user's token (`get_user_data_client`/`get_authed_client`); avoid `auth.set_session()` in per-page code because it calls Auth `/user` from the shared server IP.
- Do not change Supabase policies, schema, deployed Edge Functions, or Naver/Google console settings without the owner's explicit approval. Prepare SQL in `supabase/migrations/` with a matching file in `supabase/rollbacks/`.
- Legal guidance shown to users must come from verified official sources (link them). Do not invent statutes, deadlines, precedents or overseas-transfer facts.
- Drafts must not state that a crime was committed or predict outcomes; missing facts are marked `[확인 필요]`.
- Feature flags that collect new personal data must stay off until the privacy policy version that lists them is in effect (see `draft_save_available()`).
