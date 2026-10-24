<script setup lang="ts">
/**
 * The configuration centre (docs/config-catalog-contract.md §3).
 *
 * The backend owns the field directory: tiers, groups, labels, hints, effect
 * timing, bounds. This component only renders it and edits values — adding a
 * setting is a catalog entry, never a new page. Everything visible is bilingual:
 * label/hint/unit are `{zh,en}` on the wire, tier titles too.
 *
 * Saving sends only dirty dotted paths to `PUT /api/v1/config/values`; a 422 is
 * mapped back onto the offending rows; `restart_required` shows the exact command
 * and, for audio fields, a "restart audio plane only" action.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { api, ApiError, type CatalogDoc, type CatalogField, type CatalogGroup, type LocalizedText } from '../api'
import { isEnglish, localizeBackend, localizeIssue, pickLocalized, providerName, secretLabel, t } from '../i18n'
import {
  readAuditionOutput,
  requestAudition,
  writeAuditionOutput,
  type AuditionOutput,
} from './providers/audition'

const props = defineProps<{
  /** Catalog group ids to render (supports `providers.*`); omitted = every group. */
  groups?: string[]
  panelId?: string
  /** Optional read-only reference links handed over by the panel spec. */
  links?: { href: string; label?: LocalizedText | string; label_en?: string }[]
  /** The panel's tier (component_args.tier): only groups of this tier are shown. */
  tier?: number
  /** Panel-level warning copy (component_args.warning), shown as a banner. */
  warning?: LocalizedText | string
}>()

type Value = unknown

const doc = ref<CatalogDoc | null>(null)
const loading = ref(true)
const loadError = ref('')
const draft = ref<Record<string, Value>>({})
const baseline = ref<Record<string, Value>>({})
const errors = ref<Record<string, string>>({})
const filter = ref('')
const auditionState = reactive<Record<string, 'idle' | 'loading' | 'playing' | 'speaker'>>({})
const auditionError = ref('')
const auditionNotice = ref('')
const auditionOutput = ref<AuditionOutput>(readAuditionOutput())
let auditionAudio: HTMLAudioElement | null = null
let auditionTimer: number | undefined
const hasAudition = computed(() => fields.value.some((field) => Boolean(field.audition)))
const showAdvanced = ref(false)
/** Dotted config paths are a diagnostic detail, hidden behind one toolbar toggle. */
const showTech = ref(false)
const saving = ref(false)
const saveError = ref('')
const savedInfo = ref<{ applied: string[]; restart: boolean; restartFields: string[]; warnings: string[] } | null>(null)
const resultRef = ref<HTMLElement | null>(null)
const restarting = ref(false)
const restartError = ref('')
const restartOk = ref(false)
const copied = ref(false)
const listDrafts = reactive<Record<string, string>>({})
const secretDrafts = reactive<Record<string, string>>({})
const secretBusy = reactive<Record<string, boolean>>({})
const secretMsgs = reactive<Record<string, string>>({})
let copyTimer: number | undefined

const fields = computed<CatalogField[]>(() => doc.value?.fields ?? [])
const writableFields = computed(() => fields.value.filter((f) => f.writable !== false && f.kind !== 'secret'))

/** Groups this panel owns after tier normalisation (see selectedGroups below). */
const panelFieldSet = computed(() => {
  const ids = new Set(selectedGroups.value.map((group) => group.id))
  return writableFields.value.filter((field) => ids.has(field.group))
})

function readValue(path: string): Value {
  const values = doc.value?.values ?? {}
  if (path in values) return values[path]
  let current: unknown = values
  for (const part of path.split('.')) {
    if (current == null || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

function clone<T>(value: T): T {
  if (value == null || typeof value !== 'object') return value
  return JSON.parse(JSON.stringify(value)) as T
}

function same(a: Value, b: Value): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function syncDraft(): void {
  const nextBaseline: Record<string, Value> = {}
  for (const field of panelFieldSet.value) {
    nextBaseline[field.path] = clone(readValue(field.path))
  }
  baseline.value = nextBaseline
  draft.value = clone(nextBaseline)
  errors.value = {}
}

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const next = await api.catalog()
    doc.value = next
    syncDraft()
    if (!silent) {
      savedInfo.value = null
      saveError.value = ''
    }
  } catch (exc) {
    loadError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

/** Tier metadata, with a dictionary fallback if the catalog omits it. */
function tierHint(tier: number): string {
  const found = doc.value?.tiers?.find((item) => Number(item.id) === tier)
  return found ? pickLocalized(found.hint) : ''
}

function matchesGroup(id: string, pattern: string): boolean {
  if (pattern === id) return true
  if (pattern.endsWith('.*')) {
    const base = pattern.slice(0, -2)
    return id === base || id.startsWith(`${base}.`)
  }
  // A panel may name a parent group (`tools`) while the catalog splits it (`tools.search`).
  return id.startsWith(`${pattern}.`)
}

/**
 * Which catalog groups this panel renders.
 *
 * The panel spec names its groups, but the audit found that the platform's
 * speaker/audio split did not match the catalog's tiers: tier-2 audio+ack groups
 * were handed to `foundation`, while `speaker` only got `devices`. The catalog's
 * `tier` is the source of truth, so the panel is normalised here:
 *  - groups are filtered to the panel's declared tier;
 *  - the speaker panel always owns every tier-2 group (devices, audio, ack);
 *  - foundation therefore keeps only system/web (tier 1) and its explicit warning.
 * Adding a catalog field needs no panel change; adding a group does.
 */
const selectedGroups = computed<CatalogGroup[]>(() => {
  const all = doc.value?.groups ?? []
  const patterns = props.groups?.filter(Boolean) ?? []
  let selected = patterns.length ? all.filter((g) => patterns.some((p) => matchesGroup(g.id, p))) : all
  const tier = props.tier == null ? null : Number(props.tier)
  if (tier != null) selected = selected.filter((group) => Number(group.tier) === tier)
  if (props.panelId === 'speaker') {
    const tierTwo = all.filter((group) => Number(group.tier) === 2)
    const seen = new Set(selected.map((group) => group.id))
    selected = [...selected, ...tierTwo.filter((group) => !seen.has(group.id))]
  }
  return [...selected].sort((a, b) => a.tier - b.tier || a.order - b.order)
})

const visibleFields = computed<CatalogField[]>(() => {
  const needle = filter.value.trim().toLowerCase()
  return fields.value.filter((field) => {
    if (field.kind === 'secret') return false
    if (field.advanced && !showAdvanced.value) return false
    if (!needle) return true
    const haystack = [field.path, pickLocalized(field.label), pickLocalized(field.hint)].join(' ').toLowerCase()
    return haystack.includes(needle)
  })
})

/** One effect badge per group instead of the same tiny badge on every row. */
function groupEffect(fields: CatalogField[]): 'save' | 'restart' | 'mixed' {
  const effects = new Set(fields.map((field) => field.effect))
  if (effects.size > 1) return 'mixed'
  return fields[0]?.effect === 'restart' ? 'restart' : 'save'
}

/** Groups that still have something to show after filter + advanced rules. */
const renderedGroups = computed(() =>
  selectedGroups.value
    .map((group) => {
      const fields = visibleFields.value.filter((field) => field.group === group.id)
      return { group, fields, effect: groupEffect(fields) }
    })
    .filter((entry) => entry.fields.length > 0),
)

const visibleTiers = computed(() => {
  const tiers = new Set(renderedGroups.value.map((entry) => entry.group.tier))
  return [...tiers].sort((a, b) => a - b)
})

const hasTierOne = computed(() => visibleTiers.value.includes(1))

/**
 * Panel-level warning: the spec's own bilingual copy (foundation), else the tier
 * hint. Assistant/session are tier 1 too but are everyday settings, so a scary
 * banner is only shown when the spec asks for one.
 */
const warningText = computed(() => {
  const given = pickLocalized(props.warning)
  if (given) return given
  if (props.panelId === 'foundation' && hasTierOne.value) return tierHint(1)
  return ''
})

/** Secrets relevant to this panel, straight from the catalog's secrets table. */
const SECRET_MATCHERS: Record<string, RegExp> = {
  providers: /LLM|ASR|TTS|DEEPSEEK|SILICON|XFYUN|OPENAI|ANTHROPIC|MODEL/i,
  tools: /HASS|HOME|HA_|SEARCH/i,
  speaker: /DEVICE/i,
  foundation: /AUTH|CONSOLE/i,
}

const secretRows = computed(() => {
  const all = Object.entries(doc.value?.secrets ?? {})
  const matcher = props.panelId ? SECRET_MATCHERS[props.panelId] : undefined
  if (!matcher) return []
  return all
    .filter(([name]) => matcher.test(name))
    .map(([name, meta]) => ({ name, has: Boolean(meta?.has_value) }))
})

const secretTotal = computed(() => Object.keys(doc.value?.secrets ?? {}).length)

const totalVisible = computed(() =>
  renderedGroups.value.reduce((count, entry) => count + entry.fields.length, 0),
)

const dirtyPaths = computed(() => {
  const paths: string[] = []
  for (const field of panelFieldSet.value) {
    if (!same(draft.value[field.path], baseline.value[field.path])) paths.push(field.path)
  }
  return paths
})

const dirtyFields = computed(() => writableFields.value.filter((field) => dirtyPaths.value.includes(field.path)))

const instantCount = computed(() => dirtyFields.value.filter((f) => f.effect !== 'restart').length)
const restartCount = computed(() => dirtyFields.value.filter((f) => f.effect === 'restart').length)
const isDirty = computed(() => dirtyPaths.value.length > 0)

const restartFields = computed(() => savedInfo.value?.restartFields ?? [])
const restartCommand = computed(() => doc.value?.restart_command || 'docker compose restart')
const canRestartAudio = computed(
  () => savedInfo.value?.restart === true && restartFields.value.some((path) => path.startsWith('audio.')),
)
const restartFieldsShown = computed(() => restartFields.value.slice(0, 12))
const restartFieldsMore = computed(() => Math.max(0, restartFields.value.length - 12))

/**
 * The catalog prefixes device/provider settings with their raw id
 * (`lx06 · listen port`, `volc-tts · voice`). Replace that prefix with the
 * human name; the exact dotted path stays available in technical details.
 */
function fieldLabel(field: CatalogField): string {
  const text = pickLocalized(field.label) || field.path
  const prefixed = text.match(/^([a-z0-9_.-]+)\s*·\s*(.+)$/i)
  if (!prefixed) return text
  const rest = prefixed[2]
  if (field.path.startsWith('providers.catalog.')) {
    const providerId = field.path.split('.')[2]
    if (providerId) return `${providerName(providerId)} · ${rest}`
  }
  if (field.path.startsWith('devices.')) {
    const index = Number(field.path.split('.')[1])
    const ordinal = Number.isFinite(index) ? index + 1 : 1
    return `${t('config.deviceOrdinal', { n: ordinal })} · ${rest}`
  }
  return text
}

/** Human name for a write-only credential row; the env name is shown as detail. */
function secretDisplayName(name: string): string {
  return secretLabel(name) || t('secrets.name.other')
}

function fieldHint(field: CatalogField): string {
  return pickLocalized(field.hint)
}

/** Long copy is clamped to one line and expands in place; short copy stays plain. */
function hintIsLong(field: CatalogField): boolean {
  const text = fieldHint(field)
  return text.length > (isEnglish.value ? 96 : 42)
}

function fieldUnit(field: CatalogField): string {
  return pickLocalized(field.unit)
}

function effectLabel(effect: string): string {
  if (effect === 'mixed') return t('config.effect.mixed')
  return effect === 'restart' ? t('config.effect.restart') : t('config.effect.save')
}

type RawOption = string | number | { value: string | number; label?: LocalizedText | string }

function optionValue(option: RawOption): string | number {
  return typeof option === 'string' || typeof option === 'number' ? option : option.value
}

function optionLabel(option: RawOption): string {
  if (typeof option === 'string' || typeof option === 'number') return String(option)
  return option.label ? pickLocalized(option.label) : String(option.value)
}

/** Jump to a group; respects reduced motion and the sticky toolbar offset. */
function jumpTo(id: string): void {
  const target = document.getElementById(`group-${id}`)
  if (!target) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

function isComplex(field: CatalogField): boolean {
  const value = readValue(field.path)
  return value != null && typeof value === 'object'
}

function complexCount(field: CatalogField): number {
  const value = readValue(field.path)
  return Array.isArray(value) ? value.length : 1
}

function cfgFieldLabel(path: string): string {
  const field = doc.value?.fields.find((item) => item.path === path)
  return field ? fieldLabel(field) : path
}

function displayValue(field: CatalogField): string {
  const value = readValue(field.path)
  if (value == null || value === '') return t('config.emptyValue')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** Read-only rows can be objects (a speaker record, a server list): show them as JSON. */
const prettyValue = computed(() => (field: CatalogField): string => {
  const value = readValue(field.path)
  if (value == null || value === '') return t('config.emptyValue')
  return typeof value === 'string' ? value : JSON.stringify(value, null, 1)
})

/** Save a secret through the write-only endpoint; the catalog is re-read for has_value. */
async function saveSecret(name: string): Promise<void> {
  const value = (secretDrafts[name] ?? '').trim()
  if (!value || secretBusy[name]) return
  secretBusy[name] = true
  secretMsgs[name] = ''
  try {
    await api.putSecret(name, value)
    secretDrafts[name] = ''
    secretMsgs[name] = t('config.secretSaved')
    await load(true)
  } catch (exc) {
    secretMsgs[name] = exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc)
  } finally {
    secretBusy[name] = false
  }
}

function setList(field: CatalogField, action: 'add' | 'remove', value: string): void {
  const current = Array.isArray(draft.value[field.path]) ? [...(draft.value[field.path] as unknown[])] : []
  const text = value.trim()
  if (action === 'add') {
    if (!text) return
    for (const part of text.split(/[,，\s]+/).filter(Boolean)) {
      if (!current.some((item) => String(item) === part)) current.push(part)
    }
    listDrafts[field.path] = ''
  } else {
    const index = current.findIndex((item) => String(item) === value)
    if (index >= 0) current.splice(index, 1)
  }
  draft.value[field.path] = current
}

function asInput(value: unknown): string | number {
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number') return value
  return JSON.stringify(value)
}

function setValue(field: CatalogField, raw: unknown): void {
  draft.value[field.path] = raw
}

function listOf(field: CatalogField): string[] {
  const value = draft.value[field.path]
  return Array.isArray(value) ? value.map(String) : []
}

function coerce(field: CatalogField): Value {
  const value = draft.value[field.path]
  if (field.kind === 'number') {
    if (value === '' || value == null) return null
    const number = Number(value)
    return Number.isFinite(number) ? number : value
  }
  if (field.kind === 'bool') return value === true
  if (field.kind === 'list') return Array.isArray(value) ? value.map(String) : []
  if (field.kind === 'select') {
    for (const option of (field.options ?? []) as RawOption[]) {
      if (String(optionValue(option)) === String(value)) return optionValue(option)
    }
    return value ?? ''
  }
  return value ?? ''
}

function revert(): void {
  draft.value = clone(baseline.value)
  errors.value = {}
  saveError.value = ''
  savedInfo.value = null
}

function setAuditionOutput(next: AuditionOutput): void {
  auditionOutput.value = next
  writeAuditionOutput(next)
  stopAudition()
  auditionNotice.value = ''
}

function stopAudition(): void {
  if (auditionTimer) window.clearTimeout(auditionTimer)
  auditionTimer = undefined
  if (auditionAudio) {
    auditionAudio.pause()
    auditionAudio = null
  }
  for (const path of Object.keys(auditionState)) auditionState[path] = 'idle'
}

/** 试听: synthesise the current (possibly unsaved) voice and play it here. */
async function playAudition(field: CatalogField): Promise<void> {
  if (!field.audition) return
  stopAudition()
  auditionError.value = ''
  auditionNotice.value = ''
  auditionState[field.path] = 'loading'
  try {
    const voice = String(draft.value[field.path] ?? '')
    const prosody: Record<string, unknown> = {}
    for (const key of ['speech_rate', 'emotion', 'emotion_scale'] as const) {
      const path = `providers.catalog.${field.audition.provider}.${key}`
      if (path in draft.value) prosody[key] = draft.value[path]
    }
    const outcome = await requestAudition(
      {
        stage: field.audition.stage,
        provider: field.audition.provider,
        body: { voice, ...(prosody ?? {}) },
      },
      auditionOutput.value,
    )
    if (outcome.played === 'speaker') {
      auditionState[field.path] = 'speaker'
      auditionNotice.value = outcome.duration_ms
        ? t('providers.auditionSpeakerDone', { ms: outcome.duration_ms })
        : t('providers.auditionSpeakerPlaying')
      auditionTimer = window.setTimeout(() => {
        if (auditionState[field.path] === 'speaker') auditionState[field.path] = 'idle'
      }, Math.max(1000, outcome.duration_ms + 600))
      return
    }
    if (outcome.fallback) auditionNotice.value = outcome.fallback
    const url = URL.createObjectURL(outcome.blob)
    auditionAudio = new Audio(url)
    auditionAudio.onended = () => {
      auditionState[field.path] = 'idle'
      URL.revokeObjectURL(url)
    }
    auditionAudio.onerror = () => {
      auditionState[field.path] = 'idle'
      auditionError.value = t('config.auditionFailed')
      URL.revokeObjectURL(url)
    }
    auditionState[field.path] = 'playing'
    await auditionAudio.play()
  } catch (exc) {
    auditionState[field.path] = 'idle'
    auditionError.value = exc instanceof Error ? exc.message : String(exc)
  }
}

function fieldPathsFromIssue(issue: { loc?: (string | number)[] }): string | undefined {
  const loc = (issue.loc ?? []).map(String)
  const known = new Set(fields.value.map((field) => field.path))
  for (let i = 0; i < loc.length; i += 1) {
    const candidate = loc.slice(i).join('.')
    if (known.has(candidate)) return candidate
  }
  const last = loc[loc.length - 1]
  if (!last) return undefined
  return fields.value.find((field) => field.path === last || field.path.endsWith(`.${last}`))?.path
}

async function save(): Promise<void> {
  if (saving.value || !isDirty.value) return
  saving.value = true
  saveError.value = ''
  errors.value = {}
  savedInfo.value = null
  restartError.value = ''
  restartOk.value = false
  const payload: Record<string, Value> = {}
  for (const field of dirtyFields.value) payload[field.path] = coerce(field)
  try {
    const result = await api.saveConfigValues(payload)
    const applied = result.applied ?? dirtyPaths.value
    savedInfo.value = {
      applied,
      restart: Boolean(result.restart_required),
      restartFields: result.restart_fields ?? [],
      warnings: result.warnings ?? [],
    }
    baseline.value = clone(draft.value)
    void load(true)
    await nextTick()
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    resultRef.value?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
  } catch (exc) {
    if (exc instanceof ApiError) {
      for (const issue of exc.issues ?? []) {
        const path = fieldPathsFromIssue(issue)
        if (path) errors.value[path] = localizeIssue(issue.msg)
      }
      const count = Object.keys(errors.value).length
      saveError.value = count ? t('config.validationFailed', { n: count }) : exc.message
      if (Object.keys(errors.value).length) {
        await nextTick()
        const target = document.querySelector<HTMLElement>('.field-row.invalid')
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        target?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' })
      }
    } else {
      saveError.value = exc instanceof Error ? exc.message : String(exc)
    }
  } finally {
    saving.value = false
  }
}

async function restartAudio(): Promise<void> {
  if (restarting.value) return
  restarting.value = true
  restartError.value = ''
  restartOk.value = false
  try {
    const result = await api.restartAudio()
    if (result.restarted) {
      restartOk.value = true
      void load(true)
    } else {
      restartError.value = result.error || t('mcp.error')
    }
  } catch (exc) {
    restartError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    restarting.value = false
  }
}

async function copyCommand(): Promise<void> {
  try {
    await navigator.clipboard.writeText(restartCommand.value)
    copied.value = true
    if (copyTimer) window.clearTimeout(copyTimer)
    copyTimer = window.setTimeout(() => { copied.value = false }, 1800)
  } catch {
    copied.value = false
  }
}

function beforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

const referenceLinks = computed(() => {
  const extra = props.panelId === 'tools'
    ? [{ href: '/tools', label: t('config.registeredTools') }]
    : []
  const given = (props.links ?? []).map((link) => ({
    href: link.href,
    label: pickLocalized(link.label) || link.label_en || link.href,
  }))
  const secrets = secretTotal.value
    ? [{ href: '/secrets', label: t('config.manageSecrets') }]
    : []
  return [...given, ...extra, ...secrets]
})

onMounted(() => {
  void load()
  window.addEventListener('beforeunload', beforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  if (copyTimer) window.clearTimeout(copyTimer)
  stopAudition()
})

onBeforeRouteLeave(() => (isDirty.value ? window.confirm(t('config.unsavedLeave')) : true))
</script>

<template>
  <div class="config-center">
    <div v-if="loading" class="cfg-loading" role="status">
      <div class="skeleton skeleton-line" style="width: 32%" />
      <div class="skeleton skeleton-line" style="width: 78%" />
      <div class="skeleton skeleton-line" style="width: 64%" />
      <p class="note">{{ t('config.loading') }}</p>
    </div>

    <div v-else-if="loadError" class="banner banner-fail cfg-unavailable">
      <AppIcon name="alert" :size="18" class="banner-icon" />
      <div>
        <h2 class="cfg-unavailable-title">{{ t('config.unavailable') }}</h2>
        <p class="note">{{ t('config.unavailableHint') }}</p>
        <p class="note mono cfg-error-detail">{{ loadError }}</p>
        <button class="btn btn-sm" style="margin-top: 10px" @click="load()">{{ t('config.retry') }}</button>
      </div>
    </div>

    <template v-else>
      <div class="cfg-toolbar">
        <div class="cfg-toolbar-tools">
          <label class="sr-only" for="cfg-filter">{{ t('config.filter') }}</label>
          <input id="cfg-filter" v-model="filter" type="search" class="cfg-filter" :placeholder="t('config.filter')" />
          <label v-if="fields.some((f) => f.advanced)" class="cfg-advanced">
            <input v-model="showAdvanced" type="checkbox" />
            <span>{{ showAdvanced ? t('config.hideAdvanced') : t('config.showAdvanced') }}</span>
          </label>
          <label class="cfg-advanced">
            <input v-model="showTech" type="checkbox" />
            <span>{{ t('config.techDetails') }}</span>
          </label>
          <div v-if="hasAudition" class="cfg-audition-output" role="group" :aria-label="t('providers.auditionOutput')">
            <button
              type="button"
              class="cfg-audition-choice"
              :class="{ on: auditionOutput === 'speaker' }"
              :aria-pressed="auditionOutput === 'speaker'"
              @click="setAuditionOutput('speaker')"
            >
              {{ t('providers.auditionSpeaker') }}
            </button>
            <button
              type="button"
              class="cfg-audition-choice"
              :class="{ on: auditionOutput === 'browser' }"
              :aria-pressed="auditionOutput === 'browser'"
              @click="setAuditionOutput('browser')"
            >
              {{ t('providers.auditionLocal') }}
            </button>
          </div>
          <span v-if="isDirty" class="badge badge-warn cfg-dirty">{{ t('config.modified', { n: dirtyPaths.length }) }}</span>
          <div v-if="isDirty || saveError || saving" class="cfg-save-inline">
            <span v-if="saveError" class="fail small cfg-inline-error">{{ saveError }}</span>
            <span v-else class="mono cfg-inline-count">
              {{ t('config.instant', { n: instantCount }) }} <span class="cfg-sep">·</span>
              {{ t('config.restartCount', { n: restartCount }) }}
            </span>
            <button type="button" class="btn btn-sm btn-ghost" :disabled="saving" @click="revert">
              {{ t('config.revert') }}
            </button>
            <button type="button" class="btn btn-sm btn-primary" :disabled="saving" @click="save">
              {{ saving ? t('config.saving') : t('config.save') }}
            </button>
          </div>
        </div>
      </div>

      <nav v-if="renderedGroups.length > 1" class="cfg-indexbar" :aria-label="t('config.groups')">
        <a
          v-for="(entry, index) in renderedGroups"
          :key="`jump-${entry.group.id}`"
          class="cfg-indexchip"
          :href="`#group-${entry.group.id}`"
          @click.prevent="jumpTo(entry.group.id)"
        >
          <span class="cfg-indexnum">{{ String(index + 1).padStart(2, '0') }}</span>
          {{ pickLocalized(entry.group.title) }}
        </a>
      </nav>

      <div v-if="warningText" class="banner banner-warn cfg-warning">
        <AppIcon name="alert" :size="18" class="banner-icon" />
        <div>
          <strong>{{ t('config.tier1Badge') }}</strong>
          <p>{{ warningText }}</p>
        </div>
      </div>

      <div v-if="!renderedGroups.length" class="empty cfg-empty">
        {{ filter ? t('config.noResults') : t('config.noFields') }}
      </div>

      <section v-for="(entry, index) in renderedGroups" :id="`group-${entry.group.id}`" :key="entry.group.id" class="cfg-group">
        <header class="cfg-group-head">
          <span class="cfg-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <div class="cfg-group-title">
            <h2>{{ pickLocalized(entry.group.title) || entry.group.id }}</h2>
            <p v-if="entry.group.hint" class="field-hint">{{ pickLocalized(entry.group.hint) }}</p>
          </div>
          <div class="cfg-group-badges">
            <span class="badge" :class="entry.effect === 'restart' ? 'badge-restart' : entry.effect === 'mixed' ? 'badge-warn' : 'badge-save'">
              {{ effectLabel(entry.effect) }}
            </span>
            <span class="cfg-group-count meta">{{ t('config.itemsCount', { n: entry.fields.length }) }}</span>
          </div>
        </header>

        <div class="cfg-fields">
          <div
            v-for="field in entry.fields"
            :key="field.path"
            class="field-row"
            :class="{ invalid: errors[field.path] }"
          >
            <div class="field-meta">
              <label class="field-label" :for="`f-${field.path}`">
                <span v-if="isDirty && dirtyPaths.includes(field.path)" class="dot live" :title="t('config.dirtyTooltip')" />
                {{ fieldLabel(field) }}
                <span v-if="entry.effect === 'mixed'" class="badge" :class="field.effect === 'restart' ? 'badge-restart' : 'badge-save'">
                  {{ effectLabel(field.effect) }}
                </span>
                <span v-if="field.writable === false" class="badge">
                  <AppIcon name="lock" :size="10" />
                  {{ t('config.locked') }}
                </span>
                <span v-if="field.advanced" class="badge badge-muted">{{ t('config.advancedBadge') }}</span>
              </label>
              <details v-if="fieldHint(field) && hintIsLong(field)" :id="`h-${field.path}`" class="cfg-hint">
                <summary :title="fieldHint(field)">
                  <span class="cfg-hint-text">{{ fieldHint(field) }}</span>
                  <AppIcon name="chevronDown" :size="12" class="cfg-hint-chevron" />
                </summary>
              </details>
              <p v-else-if="fieldHint(field)" :id="`h-${field.path}`" class="field-hint cfg-hint-short">{{ fieldHint(field) }}</p>
              <div v-if="showTech || field.min != null || field.max != null || fieldUnit(field) || field.nullable" class="field-meta-line">
                <span v-if="field.min != null || field.max != null">
                  {{
                    t('config.range', {
                      min: field.min ?? '—',
                      max: field.max ?? '—',
                      unit: fieldUnit(field) ? ` ${fieldUnit(field)}` : '',
                    })
                  }}
                </span>
                <span v-else-if="fieldUnit(field)">{{ t('config.unitLabel', { unit: fieldUnit(field) }) }}</span>
                <span v-if="field.nullable" class="badge badge-muted">{{ t('common.optional') }}</span>
                <span v-if="showTech" class="cfg-path">{{ field.path }}</span>
              </div>
            </div>

            <div class="field-control">
              <details v-if="field.writable === false && isComplex(field)" class="cfg-readonly">
                <summary class="cfg-readonly-summary">
                  <span class="badge" :title="t('config.lockedHint')">
                    <AppIcon name="lock" :size="11" />
                    {{ t('config.locked') }}
                  </span>
                  <span>{{ t('config.itemsCount', { n: complexCount(field) }) }}</span>
                  <span class="cfg-readonly-more">{{ t('config.viewRaw') }}</span>
                </summary>
                <pre class="cfg-readonly-json">{{ prettyValue(field) }}</pre>
              </details>
              <div v-else-if="field.writable === false" class="cfg-readonly">
                <span class="mono">{{ displayValue(field) }}</span>
                <span class="badge" :title="t('config.lockedHint')">
                  <AppIcon name="lock" :size="11" />
                  {{ t('config.locked') }}
                </span>
              </div>

              <template v-else-if="field.kind === 'bool'">
                <label class="switch">
                  <input
                    :id="`f-${field.path}`"
                    v-model="draft[field.path]"
                    type="checkbox"
                    :aria-describedby="fieldHint(field) ? `h-${field.path}` : undefined"
                  />
                  <span class="track" />
                  <span class="switch-text">{{ draft[field.path] ? t('common.on') : t('common.off') }}</span>
                </label>
              </template>

              <template v-else-if="field.kind === 'select' && (field.options?.length ?? 0) > 0">
                <select
                  :id="`f-${field.path}`"
                  :value="draft[field.path]"
                  :class="{ invalid: errors[field.path] }"
                  :aria-invalid="Boolean(errors[field.path])"
                  :aria-describedby="fieldHint(field) ? `h-${field.path}` : undefined"
                  @change="setValue(field, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="option in field.options ?? []" :key="optionValue(option)" :value="optionValue(option)">
                    {{ optionLabel(option) }}
                  </option>
                </select>
              </template>

              <template v-else-if="field.kind === 'number'">
                <input
                  :id="`f-${field.path}`"
                  :value="asInput(draft[field.path])"
                  type="number"
                  :min="field.min ?? undefined"
                  :max="field.max ?? undefined"
                  :class="{ invalid: errors[field.path] }"
                  :aria-invalid="Boolean(errors[field.path])"
                  :aria-describedby="fieldHint(field) ? `h-${field.path}` : undefined"
                  @input="setValue(field, ($event.target as HTMLInputElement).value)"
                />
              </template>

              <template v-else-if="field.kind === 'textarea'">
                <textarea
                  :id="`f-${field.path}`"
                  :value="asInput(draft[field.path])"
                  rows="3"
                  :class="{ invalid: errors[field.path] }"
                  :aria-invalid="Boolean(errors[field.path])"
                  :aria-describedby="fieldHint(field) ? `h-${field.path}` : undefined"
                  @input="setValue(field, ($event.target as HTMLTextAreaElement).value)"
                />
              </template>

              <template v-else-if="field.kind === 'list'">
                <div class="chips" :class="{ invalid: errors[field.path] }">
                  <span v-for="item in listOf(field)" :key="item" class="chip">
                    {{ item }}
                    <button type="button" :aria-label="t('config.listRemove', { value: item })" @click="setList(field, 'remove', item)">
                      ×
                    </button>
                  </span>
                  <input
                    :id="`f-${field.path}`"
                    v-model="listDrafts[field.path]"
                    class="chip-input"
                    :placeholder="t('config.listPlaceholder')"
                    @keydown.enter.prevent="setList(field, 'add', listDrafts[field.path] ?? '')"
                  />
                </div>
              </template>

              <template v-else>
                <input
                  :id="`f-${field.path}`"
                  :value="asInput(draft[field.path])"
                  type="text"
                  :class="{ invalid: errors[field.path] }"
                  :aria-invalid="Boolean(errors[field.path])"
                  :aria-describedby="fieldHint(field) ? `h-${field.path}` : undefined"
                  @input="setValue(field, ($event.target as HTMLInputElement).value)"
                />
              </template>

              <div v-if="field.audition" class="cfg-audition">
                <button
                  type="button"
                  class="btn btn-sm"
                  :disabled="auditionState[field.path] === 'loading' || auditionState[field.path] === 'speaker'"
                  @click="auditionState[field.path] === 'playing' ? stopAudition() : playAudition(field)"
                >
                  <AppIcon :name="auditionState[field.path] === 'playing' ? 'stop' : 'play'" :size="13" />
                  {{
                    auditionState[field.path] === 'playing'
                      ? t('config.auditionStop')
                      : auditionState[field.path] === 'loading'
                        ? t('config.auditionLoading')
                        : auditionState[field.path] === 'speaker'
                          ? t('providers.auditionSpeakerPlaying')
                          : t('config.audition')
                  }}
                </button>
                <span v-if="auditionNotice" class="ok small">{{ auditionNotice }}</span>
                <span v-if="auditionError" class="fail small">{{ auditionError }}</span>
              </div>

              <p v-if="errors[field.path]" class="field-error">{{ errors[field.path] }}</p>
            </div>
          </div>
        </div>
      </section>

      <section v-if="secretRows.length" class="cfg-group">
        <header class="cfg-group-head">
          <span class="cfg-index">{{ String(renderedGroups.length + 1).padStart(2, '0') }}</span>
          <div class="cfg-group-title">
            <h2>{{ t('config.secretTitle') }}</h2>
            <p class="field-hint">{{ t('config.secretHint') }}</p>
          </div>
          <div class="cfg-group-badges">
            <span class="badge">{{ secretRows.length }}</span>
            <RouterLink class="cfg-ref-link" to="/secrets">{{ t('config.manageSecrets') }}</RouterLink>
          </div>
        </header>
        <div class="cfg-fields">
          <div v-for="secret in secretRows" :key="secret.name" class="field-row">
            <div class="field-meta">
              <div class="field-label">
                <AppIcon name="lock" :size="14" />
                <span>{{ secretDisplayName(secret.name) }}</span>
              </div>
              <p class="field-hint">
                {{ t('secrets.envName') }}:
                <code class="mono">{{ secret.name }}</code>
              </p>
            </div>
            <div class="field-control">
              <div class="secret-line">
                <span class="badge" :class="secret.has ? 'badge-ok' : 'badge-warn'">
                  {{ secret.has ? t('config.secretConfigured') : t('config.secretMissing') }}
                </span>
                <input
                  :id="`secret-${secret.name}`"
                  v-model="secretDrafts[secret.name]"
                  type="password"
                  autocomplete="new-password"
                  spellcheck="false"
                  :placeholder="t('secrets.placeholder')"
                  :aria-label="secretDisplayName(secret.name)"
                  @keyup.enter="saveSecret(secret.name)"
                />
                <button
                  type="button"
                  class="btn btn-sm"
                  :disabled="secretBusy[secret.name] || !(secretDrafts[secret.name] ?? '').trim()"
                  @click="saveSecret(secret.name)"
                >
                  {{ secretBusy[secret.name] ? t('common.saving') : t('config.secretWrite') }}
                </button>
              </div>
              <p v-if="secretMsgs[secret.name]" class="field-hint">{{ secretMsgs[secret.name] }}</p>
            </div>
          </div>
        </div>
      </section>

      <p v-if="totalVisible" class="cfg-count meta">{{ t('config.fieldCount', { n: totalVisible }) }}</p>

      <div v-if="referenceLinks.length" class="cfg-refs">
        <span class="kicker">{{ t('config.reference') }}</span>
        <RouterLink v-for="link in referenceLinks" :key="link.href" :to="link.href" class="cfg-ref-link">
          {{ link.label }}
          <AppIcon name="arrowRight" :size="13" />
        </RouterLink>
      </div>

      <div v-if="saveError" class="banner banner-fail cfg-result">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>{{ saveError }}</span>
      </div>

      <div
        v-if="savedInfo"
        ref="resultRef"
        class="banner"
        :class="savedInfo.restart ? 'banner-warn' : 'banner-ok'"
        role="status"
      >
        <AppIcon :name="savedInfo.restart ? 'alert' : 'check'" :size="16" class="banner-icon" />
        <div>
          <strong>
            {{ savedInfo.restart
              ? t('config.savedRestart', { n: savedInfo.restartFields.length || restartCount })
              : t('config.savedInstant') }}
          </strong>
          <template v-if="savedInfo.restart">
            <p class="cfg-restart-fields">{{ t('config.restart.fields') }}</p>
            <div class="chips">
              <span v-for="path in restartFieldsShown" :key="path" class="chip">{{ cfgFieldLabel(path) }}</span>
              <span v-if="restartFieldsMore" class="chip">+{{ restartFieldsMore }}</span>
            </div>
            <p class="field-hint">{{ t('config.restart.command') }}</p>
            <div class="cfg-command">
              <code>{{ restartCommand }}</code>
              <button type="button" class="btn btn-sm" @click="copyCommand">
                {{ copied ? t('common.copied') : t('config.copyCommand') }}
              </button>
            </div>
            <p v-if="canRestartAudio" class="field-hint">{{ t('config.restart.note') }}</p>
            <div v-if="canRestartAudio" class="actions">
              <button type="button" class="btn btn-sm" :disabled="restarting" @click="restartAudio">
                {{ restarting ? t('config.restart.audioBusy') : t('config.restart.audio') }}
              </button>
              <span v-if="restartOk" class="ok small">{{ t('config.restart.audioOk') }}</span>
              <span v-if="restartError" class="fail small">{{ t('config.restart.audioFail', { message: restartError }) }}</span>
            </div>
          </template>
          <template v-if="savedInfo.warnings.length">
            <p class="cfg-restart-fields">{{ t('config.warnings') }}</p>
            <ul class="cfg-warning-list">
              <li v-for="(warning, i) in savedInfo.warnings" :key="i">{{ localizeBackend(warning) }}</li>
            </ul>
          </template>
        </div>
      </div>

      <div class="cfg-savebar" :class="{ dirty: isDirty }">
        <div class="cfg-save-summary">
          <span v-if="saveError" class="fail small">{{ saveError }}</span>
          <span v-else-if="isDirty" class="mono">
            {{ t('config.modified', { n: dirtyPaths.length }) }}
            <span class="cfg-sep">·</span>
            {{ t('config.instant', { n: instantCount }) }}
            <span class="cfg-sep">·</span>
            {{ t('config.restartCount', { n: restartCount }) }}
          </span>
          <span v-else class="muted small">{{ t('config.noChanges') }}</span>
        </div>
        <div class="actions">
          <button type="button" class="btn btn-ghost" :disabled="!isDirty || saving" @click="revert">
            {{ t('config.revert') }}
          </button>
          <button type="button" class="btn btn-primary" :disabled="!isDirty || saving" @click="save">
            {{ saving ? t('config.saving') : t('config.save') }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.config-center {
  min-width: 0;
}

.cfg-loading {
  padding: var(--s5) 0;
}

.cfg-unavailable {
  margin-bottom: var(--s5);
}

.cfg-unavailable-title {
  font-size: 18px;
  margin-bottom: 4px;
}

.cfg-error-detail {
  margin-top: 8px;
  word-break: break-word;
}

.cfg-toolbar {
  position: sticky;
  top: 38px;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--s3);
  flex-wrap: wrap;
  padding: 10px 0 12px;
  margin-bottom: var(--s2);
  border-bottom: 1px solid var(--line);
  background: var(--bg);
}

.cfg-toolbar-tools {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  min-width: 0;
}

.cfg-filter {
  width: 200px;
  min-height: 32px;
  padding: 5px 9px;
}

.cfg-advanced {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
  white-space: nowrap;
}

/* — compact field rows: the default view is a ledger, not a form dump ————— */
.cfg-group {
  margin-top: var(--s5);
  scroll-margin-top: 90px;
}

.cfg-group-head {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  gap: var(--s2) var(--s3);
  padding-bottom: 10px;
  align-items: start;
  border-bottom: 1px solid var(--line-strong);
}

.cfg-group-title h2 {
  font-size: 17px;
}

.cfg-group-title .field-hint {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-top: 2px;
  line-height: 1.45;
}

.cfg-group-count {
  color: var(--muted);
  font-size: 11px;
}

.cfg-index {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
  letter-spacing: 0.06em;
  font-variant-numeric: tabular-nums;
  padding-top: 3px;
}

.cfg-fields .field-row {
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr);
  gap: 4px var(--s4);
  padding: 8px 0;
}

.cfg-fields .field-label {
  gap: 6px;
  font-size: 13.5px;
}

.cfg-fields .field-control {
  max-width: 440px;
}

.cfg-fields .field-meta-line {
  margin-top: 3px;
  gap: 6px;
  font-size: 11px;
}

.cfg-audition-output {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
}

.cfg-audition-choice {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 3px 7px;
  border-radius: var(--r-xs);
  font-size: 10.5px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
}

.cfg-audition-choice:hover {
  color: var(--ink);
}

.cfg-audition-choice.on {
  background: var(--accent-soft);
  color: var(--accent);
}

.cfg-hint {
  margin-top: 2px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.5;
}

.cfg-hint summary {
  display: flex;
  align-items: flex-start;
  gap: 3px;
  cursor: pointer;
  list-style: none;
}

.cfg-hint summary::-webkit-details-marker {
  display: none;
}

.cfg-hint-text {
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-width: 0;
  overflow-wrap: anywhere;
}

.cfg-hint-chevron {
  flex: none;
  margin-top: 3px;
  transition: transform var(--dur-fast) var(--ease);
}

.cfg-hint[open] .cfg-hint-text {
  display: block;
  overflow: visible;
}

.cfg-hint[open] .cfg-hint-chevron {
  transform: rotate(180deg);
}

.cfg-hint-short {
  margin-top: 2px;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.cfg-advanced input {
  width: 15px;
  height: 15px;
  min-height: 0;
  padding: 0;
}

.cfg-dirty {
  white-space: nowrap;
}

.cfg-indexbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: var(--s3) 0 0;
}

.cfg-indexchip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  font-size: 11.5px;
  color: var(--ink-2);
  white-space: nowrap;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.cfg-indexchip:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.cfg-indexnum {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.06em;
  color: var(--accent);
}

.cfg-warning {
  margin: var(--s4) 0;
  font-size: 12.5px;
}

.cfg-warning p {
  margin-top: 3px;
  color: var(--ink-2);
}

.cfg-empty {
  padding: var(--s6) 0;
}

.cfg-group-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.cfg-fields {
  margin-top: 2px;
}

.cfg-path {
  opacity: 0.75;
  overflow-wrap: anywhere;
}

details.cfg-readonly {
  display: block;
}

.cfg-readonly-summary {
  display: flex;
  align-items: center;
  gap: var(--s2);
  cursor: pointer;
  font-size: 12px;
  color: var(--ink-2);
  list-style: none;
}

.cfg-readonly-summary::-webkit-details-marker {
  display: none;
}

.cfg-readonly-more {
  margin-left: auto;
  color: var(--accent);
  font-size: 11.5px;
}

.cfg-readonly-json {
  margin: 0;
  font: 11.5px/1.5 var(--font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 200px;
  overflow: auto;
  flex: 1 1 100%;
}

.secret-line {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.secret-line input {
  flex: 1;
  min-width: 180px;
  max-width: 360px;
  font-family: var(--font-mono);
  font-size: 12.5px;
}

.cfg-warning-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.cfg-readonly {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  min-height: 36px;
  padding: 7px 10px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  font-size: 12.5px;
  overflow-wrap: anywhere;
}

.chips.invalid {
  outline: 1px solid var(--fail);
  border-radius: var(--r-sm);
  padding: 4px;
}

.cfg-count {
  margin-top: var(--s5);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.cfg-refs {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: var(--s4);
}

.cfg-ref-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  font-size: 12.5px;
}

.cfg-ref-link:hover {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.cfg-result {
  margin-top: var(--s5);
}

.cfg-restart-fields {
  margin-top: 8px;
  color: var(--ink-2);
  font-size: 12.5px;
}

.cfg-command {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin: 6px 0 8px;
}

.cfg-command code {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  padding: 5px 9px;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.cfg-save-inline {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding-left: var(--s3);
  border-left: 1px solid var(--line);
}

.cfg-inline-count {
  font-size: 11px;
  color: var(--ink-2);
  white-space: nowrap;
}

.cfg-inline-error {
  max-width: 320px;
}

/* The save controls live in the sticky toolbar on desktop (always on screen while
 * editing); on a phone the same state becomes a fixed bottom bar. */
.cfg-savebar {
  display: none;
}

.cfg-save-summary {
  font-size: 12px;
  color: var(--ink-2);
}

.cfg-sep {
  color: var(--muted);
  margin: 0 4px;
}

@media (max-width: 720px) {
  .cfg-toolbar {
    position: static;
    padding-top: 0;
  }

  .cfg-filter {
    width: 100%;
    flex: 1 1 100%;
  }

  .cfg-indexbar {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 4px;
    scrollbar-width: none;
  }

  .cfg-indexchip {
    min-height: 44px;
    padding: 8px 10px;
  }

  .cfg-indexbar::-webkit-scrollbar {
    display: none;
  }

  .cfg-group-head {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: var(--s2);
  }

  .cfg-fields .field-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 3px;
    padding: 10px 0;
  }

  .cfg-group-badges {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }

  .cfg-index {
    font-size: 11px;
  }

  .cfg-group-title h2 {
    font-size: 19px;
  }

  .cfg-save-inline {
    display: none;
  }

  .cfg-savebar.dirty {
    position: fixed;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--s3);
    flex-wrap: wrap;
    left: 0;
    right: 0;
    bottom: 0;
    margin: 0;
    border-radius: 0;
    border-left: 0;
    border-right: 0;
    border-bottom: 0;
    border-top: 1px solid var(--accent);
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
    box-shadow: var(--shadow-1);
    background: var(--surface);
    z-index: 30;
  }

  .cfg-save-summary {
    flex: 1 1 100%;
  }

  .cfg-savebar .actions {
    width: 100%;
  }

  .cfg-savebar .actions .btn {
    flex: 1;
  }
}
</style>

<style scoped>
.cfg-audition {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin-top: var(--s2);
  flex-wrap: wrap;
}

.cfg-audition .fail {
  color: var(--fail);
  overflow-wrap: anywhere;
}
</style>
