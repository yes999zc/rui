<script setup lang="ts">
/**
 * Evidence-only connection test (contract §7.20). Running it never plays audio
 * and never changes LEDs or volume; it only reports per-check evidence
 * (ok / fail / not_applicable / skipped) with a stable code and hint.
 */
import { ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import { api, type DeviceTestResult, type TestCheck } from '../../api'
import { describeError, type ErrorCopy } from '../../errors'
import { formatDateTime, jsonText } from '../../format'
import { t, transportLabel } from '../../i18n'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from './ErrorNotice.vue'

const props = defineProps<{ deviceId: string }>()

const running = ref(false)
const result = ref<DeviceTestResult | null>(null)
const errorCopy = ref<ErrorCopy | null>(null)

async function run(): Promise<void> {
  if (running.value) return
  running.value = true
  errorCopy.value = null
  try {
    result.value = await api.deviceTest(props.deviceId)
  } catch (err) {
    errorCopy.value = describeError(err)
  } finally {
    running.value = false
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'ok':
      return t('v2.test.status.ok')
    case 'fail':
      return t('v2.test.status.fail')
    case 'not_applicable':
      return t('v2.test.status.not_applicable')
    case 'skipped':
      return t('v2.test.status.skipped')
    default:
      return status
  }
}

function statusTone(status: string): string {
  if (status === 'ok') return 'badge-ok'
  if (status === 'fail') return 'badge-fail'
  return 'badge-muted'
}

function checkDetail(check: TestCheck): string {
  return jsonText(check.detail ?? {})
}

function isFailure(status: string): boolean {
  return status === 'fail'
}

watch(
  () => props.deviceId,
  () => {
    result.value = null
    errorCopy.value = null
  },
)
</script>

<template>
  <section class="v2-card">
    <div class="v2-card-head">
      <h2 class="v2-card-title">{{ t('v2.test.title') }}</h2>
      <button type="button" class="btn btn-sm" :disabled="running" @click="run">
        <AppIcon name="activity" :size="14" />
        {{ running ? t('v2.test.running') : result ? t('v2.test.rerun') : t('v2.test.run') }}
      </button>
    </div>
    <p class="note v2-card-lede">{{ t('v2.test.lede') }}</p>

    <ErrorNotice :error="errorCopy" show-retry @retry="run" />
    <p v-if="errorCopy && result" class="note v2-warn-text">{{ t('v2.test.stale') }}</p>

    <StateBlock
      v-if="!result && !errorCopy && !running"
      kind="empty"
      :title="t('v2.test.empty')"
      :hint="t('v2.test.emptyHint')"
    />

    <div v-if="result" class="v2-test-result">
      <div class="v2-test-summary">
        <div class="v2-test-summary-item">
          <span class="badge" :class="result.ok ? 'badge-ok' : 'badge-fail'">
            {{ result.ok ? t('v2.test.ok') : t('v2.test.fail') }}
          </span>
        </div>
        <div class="v2-test-summary-item">
          <span class="meta">{{ t('v2.test.ready') }}</span>
          <strong>{{ result.ready ? t('v2.common.yes') : t('v2.common.no') }}</strong>
        </div>
        <div class="v2-test-summary-item">
          <span class="meta">{{ t('v2.test.waited', { n: result.waited_ms }) }}</span>
        </div>
        <div class="v2-test-summary-item">
          <span class="meta">{{ t('v2.test.transport') }}</span>
          <strong>{{ transportLabel(String(result.transport)) }}</strong>
        </div>
        <div class="v2-test-summary-item">
          <span class="meta">{{ t('v2.test.checkedAt') }}</span>
          <strong class="mono">{{ formatDateTime(result.checked_at) }}</strong>
        </div>
      </div>

      <h3>{{ t('v2.test.checks') }}</h3>
      <div class="table-wrap">
        <table class="table v2-check-table">
          <thead>
            <tr>
              <th>{{ t('v2.test.check') }}</th>
              <th>{{ t('v2.test.status') }}</th>
              <th>{{ t('v2.test.code') }}</th>
              <th>{{ t('v2.test.detail') }}</th>
              <th>{{ t('v2.test.hint') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="check in result.checks ?? []"
              :key="check.id"
              :class="{ 'is-fail': isFailure(check.status) }"
            >
              <td class="mono">{{ check.id }}</td>
              <td>
                <span class="badge" :class="statusTone(check.status)">
                  {{ statusLabel(check.status) }}
                </span>
              </td>
              <td class="mono">{{ check.code }}</td>
              <td>
                <details class="v2-check-detail">
                  <summary>{{ t('v2.test.detail') }}</summary>
                  <pre class="cmd v2-json">{{ checkDetail(check) }}</pre>
                </details>
              </td>
              <td>
                <span v-if="check.hint" class="note v2-break">{{ check.hint }}</span>
                <span v-else class="meta">{{ t('v2.common.na') }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="result.errors?.length" class="v2-test-errors">
        <h3>{{ t('v2.test.errors') }}</h3>
        <ul class="v2-warning-list">
          <li v-for="(failure, index) in result.errors ?? []" :key="`${failure.code}-${index}`">
            <span class="dot fail" aria-hidden="true" />
            <strong class="mono">{{ failure.code }}</strong>
            <span class="note v2-break">{{ failure.message }}</span>
            <span v-if="failure.hint" class="note v2-break">{{ failure.hint }}</span>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
