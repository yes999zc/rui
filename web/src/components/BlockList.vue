<script setup lang="ts">
/** A ledger list: title plus mono meta, separated by hairlines. */
import type { PanelBlock } from '../panels'
import { format, lookup } from '../panels'
import { localizeBackend } from '../i18n'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

function items(): unknown[] {
  const raw = lookup(props.block.items, props.data)
  return Array.isArray(raw) ? raw.slice(0, props.block.max ?? 10) : []
}
const field = (item: unknown, expr?: string) => localizeBackend(format(expr, item))
</script>

<template>
  <section class="block">
    <div v-if="block.title" class="block-head">
      <h2>{{ localizeBackend(block.title) }}</h2>
    </div>
    <div v-if="!items().length" class="empty">{{ localizeBackend(block.empty) || '—' }}</div>
    <div v-for="(item, i) in items()" :key="i" class="list-row">
      <span class="list-title">{{ field(item, block.item_title) || '—' }}</span>
      <span class="list-sub mono">{{ field(item, block.item_sub) }}</span>
    </div>
  </section>
</template>

<style scoped>
.list-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s3);
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.list-row:first-of-type {
  border-top: 1px solid var(--line-strong);
}

.list-title {
  font-size: 14px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.list-sub {
  font-size: 11px;
  color: var(--muted);
  text-align: right;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .list-row {
    flex-wrap: wrap;
    gap: 1px;
  }

  .list-sub {
    text-align: left;
  }
}
</style>
