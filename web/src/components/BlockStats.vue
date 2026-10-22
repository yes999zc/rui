<script setup lang="ts">
import type { PanelBlock } from '../panels'
import { display, toneOf } from '../panels'

defineProps<{ block: PanelBlock; data: unknown }>()
</script>

<template>
  <div class="card">
    <h2 v-if="block.title">{{ block.title }}</h2>
    <div class="grid3 stats">
      <div v-for="card in block.cards" :key="card.label" class="stat">
        <div class="stat-label">{{ card.label }}</div>
        <div class="stat-value" :class="toneOf(card, data)">
          {{ display(card, data) }}<small v-if="card.unit">{{ card.unit }}</small>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stats { gap: 12px; }
.stat-label { color: var(--muted); font-size: 12px; margin-bottom: 2px; }
.stat-value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat-value small { font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 3px; }
</style>
