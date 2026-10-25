<script setup lang="ts">
/**
 * `test` step: evidence-only `device.test`. It never plays audio or changes
 * LEDs/volume; the server's check rows are rendered verbatim.
 *
 * `result` only exists for a run started in this page session. After a reload it
 * is null and the plan's stored evidence is the last server-side run:
 *
 * - `evidence.tested === true` means a probe ran, so `evidence.ok` decides the
 *   badge (green/red) and `evidence.checks` lists the reasons.
 * - `evidence.tested !== true` stays idle even when `evidence.ok` is true: the
 *   backend also reports `ok:true` for a connected device that was never tested,
 *   and painting that green would claim a pass that never happened.
 *
 * A live run still uses its own `result.ok` plus every check being
 * ok/skipped/not_applicable; a failed run stays red and lists the failing
 * checks (unknown statuses are treated as failures, not silently dropped).
 */
import { computed } from 'vue'
import type { SetupStep } from '../../api'
import { checkPasses, checkTone, checksFrom, setupChecks } from '../../setup'
import { formatDateTime, formatIso } from '../../format'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  busy: boolean
  /** `result` of the latest `device.test` sent from this page (null before a run). */
  result?: Record<string, unknown> | null
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
}>()

/** The live run wins; before it, the plan's stored evidence is rendered as-is. */
const runResult = computed<Record<string, unknown> | null>(() =>
  props.result && typeof props.result === 'object' ? props.result : null,
)
const checks = computed(() =>
  runResult.value ? checksFrom(runResult.value.checks) : setupChecks(props.step),
)
// Anything that is not explicitly acceptable needs a reason row, including
// status strings this build does not know yet.
const failedChecks = computed(() => checks.value.filter((check) => !checkPasses(check.status)))
const allChecksPass = computed(
  () => checks.value.length > 0 && checks.value.every((check) => checkPasses(check.status)),
)
const ranOk = computed(
  () => runResult.value !== null && runResult.value.ok === true && allChecksPass.value,
)
const evidenceTested = computed(() => props.step.evidence?.tested === true)
const evidenceOk = computed(() => props.step.evidence?.ok === true)
const resultState = computed<'idle' | 'ok' | 'fail'>(() => {
  if (runResult.value) return ranOk.value ? 'ok' : 'fail'
  if (!evidenceTested.value) return 'idle'
  return evidenceOk.value ? 'ok' : 'fail'
})
/** A run (live or stored) exists, so an empty `checks` list is server data, not "never ran". */
const hasOutcome = computed(() => resultState.value !== 'idle')
const checkedAt = computed(() => {
  const result = runResult.value
  if (result) {
    const ts = result.checked_at
    if (typeof ts === 'number' && Number.isFinite(ts)) return formatDateTime(ts)
    if (typeof ts === 'string' && ts) return formatIso(ts)
  }
  const value = props.step.evidence?.at
  return typeof value === 'string' ? formatIso(value) : ''
})
const failureText = computed(() => {
  if (!failedChecks.value.length) return ''
  const ids = failedChecks.value.map((check) => check.id || check.code || '?').join(', ')
  return t('v2.setup.test.failedChecks', { checks: ids })
})
const canRun = computed(() => props.step.actions.includes('device.test'))

function run(): void {
  if (props.busy || !canRun.value) return
  emit('submit', 'device.test', {})
}
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.test.title') }}</h4>
    <p class="note">{{ t('v2.setup.test.hint') }}</p>

    <div class="v2-setup-evidence" data-testid="setup-test-result" :data-result="resultState">
      <span class="v2-summary-label">{{ t('v2.setup.test.result') }}</span>
      <p>
        <span
          class="badge"
          :class="resultState === 'ok' ? 'badge-ok' : resultState === 'fail' ? 'badge-fail' : 'badge-muted'"
          data-testid="setup-test-badge"
        >
          {{
            resultState === 'ok'
              ? t('v2.setup.test.ready')
              : resultState === 'fail'
                ? t('v2.setup.test.notReady')
                : t('v2.setup.test.notRun')
          }}
        </span>
        <span v-if="checkedAt" class="note"> · {{ t('v2.setup.test.at') }}: {{ checkedAt }}</span>
      </p>
    </div>

    <p
      v-if="failureText"
      class="field-error"
      data-testid="setup-test-failures"
      :data-failed="failedChecks.length"
    >
      {{ failureText }}
    </p>

    <div v-if="checks.length" class="v2-setup-checks table-wrap" data-testid="setup-test-checks">
      <table class="table">
        <thead>
          <tr>
            <th>{{ t('v2.setup.test.check') }}</th>
            <th>{{ t('v2.setup.test.status') }}</th>
            <th>{{ t('v2.setup.test.code') }}</th>
            <th>{{ t('v2.setup.test.hintLabel') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(check, index) in checks"
            :key="`${check.id}-${index}`"
            :data-check-id="check.id"
            :data-check-status="check.status"
            :class="{ 'is-fail': check.status === 'fail' }"
          >
            <td class="mono">{{ check.id || t('v2.common.na') }}</td>
            <td>
              <span class="badge" :class="checkTone(check.status)">
                {{ check.status || t('v2.common.na') }}
              </span>
            </td>
            <td class="mono">{{ check.code || t('v2.common.na') }}</td>
            <td class="note">{{ check.hint }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="note" data-testid="setup-test-empty">
      {{ hasOutcome ? t('v2.setup.test.emptyChecks') : t('v2.setup.test.empty') }}
    </p>

    <div class="v2-save-bar">
      <button
        v-if="canRun"
        type="button"
        class="btn btn-primary btn-sm"
        data-testid="setup-test-submit"
        :disabled="busy"
        @click="run"
      >
        {{ busy ? t('v2.setup.test.running') : t('v2.setup.test.run') }}
      </button>
    </div>
  </div>
</template>
