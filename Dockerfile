# syntax=docker/dockerfile:1

# ffmpeg is the music source's transcoder (Navidrome returns flac/opus; the plane plays
# 16 kHz mono s16 PCM). Without it `music.enabled: true` fails on the first song, which
# only shows up in production - so it ships in the image. A static build instead of
# Debian's `ffmpeg` package: the package drags in ~100 MB of shared libraries for one
# subprocess, and this stage is pinned like any other dependency. Only the binary is
# copied, so the runtime image stays python-slim + libspeexdsp.
FROM mwader/static-ffmpeg:7.1 AS ffmpeg

FROM node:22-alpine AS web
# Build-time mirrors. Empty = the official registries; a build on a China-side NAS (or the
# dev Mac when deb.debian.org is unreachable) passes the mirrors as build args. Runtime is
# unaffected: the image installs nothing after this stage.
ARG NPM_REGISTRY=
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ARG VERSION=dev
ARG APT_MIRROR=
ARG PIP_INDEX_URL=
LABEL org.opencontainers.image.title="house-ear" \
      org.opencontainers.image.description="Self-hosted voice endpoint for smart speakers" \
      org.opencontainers.image.source="https://github.com/yes999zc/rui" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# libspeexdsp is the echo canceller the plane loads through ctypes. Without it the
# plane still runs, but barge-in is gone - so it belongs in the image, not in a README.
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libspeexdsp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# PIP_INDEX_URL is inherited from the build arg when set; empty means PyPI.
RUN pip install '.[kws]'
COPY --from=web /web/dist /app/web/dist
# Health probe: reads the port and the device list from config, and treats a configured
# device with a dead audio plane as unhealthy. See deploy/healthcheck.py.
COPY deploy/healthcheck.py /usr/local/bin/house-ear-healthcheck
RUN chmod +x /usr/local/bin/house-ear-healthcheck
ENV HOUSE_EAR_WEB_DIR=/app/web/dist
EXPOSE 8080
VOLUME ["/var/lib/house-ear"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "/usr/local/bin/house-ear-healthcheck"]
ENTRYPOINT ["house-ear"]
CMD ["serve"]
