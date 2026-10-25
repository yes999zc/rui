<script setup lang="ts">
/**
 * Provenance tag. The contract's literal `source` id stays visible (reviewers
 * and operators grep for it); the translated explanation lives in the tooltip.
 */
import { computed } from 'vue'
import { provenanceDescription } from '../i18n'

const props = defineProps<{ source: string }>()

const tone = computed(() => {
  switch (props.source) {
    case 'device-override':
      return 'override'
    case 'global-config':
      return 'global'
    case 'device-manifest':
      return 'manifest'
    case 'unresolved':
      return 'unresolved'
    default:
      return 'core'
  }
})
</script>

<template>
  <span class="v2-source" :class="`is-${tone}`" :title="provenanceDescription(source)">
    <span class="v2-source-dot" aria-hidden="true" />
    <span class="mono">{{ source }}</span>
  </span>
</template>
