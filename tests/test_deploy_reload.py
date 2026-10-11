"""배포(파일 변경) 직후 다른 세션이 보조 모듈을 sys.modules에서 비워도 앱이 멈추지 않는지 검사.

Streamlit은 파일이 바뀌면 감시 중인 모듈을 '다음에 시작하는 실행'에서 sys.modules에서 지운다. 그 순간 다른 세션이
app.py 맨 위의 importlib.reload(...)를 하고 있으면 "module ... not in sys.modules" ImportError가 났다(2026-10-12 운영 로그).
여기서는 reload 직전에 모듈이 지워지는 상황을 흉내 낸다. 실행: python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_services as fake  # noqa: E402

HARNESS = """
import importlib, runpy, sys
_real_reload = importlib.reload
_evicted = []

def reload_after_other_session_evicted(module):
    # 다른 세션이 막 비운 것처럼, 처음 reload하는 순간 그 모듈을 sys.modules에서 지운다.
    if not _evicted:
        _evicted.append(module.__name__)
        sys.modules.pop(module.__name__, None)
    return _real_reload(module)

importlib.reload = reload_after_other_session_evicted
try:
    runpy.run_path({app!r}, run_name="__main__")
finally:
    importlib.reload = _real_reload
"""


class DeployReloadTest(unittest.TestCase):
    def test_app_survives_module_evicted_during_reload(self):
        env = {"SUPABASE_URL": "https://fake.invalid", "SUPABASE_PUBLISHABLE_KEY": "fake", "GEMINI_API_KEY": "fake",
               "PRIVACY_POLICY_ENABLED": "true", "PRIVACY_OPERATOR_NAME": "운영자", "PRIVACY_CONTACT_EMAIL": "c@example.com",
               "PRIVACY_POLICY_EFFECTIVE_DATE": "2026-09-27"}
        with mock.patch.dict(os.environ, env), mock.patch("supabase.create_client", fake.fake_create_client):
            at = AppTest.from_string(HARNESS.format(app=os.path.join(ROOT, "app.py")), default_timeout=60)
            at.session_state["browser_login_checked"] = True
            at.run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(any("고소미" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
