"""Plugs and the eight-step install wizard (P2.2 backend slice).

Kept out of ``app.py`` so the factory stays a wiring file. Everything here is
server-computed: secrets are only checked for presence, the plane's live metrics are the
connection evidence, and ``data_dir/setup.json`` holds only confirmations/timestamps.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException
from pydantic import ValidationError

from ... import secrets
from ... import setup_state as setup_store
from ...audio.kws import KwsUnavailable, model_files
from ...config.schema import Config, DeviceConfig
from ...config.store import ConfigError, load_config
from ...plugins import (
    DeviceManifest,
    Plugin,
    RuntimeMode,
    TrustLevel,
    builtin_plugins,
    discover_plugins,
)

__all__ = ["install_setup_routes"]


def install_setup_routes(
    app: FastAPI,
    auth: Callable[..., None],
    *,
    config_path: Path | None,
    current: Callable[[], Config],
    plane_snapshot: Callable[[FastAPI], dict],
    write_devices: Callable[[Config], Awaitable[str]],
    probe_host: Callable[[str], Awaitable[dict]],
    run_debug_say: Callable[[str], Awaitable[dict]],
    host_value: Callable[[object], str],
    room_value: Callable[[object], str],
    wake_words_value: Callable[[dict], list[str]],
) -> None:
    # ---------------------------------------------------------------- plugs (P2)
    # Built-in manifests are validated in code; user plugs are discovered best-effort
    # under <data_dir>/plugs/*/plugin.yaml. A broken user plug is listed with its error,
    # never allowed to stop startup or hide the other plugs.
    _SETUP_ACTIONS: dict[str, set[str]] = {
        "keys": {"keys.save"},
        "models": set(),
        "prepare": {"manual.confirm"},
        "params": {"device.upsert"},
        "wake": {"wake.set"},
        "connect": {"connect.inspect"},
        "test": {"device.test"},
        "verify": {"try.run", "manual.confirm"},
    }

    def _setup_error(status: int, code: str, message: str,
                     details: dict | None = None) -> HTTPException:
        """New endpoints answer with an object detail; legacy routes keep strings."""
        return HTTPException(
            status_code=status,
            detail={"code": code, "message": message, "details": details or {}},
        )

    def _current() -> Config:
        return current()

    def _object_error(exc: HTTPException) -> HTTPException:
        """Wrap a legacy string-detail error in the new object shape."""
        if isinstance(exc.detail, dict):
            return exc
        return _setup_error(exc.status_code, "invalid_values", str(exc.detail))

    def _plug_error(plugin: Plugin) -> str:
        """Why this build cannot install the plug; "" means available."""
        if plugin.trust not in (TrustLevel.BUILTIN, TrustLevel.TRUSTED):
            return "third_party_not_supported"
        for device in plugin.devices:
            runtime = device.runtime
            if runtime is not None and runtime.mode is RuntimeMode.SIDECAR:
                return "sidecar_not_supported"
        return ""

    def _plugin_catalog() -> tuple[dict[str, tuple[Plugin, str, str]], list[dict]]:
        entries: dict[str, tuple[Plugin, str, str]] = {}
        failed: list[dict] = []
        for plugin in builtin_plugins():
            entries[plugin.id] = (plugin, "builtin", _plug_error(plugin))
        scan = discover_plugins(_current().system.data_dir)
        for plugin in scan.plugins:
            if plugin.id in entries:
                failed.append(_failed_plug(
                    None, f"{plugin.source_dir}: duplicate plugin id {plugin.id!r}; skipped"))
                continue
            entries[plugin.id] = (plugin, "user", _plug_error(plugin))
        for message in scan.errors:
            failed.append(_failed_plug(None, message))
        return entries, failed

    def _failed_plug(plugin_id: str | None, message: str) -> dict:
        return {
            "id": plugin_id, "name": None, "version": None, "trust": None,
            "class": None, "adapter": None, "transport": None, "source": "user",
            "available": False, "error": message, "devices": [],
        }

    def _plug_summary(plugin: Plugin, source: str, error: str) -> dict:
        first = plugin.devices[0]
        return {
            "id": plugin.id,
            "name": plugin.name,
            "version": plugin.version,
            "trust": str(plugin.trust),
            "class": str(first.device_class),
            "adapter": first.id,
            "transport": str(first.transport),
            "source": source,
            "available": not error,
            "error": error or None,
            "devices": [device.id for device in plugin.devices],
        }

    def _target_device(current: Config, plugin: Plugin | None = None) -> DeviceConfig | None:
        """Prefer an enabled device that explicitly names the plug, then an enabled one."""
        candidates = [device for device in current.devices if device.enabled] or list(current.devices)
        if not candidates:
            return None
        if plugin is not None:
            for device in candidates:
                if str(device.params.get("plug_id") or "") == plugin.id:
                    return device
            for device in candidates:
                if any(item.id == device.adapter for item in plugin.devices):
                    return device
        return candidates[0]

    def _select_plugin(current: Config, requested: str | None = None
                       ) -> tuple[Plugin, str, str]:
        entries, _failed = _plugin_catalog()
        if requested:
            found = entries.get(requested)
            if found is None:
                raise _setup_error(
                    404, "unknown_plug", f"no such plug: {requested}",
                    {"plug_id": requested, "available": sorted(entries)},
                )
            return found
        device = _target_device(current)
        if device is not None:
            plug_id = str(device.params.get("plug_id") or "")
            if plug_id and plug_id in entries:
                return entries[plug_id]
            for plugin, source, error in entries.values():
                if any(item.id == device.adapter for item in plugin.devices):
                    return plugin, source, error
        for source in ("builtin", "user"):
            for plugin, entry_source, error in entries.values():
                if entry_source == source:
                    return plugin, source, error
        raise _setup_error(409, "plug_unavailable", "no plug is available in this build")

    def _device_manifest(plugin: Plugin, device: DeviceConfig | None) -> DeviceManifest:
        if device is not None:
            for item in plugin.devices:
                if item.id == device.adapter:
                    return item
        return plugin.devices[0]

    def _keywords_path(current: Config) -> Path | None:
        configured = str(current.audio.voice.kws_keywords_file or "").strip()
        if configured:
            return Path(configured).expanduser()
        if config_path is not None:
            return Path(config_path).resolve().parent / "keywords.txt"
        return None

    def _turn_after_setup(started: str) -> dict | None:
        start = setup_store.epoch(started)
        turns = list(getattr(app.state, "recent_turns", None) or [])
        last = getattr(app.state, "last_turn", None)
        if isinstance(last, dict):
            turns.append(last)
        for turn in turns:
            if not isinstance(turn, dict) or int(turn.get("audio_bytes") or 0) <= 0:
                continue
            stamp = turn.get("ts_epoch")
            if start is not None and isinstance(stamp, (int, float)) and float(stamp) < start:
                continue
            return turn
        return None

    def _setup_state(requested_plug: str | None = None) -> dict:
        current = _current()
        plugin, source, plug_error = _select_plugin(current, requested_plug)
        device = _target_device(current, plugin)
        manifest = _device_manifest(plugin, device)
        # The file is the durable copy; the in-memory copy keeps the start stable when
        # data_dir is read-only (a container misconfiguration must not hide a new turn).
        started = str(getattr(app.state, "setup_started_at", "") or "")
        if not started:
            started = setup_store.ensure_started(current.system.data_dir)
            app.state.setup_started_at = started
        saved = setup_store.read(current.system.data_dir)
        plane = plane_snapshot(app)
        plane_error = str(getattr(app.state, "plane_error", "") or "")

        # keys: a provider secret exists for every stage (and every required name of
        # that provider). Values are never read out; only presence is reported.
        stage_ok: dict[str, bool] = {}
        missing: set[str] = set()
        for stage in ("asr", "tts", "llm"):
            ok = False
            chain = list(getattr(current.providers, stage).chain)
            for name in chain:
                provider = current.providers.catalog.get(name)
                if provider is None:
                    continue
                names = [
                    str(item) for item in (
                        getattr(provider, "api_key_env", ""),
                        getattr(provider, "app_id_env", ""),
                        getattr(provider, "api_secret_env", ""),
                    ) if item
                ]
                if not names:
                    ok = True
                    continue
                present = all(secrets.resolve(item) is not None for item in names)
                ok = ok or present
                missing.update(item for item in names if secrets.resolve(item) is None)
            stage_ok[stage] = ok
        keys_done = bool(stage_ok) and all(stage_ok.values())
        keys_step = {
            "id": "keys", "status": "done" if keys_done else "pending",
            "evidence": {"stages": stage_ok, "missing": sorted(missing)},
            "detail": {"stages": stage_ok, "missing": sorted(missing)},
            "actions": ["keys.save"],
        }

        # models: ASR (sherpa) files, VAD file, and the four KWS files.
        asr_dir: Path | None = None
        for name in current.providers.asr.chain:
            provider = current.providers.catalog.get(name)
            if provider is None or str(getattr(provider, "kind", "")) != "sherpa":
                continue
            model = str(getattr(provider, "model", "") or "")
            if model:
                asr_dir = Path(model).expanduser()
                break
        asr_ok = bool(
            asr_dir is not None and asr_dir.is_dir()
            and (asr_dir / "tokens.txt").is_file()
            and any((asr_dir / name).is_file() for name in ("model.int8.onnx", "model.onnx"))
        )
        voice = current.audio.voice
        vad = Path(voice.vad_model_path).expanduser() if voice.vad_model_path else None
        kws = Path(voice.kws_model_dir).expanduser() if voice.kws_model_dir else None
        try:
            kws_files = model_files(kws) if kws is not None else {}
        except KwsUnavailable:
            kws_files = {}
        models_done = asr_ok and bool(vad and vad.is_file()) and len(kws_files) == 4
        models_step = {
            "id": "models", "status": "done" if models_done else "pending",
            "evidence": {"asr": asr_ok, "vad": bool(vad and vad.is_file()),
                         "kws": len(kws_files) == 4},
            "detail": {"asr_dir": str(asr_dir) if asr_dir else "",
                       "vad": str(vad) if vad else "",
                       "kws_dir": str(kws) if kws else ""},
            "actions": [],
        }

        # prepare: a persisted manual confirmation (B also needs rollback_ack).
        manual = saved.get("prepare", {}).get("manual", {})
        if not isinstance(manual, dict):
            manual = {}
        artifact = str(manifest.device_side.artifact) if manifest.device_side else "none"
        needs_rollback = str(manifest.device_class) == "B"
        confirmed = bool(manual.get("confirm"))
        rollback_ack = bool(manual.get("rollback_ack"))
        prepare_skipped = artifact == "none"
        prepare_done = prepare_skipped or (confirmed and (rollback_ack or not needs_rollback))
        prepare_step = {
            "id": "prepare",
            "status": "skipped" if prepare_skipped else ("done" if prepare_done else "pending"),
            "evidence": {"artifact": artifact, "confirmed": confirmed,
                         "rollback_ack": rollback_ack,
                         "rollback_required": needs_rollback},
            "detail": {"artifact": artifact,
                       "source": manifest.device_side.source if manifest.device_side else ""},
            "actions": [] if prepare_skipped else ["manual.confirm"],
        }

        # params: an enabled device with every required manifest parameter present.
        missing_params: list[str] = []
        if device is not None:
            for spec in manifest.params:
                if not spec.required:
                    continue
                key = spec.key
                if key == "host":
                    value = device.host
                elif key == "room":
                    value = device.room
                elif key == "wake_words":
                    value = device.wake_words
                else:
                    value = device.params.get(key)
                if str(spec.type) == "secret":
                    name = key.upper()
                    if secrets.resolve(name) is None and secrets.resolve(key) is None:
                        missing_params.append(key)
                elif value in (None, "", []):
                    missing_params.append(key)
        params_ready = (
            device is not None and bool(device.enabled) and bool(device.host)
            and any(adaptor.id == device.adapter for adaptor in plugin.devices)
            and not missing_params
        )
        params_status = "done" if params_ready else "pending"
        if params_ready and plane_error:
            params_status = "error"
        params_step = {
            "id": "params", "status": params_status,
            "evidence": {"device": device.id if device else None,
                         "enabled": bool(device and device.enabled),
                         "adapter": device.adapter if device else None,
                         "missing": missing_params,
                         "plane_error": plane_error},
            "detail": {"device_id": device.id if device else None,
                       "params": sorted((device.params if device else {}).keys())},
            "actions": ["device.upsert"],
        }

        # wake: the file exists, has content, and mentions every configured word.
        words = list(device.wake_words) if device is not None else []
        keywords = _keywords_path(current)
        lines = 0
        text = ""
        if keywords is not None and keywords.is_file():
            try:
                text = keywords.read_text(encoding="utf-8")
            except OSError:
                text = ""
            lines = len([line for line in text.splitlines() if line.strip()])
        words_in_file = bool(text) and all(f"@{word}" in text for word in words) if words else False
        wake_done = bool(words and keywords and lines > 0 and words_in_file)
        wake_status = "done" if wake_done else "pending"
        if wake_done and plane_error:
            wake_status = "error"
        wake_step = {
            "id": "wake", "status": wake_status,
            "evidence": {"words": words, "path": str(keywords) if keywords else "",
                         "exists": bool(keywords and keywords.is_file()), "lines": lines,
                         "plane_error": plane_error},
            "detail": {"keywords_file": str(keywords) if keywords else ""},
            "actions": ["wake.set"],
        }

        # connect: inbound only, and "connected" is the live plane, not a claim.
        outbound = str(manifest.transport) == "outbound"
        connected = bool(plane.get("speaker_connected"))
        connect_step = {
            "id": "connect",
            "status": "skipped" if outbound else ("done" if connected else "pending"),
            "evidence": {"transport": str(manifest.transport), "speaker_connected": connected,
                         "listen_port": manifest.runtime.listen_port if manifest.runtime else None,
                         "token_env": manifest.runtime.token_env if manifest.runtime else ""},
            "detail": {"plane_running": bool(plane.get("running")),
                       "plane_error": str(plane.get("error") or "")},
            "actions": [] if outbound else ["connect.inspect"],
        }

        # test: a stored probe result, or the live inbound link as evidence.
        stored_test = saved.get("test", {}).get("device", {}).get("test", {})
        if not isinstance(stored_test, dict):
            stored_test = {}
        test_done = bool(stored_test.get("ok")) or connected
        test_step = {
            "id": "test", "status": "done" if test_done else "pending",
            "evidence": {"stored": bool(stored_test.get("ok")),
                         "at": stored_test.get("at") or "",
                         "speaker_connected": connected},
            "detail": {"checks": stored_test.get("checks") or []},
            "actions": ["device.test"],
        }

        # verify: a turn after setup started, or a persisted manual confirmation.
        manual_verify = saved.get("verify", {}).get("manual", {})
        if not isinstance(manual_verify, dict):
            manual_verify = {}
        turn = _turn_after_setup(started)
        verified = bool(manual_verify.get("confirm")) or turn is not None
        verify_step = {
            "id": "verify", "status": "done" if verified else "pending",
            "evidence": {"turn": turn is not None,
                         "manual": bool(manual_verify.get("confirm")),
                         "audio_bytes": int(turn.get("audio_bytes") or 0) if turn else 0,
                         "started_at": started},
            "detail": {"optional": True},
            "actions": ["try.run", "manual.confirm"],
        }

        steps = [keys_step, models_step, prepare_step, params_step,
                 wake_step, connect_step, test_step, verify_step]
        required_done = all(
            step["status"] in ("done", "skipped")
            for step in steps if step["id"] not in setup_store.OPTIONAL_STEPS
        )
        next_step = next(
            (step["id"] for step in steps
             if step["id"] not in setup_store.OPTIONAL_STEPS
             and step["status"] not in ("done", "skipped")),
            None,
        )
        blocked = None
        if plug_error:
            blocked = {"code": plug_error,
                       "message": f"plug {plugin.id!r} is not usable in this build"}
        return {
            "plug": _plug_summary(plugin, source, plug_error),
            "steps": steps,
            "next_step": next_step,
            "complete": required_done,
            "verified": verified,
            "blocked": blocked,
            "plane_error": plane_error,
        }

    @app.get("/api/v1/plugs", dependencies=[Depends(auth)])
    async def plugs_list() -> dict:
        """Built-in plus optional user plugs; a broken manifest is data, not a 500."""
        entries, failed = _plugin_catalog()
        plugs = [_plug_summary(plugin, source, error)
                 for plugin, source, error in entries.values()]
        return {"plugs": [*plugs, *failed]}

    @app.get("/api/v1/plugs/{plugin_id}", dependencies=[Depends(auth)])
    async def plug_detail(plugin_id: str) -> dict:
        """One plug, including every validated device manifest."""
        entries, _failed = _plugin_catalog()
        found = entries.get(plugin_id)
        if found is None:
            raise _setup_error(
                404, "unknown_plug", f"no such plug: {plugin_id}",
                {"plug_id": plugin_id, "available": sorted(entries)},
            )
        plugin, source, error = found
        devices = [device.model_dump(mode="json", by_alias=True) for device in plugin.devices]
        manifest = plugin.model_dump(mode="json", by_alias=True)
        manifest.pop("source_dir", None)
        manifest["devices"] = devices
        resolved = {
            "source": source,
            "devices": [
                {
                    "id": device.id,
                    "adapter": device.id,
                    "transport": str(device.transport),
                    "runtime_mode": str(device.runtime.mode) if device.runtime else None,
                    "listen_port": device.runtime.listen_port if device.runtime else None,
                    "token_env": device.runtime.token_env if device.runtime else "",
                }
                for device in plugin.devices
            ],
        }
        return {"manifest": manifest, "devices": devices, "available": not error,
                "error": error or None, "resolved": resolved}

    @app.get("/api/v1/setup/step", dependencies=[Depends(auth)])
    async def setup_step_state(plug: str | None = None) -> dict:
        """Server-computed eight-step state; never trusts a client claim or cookie."""
        return _setup_state(plug)

    def _load_disk_config() -> Config:
        if config_path is None or not Path(config_path).exists():
            raise _setup_error(409, "no_config", "there is no config file on disk to write")
        try:
            return load_config(Path(config_path))
        except ConfigError as exc:
            raise _setup_error(409, "no_config", f"config is not readable: {exc}") from exc

    def _device_by_id(current: Config, device_id: object) -> DeviceConfig:
        wanted = str(device_id or "").strip()
        if wanted:
            for device in current.devices:
                if device.id == wanted:
                    return device
            raise _setup_error(404, "unknown_device", f"no such device: {wanted}")
        device = _target_device(current)
        if device is None:
            raise _setup_error(409, "unknown_device", "add a speaker first")
        return device

    def _coerce_params(values: dict) -> dict[str, object]:
        """Validate the loose ``params`` value dict; secret values are never stored."""
        if not isinstance(values, dict):
            raise _setup_error(422, "invalid_values", "params must be an object")
        cleaned: dict[str, object] = {}
        for key, value in values.items():
            name = str(key)
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise _setup_error(422, "invalid_values", f"invalid param key: {name!r}")
            if name == "plug_id":
                continue
            if value is None or isinstance(value, (bool, int, float, str)):
                cleaned[name] = value
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                cleaned[name] = value
                continue
            raise _setup_error(422, "invalid_values",
                               f"param {name!r} must be a string, number, bool or string list")
        return cleaned

    @app.post("/api/v1/setup/step", dependencies=[Depends(auth)])
    async def setup_step(payload: Annotated[dict, Body()]) -> dict:
        """Run one allowlisted wizard action. Unknown step/action is a 400 object."""
        step = str(payload.get("step") or "").strip()
        action = str(payload.get("action") or "").strip()
        requested_plug = str(payload.get("plug") or "").strip() or None
        values = payload.get("payload", payload.get("values", {}))
        if step not in setup_store.STEPS:
            raise _setup_error(400, "unknown_step", f"no such setup step: {step!r}",
                               {"steps": list(setup_store.STEPS)})
        allowed = _SETUP_ACTIONS.get(step, set())
        if action not in allowed:
            raise _setup_error(400, "unknown_action",
                               f"action {action!r} is not allowed for step {step!r}",
                               {"step": step, "allowed": sorted(allowed)})
        if not isinstance(values, dict):
            raise _setup_error(422, "invalid_values", "payload must be an object")
        lock = app.state.setup_locks[step]
        if lock.locked():
            raise _setup_error(409, "step_in_progress",
                               f"step {step!r} is already running in another request")
        async with lock:
            current = _current()
            data_dir = current.system.data_dir
            if action == "keys.save":
                raw = values.get("values", values)
                if not isinstance(raw, dict) or not raw:
                    raise _setup_error(422, "invalid_values",
                                       "values must be a non-empty object")
                changed = False
                for name, value in raw.items():
                    if not isinstance(value, str):
                        raise _setup_error(422, "invalid_values",
                                           f"secret {name!r} must be a string")
                    existing = secrets.resolve(str(name))
                    if existing is not None and existing.value == value:
                        continue
                    if not value and existing is None:
                        continue
                    try:
                        secrets.put(str(name), value)
                    except ValueError as exc:
                        raise _setup_error(400, "secret_rejected", str(exc)) from exc
                    changed = True
                if changed:
                    setup_store.update(
                        data_dir,
                        lambda data: setup_store.set_value(
                            data, ("keys", "last_saved_at"), setup_store.iso_now()),
                    )
            elif action == "manual.confirm":
                plugin, _source, _error = _select_plugin(current, requested_plug)
                device = _target_device(current, plugin)
                manifest = _device_manifest(plugin, device)
                manual_values = values.get("manual")
                if not isinstance(manual_values, dict):
                    manual_values = {}
                confirm = values.get(
                    "confirmed", values.get("confirm", manual_values.get("confirm", True)))
                if not isinstance(confirm, bool):
                    raise _setup_error(422, "invalid_values", "confirmed must be a boolean")
                rollback = values.get(
                    "rollback_ack", manual_values.get("rollback_ack", False))
                if not isinstance(rollback, bool):
                    raise _setup_error(422, "invalid_values",
                                       "rollback_ack must be a boolean")
                if (step == "prepare" and str(manifest.device_class) == "B"
                        and confirm and not rollback):
                    raise _setup_error(422, "invalid_values",
                                       "B-class plugs require rollback_ack=true")
                branch = "prepare" if step == "prepare" else "verify"

                def confirm_state(data: dict, *, branch: str = branch,
                                  confirm: bool = confirm, rollback: bool = rollback) -> bool:
                    changed = setup_store.set_value(
                        data, (branch, "manual", "confirm"), confirm)
                    if changed:
                        setup_store.set_value(
                            data, (branch, "manual", "at"), setup_store.iso_now())
                    if branch == "prepare" and setup_store.set_value(
                            data, (branch, "manual", "rollback_ack"), rollback):
                        changed = True
                    return changed

                setup_store.update(data_dir, confirm_state)
            elif action == "device.upsert":
                current = _load_disk_config()
                plugin, _source, _error = _select_plugin(current, requested_plug)
                manifest = _device_manifest(plugin, None)
                existing = None
                wanted = str(values.get("id") or "").strip().lower()
                if not wanted and current.devices:
                    wanted = current.devices[0].id
                wanted = wanted or "living"
                for device in current.devices:
                    if device.id == wanted:
                        existing = device
                        break
                base = existing.model_dump(mode="json") if existing is not None else {}
                try:
                    host = host_value(values["host"] if "host" in values else base.get("host"))
                    room = (room_value(values.get("room")) if "room" in values
                            else str(base.get("room") or ""))
                    words = (wake_words_value(values) if "wake_words" in values
                             else list(base.get("wake_words") or ["你好小瑞"]))
                except HTTPException as exc:
                    raise _object_error(exc) from exc
                params = dict(base.get("params") or {})
                submitted = _coerce_params(values.get("params") or {})
                # A manifest-declared secret is write-only: it goes to secrets.yaml and
                # never into the device's params dict or an API response.
                secret_keys = {
                    spec.key for spec in manifest.params if str(spec.type) == "secret"}
                for key in list(submitted):
                    if key not in secret_keys:
                        continue
                    value = submitted.pop(key)
                    if not isinstance(value, str):
                        raise _setup_error(422, "invalid_values",
                                           f"secret param {key!r} must be a string")
                    try:
                        secrets.put(key.upper(), value)
                    except ValueError as exc:
                        raise _setup_error(400, "secret_rejected", str(exc)) from exc
                params.update(submitted)
                params["plug_id"] = plugin.id
                adapter = str(
                    values.get("adapter") or base.get("adapter") or manifest.id
                ).strip() or manifest.id
                base.update({
                    "id": wanted, "adapter": adapter, "host": host, "room": room,
                    "wake_words": words or ["你好小瑞"], "enabled": True, "params": params,
                })
                try:
                    device = DeviceConfig.model_validate(base)
                except ValidationError as exc:
                    raise _setup_error(422, "invalid_values", "invalid device fields",
                                       {"errors": [
                                           {"loc": list(error.get("loc", ())),
                                            "msg": str(error.get("msg", ""))}
                                           for error in exc.errors(include_url=False)
                                       ]}) from exc
                if existing is None or (
                        existing.model_dump(mode="json") != device.model_dump(mode="json")):
                    others = [item for item in current.devices if item.id != device.id]
                    merged = current.model_copy(update={"devices": [device, *others]})
                    try:
                        updated = Config.model_validate(merged.model_dump(mode="json"))
                    except ValidationError as exc:
                        raise _setup_error(422, "invalid_values", "invalid config",
                                           {"errors": exc.errors(include_url=False)}) from exc
                    await write_devices(updated)
            elif action == "wake.set":
                current = _load_disk_config()
                plugin, _source, _error = _select_plugin(current, requested_plug)
                device = _device_by_id(current, values.get("device_id"))
                try:
                    words = wake_words_value(values)
                except HTTPException as exc:
                    raise _object_error(exc) from exc
                if not words:
                    raise _setup_error(422, "invalid_values", "wake_words is required")
                keywords = _keywords_path(current)
                file_words = False
                if keywords is not None and keywords.is_file():
                    try:
                        text = keywords.read_text(encoding="utf-8")
                    except OSError:
                        text = ""
                    file_words = all(f"@{word}" in text for word in words)
                if list(device.wake_words) != words or not file_words:
                    changed_device = device.model_copy(update={"wake_words": words})
                    updated = current.model_copy(update={"devices": [
                        changed_device if item.id == device.id else item
                        for item in current.devices
                    ]})
                    await write_devices(updated)
            elif action == "connect.inspect":
                pass                      # read-only: the state below is the answer
            elif action == "device.test":
                current = _load_disk_config()
                plugin, _source, _error = _select_plugin(current, requested_plug)
                device = _device_by_id(current, values.get("device_id"))
                manifest = _device_manifest(plugin, device)
                plane = plane_snapshot(app)
                probe = await probe_host(device.host)
                # The stored probe is evidence in its own right; the live plane is the
                # stronger signal for an inbound speaker (design §3.2).
                ok = bool(probe.get("usable")) or bool(plane.get("speaker_connected"))
                checks = [
                    {"id": "plane", "ok": bool(plane.get("speaker_connected")),
                     "detail": {"running": bool(plane.get("running")),
                                "error": str(plane.get("error") or "")}},
                    {"id": "host", "ok": bool(probe.get("usable")),
                     "detail": {"ssh": probe.get("ssh") or {},
                                "hint": probe.get("hint") or ""}},
                ]
                entry = {"ok": ok, "at": setup_store.iso_now(), "device_id": device.id,
                         "checks": checks}
                setup_store.update(
                    data_dir,
                    lambda data, entry=entry: setup_store.set_value(
                        data, ("test", "device", "test"), entry),
                )
            elif action == "try.run":
                text = str(values.get("text") or "").strip()
                if not text:
                    raise _setup_error(422, "invalid_values", "text is required")
                try:
                    result = await run_debug_say(text)
                except HTTPException as exc:
                    message = str(exc.detail)
                    code = ("turn_in_progress" if "already running" in message
                            else "audio_plane_unavailable")
                    raise _setup_error(exc.status_code, code, message) from exc
                audio = int(result.get("audio_bytes") or 0)
                if audio > 0:
                    def first_answer(data: dict) -> bool:
                        changed = setup_store.set_value(
                            data, ("verify", "manual", "confirm"), True)
                        if changed:
                            setup_store.set_value(
                                data, ("verify", "manual", "at"), setup_store.iso_now())
                        setup_store.set_value(
                            data, ("verify", "first_answer", "audio_bytes"), audio)
                        return changed

                    setup_store.update(data_dir, first_answer)
            return _setup_state(requested_plug)

