<script setup lang="ts">
/** A quiet table for records (the turn log). Columns come from the PanelSpec. */
import type { PanelBlock } from '../panels'
import { lookup } from '../panels'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

function rows(): unknown[] {
  const raw = lookup(props.block.items, props.data)
  return Array.isArray(raw) ? raw.slice(0, props.block.max ?? 20) : []
}

function cell(row: unknown, key: string, column: { join?: boolean; truncate?: number }): string {
  const value = lookup(`$.${key}`, row)
  let text = Array.isArray(value) ? value.join(', ') : value == null ? '' : String(value)
  if (column.truncate && text.length > column.truncate) {
    text = text.slice(0, column.truncate) + '…'
  }
  return text
}
</script>

<template>
  <div class="card">
    <h2 v-if="block.title">{{ block.title }}</h2>
    <div v-if="!rows().length" class="empty">{{ block.empty || '还没有记录' }}</div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th v-for="c in block.columns" :key="c.key" :style="{ width: c.width }">{{ c.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in rows()" :key="i">
            <td v-for="c in block.columns" :key="c.key" :style="{ width: c.width }">
              {{ cell(row, c.key, c) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th {
  text-align: left; font-weight: 500; color: var(--muted);
  padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: 6px 8px; border-bottom: 1px solid #f2f3f6; vertical-align: top; }
td:first-child { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
