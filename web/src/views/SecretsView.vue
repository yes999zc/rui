<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { api, ApiError, type CatalogDoc, type Config, type SecretPutResult } from '../api'
import { formatClock, t, type MsgKey } from '../i18n'

/**
 * The secrets page (docs/config-catalog-contract.md §1).
 *
 * Names come from the catalog's `secrets` table (plus anything already on disk),
 * so a production install shows RUI_* keys and a dev install shows the
 * provider variables — no hard-coded name list. Values are write-only: the page
 * can only ever learn `has_value`, and the input is wiped on success.
 */
interface Kind {
  labelKey: MsgKey
  hintKey: MsgKey
}

const KINDS: { re: RegExp; kind: Kind; order: number }[] = [
  { re: /XFYUN_APPID|XFYUN_API_SECRET/i, order: 30, kind: { labelKey: 'secrets.kind.tts.label', hintKey: 'secrets.kind.tts.hint' } },
  { re: /XFYUN|TTS/i, order: 30, kind: { labelKey: 'secrets.kind.tts.label', hintKey: 'secrets.kind.tts.hint' } },
  { re: /ASR|SILICON/i, order: 20, kind: { labelKey: 'secrets.kind.asr.label', hintKey: 'secrets.kind.asr.hint' } },
  { re: /LLM|DEEPSEEK|OPENAI|ANTHROPIC|MODEL/i, order: 10, kind: { labelKey: 'secrets.kind.llm.label', hintKey: 'secrets.kind.llm.hint' } },
  { re: /HASS|HOME|HA_/i, order: 40, kind: { labelKey: 'secrets.kind.ha.label', hintKey: 'secrets.kind.ha.hint' } },
  { re: /DEVICE|SPEAKER/i, order: 50, kind: { labelKey: 'secrets.kind.device.label', hintKey: 'secrets.kind.device.hint' } },
  { re: /AUTH|CONSOLE/i, order: 60, kind: { labelKey: 'secrets.kind.auth.label', hintKey: 'secrets.kind.auth.hint' } },
]

const GENERIC: Kind = { labelKey: 'secrets.kind.generic.label', hintKey: 'secrets.kind.generic.hint' }

function kindOf(name: string): Kind & { order: number } {
  for (const entry of KINDS) {
    if (entry.re.test(name)) return { ...entry.kind, order: entry.order }
  }
  return { ...GENERIC, order: 90 }
}

interface Slot {
  name: string
  label: string
  hint: string
}

type Phase = 'idle' | 'saving' | 'saved' | 'cleared' | 'error'

interface Row {
  value: string
  phase: Phase
  at: string
  detail: string
  confirmClear: boolean
}

const rows = reactive<Record<string, Row>>({})
const names = ref<string[]>([])
const hasKey = reactive<Record<string, boolean>>({})
const consumers = reactive<Record<string, string[]>>({})
const catalogDoc = ref<CatalogDoc | null>(null)
const loading = ref(true)
const loadError = ref('')

const slots = computed<Slot[]>(() =>
  [...names.value]
    .sort((a, b) => kindOf(a).order - kindOf(b).order || a.localeCompare(b))
    .map((name) => ({ name, label: t(kindOf(name).labelKey), hint: t(kindOf(name).hintKey) })),
)

const configuredCount = computed(() => names.value.filter((name) => hasKey[name]).length)

function ensureRow(name: string) {
  if (!rows[name]) rows[name] = { value: '', phase: 'idle', at: '', detail: '', confirmClear: false }
}

function note(mapping: Record<string, string[]>, env: string | undefined, who: string) {
  if (!env) return
  const list = (mapping[env] ??= [])
  if (!list.includes(who)) list.push(who)
}

function absorbConfig(config: Config) {
  for (const key of Object.keys(consumers)) delete consumers[key]
  const catalog = config.providers?.catalog ?? {}
  for (const [provider, block] of Object.entries(catalog)) {
    note(consumers, block?.api_key_env, provider)
    note(consumers, (block as { api_secret_env?: string })?.api_secret_env, provider)
    note(consumers, (block as { app_id_env?: string })?.app_id_env, provider)
  }
  const search = config.tools?.search
  note(consumers, search?.api_key_env, 'tools.search')
  note(consumers, config.tools?.ha?.token_env, 'tools.ha')
  for (const device of config.devices ?? []) {
    note(consumers, device.token_env, `device:${device.id}`)
  }
}

function absorbCatalog(catalog: CatalogDoc, diskNames: string[]) {
  catalogDoc.value = catalog
  const set = new Set<string>([...Object.keys(catalog.secrets ?? {}), ...diskNames])
  names.value = [...set]
  for (const name of names.value) {
    ensureRow(name)
    hasKey[name] = Boolean(catalog.secrets?.[name]?.has_value) || diskNames.includes(name)
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [catalog, config, disk] = await Promise.all([api.catalog(), api.config(), api.secrets()])
    absorbConfig(config)
    absorbCatalog(catalog, disk.names ?? [])
  } catch (exc) {
    loadError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

/** Re-read has_value after a write without disturbing typed input in other rows. */
async function refreshStatus(name?: string) {
  try {
    const catalog = await api.catalog()
    catalogDoc.value = catalog
    const table = catalog.secrets ?? {}
    for (const key of Object.keys(table)) {
      if (!(key in hasKey)) ensureRow(key)
      hasKey[key] = Boolean(table[key]?.has_value)
    }
    if (name && name in table) hasKey[name] = Boolean(table[name]?.has_value)
  } catch {
    /* The write already succeeded; a stale badge is better than a scary error. */
  }
}

function detailOf(exc: unknown): string {
  if (exc instanceof ApiError) {
    const raw = exc.detail
    if (typeof raw === 'string' && raw) return raw
    if (Array.isArray(raw)) return exc.message
    if (raw != null) return JSON.stringify(raw)
    return `${exc.status} ${exc.statusText}`
  }
  return exc instanceof Error ? exc.message : String(exc)
}

function hintOf(exc: unknown): string {
  if (!(exc instanceof ApiError)) return t('secrets.error.network')
  if (exc.status === 400) return t('secrets.error.badName')
  if (exc.status === 401) return t('secrets.error.unauthorized')
  return ''
}

function applyResult(name: string, result: SecretPutResult) {
  hasKey[name] = result.has_key
}

async function save(slot: Slot) {
  const row = rows[slot.name]
  if (!row || row.phase === 'saving') return
  const value = row.value.trim()
  if (!value) return
  row.phase = 'saving'
  row.detail = ''
  try {
    applyResult(slot.name, await api.putSecret(slot.name, value))
    // The key is on the server now; nothing on this page needs to remember it.
    row.value = ''
    row.phase = hasKey[slot.name] ? 'saved' : 'error'
    row.at = formatClock()
    if (row.phase === 'error') row.detail = t('secrets.failed')
    await refreshStatus(slot.name)
  } catch (exc) {
    row.phase = 'error'
    row.detail = [detailOf(exc), hintOf(exc)].filter(Boolean).join('\n')
    row.at = ''
  }
}

async function clearSecret(slot: Slot) {
  const row = rows[slot.name]
  if (!row || row.phase === 'saving') return
  row.confirmClear = false
  row.phase = 'saving'
  row.detail = ''
  try {
    applyResult(slot.name, await api.putSecret(slot.name, ''))
    row.value = ''
    row.phase = 'cleared'
    row.at = formatClock()
    await refreshStatus(slot.name)
  } catch (exc) {
    row.phase = 'error'
    row.detail = [detailOf(exc), hintOf(exc)].filter(Boolean).join('\n')
    row.at = ''
  }
}

function onEdit(name: string) {
  const row = rows[name]
  if (!row) return
  if (row.phase === 'saved' || row.phase === 'cleared') {
    row.phase = 'idle'
    row.at = ''
  }
  row.detail = ''
}

function pillText(row: Row): string {
  if (row.phase === 'saving') return t('secrets.saving')
  if (row.phase === 'saved') return t('secrets.savedAt', { time: row.at })
  if (row.phase === 'cleared') return t('secrets.clearedAt', { time: row.at })
  if (row.phase === 'error') return t('secrets.failed')
  return ''
}

onMounted(load)
</script>

<template>
  <header class="page-head">
    <div>
      <div class="kicker">{{ t('secrets.kicker') }}</div>
      <h1>{{ t('secrets.title') }}</h1>
      <p class="lede">{{ t('secrets.lede') }}</p>
    </div>
    <div v-if="!loading && !loadError" class="page-head-side">
      <span class="badge badge-ok">
        {{ t('secrets.configuredCount', { configured: configuredCount, total: names.length }) }}
      </span>
    </div>
  </header>

  <p v-if="loading" class="empty">{{ t('common.loading') }}</p>

  <div v-if="loadError" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <div>
      <strong>{{ t('secrets.loadFailTitle') }}</strong>
      <p class="note">{{ t('secrets.loadFailHint') }}</p>
      <p class="note mono">{{ loadError }}</p>
      <button type="button" class="btn btn-sm" style="margin-top: 10px" @click="load">{{ t('secrets.retry') }}</button>
    </div>
  </div>

  <template v-if="!loading && !loadError">
    <section class="block">
      <div class="block-head">
        <h2>{{ t('secrets.envVars') }}</h2>
        <span class="meta">{{ t('secrets.writeHint') }}</span>
      </div>

      <p v-if="!slots.length" class="empty">{{ t('secrets.empty') }}</p>

      <div v-for="slot in slots" :key="slot.name" class="secret-row">
        <div class="secret-head">
          <code class="secret-name mono">{{ slot.name }}</code>
          <span class="badge">{{ slot.label }}</span>
          <span class="badge" :class="hasKey[slot.name] ? 'badge-ok' : 'badge-warn'">
            {{ hasKey[slot.name] ? t('secrets.configured') : t('secrets.missing') }}
          </span>
          <span class="secret-consumer">
            {{ t('secrets.usedBy', { names: consumers[slot.name]?.join(', ') || t('secrets.nobody') }) }}
          </span>
        </div>
        <p class="field-hint">
          {{ slot.hint }}
          <span v-if="!catalogDoc?.secrets?.[slot.name]"> · {{ t('secrets.kind.generic.hint') }}</span>
        </p>

        <div class="secret-entry">
          <label class="sr-only" :for="'key-' + slot.name">{{ slot.name }}</label>
          <input
            :id="'key-' + slot.name"
            v-model="rows[slot.name].value"
            type="password"
            autocomplete="new-password"
            spellcheck="false"
            :placeholder="t('secrets.placeholder')"
            :disabled="rows[slot.name].phase === 'saving'"
            @input="onEdit(slot.name)"
            @keyup.enter="save(slot)"
          />
          <button type="button" class="btn btn-primary" :disabled="rows[slot.name].phase === 'saving' || !rows[slot.name].value.trim()" @click="save(slot)">
            {{ rows[slot.name].phase === 'saving' ? t('secrets.saving') : t('secrets.save') }}
          </button>

          <template v-if="!rows[slot.name].confirmClear">
            <button type="button" class="btn" :disabled="rows[slot.name].phase === 'saving' || !hasKey[slot.name]" @click="rows[slot.name].confirmClear = true">
              {{ t('secrets.clear') }}
            </button>
          </template>
          <template v-else>
            <span class="secret-confirm">{{ t('secrets.confirmClear') }}</span>
            <button type="button" class="btn btn-danger" :disabled="rows[slot.name].phase === 'saving'" @click="clearSecret(slot)">
              {{ t('secrets.confirmYes') }}
            </button>
            <button type="button" class="btn btn-ghost" @click="rows[slot.name].confirmClear = false">
              {{ t('common.cancel') }}
            </button>
          </template>

          <span
            v-if="pillText(rows[slot.name])"
            class="badge"
            :class="rows[slot.name].phase === 'error' ? 'badge-fail' : rows[slot.name].phase === 'saved' ? 'badge-ok' : 'badge-warn'"
          >
            {{ pillText(rows[slot.name]) }}
          </span>
        </div>

        <p v-if="rows[slot.name].phase === 'error'" class="note fail" role="alert">{{ rows[slot.name].detail }}</p>
        <p v-if="rows[slot.name].phase === 'cleared' && hasKey[slot.name]" class="note warn">
          {{ t('secrets.clearedEnv') }}
        </p>
      </div>
    </section>

    <section class="block">
      <div class="block-head">
        <h2>{{ t('secrets.rulesTitle') }}</h2>
      </div>
      <ul class="rules">
        <li>{{ t('secrets.rule.writeOnly') }}</li>
        <li>{{ t('secrets.rule.envFirst') }}</li>
        <li>{{ t('secrets.rule.immediate') }}</li>
        <li>{{ t('secrets.rule.allowlist') }}</li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
.secret-row {
  padding: 18px 0;
  border-top: 1px solid var(--line);
}

.secret-row:first-of-type {
  border-top: 1px solid var(--line-strong);
}

.secret-head {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.secret-name {
  font-size: 12.5px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.secret-consumer {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.secret-entry {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s3);
}

.secret-entry input {
  flex: 1;
  min-width: 220px;
  max-width: 420px;
  font-family: var(--font-mono);
  font-size: 12.5px;
}

.secret-confirm {
  font-size: 12px;
  color: var(--warn);
}

.rules {
  margin: var(--s2) 0 0;
  padding-left: 18px;
  color: var(--ink-2);
  font-size: 13px;
}

.rules li {
  margin-bottom: 6px;
}

@media (max-width: 720px) {
  .secret-consumer {
    margin-left: 0;
    flex: 1 1 100%;
  }

  .secret-entry input {
    flex: 1 1 100%;
    min-width: 0;
    max-width: none;
  }

  .secret-entry .btn {
    flex: 1;
  }
}
</style>
