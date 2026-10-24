# Migration: house-ear → rui (0.2.0)

0.2.0 is a **breaking rename** of the project, package, CLI, paths and environment
namespace. The GitHub repository and container image were already named `rui`, so the
image name itself does not change.

Existing deployments keep working during the upgrade window: every environment read
goes through `rui.env.get()`, which tries the new `RUI_*` name first and falls back to
the legacy `HOUSE_EAR_*` name. Shell deploy scripts accept both spellings as well.
This compatibility is a one-release bridge, not a promise to keep the old names
forever.

## Mapping

| Item | Old | New (0.2.0) |
|---|---|---|
| Python package | `src/house_ear` | `src/rui` |
| Import / module | `house_ear.*` | `rui.*` |
| Distribution name | `house-ear` | `rui` |
| Main CLI | `house-ear` | `rui` |
| Channel CLI | `house-ear-channels` | `rui-channels` |
| Healthcheck | `house-ear-healthcheck` | `rui-healthcheck` |
| Env prefix | `HOUSE_EAR_*` | `RUI_*` |
| LLM key | `HOUSE_EAR_LLM_KEY` | `RUI_LLM_KEY` |
| ASR key | `HOUSE_EAR_ASR_KEY` | `RUI_ASR_KEY` |
| TTS key | `HOUSE_EAR_TTS_KEY` | `RUI_TTS_KEY` |
| HA token | `HOUSE_EAR_HA_TOKEN` | `RUI_HA_TOKEN` |
| Device token | `HOUSE_EAR_DEVICE_TOKEN` | `RUI_DEVICE_TOKEN` |
| Config path override | `HOUSE_EAR_CONFIG` | `RUI_CONFIG` |
| Web assets override | `HOUSE_EAR_WEB_DIR` | `RUI_WEB_DIR` |
| Installer overrides | `HOUSE_EAR_REPO` / `_REF` / `_IMAGE` / `_HOME` / `_RAW` | `RUI_REPO` / `RUI_REF` / `RUI_IMAGE` / `RUI_HOME` / `RUI_RAW` |
| Model mirror URLs | `HOUSE_EAR_ASR_URL` / `_VAD_URL` / `_KWS_URL` | `RUI_ASR_URL` / `RUI_VAD_URL` / `RUI_KWS_URL` |
| Data dir (container) | `/var/lib/house-ear` | `/var/lib/rui` |
| State dir (host) | `~/.local/state/house-ear` | `~/.local/state/rui` |
| Config dir (host) | `~/.config/house-ear` | `~/.config/rui` |
| Install dir default | `$HOME/house-ear` | `$HOME/rui` |
| Compose project / container | `house-ear` | `rui` |
| Backup archive prefix | `house-ear-backup-` | `rui-backup-` |
| Log file | `service.log` | `service.log` (unchanged) |

The device WebSocket contract, capability names and JSON/API field names are not part
of the rename. A speaker client configured with the old `?token=...` URL keeps working;
only the environment variable that supplies the token name changes.

## Upgrade an existing install

1. Pull the 0.2.0 image (or run the new `deploy/install.sh` with `RUI_HOME` pointing at
   the existing directory) and stop the old container.
2. Rename the install directory, or keep it and set `RUI_HOME=/path/to/house-ear`:
   `mv "$HOME/house-ear" "$HOME/rui"`.
3. Update `.env` keys to `RUI_*` when convenient. Leaving the old keys works because of
   the fallback, but the file is the operator-facing contract and should be canonical.
   At minimum set `RUI_IMAGE=ghcr.io/yes999zc/rui:0.2.0` so a plain
   `docker compose up -d` does not drift back to `latest`.
4. Update `config/config.yaml` paths and names:
   - `system.data_dir: /var/lib/rui`
   - `system.cache_dir: /var/lib/rui/music_cache` (and any `music.cache_dir`)
   - `devices[].token_env: RUI_DEVICE_TOKEN`
   - `providers.catalog.*.api_key_env: RUI_*`
   - `tools.ha.token_env: RUI_HA_TOKEN`
   The old env names in a config file still resolve, but the file should match the new
   namespace. The host directory behind `./data` does not change, so memory, history and
   secrets remain on the same volume.
5. Replace `docker-compose.yml` with the 0.2.0 file (container name `rui`, data mount
   `/var/lib/rui`), then `docker compose up -d --force-recreate`.
6. Verify: `docker compose exec rui rui-healthcheck`, `docker compose exec rui rui doctor`,
   and the console's Setup → connect page.

## Rollback

Keep the previous image tag in `.env` and the old compose file before upgrading. To roll
back, point `RUI_IMAGE` (or `HOUSE_EAR_IMAGE` on the old file) at the previous tag, restore
the old compose file if needed, and recreate the container. The fallback works in one
direction only: a 0.2.0 config that uses only `RUI_*` paths is not understood by an older
image, so keep a copy of `config/` from before the upgrade.
