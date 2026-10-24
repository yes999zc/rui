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
| Bundled in the container image | sherpa-onnx (wheel), speexdsp (`libspeexdsp1`), FFmpeg (static binary), Python deps, compiled web console |
| Downloaded at runtime into the data volume | SenseVoice ASR, Silero VAD, KWS wake-word model |
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

- **Role:** voice-activity detection. `house-ear fetch-models` downloads `silero_vad.onnx`
  from the sherpa-onnx release (with hf-mirror / proxy fallbacks) into the data volume.
- **Links:** <https://github.com/snakers4/silero-vad> ·
  <https://github.com/k2-fsa/sherpa-onnx/releases>
- **License:** MIT (verified: upstream `LICENSE`, © 2020-present Silero Team). The ONNX file is
  redistributed through the sherpa-onnx release channel; keep the MIT notice when
  redistributing the model.

### KWS wake-word model

- **Role:** wake-word spotting. Downloaded at runtime from the k2-fsa/sherpa-onnx GitHub
  release as `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`.
- **Link:** <https://github.com/k2-fsa/sherpa-onnx/releases>
- **License:** **to verify**. No explicit license file was confirmed for the released archive
  on 2026-10-24. Do not relicense it as part of Rui.

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

- **Role:** music transcoder (Navidrome flac/opus → 16 kHz mono s16 PCM), invoked as a
  subprocess. Only `/ffmpeg` is copied into the runtime image.
- **Current source:** `FROM mwader/static-ffmpeg:7.1 AS ffmpeg` (see `Dockerfile`).
- **License status:** **needs audit / LGPL-only plan** — do **not** assume this binary is
  LGPL. FFmpeg upstream is LGPL-2.1-or-later by default, but a build with `--enable-gpl`
  (or GPL-only libraries such as x264/x265) is GPL-2.0-or-later. The configure flags of this
  static image have not been audited, and Docker Hub currently exposes only version tags
  (no `-lgpl` variant) as of 2026-10-24.
- **How to check the shipped binary:**
  ```bash
  docker run --rm ghcr.io/yes999zc/rui:latest ffmpeg -version
  docker run --rm ghcr.io/yes999zc/rui:latest ffmpeg -L
  # Inspect the configuration line: --enable-gpl / --enable-nonfree / --enable-libx264 ...
  ```
- **Options for the LGPL-only migration (research note; not changed in this task):**
  1. **Build from pinned source (recommended).** Add a builder stage that compiles FFmpeg
     from a pinned release tarball (with SHA-256) using
     `--disable-gpl --disable-nonfree --enable-lgpl`, keeping only what the music path needs
     (flac/opus/vorbis/mp3/aac decode, wav/pcm output). Copy the resulting binary. Add a CI
     assertion that `ffmpeg -version` contains no `--enable-gpl`/`--enable-nonfree`.
  2. **Use a verified LGPL-only distro build.** Debian's and Alpine's default `ffmpeg`
     packages enable GPL code, so a package name alone is not proof; read the build config
     and package copyright first. Only adopt after `ffmpeg -version`/`ffmpeg -L` confirms it.
  3. **Request/consume an LGPL-only static image variant.** Not available from
     `mwader/static-ffmpeg` today; would require an upstream change or a locally published
     image.
  4. **Transcode in-process or drop transcoding.** Rejected for now: larger scope and
     CPU/latency risk on NAS hardware; revisit only if licensing forces it.
- **Decision needed:** pick option 1 for the 0.1.1 image, keep the `ffmpeg -version` check in
  CI, then update this section with the verified result.

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
| mwader/static-ffmpeg:7.1 | see [FFmpeg](#ffmpeg) — needs audit |

---

## Attribution checklist for releases

1. Keep the upstream MIT/BSD/Apache notices for code and models that are redistributed.
2. Preserve Apache-2.0 `NOTICE` content for sherpa-onnx and other Apache-2.0 dependencies.
3. Attribute SenseVoice/FunASR model names and source; comply with the FunASR Model License.
4. Audit FFmpeg (option 1 above) and record the verified license before claiming LGPL.
5. Update this file and the README credits when a component is added or removed.
