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
COPY --from=ffmpeg /usr/src/ffmpeg/COPYING.LGPLv2.1 /usr/share/doc/house-ear/ffmpeg/COPYING.LGPLv2.1
COPY --from=ffmpeg /usr/src/ffmpeg/LICENSE.md /usr/share/doc/house-ear/ffmpeg/LICENSE.md
COPY --from=ffmpeg /usr/share/doc/libopus-dev/copyright /usr/share/doc/house-ear/ffmpeg/libopus.copyright
COPY --from=ffmpeg /usr/share/doc/libmp3lame-dev/copyright /usr/share/doc/house-ear/ffmpeg/libmp3lame.copyright
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# PIP_INDEX_URL is inherited from the build arg when set; empty means PyPI.
RUN pip install '.[kws,channels]'
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
