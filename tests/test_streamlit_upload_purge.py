"""Streamlit 업로드 저장소에서 음성을 지우는 방식이 이 Streamlit 버전에서 동작하는지 확인한다.

app.py의 purge_uploaded_audio()는 ScriptRunContext.uploaded_file_mgr.remove_file()을 쓴다.
Streamlit을 올리면 이 테스트가 깨지는지 먼저 확인한다. 실행: python3 -m unittest discover -s tests
"""

import unittest

from streamlit.testing.v1 import AppTest

# AppTest는 실행마다 업로드 저장소를 새로 만들므로, 같은 실행 안에서 지우기 전후 개수를 잰다.
SCRIPT = '''
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
ctx = get_script_run_ctx()
mgr = ctx.uploaded_file_mgr
count = lambda: sum(len(files) for files in mgr.file_storage.values())
n = st.session_state.setdefault("n", 0)
uploaded = st.file_uploader("음성", key=f"voice_upload_{n}")
if uploaded is not None and st.button("지우기", key="purge"):
    st.session_state["before"] = count()
    mgr.remove_file(session_id=ctx.session_id, file_id=uploaded.file_id)
    st.session_state["after"] = count()
    st.session_state["bytes_after"] = mgr._total_bytes
    st.session_state.pop(f"voice_upload_{n}", None)
    st.session_state["n"] = n + 1
'''


class UploadPurgeTest(unittest.TestCase):
    def test_remove_file_empties_upload_store(self):
        at = AppTest.from_string(SCRIPT).run()
        at.file_uploader[0].set_value(("t.wav", b"RIFF" + b"0" * 4096, "audio/wav")).run()
        at.button(key="purge").click().run()
        self.assertEqual(at.session_state["before"], 1)
        self.assertEqual(at.session_state["after"], 0)
        self.assertEqual(at.session_state["bytes_after"], 0)


if __name__ == "__main__":
    unittest.main()
