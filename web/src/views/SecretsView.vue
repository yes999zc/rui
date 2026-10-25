<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import {
  api,
  ApiError,
  type CatalogDoc,
  type SecretConsumer,
  type SecretMeta,
  type SecretPutResult,
} from '../api'
import {
  formatClock,
  pickLocalized,
  secretConsumerLabel,
  secretHint,
  secretLabel,
  t,
  type MsgKey,
} from '../i18n'

/**
 * The credential ledger (docs/config-catalog-contract.md §1), now a hidden advanced
 * page (`/panel/credentials` + the bookmarked `/secrets` route).
 *
 * Names and types come from the catalog's typed `secrets` table: `category` says what
 * kind of credential it is and `consumers[]` says who reads it. Values are write-only:
 * the page can only ever learn `has_value`, and the input is wiped on success.
 *
 * Music is listed too, but its credentials live in their own 0600 file, so that row is
 * `writable=false` and links to the music page instead of offering a dead input.
 */
const props = defineProps<{ embedded?: boolean }>()

const CATEGORY_LABELS: Record<string, MsgKey> = {
  provider: 'secrets.category.provider',
  search: 'secrets.category.search',
  music: 'secrets.category.music',
  ha: 'secrets.category.ha',
  device: 'secrets.category.device',
  console: 'secrets.category.console',
  other: 'secrets.category.other',
}

const CATEGORY_HINTS: Record<string, MsgKey> = {
  provider: 'secrets.category.providerHint',
  search: 'secrets.category.searchHint',
  music: 'secrets.category.musicHint',
  ha: 'secrets.category.haHint',
  device: 'secrets.category.deviceHint',
  console: 'secrets.category.consoleHint',
  other: 'secrets.category.otherHint',
}

const CATEGORY_ORDER: Record<string, number> = {
  provider: 10,
  search: 20,
  music: 30,
  ha: 40,
  device: 50,
  console: 60,
  other: 90,
}

function categoryOf(meta: SecretMeta | undefined): string {
  const category = String(meta?.category ?? '')
  return category in CATEGORY_LABELS ? category : 'other'
}

function categoryLabel(category: string): string {
  return t(CATEGORY_LABELS[category] ?? CATEGORY_LABELS.other)
}

interface Slot {
  name: string
  label: string
  category: string
  categoryLabel: string
  hint: string
  consumers: SecretConsumer[]
  writable: boolean
  href: string
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
const catalogDoc = ref<CatalogDoc | null>(null)
const loading = ref(true)
const loadError = ref('')

const slots = computed<Slot[]>(() => {
  const table = catalogDoc.value?.secrets ?? {}
  return [...names.value]
    .sort((a, b) => {
      const order = (CATEGORY_ORDER[categoryOf(table[a])] ?? 90)
        - (CATEGORY_ORDER[categoryOf(table[b])] ?? 90)
      return order || a.localeCompare(b)
    })
    .map((name) => {
      const meta = table[name]
      const category = categoryOf(meta)
      const consumers = meta?.consumers ?? []
      return {
        name,
        label: secretLabel(name) || categoryLabel(category),
        category,
        categoryLabel: categoryLabel(category),
        hint: pickLocalized(meta?.hint) || secretHint(name)
          || t(CATEGORY_HINTS[category] ?? CATEGORY_HINTS.other),
        consumers,
        // A disk-only name from an older install has no metadata; it is still writable.
        writable: meta?.writable !== false,
        href: String(meta?.href ?? '/panel/music'),
      }
    })
})

/** Consumer labels are backend-provided; the id is only a last-resort fallback. */
function consumerNames(slot: Slot): string {
  return slot.consumers.length
    ? slot.consumers.map((item) => pickLocalized(item.label) || secretConsumerLabel(item.id)).join('、')
    : t('secrets.nobody')
}

const configuredCount = computed(() => names.value.filter((name) => hasKey[name]).length)

function ensureRow(name: string) {
  if (!rows[name]) rows[name] = { value: '', phase: 'idle', at: '', detail: '', confirmClear: false }
}

function absorbCatalog(catalog: CatalogDoc, diskNames: string[]) {
  catalogDoc.value = catalog
  const table = catalog.secrets ?? {}
  const set = new Set<string>([...Object.keys(table), ...diskNames])
  names.value = [...set]
  for (const name of names.value) {
    ensureRow(name)
    hasKey[name] = Boolean(table[name]?.has_value) || diskNames.includes(name)
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [catalog, disk] = await Promise.all([api.catalog(), api.secrets()])
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
  <header v-if="!props.embedded" class="page-head">
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

  <div v-else class="secret-embedded-head">
    <p class="lede">{{ t('secrets.lede') }}</p>
    <span v-if="!loading && !loadError" class="badge badge-ok">
      {{ t('secrets.configuredCount', { configured: configuredCount, total: names.length }) }}
    </span>
  </div>

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
          <div class="secret-title">
            <h3>{{ slot.label }}</h3>
            <span v-if="slot.label !== slot.categoryLabel" class="badge">{{ slot.categoryLabel }}</span>
          </div>
          <span class="badge" :class="hasKey[slot.name] ? 'badge-ok' : 'badge-warn'">
            {{ hasKey[slot.name] ? t('secrets.configured') : t('secrets.missing') }}
          </span>
          <span class="secret-consumer">
            {{ t('secrets.usedBy', { names: consumerNames(slot) }) }}
          </span>
        </div>
        <p class="field-hint">
          {{ slot.hint }}
          <span v-if="!catalogDoc?.secrets?.[slot.name]"> · {{ t('secrets.kind.generic.hint') }}</span>
        </p>

        <div v-if="slot.writable" class="secret-entry">
          <label class="sr-only" :for="'key-' + slot.name">{{ slot.label }}</label>
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

        <div v-else class="secret-external">
          <span class="field-hint">{{ t('secrets.externalWriter') }}</span>
          <RouterLink class="btn btn-sm" :to="slot.href">{{ t('secrets.openWriter') }}</RouterLink>
        </div>

        <details class="secret-tech">
          <summary>{{ t('secrets.advanced') }}</summary>
          <div class="secret-tech-body">
            <span>{{ t('secrets.envName') }}</span>
            <code class="mono">{{ slot.name }}</code>
            <span>{{ t('secrets.usedByRaw', { names: slot.consumers.map((item) => item.id).join(', ') || t('secrets.nobody') }) }}</span>
          </div>
        </details>

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
.secret-embedded-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-bottom: var(--s4);
}

.secret-external {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: var(--s3);
}

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

.secret-title {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.secret-title h3 {
  font-family: var(--font-ui);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.secret-tech {
  margin-top: var(--s2);
  color: var(--muted);
  font-size: 11px;
}

.secret-tech summary {
  cursor: pointer;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.secret-tech summary::-webkit-details-marker {
  display: none;
}

.secret-tech summary::before {
  content: '＋';
  font-size: 10px;
}

.secret-tech[open] summary::before {
  content: '－';
}

.secret-tech-body {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: 6px;
}

.secret-tech-body code {
  font-size: 11px;
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
