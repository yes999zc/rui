<script setup lang="ts">
/**
 * One PanelSpec block (docs/console.md).
 *
 * Extracted from PanelHost so a tabbed page and a page with sections share the
 * exact same renderer. The block vocabulary is declarative; the `component`
 * block reuses a hand-written view from panelComponents.ts instead of growing a
 * second copy of DeviceManager/McpManager/PhrasebookEditor here.
 */
import { defineAsyncComponent, type Component } from 'vue'
import type { PanelBlock } from '../panels'
import { PANEL_COMPONENTS } from './panelComponents'
import { t } from '../i18n'
import AppIcon from './AppIcon.vue'
import BlockStats from './BlockStats.vue'
import BlockNow from './BlockNow.vue'
import BlockActions from './BlockActions.vue'
import BlockList from './BlockList.vue'
import BlockForm from './BlockForm.vue'
import BlockTable from './BlockTable.vue'
import BlockLog from './BlockLog.vue'
import BlockMemory from './BlockMemory.vue'
import BlockSay from './BlockSay.vue'
import BlockVolume from './BlockVolume.vue'
import BlockCredentials from './BlockCredentials.vue'

const props = defineProps<{
  block: PanelBlock
  data: unknown
  panelId: string
  refresh: () => void
}>()
const emit = defineEmits<{ saved: [] }>()

/**
 * The catalog editor is already a lazy route (panelComponents.ts); import it the
 * same way here so an inline `config` block does not pull it into the shell chunk.
 */
const InlineConfig = defineAsyncComponent(() => import('./ConfigCenter.vue'))

/** Hand-written views named by a `component` block, resolved once per id. */
const HOST_COMPONENTS: Record<string, Component> = Object.fromEntries(
  Object.entries(PANEL_COMPONENTS).map(([id, loader]) => [id, defineAsyncComponent(loader)]),
)

function hostComponent(block: PanelBlock): Component | null {
  if (!block.component) return null
  return HOST_COMPONENTS[block.component] ?? null
}
</script>

<template>
  <div class="block-slot" :class="`b-${block.type}`">
    <BlockStats v-if="block.type === 'stats'" :block="block" :data="data" />
    <BlockNow v-else-if="block.type === 'now'" :block="block" :data="data" />
    <BlockActions
      v-else-if="block.type === 'actions'"
      :block="block"
      :data="data"
      :refresh="refresh"
    />
    <BlockList v-else-if="block.type === 'list'" :block="block" :data="data" />
    <BlockForm
      v-else-if="block.type === 'form'"
      :block="block"
      :data="data"
      :refresh="refresh"
    />
    <BlockTable v-else-if="block.type === 'table'" :block="block" :data="data" />
    <BlockLog
      v-else-if="block.type === 'log'"
      :block="block"
      :data="data"
      :hide-title="!block.title"
    />
    <BlockMemory
      v-else-if="block.type === 'memory'"
      :block="block"
      :data="data"
      :refresh="refresh"
    />
    <BlockSay v-else-if="block.type === 'say'" :block="block" :data="data" />
    <BlockVolume v-else-if="block.type === 'volume'" :block="block" :data="data" />
    <InlineConfig
      v-else-if="block.type === 'config'"
      :groups="block.groups"
      :tier="block.tier"
      :warning="block.warning"
      :panel-id="panelId"
      @saved="emit('saved')"
    />
    <BlockCredentials
      v-else-if="block.type === 'credentials'"
      :block="block"
      :data="data"
      :refresh="refresh"
    />
    <component
      :is="hostComponent(block)"
      v-else-if="block.type === 'component' && hostComponent(block)"
      v-bind="block.component_args ?? {}"
    />
    <div v-else class="banner banner-warn">
      <AppIcon name="alert" :size="16" class="banner-icon" />
      <span>
        {{ block.type === 'component'
          ? t('page.componentMissing', { id: panelId, component: block.component ?? '' })
          : t('page.blockUnknown', { type: block.type }) }}
      </span>
    </div>
  </div>
</template>
