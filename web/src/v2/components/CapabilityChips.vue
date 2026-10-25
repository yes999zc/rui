<script setup lang="ts">
/** Effective capability chips; unknown ids get a neutral, clearly-labelled chip. */
import { capabilityDescription, capabilityLabel } from '../capabilities'
import { t } from '../i18n'

withDefaults(
  defineProps<{
    ids: string[]
    unknown?: string[]
    emptyLabel?: string
  }>(),
  {
    unknown: () => [],
    emptyLabel: '',
  },
)
</script>

<template>
  <div class="v2-chips">
    <span v-for="id in ids" :key="id" class="v2-cap" :title="capabilityDescription(id) || id">
      {{ capabilityLabel(id) }}
    </span>
    <span
      v-for="id in unknown"
      :key="`unknown-${id}`"
      class="v2-cap is-unknown"
      :title="t('v2.capabilities.unknownHint')"
    >
      {{ t('v2.capability.unknown', { id }) }}
    </span>
    <span v-if="ids.length + unknown.length === 0" class="v2-cap is-empty">
      {{ emptyLabel || t('v2.devices.noCapabilities') }}
    </span>
  </div>
</template>
