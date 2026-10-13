# 고소미 서버 이미지 (Fly.io·AWS Lightsail 공용). 2026-10-14 준비, 아직 어떤 서버에도 올리지 않음.
# - 앱 파일만 넣는다(.dockerignore가 허용 목록 방식). 비밀값은 이미지에 넣지 않고 실행할 때 환경변수로 받는다.
# - Streamlit Cloud와 같은 Python 3.13, 같은 requirements.txt.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # glibc가 놓은 메모리를 더 빨리 돌려주도록(작은 서버에서 '내 자료 모두 내려받기' 뒤 메모리 정체 완화)
    MALLOC_ARENA_MAX=2 \
    # 서버용 Streamlit 설정. 저장소의 .streamlit/config.toml(화면 테마)은 그대로 두고 여기서만 정한다.
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    # 실행 중 파일 감시 끔: 배포는 컨테이너를 새로 띄우는 방식이라 예전 모듈이 남는 문제(2026-10-06·10-13 장애)가 생기지 않음
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    STREAMLIT_SERVER_RUN_ON_SAVE=false \
    # 업로드 상한(MB). 증거파일·음성은 앱에서 10MB로 막고, 서버는 그 위 여유만 둔다(기본 200MB는 메모리에 부담)
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=25 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_CLIENT_TOOLBAR_MODE=viewer

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app . .
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"
CMD ["streamlit", "run", "app.py"]
