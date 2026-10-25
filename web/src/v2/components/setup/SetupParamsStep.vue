<script setup lang="ts">
/**
 * `params` step — the M3 quick-add form, now rendered inside the server plan.
 *
 * The manifest's `params_schema` drives the extra inputs; `host/room/wake_words`
 * are promoted to the device fields exactly like `POST /devices`. The component
 * never chooses the action: the parent passes `device.create` only when the plan
 * advertises it, so add/first-run can never fall into an edit write.
 */
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { SetupParamSchema, SetupStep } from '../../api'
import { stringList } from '../../setup'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  action: string
  pluginId: string
  pluginName: string
  modelId: string
  modelName: string
  multiDeviceEnabled: boolean
  busy: boolean
  initialId?: string
  serverFields: Record<string, string>
  conflictIds: string[]
  conflictPort: number | null
  existingId: string
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
  fieldEdit: [key: string]
  idChange: [value: string]
}>()

const PROMOTED = new Set(['host', 'room', 'wake_words', 'listen_port'])
const schema = computed<SetupParamSchema[]>(() => props.step.detail?.params_schema ?? [])
const extraSpecs = computed(() => schema.value.filter((spec) => !PROMOTED.has(spec.key)))
const visibleExtraSpecs = computed(() => extraSpecs.value.filter((spec) => spec.type !== 'secret'))
const secretSpecs = computed(() => extraSpecs.value.filter((spec) => spec.type === 'secret'))

const id = ref('')
const room = ref('')
const host = ref('')
const listenPort = ref('')
const wakeWords = ref<string[]>([''])
const extra = ref<Record<string, string | boolean>>({})
const submitted = ref(false)
const touched = ref<Record<string, boolean>>({})

const defaults = computed<Record<string, unknown>>(() => props.step.detail?.defaults ?? {})

function asDraft(spec: SetupParamSchema, value: unknown): string | boolean {
  if (spec.type === 'bool') return value === true
  if (spec.type === 'list') return stringList(value).join(', ')
  if (value === null || value === undefined) return ''
  return String(value)
}

function applyDefaults(): void {
  const values = defaults.value
  id.value = props.initialId ?? ''
  room.value = typeof values.room === 'string' ? values.room : ''
  host.value = typeof values.host === 'string' ? values.host : ''
  listenPort.value =
    typeof values.listen_port === 'number' || typeof values.listen_port === 'string'
      ? String(values.listen_port)
      : ''
  const words = stringList(values.wake_words)
  wakeWords.value = words.length ? words : ['']
  const next: Record<string, string | boolean> = {}
  for (const spec of visibleExtraSpecs.value) {
    next[spec.key] = asDraft(spec, values[spec.key] ?? spec.default)
  }
  extra.value = next
  submitted.value = false
  touched.value = {}
}

const formKey = computed(() => `${props.pluginId}|${props.modelId}`)
watch(formKey, applyDefaults, { immediate: true })

/**
 * The wizard owns the draft id (it drives `GET /setup/plan`). Mirror it here so
 * an id typed into the collision card shows up again when the plan returns to
 * add mode, without resetting the other fields the operator already typed.
 */
watch(
  () => props.initialId ?? '',
  (value) => {
    if (value !== id.value) id.value = value
  },
)

function touch(key: string): void {
  touched.value[key] = true
  emit('fieldEdit', key)
}

function cleanWakeWords(): string[] {
  return wakeWords.value.map((word) => word.trim()).filter(Boolean)
}

function setWake(index: number, value: string): void {
  wakeWords.value[index] = value
  touch(`wake_words:${index}`)
  touch('wake_words')
}

function addWake(): void {
  if (wakeWords.value.length >= 3) return
  wakeWords.value.push('')
}

function removeWake(index: number): void {
  wakeWords.value.splice(index, 1)
  touch('wake_words')
}

function setExtra(key: string, value: string | boolean): void {
  extra.value[key] = value
  touch(`params.${key}`)
}

const validation = computed<Record<string, string>>(() => {
  const errors: Record<string, string> = {}
  const idValue = id.value.trim()
  if (!idValue) errors.id = t('v2.setup.params.missingRequired')
  else if (/^add$/i.test(idValue)) errors.id = t('v2.setup.params.idReserved')
  else if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(idValue)) {
    errors.id = t('v2.setup.params.idInvalid')
  }
  if (props.multiDeviceEnabled && !room.value.trim()) {
    errors.room = t('v2.setup.params.roomRequired')
  }
  const hostValue = host.value.trim()
  if (!hostValue) errors.host = t('v2.setup.params.missingRequired')
  else if (/\s/.test(hostValue) || hostValue.includes('://') || hostValue.length > 64) {
    errors.host = t('v2.setup.params.hostInvalid')
  }
  if (String(listenPort.value).trim()) {
    const port = Number(listenPort.value)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      errors.listen_port = t('v2.setup.params.portInvalid')
    }
  }
  const words = cleanWakeWords()
  if (words.length > 3) errors.wake_words = t('v2.setup.params.wakeMax')
  wakeWords.value.forEach((word, index) => {
    const value = word.trim()
    if (!value) return
    if (value.length > 12) errors[`wake_words:${index}`] = t('v2.setup.params.wakeTooLong')
    else if (/\s/.test(value)) errors[`wake_words:${index}`] = t('v2.setup.params.wakeSpace')
  })
  for (const spec of visibleExtraSpecs.value) {
    const raw = extra.value[spec.key]
    const empty = spec.type === 'bool' ? false : String(raw ?? '').trim() === ''
    if (spec.required && empty) {
      errors[`params.${spec.key}`] = t('v2.setup.params.missingRequired')
      continue
    }
    if (spec.type === 'int' && !empty) {
      const value = Number(raw)
      if (!Number.isInteger(value)) errors[`params.${spec.key}`] = t('v2.setup.params.intInvalid')
    }
    if (spec.type === 'choice' && !empty && spec.options?.length && !spec.options.includes(String(raw))) {
      errors[`params.${spec.key}`] = t('v2.setup.params.choiceInvalid')
    }
  }
  return errors
})

function visibleError(key: string): string {
  if (props.serverFields[key]) return props.serverFields[key]
  if (submitted.value || touched.value[key]) return validation.value[key] ?? ''
  return ''
}

function fieldInvalid(key: string): boolean {
  return Boolean(visibleError(key))
}

function buildValues(): Record<string, unknown> {
  const values: Record<string, unknown> = {
    id: id.value.trim(),
    room: room.value.trim(),
    host: host.value.trim(),
    enabled: true,
  }
  const port = Number(listenPort.value)
  if (String(listenPort.value).trim() && Number.isInteger(port)) values.listen_port = port
  const words = cleanWakeWords()
  if (words.length) values.wake_words = words
  const params: Record<string, unknown> = {}
  for (const spec of visibleExtraSpecs.value) {
    const raw = extra.value[spec.key]
    if (spec.type === 'bool') {
      params[spec.key] = raw === true
      continue
    }
    const text = String(raw ?? '').trim()
    if (!text) continue
    if (spec.type === 'int') params[spec.key] = Number(text)
    else if (spec.type === 'list') {
      params[spec.key] = text.split(',').map((item) => item.trim()).filter(Boolean)
    } else params[spec.key] = text
  }
  if (Object.keys(params).length) values.params = params
  return values
}

function submit(): void {
  if (props.busy || !props.action) return
  submitted.value = true
  if (Object.keys(validation.value).length > 0) return
  emit('submit', props.action, buildValues())
}

const conflictText = computed(() =>
  props.conflictIds.length || props.conflictPort
    ? t('v2.setup.params.portConflict', {
        port: props.conflictPort ?? '—',
        ids: props.conflictIds.join(', ') || '—',
      })
    : '',
)
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.params.title') }}</h4>
    <p class="note">
      {{ pluginName }}<span v-if="modelName"> · {{ modelName }}</span>
    </p>

    <p v-if="!action" class="banner banner-warn v2-banner" data-testid="setup-params-no-action">
      {{ t('v2.setup.params.noCreateAction') }}
    </p>

    <form v-else class="v2-setup-form" data-testid="setup-params-form" @submit.prevent="submit">
      <div class="field-row" :class="{ invalid: fieldInvalid('id') }">
        <div class="field-meta">
          <label class="field-label" for="setup-id">{{ t('v2.setup.params.id') }}</label>
          <p class="field-hint">{{ t('v2.setup.params.idHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="setup-id"
            v-model="id"
            type="text"
            class="input mono"
            :class="{ invalid: fieldInvalid('id') }"
            :aria-invalid="fieldInvalid('id')"
            :placeholder="t('v2.setup.params.idPlaceholder')"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
            data-testid="setup-id"
            @input="touch('id'); emit('idChange', ($event.target as HTMLInputElement).value)"
          />
          <p v-if="visibleError('id')" class="field-error" data-testid="setup-error-id">
            {{ visibleError('id') }}
          </p>
          <div v-if="existingId" class="v2-add-existing" data-testid="setup-device-exists">
            <p class="note">{{ t('v2.setup.params.deviceExists') }} · <span class="mono">{{ existingId }}</span></p>
            <p class="note">{{ t('v2.setup.params.deviceExistsHint') }}</p>
            <RouterLink class="btn btn-sm" :to="`/devices/${existingId}`">
              {{ t('v2.setup.params.openDevice') }}
            </RouterLink>
          </div>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('room') }">
        <div class="field-meta">
          <label class="field-label" for="setup-room">
            {{ t('v2.setup.params.room') }}
            <span v-if="multiDeviceEnabled" class="v2-setup-req">*</span>
          </label>
          <p class="field-hint">{{ t('v2.setup.params.roomHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="setup-room"
            v-model="room"
            type="text"
            class="input"
            :class="{ invalid: fieldInvalid('room') }"
            :aria-invalid="fieldInvalid('room')"
            :placeholder="t('v2.setup.params.roomPlaceholder')"
            :disabled="busy"
            data-testid="setup-room"
            @input="touch('room')"
          />
          <p v-if="visibleError('room')" class="field-error" data-testid="setup-error-room">
            {{ visibleError('room') }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('host') }">
        <div class="field-meta">
          <label class="field-label" for="setup-host">
            {{ t('v2.setup.params.host') }}
            <span class="v2-setup-req">*</span>
          </label>
          <p class="field-hint">{{ t('v2.setup.params.hostHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="setup-host"
            v-model="host"
            type="text"
            class="input"
            :class="{ invalid: fieldInvalid('host') }"
            :aria-invalid="fieldInvalid('host')"
            :placeholder="t('v2.setup.params.hostPlaceholder')"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
            data-testid="setup-host"
            @input="touch('host')"
          />
          <p v-if="visibleError('host')" class="field-error" data-testid="setup-error-host">
            {{ visibleError('host') }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('listen_port') }">
        <div class="field-meta">
          <label class="field-label" for="setup-port">{{ t('v2.setup.params.port') }}</label>
          <p class="field-hint">{{ t('v2.setup.params.portHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="setup-port"
            v-model="listenPort"
            type="number"
            inputmode="numeric"
            min="1"
            max="65535"
            class="input"
            :class="{ invalid: fieldInvalid('listen_port') }"
            :aria-invalid="fieldInvalid('listen_port')"
            :placeholder="t('v2.setup.params.portPlaceholder')"
            :disabled="busy"
            data-testid="setup-port"
            @input="touch('listen_port')"
          />
          <!-- A `port_conflict` response feeds both `serverFields.listen_port`
               and the structured `conflictText` (port + device ids); render the
               detailed one only, not the same error twice. -->
          <p
            v-if="visibleError('listen_port') && !conflictText"
            class="field-error"
            data-testid="setup-error-listen-port"
          >
            {{ visibleError('listen_port') }}
          </p>
          <p v-if="conflictText" class="field-error" data-testid="setup-error-port-conflict">
            {{ conflictText }}
          </p>
          <p v-if="conflictText" class="note">{{ t('v2.setup.params.portConflictHint') }}</p>
          <p v-if="conflictIds.length" class="v2-setup-chip-row" data-testid="setup-port-conflict-ids">
            <RouterLink
              v-for="id in conflictIds"
              :key="id"
              class="btn btn-sm mono"
              :to="`/devices/${id}`"
            >
              {{ id }}
            </RouterLink>
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('wake_words') }">
        <div class="field-meta">
          <label class="field-label">{{ t('v2.setup.params.wakeLabel') }}</label>
          <p class="field-hint">{{ t('v2.setup.params.wakeHint') }}</p>
        </div>
        <div class="field-control">
          <div v-for="(word, index) in wakeWords" :key="`setup-wake-${index}`" class="v2-wake-row">
            <input
              :id="`setup-wake-${index}`"
              :value="word"
              type="text"
              maxlength="12"
              class="input"
              :class="{ invalid: fieldInvalid(`wake_words:${index}`) }"
              :aria-invalid="fieldInvalid(`wake_words:${index}`)"
              :placeholder="t('v2.setup.params.wakePlaceholder')"
              :disabled="busy"
              :data-testid="`setup-wake-${index}`"
              @input="setWake(index, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn btn-sm"
              :aria-label="t('v2.setup.params.wakeRemove', { n: index + 1 })"
              :disabled="busy"
              @click="removeWake(index)"
            >
              ×
            </button>
            <p
              v-if="visibleError(`wake_words:${index}`)"
              class="field-error"
              :data-testid="`setup-error-wake-${index}`"
            >
              {{ visibleError(`wake_words:${index}`) }}
            </p>
          </div>
          <p v-if="visibleError('wake_words')" class="field-error" data-testid="setup-error-wake-words">
            {{ visibleError('wake_words') }}
          </p>
          <button
            type="button"
            class="btn btn-sm v2-wake-add"
            :disabled="busy || wakeWords.length >= 3"
            @click="addWake"
          >
            {{ t('v2.setup.params.wakeAdd') }}
          </button>
        </div>
      </div>

      <div v-if="visibleExtraSpecs.length" class="v2-setup-subcard">
        <h5 class="v2-setup-subtitle">{{ t('v2.setup.params.extra') }}</h5>
        <p class="note">{{ t('v2.setup.params.extraHint') }}</p>
        <div
          v-for="spec in visibleExtraSpecs"
          :key="spec.key"
          class="field-row"
          :class="{ invalid: fieldInvalid(`params.${spec.key}`) }"
        >
          <div class="field-meta">
            <label class="field-label" :for="`setup-param-${spec.key}`">
              {{ spec.label || spec.key }}
              <span v-if="spec.required" class="v2-setup-req">*</span>
            </label>
            <p v-if="spec.help" class="field-hint">{{ spec.help }}</p>
          </div>
          <div class="field-control">
            <select
              v-if="spec.type === 'choice'"
              :id="`setup-param-${spec.key}`"
              class="input"
              :class="{ invalid: fieldInvalid(`params.${spec.key}`) }"
              :value="String(extra[spec.key] ?? '')"
              :disabled="busy"
              :data-testid="`setup-param-${spec.key}`"
              @change="setExtra(spec.key, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('v2.setup.params.choicePlaceholder') }}</option>
              <option v-for="option in spec.options ?? []" :key="option" :value="option">
                {{ option }}
              </option>
            </select>
            <input
              v-else-if="spec.type === 'bool'"
              :id="`setup-param-${spec.key}`"
              type="checkbox"
              :checked="extra[spec.key] === true"
              :disabled="busy"
              :data-testid="`setup-param-${spec.key}`"
              @change="setExtra(spec.key, ($event.target as HTMLInputElement).checked)"
            />
            <input
              v-else
              :id="`setup-param-${spec.key}`"
              :value="String(extra[spec.key] ?? '')"
              :type="spec.type === 'int' ? 'number' : 'text'"
              :inputmode="spec.type === 'int' ? 'numeric' : undefined"
              class="input"
              :class="{ invalid: fieldInvalid(`params.${spec.key}`) }"
              :aria-invalid="fieldInvalid(`params.${spec.key}`)"
              :placeholder="spec.placeholder ?? ''"
              :disabled="busy"
              :data-testid="`setup-param-${spec.key}`"
              @input="setExtra(spec.key, ($event.target as HTMLInputElement).value)"
            />
            <p
              v-if="visibleError(`params.${spec.key}`)"
              class="field-error"
              :data-testid="`setup-error-param-${spec.key}`"
            >
              {{ visibleError(`params.${spec.key}`) }}
            </p>
          </div>
        </div>
      </div>

      <p v-if="secretSpecs.length" class="note" data-testid="setup-params-secret-note">
        {{ t('v2.setup.params.secret') }}
      </p>

      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-primary"
          data-testid="setup-params-submit"
          :disabled="busy"
        >
          {{ busy ? t('v2.setup.params.creating') : t('v2.setup.params.create') }}
        </button>
        <p class="note">{{ t('v2.setup.params.createdHint') }}</p>
      </div>
    </form>
  </div>
</template>
