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
    # 두 번째 모듈은 지운 직후 또 다른 세션이 다시 불러온 경우(오류는 나지만 지금은 sys.modules에 있음)를 흉내 낸다.
    if len(_evicted) < 2:
        _evicted.append(module.__name__)
        saved = sys.modules.pop(module.__name__, None)
        try:
            return _real_reload(module)
        finally:
            if len(_evicted) == 2 and saved is not None:
                sys.modules[module.__name__] = saved
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


class ReloadListCoversHelpersTest(unittest.TestCase):
    """app.py가 이름을 가져오는(from X import ...) 이 저장소의 보조 모듈은 모두 배포 때 다시 불러와야 한다.
    빠지면 배포 뒤 예전 모듈이 남아 새 이름을 못 찾고 앱이 열리지 않는다(2026-10-13 data_export 운영 ImportError)."""

    def test_every_local_helper_is_reloaded(self):
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        local = {name[:-3] for name in os.listdir(root) if name.endswith(".py")} - {"app"}
        used = {n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.module in local}
        aliases = {a.asname: a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names if a.asname}
        loop = next(n for n in tree.body if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple))
        reloaded = {aliases[e.id] for e in loop.iter.elts}
        self.assertTrue(used, "app.py에서 보조 모듈 import를 찾지 못함")
        self.assertEqual(sorted(used - reloaded), [], "배포 때 다시 불러오지 않는 보조 모듈")
