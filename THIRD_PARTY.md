# Third-Party Components / 第三方组件

Rui (小瑞) is MIT-licensed, but it builds on, ships, or downloads other people's work. The
MIT license in [LICENSE](LICENSE) covers **this repository's own code only**. Every component
below keeps its own license and attribution requirements.

This is an inventory, not legal advice. License facts were checked against upstream license
files and package metadata on **2026-10-24**. Where a fact could not be confirmed from an
upstream source, the entry says **license: to verify** instead of guessing. Re-check this file
whenever a dependency, base image, or model source changes.

How each component reaches a user:

| Delivery | Components |
|---|---|
| Bundled in the container image | sherpa-onnx (wheel), speexdsp (`libspeexdsp1`), FFmpeg (LGPL-only static build with libopus/libmp3lame), Python deps, compiled web console, Silero VAD + KWS wake-word model (`/opt/seed-models`) |
| Downloaded at runtime into the data volume | SenseVoice ASR (the VAD/KWS seed is copied locally; older images still download all three) |
| Installed by the user on the speaker | open-xiaoai client |
| Reference only (no code shipped) | EchoMuse |

---

## 1. Device client and protocol

### open-xiaoai

- **Role:** speaker-side client and the LX06 WebSocket protocol used to dial into Rui.
- **Link:** <https://github.com/coderzc/open-xiaoai>
- **License:** MIT (verified: upstream `LICENSE`, © 2024 Del Wang).
- **Distribution:** not bundled in the image; the user installs the client on the speaker.
  Rui only speaks the protocol, so the client's license does not flow into the image.
- **Upstream dependency:** the client itself depends on the speaker firmware/root setup; that
  is outside this repository.

## 2. Design references

### EchoMuse

- **Role:** reference implementation for the device-management console (P2.0 research,
  `docs/device-management-research.md`). Useful for its state machine, evidence-based wizard,
  recovery actions, and diagnostics.
- **Link:** <https://github.com/wilbowes/EchoMuse>
- **License:** MIT (verified: upstream `LICENSE`, © 2026 Wil Bowes).
- **Distribution:** no EchoMuse code is copied into Rui at the time of writing. If code is
  copied later, keep the MIT copyright/license notice and update this entry.

## 3. Speech stack

### sherpa-onnx (k2-fsa)

- **Role:** local ASR and keyword-spotting runtime. Installed into the image through the
  `kws` extra (`sherpa-onnx>=1.12`).
- **Link:** <https://github.com/k2-fsa/sherpa-onnx>
- **License:** Apache-2.0 (verified: upstream `LICENSE` and the installed wheel's `METADATA`).
- **Notes:** Apache-2.0 requires preserving the license and NOTICE file when redistributing.
  The wheel may bundle third-party native libraries; those are covered by their own licenses.

### SenseVoice (model)

- **Role:** local ASR model. `house-ear fetch-models` downloads `model.int8.onnx` and
  `tokens.txt` into the data volume; the model is **not** in the image.
- **Exact source used:** <https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17>
  (a sherpa-onnx conversion of SenseVoiceSmall, mirrored from Hugging Face).
- **License:** **to verify** for the converted files downloaded by this project. The model
  repository published no license metadata when checked on 2026-10-24.
- **Upstream context (verified):**
  - The [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) code repository is
    MIT.
  - The original SenseVoiceSmall **model weights** are released under the
    [FunASR Model Open Source License v1.1](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)
    (Alibaba; `license: other` / `model-license` on Hugging Face). It permits use, copying,
    modification, and sharing, but requires attribution and retention of model names.
  - Do not describe the downloaded weights as MIT. Treat them as `model-license` until the
    conversion repository states its own terms.

### Silero VAD

- **Role:** voice-activity detection. New images ship `silero_vad.onnx` at
  `/opt/seed-models/silero_vad.onnx`; `house-ear fetch-models` copies it into the data volume
  before any network access, so the file is **redistributed inside the image**. Older images
  and source installs download it from the sherpa-onnx release (with hf-mirror / proxy
  fallbacks) instead.
- **Exact source used:** <https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx>
  (SHA256 `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`, 643,854 B).
  The dead hf-mirror URL is only a download fallback, not the image build source.
- **Links:** <https://github.com/snakers4/silero-vad> ·
  <https://github.com/k2-fsa/sherpa-onnx/releases>
- **License:** MIT (verified: upstream `LICENSE`, © 2020-present Silero Team). The ONNX file is
  redistributed through the sherpa-onnx release channel; keep the MIT notice when
  redistributing the model.

### KWS wake-word model

- **Role:** wake-word spotting. New images ship the extracted archive at
  `/opt/seed-models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/`; `fetch-models`
  copies it into the data volume before any network access, so these model files are
  **redistributed inside the image**. Older images and source installs download the tarball
  at runtime instead.
- **Exact source used:** <https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2>
  (SHA256 `68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6`, 32,885,699 B).
- **License:** Apache-2.0 (verified 2026-10-24 against the upstream
  [k2-fsa/sherpa-onnx LICENSE](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE);
  the release archive itself carries no separate LICENSE file, so the repository license is
  the stated terms). Apache-2.0 requires preserving the license and NOTICE when redistributing.
  Do not relicense the model as part of Rui.

## 4. Native and system components

### SpeexDSP

- **Role:** acoustic echo cancellation, loaded from the `libspeexdsp1` Debian package through
  `ctypes`. Removing it disables barge-in but the plane still runs.
- **Link:** <https://github.com/xiph/speexdsp>
- **License:** BSD-3-Clause (verified: upstream `COPYING` has the three-clause BSD text;
  © Xiph.Org Foundation and contributors).
- **Notes:** for the exact binary in the image, the Debian package copyright file
  (`/usr/share/doc/libspeexdsp1/copyright`) is authoritative.

### FFmpeg

- **Role:** music transcoder (Navidrome flac/opus/mp3/… → 16 kHz mono s16 PCM) and voice
  encoder for channel replies (Ogg/Opus for Telegram, MP3 for WeChat), invoked as a
  subprocess. Only `/opt/ffmpeg/bin/ffmpeg` from the builder stage is copied into the
  runtime image, as `/usr/local/bin/ffmpeg`.
- **Source:** FFmpeg **7.1.5**, pinned release tarball
  <https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz>, SHA-256
  `de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f`.
  The builder stage (`FROM debian:bookworm-slim AS ffmpeg`) verifies the hash with
  `sha256sum -c` **before** unpacking; bump `FFMPEG_VERSION` and `FFMPEG_SHA256` together.
  `FFMPEG_MIRROR` may point at a mirror of the same tarball; the hash still applies.
- **License:** **LGPL-2.1-or-later** (FFmpeg's default when GPL/nonfree/version3 code is
  disabled). Note that FFmpeg has **no `--enable-lgpl` configure switch**; older notes in
  this repo were wrong about that. LGPL is preserved by configuring with
  `--disable-gpl --disable-nonfree --disable-version3` and by linking only
  LGPL-compatible libraries:
  - [`libopus`](https://github.com/xiph/opus) (BSD-3-Clause): Opus decode and the `libopus`
    encoder for Telegram voice replies. Verified against upstream `COPYING`.
  - [`libmp3lame`](https://lame.sourceforge.io/) (LGPL-2.0-or-later, GNU Library General
    Public License v2): `libmp3lame` encoder used by the WeChat audio reply path. Verified against
    upstream source headers; LGPL-2.0-or-later is compatible with the LGPL-2.1+ combination.
  No GPL-only codec (x264/x265/etc.) is configured or linked. All container demuxers and
  the flac/vorbis/opus/mp3/aac decoders are FFmpeg-native code covered by the LGPL.
- **Exact build configuration (Dockerfile `ffmpeg` stage):**
  ```text
  ./configure --prefix=/opt/ffmpeg \
    --disable-gpl --disable-nonfree --disable-version3 \
    --disable-autodetect --disable-doc --disable-debug \
    --disable-ffplay --disable-ffprobe \
    --enable-static --disable-shared \
    --enable-libopus --enable-libmp3lame \
    --pkg-config-flags=--static --extra-ldflags=-static --extra-libs=-lm
  ```
  `--disable-autodetect` is deliberate: only the two libraries above are linked, so no
  system library can change the license or the configuration line. The result is a
  stripped, statically linked `ffmpeg` binary; the runtime image stays `python:3.12-slim`
  plus `libspeexdsp1`.
- **Build-time license gates (fail `docker build`, therefore CI and image publishing):**
  - builder stage: `ffmpeg -L` must say "GNU Lesser General Public … version 2.1 of the
    License", must not say "GNU General Public License", and `ffmpeg -version` must contain
    no `--enable-gpl`, `--enable-nonfree`, or `--enable-version3`;
  - runtime stage: the same checks run on the copied binary, which also proves it executes
    on the runtime base image.
- **How to check the shipped image:**
  ```bash
  docker run --rm ghcr.io/yes999zc/rui:latest ffmpeg -L      # expect GNU Lesser General Public ... 2.1
  docker run --rm ghcr.io/yes999zc/rui:latest ffmpeg -version # configuration: no --enable-gpl/nonfree/version3
  docker run --rm ghcr.io/yes999zc/rui:latest ls /usr/share/doc/house-ear/ffmpeg
  # COPYING.LGPLv2.1, LICENSE.md, libopus.copyright, libmp3lame.copyright
  ```
- **Source offer (LGPL compliance):** the image is built entirely from the pinned FFmpeg
  tarball above plus Debian's `libopus`/`libmp3lame` sources; the Dockerfile is the complete
  rebuild/relink recipe. Because `libmp3lame` is statically linked, a redistributor should
  keep the tarballs (or a written offer for them) available for at least three years and
  keep this recipe in the tree so anyone can rebuild the binary with a modified library
  (LGPL-2.1 §6). Before a formal release, publish the tarball URLs and this commit/tag
  alongside the image. (Inventory note, not legal advice.)
- **Decision (P1.6):** from-source build adopted. `mwader/static-ffmpeg` publishes no
  LGPL variant (Docker Hub tag list checked 2026-10-24), and no other prebuilt image was
  adopted because its configure line could not be verified without pulling it. Fallback if
  the build breaks: return to the last published image tag **only** with an explicit
  "FFmpeg license not audited" NOTICE and keep the P1.6 item open; do **not** silently
  ship a GPL binary.

## 5. Python dependencies

License fields below come from PyPI/installed distribution metadata. Versions are the
constraints in `pyproject.toml`, not a lockfile snapshot.

| Package | Constraint | License | When it ships |
|---|---|---|---|
| fastapi | >=0.115 | MIT | image |
| uvicorn | >=0.30 | BSD-3-Clause | image |
| pydantic | >=2.7 | MIT | image |
| ruamel.yaml | >=0.18 | MIT | image |
| numpy | >=1.26 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | image (compound; keep all notices) |
| psutil | >=5.9 | BSD-3-Clause | image |
| websockets | >=13 | BSD-3-Clause | image |
| httpx | >=0.27 | BSD-3-Clause | image |
| mcp | >=2.3.0 | MIT | image |
| pypinyin | >=0.55.0 | MIT | image |
| segno | >=1.6 | BSD-3-Clause | image |
| socksio | >=1.0 | MIT | image |
| cryptography | >=42 | Apache-2.0 OR BSD-3-Clause | image (`channels` extra) |
| sherpa-onnx | >=1.12 | Apache-2.0 | image (`kws` extra) |
| pilk | >=0.2.4 | GPL-3.0 | **not in the image**; only with the `weixin-voice` extra |
| pytest | >=8 | MIT | dev only |
| ruff | >=0.5 | MIT | dev only |

Notes:

- `pilk` is GPL-3.0 (PyPI classifier). The Dockerfile installs `.[kws,channels]`, so it is not
  part of the published image. Enabling the `weixin-voice` extra would put GPL code into the
  deployment; decide deliberately before doing that.
- `segno` is BSD-3-Clause (upstream `LICENSE`; PyPI only advertises a generic BSD classifier).
- `socksio` is MIT per its PyPI classifier; its upstream license file was not independently
  fetched.
- Transitive dependencies are not listed individually; their licenses still apply. Use
  `uv tree` / `pip-licenses` before a release if a full SBOM is needed.

## 6. Frontend build dependencies

The console is compiled during the image build; these packages are build-time dependencies
(and their notices should be kept with the distributed build):

| Package | License |
|---|---|
| vue, vue-router | MIT |
| vite | MIT |
| typescript | Apache-2.0 |
| vue-tsc, @vitejs/plugin-vue | MIT |

## 7. Base images

| Image | License notes |
|---|---|
| python:3.12-slim | Python-2.0 plus Debian packages; see the image's `/usr/share/doc` |
| node:22-alpine | Node.js MIT plus Alpine packages |
| debian:bookworm-slim | build-only stage for FFmpeg; Debian packages plus the pinned FFmpeg tarball, see [FFmpeg](#ffmpeg) |

---

## Attribution checklist for releases

1. Keep the upstream MIT/BSD/Apache notices for code and models that are redistributed.
2. Preserve Apache-2.0 `NOTICE` content for sherpa-onnx and other Apache-2.0 dependencies.
3. Attribute SenseVoice/FunASR model names and source; comply with the FunASR Model License.
   The image also redistributes Silero VAD (MIT) and the KWS wake-word model (Apache-2.0):
   keep those notices with the `/opt/seed-models` copies.
4. Keep the FFmpeg source pin/SHA-256 and the LGPL build gates (`--disable-gpl`, builder +
   runtime `ffmpeg -L`/`-version` checks) intact; re-run them whenever FFmpeg is bumped.
5. Update this file and the README credits when a component is added or removed.
