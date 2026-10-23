<script setup lang="ts">
/**
 * Ordered fallback chain for one provider stage.
 *
 * Explicit up/down/remove instead of drag-and-drop: this is a rarely-changed,
 * high-consequence order and every move must be a deliberate click. Adding is
 * limited to providers that belong to the stage and are still offered.
 */
import { computed, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import { providerName, t } from '../../i18n'
import { kindLabel, roleLabel, type ProviderDef, type Stage } from './types'

const props = defineProps<{
  stage: Stage
  chain: string[]
  defs: ProviderDef[]
  allowed: string[]
  excluded: string[]
  error?: string
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update:chain': [chain: string[]] }>()

const addChoice = ref('')

const byName = computed(() => new Map(props.defs.map((def) => [def.name, def])))
const options = computed(() => props.allowed.filter((name) => !props.chain.includes(name)))
const canRemove = computed(() => props.chain.length > 1)

function defOf(name: string): ProviderDef | undefined {
  return byName.value.get(name)
}

function kindOf(name: string): string {
  return kindLabel(defOf(name)?.kind ?? '')
}

function isExcluded(name: string): boolean {
  return props.excluded.includes(name)
}

function move(index: number, delta: number): void {
  const next = [...props.chain]
  const target = index + delta
  if (target < 0 || target >= next.length) return
  ;[next[index], next[target]] = [next[target], next[index]]
  emit('update:chain', next)
}

function remove(index: number): void {
  if (!canRemove.value) return
  emit('update:chain', props.chain.filter((_, i) => i !== index))
}

function add(): void {
  const name = addChoice.value
  if (!name || props.chain.includes(name)) return
  emit('update:chain', [...props.chain, name])
  addChoice.value = ''
}
</script>

<template>
  <div class="chain">
    <div class="chain-head">
      <div>
        <div class="kicker">{{ t('providers.chain.title') }}</div>
        <p class="chain-hint">{{ t('providers.chain.hint') }}</p>
      </div>
      <span class="badge badge-restart">{{ t('providers.effect.restart') }}</span>
    </div>

    <div v-if="!chain.length" class="banner banner-warn chain-empty">{{ t('providers.chain.empty') }}</div>

    <ol class="chain-list">
      <li v-for="(name, index) in chain" :key="name + index" class="chain-row" :class="{ retired: isExcluded(name) }">
        <span class="chain-index mono">{{ String(index + 1).padStart(2, '0') }}</span>
        <div class="chain-provider">
          <div class="chain-name-line">
            <span class="chain-name">{{ providerName(name) }}</span>
            <code class="chain-id mono">{{ name }}</code>
            <span class="badge">{{ kindOf(name) }}</span>
          </div>
          <div class="chain-meta">
            <span class="badge" :class="index === 0 ? 'badge-accent' : 'badge-muted'">{{ roleLabel(index) }}</span>
            <span v-if="isExcluded(name)" class="badge badge-warn">{{ t('providers.chain.notOffered') }}</span>
            <span v-else-if="!defOf(name)" class="badge badge-fail">{{ t('providers.chain.unknown') }}</span>
          </div>
        </div>
        <div class="chain-actions">
          <button
            type="button"
            class="chain-btn"
            :disabled="disabled || index === 0"
            :title="t('providers.chain.up')"
            :aria-label="t('providers.chain.up')"
            @click="move(index, -1)"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 19V5" /><path d="M6 11l6-6 6 6" />
            </svg>
          </button>
          <button
            type="button"
            class="chain-btn"
            :disabled="disabled || index === chain.length - 1"
            :title="t('providers.chain.down')"
            :aria-label="t('providers.chain.down')"
            @click="move(index, 1)"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 5v14" /><path d="M18 13l-6 6-6-6" />
            </svg>
          </button>
          <button
            type="button"
            class="chain-btn danger"
            :disabled="disabled || !canRemove"
            :title="canRemove ? t('providers.chain.remove') : t('providers.chain.keepOne')"
            :aria-label="t('providers.chain.remove')"
            @click="remove(index)"
          >
            <AppIcon name="close" :size="14" />
          </button>
        </div>
      </li>
    </ol>

    <p v-if="chain.includes('sf-tts')" class="chain-warning">{{ t('providers.chain.deprecatedHint') }}</p>
    <p v-if="error" class="field-error" role="alert">{{ error }}</p>

    <div class="chain-add">
      <label class="sr-only" :for="`chain-add-${stage}`">{{ t('providers.chain.add') }}</label>
      <select :id="`chain-add-${stage}`" v-model="addChoice" :disabled="disabled || !options.length">
        <option value="">{{ t('providers.chain.addPlaceholder') }}</option>
        <option v-for="name in options" :key="name" :value="name">{{ name }}</option>
      </select>
      <button type="button" class="btn btn-sm" :disabled="disabled || !addChoice" @click="add">
        <AppIcon name="plus" :size="13" />
        {{ t('providers.chain.add') }}
      </button>
    </div>
    <p v-if="!options.length && chain.length" class="chain-all-added">{{ t('providers.chain.allAdded') }}</p>
  </div>
</template>

<style scoped>
.chain {
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: var(--s4);
  min-width: 0;
}

.chain-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
}

.chain-hint {
  margin-top: 2px;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.5;
}

.chain-empty {
  margin-top: var(--s3);
  font-size: 12px;
}

.chain-list {
  list-style: none;
  margin: var(--s3) 0 0;
  padding: 0;
}

.chain-row {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  gap: var(--s2);
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid var(--line);
}

.chain-row:first-child {
  border-top: 1px solid var(--line-strong);
}

.chain-row.retired {
  background: var(--warn-soft);
  margin: 0 -6px;
  padding-left: 6px;
  padding-right: 6px;
  border-radius: var(--r-xs);
}

.chain-index {
  font-size: 10.5px;
  color: var(--accent);
  letter-spacing: 0.04em;
}

.chain-provider {
  min-width: 0;
}

.chain-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chain-name {
  font-size: 13px;
  font-weight: 550;
  overflow-wrap: anywhere;
}

.chain-id {
  font-size: 10px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.chain-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 3px;
}

.chain-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.chain-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--r-xs);
  background: transparent;
  color: var(--ink-2);
  cursor: pointer;
}

.chain-btn:hover:not(:disabled) {
  background: var(--surface-2);
  color: var(--ink);
}

.chain-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.chain-btn.danger:hover:not(:disabled) {
  color: var(--fail);
  background: var(--fail-soft);
}

.chain-warning {
  margin-top: var(--s2);
  color: var(--warn);
  font-size: 11.5px;
  line-height: 1.5;
}

.chain-add {
  display: flex;
  gap: 6px;
  margin-top: var(--s3);
  padding-top: var(--s3);
  border-top: 1px dashed var(--line);
}

.chain-add select {
  flex: 1;
  min-width: 0;
  min-height: 30px;
  padding: 4px 8px;
  font-size: 12px;
}

.chain-all-added {
  margin-top: 6px;
  color: var(--muted);
  font-size: 11px;
}

@media (max-width: 720px) {
  .chain-btn {
    width: 32px;
    height: 32px;
  }

  .chain-add {
    flex-wrap: wrap;
  }

  .chain-add select {
    flex: 1 1 100%;
  }
}
</style>
