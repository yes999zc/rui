<script setup lang="ts">
/**
 * Capability card: effective ids, the declared/probed/effective table and the
 * unknown ids. Labels come from the core catalog (`/api/v2/capabilities`); when
 * that endpoint is unavailable the raw id is shown and a note explains why.
 */
import { computed } from 'vue'
import type { DeviceDetail } from '../../api'
import { capabilityError, capabilityLabel } from '../../capabilities'
import { capabilityReasonLabel, t } from '../../i18n'
import CapabilityChips from '../CapabilityChips.vue'

const props = defineProps<{ detail: DeviceDetail }>()

const effectiveIds = computed(() => {
  const entry = props.detail
  return (
    entry.effective_capabilities ??
    entry.capabilities.filter((cap) => cap.effective).map((cap) => cap.id)
  )
})
const declaredEntries = computed(() =>
  props.detail.capabilities.filter((cap) => cap.declared),
)
const unknownIds = computed(() => props.detail.unknown_capabilities ?? [])
const missingBargeIn = computed(() => props.detail.missing_for_barge_in ?? [])
const catalogOff = computed(() => Boolean(capabilityError.value) && Boolean(props.detail))

function boolText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.common.na')
  return value ? t('v2.common.yes') : t('v2.common.no')
}
</script>

<template>
  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.capabilities.title') }}</h2>
    <p class="note v2-card-lede">{{ t('v2.capabilities.lede') }}</p>

    <div class="v2-cap-group">
      <h3>{{ t('v2.capabilities.group.effective') }}</h3>
      <CapabilityChips :ids="effectiveIds" :empty-label="t('v2.capabilities.emptyEffective')" />
    </div>

    <div class="v2-cap-group">
      <h3>{{ t('v2.capabilities.group.declared') }}</h3>
      <p v-if="!declaredEntries.length" class="empty">
        {{ t('v2.capabilities.emptyDeclared') }}
      </p>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>{{ t('v2.capabilities.table.capability') }}</th>
              <th>{{ t('v2.capabilities.table.declared') }}</th>
              <th>{{ t('v2.capabilities.table.probed') }}</th>
              <th>{{ t('v2.capabilities.table.effective') }}</th>
              <th>{{ t('v2.capabilities.table.reason') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cap in declaredEntries" :key="cap.id">
              <td>
                <strong>{{ capabilityLabel(cap.id) }}</strong>
                <span class="meta mono v2-block">{{ cap.id }}</span>
              </td>
              <td>{{ boolText(cap.declared) }}</td>
              <td>
                {{
                  cap.probed === null
                    ? t('v2.capabilities.probeNotApplicable')
                    : boolText(cap.probed)
                }}
              </td>
              <td>
                <span class="badge" :class="cap.effective ? 'badge-ok' : 'badge-muted'">
                  {{ cap.effective ? t('v2.common.yes') : t('v2.common.no') }}
                </span>
              </td>
              <td>{{ capabilityReasonLabel(cap.reason) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="v2-cap-group">
      <h3>{{ t('v2.capabilities.group.unknown') }}</h3>
      <CapabilityChips
        :ids="[]"
        :unknown="unknownIds"
        :empty-label="t('v2.capabilities.emptyUnknown')"
      />
    </div>

    <p v-if="missingBargeIn.length" class="note v2-warn-text">
      {{ t('v2.capabilities.missingBargeIn', { ids: missingBargeIn.join(', ') }) }}
    </p>
    <p v-if="catalogOff" class="note">{{ t('v2.capabilities.catalogOff') }}</p>
  </section>
</template>
