<script setup lang="ts">
/**
 * Public params plus legacy keys the manifest does not declare. Both are
 * read-only in M2; `unknown_params` documents what is kept in config but
 * ignored on write.
 */
import { computed } from 'vue'
import { displayValue } from '../../format'
import { t } from '../../i18n'

const props = defineProps<{
  params: Record<string, unknown>
  unknownParams: string[]
}>()

const rows = computed(() =>
  Object.entries(props.params ?? {}).map(([key, value]) => ({ key, value })),
)
const unknown = computed(() => props.unknownParams ?? [])
</script>

<template>
  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.detail.params') }}</h2>
    <p v-if="!rows.length" class="empty">{{ t('v2.detail.paramsEmpty') }}</p>
    <dl v-else class="v2-kv">
      <div v-for="row in rows" :key="row.key">
        <dt class="mono">{{ row.key }}</dt>
        <dd class="mono">{{ displayValue(row.value) }}</dd>
      </div>
    </dl>
  </section>

  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.detail.unknownParams') }}</h2>
    <p class="note v2-card-lede">{{ t('v2.detail.unknownParamsHint') }}</p>
    <p v-if="!unknown.length" class="empty">
      {{ t('v2.detail.unknownParamsEmpty') }}
    </p>
    <div v-else class="chips">
      <span v-for="key in unknown" :key="key" class="chip mono">{{ key }}</span>
    </div>
  </section>
</template>
