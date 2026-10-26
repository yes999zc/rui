# syntax=docker/dockerfile:1

# ffmpeg is the music source's transcoder (Navidrome returns flac/opus; the plane plays
# 16 kHz mono s16 PCM) and encodes channel voice replies (Ogg/Opus, MP3). Without it
# `music.enabled: true` fails on the first song, which only shows up in production - so it
# ships in the image. It is built here from a pinned source tarball instead of copied from
# a prebuilt static image, because the shipped binary must have an auditable LGPL-only
# provenance. Only the binary is copied, so the runtime image stays python-slim +
# libspeexdsp.
#
# License: FFmpeg is LGPL-2.1-or-later by default. It stays that way because `configure`
# gets --disable-gpl/--disable-nonfree/--disable-version3 and only LGPL-compatible
# libraries are linked (libopus: BSD-3-Clause; libmp3lame: LGPL-2.0-or-later). There is no
# `--enable-lgpl` switch in FFmpeg despite older notes here. The build stage AND the
# runtime stage assert the result with `ffmpeg -L` / `ffmpeg -version`, so a GPL
# regression fails `docker build` (and therefore ci.yml + docker-publish.yml).
# Flags and source-offer notes: THIRD_PARTY.md (FFmpeg).
FROM debian:bookworm-slim AS ffmpeg
ARG APT_MIRROR=
# Pinned release. Bump FFMPEG_VERSION and FFMPEG_SHA256 together; the hash is enforced
# before the tarball is unpacked, so a compromised/mirrored download cannot slip through.
# FFMPEG_MIRROR can point at a closer copy of the same tarball (e.g. on a China-side NAS).
ARG FFMPEG_VERSION=7.1.5
ARG FFMPEG_SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
ARG FFMPEG_MIRROR=https://ffmpeg.org/releases
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl xz-utils pkg-config nasm \
        libopus-dev libmp3lame-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/ffmpeg
RUN curl -fsSL --retry 3 --retry-all-errors --max-time 300 \
        -o ffmpeg.tar.xz "$FFMPEG_MIRROR/ffmpeg-$FFMPEG_VERSION.tar.xz" \
    && echo "$FFMPEG_SHA256  ffmpeg.tar.xz" | sha256sum -c - \
    && tar -xf ffmpeg.tar.xz --strip-components=1 \
    && ./configure \
        --prefix=/opt/ffmpeg \
        --disable-gpl \
        --disable-nonfree \
        --disable-version3 \
        --disable-autodetect \
        --disable-doc \
        --disable-debug \
        --disable-ffplay \
        --disable-ffprobe \
        --enable-static \
        --disable-shared \
        --enable-libopus \
        --enable-libmp3lame \
        --pkg-config-flags=--static \
        --extra-ldflags=-static \
        --extra-libs=-lm \
    && make -j"$(nproc)" \
    && make install \
    && license="$(/opt/ffmpeg/bin/ffmpeg -L 2>&1)" \
    && version="$(/opt/ffmpeg/bin/ffmpeg -version 2>&1)" \
    && printf '%s\n' "$license" | grep -q 'GNU Lesser General Public' \
    && printf '%s\n' "$license" | grep -q 'version 2.1 of the License' \
    && ! printf '%s\n' "$license" | grep -q 'GNU General Public License' \
    && ! printf '%s\n' "$version" | grep -Eq -- '--enable-(gpl|nonfree|version3)' \
    && printf '%s\n' "$version" | grep -m1 'configuration:' \
    && echo "ffmpeg build is LGPL-only"

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

# Models seed. The VAD and wake-word models are small, but their GitHub release asset is
# the one download that stalls a mainland cold install (~55 KB/s direct, mirrors ~30 KB/s;
# 33 MB then feels like forever). CI builds on GitHub Actions have no such problem, so the
# image bakes both in and `fetch-models` copies them into the data volume before any
# network access. The 239 MB SenseVoice ASR model is *not* seeded - hf-mirror serves it
# at ~1 MB/s - so the image grows by ~40 MB (0.6 MB VAD + 39 MB extracted KWS dir; the
# 33 MB tarball is deleted in this stage and never reaches the runtime layer).
#
# Bump a URL and its SHA256 together. The hash is checked before extraction, so a proxied
# or mirrored copy cannot silently change the bytes that ship. The VAD default is the
# GitHub release asset, not the hf-mirror URL: that HuggingFace repo returned 401/404 on
# 2026-10-24, and a CI build must not depend on it.
FROM debian:bookworm-slim AS models
ARG VAD_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
ARG VAD_SHA256=9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6
ARG KWS_REPO=sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20
ARG KWS_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
ARG KWS_SHA256=68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6
# VAD_URL / KWS_URL are overridable for a China-side builder; the defaults are the fast
# GitHub Actions path. ca-certificates + curl + bzip2 only; the builder's apt layer is not
# copied into the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bzip2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /seed/kws \
    && curl -fsSL --retry 3 --retry-all-errors --max-time 300 \
        -o /seed/silero_vad.onnx "$VAD_URL" \
    && echo "$VAD_SHA256  /seed/silero_vad.onnx" | sha256sum -c - \
    && curl -fsSL --retry 3 --retry-all-errors --max-time 600 \
        -o /tmp/kws.tar.bz2 "$KWS_URL" \
    && echo "$KWS_SHA256  /tmp/kws.tar.bz2" | sha256sum -c - \
    && tar -xjf /tmp/kws.tar.bz2 -C /seed/kws \
    && rm -f /tmp/kws.tar.bz2 \
    && test -f "/seed/kws/$KWS_REPO/tokens.txt" \
    && test -f "/seed/kws/$KWS_REPO/encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx" \
    && test -f "/seed/kws/$KWS_REPO/joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx" \
    && test -f "/seed/kws/$KWS_REPO/decoder-epoch-13-avg-2-chunk-8-left-64.onnx"

# Pin the Debian release: plain python:3.12-slim moved from bookworm to trixie,
# where libspeexdsp1 is no longer available and the image build breaks.
FROM python:3.12-slim-bookworm
ARG VERSION=dev
ARG APT_MIRROR=
ARG PIP_INDEX_URL=
LABEL org.opencontainers.image.title="rui" \
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
# The service runs unprivileged. uid/gid 1000 is both the image's runtime identity and
# the identity deploy/install.sh chowns the ./config and ./data bind mounts to, so the
# container can write its state without a root/gosu entrypoint. HOME is the data volume:
# anything that still resolves a home-relative default writes to persistent state, not
# to an unwritable /root. RUI_UID/RUI_GID + compose `user:` can override the mapping,
# but then the host directories must be chowned to the same pair.
RUN groupadd --gid 1000 rui \
    && useradd --uid 1000 --gid 1000 --home-dir /var/lib/rui \
        --no-create-home --shell /usr/sbin/nologin rui \
    && mkdir -p /var/lib/rui/state \
    && chown -R rui:rui /var/lib/rui \
    && chmod 0750 /var/lib/rui /var/lib/rui/state
# The statically linked binary from the builder stage. Re-run the license gate on the
# artifact that actually ships (not just in the builder): this also proves the static
# binary executes on this base image. A GPL/nonfree flag here fails the image build.
COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
RUN license="$(ffmpeg -L 2>&1)" \
    && version="$(ffmpeg -version 2>&1)" \
    && printf '%s\n' "$license" | grep -q 'GNU Lesser General Public' \
    && printf '%s\n' "$license" | grep -q 'version 2.1 of the License' \
    && ! printf '%s\n' "$license" | grep -q 'GNU General Public License' \
    && ! printf '%s\n' "$version" | grep -Eq -- '--enable-(gpl|nonfree|version3)'
# License texts for the statically linked LGPL binary, so the image carries its own
# notices: FFmpeg's LGPL-2.1 text plus the Debian copyright files for libopus/libmp3lame.
COPY --from=ffmpeg /usr/src/ffmpeg/COPYING.LGPLv2.1 /usr/share/doc/rui/ffmpeg/COPYING.LGPLv2.1
COPY --from=ffmpeg /usr/src/ffmpeg/LICENSE.md /usr/share/doc/rui/ffmpeg/LICENSE.md
COPY --from=ffmpeg /usr/share/doc/libopus-dev/copyright /usr/share/doc/rui/ffmpeg/libopus.copyright
COPY --from=ffmpeg /usr/share/doc/libmp3lame-dev/copyright /usr/share/doc/rui/ffmpeg/libmp3lame.copyright
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# No plug ships in the core image since 0.7.6 (B: the LX06 adapter lives in
# rui-xiaoai-bridge). The empty directory is the mount point for downstream plugs
# (`/opt/rui-plugins`), so the runtime path stays the same.
RUN install -d -o rui -g rui -m 0755 /opt/rui-plugins
# PIP_INDEX_URL is inherited from the build arg when set; empty means PyPI.
RUN pip install '.[kws,channels]'
COPY --from=web /web/dist /app/web/dist
# The seed is ~40 MB, all of it the two models the install used to pull from GitHub. It is
# a single layer; `fetch-models` copies from /opt/seed-models before its first network
# request. An older image without this directory keeps the old download behavior.
COPY --from=models /seed/ /opt/seed-models/
# Health probe: reads the port and the device list from config, and treats a configured
# device with a dead audio plane as unhealthy. See deploy/rui-healthcheck.py.
COPY deploy/rui-healthcheck.py /usr/local/bin/rui-healthcheck
RUN chmod +x /usr/local/bin/rui-healthcheck
# /app is installed as root:root and only ever read by the service. a+rX makes that
# explicit for every file shipped above, so a future COPY cannot add a 0600 asset the
# runtime user cannot read. The checks below are the non-root contract:
#   * id -u rui is 1000 (also catches a base-image uid collision);
#   * the ffmpeg binary, the web UI and both seed models are readable;
#   * the state directory exists and is owned by rui.
RUN chmod -R a+rX /app /opt/seed-models /usr/local/bin/rui-healthcheck \
    && id -u rui \
    && test "$(id -u rui)" = 1000 \
    && test "$(id -g rui)" = 1000 \
    && test -x /usr/local/bin/ffmpeg \
    && test -r /app/web/dist/index.html \
    && test -r /app/src/rui/__main__.py \
    && setpriv --reuid=1000 --regid=1000 --clear-groups test -w /opt/rui-plugins \
    && test -r /opt/seed-models/silero_vad.onnx \
    && test -n "$(find /opt/seed-models/kws -name tokens.txt -print -quit)" \
    && test -d /var/lib/rui/state \
    && test "$(stat -c '%u:%g' /var/lib/rui)" = "1000:1000"
# State that used to live in /root/.local/state (audio-plane socket, channel gateway
# data) now lives on the data volume. Both variables are read by the code today
# (src/rui/audio/process.py, src/rui/channels/cli.py); RUI_STATE_DIR is the documented
# anchor and the two paths below are its concrete consumers. Keeping them inside
# /var/lib/rui means they survive container rebuilds and are writable by uid 1000.
ENV HOME=/var/lib/rui \
    RUI_STATE_DIR=/var/lib/rui/state \
    RUI_AUDIO_SOCK=/var/lib/rui/state/audio.sock \
    RUI_CHANNEL_DATA=/var/lib/rui/state/channels \
    RUI_WEB_DIR=/app/web/dist \
    RUI_PLUGINS_DIR=/opt/rui-plugins
EXPOSE 8080
VOLUME ["/var/lib/rui"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "/usr/local/bin/rui-healthcheck"]
# The probe and ENTRYPOINT run as rui too; no gosu/su shim is needed. Bind mounts are
# prepared by deploy/install.sh (or the documented chown for existing installs).
USER rui
ENTRYPOINT ["rui"]
CMD ["serve"]
