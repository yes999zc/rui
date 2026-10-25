<script setup lang="ts">
/**
 * Minimal M3 "add device" page (contract §7.11).
 *
 * The catalog is `GET /plugins`; only plugins with `available=true` can be
 * picked. A single-device plugin selects its model automatically, a multi-model
 * plugin requires an explicit pick (409/422 are mapped back onto the selects).
 * Create is deliberately not an upsert: `409 device_exists` links to the
 * existing device instead of overwriting it.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import CapabilityChips from '../components/CapabilityChips.vue'
import StateBlock from '../components/StateBlock.vue'
import ErrorNotice from '../components/device/ErrorNotice.vue'
import RestartNotice from '../components/device/RestartNotice.vue'
import {
  api,
  type CreateDeviceRequest,
  type HouseInfo,
  type PluginInfo,
  type PluginModel,
  type Restart,
} from '../api'
import {
  apiErrorCode,
  apiErrorDetail,
  apiRetryAfterSeconds,
  describeError,
  normalizeFieldPath,
  type ErrorCopy,
} from '../errors'
import { metaFeature } from '../meta'
import { t, transportLabel } from '../i18n'
import { useResource } from '../useResource'

const router = useRouter()

const pluginsResource = useResource(() => api.plugins())
const houseResource = useResource(() => api.house())
const { data: pluginData, error: pluginError, loading: pluginLoading } = pluginsResource
const house = computed<HouseInfo | null>(() => houseResource.data.value)

const featureEnabled = metaFeature('device_create')

const availablePlugins = computed<PluginInfo[]>(
  () => (pluginData.value?.items ?? []).filter((plugin) => plugin.available),
)

const selectedPluginId = ref('')
const selectedModelId = ref('')
const id = ref('')
const room = ref('')
const host = ref('')
const listenPort = ref<string | number>('')
const wakeWords = ref<string[]>([''])

const submitted = ref(false)
const touched = ref<Record<string, boolean>>({})
const creating = ref(false)
const saveError = ref<ErrorCopy | null>(null)
const saveErrorRaw = ref<unknown>(null)
const serverFields = ref<Record<string, string>>({})
/**
 * A create can be 201 and still fail to rebuild the fleet (contract §7.11:
 * `restart.status="failed"` / non-empty `restart.error`). The device config is
 * already on disk, so silently navigating away hides the only error the
 * operator would get. Keep the page, show `restart`, and link to the device.
 */
const createdId = ref('')
const createdRestart = ref<Restart | null>(null)
/** 429 cooldown: submit stays disabled until `retry_after_s` seconds elapse. */
const cooldown = ref(0)
let cooldownTimer: number | null = null

const createdRestartFailed = computed(() => {
  const restart = createdRestart.value
  return Boolean(restart && (restart.status === 'failed' || restart.error))
})

const selectedPlugin = computed<PluginInfo | null>(
  () => availablePlugins.value.find((plugin) => plugin.id === selectedPluginId.value) ?? null,
)
const models = computed<PluginModel[]>(() => selectedPlugin.value?.devices ?? [])
const selectedModel = computed<PluginModel | null>(
  () => models.value.find((model) => model.model_id === selectedModelId.value) ?? null,
)
const multiDeviceEnabled = computed(() => house.value?.multi_device?.enabled ?? true)

const pluginErrorCopy = computed(() =>
  pluginError.value ? describeError(pluginError.value) : null,
)

watch(selectedPluginId, () => {
  const list = models.value
  selectedModelId.value = list.length === 1 ? list[0].model_id : ''
  touch('plugin_id')
  touch('model_id')
})

function parsePort(): number | null | 'invalid' {
  const raw = String(listenPort.value ?? '').trim()
  if (!raw) return null
  if (!/^\d+$/.test(raw)) return 'invalid'
  const value = Number(raw)
  return Number.isInteger(value) && value >= 1 && value <= 65535 ? value : 'invalid'
}

function cleanWakeWords(): string[] {
  return wakeWords.value.map((word) => word.trim()).filter(Boolean)
}

function clientErrors(): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!selectedPlugin.value) errors.plugin_id = t('v2.add.validation.plugin')
  if (selectedPlugin.value && models.value.length === 0) {
    errors.model_id = t('v2.add.pluginEmpty')
  } else if (!selectedModel.value) {
    errors.model_id = t('v2.add.validation.model')
  }
  const value = id.value.trim()
  // `/devices/add` is a static route and would shadow a device whose id is
  // `add`, so that id (case-insensitive) is reserved; see router.ts.
  if (/^add$/i.test(value)) {
    errors.id = t('v2.add.validation.idReserved')
  } else if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(value)) {
    errors.id = t('v2.add.validation.id')
  }
  if (multiDeviceEnabled.value && !room.value.trim()) {
    errors.room = t('v2.add.validation.room')
  }
  const hostValue = host.value.trim()
  if (!hostValue || /\s/.test(hostValue) || hostValue.includes('://') || hostValue.length > 64) {
    errors.host = t('v2.add.validation.host')
  }
  if (parsePort() === 'invalid') errors.listen_port = t('v2.add.validation.port')
  const words = cleanWakeWords()
  if (words.length > 3) errors.wake_words = t('v2.add.validation.wakeMax')
  wakeWords.value.forEach((word, index) => {
    if (word.trim().length > 12) {
      errors[`wake_words:${index}`] = t('v2.add.validation.wakeTooLong')
    }
  })
  return errors
}

const validation = computed(clientErrors)

function visibleError(key: string): string {
  if (serverFields.value[key]) return serverFields.value[key]
  if ((submitted.value || touched.value[key]) && validation.value[key]) {
    return validation.value[key]
  }
  return ''
}

function fieldInvalid(key: string): boolean {
  return Boolean(visibleError(key))
}

function touch(key: string): void {
  touched.value[key] = true
  if (serverFields.value[key]) {
    const next = { ...serverFields.value }
    delete next[key]
    serverFields.value = next
  }
  saveError.value = null
  saveErrorRaw.value = null
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

function fieldKnown(path: string): boolean {
  const base = path.split('.')[0]
  return ['id', 'room', 'host', 'listen_port', 'wake_words', 'plugin_id', 'model_id'].includes(
    base,
  )
}

function applyServerFields(err: unknown): void {
  const copy = err ? describeError(err) : null
  const map: Record<string, string> = {}
  for (const field of copy?.fields ?? []) {
    const path = normalizeFieldPath(field.path)
    const message = field.message || t('v2.deviceConfig.validation.invalid')
    const indexed = path.match(/^wake_words(?:\[(\d+)\]|\.(\d+))$/)
    if (indexed) {
      map[`wake_words:${indexed[1] ?? indexed[2]}`] = message
      continue
    }
    if (fieldKnown(path)) map[path.split('.')[0]] = message
  }
  if (apiErrorCode(err) === 'port_conflict') {
    map.listen_port = map.listen_port || t('v2.error.portConflict')
  }
  if (apiErrorCode(err) === 'device_exists') map.id = map.id || t('v2.error.deviceExists')
  if (apiErrorCode(err) === 'plug_unavailable') {
    map.plugin_id = map.plugin_id || t('v2.error.plugUnavailable')
  }
  serverFields.value = map
}

const existingId = computed(() => {
  if (apiErrorCode(saveErrorRaw.value) !== 'device_exists') return ''
  const detail = apiErrorDetail(saveErrorRaw.value, 'id')
  return typeof detail === 'string' && detail ? detail : id.value.trim()
})

const conflictIds = computed<string[]>(() => {
  if (apiErrorCode(saveErrorRaw.value) !== 'port_conflict') return []
  const ids = apiErrorDetail(saveErrorRaw.value, 'device_ids')
  return Array.isArray(ids) ? ids.map(String) : []
})
const conflictPort = computed<number | null>(() => {
  if (apiErrorCode(saveErrorRaw.value) !== 'port_conflict') return null
  const port = apiErrorDetail(saveErrorRaw.value, 'port')
  return typeof port === 'number' ? port : null
})

async function submit(): Promise<void> {
  if (creating.value || !featureEnabled || cooldown.value > 0 || createdId.value) return
  submitted.value = true
  saveError.value = null
  saveErrorRaw.value = null
  serverFields.value = {}
  if (Object.keys(validation.value).length > 0) return
  const port = parsePort()
  if (port === 'invalid') return
  const plugin = selectedPlugin.value
  const model = selectedModel.value
  if (!plugin || !model) return
  const body: CreateDeviceRequest = {
    id: id.value.trim(),
    room: room.value.trim(),
    plugin_id: plugin.id,
    model_id: model.model_id,
    host: host.value.trim(),
    listen_port: port,
    wake_words: cleanWakeWords(),
    enabled: true,
  }
  creating.value = true
  try {
    const response = await api.createDevice(body)
    const restart = response.restart
    // 201 + failed fleet restart is not a success story: the config is written
    // but audio stopped. Show the restart envelope and stay put so the operator
    // actually sees scope/error; the device link replaces a silent redirect.
    if (restart && (restart.status === 'failed' || restart.error)) {
      createdId.value = response.device.id
      createdRestart.value = restart
      return
    }
    await router.push({ name: 'device-detail', params: { id: response.device.id } })
  } catch (err) {
    saveErrorRaw.value = err
    saveError.value = describeError(err)
    applyServerFields(err)
    if (apiErrorCode(err) === 'rate_limited' || saveError.value.status === 429) {
      startCooldown(apiRetryAfterSeconds(err) ?? 10)
    }
  } finally {
    creating.value = false
  }
}

function clearCooldownTimer(): void {
  if (cooldownTimer !== null) {
    window.clearInterval(cooldownTimer)
    cooldownTimer = null
  }
}

/** Disables submit for the server-advertised window (same UX as rotate/reveal). */
function startCooldown(seconds: number): void {
  clearCooldownTimer()
  cooldown.value = Math.max(0, Math.ceil(seconds))
  if (cooldown.value <= 0) return
  cooldownTimer = window.setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) clearCooldownTimer()
  }, 1000)
}

function retry(): void {
  if (!featureEnabled) return
  void pluginsResource.load()
  void houseResource.load()
}

onMounted(() => {
  if (!featureEnabled) return
  void pluginsResource.load()
  void houseResource.load()
})

onBeforeUnmount(clearCooldownTimer)
</script>

<template>
  <div class="v2-page">
    <StateBlock
      v-if="!featureEnabled"
      kind="unavailable"
      :title="t('v2.add.featureOff')"
      :hint="t('v2.add.featureOffHint')"
    >
      <RouterLink class="btn" to="/devices">{{ t('v2.add.back') }}</RouterLink>
    </StateBlock>

    <template v-else>
      <RouterLink class="v2-back" to="/devices">
        <AppIcon name="arrowRight" :size="14" class="v2-back-icon" />
        {{ t('v2.add.back') }}
      </RouterLink>

      <header class="v2-page-head">
        <div class="v2-page-title">
          <span class="kicker">{{ t('v2.add.badge') }}</span>
          <h1>{{ t('v2.add.title') }}</h1>
          <p class="lede">{{ t('v2.add.lede') }}</p>
        </div>
      </header>

      <StateBlock
        v-if="pluginLoading && !pluginData"
        kind="loading"
        :title="t('v2.common.loading')"
      />

      <StateBlock
        v-else-if="pluginErrorCopy && !pluginData"
        kind="error"
        :title="t('v2.add.pluginLoadFailed')"
        :detail="pluginErrorCopy.detail"
        :hint="pluginErrorCopy.hint"
        :status="pluginErrorCopy.status"
        :request-id="pluginErrorCopy.requestId"
        show-retry
        @retry="retry"
      />

      <StateBlock
        v-else-if="availablePlugins.length === 0"
        kind="empty"
        :title="t('v2.add.noPlugins')"
        :hint="t('v2.add.noPluginsHint')"
      >
        <button type="button" class="btn" @click="retry">{{ t('v2.common.refresh') }}</button>
      </StateBlock>

      <section v-else class="v2-card" data-testid="add-form">
        <div class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.add.title') }}</h2>
          <span class="badge badge-muted">{{ pluginData?.total ?? 0 }}</span>
        </div>

        <div class="field-row" :class="{ invalid: fieldInvalid('plugin_id') }">
          <div class="field-meta">
            <label class="field-label" for="add-plugin">{{ t('v2.add.pluginLabel') }}</label>
          </div>
          <div class="field-control">
            <select
              id="add-plugin"
              v-model="selectedPluginId"
              class="input"
              :class="{ invalid: fieldInvalid('plugin_id') }"
              :disabled="creating"
              data-testid="add-plugin"
            >
              <option value="">{{ t('v2.add.pluginPlaceholder') }}</option>
              <option v-for="plugin in availablePlugins" :key="plugin.id" :value="plugin.id">
                {{ plugin.name }} · {{ plugin.id }}
              </option>
            </select>
            <p v-if="selectedPlugin" class="field-meta-line">
              <span class="mono">{{ selectedPlugin.id }}</span>
              <span>{{ t('v2.add.pluginVersion', { version: selectedPlugin.version }) }}</span>
            </p>
            <p v-if="visibleError('plugin_id')" class="field-error" data-testid="error-plugin">
              {{ visibleError('plugin_id') }}
            </p>
          </div>
        </div>

        <div class="field-row" :class="{ invalid: fieldInvalid('model_id') }">
          <div class="field-meta">
            <label class="field-label" for="add-model">{{ t('v2.add.modelLabel') }}</label>
          </div>
          <div class="field-control">
            <select
              id="add-model"
              v-model="selectedModelId"
              class="input"
              :class="{ invalid: fieldInvalid('model_id') }"
              :disabled="creating || !selectedPlugin"
              data-testid="add-model"
            >
              <option value="">{{ t('v2.add.modelPlaceholder') }}</option>
              <option v-for="model in models" :key="model.model_id" :value="model.model_id">
                {{ model.name }} · {{ model.model_id }}
              </option>
            </select>
            <p v-if="selectedModel" class="field-meta-line">
              <span>{{ t('v2.add.selectedModel', { name: selectedModel.name }) }}</span>
              <span>{{ t('v2.add.transport') }}: {{ transportLabel(selectedModel.transport) }}</span>
            </p>
            <p v-else-if="selectedPlugin && models.length === 0" class="field-hint">
              {{ t('v2.add.pluginEmpty') }}
            </p>
            <CapabilityChips
              v-if="selectedModel"
              :ids="selectedModel.capabilities"
              :unknown="[]"
            />
            <p v-if="visibleError('model_id')" class="field-error" data-testid="error-model">
              {{ visibleError('model_id') }}
            </p>
          </div>
        </div>

        <template v-if="selectedPlugin">
          <div class="field-row" :class="{ invalid: fieldInvalid('id') }">
            <div class="field-meta">
              <label class="field-label" for="add-id">{{ t('v2.add.idLabel') }}</label>
              <p class="field-hint">{{ t('v2.add.idHint') }}</p>
            </div>
            <div class="field-control">
              <input
                id="add-id"
                v-model="id"
                type="text"
                class="input mono"
                :class="{ invalid: fieldInvalid('id') }"
                :aria-invalid="fieldInvalid('id')"
                :placeholder="t('v2.add.idPlaceholder')"
                autocomplete="off"
                spellcheck="false"
                :disabled="creating"
                data-testid="add-id"
                @input="touch('id')"
              />
              <p v-if="visibleError('id')" class="field-error" data-testid="error-id">
                {{ visibleError('id') }}
              </p>
              <div v-if="existingId" class="v2-add-existing">
                <p class="note">{{ t('v2.add.deviceExists', { id: existingId }) }}</p>
                <RouterLink class="btn btn-sm" :to="`/devices/${existingId}`">
                  {{ t('v2.add.openExisting') }}
                </RouterLink>
              </div>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldInvalid('room') }">
            <div class="field-meta">
              <label class="field-label" for="add-room">{{ t('v2.add.roomLabel') }}</label>
              <p class="field-hint">{{ t('v2.add.roomHint') }}</p>
            </div>
            <div class="field-control">
              <input
                id="add-room"
                v-model="room"
                type="text"
                class="input"
                :class="{ invalid: fieldInvalid('room') }"
                :aria-invalid="fieldInvalid('room')"
                :placeholder="t('v2.add.roomPlaceholder')"
                :disabled="creating"
                data-testid="add-room"
                @input="touch('room')"
              />
              <p v-if="visibleError('room')" class="field-error" data-testid="error-room">
                {{ visibleError('room') }}
              </p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldInvalid('host') }">
            <div class="field-meta">
              <label class="field-label" for="add-host">{{ t('v2.add.hostLabel') }}</label>
            </div>
            <div class="field-control">
              <input
                id="add-host"
                v-model="host"
                type="text"
                class="input"
                :class="{ invalid: fieldInvalid('host') }"
                :aria-invalid="fieldInvalid('host')"
                :placeholder="t('v2.add.hostPlaceholder')"
                :disabled="creating"
                data-testid="add-host"
                @input="touch('host')"
              />
              <p v-if="visibleError('host')" class="field-error" data-testid="error-host">
                {{ visibleError('host') }}
              </p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldInvalid('listen_port') }">
            <div class="field-meta">
              <label class="field-label" for="add-port">{{ t('v2.add.portLabel') }}</label>
            </div>
            <div class="field-control">
              <input
                id="add-port"
                v-model="listenPort"
                type="number"
                inputmode="numeric"
                min="1"
                max="65535"
                class="input"
                :class="{ invalid: fieldInvalid('listen_port') }"
                :aria-invalid="fieldInvalid('listen_port')"
                :placeholder="t('v2.add.portPlaceholder')"
                :disabled="creating"
                data-testid="add-port"
                @input="touch('listen_port')"
              />
              <p
                v-if="visibleError('listen_port')"
                class="field-error"
                data-testid="error-listen-port"
              >
                {{ visibleError('listen_port') }}
              </p>
              <p
                v-if="conflictIds.length || conflictPort"
                class="field-error"
                data-testid="error-port-conflict"
              >
                {{
                  t('v2.add.portConflict', {
                    port: conflictPort ?? '—',
                    ids: conflictIds.join(', ') || '—',
                  })
                }}
              </p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldInvalid('wake_words') }">
            <div class="field-meta">
              <label class="field-label">{{ t('v2.add.wakeLabel') }}</label>
              <p class="field-hint">{{ t('v2.add.wakeHint') }}</p>
            </div>
            <div class="field-control">
              <div
                v-for="(word, index) in wakeWords"
                :key="`add-wake-${index}`"
                class="v2-wake-row"
              >
                <input
                  :id="`add-wake-${index}`"
                  :value="word"
                  type="text"
                  maxlength="12"
                  class="input"
                  :class="{ invalid: fieldInvalid(`wake_words:${index}`) }"
                  :aria-invalid="fieldInvalid(`wake_words:${index}`)"
                  :placeholder="t('v2.add.wakePlaceholder')"
                  :disabled="creating"
                  :data-testid="`add-wake-${index}`"
                  @input="setWake(index, ($event.target as HTMLInputElement).value)"
                />
                <button
                  type="button"
                  class="btn btn-sm"
                  :aria-label="t('v2.add.wakeRemove', { n: index + 1 })"
                  :disabled="creating"
                  @click="removeWake(index)"
                >
                  <AppIcon name="trash" :size="14" />
                </button>
                <p
                  v-if="visibleError(`wake_words:${index}`)"
                  class="field-error"
                  :data-testid="`error-wake-${index}`"
                >
                  {{ visibleError(`wake_words:${index}`) }}
                </p>
              </div>
              <p
                v-if="visibleError('wake_words')"
                class="field-error"
                data-testid="error-wake-words"
              >
                {{ visibleError('wake_words') }}
              </p>
              <button
                type="button"
                class="btn btn-sm v2-wake-add"
                :disabled="creating || wakeWords.length >= 3"
                @click="addWake"
              >
                <AppIcon name="plus" :size="14" />
                {{ t('v2.add.wakeAdd') }}
              </button>
            </div>
          </div>

          <div class="v2-save-bar">
            <button
              type="button"
              class="btn btn-primary"
              data-testid="add-submit"
              :disabled="creating || cooldown > 0 || Boolean(createdId)"
              @click="submit"
            >
              <AppIcon name="plus" :size="15" />
              {{ creating ? t('v2.add.submitting') : t('v2.add.submit') }}
            </button>
          </div>
          <p v-if="cooldown > 0" class="note v2-cooldown" role="status" data-testid="add-cooldown">
            {{ t('v2.add.cooldown', { n: cooldown }) }}
          </p>
        </template>
      </section>

      <RestartNotice
        v-if="createdRestart && createdRestartFailed"
        :restart="createdRestart"
        :show-retry="false"
      />

      <div
        v-if="createdId"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="created-device"
      >
        <AppIcon name="alert" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.add.createdTitle') }}</strong>
          <p class="note">{{ t('v2.add.createdFailedHint', { id: createdId }) }}</p>
          <RouterLink class="btn btn-sm btn-primary" :to="`/devices/${createdId}`">
            {{ t('v2.add.openCreated') }}
          </RouterLink>
        </div>
      </div>

      <ErrorNotice :error="saveError" />

      <p v-if="pluginData && availablePlugins.length < pluginData.total" class="note">
        {{ t('v2.add.noPluginsHint') }}
      </p>
    </template>
  </div>
</template>
