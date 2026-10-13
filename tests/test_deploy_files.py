"""새 서버(Fly.io·AWS Lightsail) 배포 파일 검사. 실제 서버에는 접속하지 않는다.

- .dockerignore 허용 목록에 app.py와 app.py가 쓰는 보조 모듈이 모두 있고, 비밀·개인정보 파일은 없다.
- deploy/app.env.example에 코드가 읽는 설정 이름이 모두 있고, 값은 비어 있다.
- Dockerfile에 서버용 Streamlit 설정(파일 감시 끔 등)이 있고 비밀값이 없다.
실행: python3 -m unittest discover -s tests
"""

import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def allowlist():
    return {line[1:].strip() for line in read(".dockerignore").splitlines() if line.startswith("!")}


class DockerIgnoreTest(unittest.TestCase):
    def test_app_and_all_local_helpers_are_shipped(self):
        tree = ast.parse(read("app.py"))
        local = {name[:-3] for name in os.listdir(ROOT) if name.endswith(".py")}
        used = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module in local}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names if a.name in local}
        # 보조 모듈이 서로 가져오는 것도 포함
        for module in list(used):
            sub = ast.parse(read(f"{module}.py"))
            used |= {n.module for n in ast.walk(sub) if isinstance(n, ast.ImportFrom) and n.module in local}
            used |= {a.name for n in ast.walk(sub) if isinstance(n, ast.Import) for a in n.names if a.name in local}
        shipped = allowlist()
        self.assertIn("app.py", shipped)
        self.assertIn("requirements.txt", shipped)
        self.assertEqual(sorted(f"{m}.py" for m in used if f"{m}.py" not in shipped), [], "이미지에 빠진 보조 모듈")

    def test_secrets_and_personal_files_are_never_shipped(self):
        self.assertEqual(read(".dockerignore").splitlines()[[l.strip() for l in read(".dockerignore").splitlines()].index("*")], "*")
        for bad in ("credentials.json", "token.json", "cases.json", "memory.json", "schedules.json", ".env", "app.env",
                    ".streamlit/secrets.toml", "main.py", "calendar_test.py", "tests", "docs", "supabase"):
            self.assertNotIn(bad, allowlist())


class EnvTemplateTest(unittest.TestCase):
    def test_every_setting_the_code_reads_is_listed_without_values(self):
        names = set()
        for name in allowlist():                      # 서버에 올라가는 파일만(예전 개인 비서 코드 제외)
            if name.endswith(".py"):
                names |= set(re.findall(r'os\.(?:environ\.get|environ\[|getenv)\(?\s*"([A-Z0-9_]{3,})"', read(name)))
        names -= {"PATH", "HOME"}
        template = read("deploy/app.env.example")
        self.assertEqual(sorted(n for n in names if not re.search(rf"^#?\s*{n}=", template, re.M)), [], "템플릿에 없는 설정")
        filled = [line for line in template.splitlines() if re.match(r"^[A-Z0-9_]+=.+", line)]
        self.assertEqual(filled, [], "템플릿에 값이 들어 있음")


class DockerfileTest(unittest.TestCase):
    def test_server_settings_and_no_secrets(self):
        text = read("Dockerfile")
        for setting in ("STREAMLIT_SERVER_FILE_WATCHER_TYPE=none", "STREAMLIT_SERVER_HEADLESS=true", "STREAMLIT_SERVER_PORT=8501",
                        "STREAMLIT_SERVER_MAX_UPLOAD_SIZE", "_stcore/health", "USER app"):
            self.assertIn(setting, text)
        for secret in ("GEMINI_API_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_URL", "AIza", "sb_"):
            self.assertNotIn(secret, text)
        self.assertIn("internal_port = 8501", read("fly.toml"))
        self.assertIn('path = "/_stcore/health"', read("fly.toml"))
        self.assertIn("reverse_proxy app:8501", read("deploy/lightsail/Caddyfile"))
