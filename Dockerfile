# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build && npm test

FROM python:3.12-slim-bookworm AS python-build
WORKDIR /build
COPY pyproject.toml requirements.lock README.md LICENSE ./
COPY src/ src/
COPY examples/sample-knowledge/ examples/sample-knowledge/
COPY examples/sample-knowledge/ examples/sample-knowledge/
COPY --from=frontend /build/src/hindsight/static/ src/hindsight/static/
RUN pip install --no-cache-dir --prefix=/install -c requirements.lock .

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 hindsight && useradd --uid 10001 --gid 10001 --create-home hindsight \
    && mkdir -p /data /opt/hindsight \
    && chown -R hindsight:hindsight /data /opt/hindsight
COPY --from=python-build /install/ /usr/local/
LABEL org.opencontainers.image.title="Hindsight SRE" \
      io.modelcontextprotocol.server.name="io.github.DrDroidLab/hindsight-sre"
USER 10001:10001
WORKDIR /opt/hindsight
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HINDSIGHT_DATA_DIR=/data DATA_DIR=/data
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)"
ENTRYPOINT ["hindsight"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
