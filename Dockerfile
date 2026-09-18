# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ARG VCS_REF=unknown
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY packages ./packages
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
RUN pip install --upgrade pip && pip install .

FROM python:3.12-slim AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="GH-Bot-Factory" \
      org.opencontainers.image.source="https://github.com/GH-ZX/GH-Bot-Factory" \
      org.opencontainers.image.revision="$VCS_REF"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOME=/app
RUN groupadd --gid 10001 ghbf && useradd --uid 10001 --gid ghbf --create-home --shell /usr/sbin/nologin ghbf \
    && mkdir -p /var/lib/ghbf/secret-store /var/lib/ghbf/handoffs \
    && chown -R ghbf:ghbf /var/lib/ghbf
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=ghbf:ghbf apps ./apps
COPY --chown=ghbf:ghbf packages ./packages
COPY --chown=ghbf:ghbf migrations ./migrations
COPY --chown=ghbf:ghbf scripts ./scripts
COPY --chown=ghbf:ghbf alembic.ini pyproject.toml README.md ./
USER 10001:10001
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
