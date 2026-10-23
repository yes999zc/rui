# Channels audit — 2026-10-23

Scope: Telegram + WeChat iLink adapters, channel gateway/supervisor, `/api/v1/chat` and
`/api/v1/channels` APIs, the Channels console page, and the NAS rollout. No credentials
or user IDs are repeated here.

## Fixed during the audit

- **Speaker idle leak (high impact, fixed)**: a channel turn called `TurnRunner.run()` with
  the default `touch_idle=True`; 15 s later the living-room idle watchdog spoke the
  farewell. Channel turns now pass `touch_idle=False` (`ea3b116`), verified on NAS.
- **Telegram bot token in URLs/logs**: httpx logs the full request URL at INFO, including
  the bot token. The gateway now silences `httpx`/`httpcore` logging and redacts token
  text in adapter errors (`c3e5503`, `5f0abe1`).
- **Default-open bots**: an empty allowlist used to accept every peer. Both adapters now
  default-deny; explicit `--telegram-allow-all` / `--weixin-allow-all` is required for a
  temporary pairing window.
- **WeChat voice reliability**: inbound voice downloads/decrypts the original `.silk`,
  decodes with `pilk` and re-runs ASR; Tencent `voice_item.text` is not trusted. Voice
  replies are file attachments (`ea3b116`, `5f0abe1`).
- **Missing runtime deps on Mac**: `ffmpeg` lookup falls back to Homebrew paths; `pilk`
  and `cryptography` are in the `channels` extra; the Docker image installs that extra.
- **Secret handling**: Telegram token is accepted write-only via the API and stored in
  `secrets.yaml` (0600); GET responses expose only `token_configured`. WeChat credentials
  and context tokens are 0600. The supervisor passes the token only in the child env.
- **Control-plane boundary**: `/api/v1/chat` requires the console token, keeps per-peer
  history, never touches the audio plane, and (when `audio=false`) skips TTS.

## Residual / accepted

- WeChat QR login has no server-side cancel endpoint yet; leaving the console stops the
  frontend polling but the login task may continue until timeout. Low risk, single-user
  console.
- NAS Telegram egress currently relies on a Mac LAN CONNECT proxy. This is an operational
  dependency, not a code secret; move the proxy to the NAS/router or use a NAS VPN for a
  standalone deployment.
- `GET /channels` does not return the last validated Telegram bot username; the UI caches
  it locally. Minor UX only.
- The managed gateway is a child of the control plane. If the control-plane process is
  killed hard, the child is terminated with it; Docker restart policy starts both again.
  This is intentional for a single-home deployment; a separate compose service is the
  next step if the control plane and gateway need independent lifecycles.
- No public webhook; Telegram/WeChat both use long polling. Keep the console and channel
  credentials on a trusted network/Tailscale; no inbound port is required.

## Verification

- `744 passed, 1 skipped`; ruff clean; `web` typecheck/build green.
- NAS `/api/v1/chat` text turn: HTTP 200, `audio_bytes=0` when `want_audio=false`; no idle
  farewell after 18 s.
- Telegram voice bubble successfully produced via ffmpeg/Opus; WeChat audio file upload
  successfully exercised.
- WeChat QR login and voice tests passed on the Mac gateway; NAS rollout is Telegram-only.
