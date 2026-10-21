# syntax=docker/dockerfile:1

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# libspeexdsp is the echo canceller the plane loads through ctypes. Without it the
# plane still runs, but barge-in is gone - so it belongs in the image, not in a README.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libspeexdsp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY --from=web /web/dist /app/web/dist
ENV HOUSE_EAR_WEB_DIR=/app/web/dist
EXPOSE 8080
VOLUME ["/var/lib/house-ear"]
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')"
ENTRYPOINT ["house-ear"]
CMD ["serve"]
