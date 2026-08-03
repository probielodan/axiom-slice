FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:latest AS uv

FROM ubuntu:24.04 AS python-deps
ENV DEBIAN_FRONTEND=noninteractive PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python-deps AS app
COPY src ./src
RUN uv sync --frozen --no-dev

FROM app AS api
COPY --from=web /web/dist ./frontend/dist
ENV AXIOM_DATA_DIR=/data AXIOM_PROFILE_ROOT=/profiles AXIOM_STATIC_DIR=/app/frontend/dist
EXPOSE 5000
CMD ["axiom-slice-api"]

FROM python-deps AS orca-runtime
ARG ORCA_VERSION=2.4.2
ARG ORCA_SHA256=d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd
ARG ORCA_URL=https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libasound2t64 libegl1 libgl1 libglu1-mesa libgtk-3-0t64 libgstreamer-plugins-base1.0-0 libice6 libjavascriptcoregtk-4.1-0 libmspack0 libsm6 libwebkit2gtk-4.1-0 \
    && curl --fail --location --output /tmp/orca.AppImage "$ORCA_URL" \
    && echo "$ORCA_SHA256  /tmp/orca.AppImage" | sha256sum --check --strict \
    && chmod +x /tmp/orca.AppImage \
    && cd /tmp && ./orca.AppImage --appimage-extract >/dev/null \
    && mv /tmp/squashfs-root /opt/OrcaSlicer \
    && rm /tmp/orca.AppImage \
    && rm -rf /var/lib/apt/lists/*

FROM orca-runtime AS worker
COPY --from=app /app /app
ENV AXIOM_DATA_DIR=/data AXIOM_PROFILE_ROOT=/profiles AXIOM_ORCA_BIN=/opt/OrcaSlicer/AppRun AXIOM_ORCA_VERSION=2.4.2 AXIOM_ORCA_DIGEST=sha256:d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd HOME=/tmp/axiom-slice-home
CMD ["axiom-slice-worker"]
