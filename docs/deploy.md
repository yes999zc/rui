# Deploy

## Requirements

- x86_64 or arm64, 4 cores, 4 GB RAM, 2 GB free disk
- Docker with `--network host` allowed (audio is a long-lived socket)
- The speaker on the same L2 network

## One command

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/house-ear/main/deploy/install.sh | sh
```

It writes `~/house-ear/config/config.yaml` and `.env`, then starts the service.
Fill in the keys in `.env`:

```bash
HOUSE_EAR_LLM_KEY=
HOUSE_EAR_ASR_KEY=
HOUSE_EAR_TTS_KEY=
HOUSE_EAR_HA_TOKEN=
```

Then open `http://<nas-ip>:8080/` and run the doctor once.

## Manual

```bash
git clone https://github.com/yes999zc/house-ear && cd house-ear
uv sync && uv run house-ear doctor
uv run house-ear serve
```

## Notes

- `network_mode: host` is deliberate. Port mapping breaks the device's audio socket.
- Config lives on disk, written atomically with timestamped backups. There is no
  in-memory config that can drift from the file.
- Logs: `docker compose logs -f house-ear`. M2 adds a WebSocket log view.
