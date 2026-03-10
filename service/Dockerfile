# ── P2Pool Service ────────────────────────────────────────────────────────
# Node Registry + Explorer Proxy + RPC Provider
#
# Build:  docker build -t p2pool-service -f service/Dockerfile .
# Run:    docker run -p 8920:8920 -v $(pwd)/service/config.yaml:/app/config.yaml p2pool-service
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY service/ /app/service/

# Default config (override with volume mount)
COPY service/config.yaml /app/config.yaml

# Health check for container orchestrators
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8920/health')"

EXPOSE 8920

ENV P2POOL_SERVICE_PORT=8920

ENTRYPOINT ["python", "-m", "service.main"]
CMD ["--config", "/app/config.yaml"]
