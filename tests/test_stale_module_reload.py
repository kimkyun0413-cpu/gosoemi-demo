"""Streamlit Cloud에서 실제로 난 오류(예전 모듈이 메모리에 남아 ImportError) 재현과 app.py 대응 확인.

실행: python3 -m unittest discover -s tests
"""

import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class StaleModuleReloadTest(unittest.TestCase):
    def test_reload_restores_names_missing_from_a_stale_module(self):
        import voice_input

        # 배포 전 예전 버전처럼, 새로 추가된 이름이 없는 모듈이 메모리에 남아 있는 상황을 만든다.
        for name in ("TEST_PHRASE_KEYWORDS", "test_phrase_check"):
            delattr(voice_input, name)
        with self.assertRaises(ImportError):
            exec("from voice_input import TEST_PHRASE_KEYWORDS", {})

        # app.py와 같은 순서로 다시 불러오면 배포된 파일 내용으로 돌아온다.
        import auth_ip, billing, case_guide, policy_text  # noqa: E401
        for module in (case_guide, policy_text, billing, auth_ip, voice_input):
            importlib.reload(module)
        namespace = {}
        exec("from voice_input import TEST_PHRASE_KEYWORDS, test_phrase_check", namespace)
        self.assertIn("판매왕", namespace["TEST_PHRASE_KEYWORDS"])

    def test_app_reloads_every_local_helper_it_imports_from(self):
        source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        reload_block = source.split("importlib.reload(_module)")[0]
        for module in ("case_guide", "policy_text", "billing", "auth_ip", "voice_input"):
            self.assertIn(f"import {module} as _{module}_module", reload_block)
            self.assertIn(f"from {module} import", source)


if __name__ == "__main__":
    unittest.main()
