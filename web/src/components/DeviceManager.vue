<script setup lang="ts">
/**
 * P5: device administration (the wizard's "add a speaker" plus the day-after edits).
 *
 * Editing or deleting here writes config.yaml and restarts the audio plane (the
 * plane reads the device list once at startup). Fields the form does not show -
 * the LED colours, the volume target - are preserved by the backend, so a room
 * rename cannot wipe the ring configuration.
 */
import { onMounted, ref } from 'vue'
import { api, ApiError, type DeviceDetail } from '../api'

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
const newDevice = ref({ id: 'living', host: '', room: '', wake_words: '你好小瑞' })

async function load() {
  try {
    const list = await api.devices()
    devices.value = list as unknown as typeof devices.value
    if (selected.value) {
      await select(selected.value.device.id)
    } else if (devices.value.length) {
      await select(devices.value[0].id)     // one device is the common case
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
    notice.value = '已保存；音频面已按新配置重启（看门狗会在失败时继续重试）'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function remove() {
  if (!selected.value) return
  if (!window.confirm(`删除设备 ${selected.value.device.id}？音频面会停止，直到再加一台音箱。`)) return
  busy.value = 'delete'
  try {
    await api.deleteDevice(selected.value.device.id)
    selected.value = null
    await load()
    notice.value = '已删除'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
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
    newDevice.value = { id: 'living', host: '', room: '', wake_words: '你好小瑞' }
    await load()
    notice.value = '设备已添加'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="sub">
    这里改的是 config.yaml 里的 devices[]。保存/删除/添加都会重启音频面（它只在启动时读设备表），
    没在表单里出现的字段（灯环配色、音量目标）会保留。
  </p>

  <div class="card">
    <h2>设备 <button class="right" :disabled="busy !== ''" @click="adding = !adding">
      {{ adding ? '收起' : '添加音箱' }}</button></h2>
    <div v-if="!devices.length" class="empty">还没有音箱。点「添加音箱」开始。</div>
    <div v-for="device in devices" :key="device.id" class="row clickable"
         :class="{ active: selected?.device.id === device.id }" @click="select(device.id)">
      <span class="v">{{ device.id }}</span>
      <span class="k" style="width: auto">{{ device.room }} · {{ device.host }} · {{ device.status }}</span>
    </div>
    <template v-if="adding">
      <div class="field"><label>地址</label><input v-model="newDevice.host" placeholder="<LAN-IP>" /></div>
      <div class="field"><label>房间</label><input v-model="newDevice.room" placeholder="客厅" /></div>
      <div class="field"><label>唤醒词</label><input v-model="newDevice.wake_words" placeholder="你好小瑞" /></div>
      <div class="actions">
        <button class="primary" :disabled="busy !== '' || !newDevice.host.trim()" @click="add">
          {{ busy === 'add' ? '…' : '保存并重启音频面' }}</button>
      </div>
    </template>
  </div>

  <div v-if="selected" class="card">
    <h2>{{ selected.device.id }} 的设置</h2>
    <div class="live">
      实时：{{ selected.live.status || 'adapter 不可用' }}
      <span v-if="selected.live.model">· {{ selected.live.model }}</span>
      <span v-if="selected.live.capabilities?.length">· {{ selected.live.capabilities.join(', ') }}</span>
    </div>
    <div class="field"><label>地址</label><input v-model="draft.host" /></div>
    <div class="field"><label>房间</label><input v-model="draft.room" /></div>
    <div class="field"><label>唤醒词（逗号分隔）</label><input v-model="draft.wake_words" maxlength="40" /></div>
    <div class="field"><label>音量目标（0 = 不接管）</label>
      <input v-model.number="draft.volume" type="number" min="0" max="255" /></div>
    <div class="field"><label>监听端口（留空 = adapter 默认）</label>
      <input v-model="draft.listen_port" type="number" min="1" max="65535" /></div>
    <div class="field"><label>启用</label>
      <input v-model="draft.enabled" type="checkbox" class="check" /></div>
    <div class="actions">
      <button :disabled="busy !== ''" @click="runProbe">{{ busy === 'probe' ? '探测中…' : '探测 SSH' }}</button>
      <button class="primary" :disabled="busy !== ''" @click="save">
        {{ busy === 'save' ? '保存中…' : '保存' }}</button>
      <button class="danger" :disabled="busy !== ''" @click="remove">
        {{ busy === 'delete' ? '…' : '删除设备' }}</button>
    </div>
    <p v-if="probe" :class="probe.usable ? 'ok' : 'fail'">
      {{ probe.usable ? `可达：SSH banner 正常（${probe.ssh.ms ?? '?'} ms）` : probe.hint }}
    </p>
  </div>

  <div v-if="notice" class="card"><p class="ok">{{ notice }}</p></div>
  <div v-if="error" class="card"><p class="fail">{{ error }}</p></div>
</template>

<style scoped>
.field { display: flex; gap: 12px; align-items: center; margin-bottom: 8px; }
.field label { width: 200px; color: var(--muted); font-size: 13px; flex: none; }
.field input {
  font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1; max-width: 420px;
}
.field input.check { flex: none; width: 18px; height: 18px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
button.right { float: right; font-size: 12px; padding: 3px 10px; }
button.danger { color: var(--fail); border-color: #f3c6c6; }
.clickable { cursor: pointer; }
.clickable.active { background: #f6f8fc; }
.live { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
</style>
