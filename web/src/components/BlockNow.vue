<script setup lang="ts">
/** The now-playing line: the one place a serif display size earns its keep. */
import type { PanelBlock } from '../panels'
import { format } from '../panels'
import { localizeBackend, t } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown }>()
const title = () => format(props.block.title, props.data)
const sub = () => format(props.block.sub, props.data)
</script>

<template>
  <section class="block">
    <div class="kicker">{{ t('backend.nowPlaying') }}</div>
    <div v-if="title()" class="now">
      <h2 class="now-title">{{ title() }}</h2>
      <p v-if="sub()" class="now-sub">{{ sub() }}</p>
    </div>
    <div v-else class="empty now-empty">
      <AppIcon name="note" :size="16" />
      <span>{{ localizeBackend(block.empty) || t('backend.noTrack') }}</span>
    </div>
  </section>
</template>

<style scoped>
.now {
  margin-top: 4px;
}

.now-title {
  font-family: var(--font-display);
  font-size: clamp(24px, 3vw, 32px);
  line-height: 1.15;
  letter-spacing: -0.02em;
}

.now-sub {
  margin-top: 3px;
  color: var(--ink-2);
  font-size: 14px;
}

.now-empty {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
