# Install paths: `RUI_PLUGINS_DIR` vs `<data_dir>/plugs` vs `RUI_ADAPTER_PLUGINS`

rui has three ways to see a plug. They answer different questions and are easy to
confuse:

| | `RUI_PLUGINS_DIR` | `<data_dir>/plugs/<id>/` | `RUI_ADAPTER_PLUGINS` |
| --- | --- | --- | --- |
| What it is | os.pathsep-separated list of **catalog roots** | a **data directory** the catalog scans | a list of **explicit in-process adapter entries** |
| Who sets it | operator (environment/image) | the install/wizard/user | operator (environment, root/container only) |
| Executes adapter code? | **yes** — in-process entry points of `builtin`/`trusted` plugs are registered at startup | **no** — manifests/declarations only | **yes** — imports the named module and registers the builder |
| Shows up in console/`/api/v2/plugins`? | yes | yes | only the manifest it was gated against, if one resolves |
| Needs a manifest? | yes (`<root>/<plug>/plugin.yaml`) | yes | **yes**, and `trust: builtin|trusted` |
| Typical use | ship a plug tree with the image / operator-managed install | keep manifests in the data volume; wizard-managed | out-of-tree bridge development or installed package |

The one-line version: **`RUI_PLUGINS_DIR` and `RUI_ADAPTER_PLUGINS` are execution
paths; `<data_dir>/plugs` is a declaration/catalog path.** A plug under
`<data_dir>/plugs` is visible but will not run until an execution path names it.

---

## 1. `RUI_PLUGINS_DIR` — operator roots, append-only

Semantics ([`src/rui/plugins/builtin.py:43-75`](../../src/rui/plugins/builtin.py#L43-L75)):

- The value is a `:`-separated (PATH-like) list of **roots**; each root contains
  one directory per plug: `<root>/<plug-dir>/plugin.yaml`.
- Roots are scanned **in the order written**; the image directory
  `/opt/rui-plugins` is then appended if it exists. This is not a replacement:
  an operator override never hides the adapter an existing config selects.
  The repository's `plugs/` directory is used only as a development fallback
  when neither of the above exists.
- Directories without `plugin.yaml` are ignored; symlinked plug directories are
  **skipped** ([`src/rui/plugins/builtin.py:133-140`](../../src/rui/plugins/builtin.py#L133-L140)).
- `builtin_plugins()` scans the roots and treats `builtin`/`trusted` plugs as
  image plugs: their `runtime.entrypoint` is imported and registered as the
  device manifest `id` ([`src/rui/device/registry.py:92-145`](../../src/rui/device/registry.py#L92-L145)).
  A user-writable `<data_dir>/plugs` is deliberately not in this list.

**Use it when** you ship a whole plug directory with the core (downstream image),
or you want one operator-managed root that you can point at a versioned copy.
It is the only **directory-based** path that makes an in-process adapter run
**without** `RUI_ADAPTER_PLUGINS` (the `rui.adapters` entry-point group is the
other operator channel, for installed distributions; an operator root does not
need it).

**Do not use it** to point at a single plug directory: the root is the parent,
not the plug. `RUI_PLUGINS_DIR=/opt/rui-plugins` + `/opt/rui-plugins/dot2/plugin.yaml`
is correct; `RUI_PLUGINS_DIR=/opt/rui-plugins/dot2` finds nothing.

---

## 2. `<data_dir>/plugs/<id>/` — catalog, management, and the manifest home

`discover_plugins(data_dir)` reads `<data_dir>/plugs/<dir>/plugin.yaml`, skips
non-directories and symlinks, and returns `plugins` + `errors` as data (one bad
plug never stops the scan) ([`src/rui/plugins/loader.py:275-326`](../../src/rui/plugins/loader.py#L275-L326)).

This is what makes a plug appear in:

- `GET /api/v2/plugins` and the device page/wizard,
- `PlugCatalog` device resolution (by `params.plug_id`, then by adapter name =
  device manifest id) ([`src/rui/plugins/resolve.py:132-231`](../../src/rui/plugins/resolve.py#L132-L231)),
- the audio plane's hardware facts (`audio_defaults`) for a device.

It does **not** execute the adapter. `_ensure_image_adapters` only walks
`builtin_plugins()` and its docstring is explicit that a user plug still has to
be named by the operator through `RUI_ADAPTER_PLUGINS`
([`src/rui/device/registry.py:92-114`](../../src/rui/device/registry.py#L92-L114)).
`docs/socket-plug-v1.md` §6.1 says the same: `data/plugs/` carries
manifest/declaration, not code execution.

**How code installed elsewhere still uses this manifest:** put the manifest under
`<data_dir>/plugs/<id>/`, register the module with `RUI_ADAPTER_PLUGINS`, and
point `RUI_DATA_DIR` at the data directory. The adapter loader checks the
manifest next to the module first, then `<data_dir>/plugs` when the module is
external ([`src/rui/device/loader.py:292-377`](../../src/rui/device/loader.py#L292-L377)).
`RUI_DATA_DIR` is needed because the startup hook runs before the config value
is passed; without it the loader only sees co-located manifests
([`src/rui/device/loader.py:50-54`](../../src/rui/device/loader.py#L50-L54),
[`src/rui/device/loader.py:120-129`](../../src/rui/device/loader.py#L120-L129)).

**Use it when** you want the manifest visible/manageable before enabling a device,
when the console/wizard owns the device entry, or when you install the manifest
from a plug package but keep the Python code in a package/distribution.

---

## 3. `RUI_ADAPTER_PLUGINS` — explicit in-process registration

`RUI_ADAPTER_PLUGINS` is a comma-separated list
([`src/rui/device/loader.py:48`](../../src/rui/device/loader.py#L48),
[`src/rui/device/loader.py:113-147`](../../src/rui/device/loader.py#L113-L147)):

```text
# name=module:build (name is required to match devices[].adapter)
RUI_ADAPTER_PLUGINS="fake-speaker=fake_device:build"
# no name: the plugin-returned name, else the module name
RUI_ADAPTER_PLUGINS="my_plugin.adapter:build"
# registration hook instead of a builder
RUI_ADAPTER_PLUGINS="my_plugin.adapter:register"
# several entries, processed in order
RUI_ADAPTER_PLUGINS="dot2=rui_dot2_bridge.adapter:build,lx06x=other:build"
```

Rules that matter for an install:

- **Manifest-gated.** The entry registers only when a resolvable manifest with
  `trust: builtin` or `trust: trusted` backs the adapter name. `third-party` is
  refused; no manifest is refused; a built-in name (`mock`, image adapters)
  cannot be replaced ([`src/rui/device/loader.py:242-289`](../../src/rui/device/loader.py#L242-L289),
  [`src/rui/device/registry.py:44-49`](../../src/rui/device/registry.py#L44-L49)).
- **Manifest location.** A `plugin.yaml` next to the module/package is always
  checked first; otherwise `<data_dir>/plugs` is searched when `RUI_DATA_DIR` is
  set. A manifest that exists but fails to load is a hard failure, not a
  fall-through.
- **Two processes.** The control plane loads entries at startup, and the audio
  plane child loads them again in `build_plane()`; both need the same
  environment ([`src/rui/web/api/app.py:3256-3261`](../../src/rui/web/api/app.py#L3256-L3261),
  [`src/rui/audio/process.py:3994-4000`](../../src/rui/audio/process.py#L3994-L4000)).
- **Runtime defaults.** When the config omits `listen_port`/`token_env`, the
  manifest's `runtime.listen_port`/`runtime.token_env` fill them; an explicit
  config value wins ([`src/rui/device/registry.py:245-284`](../../src/rui/device/registry.py#L254-L284),
  [`src/rui/web/api/device_rules.py:51-101`](../../src/rui/web/api/device_rules.py#L51-L101)).

**Use it when** the adapter is out-of-tree (a bridge repository), or when an
installed Python package provides the code and you want the operator to name it
explicitly. It is the closest route to the dot2 bridge's production recipe.

---

## 4. Recommended order

Pick the first row that matches your situation; later rows are heavier.

1. **`RUI_ADAPTER_PLUGINS` + a co-located `plugin.yaml`** (adapter development,
   bridge repositories, a single operator-managed copy). Fewest moving parts: one
   directory, one env var, no image rebuild, no `RUI_DATA_DIR` dependency.
   This is what the quickstart uses ([`quickstart.md`](quickstart.md)).
2. **`RUI_ADAPTER_PLUGINS` + manifest under `<data_dir>/plugs/<id>/` +
   `RUI_DATA_DIR`** when you also want the plug visible in the console/wizard and
   the same manifest to describe the running adapter (the loader registers the
   gating manifest into the process catalog; `/api/v2/plugins` then reports the
   version/origin that actually gated the code —
   [`src/rui/plugins/resolve.py:49-77`](../../src/rui/plugins/resolve.py#L49-L77)).
3. **`RUI_PLUGINS_DIR`** when you own a whole plug tree (downstream image,
   operator root) and want rui to register the adapters automatically from the
   manifests. This is the packaging route; it needs no
   `RUI_ADAPTER_PLUGINS`, but every plug must be complete and versioned as one
   tree.

Do not combine 3 and the same adapter id in 1 unless you intend the explicit env
entry to win: an env/entry-point registration may override a non-builtin adapter
with a warning, but entry points never override env or builtin names
([`src/rui/device/loader.py:818-857`](../../src/rui/device/loader.py#L818-L857)).

---

## 5. Upgrade and rollback

### What an install change does *not* touch

`RUI_PLUGINS_DIR` (and `RUI_ADAPTER_PLUGINS`/`RUI_DATA_DIR`) affect **discovery
and registration only**. They never rewrite `config.yaml`; `devices[].adapter`
stays the same string you wrote, and the config loader is unaware of where the
plug lives ([`src/rui/plugins/builtin.py:57-75`](../../src/rui/plugins/builtin.py#L57-L75),
[`src/rui/device/loader.py:113-147`](../../src/rui/device/loader.py#L113-L147)).
Consequences:

- Pointing `RUI_PLUGINS_DIR` at a new directory and restarting is a valid upgrade
  **and** a valid rollback; no config edit is needed.
- If the old directory is gone and the config still selects that adapter, rui
  starts but reports the device as unavailable (`unknown adapter`); it does not
  corrupt or rewrite the config. The fix is the environment, not the YAML.

### Upgrade

1. Stage the new plug tree in a new directory (for example
   `$DATA_DIR/plugins-v2/<plug>/`), keeping the old one untouched.
2. Run the contract kit against the new tree:
   `rui plug-test <plug-dir> --adapter <module>:build`.
3. Switch the environment (`RUI_PLUGINS_DIR=…/plugins-v2`, or update the
   package/manifest for `RUI_ADAPTER_PLUGINS` + `RUI_DATA_DIR`) and restart rui.
   A restart is required: the builtin catalog caches its scan per process by the
   resolved directory list ([`src/rui/plugins/builtin.py:52-54`](../../src/rui/plugins/builtin.py#L52-L54),
   [`src/rui/plugins/builtin.py:117-124`](../../src/rui/plugins/builtin.py#L117-L124)),
   and registered adapters are fixed at startup.
4. Verify: `adapter_plugins.registered` contains the name, `/health` shows
   `device_errors: 0`, the device is `ready`, and one capture + one playback
   works (see [`debugging.md`](debugging.md) if not).
5. Only then remove the old tree.

Avoid symlinked plug directories in a root: both scanners skip symlinks
([`src/rui/plugins/builtin.py:133-140`](../../src/rui/plugins/builtin.py#L133-L140),
[`src/rui/plugins/loader.py:307-312`](../../src/rui/plugins/loader.py#L307-L312)).
Use versioned directories plus an environment switch instead of a symlink.

### Rollback

1. Set the environment back to the previous versioned directory (or previous
   package/manifest combination) and restart.
2. If the adapter was registered by name from a distribution, reinstall the
   previous package version; do not leave two manifests with the same id in
   different roots — the scan order decides which one wins
   ([`src/rui/plugins/builtin.py:65-75`](../../src/rui/plugins/builtin.py#L65-L75)).
3. Confirm with `curl /health` (`adapter_plugins.registered`, `device_errors`)
   and the device detail `status_reason.code` is `ready`, not
   `adapter_unavailable`/`plug_unresolved`.

The config keeps working across both directions because `devices[].adapter` is
only a name. What changes is whether that name resolves.

---

## 6. Verification commands

```bash
# manifest only, then manifest + adapter (no hardware)
rui plug-test <plug-dir>
rui plug-test <plug-dir> --adapter <module>:build

# what the running process registered (names + warning count; text is in the log)
curl -s http://127.0.0.1:8080/health | python3 -m json.tool

# which plug describes a device, and whether the device is resolved
curl -s http://127.0.0.1:8080/api/v2/plugins | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/v2/devices/<device-id> | python3 -m json.tool

# registration failures: every line starts with "adapter plugin:"
grep "adapter plugin:" /var/lib/rui/service.log
```

The `/health` body exposes `adapter_plugins.registered`/`warnings` but not the
warning text, by design ([`src/rui/web/api/app.py:4247-4251`](../../src/rui/web/api/app.py#L4247-L4251):
"the service only exposes names/counts, never tracebacks"). The browser console's
log page reads `service.log`.
