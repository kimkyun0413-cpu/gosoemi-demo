"""브라우저 세션 컴포넌트 key 검사. 운영에서 실제로 난 BidiComponentInvalidIdError 재발 방지.

Streamlit은 컴포넌트 ID(위젯 key 포함)에 '__'가 있으면 오류를 낸다.
실행: python3 -m unittest discover -s tests
"""

import ast
import hashlib
import os
import secrets
import unittest

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_key_builder():
    source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "browser_save_component_key")
    namespace = {"hashlib": hashlib}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["browser_save_component_key"]


class ComponentKeyTest(unittest.TestCase):
    def test_keys_never_contain_double_underscore(self):
        build = load_key_builder()
        nonces = ["_", "__", "a__b", "_abc", "abc_"] + [secrets.token_urlsafe(16) for _ in range(5000)]
        for nonce in nonces:
            self.assertNotIn("__", build(nonce), nonce)

    def test_streamlit_accepts_keys_built_from_underscore_tokens(self):
        build = load_key_builder()
        script = (
            "import streamlit as st\n"
            "comp = st.components.v2.component('key_check', js='export default function(){}')\n"
            f"comp(key={build('_starts_with_underscore')!r}, data={{'x': 1}})\n"
            f"comp(key={build('has__double')!r}, data={{'x': 2}})\n"
        )
        at = AppTest.from_string(script).run()
        self.assertEqual([e.value for e in at.exception], [])

    def test_old_pattern_fails_as_seen_in_production(self):
        script = (
            "import streamlit as st\n"
            "comp = st.components.v2.component('key_check_old', js='export default function(){}')\n"
            "comp(key='browser_login_save__abc', data={'x': 1})\n"
        )
        at = AppTest.from_string(script).run()
        self.assertTrue(at.exception)


if __name__ == "__main__":
    unittest.main()
