# syntax=docker/dockerfile:1.7
# One image for both roles: `erasure-audit serve` (API) and `erasure-audit worker` (Temporal worker).

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
# Behind a TLS-intercepting proxy, pass its CA as an optional build secret (never stored in a layer):
#   docker build --secret id=ca_bundle,src=/path/to/ca.crt .
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip wheel --wheel-dir /wheels ".[api,worker,llm]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ERASURE_DATASET_DIR=/app/dataset ERASURE_CACHE_DIR=/data/cache
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app
COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links /wheels "erasure-audit[api,worker,llm]" && rm -rf /wheels
WORKDIR /app
COPY --chown=app:app dataset ./dataset
RUN mkdir -p /data/cache /data/outbox && chown app:app /data/cache /data/outbox
# Warm the extraction cache at build time (deterministic parsers only), so containers start in about a second.
# A cache volume mounted on /data/cache shadows it, and the cache is rebuilt on first use.
USER app
RUN erasure-audit extract --no-llm > /dev/null
VOLUME ["/data/cache", "/data/outbox"]
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["erasure-audit", "serve", "--host", "0.0.0.0", "--port", "8000"]
