<script setup lang="ts">
import type { PanelBlock } from '../panels'
import { format, lookup } from '../panels'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

function items(): unknown[] {
  const raw = lookup(props.block.items, props.data)
  return Array.isArray(raw) ? raw.slice(0, props.block.max ?? 10) : []
}
const field = (item: unknown, expr?: string) => format(expr, item)
</script>

<template>
  <div class="card">
    <h2 v-if="block.title">{{ block.title }}</h2>
    <div v-if="!items().length" class="empty">{{ block.empty || '还没有内容' }}</div>
    <div v-for="(item, i) in items()" :key="i" class="row">
      <span class="v">{{ field(item, block.item_title) || '—' }}</span>
      <span class="k" style="width:auto">{{ field(item, block.item_sub) }}</span>
    </div>
  </div>
</template>
