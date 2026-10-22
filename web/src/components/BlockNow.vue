<script setup lang="ts">
import type { PanelBlock } from '../panels'
import { lookup } from '../panels'

const props = defineProps<{ block: PanelBlock; data: unknown }>()
const title = () => lookup(props.block.title, props.data)
const sub = () => lookup(props.block.sub, props.data)
</script>

<template>
  <div class="card now">
    <template v-if="title()">
      <div class="now-title">{{ title() }}</div>
      <div class="now-sub">{{ sub() }}</div>
    </template>
    <div v-else class="empty">{{ block.empty || '—' }}</div>
  </div>
</template>

<style scoped>
.now-title { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
.now-sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
</style>
