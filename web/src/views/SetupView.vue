<script setup lang="ts">
/**
 * The install wizard (C-UI P4).
 *
 * This is the page a stranger sees after `install.sh`: keys, models, the speaker,
 * the wake word, one spoken try. Everything here calls a real endpoint — the
 * checklist is computed by the server from keys/files, never from a cookie saying
 * "welcome". It is a hand-written view on purpose: the panels render data, this
 * renders a sequence, which is exactly what the PanelSpec escape hatch is for.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { api, ApiError, type CatalogDoc, type SetupState } from '../api'
import { localizeBackend, pickLocalized, providerName, t, type MsgKey } from '../i18n'

const state = ref<SetupState | null>(null)
const error = ref('')
const busy = ref('')

const catalog = ref<CatalogDoc | null>(null)
const keyDrafts = ref<Record<string, string>>({})

type Stage = 'llm' | 'asr' | 'tts'
const STAGES: Stage[] = ['llm', 'asr', 'tts']
const STAGE_LABELS: Record<Stage, MsgKey> = {
  llm: 'setup.keys.stage.llm',
  asr: 'setup.keys.stage.asr',
  tts: 'setup.keys.stage.tts',
}

interface SetupKey {
  name: string
  stages: Stage[]
  providers: string[]
  hasValue: boolean
  hint: string
  missing: boolean
}

const host = ref('')
const room = ref('')
const wakeWord = ref('你好小瑞')
const probe = ref<{ usable: boolean; hint: string; ssh: { port_open: boolean; banner: boolean; ms?: number; error?: string } } | null>(null)

const sayText = ref('')
const said = ref('')
const sayError = ref('')

const STEP_TITLES: Record<string, MsgKey> = {
  keys: 'setup.step.keys',
  models: 'setup.step.models',
  device: 'setup.step.device',
  wake: 'setup.step.wake',
  verify: 'setup.step.verify',
}

function stepTitle(id: string): string {
  const key = STEP_TITLES[id]
  return key ? t(key) : localizeBackend(id)
}

async function load() {
  try {
    const [next, cat] = await Promise.all([
      api.setup(),
      api.catalog().catch(() => null),
    ])
    state.value = next
    catalog.value = cat
    keyDrafts.value = {}
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
  for (const [name, value] of Object.entries(keyDrafts.value)) {
    if (value.trim()) values[name] = value.trim()
  }
  if (!Object.keys(values).length) return
  void run('keys', async () => {
    const next = await api.setupKeys(values)
    keyDrafts.value = {}
    catalog.value = await api.catalog().catch(() => catalog.value)
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
    said.value = result.reply || '—'
    if (result.error) sayError.value = result.error
    await load()
  } catch (exc) {
    const message = exc instanceof ApiError ? exc.message : String(exc)
    // The backend's own sentence is right; say what to do about it in the user's language.
    sayError.value = message.includes('no audio plane') ? t('setup.error.audioPlane') : message
  } finally {
    busy.value = ''
  }
}

function done(id: string): boolean {
  return state.value?.steps.find((step) => step.id === id)?.done ?? false
}

/** The keys step's per-stage detail: what is missing, in the room's language. */
const missingByStage = computed<Record<string, string[]>>(() => {
  const step = state.value?.steps.find((item) => item.id === 'keys')
  const detail = step?.detail as { missing_by_stage?: Record<string, string[]> } | undefined
  return detail?.missing_by_stage ?? {}
})

/**
 * The keys the wizard must show, derived from the live catalog rather than a
 * hard-coded list: active chain order → provider env var names → secrets table.
 * That is why VOLC_API_KEY appears and the retired HOUSE_EAR_TTS_KEY does not.
 */
const missingLines = computed(() =>
  Object.entries(missingByStage.value).filter(([, names]) => names.length > 0),
)

const requiredKeys = computed<SetupKey[]>(() => {
  const doc = catalog.value
  if (!doc) return []
  const missing = new Set<string>()
  for (const names of Object.values(missingByStage.value)) {
    for (const name of names) missing.add(name)
  }
  const buckets = new Map<string, SetupKey>()
  for (const stage of STAGES) {
    const rawChain = doc.values[`providers.${stage}.chain`]
    const chain = Array.isArray(rawChain) ? rawChain.map(String) : []
    for (const provider of chain) {
      const prefix = `providers.catalog.${provider}.`
      for (const field of doc.fields) {
        if (!field.path.startsWith(prefix)) continue
        if (!/(api_key_env|app_id_env|api_secret_env)$/.test(field.path)) continue
        const env = String(doc.values[field.path] ?? '').trim()
        if (!env) continue
        const meta = doc.secrets?.[env]
        const existing = buckets.get(env)
        if (existing) {
          if (!existing.stages.includes(stage)) existing.stages.push(stage)
          if (!existing.providers.includes(provider)) existing.providers.push(provider)
          continue
        }
        buckets.set(env, {
          name: env,
          stages: [stage],
          providers: [provider],
          hasValue: Boolean(meta?.has_value),
          hint: pickLocalized(meta?.hint),
          missing: missing.has(env) || !meta?.has_value,
        })
      }
    }
  }
  return [...buckets.values()]
})

function stageLabel(stage: Stage): string {
  return t(STAGE_LABELS[stage])
}

const anyKeyInput = computed(() => Object.values(keyDrafts.value).some((value) => value.trim()))

const audioState = computed(() =>
  state.value?.audio_plane ? t('setup.audioOnline') : t('setup.audioOffline'),
)

onMounted(load)
</script>

<template>
  <header class="page-head">
    <div>
      <div class="kicker">{{ t('setup.kicker') }}</div>
      <h1>{{ t('setup.title') }}</h1>
      <p class="lede">{{ t('setup.lede') }}</p>
    </div>
    <div v-if="state" class="page-head-side">
      <span class="badge" :class="state.complete ? 'badge-ok' : 'badge-warn'">
        {{ state.complete ? t('setup.allDone') : t('setup.next', { step: stepTitle(state.next_step) }) }}
      </span>
    </div>
  </header>

  <div v-if="error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <span>{{ error }}</span>
  </div>

  <div v-if="!state && !error" class="setup-loading" role="status">
    <div class="skeleton skeleton-line" style="width: 60%" />
    <div class="skeleton skeleton-line" style="width: 42%" />
    <p class="note">{{ t('common.loading') }}</p>
  </div>

  <template v-if="state">
    <section class="setup-section">
      <div class="setup-index">00</div>
      <div class="setup-body">
        <h2>{{ t('setup.progress') }}</h2>
        <div class="progress-list">
          <div v-for="step in state.steps" :key="step.id" class="progress-row">
            <span class="progress-mark" :class="step.done ? 'ok' : 'warn'">
              <AppIcon :name="step.done ? 'check' : 'dot'" :size="14" />
            </span>
            <span class="progress-title">{{ stepTitle(step.id) }}</span>
            <span class="progress-detail mono">
              <template v-if="step.id === 'device' && state.device">{{ state.device.host }} · {{ state.device.room }}</template>
              <template v-else-if="step.id === 'wake' && state.keywords_exists">{{ state.device?.wake_words.join(' / ') }}</template>
              <template v-else-if="step.id === 'models' && !step.done">
                {{ t('setup.missing', { items: Object.entries(step.detail).filter(([, value]) => !value).map(([key]) => key).join(', ') }) }}
              </template>
              <template v-else-if="step.id === 'keys' && !step.done">{{ t('setup.missingKeys') }}</template>
            </span>
          </div>
        </div>
        <div v-if="missingLines.length" class="missing-list">
          <div v-for="[stage, names] in missingLines" :key="stage" class="missing-line">
            <span class="missing-stage mono">{{ stage }}</span>
            <span class="fail">{{ names.join(' / ') }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="setup-section">
      <div class="setup-index">01</div>
      <div class="setup-body">
        <h2>{{ t('setup.keys.title') }}</h2>
        <p class="field-hint">{{ t('setup.keys.lede') }}</p>
        <div v-if="requiredKeys.length" class="setup-fields">
          <div v-for="key in requiredKeys" :key="key.name" class="field-row setup-key-row">
            <div class="field-meta">
              <div class="field-label">
                <code class="mono">{{ key.name }}</code>
              </div>
              <div class="setup-key-tags">
                <span v-for="stage in key.stages" :key="stage" class="badge">{{ stageLabel(stage) }}</span>
                <span class="field-hint">{{ t('setup.keys.usedBy', { providers: key.providers.map(providerName).join(' / ') }) }}</span>
              </div>
              <p v-if="key.hint" class="field-hint setup-key-hint">{{ key.hint }}</p>
            </div>
            <div class="field-control">
              <div class="setup-key-line">
                <span class="badge" :class="key.missing ? 'badge-warn' : 'badge-ok'">
                  {{ key.missing ? t('setup.keys.missing') : t('setup.keys.configured') }}
                </span>
                <input
                  :id="`setup-key-${key.name}`"
                  v-model="keyDrafts[key.name]"
                  type="password"
                  autocomplete="off"
                  :placeholder="t('secrets.placeholder')"
                />
              </div>
            </div>
          </div>
        </div>
        <p v-else-if="!catalog" class="field-hint">{{ t('setup.keys.unavailable') }}</p>
        <p v-else class="field-hint">{{ t('setup.keys.none') }}</p>
        <div class="actions" style="margin-top: var(--s3)">
          <button type="button" class="btn btn-primary" :disabled="busy !== '' || !anyKeyInput" @click="saveKeys">
            {{ busy === 'keys' ? t('common.saving') : t('setup.keys.save') }}
          </button>
        </div>
        <p class="field-hint" style="margin-top: var(--s3)">{{ t('setup.keys.writeOnly') }}</p>
      </div>
    </section>

    <section class="setup-section">
      <div class="setup-index">02</div>
      <div class="setup-body">
        <h2>{{ t('setup.models.title') }}</h2>
        <p v-if="done('models')" class="ok">{{ t('setup.models.ready') }}</p>
        <template v-else>
          <p class="field-hint">{{ t('setup.models.need') }}</p>
          <pre class="cmd">{{ state.models_command || 'house-ear fetch-models' }}</pre>
          <button type="button" class="btn" :disabled="busy !== ''" @click="load">{{ t('setup.models.refresh') }}</button>
        </template>
      </div>
    </section>

    <section class="setup-section">
      <div class="setup-index">03</div>
      <div class="setup-body">
        <h2>{{ t('setup.device.title') }}</h2>
        <div class="setup-fields">
          <div class="field-row">
            <div class="field-meta"><label class="field-label" for="setup-host">{{ t('setup.device.host') }}</label></div>
            <div class="field-control">
              <input id="setup-host" v-model="host" :placeholder="t('setup.device.hostPlaceholder')" />
            </div>
          </div>
          <div class="field-row">
            <div class="field-meta"><label class="field-label" for="setup-room">{{ t('setup.device.room') }}</label></div>
            <div class="field-control">
              <input id="setup-room" v-model="room" :placeholder="t('setup.device.roomPlaceholder')" />
            </div>
          </div>
          <div class="field-row">
            <div class="field-meta"><label class="field-label" for="setup-wake">{{ t('setup.device.wake') }}</label></div>
            <div class="field-control">
              <input id="setup-wake" v-model="wakeWord" maxlength="12" :placeholder="t('setup.device.wakePlaceholder')" />
            </div>
          </div>
        </div>
        <div class="actions" style="margin-top: var(--s3)">
          <button type="button" class="btn" :disabled="busy !== '' || !host.trim()" @click="probeHost">
            {{ busy === 'probe' ? t('setup.device.probing') : t('setup.device.probe') }}
          </button>
          <button type="button" class="btn btn-primary" :disabled="busy !== '' || !host.trim()" @click="saveDevice">
            {{ busy === 'device' ? t('setup.device.saving') : t('setup.device.save') }}
          </button>
          <button type="button" class="btn" :disabled="busy !== '' || !state.device" @click="saveWake">
            {{ t('setup.device.wakeOnly') }}
          </button>
        </div>
        <p v-if="probe" :class="probe.usable ? 'ok' : 'fail'" class="field-hint">
          {{ probe.usable ? t('setup.device.reachable', { ms: probe.ssh.ms ?? '?' }) : probe.hint }}
        </p>
        <p class="field-hint" style="margin-top: var(--s3)">
          {{ t('setup.device.note', { file: state.keywords_file || t('setup.device.keywordsFile'), state: audioState }) }}
        </p>
      </div>
    </section>

    <section class="setup-section">
      <div class="setup-index">04</div>
      <div class="setup-body">
        <h2>{{ t('setup.try.title') }}</h2>
        <div class="say-bar">
          <input v-model="sayText" :placeholder="t('setup.try.placeholder')" :disabled="busy !== ''" @keyup.enter="tryOne" />
          <button type="button" class="btn btn-primary" :disabled="busy !== '' || !sayText.trim()" @click="tryOne">
            {{ busy === 'say' ? t('block.sending') : t('setup.try.send') }}
          </button>
        </div>
        <blockquote v-if="said" class="say-reply">{{ said }}</blockquote>
        <p v-if="sayError" class="note fail" role="alert">{{ sayError }}</p>
        <p class="field-hint" style="margin-top: var(--s3)">{{ t('setup.try.note') }}</p>
      </div>
    </section>

    <div v-if="state.complete" class="banner banner-ok setup-done">
      <AppIcon name="check" :size="18" class="banner-icon" />
      <div>
        <strong>{{ t('setup.done.title') }}</strong>
        <p>
          {{ t('setup.done.text', { wake: state.device?.wake_words[0] || t('setup.device.wakePlaceholder') }) }}
          <RouterLink to="/panel/overview" class="setup-done-link">{{ t('setup.done.open') }}</RouterLink>
        </p>
      </div>
    </div>
  </template>
</template>

<style scoped>
.setup-loading {
  padding: var(--s5) 0;
}

.setup-section {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: var(--s3);
  padding: var(--s6) 0;
  border-top: 1px solid var(--line);
}

.setup-index {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.06em;
  color: var(--accent);
  padding-top: 6px;
}

.setup-body {
  min-width: 0;
}

.setup-body h2 {
  font-size: 21px;
}

.setup-fields {
  margin-top: var(--s3);
}

.setup-key-row {
  align-items: start;
}

.setup-key-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 3px;
}

.setup-key-tags .field-hint {
  margin: 0;
  font-size: 11.5px;
}

.setup-key-hint {
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.setup-key-line {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-key-line input {
  flex: 1;
  min-width: 180px;
}

.progress-list {
  margin-top: var(--s3);
}

.progress-row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: 8px 0;
  border-top: 1px solid var(--line);
}

.progress-row:first-child {
  border-top: 0;
}

.progress-mark {
  display: inline-flex;
  width: 20px;
  flex: none;
}

.progress-title {
  font-size: 13.5px;
  font-weight: 550;
  flex: none;
  min-width: 96px;
}

.progress-detail {
  color: var(--muted);
  font-size: 11.5px;
  text-align: right;
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.missing-list {
  margin-top: var(--s2);
  padding: var(--s2) var(--s3);
  background: var(--fail-soft);
  border-radius: var(--r-sm);
}

.missing-line {
  display: flex;
  gap: var(--s2);
  font-size: 12px;
  overflow-wrap: anywhere;
  padding: 1px 0;
}

.missing-stage {
  color: var(--muted);
  flex: none;
  width: 40px;
  text-transform: uppercase;
  font-size: 10.5px;
}

.say-bar {
  display: flex;
  gap: var(--s2);
  margin-top: var(--s3);
}

.say-bar input {
  flex: 1;
  min-width: 0;
}

.say-reply {
  margin: var(--s3) 0 0;
  padding: 10px 12px;
  background: var(--surface-2);
  border-left: 2px solid var(--accent);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  font-size: 13.5px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.setup-done {
  margin-top: var(--s5);
}

.setup-done-link {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  margin-left: 4px;
}

@media (max-width: 720px) {
  .setup-section {
    grid-template-columns: 28px minmax(0, 1fr);
    padding: var(--s5) 0;
  }

  .progress-row {
    flex-wrap: wrap;
  }

  .progress-detail {
    text-align: left;
    flex: 1 1 100%;
    padding-left: 28px;
  }

  .say-bar {
    flex-direction: column;
  }
}
</style>
