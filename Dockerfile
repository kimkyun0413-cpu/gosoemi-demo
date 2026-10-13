FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 gosomi
COPY --chown=gosomi:gosomi *.py ./
COPY --chown=gosomi:gosomi scripts/ai_usage_report.py scripts/ai_usage_report.py
COPY --chown=gosomi:gosomi .streamlit/config.toml .streamlit/config.toml
USER gosomi
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
# Immutable image per release; do not hot-reload production helper modules from edited host files.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.fileWatcherType=none", "--server.runOnSave=false", "--server.maxUploadSize=10"]
