<script setup lang="ts">
/**
 * M6 setup wizard: one engine for `/v2/devices/add` (add) and `/v2/setup`
 * (first-run). Contract §7.23/§7.24 owns everything that matters:
 *
 * - `plan.steps` order, status, evidence, `next_step` and `blocked` are rendered
 *   verbatim; this component never re-orders or infers the eight steps.
 * - a step runs only an action the plan advertises for that step. `device.create`
 *   is the sole params action in add/first-run; `device.update` is never used.
 * - after `device.create` the wizard continues with the same device id returned
 *   by the server, leaving every existing device untouched.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import CapabilityChips from '../CapabilityChips.vue'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from '../device/ErrorNotice.vue'
import RestartNotice from '../device/RestartNotice.vue'
import SetupConnectStep from './SetupConnectStep.vue'
import SetupKeysStep from './SetupKeysStep.vue'
import SetupModelsStep from './SetupModelsStep.vue'
import SetupParamsStep from './SetupParamsStep.vue'
import SetupPrepareStep from './SetupPrepareStep.vue'
import SetupTestStep from './SetupTestStep.vue'
import SetupVerifyStep from './SetupVerifyStep.vue'
import SetupWakeStep from './SetupWakeStep.vue'
import {
  api,
  type DeviceDetail,
  type HouseInfo,
  type PluginInfo,
  type PluginModel,
  type Restart,
  type SetupPlan,
  type SetupStep,
  type SetupStepRequest,
  type SetupStepResponse,
} from '../../api'
import {
  apiErrorCode,
  apiErrorDetail,
  apiRetryAfterSeconds,
  describeError,
  type ErrorCopy,
} from '../../errors'
import { metaFeature } from '../../meta'
import {
  setupFieldKey,
  setupScopeLabel,
  setupStatusLabel,
  stepDescription,
  stepLabel,
  stepStatusClass,
} from '../../setup'
import { t, transportLabel } from '../../i18n'
import { useCooldown } from '../../useCooldown'
import { useResource } from '../../useResource'

const props = defineProps<{ mode: 'add' | 'first-run' }>()

const featureEnabled = metaFeature('setup_plan')

const pluginsResource = useResource(() => api.plugins())
const houseResource = useResource(() => api.house())
const { data: pluginData, error: pluginError, loading: pluginLoading } = pluginsResource
const house = computed<HouseInfo | null>(() => houseResource.data.value)
const availablePlugins = computed<PluginInfo[]>(
  () => (pluginData.value?.items ?? []).filter((plugin) => plugin.available),
)

const selectedPluginId = ref('')
const selectedModelId = ref('')
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

const plan = ref<SetupPlan | null>(null)
const planLoading = ref(false)
const planError = ref<unknown>(null)
let planSequence = 0

const draftId = ref('')
const createdByWizard = ref(false)
const createdDevice = ref<DeviceDetail | null>(null)
const busyStep = ref('')
const actionError = ref<ErrorCopy | null>(null)
const actionErrorRaw = ref<unknown>(null)
const errorStep = ref('')
const lastResult = ref<Record<string, unknown> | null>(null)
const lastAction = ref('')
const lastOutcome = ref<'ok' | 'neutral'>('ok')
const lastRestart = ref<Restart | null>(null)
const serverFields = ref<Record<string, string>>({})
const activeStepId = ref('')
let idTimer: number | null = null

/**
 * These steps run against a device row that must already exist. In add/first-run
 * mode nothing may reach them before `device.create` returned an id: sending a
 * draft id would let `wake.set` resolve an existing device in edit mode.
 */
const CREATED_DEVICE_STEPS = new Set(['wake', 'connect', 'test', 'verify'])
const {
  seconds: cooldownSeconds,
  active: cooldownActive,
  start: startCooldown,
} = useCooldown()

const steps = computed<SetupStep[]>(() => plan.value?.steps ?? [])
const prepareStep = computed<SetupStep | null>(
  () => steps.value.find((step) => step.id === 'prepare') ?? null,
)
const paramsStep = computed<SetupStep | null>(
  () => steps.value.find((step) => step.id === 'params') ?? null,
)
const doneCount = computed(
  () => steps.value.filter((step) => step.status === 'done' || step.status === 'skipped').length,
)
/** Server evidence that `params` now points at a persisted device row. */
const paramsEvidenceId = computed(() => {
  const value = paramsStep.value?.evidence?.device_id
  return typeof value === 'string' ? value : ''
})
/**
 * `device.create` succeeded and returned a device id. Only `createdByWizard`
 * proves that: plan evidence may also name an *existing* device (edit
 * collision), and that id must never be mistreated as a created device.
 */
const paramsCreated = computed(() => createdByWizard.value)
const idCollision = computed(
  () => plan.value?.mode === 'edit' && !createdByWizard.value,
)
const locked = computed(
  () =>
    createdByWizard.value ||
    Boolean(plan.value?.complete) ||
    Boolean(busyStep.value) ||
    cooldownActive.value,
)
const createdDeviceId = computed(() => {
  if (!paramsCreated.value) return ''
  return createdDevice.value?.id || paramsEvidenceId.value || plan.value?.device_id || ''
})
const nextStep = computed<SetupStep | null>(() => {
  const id = plan.value?.next_step
  return id ? steps.value.find((step) => step.id === id) ?? null : null
})
const createAction = computed(() => {
  if (idCollision.value) return ''
  const step = paramsStep.value
  if (!step) return ''
  return step.actions.includes('device.create') ? 'device.create' : ''
})
const blockedCopy = computed(() => {
  const blocked = plan.value?.blocked
  if (!blocked) return ''
  if (blocked.code === 'house_setup_incomplete') {
    return t('v2.setup.blocked.houseSetupIncomplete')
  }
  if (blocked.code === 'plug_unavailable') return t('v2.setup.blocked.plugUnavailable')
  return t('v2.setup.blocked.other', { code: blocked.code })
})
const missingSteps = computed(() => plan.value?.blocked?.missing_steps ?? [])
const allowedActions = computed<string[]>(() => {
  const raw = apiErrorDetail(actionErrorRaw.value, 'allowed')
  return Array.isArray(raw) ? raw.map(String) : []
})
const unknownStepList = computed<string[]>(() => {
  const raw = apiErrorDetail(actionErrorRaw.value, 'steps')
  return Array.isArray(raw) ? raw.map(String) : []
})
const missingPrereqs = computed<string[]>(() => {
  const err = actionErrorRaw.value
  const code = apiErrorCode(err)
  const asList = (value: unknown): string[] =>
    Array.isArray(value) ? value.map(String).filter(Boolean) : []
  // Only `missing_prerequisite` names prerequisite steps. Its contract shape is
  // `details.missing[]` plus `fields[path=step]`; `details.step` is accepted as
  // the documented fallback. Other 4xx codes reuse `details.step` for their own
  // meaning (`action_not_allowed` = the step that refused, `step_in_progress` =
  // the running step), so reading it unconditionally would invent a fake
  // "missing prerequisite" row.
  if (code === 'missing_prerequisite') {
    const fromMissing = asList(apiErrorDetail(err, 'missing'))
    if (fromMissing.length) return fromMissing
    const step = apiErrorDetail(err, 'step')
    if (typeof step === 'string' && step) return [step]
    const fromStepList = asList(step)
    if (fromStepList.length) return fromStepList
    const fields = apiErrorDetail(err, 'fields')
    if (!Array.isArray(fields)) return []
    return fields
      .filter(
        (entry): entry is Record<string, unknown> =>
          Boolean(entry) && typeof entry === 'object',
      )
      .filter((entry) => entry.path === 'step')
      .map((entry) => String(entry.message || entry.code || 'step'))
      .filter(Boolean)
  }
  // `house_setup_incomplete` names missing house steps in `missing_steps` only;
  // it never carries a prerequisite `step` row.
  if (code === 'house_setup_incomplete') {
    return asList(apiErrorDetail(err, 'missing_steps'))
  }
  return []
})
const errorDetailsText = computed(() => {
  const err = actionErrorRaw.value
  if (!err || typeof err !== 'object') return ''
  const details = (err as { details?: unknown }).details
  if (details === undefined || details === null) return ''
  try {
    return JSON.stringify(details)
  } catch {
    return ''
  }
})
/** `missing_prerequisite` whose details matched none of the known shapes. */
const missingPrereqUnknown = computed(
  () =>
    apiErrorCode(actionErrorRaw.value) === 'missing_prerequisite' &&
    missingPrereqs.value.length === 0 &&
    Boolean(errorDetailsText.value),
)
const conflictIds = computed<string[]>(() => {
  const raw = apiErrorDetail(actionErrorRaw.value, 'device_ids')
  return Array.isArray(raw) ? raw.map(String) : []
})
const conflictPort = computed<number | null>(() => {
  const raw = apiErrorDetail(actionErrorRaw.value, 'port')
  return typeof raw === 'number' ? raw : null
})
const lastActionLabel = computed(() => {
  const stepId = lastAction.value.split(':')[0]
  return stepId ? stepLabel(stepId) : lastAction.value
})
const existingId = computed(() => {
  if (apiErrorCode(actionErrorRaw.value) !== 'device_exists') return ''
  const raw = apiErrorDetail(actionErrorRaw.value, 'id')
  return typeof raw === 'string' && raw ? raw : draftId.value.trim()
})

const title = computed(() =>
  props.mode === 'first-run' ? t('v2.setup.title.firstRun') : t('v2.setup.title.add'),
)
const lede = computed(() =>
  props.mode === 'first-run' ? t('v2.setup.lede.firstRun') : t('v2.setup.lede.add'),
)

function modeLabel(mode: string): string {
  if (mode === 'first-run') return t('v2.setup.mode.first-run')
  if (mode === 'edit') return t('v2.setup.mode.edit')
  if (mode === 'add') return t('v2.setup.mode.add')
  return mode
}

function resetPlan(): void {
  planSequence += 1
  plan.value = null
  planError.value = null
  planLoading.value = false
  activeStepId.value = ''
  actionError.value = null
  actionErrorRaw.value = null
  errorStep.value = ''
  lastResult.value = null
  lastAction.value = ''
  lastOutcome.value = 'ok'
  lastRestart.value = null
  serverFields.value = {}
  createdByWizard.value = false
  createdDevice.value = null
  draftId.value = ''
}

function applyPlan(next: SetupPlan, advance: boolean): void {
  const previousNext = plan.value?.next_step
  plan.value = next
  if (idCollision.value) {
    // Add/first-run must not fall into `device.update`: keep the params card open
    // so the operator can pick a different id.
    activeStepId.value = 'params'
    return
  }
  if (advance && next.next_step && next.next_step !== previousNext) {
    activeStepId.value = next.next_step
    return
  }
  if (!activeStepId.value || !next.steps.some((step) => step.id === activeStepId.value)) {
    activeStepId.value = next.next_step ?? next.steps[0]?.id ?? ''
  }
}

async function loadPlan(): Promise<void> {
  const pluginId = selectedPluginId.value
  const modelId = selectedModelId.value
  if (!pluginId || !modelId || cooldownActive.value) return
  const mine = ++planSequence
  planLoading.value = true
  planError.value = null
  serverFields.value = {}
  try {
    // Contract §7.23 has no `mode` query: the backend derives first-run/add/edit
    // from the house state and the draft `device_id`. The route only selects the
    // page copy; it never forces a mode the server did not return.
    const query: { plugin_id: string; model_id: string; device_id?: string } = {
      plugin_id: pluginId,
      model_id: modelId,
    }
    // Before create the draft only asks the server whether an id already exists
    // (edit collision), so the *current draft* must drive the query: otherwise a
    // collision would pin the plan to the existing device forever. After create
    // the wizard may only use the returned id.
    const deviceId =
      paramsCreated.value && createdDeviceId.value
        ? createdDeviceId.value
        : draftId.value.trim()
    if (deviceId) query.device_id = deviceId
    const next = await api.setupPlan(query)
    if (mine !== planSequence) return
    applyPlan(next, false)
  } catch (err) {
    if (mine !== planSequence) return
    planError.value = err
    applyServerFields(err)
    const copy = describeError(err)
    if (copy.status === 429) startCooldown(apiRetryAfterSeconds(err) ?? 10)
  } finally {
    if (mine === planSequence) planLoading.value = false
  }
}

function onIdChange(value: string): void {
  draftId.value = value
  if (apiErrorCode(actionErrorRaw.value) === 'device_exists') {
    actionError.value = null
    actionErrorRaw.value = null
    errorStep.value = ''
  }
  if (locked.value || !selectedPluginId.value || !selectedModelId.value) return
  if (idTimer !== null) window.clearTimeout(idTimer)
  idTimer = window.setTimeout(() => {
    idTimer = null
    void loadPlan()
  }, 600)
}

function isCreatedDeviceStep(step: SetupStep): boolean {
  return CREATED_DEVICE_STEPS.has(step.id)
}

function toggleStep(step: SetupStep): void {
  if (busyStep.value || cooldownActive.value) return
  if (stepDisabled(step)) {
    // The params card stays reachable in an edit collision so a new id can be
    // entered; every other disabled step must not open (and cannot POST).
    const collisionEditor = step.id === 'params' && idCollision.value
    if (!collisionEditor) {
      if (activeStepId.value === step.id) activeStepId.value = ''
      return
    }
  }
  activeStepId.value = activeStepId.value === step.id ? '' : step.id
}

function blockedReasonFor(step: SetupStep): string {
  if (idCollision.value) return t('v2.setup.params.modeEditCollisionHint')
  const blocked = plan.value?.blocked
  if (blocked?.code === 'plug_unavailable') return t('v2.setup.blocked.plugHint')
  if (blocked && step.scope === 'device') {
    if (blocked.code === 'house_setup_incomplete') {
      return t('v2.setup.blocked.deviceStepsDisabled')
    }
    return t('v2.setup.blocked.step', { reason: blocked.message })
  }
  if (isCreatedDeviceStep(step) && !createdDeviceId.value) {
    return t('v2.setup.blocked.createFirst')
  }
  return ''
}

function stepDisabled(step: SetupStep): boolean {
  if (idCollision.value) return true
  // Add/first-run: wake/connect/test/verify need the id returned by
  // `device.create`; a draft id must never reach those steps.
  if (isCreatedDeviceStep(step) && !createdDeviceId.value) return true
  const blocked = plan.value?.blocked
  if (!blocked) return false
  // A missing/unusable plug blocks every step, including the house ones:
  // `POST /setup/plan/steps/*` would 409 before dispatch.
  if (blocked.code === 'plug_unavailable') return true
  return step.scope === 'device'
}

function stepHeadDisabled(step: SetupStep): boolean {
  // The header must look and announce the same state as `toggleStep`: a 429
  // cooldown closes every step header even when the step itself is runnable.
  return stepDisabled(step) || cooldownActive.value
}

function clearActionError(): void {
  actionError.value = null
  actionErrorRaw.value = null
  errorStep.value = ''
}

function clearServerField(key: string): void {
  // A host/port edit invalidates the whole failed request: `port_conflict`
  // supplies the banner, the device links and the inline hint from
  // `details.port`/`details.device_ids`, and all of them are stale once the
  // operator types a different host or port. `id` keeps the narrower
  // `onIdChange` path (it owns `device_exists`).
  if (key === 'host' || key === 'listen_port') {
    clearActionError()
    serverFields.value = {}
    return
  }
  if (!(key in serverFields.value)) return
  const next = { ...serverFields.value }
  delete next[key]
  serverFields.value = next
}

function isKnownField(key: string): boolean {
  return (
    ['id', 'room', 'host', 'listen_port', 'wake_words', 'plugin_id', 'model_id'].includes(
      key,
    ) ||
    /^wake_words:\d+$/.test(key) ||
    key.startsWith('params.')
  )
}

function applyServerFields(err: unknown): void {
  const copy = describeError(err)
  const map: Record<string, string> = {}
  for (const field of copy.fields) {
    const key = setupFieldKey(field.path)
    if (isKnownField(key)) map[key] = field.message || t('v2.setup.params.invalid')
  }
  if (apiErrorCode(err) === 'port_conflict') {
    map.listen_port = map.listen_port || t('v2.setup.params.portConflictHint')
  }
  if (apiErrorCode(err) === 'device_exists') {
    map.id = map.id || t('v2.setup.params.deviceExistsHint')
  }
  if (apiErrorCode(err) === 'plug_unavailable') {
    map.plugin_id = map.plugin_id || t('v2.error.plugUnavailable')
  }
  serverFields.value = map
}

/**
 * A resolved request is not automatically a success. `keys.test` / `device.test`
 * report logical `ok:false` in a 200 body, and `verify.say` only counts when the
 * new plan says `verified:true`. Everything else keeps the green "done" banner.
 */
function actionSucceeded(action: string, response: SetupStepResponse): boolean {
  if (action === 'keys.test' || action === 'device.test') {
    return response.result?.ok !== false
  }
  if (action === 'verify.say') return response.plan.verified === true
  return true
}

async function runStep(
  step: SetupStep,
  action: string,
  values: Record<string, unknown>,
): Promise<void> {
  if (busyStep.value || cooldownActive.value) return
  if (!step.actions.includes(action)) return
  // Refuse disabled steps here as well as in the template: no `device_id` may
  // leave the page before `device.create` returned one.
  if (stepDisabled(step)) return
  busyStep.value = step.id
  errorStep.value = ''
  actionError.value = null
  actionErrorRaw.value = null
  lastAction.value = ''
  lastResult.value = null
  lastOutcome.value = 'ok'
  serverFields.value = {}
  try {
    // Only the id the server returned (create response or params evidence) is
    // ever sent; the draft input is not a device.
    const targetId =
      paramsCreated.value && createdDeviceId.value ? createdDeviceId.value : ''
    const body: SetupStepRequest = {
      plugin_id: selectedPluginId.value,
      model_id: selectedModelId.value,
      action,
      values,
    }
    if (targetId && action !== 'device.create') body.device_id = targetId
    if (action === 'device.create') {
      body.values = {
        ...values,
        plugin_id: selectedPluginId.value,
        model_id: selectedModelId.value,
      }
    }
    const response = await api.setupStep(step.id, body)
    if (action === 'device.create') {
      createdByWizard.value = true
      createdDevice.value = response.device
      if (response.device) draftId.value = response.device.id
    }
    lastResult.value = response.result ?? null
    lastAction.value = `${step.id}:${action}`
    lastOutcome.value = actionSucceeded(action, response) ? 'ok' : 'neutral'
    lastRestart.value = response.restart
    // A neutral/failed logical outcome must not advance: the plan's `next_step`
    // may still move because evidence is stored (a connected device makes
    // `test_done` true even when `device.test` returned ok:false), and jumping to
    // the next card would hide the failed checks the operator has to fix.
    applyPlan(response.plan, lastOutcome.value === 'ok')
  } catch (err) {
    actionErrorRaw.value = err
    const copy = describeError(err)
    actionError.value = copy
    errorStep.value = step.id
    if (step.id === 'params') applyServerFields(err)
    const code = apiErrorCode(err)
    if (copy.status === 429) startCooldown(apiRetryAfterSeconds(err) ?? 10)
    if (code === 'house_setup_incomplete' || code === 'plug_unavailable') {
      // The stale plan no longer reflects the blocker; re-read the server truth.
      void loadPlan()
    }
  } finally {
    busyStep.value = ''
  }
}

function lastResultFor(stepId: string): Record<string, unknown> | null {
  return lastAction.value.startsWith(`${stepId}:`) ? lastResult.value : null
}

function errorFor(stepId: string): unknown {
  return errorStep.value === stepId ? actionErrorRaw.value : null
}

watch(selectedPluginId, () => {
  const list = models.value
  const next = list.length === 1 ? list[0].model_id : ''
  if (selectedModelId.value !== next) selectedModelId.value = next
})

watch([selectedPluginId, selectedModelId], () => {
  resetPlan()
  if (selectedPluginId.value && selectedModelId.value) void loadPlan()
})

onMounted(() => {
  if (!featureEnabled) return
  void pluginsResource.load()
  void houseResource.load()
})

onBeforeUnmount(() => {
  if (idTimer !== null) window.clearTimeout(idTimer)
})
</script>

<template>
  <div class="v2-page">
    <StateBlock
      v-if="!featureEnabled"
      kind="unavailable"
      :title="t('v2.setup.featureOff')"
      :hint="t('v2.setup.featureOffHint')"
    >
      <RouterLink class="btn" to="/devices">{{ t('v2.setup.backDevices') }}</RouterLink>
    </StateBlock>

    <template v-else>
      <RouterLink class="v2-back" to="/devices">
        <AppIcon name="arrowRight" :size="14" class="v2-back-icon" />
        {{ t('v2.setup.backDevices') }}
      </RouterLink>

      <header class="v2-page-head">
        <div class="v2-page-title">
          <span class="kicker">{{ t('v2.setup.badge') }}</span>
          <h1>{{ title }}</h1>
          <p class="lede">{{ lede }}</p>
        </div>
        <div class="v2-page-actions">
          <span v-if="plan" class="badge" data-testid="setup-mode">
            {{ t('v2.setup.summary.mode') }}: {{ modeLabel(plan.mode) }}
          </span>
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
        :title="t('v2.setup.loadFailed')"
        :detail="pluginErrorCopy.detail"
        :hint="pluginErrorCopy.hint"
        :status="pluginErrorCopy.status"
        :request-id="pluginErrorCopy.requestId"
        show-retry
        @retry="pluginsResource.load(); houseResource.load()"
      />

      <StateBlock
        v-else-if="availablePlugins.length === 0"
        kind="empty"
        :title="t('v2.setup.select.noPlugins')"
        :hint="t('v2.setup.select.noPluginsHint')"
      >
        <button type="button" class="btn" @click="pluginsResource.load()">
          {{ t('v2.common.refresh') }}
        </button>
      </StateBlock>

      <template v-else>
        <section class="v2-card" data-testid="setup-selection">
          <div class="v2-card-head">
            <h2 class="v2-card-title">{{ t('v2.setup.select.title') }}</h2>
            <span v-if="selectedModel" class="badge badge-muted">
              {{ transportLabel(selectedModel.transport) }}
            </span>
          </div>
          <p class="v2-card-lede note">{{ t('v2.setup.select.lede') }}</p>

          <div class="field-row">
            <div class="field-meta">
              <label class="field-label" for="setup-plugin">{{ t('v2.setup.select.plugin') }}</label>
            </div>
            <div class="field-control">
              <select
                id="setup-plugin"
                v-model="selectedPluginId"
                class="input"
                :disabled="locked || planLoading"
                data-testid="setup-plugin"
              >
                <option value="">{{ t('v2.setup.select.pluginPlaceholder') }}</option>
                <option v-for="plugin in availablePlugins" :key="plugin.id" :value="plugin.id">
                  {{ plugin.name }} · {{ plugin.id }}
                </option>
              </select>
              <p v-if="serverFields.plugin_id" class="field-error" data-testid="setup-plugin-error">
                {{ serverFields.plugin_id }}
              </p>
              <p v-if="selectedPlugin" class="field-meta-line">
                <span class="mono">{{ selectedPlugin.id }}</span>
                <span>{{ t('v2.setup.select.pluginVersion', { version: selectedPlugin.version }) }}</span>
              </p>
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <label class="field-label" for="setup-model">{{ t('v2.setup.select.model') }}</label>
            </div>
            <div class="field-control">
              <select
                id="setup-model"
                v-model="selectedModelId"
                class="input"
                :disabled="locked || planLoading || !selectedPlugin"
                data-testid="setup-model"
              >
                <option value="">{{ t('v2.setup.select.modelPlaceholder') }}</option>
                <option v-for="model in models" :key="model.model_id" :value="model.model_id">
                  {{ model.name }} · {{ model.model_id }}
                </option>
              </select>
              <p v-if="serverFields.model_id" class="field-error" data-testid="setup-model-error">
                {{ serverFields.model_id }}
              </p>
              <p v-if="selectedPlugin && models.length === 0" class="field-hint">
                {{ t('v2.setup.select.pluginEmpty') }}
              </p>
              <p v-if="selectedModel" class="field-meta-line">
                {{ t('v2.setup.select.selectedModel', { name: selectedModel.name }) }}
              </p>
              <CapabilityChips
                v-if="selectedModel"
                :ids="selectedModel.capabilities"
                :unknown="[]"
              />
            </div>
          </div>

          <p v-if="locked && createdDeviceId" class="note" data-testid="setup-selection-locked">
            {{ t('v2.setup.select.locked') }}
          </p>
          <p v-else-if="props.mode === 'first-run' && (!plan || plan.mode === 'first-run')" class="note">
            {{ t('v2.setup.select.firstRunNote') }}
          </p>
          <p v-else-if="props.mode === 'add'" class="note">
            {{ t('v2.setup.select.addNote') }}
          </p>

          <p
            v-if="props.mode === 'first-run' && plan && plan.mode !== 'first-run' && !createdByWizard"
            class="note"
            data-testid="setup-first-run-already"
          >
            {{ t('v2.setup.select.firstRunAlready') }}
          </p>

          <div class="v2-save-bar">
            <button
              type="button"
              class="btn btn-sm"
              data-testid="setup-plan-refresh"
              :disabled="planLoading || cooldownActive || !selectedPluginId || !selectedModelId"
              @click="loadPlan"
            >
              {{ t('v2.setup.refreshPlan') }}
            </button>
            <span v-if="planLoading" class="note">{{ t('v2.setup.planLoading') }}</span>
          </div>
        </section>

        <div v-if="planError" class="banner banner-warn v2-banner" role="alert" data-testid="setup-plan-error">
          <AppIcon name="alert" :size="18" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.setup.loadFailed') }}</strong>
            <p class="note mono v2-break">{{ describeError(planError).detail }}</p>
            <p class="note">{{ describeError(planError).hint || t('v2.setup.loadFailedHint') }}</p>
            <button type="button" class="btn btn-sm" :disabled="cooldownActive" @click="loadPlan">
              {{ t('v2.common.retry') }}
            </button>
          </div>
        </div>

        <div
          v-if="cooldownActive"
          class="banner banner-warn v2-banner"
          role="status"
          data-testid="setup-cooldown"
        >
          <AppIcon name="alert" :size="18" />
          <div class="v2-banner-body">
            <p class="note">{{ t('v2.setup.action.retryAfter', { n: cooldownSeconds }) }}</p>
          </div>
        </div>

        <StateBlock
          v-if="planLoading && !plan"
          kind="loading"
          :title="t('v2.setup.planLoading')"
        />

        <template v-else-if="plan">
          <div v-if="plan.blocked" class="banner banner-warn v2-banner" role="status" data-testid="setup-blocked">
            <AppIcon name="alert" :size="18" />
            <div class="v2-banner-body">
              <strong>
                {{ t('v2.setup.blocked.title') }} ·
                <span class="mono">{{ plan.blocked.code }}</span>
              </strong>
              <p>{{ blockedCopy }}</p>
              <p class="note mono v2-break">{{ plan.blocked.message }}</p>
              <p v-if="missingSteps.length" class="note" data-testid="setup-blocked-missing">
                {{ t('v2.setup.blocked.missingSteps', { steps: missingSteps.map(stepLabel).join(', ') }) }}
              </p>
              <p v-if="plan.blocked.code === 'house_setup_incomplete'" class="note">
                {{ t('v2.setup.blocked.deviceStepsDisabled') }}
              </p>
              <p v-if="plan.blocked.code === 'plug_unavailable'" class="note">
                {{ t('v2.setup.blocked.plugHint') }}
              </p>
            </div>
          </div>

          <section class="v2-card v2-setup-summary" data-testid="setup-summary">
            <div class="v2-card-head">
              <h2 class="v2-card-title">{{ t('v2.setup.summary.title') }}</h2>
              <div class="v2-setup-summary-badges">
                <span
                  class="badge"
                  :class="plan.complete ? 'badge-ok' : 'badge-warn'"
                  data-testid="setup-complete"
                >
                  {{ plan.complete ? t('v2.setup.summary.complete') : t('v2.setup.summary.stepsDone', { done: doneCount, total: steps.length }) }}
                </span>
                <span
                  class="badge"
                  :class="plan.verified ? 'badge-ok' : 'badge-muted'"
                  data-testid="setup-verified"
                >
                  {{ plan.verified ? t('v2.setup.summary.verified') : t('v2.setup.summary.notVerified') }}
                </span>
              </div>
            </div>
            <dl class="v2-kv v2-setup-kv">
              <div>
                <dt>{{ t('v2.setup.summary.plugin') }}</dt>
                <dd>
                  {{ plan.plugin.name || plan.plugin.id || t('v2.common.na') }}
                  <span v-if="plan.plugin.version" class="mono"> · {{ plan.plugin.version }}</span>
                </dd>
              </div>
              <div>
                <dt>{{ t('v2.setup.summary.deviceId') }}</dt>
                <dd class="mono">{{ createdDeviceId || plan.device_id || draftId || t('v2.common.na') }}</dd>
              </div>
              <div v-if="plan.next_step">
                <dt>{{ t('v2.setup.summary.next') }}</dt>
                <dd>{{ nextStep ? stepLabel(nextStep.id) : plan.next_step }}</dd>
              </div>
            </dl>
            <div v-if="plan.complete" class="v2-save-bar">
              <RouterLink
                v-if="createdDeviceId || plan.device_id"
                class="btn btn-primary btn-sm"
                :to="`/devices/${createdDeviceId || plan.device_id}`"
                data-testid="setup-open-device"
              >
                {{ t('v2.setup.summary.openDevice') }}
              </RouterLink>
              <RouterLink class="btn btn-sm" to="/run">
                {{ t('v2.setup.summary.runPage') }}
              </RouterLink>
            </div>
          </section>

          <RestartNotice v-if="lastRestart" :restart="lastRestart" :show-retry="false" />

          <div
            v-if="lastAction"
            class="banner v2-banner"
            :class="lastOutcome === 'ok' ? 'banner-ok' : ''"
            role="status"
            :data-testid="lastOutcome === 'ok' ? 'setup-action-ok' : 'setup-action-neutral'"
            :data-outcome="lastOutcome"
          >
            <AppIcon :name="lastOutcome === 'ok' ? 'check' : 'alert'" :size="18" />
            <div class="v2-banner-body">
              <strong v-if="lastOutcome === 'ok'">
                {{ t('v2.setup.action.ok', { action: lastActionLabel }) }}
              </strong>
              <strong v-else>
                {{ t('v2.setup.action.requestDone', { action: lastActionLabel }) }}
              </strong>
            </div>
          </div>

          <ErrorNotice :error="actionError" data-testid="setup-error" />

          <div
            v-if="actionErrorRaw && (allowedActions.length || unknownStepList.length || missingPrereqs.length || apiErrorCode(actionErrorRaw) === 'step_in_progress' || missingPrereqUnknown)"
            class="v2-setup-error-meta"
            data-testid="setup-error-meta"
          >
            <p v-if="allowedActions.length">
              <strong>{{ t('v2.setup.action.allowed') }}</strong>
              <span class="mono">{{ allowedActions.join(', ') }}</span>
            </p>
            <p v-if="unknownStepList.length">
              <strong>{{ t('v2.setup.action.unknownStep') }}</strong>
              <span class="mono">{{ unknownStepList.join(', ') }}</span>
            </p>
            <p v-if="missingPrereqs.length">
              <strong>{{ t('v2.setup.action.missingPrereq', { steps: missingPrereqs.join(', ') }) }}</strong>
            </p>
            <p v-if="missingPrereqUnknown" class="note mono v2-break" data-testid="setup-error-details">
              {{ errorDetailsText }}
            </p>
            <p v-if="apiErrorCode(actionErrorRaw) === 'step_in_progress'" class="note">
              {{ t('v2.setup.action.stepInProgress') }}
            </p>
          </div>

          <div class="v2-setup-steps">
            <section
              v-for="(step, index) in steps"
              :key="step.id"
              class="v2-card v2-setup-step"
              :class="[
                stepStatusClass(step.status),
                {
                  'is-next': step.id === plan.next_step,
                  'is-active': step.id === activeStepId,
                  'is-disabled': stepHeadDisabled(step),
                },
              ]"
              :data-testid="`setup-step-${step.id}`"
              :data-step-id="step.id"
              :data-status="step.status"
              :data-scope="step.scope"
              :data-disabled="stepDisabled(step) ? 'true' : 'false'"
            >
              <button
                type="button"
                class="v2-setup-step-head"
                :aria-expanded="activeStepId === step.id"
                :aria-disabled="stepHeadDisabled(step) ? 'true' : 'false'"
                :data-head-disabled="stepHeadDisabled(step) ? 'true' : 'false'"
                @click="toggleStep(step)"
              >
                <span class="v2-setup-step-index">{{ index + 1 }}</span>
                <span class="v2-setup-step-title">
                  <strong>{{ stepLabel(step.id) }}</strong>
                  <span class="note">{{ stepDescription(step.id) }}</span>
                </span>
                <span class="badge badge-muted">{{ setupScopeLabel(step.scope) }}</span>
                <span class="badge" :class="step.status === 'done' ? 'badge-ok' : step.status === 'error' ? 'badge-fail' : step.status === 'skipped' ? 'badge-muted' : 'badge-warn'">
                  {{ setupStatusLabel(step.status) }}
                </span>
                <span v-if="!step.required" class="badge badge-muted">{{ t('v2.setup.optional') }}</span>
                <span v-if="step.id === plan.next_step" class="badge badge-accent" data-testid="setup-next-badge">
                  {{ t('v2.setup.next') }}
                </span>
                <AppIcon
                  name="chevronDown"
                  :size="16"
                  class="v2-setup-chevron"
                  :class="{ 'is-open': activeStepId === step.id }"
                />
              </button>

              <div v-if="activeStepId === step.id" class="v2-setup-step-content">
                <template v-if="step.id === 'params'">
                  <div
                    v-if="createdDeviceId"
                    class="v2-setup-subcard"
                    data-testid="setup-params-created"
                  >
                    <p class="v2-setup-done">
                      {{ t('v2.setup.params.created', { id: createdDeviceId }) }}
                    </p>
                    <p class="note">{{ t('v2.setup.params.createdHint') }}</p>
                    <RouterLink class="btn btn-sm" :to="`/devices/${createdDeviceId}`">
                      {{ t('v2.setup.params.openDevice') }}
                    </RouterLink>
                  </div>

                  <template v-else>
                    <div
                      v-if="idCollision"
                      class="v2-setup-subcard"
                      data-testid="setup-id-collision"
                    >
                      <strong>{{ t('v2.setup.params.modeEditCollision') }}</strong>
                      <p class="note">{{ t('v2.setup.params.modeEditCollisionHint') }}</p>
                      <div class="field-row">
                        <div class="field-meta">
                          <label class="field-label" for="setup-collision-id">
                            {{ t('v2.setup.params.id') }}
                          </label>
                        </div>
                        <div class="field-control">
                          <input
                            id="setup-collision-id"
                            :value="draftId"
                            type="text"
                            class="input mono"
                            autocomplete="off"
                            spellcheck="false"
                            data-testid="setup-collision-id"
                            @input="onIdChange(($event.target as HTMLInputElement).value)"
                          />
                        </div>
                      </div>
                      <RouterLink
                        v-if="plan.device_id"
                        class="btn btn-sm"
                        :to="`/devices/${plan.device_id}`"
                      >
                        {{ t('v2.setup.select.openExisting') }}
                      </RouterLink>
                    </div>

                    <p
                      v-else-if="stepDisabled(step)"
                      class="note v2-setup-blocked-step"
                      :data-testid="`setup-blocked-${step.id}`"
                    >
                      {{ blockedReasonFor(step) }}
                    </p>

                    <!--
                      Keep the draft form mounted while a collision is resolved
                      (`v-show` instead of unmounting): the room/host/port the
                      operator already typed must survive the edit -> add round
                      trip that a new id triggers.
                    -->
                    <fieldset
                      v-show="!idCollision && !stepDisabled(step)"
                      class="v2-setup-fieldset"
                      :disabled="cooldownActive"
                    >
                      <SetupParamsStep
                        :step="step"
                        :action="createAction"
                        :plugin-id="selectedPluginId"
                        :plugin-name="plan.plugin.name || selectedPlugin?.name || selectedPluginId"
                        :model-id="selectedModelId"
                        :model-name="selectedModel?.name ?? ''"
                        :multi-device-enabled="multiDeviceEnabled"
                        :busy="busyStep === step.id"
                        :initial-id="draftId"
                        :server-fields="serverFields"
                        :conflict-ids="conflictIds"
                        :conflict-port="conflictPort"
                        :existing-id="existingId"
                        @submit="(action, values) => runStep(step, action, values)"
                        @field-edit="clearServerField"
                        @id-change="onIdChange"
                      />
                    </fieldset>
                  </template>
                </template>

                <p
                  v-else-if="stepDisabled(step)"
                  class="note v2-setup-blocked-step"
                  :data-testid="`setup-blocked-${step.id}`"
                >
                  {{ blockedReasonFor(step) }}
                </p>

                <template v-else>
                  <fieldset class="v2-setup-fieldset" :disabled="cooldownActive">
                  <p v-if="!step.actions.length && step.id !== 'models' && step.id !== 'connect'" class="note">
                    {{ t('v2.setup.action.noActions') }}
                  </p>

                  <SetupKeysStep
                    v-if="step.id === 'keys'"
                    :step="step"
                    :busy="busyStep === step.id"
                    :result="lastResultFor(step.id)"
                    :error-raw="errorFor(step.id)"
                    @submit="(action, values) => runStep(step, action, values)"
                  />

                  <SetupModelsStep
                    v-else-if="step.id === 'models'"
                    :step="step"
                    @refresh="loadPlan"
                  />

                  <SetupPrepareStep
                    v-else-if="step.id === 'prepare'"
                    :step="step"
                    :busy="busyStep === step.id"
                    @submit="(action, values) => runStep(step, action, values)"
                  />

                  <SetupWakeStep
                    v-else-if="step.id === 'wake'"
                    :step="step"
                    :busy="busyStep === step.id"
                    @submit="(action, values) => runStep(step, action, values)"
                  />

                  <SetupConnectStep
                    v-else-if="step.id === 'connect'"
                    :step="step"
                    :prepare-step="prepareStep"
                    @refresh="loadPlan"
                  />

                  <SetupTestStep
                    v-else-if="step.id === 'test'"
                    :step="step"
                    :busy="busyStep === step.id"
                    :result="lastResultFor(step.id)"
                    @submit="(action, values) => runStep(step, action, values)"
                  />

                  <SetupVerifyStep
                    v-else-if="step.id === 'verify'"
                    :step="step"
                    :busy="busyStep === step.id"
                    :cooldown="cooldownActive"
                    @submit="(action, values) => runStep(step, action, values)"
                  />

                  <p v-else class="note">
                    {{ t('v2.setup.action.unknownAction', { action: step.id }) }}
                  </p>
                  </fieldset>
                </template>
              </div>
            </section>
          </div>
        </template>

        <StateBlock
          v-else-if="!planLoading"
          kind="empty"
          :title="t('v2.setup.select.title')"
          :hint="t('v2.setup.select.lede')"
        />
      </template>
    </template>
  </div>
</template>
