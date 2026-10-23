<script setup lang="ts">
/**
 * The install wizard (C-UI P4).
 *
 * This is the page a stranger sees after `install.sh`: keys, models, the speaker,
 * the wake word, one spoken try. Everything here calls a real endpoint - the
 * checklist is computed by the server from keys/files, never from a cookie saying
 * "welcome". It is a hand-written view on purpose: the panels render data, this
 * renders a sequence, which is exactly what the PanelSpec escape hatch is for.
 */
import { onMounted, ref } from 'vue'
import { api, ApiError, type SetupState } from '../api'

const state = ref<SetupState | null>(null)
const error = ref('')
const busy = ref('')

const keys = ref({ HOUSE_EAR_LLM_KEY: '', HOUSE_EAR_ASR_KEY: '', HOUSE_EAR_TTS_KEY: '' })
const optionalKeys = ref({ XFYUN_APPID: '', XFYUN_API_KEY: '', XFYUN_API_SECRET: '' })
const showOptional = ref(false)

const host = ref('')
const room = ref('客厅')
const wakeWord = ref('你好小瑞')
const probe = ref<{ usable: boolean; hint: string; ssh: { open: boolean; ms?: number; error?: string } } | null>(null)

const sayText = ref('现在几点？')
const said = ref('')
const sayError = ref('')

async function load() {
  try {
    state.value = await api.setup()
    if (state.value.device) {
      host.value = state.value.device.host
      room.value = state.value.device.room || room.value
      wakeWord.value = state.value.device.wake_words[0] || wakeWord.value
    }
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  }
}

async function run(label: string, action: () => Promise<SetupState>) {
  busy.value = label
  error.value = ''
  try {
    state.value = await action()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

function saveKeys() {
  const values: Record<string, string> = {}
  for (const [name, value] of Object.entries({ ...keys.value, ...optionalKeys.value })) {
    if (value.trim()) values[name] = value.trim()
  }
  if (!Object.keys(values).length) return
  void run('keys', async () => {
    const next = await api.setupKeys(values)
    keys.value = { HOUSE_EAR_LLM_KEY: '', HOUSE_EAR_ASR_KEY: '', HOUSE_EAR_TTS_KEY: '' }
    optionalKeys.value = { XFYUN_APPID: '', XFYUN_API_KEY: '', XFYUN_API_SECRET: '' }
    return next
  })
}

async function probeHost() {
  busy.value = 'probe'
  probe.value = null
  try {
    probe.value = await api.setupProbe(host.value.trim())
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

function saveDevice() {
  void run('device', () => api.setupDevice({
    host: host.value.trim(), room: room.value.trim(), wake_words: [wakeWord.value.trim()],
  }))
}

function saveWake() {
  void run('wake', () => api.setupWake([wakeWord.value.trim()]))
}

async function tryOne() {
  busy.value = 'say'
  said.value = ''
  sayError.value = ''
  try {
    const result = await api.say(sayText.value.trim())
    said.value = result.reply || '(空回复)'
    if (result.error) sayError.value = result.error
    await load()
  } catch (exc) {
    sayError.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

function done(id: string): boolean {
  return state.value?.steps.find((step) => step.id === id)?.done ?? false
}

onMounted(load)
</script>

<template>
  <h1>安装向导</h1>
  <p class="sub">
    一次装完就能对话：填 key、下载模型、加音箱、定唤醒词、说一句。每一步都是真调用，面板上的状态来自服务器上的文件。
  </p>
  <div v-if="error" class="card"><p class="fail">{{ error }}</p></div>

  <div v-if="state" class="card">
    <h2>进度 <span class="sub" style="margin: 0">{{ state.next_step === 'done' ? '全部完成' : '下一步：' + state.next_step }}</span></h2>
    <div v-for="step in state.steps" :key="step.id" class="row">
      <span :class="step.done ? 'ok' : 'warn'" style="width: 24px">{{ step.done ? '✓' : '○' }}</span>
      <span class="v">{{ step.title }}</span>
      <span class="k" style="width: auto">
        <template v-if="step.id === 'device' && state.device">{{ state.device.host }} · {{ state.device.room }}</template>
        <template v-else-if="step.id === 'wake' && state.keywords_exists">{{ state.device?.wake_words.join(' / ') }}</template>
        <template v-else-if="step.id === 'models' && !step.done">缺：{{ Object.entries(step.detail).filter(([, v]) => !v).map(([k]) => k).join(', ') }}</template>
      </span>
    </div>
  </div>

  <div class="card">
    <h2>1 · 三个 key（只写不读）</h2>
    <div class="field"><label>LLM（DeepSeek）</label>
      <input v-model="keys.HOUSE_EAR_LLM_KEY" type="password" placeholder="sk-…" autocomplete="off" /></div>
    <div class="field"><label>ASR 备用（SiliconFlow）</label>
      <input v-model="keys.HOUSE_EAR_ASR_KEY" type="password" placeholder="sk-…" autocomplete="off" /></div>
    <div class="field"><label>TTS 备用（SiliconFlow）</label>
      <input v-model="keys.HOUSE_EAR_TTS_KEY" type="password" placeholder="sk-…" autocomplete="off" /></div>
    <button class="link" @click="showOptional = !showOptional">
      {{ showOptional ? '收起' : '讯飞语音（可选，主用 TTS）' }}
    </button>
    <template v-if="showOptional">
      <div class="field"><label>XFYUN_APPID</label><input v-model="optionalKeys.XFYUN_APPID" autocomplete="off" /></div>
      <div class="field"><label>XFYUN_API_KEY</label><input v-model="optionalKeys.XFYUN_API_KEY" type="password" autocomplete="off" /></div>
      <div class="field"><label>XFYUN_API_SECRET</label><input v-model="optionalKeys.XFYUN_API_SECRET" type="password" autocomplete="off" /></div>
    </template>
    <button :disabled="busy !== ''" @click="saveKeys">{{ busy === 'keys' ? '…' : '保存 key' }}</button>
    <p class="note">值写进 data 卷的 secrets.yaml（0600），config.yaml 里只有变量名；任何页面都读不回来。</p>
  </div>

  <div class="card">
    <h2>2 · 本地模型 {{ done('models') ? '（已就绪）' : '' }}</h2>
    <p v-if="done('models')" class="ok">ASR / VAD / 唤醒模型都在。</p>
    <template v-else>
      <p class="sub" style="margin-bottom: 8px">在 NAS 上跑一次（约 272 MB，只下一次）：</p>
      <pre class="cmd">docker compose exec house-ear {{ state?.models_command || 'house-ear fetch-models' }}</pre>
      <button :disabled="busy !== ''" @click="load">跑完了，刷新</button>
    </template>
  </div>

  <div class="card">
    <h2>3 · 添加音箱</h2>
    <div class="field"><label>音箱地址</label>
      <input v-model="host" placeholder="<LAN-IP> 或 speaker.local" /></div>
    <div class="field"><label>房间</label><input v-model="room" placeholder="客厅" /></div>
    <div class="field"><label>唤醒词</label><input v-model="wakeWord" placeholder="你好小瑞" /></div>
    <div class="actions">
      <button :disabled="busy !== '' || !host.trim()" @click="probeHost">{{ busy === 'probe' ? '探测中…' : '探测' }}</button>
      <button class="primary" :disabled="busy !== '' || !host.trim()" @click="saveDevice">{{ busy === 'device' ? '保存中…' : '保存音箱' }}</button>
      <button :disabled="busy !== '' || !state?.device" @click="saveWake">{{ busy === 'wake' ? '…' : '只改唤醒词' }}</button>
    </div>
    <p v-if="probe" :class="probe.usable ? 'ok' : 'fail'">
      {{ probe.usable ? `可达（SSH ${probe.ssh.ms ?? '?'} ms）` : probe.hint }}
    </p>
    <p class="note">
      保存后服务会重启音频面并重新生成 keywords.txt（{{ state?.keywords_file || 'config 同目录' }}），
      音频面状态：<span :class="state?.audio_plane ? 'ok' : 'warn'">{{ state?.audio_plane ? '在线' : '离线（看日志）' }}</span>
    </p>
  </div>

  <div class="card">
    <h2>4 · 试一句</h2>
    <div class="say-bar">
      <input v-model="sayText" placeholder="现在几点？" :disabled="busy !== ''" @keyup.enter="tryOne" />
      <button :disabled="busy !== '' || !sayText.trim()" @click="tryOne">{{ busy === 'say' ? '…' : '说一句' }}</button>
    </div>
    <div v-if="said" class="said">{{ said }}</div>
    <div v-if="sayError" class="note fail">{{ sayError }}</div>
    <p class="note">这句话走的是真实的 ASR 之后全链路：同一套工具、TTS 和播放。音箱应该真的出声。</p>
  </div>

  <div v-if="state?.next_step === 'done'" class="card">
    <h2>装好了</h2>
    <p>打开 <RouterLink to="/panel/overview">概览</RouterLink>，或者直接对音箱说「{{ state.device?.wake_words[0] || '你好小瑞' }}」。</p>
  </div>
</template>

<style scoped>
.field { display: flex; gap: 12px; align-items: center; margin-bottom: 8px; }
.field label { width: 200px; color: var(--muted); font-size: 13px; flex: none; }
.field input {
  font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1; max-width: 420px;
}
button.link {
  background: none; border: 0; color: var(--muted); font-size: 12px; padding: 2px 0;
  text-align: left; cursor: pointer; text-decoration: underline;
}
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
.cmd {
  background: #f7f8fa; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; overflow-x: auto; margin: 0 0 10px;
}
.say-bar { display: flex; gap: 8px; }
.say-bar input {
  font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1;
}
.said {
  margin-top: 10px; padding: 10px 12px; background: #f7f8fa; border: 1px solid var(--line);
  border-radius: 8px; font-size: 13px; white-space: pre-wrap;
}
</style>
