"""음성 대화 답변 목소리: 기기 목소리(기본)·서버 목소리(Gemini 음성 생성, 관리자 시험) 검사.

- 순수 함수(chat_assistant): 읽기 좋은 표기, 서버 음성 길이, 요청 형식, 응답에서 음성 꺼내기
- 실제 app.py 처리(가짜 Gemini): 서버 목소리 사용 조건, 실패 시 글 답변 유지, 다시 시도, 미리듣기, 호출 제한, 정리
브라우저 부품의 재생·마이크 끄기는 자동 테스트로 흉내 낼 수 없어 실제 브라우저에서 따로 확인한다.
실행: python3 -m unittest discover -s tests
"""

import base64
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chat_assistant as c  # noqa: E402
import fake_services as fake  # noqa: E402

# 사용량 기록은 서버 메모리에 계속 남으므로, 호출 제한 시험은 이 시험만 쓰는 관리자 계정으로 한다.
TTS_LIMIT_ADMIN_ID = "77777777-7777-4777-8777-777777777777"
SELFTEST_TEXT = "안녕하세요, 고소미 음성 시험입니다."


def tts_calls() -> list:
    """서버가 처음 뜰 때 한 번 하는 자체 시험(뒤에서 실행)을 뺀 음성 생성 요청 본문."""
    return [call["body"] for call in fake.TTS_CALLS if call["body"]["input"][0]["content"][0]["text"] != SELFTEST_TEXT]


class HelperTest(unittest.TestCase):
    def test_tts_text_reads_dates_and_ranges_clearly(self):
        self.assertEqual(c.tts_text("2026-10-20까지"), "2026년 10월 20일까지")
        self.assertEqual(c.tts_text("2026.1.5 접수"), "2026년 1월 5일 접수")
        self.assertEqual(c.tts_text("3~5일"), "3에서 5일")
        self.assertEqual(c.tts_text("2026-13-40"), "2026-13-40")           # 날짜가 아니면 그대로
        self.assertEqual(c.tts_text("45만 원, 010-1234-5678"), "45만 원, 010-1234-5678")  # 금액·전화번호는 바꾸지 않는다
        self.assertIn("2026년 10월 20일", c.speech_text("**기한**: 2026-10-20"))

    def test_server_speech_text_is_capped(self):
        long = "가나다라마바사요. " * 200
        out = c.server_speech_text(long)
        self.assertLessEqual(len(out), c.SERVER_SPEECH_MAX_CHARS + 30)
        self.assertTrue(out.endswith("나머지는 화면의 글로 확인해 주세요."))
        self.assertEqual(c.server_speech_text("짧은 답변"), "짧은 답변")

    def test_request_shape_and_voice_check(self):
        body = c.tts_request("안녕하세요", "Kore", "gemini-3.8-flash-lite-tts")
        self.assertEqual(body["model"], "gemini-3.8-flash-lite-tts")
        part = body["input"][0]["content"][0]
        self.assertEqual((part["type"], part["text"]), ("text", "안녕하세요"))
        self.assertEqual(part["annotations"][0]["type"], "speech_metadata")
        self.assertEqual(body["generation_config"]["speech_config"], [{"voice": "Kore"}])
        self.assertEqual(body["response_format"]["type"], "audio")
        with self.assertRaises(ValueError):
            c.tts_request("안녕하세요", "아무나", "m")

    def test_extract_audio_from_sdk_rest_and_pcm(self):
        wav = fake.fake_wav()
        b64 = base64.b64encode(wav).decode()
        self.assertEqual(c.extract_tts_audio(SimpleNamespace(output_audio=SimpleNamespace(data=b64))), wav)
        rest = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": b64}]}]}
        self.assertEqual(c.extract_tts_audio(rest), wav)
        pcm = b"\x01\x00" * 4000
        out = c.extract_tts_audio({"output_audio": {"data": base64.b64encode(pcm).decode()}})
        self.assertEqual(out[:4], b"RIFF")
        self.assertEqual(out[44:], pcm)
        for bad in ({}, {"output_audio": {"data": ""}}, {"output_audio": {"data": base64.b64encode(b"RIFF").decode()}}):
            with self.assertRaises(ValueError):
                c.extract_tts_audio(bad)

    def test_error_summary_hides_key_and_text(self):
        body = '{"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Unknown name \\"type\\" key=AIzaSyA1234567890abcdefghijk 홍길동 님 문장"}}'
        err = c.tts_error_from_response(400, body, hide=("홍길동 님 문장",))
        self.assertEqual((err.http_status, err.status), (400, "INVALID_ARGUMENT"))
        self.assertNotIn("AIza", err.detail)
        self.assertNotIn("홍길동", err.detail)
        self.assertEqual(c.tts_error_from_response(500, "not json").status, "")

    def test_wav_seconds(self):
        self.assertEqual(c.wav_seconds(fake.fake_wav(1.5)), 1.5)
        self.assertEqual(c.wav_seconds(b"nope"), 0.0)

    def test_tts_goes_to_rest_endpoint_not_sdk(self):
        # SDK(2.17.0)는 speech_metadata 주석을 UNKNOWN으로 바꿔 보내 400이 났다. 본문은 문서 형식 그대로 보낸다.
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("interactions.create", src)
        self.assertEqual(c.TTS_ENDPOINT, "https://generativelanguage.googleapis.com/v1beta/interactions")
        import json
        self.assertNotIn("UNKNOWN", json.dumps(c.tts_request("안녕", "Kore", "m")))

    def test_component_keeps_safety_rules(self):
        js = c.VOICE_CHAT_JS
        self.assertIn("v.localService", js)                 # 기기 목소리는 기기 안 음성만
        self.assertIn("micEnabled(S, false);\n  showFail(S, false);", js)   # 재생 전에 마이크를 끈다
        self.assertIn("S.player.pause()", js)               # 정지·종료·화면 이동 때 서버 음성도 멈춘다
        self.assertIn("<details class=\"gv-set\"><summary>목소리 설정</summary>", js)  # 설정은 접어 둔다
        self.assertNotIn("GEMINI", js)                     # 키·모델 정보는 브라우저 코드에 없다


# 실제 app.py를 실행한 뒤, 세션에 넣어 둔 호출을 app.py 안의 함수로 처리한다(부품 신호 대신).
HARNESS = """
import runpy, streamlit as st
g = runpy.run_path({app!r}, run_name="__main__")
call = st.session_state.pop("_test_call", None)
if call:
    name, arg = call
    st.session_state["_test_result"] = g[name](arg) if arg is not None else g[name]()
st.session_state["_server_tts"] = g["server_tts_available"]()
"""


class AppVoiceTest(unittest.TestCase):
    ENV = {
        "SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake-key",
        "ADMIN_USER_IDS": f"{fake.ADMIN_ID},{TTS_LIMIT_ADMIN_ID}", "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자",
        "PRIVACY_CONTACT_EMAIL": "contact@example.com", "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27",
        "AI_CALLS_PER_HOUR": "30", "VOICE_SERVER_TTS_ENABLED": "true",
    }

    def setUp(self):
        fake.reset()
        self.env = dict(self.ENV)
        # 호출 제한 기록이 다른 시험과 섞이지 않게 시험마다 새 관리자 계정을 쓴다.
        import uuid
        self.admin = str(uuid.uuid4())
        self.env["ADMIN_USER_IDS"] = f"{self.admin},{TTS_LIMIT_ADMIN_ID}"

    def start(self, user_id=None, **env):
        user_id = user_id or self.admin
        self.env.update(env)
        patches = [mock.patch.dict(os.environ, self.env), mock.patch("supabase.create_client", fake.fake_create_client),
                   mock.patch("google.genai.Client", fake.FakeGenaiClient), mock.patch("httpx.post", fake.fake_tts_post)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])
        at = AppTest.from_string(HARNESS.format(app=os.path.join(ROOT, "app.py")), default_timeout=60)
        at.session_state["supabase_session"] = fake.session_for(user_id, "x@example.com")
        at.session_state["browser_login_checked"] = True
        at.session_state["nav_page"] = "chat"
        at.session_state["chat_mode"] = "voice"
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at

    def call(self, at, name, arg=None):
        at.session_state["_test_call"] = (name, arg)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        return at.session_state["_test_result"] if "_test_result" in at.session_state else None

    def utterance(self, seq=1, voice="Kore"):
        payload = {"sid": "abc123", "seq": seq, "audio": base64.b64encode(fake.fake_wav(0.5, 16000)).decode()}
        if voice:
            payload["tts"] = {"voice": voice}
        return payload

    def test_admin_server_voice_reply_has_audio(self):
        fake.REPLIES[:] = ["9월 2일에 45만 원을 보냈어요", "2026-10-20까지 이체 내역을 준비해 두세요."]
        at = self.start()
        self.assertTrue(at.session_state["_server_tts"])
        self.call(at, "handle_voice_turn", self.utterance())
        reply = at.session_state["chat_voice_reply"]
        self.assertEqual(reply["text"], "2026년 10월 20일까지 이체 내역을 준비해 두세요.")
        self.assertEqual(base64.b64decode(reply["audio"])[:4], b"RIFF")
        call = [c_ for c_ in fake.TTS_CALLS if c_["body"]["input"][0]["content"][0]["text"] == reply["text"]][0]
        self.assertEqual(call["url"], c.TTS_ENDPOINT)
        self.assertTrue(call["has_key"])                                   # 키는 머리말로만 보낸다(본문에 없음)
        self.assertNotIn("fake-key", str(call["body"]))
        body = call["body"]
        self.assertEqual(body["model"], "gemini-3.8-flash-lite-tts")
        self.assertEqual(body["generation_config"]["speech_config"], [{"voice": "Kore"}])
        self.assertEqual(body["input"][0]["content"][0]["text"], reply["text"])
        self.assertEqual(len(at.session_state["chat_messages"]), 2)      # 앞선 대화 기억 유지
        self.assertEqual(len(fake.CALLS), 2)                               # 받아쓰기·답변은 그대로 2번

    def test_device_voice_sends_no_tts(self):
        fake.REPLIES[:] = ["질문", "답변입니다."]
        at = self.start()
        self.call(at, "handle_voice_turn", self.utterance(voice=None))
        self.assertNotIn("audio", at.session_state["chat_voice_reply"])
        self.assertEqual(tts_calls(), [])

    def test_flag_off_or_regular_user_never_uses_server_voice(self):
        fake.REPLIES[:] = ["질문", "답변입니다."]
        at = self.start(VOICE_SERVER_TTS_ENABLED="false")
        self.assertFalse(at.session_state["_server_tts"])
        self.call(at, "handle_voice_turn", self.utterance())
        self.assertNotIn("audio", at.session_state["chat_voice_reply"])
        self.assertEqual(tts_calls(), [])
        user = self.start(user_id=fake.USER_ID, VOICE_SERVER_TTS_ENABLED="true", VOICE_CHAT_ENABLED="true",
                          AI_CHAT_V2_ENABLED="true")
        self.assertFalse(user.session_state["_server_tts"])               # 방침 v3 시행 전 일반 이용자

    def test_tts_failure_keeps_text_answer_then_retry_works(self):
        fake.REPLIES[:] = ["질문", "답변입니다."]
        fake.TTS_REPLIES[:] = [(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})]
        at = self.start()
        self.call(at, "handle_voice_turn", self.utterance())
        reply = at.session_state["chat_voice_reply"]
        self.assertEqual(reply["text"], "답변입니다.")
        self.assertNotIn("audio", reply)
        self.assertIn("기기 목소리", reply["tts_error"])
        self.assertEqual(len(at.session_state["chat_messages"]), 2)      # 글 답변은 대화에 남는다
        self.call(at, "handle_tts_retry", {"sid": "abc123", "id": 1, "voice": "Kore", "n": 1})
        reply = at.session_state["chat_voice_reply"]
        self.assertIn("audio", reply)
        self.assertNotIn("tts_error", reply)
        self.assertEqual(reply["try"], 1)
        self.assertEqual(len(fake.CALLS), 2)                               # 다시 시도는 질문·답변을 다시 보내지 않는다
        self.call(at, "handle_tts_retry", {"sid": "other", "id": 1, "voice": "Kore", "n": 2})  # 다른 대화의 요청은 무시
        self.assertEqual(len(tts_calls()), 2)

    def test_preview_uses_fixed_sentence_and_validates_voice(self):
        at = self.start()
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 1, "voice": "Iapetus"})
        preview = at.session_state["chat_voice_preview"]
        self.assertEqual((preview["sid"], preview["n"]), ("abc123", 1))
        self.assertIn("audio", preview)
        self.assertEqual(tts_calls()[0]["input"][0]["content"][0]["text"], c.VOICE_PREVIEW_TEXT)
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 2, "voice": "<script>"})
        self.assertEqual(len(tts_calls()), 1)

    QUOTA_429 = (429, {"error": {"code": 429, "message": "Rate limit exceeded for model gemini-3.8-flash-lite-tts "
                                 "(limit: 10 requests per day on Free Tier). Please retry in 7h12m57s or upgrade your tier."}})

    def test_daily_quota_error_is_explained_and_not_retried_as_success(self):
        fake.TTS_REPLIES[:] = [self.QUOTA_429]
        at = self.start()
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 1, "voice": "Sulafat"})
        preview = at.session_state["chat_voice_preview"]
        self.assertNotIn("audio", preview)
        self.assertIn("사용량 한도", preview["error"])
        self.assertIn("약 7시간 뒤", preview["error"])
        fake.REPLIES[:] = ["질문", "답변입니다."]
        fake.TTS_REPLIES[:] = [self.QUOTA_429]
        self.call(at, "handle_voice_turn", self.utterance(voice="Sulafat"))
        reply = at.session_state["chat_voice_reply"]
        self.assertEqual(reply["text"], "답변입니다.")                      # 글 답변은 그대로
        self.assertIn("사용량 한도", reply["tts_error"])

    def test_preview_audio_is_reused_per_voice(self):
        at = self.start()
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 1, "voice": "Sulafat"})
        first = len(tts_calls())
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 2, "voice": "Sulafat"})
        self.assertEqual(len(tts_calls()), first)                         # 같은 목소리는 다시 만들지 않는다
        self.assertIn("audio", at.session_state["chat_voice_preview"])
        self.assertEqual(at.session_state["chat_voice_preview"]["n"], 2)

    def test_no_startup_self_test(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            self.assertNotIn("self_test", f.read())                        # 무료 등급 하루 한도를 깎지 않는다

    def test_tts_counts_toward_hourly_limit(self):
        at = self.start(user_id=TTS_LIMIT_ADMIN_ID, AI_CALLS_PER_HOUR="1")
        # 미리듣기 음성은 목소리별로 재사용되므로, 이 시험에서만 쓰는 목소리 두 개로 확인한다.
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 1, "voice": "Achernar"})
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 2, "voice": "Schedar"})
        self.assertEqual(len(tts_calls()), 1)
        self.assertIn("1시간에 1번", at.session_state["chat_voice_preview"]["error"])

    def test_closing_voice_mode_drops_audio(self):
        fake.REPLIES[:] = ["질문", "답변입니다."]
        at = self.start()
        self.call(at, "handle_voice_turn", self.utterance())
        self.call(at, "handle_voice_preview", {"sid": "abc123", "n": 1, "voice": "Kore"})
        self.call(at, "close_voice_mode")
        self.assertNotIn("chat_voice_reply", at.session_state)
        self.assertNotIn("chat_voice_preview", at.session_state)

    def test_key_not_logged_or_sent_to_browser(self):
        fake.REPLIES[:] = ["질문", "답변입니다."]
        fake.TTS_REPLIES[:] = [(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                               "message": "bad request fake-key 답변입니다. AIzaSyA1234567890abcdefghijklmn"}})]
        at = self.start()
        with mock.patch("builtins.print") as printed:
            self.call(at, "handle_voice_turn", self.utterance())
        logged = " ".join(str(a) for call in printed.call_args_list for a in call.args)
        self.assertNotIn("fake-key", logged)
        self.assertNotIn("답변입니다", logged)                              # 답변 글도 기록하지 않는다
        self.assertNotIn("fake-key", str(at.session_state["chat_voice_reply"]))


if __name__ == "__main__":
    unittest.main()
