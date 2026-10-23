<script setup lang="ts">
/**
 * Compact provider-key status. Values are never edited here (they are write-only
 * on the Secrets page); this strip only answers "will these chains find a key?"
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../AppIcon.vue'
import { t } from '../../i18n'
import type { SecretMeta } from './types'

const props = defineProps<{ secrets: [string, SecretMeta][] }>()

const total = computed(() => props.secrets.length)
const configured = computed(() => props.secrets.filter(([, meta]) => meta.has_value).length)
const missing = computed(() => total.value - configured.value)

function usedBy(meta: SecretMeta): string {
  return (meta.used_by ?? []).join(', ')
}
</script>

<template>
  <details class="p-keys" :class="{ warn: missing > 0 }">
    <summary>
      <span class="kicker">{{ t('providers.key.title') }}</span>
      <span class="p-keys-count mono">{{ t('providers.key.summary', { configured, total }) }}</span>
      <span v-if="missing > 0" class="badge badge-warn">{{ t('providers.key.missingCount', { n: missing }) }}</span>
      <AppIcon name="chevronDown" :size="13" class="p-keys-chevron" />
    </summary>
    <div class="p-keys-body">
      <div class="p-keys-chips">
        <RouterLink
          v-for="[name, meta] in secrets"
          :key="name"
          to="/secrets"
          class="p-key-chip"
          :class="meta.has_value ? 'ok' : 'warn'"
          :title="usedBy(meta)"
        >
          <span class="dot" :class="meta.has_value ? 'ok' : 'warn'" />
          <code class="mono">{{ name }}</code>
          <span class="p-key-state">{{ meta.has_value ? t('providers.key.configured') : t('providers.key.missing') }}</span>
        </RouterLink>
      </div>
      <RouterLink to="/secrets" class="p-keys-manage">{{ t('providers.key.manage') }} →</RouterLink>
    </div>
  </details>
</template>

<style scoped>
.p-keys {
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  padding: 8px 12px;
}

.p-keys.warn {
  border-color: var(--warn);
}

.p-keys summary {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  cursor: pointer;
  list-style: none;
}

.p-keys summary::-webkit-details-marker {
  display: none;
}

.p-keys-count {
  font-size: 11px;
  color: var(--ink-2);
}

.p-keys-chevron {
  margin-left: auto;
  color: var(--muted);
  transition: transform var(--dur-fast) var(--ease);
}

.p-keys[open] .p-keys-chevron {
  transform: rotate(180deg);
}

.p-keys-body {
  margin-top: var(--s2);
  padding-top: var(--s2);
  border-top: 1px solid var(--line);
}

.p-keys-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.p-key-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface-2);
  font-size: 11px;
  max-width: 100%;
}

.p-key-chip.warn {
  border-color: var(--warn);
  background: var(--warn-soft);
}

.p-key-chip .dot {
  width: 6px;
  height: 6px;
}

.p-key-chip code {
  font-size: 10.5px;
  overflow-wrap: anywhere;
  background: none;
  padding: 0;
}

.p-key-state {
  color: var(--muted);
  font-size: 10px;
}

.p-keys-manage {
  display: inline-block;
  margin-top: var(--s2);
  color: var(--accent);
  font-size: 11.5px;
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>
