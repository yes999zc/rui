/**
 * Shared types and pure helpers for the dedicated Providers page.
 *
 * The page is still catalog-driven: every editable value comes from
 * `GET /api/v1/config/catalog` (dotted path + bilingual label/hint + effect), and
 * every save goes through `PUT /api/v1/config/values`. This file only turns the
 * flat field list into the three provider stages and their chains.
 */
import type { CatalogDoc, CatalogField, CatalogOption, LocalizedText } from '../../api'
import { pickLocalized, t, type MsgKey } from '../../i18n'

export type Stage = 'llm' | 'asr' | 'tts'

/** Task order (LLM / ASR / TTS), not the pipeline order. */
export const STAGES: Stage[] = ['llm', 'asr', 'tts']

export interface VoiceOption {
  value: string
  label?: LocalizedText | string
}

export interface VoicesDoc {
  tts?: Record<string, VoiceOption[]>
  asr?: Record<string, VoiceOption[]>
}

export interface SecretMeta {
  has_value: boolean
  used_by?: string[]
  hint?: LocalizedText | string
}

/**
 * Providers that stay defined for backward compatibility but are no longer
 * offered in the console (9哥: "tts删掉硅基流动").
 */
export const EXCLUDED: Record<Stage, string[]> = {
  llm: [],
  asr: [],
  tts: ['sf-tts'],
}

export interface ProviderDef {
  name: string
  stage: Stage | null
  kind: string
  /** Every catalog field for this provider (`providers.catalog.<name>.*`). */
  fields: CatalogField[]
  /** Inline fields: not advanced, and the structural `kind` is a badge instead. */
  core: CatalogField[]
  /** Collapsed troubleshooting fields. */
  advanced: CatalogField[]
}

export function isStage(value: string): value is Stage {
  return (STAGES as string[]).includes(value)
}

export function stageTitle(stage: Stage): string {
  const key = {
    llm: 'providers.stage.llm.title',
    asr: 'providers.stage.asr.title',
    tts: 'providers.stage.tts.title',
  }[stage] as MsgKey
  return t(key)
}

export function stagePurpose(stage: Stage): string {
  const key = {
    llm: 'providers.stage.llm.purpose',
    asr: 'providers.stage.asr.purpose',
    tts: 'providers.stage.tts.purpose',
  }[stage] as MsgKey
  return t(key)
}

export function stageShort(stage: Stage): string {
  return stage.toUpperCase()
}

export function chainPath(stage: Stage): string {
  return `providers.${stage}.chain`
}

export function providerPrefix(name: string): string {
  return `providers.catalog.${name}.`
}

export function fieldKey(field: CatalogField, name: string): string {
  const prefix = providerPrefix(name)
  return field.path.startsWith(prefix) ? field.path.slice(prefix.length) : field.path
}

/** The catalog labels include the provider name (`volc-tts · 音色`); the card already says it. */
export function fieldLabel(field: CatalogField, name: string): string {
  const prefix = `${name} · `
  const text = pickLocalized(field.label)
  return text.startsWith(prefix) ? text.slice(prefix.length) : text
}

export function fieldHint(field: CatalogField): string {
  return pickLocalized(field.hint)
}

export function fieldUnit(field: CatalogField): string {
  return pickLocalized(field.unit)
}

export function fieldId(field: CatalogField): string {
  return `pf-${field.path.replace(/[^a-zA-Z0-9_-]+/g, '-')}`
}

export function optionValue(option: string | number | CatalogOption): string | number {
  return typeof option === 'string' || typeof option === 'number' ? option : option.value
}

export function optionLabel(option: string | number | CatalogOption): string {
  if (typeof option === 'string' || typeof option === 'number') return String(option)
  return option.label ? pickLocalized(option.label) : String(option.value)
}

export function roleLabel(index: number): string {
  return index === 0 ? t('providers.role.primary') : t('providers.role.fallback', { n: index })
}

const KIND_KEYS: Record<string, MsgKey> = {
  openai_compat: 'providers.kind.openai',
  sherpa: 'providers.kind.sherpa',
  volc_asr: 'providers.kind.volcAsr',
  volc_tts: 'providers.kind.volcTts',
  xfyun: 'providers.kind.xfyun',
}

export function kindLabel(kind: string): string {
  const key = KIND_KEYS[kind]
  return key ? t(key) : kind || t('providers.kind.unknown')
}

const STAGE_BY_KIND: Record<string, Stage> = {
  sherpa: 'asr',
  volc_asr: 'asr',
  volc_tts: 'tts',
  xfyun: 'tts',
}

function inferStage(name: string, kind: string, chains: Record<Stage, string[]>): Stage | null {
  for (const stage of STAGES) {
    if (chains[stage].includes(name)) return stage
  }
  const suffix = name.match(/[-_](llm|asr|tts)$/i)?.[1]?.toLowerCase()
  if (suffix && isStage(suffix)) return suffix
  return STAGE_BY_KIND[kind] ?? null
}

function asArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

export interface ProviderModel {
  defs: ProviderDef[]
  byName: Map<string, ProviderDef>
  chains: Record<Stage, string[]>
}

/**
 * Turn the flat catalog into provider definitions plus their three chains.
 * Stage inference: current chain membership first, then the id suffix
 * (`ds-llm`, `volc-tts`), then the provider kind (`sherpa` → ASR).
 */
export function buildProviderModel(doc: CatalogDoc): ProviderModel {
  const chains: Record<Stage, string[]> = {
    llm: asArray(doc.values[chainPath('llm')]),
    asr: asArray(doc.values[chainPath('asr')]),
    tts: asArray(doc.values[chainPath('tts')]),
  }

  const grouped = new Map<string, CatalogField[]>()
  for (const field of doc.fields) {
    const match = field.path.match(/^providers\.catalog\.([^.]+)\./)
    if (!match) continue
    const name = match[1]
    const list = grouped.get(name) ?? []
    list.push(field)
    grouped.set(name, list)
  }

  const defs: ProviderDef[] = []
  for (const [name, fields] of grouped) {
    const kind = String(doc.values[`${providerPrefix(name)}kind`] ?? '')
    const stage = inferStage(name, kind, chains)
    const editable = fields
      .filter((field) => fieldKey(field, name) !== 'kind')
      .filter((field) => field.writable !== false)
    const core = editable.filter((field) => !field.advanced)
    const advanced = editable.filter((field) => field.advanced)
    const order = ['base_url', 'model', 'voice', 'speech_rate', 'emotion', 'api_key_env']
    const rank = (field: CatalogField) => {
      const index = order.indexOf(fieldKey(field, name))
      return index === -1 ? order.length : index
    }
    defs.push({
      name,
      stage,
      kind,
      fields,
      core: [...core].sort((a, b) => rank(a) - rank(b)),
      advanced: [...advanced].sort((a, b) => rank(a) - rank(b)),
    })
  }

  defs.sort((a, b) => {
    const sa = a.stage ? STAGES.indexOf(a.stage) : STAGES.length
    const sb = b.stage ? STAGES.indexOf(b.stage) : STAGES.length
    return sa - sb || a.name.localeCompare(b.name)
  })

  return { defs, byName: new Map(defs.map((def) => [def.name, def])), chains }
}

/** Providers that may be added to a stage (definitions minus the retired ones). */
export function allowedForStage(model: ProviderModel, stage: Stage): string[] {
  return model.defs
    .filter((def) => def.stage === stage)
    .map((def) => def.name)
    .filter((name) => !EXCLUDED[stage].includes(name))
}

/** Which stages currently reference a provider (for the "unused" section). */
export function usedStages(chains: Record<Stage, string[]>, name: string): Stage[] {
  return STAGES.filter((stage) => chains[stage].includes(name))
}

export function secretsFor(secrets: Record<string, SecretMeta>, stage: Stage, name: string): string[] {
  return Object.entries(secrets)
    .filter(([, meta]) => meta.used_by?.includes(`${stage}:${name}`))
    .map(([key]) => key)
}

/** Every secret some provider stage reads (for the page-level key strip). */
export function providerSecrets(secrets: Record<string, SecretMeta>): [string, SecretMeta][] {
  const prefixes = STAGES.map((stage) => `${stage}:`)
  return Object.entries(secrets).filter(([, meta]) =>
    meta.used_by?.some((user) => prefixes.some((prefix) => user.startsWith(prefix))),
  )
}

export function coerceValue(field: CatalogField, value: unknown): unknown {
  if (field.kind === 'number') {
    if (value === '' || value == null) return null
    const number = Number(value)
    return Number.isFinite(number) ? number : value
  }
  if (field.kind === 'bool') return value === true || value === 'true'
  if (field.kind === 'list') return Array.isArray(value) ? value.map(String) : []
  if (field.kind === 'select') {
    for (const option of field.options ?? []) {
      if (String(optionValue(option)) === String(value)) return optionValue(option)
    }
    return value ?? ''
  }
  return value ?? ''
}

export function equalValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

export function voiceLabel(value: string, voices: VoiceOption[], field?: CatalogField): string {
  const fromVoices = voices.find((voice) => String(voice.value) === value)
  if (fromVoices?.label) return pickLocalized(fromVoices.label)
  const fromField = (field?.options ?? []).find((option) => String(optionValue(option)) === value)
  if (fromField) return optionLabel(fromField)
  return value
}
