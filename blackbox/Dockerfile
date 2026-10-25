FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 BLACKBOX_DB=/data/blackbox.sqlite3 BLACKBOX_IMPORT_DIR=/imports
WORKDIR /app
RUN groupadd --system --gid 10001 blackbox && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin blackbox
COPY --chown=blackbox:blackbox app/ /app/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1
CMD ["python", "/app/server.py"]
