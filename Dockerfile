FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SLOTSMITH_DB=/data/slotsmith.db PYTHONPATH=/app/src
WORKDIR /app
COPY vendor/runtime/ /wheels/
RUN cd /wheels && sha256sum -c SHA256SUMS && \
    pip install --no-cache-dir --no-index --find-links=/wheels fastapi==0.139.2 uvicorn==0.51.0 && \
    rm -rf /wheels
COPY src/ ./src/
COPY frontend/dist/ ./frontend/dist/
RUN mkdir -p /data && chown -R 10001:10001 /data /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "slotsmith.api:app", "--host", "0.0.0.0", "--port", "8000"]
