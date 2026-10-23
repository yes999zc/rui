<script setup lang="ts">
/**
 * P5: device administration (the wizard's "add a speaker" plus the day-after edits).
 *
 * Editing or deleting here writes config.yaml and restarts the audio plane (the
 * plane reads the device list once at startup). Fields the form does not show —
 * the LED colours, the volume target — are preserved by the backend, so a room
 * rename cannot wipe the ring configuration.
 */
import { computed, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { api, ApiError, type DeviceDetail } from '../api'
import { t } from '../i18n'

const devices = ref<{ id: string; adapter: string; room: string; host: string; status: string }[]>([])
const selected = ref<DeviceDetail | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref('')
const probe = ref<{ usable: boolean; hint: string; ssh: { port_open: boolean; banner: boolean; ms?: number } } | null>(null)

const draft = ref({
  host: '', room: '', wake_words: '', volume: 0, listen_port: '', enabled: true,
})
const adding = ref(false)
const newDevice = ref({ host: '', room: '', wake_words: '你好小瑞' })

const liveText = computed(() => {
  const live = selected.value?.live
  if (!live) return t('devices.noAdapter')
  const parts = [live.status ? statusLabel(live.status) : t('devices.noAdapter'), live.model, live.transport]
  return parts.filter(Boolean).join(' · ')
})

function statusLabel(status: string): string {
  if (status === 'ready') return t('backend.online')
  if (status === 'unavailable') return t('backend.offline')
  return status
}

async function load() {
  try {
    const list = await api.devices()
    devices.value = list as unknown as typeof devices.value
    if (selected.value) {
      await select(selected.value.device.id)
    } else if (devices.value.length) {
      await select(devices.value[0].id) // one device is the common case
    }
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  }
}

async function select(id: string) {
  busy.value = 'load'
  try {
    const detail = await api.deviceDetail(id)
    selected.value = detail
    draft.value = {
      host: detail.device.host,
      room: detail.device.room,
      wake_words: detail.device.wake_words.join('，'),
      volume: Number(detail.device.volume ?? 0),
      listen_port: detail.device.listen_port == null ? '' : String(detail.device.listen_port),
      enabled: Boolean(detail.device.enabled),
    }
    probe.value = null
    notice.value = ''
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

function words(text: string): string[] {
  return text.split(/[,，\s]+/).map((word) => word.trim()).filter(Boolean)
}

async function save() {
  if (!selected.value) return
  busy.value = 'save'
  error.value = ''
  notice.value = ''
  try {
    const patch: Record<string, unknown> = {
      host: draft.value.host.trim(),
      room: draft.value.room.trim(),
      wake_words: words(draft.value.wake_words),
      volume: Number(draft.value.volume),
      enabled: draft.value.enabled,
    }
    if (draft.value.listen_port.trim()) patch.listen_port = Number(draft.value.listen_port)
    selected.value = await api.updateDevice(selected.value.device.id, patch)
    await load()
    notice.value = t('devices.saved')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

function remove() {
  if (!selected.value) return
  if (!window.confirm(t('devices.confirmDelete', { id: selected.value.device.id }))) return
  busy.value = 'delete'
  void (async () => {
    try {
      await api.deleteDevice(selected.value!.device.id)
      selected.value = null
      await load()
      notice.value = t('devices.deleted')
    } catch (exc) {
      error.value = exc instanceof ApiError ? exc.message : String(exc)
    } finally {
      busy.value = ''
    }
  })()
}

async function runProbe() {
  busy.value = 'probe'
  probe.value = null
  try {
    probe.value = await api.probeDevice(draft.value.host.trim())
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function add() {
  busy.value = 'add'
  error.value = ''
  try {
    await api.setupDevice({
      host: newDevice.value.host.trim(),
      room: newDevice.value.room.trim(),
      wake_words: words(newDevice.value.wake_words),
    })
    adding.value = false
    newDevice.value = { host: '', room: '', wake_words: '你好小瑞' }
    await load()
    notice.value = t('devices.added')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="lede mgr-intro">{{ t('devices.lede') }}</p>

  <section class="block">
    <div class="block-head">
      <h2>{{ t('devices.list') }}</h2>
      <button type="button" class="btn btn-sm" :disabled="busy !== ''" @click="adding = !adding">
        <AppIcon :name="adding ? 'close' : 'plus'" :size="13" />
        {{ adding ? t('devices.addCollapse') : t('devices.add') }}
      </button>
    </div>

    <div v-if="!devices.length" class="empty">{{ t('devices.empty') }}</div>
    <div
      v-for="device in devices"
      :key="device.id"
      class="device-row"
      :class="{ active: selected?.device.id === device.id }"
      role="button"
      tabindex="0"
      :aria-pressed="selected?.device.id === device.id"
      @click="select(device.id)"
      @keydown.enter.prevent="select(device.id)"
      @keydown.space.prevent="select(device.id)"
    >
      <span class="device-id mono">{{ device.id }}</span>
      <span class="device-room">{{ device.room }}</span>
      <span class="device-meta mono">{{ device.host }} · {{ device.adapter }} · {{ statusLabel(device.status) }}</span>
      <AppIcon name="arrowRight" :size="14" class="device-arrow" />
    </div>

    <div v-if="adding" class="surface add-device">
      <div class="field-row">
        <div class="field-meta"><label class="field-label" for="add-host">{{ t('devices.host') }}</label></div>
        <div class="field-control"><input id="add-host" v-model="newDevice.host" placeholder="<LAN-IP>" /></div>
      </div>
      <div class="field-row">
        <div class="field-meta"><label class="field-label" for="add-room">{{ t('devices.room') }}</label></div>
        <div class="field-control"><input id="add-room" v-model="newDevice.room" :placeholder="t('setup.device.roomPlaceholder')" /></div>
      </div>
      <div class="field-row">
        <div class="field-meta"><label class="field-label" for="add-wake">{{ t('devices.wakeWords') }}</label></div>
        <div class="field-control"><input id="add-wake" v-model="newDevice.wake_words" :placeholder="t('setup.device.wakePlaceholder')" /></div>
      </div>
      <div class="actions">
        <button type="button" class="btn btn-primary" :disabled="busy !== '' || !newDevice.host.trim()" @click="add">
          {{ busy === 'add' ? t('devices.adding') : t('devices.addSubmit') }}
        </button>
      </div>
    </div>
  </section>

  <section v-if="selected" class="block">
    <div class="block-head">
      <h2>{{ t('devices.settingsTitle', { id: selected.device.id }) }}</h2>
      <span class="meta">{{ t('devices.live', { status: liveText }) }}</span>
    </div>

    <div class="field-row">
      <div class="field-meta"><label class="field-label" for="dev-host">{{ t('devices.host') }}</label></div>
      <div class="field-control"><input id="dev-host" v-model="draft.host" /></div>
    </div>
    <div class="field-row">
      <div class="field-meta"><label class="field-label" for="dev-room">{{ t('devices.room') }}</label></div>
      <div class="field-control"><input id="dev-room" v-model="draft.room" /></div>
    </div>
    <div class="field-row">
      <div class="field-meta"><label class="field-label" for="dev-wake">{{ t('devices.wakeWords') }}</label></div>
      <div class="field-control"><input id="dev-wake" v-model="draft.wake_words" maxlength="40" /></div>
    </div>
    <div class="field-row">
      <div class="field-meta">
        <label class="field-label" for="dev-volume">{{ t('devices.volumeTarget') }}</label>
        <p class="field-hint">{{ t('devices.volumeHint') }}</p>
      </div>
      <div class="field-control"><input id="dev-volume" v-model.number="draft.volume" type="number" min="0" max="255" /></div>
    </div>
    <div class="field-row">
      <div class="field-meta"><label class="field-label" for="dev-port">{{ t('devices.listenPort') }}</label></div>
      <div class="field-control"><input id="dev-port" v-model="draft.listen_port" type="number" min="1" max="65535" /></div>
    </div>
    <div class="field-row">
      <div class="field-meta"><label class="field-label" for="dev-enabled">{{ t('devices.enabled') }}</label></div>
      <div class="field-control">
        <label class="switch">
          <input id="dev-enabled" v-model="draft.enabled" type="checkbox" />
          <span class="track" />
          <span class="switch-text">{{ draft.enabled ? t('common.on') : t('common.off') }}</span>
        </label>
      </div>
    </div>

    <div class="actions" style="margin-top: var(--s4)">
      <button type="button" class="btn" :disabled="busy !== ''" @click="runProbe">
        {{ busy === 'probe' ? t('devices.probing') : t('devices.probe') }}
      </button>
      <button type="button" class="btn btn-primary" :disabled="busy !== ''" @click="save">
        {{ busy === 'save' ? t('devices.saving') : t('devices.save') }}
      </button>
      <button type="button" class="btn btn-danger" :disabled="busy !== ''" @click="remove">
        <AppIcon name="trash" :size="13" />
        {{ busy === 'delete' ? '…' : t('devices.delete') }}
      </button>
    </div>
    <p v-if="probe" :class="probe.usable ? 'ok' : 'fail'" class="field-hint">
      {{ probe.usable ? t('devices.reachable', { ms: probe.ssh.ms ?? '?' }) : probe.hint }}
    </p>
  </section>

  <div v-if="notice" class="banner banner-ok mgr-result" role="status">
    <AppIcon name="check" :size="16" class="banner-icon" />
    <span>{{ notice }}</span>
  </div>
  <div v-if="error" class="banner banner-fail mgr-result" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ error }}</span>
  </div>
</template>

<style scoped>
.mgr-intro {
  margin-bottom: var(--s5);
}

.device-row {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  padding: 11px 10px;
  border-top: 1px solid var(--line);
  cursor: pointer;
  border-radius: var(--r-xs);
  transition: background var(--dur-fast) var(--ease);
  min-width: 0;
}

.device-row:first-of-type {
  border-top: 1px solid var(--line-strong);
}

.device-row:hover {
  background: var(--surface-2);
}

.device-row.active {
  background: var(--accent-soft);
  box-shadow: inset 2px 0 0 var(--accent);
}

.device-row:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}

.device-id {
  font-size: 12px;
  min-width: 72px;
  flex: none;
}

.device-room {
  font-size: 13.5px;
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.device-meta {
  color: var(--muted);
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.device-arrow {
  color: var(--muted);
  flex: none;
  align-self: center;
}

.add-device {
  margin-top: var(--s3);
}

.mgr-result {
  margin-top: var(--s5);
}

@media (max-width: 720px) {
  .device-row {
    flex-wrap: wrap;
    gap: 1px var(--s2);
  }

  .device-meta {
    flex: 1 1 100%;
  }

  .device-arrow {
    display: none;
  }
}
</style>
