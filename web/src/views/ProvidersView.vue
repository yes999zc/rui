<script setup lang="ts">
/**
 * Dedicated Providers page (replaces the generic config-center dump at
 * /panel/providers).
 *
 * Data path stays exactly the one the backend owns:
 *   GET  /api/v1/config/catalog        → fields, values, secrets, restart command
 *   GET  /api/v1/providers/voices      → per-provider voice lists (labels)
 *   POST /api/v1/providers/{stage}/{name}/audition → draft voice preview
 *   PUT  /api/v1/config/values         → save chain order + fields
 *
 * Three stages (LLM / ASR / TTS) are composed as: chain rail on the left, the
 * provider cards that actually serve the chain on the right. Advanced fields are
 * collapsed per provider; every save is followed by an explicit "restart to
 * apply" note, because provider construction happens at startup.
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import ChainEditor from '../components/providers/ChainEditor.vue'
import ProviderCard from '../components/providers/ProviderCard.vue'
import ProviderCreateDialog from '../components/providers/ProviderCreateDialog.vue'
import ProviderKeyStrip from '../components/providers/ProviderKeyStrip.vue'
import {
  api,
  ApiError,
  type CatalogDoc,
  type CreateProviderResult,
  type ProviderPresetsDoc,
  type ProviderStatusDoc,
  type ProviderStatusRow,
} from '../api'
import { localizeIssue, providerName, t } from '../i18n'
import {
  allowedForStage,
  chainPath,
  coerceValue,
  EXCLUDED,
  kindLabel,
  providerSecrets,
  STAGES,
  stagePurpose,
  stageShort,
  stageTitle,
  usedStages,
  type ProviderDef,
  type ProviderModel,
  type SecretMeta,
  type Stage,
  type VoicesDoc,
  buildProviderModel,
} from '../components/providers/types'

defineProps<{ panelId?: string }>()

type StageState = 'idle' | 'saving' | 'saved' | 'error'

const doc = ref<CatalogDoc | null>(null)
const voicesDoc = ref<VoicesDoc>({})
const presetsDoc = ref<ProviderPresetsDoc | null>(null)
const statusDoc = ref<ProviderStatusDoc | null>(null)
const loading = ref(true)
const loadError = ref('')
const createOpen = ref(false)
const notice = ref('')

const fieldDraft = ref<Record<string, unknown>>({})
const fieldBaseline = ref<Record<string, unknown>>({})
const chainDraft = ref<Record<Stage, string[]>>({ llm: [], asr: [], tts: [] })
const chainBaseline = ref<Record<Stage, string[]>>({ llm: [], asr: [], tts: [] })
/** Chain as the *running* server built it; audition only works for these until restart. */
const serverChains = ref<Record<Stage, string[]>>({ llm: [], asr: [], tts: [] })

const errors = reactive<Record<Stage, Record<string, string>>>({ llm: {}, asr: {}, tts: {} })
const chainErrors = reactive<Record<Stage, string>>({ llm: '', asr: '', tts: '' })
const stageState = reactive<Record<Stage, { state: StageState; message: string; fields: number }>>({
  llm: { state: 'idle', message: '', fields: 0 },
  asr: { state: 'idle', message: '', fields: 0 },
  tts: { state: 'idle', message: '', fields: 0 },
})
const globalError = ref('')
const copiedStage = ref<Stage | ''>('')
let copyTimer: number | undefined

function clone<T>(value: T): T {
  if (value == null || typeof value !== 'object') return value
  return JSON.parse(JSON.stringify(value)) as T
}

function asArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

const EMPTY_MODEL: ProviderModel = { defs: [], byName: new Map(), chains: { llm: [], asr: [], tts: [] } }

const model = computed<ProviderModel>(() => (doc.value ? buildProviderModel(doc.value) : EMPTY_MODEL))
const defs = computed(() => model.value.defs)
const fieldByPath = computed(() => new Map((doc.value?.fields ?? []).map((field) => [field.path, field])))
const secrets = computed(
  () => (doc.value?.secrets ?? {}) as unknown as Record<string, SecretMeta>,
)
const providerKeyList = computed(() => providerSecrets(secrets.value))
const restartCommand = computed(() => doc.value?.restart_command || 'docker compose restart')
const editableCount = computed(
  () =>
    (doc.value?.fields ?? []).filter(
      (field) =>
        field.path.startsWith('providers.catalog.') &&
        field.writable !== false &&
        !field.path.endsWith('.kind'),
    ).length,
)

const statusByKey = computed(() => {
  const map = new Map<string, ProviderStatusRow>()
  for (const stage of STAGES) {
    for (const row of statusDoc.value?.stages?.[stage]?.providers ?? []) {
      map.set(`${stage}:${row.name}`, row)
    }
  }
  return map
})

function statusFor(stage: Stage, name: string): ProviderStatusRow | undefined {
  return statusByKey.value.get(`${stage}:${name}`)
}

function stageStatus(stage: Stage) {
  return statusDoc.value?.stages?.[stage]
}

const allowed = computed<Record<Stage, string[]>>(() => ({
  llm: allowedForStage(model.value, 'llm'),
  asr: allowedForStage(model.value, 'asr'),
  tts: allowedForStage(model.value, 'tts'),
}))

/** Providers that currently serve each draft chain, in order. */
const stageCards = computed<Record<Stage, ProviderDef[]>>(() => {
  const out: Record<Stage, ProviderDef[]> = { llm: [], asr: [], tts: [] }
  for (const stage of STAGES) {
    for (const name of chainDraft.value[stage]) {
      // Retired providers stay in the chain until the operator removes them, but
      // they never get a card: the console no longer offers them (SiliconFlow TTS).
      if (EXCLUDED[stage].includes(name)) continue
      const def = model.value.byName.get(name)
      if (def) out[stage].push(def)
    }
  }
  return out
})

const unusedDefs = computed(() =>
  defs.value.filter((def) => STAGES.every((stage) => !chainDraft.value[stage].includes(def.name))),
)

function voicesFor(name: string) {
  return voicesDoc.value.tts?.[name] ?? []
}

/** A provider can only be auditioned once the running service knows it (post-restart). */
function auditionReady(name: string): boolean {
  return serverChains.value.tts.includes(name)
}

function modelSummaryOf(def: ProviderDef): string {
  const field = def.fields.find((item) => item.path === `providers.catalog.${def.name}.model`)
  const value = field ? fieldDraft.value[field.path] : ''
  return value == null || value === '' ? '' : String(value)
}

function usedNames(def: ProviderDef): string {
  return usedStages(chainDraft.value, def.name)
    .map((stage) => stageShort(stage))
    .join(' / ')
}

function stageFieldPaths(stage: Stage): string[] {
  const paths: string[] = []
  for (const name of chainDraft.value[stage]) {
    const def = model.value.byName.get(name)
    if (!def) continue
    for (const field of def.fields) paths.push(field.path)
  }
  return paths
}

function stageDirtyFields(stage: Stage): string[] {
  return stageFieldPaths(stage).filter(
    (path) => JSON.stringify(fieldDraft.value[path]) !== JSON.stringify(fieldBaseline.value[path]),
  )
}

function chainDirty(stage: Stage): boolean {
  return JSON.stringify(chainDraft.value[stage]) !== JSON.stringify(chainBaseline.value[stage])
}

function stageDirtyCount(stage: Stage): number {
  return stageDirtyFields(stage).length + (chainDirty(stage) ? 1 : 0)
}

const anyDirty = computed(() => STAGES.some((stage) => stageDirtyCount(stage) > 0))

function isSaving(stage: Stage): boolean {
  return stageState[stage].state === 'saving'
}

function stageOfProvider(name: string): Stage | undefined {
  return model.value.byName.get(name)?.stage ?? undefined
}

function markEdited(stage: Stage): void {
  if (stageState[stage].state === 'saved') {
    stageState[stage] = { state: 'idle', message: '', fields: 0 }
  }
  globalError.value = ''
}

function updateChain(stage: Stage, next: string[]): void {
  const removed = chainDraft.value[stage].filter((name) => !next.includes(name))
  for (const name of removed) {
    const def = model.value.byName.get(name)
    if (!def) continue
    for (const field of def.fields) fieldDraft.value[field.path] = clone(fieldBaseline.value[field.path])
  }
  chainDraft.value = { ...chainDraft.value, [stage]: next }
  chainErrors[stage] = ''
  markEdited(stage)
}

function updateField(stage: Stage, path: string, value: unknown): void {
  fieldDraft.value = { ...fieldDraft.value, [path]: value }
  delete errors[stage][path]
  markEdited(stage)
}

function resetStage(stage: Stage): void {
  const union = new Set([...chainBaseline.value[stage], ...chainDraft.value[stage]])
  for (const name of union) {
    const def = model.value.byName.get(name)
    if (!def) continue
    for (const field of def.fields) fieldDraft.value[field.path] = clone(fieldBaseline.value[field.path])
  }
  chainDraft.value = { ...chainDraft.value, [stage]: clone(chainBaseline.value[stage]) }
  errors[stage] = {}
  chainErrors[stage] = ''
  stageState[stage] = { state: 'idle', message: '', fields: 0 }
  globalError.value = ''
}

function setPrimary(stage: Stage, name: string): void {
  const chain = chainDraft.value[stage]
  if (!chain.length || chain[0] === name) return
  updateChain(stage, [name, ...chain.filter((item) => item !== name)])
  notice.value = t('providers.action.reorderHint')
}

function removeProvider(stage: Stage, name: string): void {
  const chain = chainDraft.value[stage]
  if (chain.length <= 1) return
  updateChain(stage, chain.filter((item) => item !== name))
  notice.value = t('providers.action.reorderHint')
}

function addUnused(def: ProviderDef): void {
  if (!def.stage) return
  chainDraft.value = { ...chainDraft.value, [def.stage]: [...chainDraft.value[def.stage], def.name] }
  markEdited(def.stage)
}

function stageFromLoc(loc: string[]): Stage | undefined {
  const joined = loc.join('.')
  const match = joined.match(/providers\.(llm|asr|tts)\./)
  if (match) return match[1] as Stage
  const last = loc[loc.length - 1]
  if (last && model.value.byName.has(last)) return stageOfProvider(last)
  return undefined
}

function fieldPathFromLoc(loc: string[]): string | undefined {
  const known = fieldByPath.value
  for (let index = 0; index < loc.length; index += 1) {
    const candidate = loc.slice(index).join('.')
    if (known.has(candidate)) return candidate
  }
  const last = loc[loc.length - 1]
  if (!last) return undefined
  return [...known.keys()].find((path) => path.endsWith(`.${last}`))
}

function applyIssues(issues: { loc?: (string | number)[]; msg: string }[], fallback: string): string {
  let summary = ''
  for (const issue of issues) {
    const loc = (issue.loc ?? []).map(String)
    const message = localizeIssue(issue.msg)
    summary = summary || message
    const isChain = loc.some((part) => part === 'chain')
    if (isChain) {
      const stage = stageFromLoc(loc)
      if (stage) chainErrors[stage] = message
      else globalError.value = message
      continue
    }
    const path = fieldPathFromLoc(loc)
    if (path) {
      const field = fieldByPath.value.get(path)
      const stage = field ? stageOfProvider(path.split('.')[2]) : undefined
      if (stage) errors[stage][path] = message
      else globalError.value = message
      continue
    }
    const stage = stageFromLoc(loc)
    if (stage) errors[stage]['*'] = message
    else globalError.value = message
  }
  return summary || fallback
}

async function saveStage(stage: Stage): Promise<void> {
  if (isSaving(stage)) return
  const dirtyFields = stageDirtyFields(stage)
  if (!dirtyFields.length && !chainDirty(stage)) return
  stageState[stage] = { state: 'saving', message: '', fields: 0 }
  errors[stage] = {}
  chainErrors[stage] = ''
  globalError.value = ''

  const payload: Record<string, unknown> = {}
  if (chainDirty(stage)) payload[chainPath(stage)] = chainDraft.value[stage]
  for (const path of dirtyFields) {
    const field = fieldByPath.value.get(path)
    if (field) payload[path] = coerceValue(field, fieldDraft.value[path])
  }

  try {
    const result = await api.saveConfigValues(payload)
    if (chainDirty(stage)) chainBaseline.value = { ...chainBaseline.value, [stage]: clone(chainDraft.value[stage]) }
    for (const path of dirtyFields) fieldBaseline.value = { ...fieldBaseline.value, [path]: clone(fieldDraft.value[path]) }
    const restartFields = result.restart_fields?.length ?? 0
    stageState[stage] = {
      state: 'saved',
      message: result.restart_required ? t('providers.savedRestart') : t('providers.saved'),
      fields: restartFields,
    }
    await refreshStatus()
    await nextTick()
    document.getElementById(`stage-${stage}`)?.scrollIntoView({
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    })
  } catch (exc) {
    let message = exc instanceof Error ? exc.message : String(exc)
    if (exc instanceof ApiError) {
      message = applyIssues(exc.issues as { loc?: (string | number)[]; msg: string }[], message)
    }
    stageState[stage] = {
      state: 'error',
      message: t('providers.saveFailed', { message }),
      fields: 0,
    }
    await nextTick()
    const root = document.getElementById(`stage-${stage}`)
    const target = root?.querySelector<HTMLElement>('.field-error, .invalid input, .invalid select')
    target?.scrollIntoView({ behavior: 'auto', block: 'center' })
  }
}

async function refreshStatus(): Promise<void> {
  try {
    statusDoc.value = await api.providerStatus()
  } catch {
    // Badges are additive: an older server (or a transient error) must not hide the editor.
  }
}

async function copyCommand(stage: Stage): Promise<void> {
  try {
    await navigator.clipboard.writeText(restartCommand.value)
    copiedStage.value = stage
    if (copyTimer) window.clearTimeout(copyTimer)
    copyTimer = window.setTimeout(() => { copiedStage.value = '' }, 1800)
  } catch {
    copiedStage.value = ''
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [catalog, voices, presets, status] = await Promise.all([
      api.catalog(),
      api.json<VoicesDoc>('/api/v1/providers/voices').catch(() => ({ tts: {}, asr: {} })),
      api.providerPresets().catch(() => null),
      api.providerStatus().catch(() => null),
    ])
    doc.value = catalog
    voicesDoc.value = voices
    presetsDoc.value = presets
    statusDoc.value = status
    fieldBaseline.value = clone(catalog.values)
    fieldDraft.value = clone(catalog.values)
    const chains: Record<Stage, string[]> = {
      llm: asArray(catalog.values[chainPath('llm')]),
      asr: asArray(catalog.values[chainPath('asr')]),
      tts: asArray(catalog.values[chainPath('tts')]),
    }
    chainBaseline.value = clone(chains)
    chainDraft.value = clone(chains)
    serverChains.value = clone(chains)
    for (const stage of STAGES) {
      errors[stage] = {}
      chainErrors[stage] = ''
      stageState[stage] = { state: 'idle', message: '', fields: 0 }
    }
    globalError.value = ''
  } catch (exc) {
    loadError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

async function onCreated(result: CreateProviderResult): Promise<void> {
  createOpen.value = false
  const name = result.provider?.name ?? ''
  notice.value = t('providers.create.created', { name })
  await load()
  if (!result.provider?.stage) return
  await nextTick()
  document.getElementById(`stage-${result.provider.stage}`)?.scrollIntoView({
    behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'start',
  })
}

onMounted(load)

onUnmounted(() => {
  if (copyTimer) window.clearTimeout(copyTimer)
})

onBeforeRouteLeave(() => (anyDirty.value ? window.confirm(t('providers.unsavedLeave')) : true))
</script>

<template>
  <div class="providers-page">
    <div v-if="loading" class="providers-loading" role="status">
      <div class="skeleton skeleton-line" style="width: 46%" />
      <div class="skeleton skeleton-line" style="width: 72%" />
      <div class="skeleton skeleton-line" style="width: 30%" />
      <p class="note">{{ t('providers.loading') }}</p>
    </div>

    <div v-else-if="loadError" class="banner banner-fail">
      <AppIcon name="alert" :size="18" class="banner-icon" />
      <div>
        <p>{{ t('providers.loadError', { message: loadError }) }}</p>
        <button type="button" class="btn btn-sm" style="margin-top: 10px" @click="load">{{ t('providers.retry') }}</button>
      </div>
    </div>

    <template v-else>
      <p class="lede providers-lede">{{ t('providers.lede') }}</p>

      <div class="providers-bar">
        <span class="meta">{{ t('providers.catalogMeta', { providers: defs.length, fields: editableCount }) }}</span>
        <div class="providers-bar-actions">
          <ProviderKeyStrip :secrets="providerKeyList" />
          <button type="button" class="btn btn-sm btn-primary" @click="createOpen = true">
            <AppIcon name="plus" :size="13" />
            {{ t('providers.create.open') }}
          </button>
        </div>
      </div>

      <div v-if="notice" class="banner banner-ok" role="status">
        <AppIcon name="check" :size="16" class="banner-icon" />
        <span>{{ notice }}</span>
        <button type="button" class="btn btn-sm" style="margin-left: auto" @click="notice = ''">
          <AppIcon name="close" :size="12" />
        </button>
      </div>

      <div v-if="globalError" class="banner banner-fail" role="alert">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>{{ globalError }}</span>
      </div>

      <section v-for="(stage, index) in STAGES" :id="`stage-${stage}`" :key="stage" class="stage">
        <header class="stage-head">
          <span class="stage-index mono">{{ String(index + 1).padStart(2, '0') }}</span>
          <div class="stage-title-block">
            <h2>{{ stageTitle(stage) }}</h2>
            <p class="stage-purpose">{{ stagePurpose(stage) }}</p>
          </div>
          <div class="stage-actions">
            <span v-if="stageStatus(stage)" class="badge" :class="stageStatus(stage)?.satisfied ? 'badge-accent' : 'badge-warn'">
              {{ t('providers.status.ready') }} {{ stageStatus(stage)?.ready.length ?? 0 }}/{{ chainDraft[stage].length }}
            </span>
            <span class="badge badge-restart">{{ t('providers.effect.restart') }}</span>
            <span v-if="stageDirtyCount(stage)" class="badge badge-warn">
              {{ t('providers.modified', { n: stageDirtyCount(stage) }) }}
            </span>
            <button
              type="button"
              class="btn btn-sm"
              :disabled="!stageDirtyCount(stage) || isSaving(stage)"
              @click="resetStage(stage)"
            >
              {{ t('providers.reset') }}
            </button>
            <button
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="!stageDirtyCount(stage) || isSaving(stage)"
              @click="saveStage(stage)"
            >
              {{ isSaving(stage) ? t('providers.saving') : t('providers.save') }}
            </button>
          </div>
        </header>
        <p class="stage-note">{{ t('providers.effect.note') }}</p>

        <div v-if="stageState[stage].state === 'saved'" class="banner banner-warn stage-result" role="status">
          <AppIcon name="check" :size="16" class="banner-icon" />
          <div class="stage-result-body">
            <strong>{{ stageState[stage].message }}</strong>
            <span v-if="stageState[stage].fields" class="stage-result-fields">
              {{ t('providers.savedFields', { n: stageState[stage].fields }) }}
            </span>
            <div class="stage-command">
              <span class="stage-command-label">{{ t('providers.restartCommand') }}</span>
              <code>{{ restartCommand }}</code>
              <button type="button" class="btn btn-sm" @click="copyCommand(stage)">
                {{ copiedStage === stage ? t('providers.copied') : t('providers.copyCommand') }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="stageState[stage].state === 'error'" class="banner banner-fail stage-result" role="alert">
          <AppIcon name="alert" :size="16" class="banner-icon" />
          <span>{{ stageState[stage].message }}</span>
        </div>

        <div class="stage-body">
          <ChainEditor
            :stage="stage"
            :chain="chainDraft[stage]"
            :defs="defs"
            :allowed="allowed[stage]"
            :excluded="EXCLUDED[stage]"
            :error="chainErrors[stage]"
            :disabled="isSaving(stage)"
            @update:chain="updateChain(stage, $event)"
          />

          <div class="stage-cards">
            <ProviderCard
              v-for="def in stageCards[stage]"
              :key="def.name"
              :def="def"
              :stage="stage"
              :role-index="chainDraft[stage].indexOf(def.name)"
              :field-draft="fieldDraft"
              :field-baseline="fieldBaseline"
              :errors="errors[stage]"
              :secrets="secrets"
              :voices="voicesFor(def.name)"
              :audition-ready="auditionReady(def.name)"
              :status="statusFor(stage, def.name)"
              :can-remove="chainDraft[stage].length > 1"
              :disabled="isSaving(stage)"
              @update-field="(path, value) => updateField(stage, path, value)"
              @set-primary="setPrimary(stage, def.name)"
              @remove="removeProvider(stage, def.name)"
            />
            <p v-if="!stageCards[stage].length" class="empty">{{ t('providers.chain.empty') }}</p>
          </div>
        </div>
      </section>

      <details class="unused" :class="{ empty: !unusedDefs.length }">
        <summary>
          <span class="kicker">{{ t('providers.unused.title') }}</span>
          <span class="meta">{{ unusedDefs.length }}</span>
          <AppIcon name="chevronDown" :size="13" class="unused-chevron" />
        </summary>
        <p class="unused-hint">{{ t('providers.unused.hint') }}</p>
        <p v-if="!unusedDefs.length" class="empty">{{ t('providers.unused.none') }}</p>
        <div v-for="def in unusedDefs" :key="def.name" class="unused-row">
          <div class="unused-id">
            <strong>{{ providerName(def.name) }}</strong>
            <code class="mono">{{ def.name }}</code>
            <span class="badge">{{ kindLabel(def.kind) }}</span>
            <span v-if="def.stage" class="badge badge-muted">{{ usedNames(def) || stageShort(def.stage) }}</span>
          </div>
          <span class="unused-model mono">{{ modelSummaryOf(def) }}</span>
          <template v-if="def.stage && EXCLUDED[def.stage].includes(def.name)">
            <span class="badge badge-warn">{{ t('providers.unused.removed') }}</span>
            <span class="unused-note">{{ t('providers.unused.removedHint') }}</span>
          </template>
          <button
            v-else-if="def.stage"
            type="button"
            class="btn btn-sm"
            :disabled="isSaving(def.stage)"
            @click="addUnused(def)"
          >
            {{ t('providers.unused.addTo', { stage: stageShort(def.stage) }) }}
          </button>
        </div>
      </details>

      <p class="providers-foot meta">
        <RouterLink to="/secrets">{{ t('providers.key.manage') }} →</RouterLink>
        <span>·</span>
        <span>{{ restartCommand }}</span>
      </p>

      <ProviderCreateDialog
        :open="createOpen"
        :presets="presetsDoc"
        @close="createOpen = false"
        @created="onCreated"
      />
    </template>
  </div>
</template>

<style scoped>
.providers-page {
  min-width: 0;
}

.providers-loading {
  padding: var(--s5) 0;
}

.providers-lede {
  max-width: 76ch;
}

.providers-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: var(--s4);
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--line);
}

.providers-bar-actions {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-left: auto;
}

.providers-bar .p-keys {
  flex: 1 1 420px;
}

.stage {
  margin-top: var(--s7);
  scroll-margin-top: 56px;
}

.stage-head {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  gap: var(--s3);
  align-items: start;
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--line-strong);
}

.stage-index {
  font-size: 12px;
  color: var(--accent);
  letter-spacing: 0.06em;
  padding-top: 4px;
}

.stage-title-block {
  min-width: 0;
}

.stage-title-block h2 {
  /* Same as a ConfigCenter group header: the page title is the H1, the stage is a section. */
  font-size: 17px;
}

.stage-purpose {
  margin-top: 2px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.5;
  max-width: 70ch;
}

.stage-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--s2);
  flex-wrap: wrap;
}

.stage-note {
  margin-top: var(--s2);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

.stage-result {
  margin-top: var(--s3);
  font-size: 12.5px;
}

.stage-result-body {
  min-width: 0;
}

.stage-result-fields {
  display: block;
  margin-top: 2px;
  color: var(--ink-2);
  font-size: 11.5px;
}

.stage-command {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.stage-command-label {
  color: var(--muted);
  font-size: 11px;
}

.stage-command code {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  padding: 3px 7px;
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.stage-body {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: var(--s5) var(--s6);
  align-items: start;
  margin-top: var(--s4);
}

.stage-cards {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  min-width: 0;
}

.unused {
  margin-top: var(--s7);
  border-top: 1px solid var(--line-strong);
  padding-top: var(--s3);
}

.unused summary {
  display: flex;
  align-items: center;
  gap: var(--s2);
  cursor: pointer;
  list-style: none;
}

.unused summary::-webkit-details-marker {
  display: none;
}

.unused-chevron {
  color: var(--muted);
  margin-left: auto;
  transition: transform var(--dur-fast) var(--ease);
}

.unused[open] .unused-chevron {
  transform: rotate(180deg);
}

.unused-hint {
  margin-top: var(--s2);
  color: var(--muted);
  font-size: 11.5px;
}

.unused-row {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  flex-wrap: wrap;
  padding: 9px 0;
  border-top: 1px solid var(--line);
}

.unused-id {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  min-width: 0;
}

.unused-id strong {
  font-size: 13px;
}

.unused-id code {
  font-size: 11px;
  color: var(--muted);
  background: none;
  padding: 0;
}

.unused-model {
  flex: 1;
  min-width: 120px;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.unused-note {
  flex: 1 1 100%;
  color: var(--muted);
  font-size: 11px;
}

.providers-foot {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s6);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.providers-foot a {
  color: var(--accent);
}

@media (max-width: 1100px) {
  .stage-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .stage-head {
    grid-template-columns: 32px minmax(0, 1fr);
  }

  .stage-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}

@media (max-width: 720px) {
  .stage-actions .btn {
    flex: 1;
    min-height: 44px;
  }

  .providers-bar .p-keys {
    flex: 1 1 100%;
  }
}
</style>
