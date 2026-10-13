"""새 서버(Fly.io·AWS Lightsail) 배포 파일 검사. 실제 서버에는 접속하지 않는다.

- .dockerignore 허용 목록에 app.py와 app.py가 쓰는 보조 모듈이 모두 있고, 비밀·개인정보 파일은 없다.
- .env.example에 코드가 읽는 설정 이름이 모두 있고, 값은 비었거나 정해 둔 안전한 시작값뿐이다(AI·공개 기능 꺼짐).
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
        for bad in ("credentials.json", "token.json", "cases.json", "memory.json", "schedules.json", ".env", ".env.example",
                    ".streamlit/secrets.toml", "main.py", "calendar_test.py", "tests", "docs", "supabase"):
            self.assertNotIn(bad, allowlist())


class EnvTemplateTest(unittest.TestCase):
    def test_every_setting_the_code_reads_is_listed_without_values(self):
        names = set()
        for name in allowlist():                      # 서버에 올라가는 파일만(예전 개인 비서 코드 제외)
            if name.endswith(".py"):
                names |= set(re.findall(r'os\.(?:environ\.get|environ\[|getenv)\(?\s*"([A-Z0-9_]{3,})"', read(name)))
        names -= {"PATH", "HOME"}
        template = read(".env.example")
        self.assertEqual(sorted(n for n in names if not re.search(rf"^#?\s*{n}=", template, re.M)), [], "템플릿에 없는 설정")
        filled = dict(re.findall(r"^([A-Z0-9_]+)=(.+)$", template, re.M))
        safe = {"GOSOMI_DOMAIN": "gosomi.ai.kr", "GEMINI_MODEL": "gemini-3.5-flash-lite", "EMAIL_SIGNUP_ENABLED": "false",
                "PASSWORD_RESET_ENABLED": "false", "AI_FEATURES_PAUSED": "true", "AI_PUBLIC_LAUNCH_ENABLED": "false",
                "AI_PAID_TIER_VERIFIED": "false", "AI_HOSTING_VERIFIED": "false", "AI_USAGE_DB_ENABLED": "false",
                "AI_CALLS_PER_HOUR": "30", "AI_CALLS_PER_HOUR_TOTAL": "300", "AI_CALLS_PER_DAY": "100",
                "AI_CALLS_PER_DAY_TOTAL": "2000", "DRAFT_ATOMIC_SAVE_ENABLED": "false", "BILLING_MODE": "off"}
        self.assertEqual({k: v for k, v in filled.items() if safe.get(k) != v}, {}, "안전한 시작값 외의 값이 들어 있음")
        for secret in ("SUPABASE_PUBLISHABLE_KEY", "GEMINI_API_KEY", "AI_USAGE_SERVICE_KEY", "SUPABASE_URL"):
            self.assertNotIn(secret, filled)


class DockerfileTest(unittest.TestCase):
    def test_server_settings_and_no_secrets(self):
        text = read("Dockerfile")
        for setting in ("STREAMLIT_SERVER_FILE_WATCHER_TYPE=none", "STREAMLIT_SERVER_HEADLESS=true", "STREAMLIT_SERVER_PORT=8501",
                        "STREAMLIT_SERVER_MAX_UPLOAD_SIZE", "_stcore/health", "USER gosomi"):
            self.assertIn(setting, text)
        for secret in ("GEMINI_API_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_URL", "AIza", "sb_"):
            self.assertNotIn(secret, text)
        self.assertIn("internal_port = 8501", read("fly.toml"))
        self.assertIn('path = "/_stcore/health"', read("fly.toml"))
        self.assertIn("reverse_proxy app:8501", read("deploy/Caddyfile"))
        compose = read("deploy/compose.yaml")
        self.assertIn("env_file: ../.env", compose)
        self.assertNotIn("8501:8501", compose)                      # 앱 포트는 밖으로 열지 않음
        self.assertIn("gosomi.ai.kr", compose)
        for script in ("deploy/lightsail/deploy.sh", "deploy/lightsail/setup.sh"):
            self.assertNotIn("app.env", read(script))
        self.assertIn("deploy/compose.yaml", read("deploy/lightsail/deploy.sh"))
