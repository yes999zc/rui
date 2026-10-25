<script setup lang="ts">
/**
 * A PanelSpec block list (docs/console.md).
 *
 * Two layouts share one renderer:
 *  - plain list: the overview/legacy pages render blocks as siblings;
 *  - sections: a `section_title` starts a new section (the music page's
 *    正在播放 / 音源与凭据 / 高级). A section is a heading plus the same blocks;
 *    it never adds a second save path.
 */
import { computed } from 'vue'
import type { PanelBlock, PanelText } from '../panels'
import { pickLocalized } from '../i18n'
import PanelBlockView from './PanelBlock.vue'

const props = defineProps<{
  blocks: PanelBlock[]
  data: unknown
  extras?: Record<string, unknown>
  panelId: string
  refresh: () => void
}>()
const emit = defineEmits<{ saved: [] }>()

interface Section {
  key: string
  marker: string
  title?: PanelText
  blocks: PanelBlock[]
}

/** Consecutive blocks sharing a `section_title` form one section. */
const sections = computed<Section[]>(() => {
  if (!props.blocks.some((block) => block.section_title)) {
    return [{ key: 'flat', marker: '', blocks: props.blocks }]
  }
  const out: Section[] = []
  for (const block of props.blocks) {
    const title = block.section_title
    const marker = title ? JSON.stringify(title) : ''
    let last = out[out.length - 1]
    if (!last || last.marker !== marker) {
      last = { key: `${out.length}-${marker}`, marker, title, blocks: [] }
      out.push(last)
    }
    last.blocks.push(block)
  }
  return out
})

function dataFor(block: PanelBlock): unknown {
  return block.source ? (props.extras ?? {})[block.source] : props.data
}
</script>

<template>
  <template v-if="sections.length === 1 && !sections[0].title">
    <PanelBlockView
      v-for="(block, index) in sections[0].blocks"
      :key="block.type + (block.title ?? '') + index"
      :block="block"
      :data="dataFor(block)"
      :panel-id="panelId"
      :refresh="refresh"
      @saved="emit('saved')"
    />
  </template>
  <template v-else>
    <section v-for="section in sections" :key="section.key" class="panel-section">
      <h2 v-if="section.title" class="panel-section-title">
        {{ pickLocalized(section.title) }}
      </h2>
      <PanelBlockView
        v-for="(block, index) in section.blocks"
        :key="block.type + (block.title ?? '') + index"
        :block="block"
        :data="dataFor(block)"
        :panel-id="panelId"
        :refresh="refresh"
        @saved="emit('saved')"
      />
    </section>
  </template>
</template>

<style scoped>
.panel-section + .panel-section {
  margin-top: var(--s7);
}

.panel-section-title {
  margin: 0 0 var(--s3);
  padding-bottom: var(--s2);
  border-bottom: 1px solid var(--line-strong);
  font-size: 20px;
  letter-spacing: -0.01em;
}

.panel-section :deep(.block-slot + .block-slot) {
  margin-top: var(--s7);
}

@media (max-width: 720px) {
  .panel-section + .panel-section {
    margin-top: var(--s6);
  }

  .panel-section-title {
    font-size: 18px;
  }
}
</style>
