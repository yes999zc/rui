<script setup lang="ts">
/**
 * A metrics strip, not a card grid (docs/console-redesign.md §5.1): one hairline
 * rule, tabular numbers, labels in micro caps. A stat block is a ledger reading,
 * not a decorative tile.
 */
import type { PanelBlock } from '../panels'
import { display, toneOf } from '../panels'
import { localizeBackend } from '../i18n'

defineProps<{ block: PanelBlock; data: unknown }>()
</script>

<template>
  <section class="block">
    <div v-if="block.title" class="block-head">
      <h2>{{ localizeBackend(block.title) }}</h2>
    </div>
    <div class="metric-strip">
      <div v-for="card in block.cards" :key="card.label" class="metric">
        <div class="metric-label">{{ localizeBackend(card.label) }}</div>
        <div class="metric-value" :class="toneOf(card, data)">
          {{ localizeBackend(display(card, data)) }}<span v-if="card.unit" class="metric-unit">{{
            localizeBackend(card.unit)
          }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.metric-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  border-top: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line);
}

.metric {
  padding: 14px 16px 13px;
  border-left: 1px solid var(--line);
  min-width: 0;
}

.metric:first-child {
  border-left: 0;
  padding-left: 0;
}

.metric-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric-value {
  margin-top: 3px;
  font-family: var(--font-mono);
  font-size: clamp(20px, 2.3vw, 27px);
  line-height: 1.15;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.metric-unit {
  margin-left: 3px;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--muted);
}

@media (max-width: 720px) {
  .metric-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric {
    padding: 12px 12px 11px;
    border-left: 1px solid var(--line);
  }

  .metric:nth-child(odd) {
    border-left: 0;
    padding-left: 0;
  }

  .metric-value {
    font-size: 22px;
  }
}
</style>
