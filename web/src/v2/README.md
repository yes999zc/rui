# Console v2 (M9 — config pages)

A second, side-by-side console. The legacy console at `/` keeps working and none
of its files (`panels.ts`, `PanelHost.vue`, legacy `views/`) are imported here.

## Layout

```
web/v2.html                    second Vite entry (multi-page build)
web/src/v2/main.ts             app bootstrap (shared theme + language init)
web/src/v2/router.ts           createWebHistory('/v2/')
web/src/v2/App.vue             shell: Run / Devices / House / Config, language, theme, token
web/src/v2/api.ts              hand-written client: `/api/v2/*` + C-002 §1.2 stable non-device v1 endpoints
web/src/v2/audio.ts            override surface + provenance row model for the audio tab
web/src/v2/houseAudio.ts       global audio leaf directory + field-level diff helpers (M5)
web/src/v2/setup.ts            setup-plan presentation helpers (status/evidence/field paths) (M6)
web/src/v2/useCooldown.ts      shared 429 countdown for M5 write buttons
web/src/v2/i18n.ts             v2-owned zh-Hans + en copy
web/src/v2/views/              DevicesView, AddDeviceView, SetupView, DeviceDetailView, RoomsView,
                               RunView, ConversationView, LogsView, MemoryView,
                               VoiceAssistantView, MusicView, CredentialsView, HealthView, 404
web/src/v2/components/         StateBlock, StatusBadge, CapabilityChips, ProvenanceTag,
                               TokenDialog, ConfirmDialog
web/src/v2/components/config/  ConfigField, ConfigGroupCard, DeviceConfigCard (M9)
web/src/v2/config.ts           catalog-field helpers and the field-level diff (M9)
web/src/v2/components/house/   RoomsPanel, HousePolicyEditor, HouseAudioEditor, HouseAudioField
web/src/v2/components/setup/   SetupWizard + one component per plan step (keys/models/prepare/
                               params/wake/connect/test/verify) (M6)
web/src/v2/components/device/  Identity, Capabilities, AudioProvenance, AudioPanel, Activity,
                               Connection, TestPanel, ActionsPanel, ConfigPanel, DangerPanel,
                               RestartNotice, PlaneCard, SetupCard, ParamsCard, ErrorNotice
```

## Pages

- `/v2/devices` — room-grouped device list, status/capability badges. An empty
  house links to `/v2/setup` (first run); once a device exists the header/action
  links to `/v2/devices/add`.
- `/v2/devices/add` — M6 add wizard: choose plugin/model, then render the
  server's eight-step plan (`GET /setup/plan?plugin_id=&model_id=&device_id=`).
  Step order, status, evidence, `next_step` and `blocked` are never inferred in
  the page. `params` is the former quick-add form (id/room/host/port/wake_words
  plus manifest params) and sends `device.create`; a successful create keeps the
  returned device id for the remaining steps and leaves existing devices
  untouched. Before that create, `wake/connect/test/verify` are disabled and
  cannot POST: the only `device_id` the wizard ever sends is the one the server
  returned, never the draft id. 409 `device_exists` links to the existing device;
  422 `devices[0].room` highlights the input; `port_conflict` names the port and
  links every conflicting device. A 429 starts the server-advertised cooldown and
  disables the step controls until it expires.
- `/v2/setup` — M6 first-run wizard. Same plan/engine; the backend derives
  `first-run` from an empty house. A blocked plan explains
  `house_setup_incomplete` (house keys/models stay runnable, device steps
  disabled) / `plug_unavailable` (every step is disabled until another plugin is
  selected; a step POST would 409 before dispatch). `verify.say` warns that it
  really speaks and only posts after a second confirmation. A resolved step is
  only shown as green when its own result says so (`keys.test`/`device.test`
  `ok:true`, `verify.say` with `plan.verified:true`); logical failures use a
  neutral "request completed" banner and the step result.
- `/v2/devices/:id?tab=overview|audio|config|test|activity|connect|danger` — device detail:
  - **overview** — identity, capabilities, audio provenance, plane/setup/params.
  - **audio** — `GET /devices/{id}/audio` grouped by capture/voice/playback/aec/ack.
    Every leaf shows its effective value, the `source` badge and an expandable
    four-layer view (core/manifest/global/device). Only leaves the C-001
    `DeviceAudioOverride` schema can store are editable; the rest are explicit
    read-only. A save sends a **field-level diff** (`PATCH /devices/{id}/audio`)
    after a second confirmation that states the fleet restart; a
    `restart.status="failed"` response is rendered red, never as a saved state.
    "Restore inheritance" sends `null` for one leaf, then re-reads `/audio` and
    verifies both the source (manifest/global/core) and that the path left
    `overrides`. A global value that explicitly shadows a manifest default is
    called out inline, and `plug_resolved=false` keeps the table with an
    `unresolved` explanation instead of an error page.
  - **config** — edits `room/host/enabled/wake_words/volume_target/listen_port`
    with one atomic `PATCH /devices/{id}`. Save requires a second confirmation
    that states "rebuilds the whole-house audio fleet (fleet restart)"; the
    response's `restart` always renders scope/status, and a non-empty
    `restart.error` turns red with a read-only retry. 422 `details.fields[]`
    paths (`devices[0].host`, `wake_words[1]`, …) highlight their inputs.
    id/plugin/model/adapter are read-only with the "delete + re-add to change
    the model" rule.
  - **danger** — explains disable vs delete; deletion is a second confirmation
    that additionally requires typing the exact device id (sent as
    `?confirm=<id>`), warns when deleting the last enabled device stops the
    audio plane, and returns to the list on success.
  - **test / actions** — evidence-only `POST /test` (ok/fail/not_applicable with
    code/hint) and capability-gated `POST /actions` (`volume.read/set`, `led.set`,
    `audio.say/play/stop`). Missing capabilities stay visible but disabled, with
    the server's `reason`; `unknown_capabilities` are named explicitly.
    `audio.say` warns that it really speaks and updates `last_active_room`, and
    asks for a second confirmation before sending.
  - **activity** — `GET /activity?limit=&type=`; turns and text-matched logs are
    separate groups and logs carry the `matched_by=literal` badge.
  - **connect** — masked `ws_url` by default; reveal and rotate are explicit.
    Reveal shows the token once and hides it after 30 s (or on demand). Rotate
    needs a second confirmation, renders the 429 cooldown and the two 409 cases
    (environment token, adapter-managed token), and never writes secrets.
- `/v2/run` — house overview from `GET /api/v2/house`: counts, multi-device
  policy, room aggregates and warnings. If `house.rooms` is absent it falls back
  to grouping the existing `GET /api/v2/devices` response (no new endpoint).
- `/v2/rooms` — rooms & house (M5): room cards derived from `devices[].room`
  (no room CRUD), duplicate-room warnings, default-output staging, the
  multi-device policy editor (`enabled` / `max_planes` / `default_output` /
  ordered `room_priority`), and the global audio defaults editor.
  Both editors send a **field-level diff** and ask for a second confirmation.
  A global-audio save never restarts the audio plane; the explicit
  `POST /house/audio/restart` button renders the returned `restart` envelope
  (scope/status/affected/duration/error, failures red). The page states that
  global values shadow the plug manifest, shows the `explicit` marker per leaf,
  and counts devices whose own `/devices/{id}/audio` overrides are non-empty.
- `/v2/voice-assistant` — M9 catalog-driven config: `GET /config/catalog` supplies
  groups, field order, kind/range/options and `effect`; `PUT /config/values` sends
  only the changed dotted paths. `effect=restart` needs a confirmation and the
  response's `restart_fields` stays visible. 422 `details.fields[].path`
  (FastAPI `detail[].loc`) marks the row red. Device fields are read-only here and
  link to `/v2/devices/{id}`.
- `/v2/music` — M9 source registry (`/music/sources`), playback/config status
  (`/music`), presence-only credential slots, source switch/save through
  `PUT /music/credentials`, and a draft-only `POST /music/test`. Credential
  inputs are `type=password`; saved values are never rendered.
- `/v2/credentials` — M9 secret names + configured/not configured only
  (`GET /secrets`), write-only `POST /secrets`, and a post-write
  `POST /providers/probe`. Values stay in a local ref, are cleared after a
  successful write, and never enter localStorage, the URL or a console log.
- `/v2/health` — M9 `/health` + `/doctor` + `/config/recovery` in five sections
  with an exceptions-first list; doctor checks and planes put fail/warn rows
  first. Read-only except explicit download/restore actions (restore requires a
  confirm dialog).
- `/v2/config` — compatibility redirect to `/v2/voice-assistant`.

## Deploy / mount requirements (backend)

The frontend side is complete; FastAPI still needs the v2 shell routes from
contract C-002 §2.2:

| Request | Response |
|---|---|
| `GET /v2`, `GET /v2/*` (not `/api`, not assets) | `dist/v2.html` (history fallback for deep links) |
| `GET /api/v2/*` | JSON from the v2 API — must be registered **before** the SPA catch-all |
| assets | static files referenced by the built HTML |

**Known deviation to resolve on the backend side:** because this task only added
a second `rollupOptions.input` (no output/base changes), Vite emits the usual
`/assets/*` filenames, and `dist/v2.html` references `/assets/...`. The contract
table mentions `/v2/assets/*`; serving the shared `/assets` prefix (already used
by the legacy shell) satisfies the built HTML. If the backend insists on
`/v2/assets/*`, either add an output naming rule in `vite.config.ts` (needs Lead
approval; it changes build config beyond the multi-page entry) or map
`/v2/assets/*` → `dist/assets/*` in FastAPI.

## Auth

Same convention as the legacy console: token in `localStorage['rui-token']`,
sent as `X-Auth-Token` on every `/api/v2/*` call. Any 401 opens the v2 token
dialog and shows an inline error; saving reloads the page.

`GET /devices/{id}/connection` is only ever displayed in its masked form.
`POST /connection/reveal` and `POST /token/rotate` run only after an explicit
button click; their values live in component state (never in localStorage) and
disappear after 30 seconds.

## Local rendering / dev

`npm run dev` still serves the legacy `index.html`. The v2 router base is
`/v2/`, so `http://localhost:5173/v2.html` intentionally does not resolve as a
page (the path does not start with the base). To exercise the built shell, run
`npm run build` and serve `dist/` behind a tiny static host that maps `/v2(/*)`
to `dist/v2.html` (or use FastAPI). No mock API belongs in the source tree;
temporary harnesses must live outside `web/src/v2/` and be deleted afterwards.

## Lead errata (2026-10-25)

- `model_id` is the manifest's opaque device id; the human-readable name is
  `model`. The UI never uses ids as display copy and never branches on them.
- Write responses are envelopes: `POST /devices` → `{device, restart}`,
  `PATCH /devices/{id}` → `{device, restart}`, `PATCH /devices/{id}/audio` →
  `{audio, device, restart}`, `DELETE /devices/{id}` → `{removed: true, device_id}`,
  `POST /house/audio/restart` → `{restart}`. The device create/patch/delete
  calls are wired in `api.ts` since M3.
- `DeviceDetail.unknown_params` lists legacy keys kept in config but not declared
  by the manifest; the detail page shows them read-only.
- The backend does not expose `requires_socket` or `secret_params`; the detail
  page therefore has no secret section and the types omit both fields.
  Capabilities and params schema are the only device-shape sources.

## Detail page split (M2/M3/M4)

`DeviceDetailView.vue` is layout + data assembly only. The read-only sections
live in `components/device/` (`Identity`, `Capabilities`, `AudioProvenance`,
`Activity`, `Connection`), and the interactive panels are `AudioPanel`,
`TestPanel`, `ActionsPanel`, `ConfigPanel` and `DangerPanel`. Each section keeps
its own loading/error/retry state; a failed write never clears loaded device
data, and every write disables its submit control while in flight.

## Rules for follow-up work

- **M5 draft state:** the global-audio editor lives in `HouseAudioEditor.vue`
  instead of growing `AudioPanel.vue`. Its clear semantics (`null` removes an
  explicit global leaf) and restart scope (`audio_plane`) differ from the
  device-override editor, so the two state machines are intentionally separate
  for now. `houseAudio.ts` and `audio.ts` currently duplicate the 37-leaf
  metadata table (paths are 37/37 identical; bounds are checked against
  `src/rui/config/schema.py` and are not always numerically equal on purpose:
  the global `ack.echo_ignore_ms` is ≤ 3000, while the per-device override
  `voice.ack_echo_ignore_ms` is ≤ 5000). **Before M6**, extract the shared
  leaf metadata and a `useAudioDraft` composable; do not add more surfaces to
  `AudioPanel.vue`.
- **Unsaved drafts on `/v2/rooms`:** both editors emit `dirty-change`; the page
  blocks in-app navigation with a confirmation and only guards `beforeunload`
  while dirty. Keep new editors on that page wired to the same signal.
- No model-name branches. Every device difference is read from `capabilities`,
  `params`, or the plugin catalog.
- Action controls are driven by `effective_capabilities`; when a capability is
  missing the controls stay rendered but disabled and the `reason` is shown.
  `unknown_capabilities` are listed rather than guessed at.
- All user-facing copy goes through `t()`; add keys to **both** dictionaries.
- `meta.features` gates pages: an explicit `false` renders "not enabled in this
  build"; a missing key is treated as available so a partial backend still works.
- Each section of the detail page loads independently, so a missing endpoint
  never blanks the whole page.
- Test/actions are not "save" operations and must not show a saved state; the
  backend owns the real result. `audio.say` is the only action with a visible
  side-effect warning and a second confirmation.

## Integration status

M9 is typed and built against C-002/1. The config pages use the stable v1
non-device endpoints allowed by C-002 §1.2; no v1 device endpoint is called.
Rendering and interaction flows were checked with a one-shot headless-Chrome
harness outside the repository (catalog-driven order/fields, exactly-one-path
PUT body, restart confirmation, 422 field marking, presence-only credentials,
secret canary absence from DOM/storage/URL/console/network, provider probe,
exceptions-first health, restore confirmation, and four deep links).
