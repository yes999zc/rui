<script setup lang="ts">
/** A quiet records table (the turn log). Columns come from the PanelSpec. */
import type { PanelBlock } from '../panels'
import { lookup } from '../panels'
import { localizeBackend } from '../i18n'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

function rows(): unknown[] {
  const raw = lookup(props.block.items, props.data)
  return Array.isArray(raw) ? raw.slice(0, props.block.max ?? 20) : []
}

function cell(row: unknown, key: string, column: { join?: boolean; truncate?: number }): string {
  const value = lookup(`$.${key}`, row)
  let text = Array.isArray(value) ? value.join(', ') : value == null ? '' : String(value)
  if (column.truncate && text.length > column.truncate) text = `${text.slice(0, column.truncate)}…`
  return text
}
</script>

<template>
  <section class="block">
    <div v-if="block.title" class="block-head">
      <h2>{{ localizeBackend(block.title) }}</h2>
    </div>
    <div v-if="!rows().length" class="empty">{{ localizeBackend(block.empty) || '—' }}</div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th v-for="column in block.columns" :key="column.key" :style="{ width: column.width }">
              {{ localizeBackend(column.label) }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in rows()" :key="i">
            <td
              v-for="column in block.columns"
              :key="column.key"
              :class="{ num: column.width === '70px' }"
              :style="{ width: column.width }"
            >
              {{ cell(row, column.key, column) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
