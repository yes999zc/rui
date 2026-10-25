# Console v2 (M2 — device detail interactions)

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
web/src/v2/views/              DevicesView, DeviceDetailView, RunView, ConfigView, 404
web/src/v2/components/         StateBlock, StatusBadge, CapabilityChips, ProvenanceTag, TokenDialog
web/src/v2/components/device/  Identity, Capabilities, AudioProvenance, Activity, Connection,
                               TestPanel, ActionsPanel, PlaneCard, SetupCard, ParamsCard, ErrorNotice
```

## Pages

- `/v2/devices` — room-grouped device list, status/capability badges.
- `/v2/devices/:id?tab=overview|test|activity|connect` — device detail:
  - **overview** — identity, capabilities, audio provenance, plane/setup/params.
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
  `POST /house/audio/restart` → `{restart}`. These are typed in
  `api.ts` even though M2 only adds device test/action/activity/token calls.
- `DeviceDetail.unknown_params` lists legacy keys kept in config but not declared
  by the manifest; the detail page shows them read-only.
- The backend does not expose `requires_socket` or `secret_params`; the detail
  page therefore has no secret section and the types omit both fields.
  Capabilities and params schema are the only device-shape sources.

## M2 split (done)

`DeviceDetailView.vue` is now layout + data assembly only (well under 400 lines).
The five read-only sections live in `components/device/` (`Identity`,
`Capabilities`, `AudioProvenance`, `Activity`, `Connection`), and the M2 write
interactions live in `TestPanel` and `ActionsPanel`. Each section keeps its own
loading/error/retry state; a failed write never clears loaded device data.

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

M2 is typed and built against C-002/1. The Python `/api/v2` implementation is
being finished by a separate workstream, so **live end-to-end integration has
not been run** from this checkout; rendering and interaction flows were checked
with a temporary mock server outside the repository.
