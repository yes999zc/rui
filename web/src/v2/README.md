# Console v2 (M3 — device create / edit / delete)

A second, side-by-side console. The legacy console at `/` keeps working and none
of its files (`panels.ts`, `PanelHost.vue`, legacy `views/`) are imported here.

## Layout

```
web/v2.html                    second Vite entry (multi-page build)
web/src/v2/main.ts             app bootstrap (shared theme + language init)
web/src/v2/router.ts           createWebHistory('/v2/')
web/src/v2/App.vue             shell: Run / Devices / Config, language, theme, token
web/src/v2/api.ts              hand-written C-002 client (`/api/v2/*` only)
web/src/v2/i18n.ts             v2-owned zh-Hans + en copy
web/src/v2/views/              DevicesView, AddDeviceView, DeviceDetailView, RunView, ConfigView, 404
web/src/v2/components/         StateBlock, StatusBadge, CapabilityChips, ProvenanceTag,
                               TokenDialog, ConfirmDialog
web/src/v2/components/device/  Identity, Capabilities, AudioProvenance, Activity, Connection,
                               TestPanel, ActionsPanel, ConfigPanel, DangerPanel, RestartNotice,
                               PlaneCard, SetupCard, ParamsCard, ErrorNotice
```

## Pages

- `/v2/devices` — room-grouped device list, status/capability badges, Add entry.
- `/v2/devices/add` — minimal M3 create flow: `GET /plugins` → plugin/model →
  id/room/host/listen_port/wake_words → `POST /devices` (201 → device detail;
  409 `device_exists` links to the existing device; 422 `port_conflict` names
  the port and owning device ids).
- `/v2/devices/:id?tab=overview|config|test|activity|connect|danger` — device detail:
  - **overview** — identity, capabilities, audio provenance, plane/setup/params.
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
- `/v2/config` — honest "not in this milestone" state.

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

## Detail page split (M2/M3)

`DeviceDetailView.vue` is layout + data assembly only. The five read-only
sections live in `components/device/` (`Identity`, `Capabilities`,
`AudioProvenance`, `Activity`, `Connection`), and the interactive panels are
`TestPanel`, `ActionsPanel`, `ConfigPanel` and `DangerPanel`. Each section keeps
its own loading/error/retry state; a failed write never clears loaded device
data, and every write disables its submit control while in flight.

## Rules for follow-up work

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

M3 is typed and built against C-002/1. The Python `/api/v2` implementation is
being finished by a separate workstream, so **live end-to-end integration has
not been run** from this checkout; rendering and interaction flows were checked
with a temporary headless Playwright mock outside the repository (config save
confirmation, 422 field mapping, restart error/retry, typed delete, 409/422
create errors, create navigation).
