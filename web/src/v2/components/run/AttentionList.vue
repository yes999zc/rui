<script setup lang="ts">
/**
 * "Needs attention" section for `/v2/run` (M7).
 *
 * Every row is a link to the page that can fix it: a device detail, the rooms
 * page, or the setup wizard. The component renders only; ranking and the
 * reason-to-copy mapping live in `run.ts`.
 */
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import type { AttentionItem } from '../../run'
import { t } from '../../i18n'

defineProps<{ items: AttentionItem[] }>()
</script>

<template>
  <section class="v2-card v2-attention" :aria-label="t('v2.run.attention')">
    <div class="v2-card-head">
      <h2 class="v2-card-title">
        <AppIcon name="alert" :size="16" />
        {{ t('v2.run.attention') }}
      </h2>
      <span class="v2-attention-count" data-testid="attention-count">
        {{ t('v2.run.attentionCount', { n: items.length }) }}
      </span>
    </div>
    <p class="note v2-card-lede">{{ t('v2.run.attentionLede') }}</p>

    <p v-if="!items.length" class="v2-attention-empty" data-testid="attention-empty">
      <AppIcon name="check" :size="15" />
      <span>{{ t('v2.run.attentionEmpty') }}</span>
      <span class="note">{{ t('v2.run.attentionEmptyHint') }}</span>
    </p>

    <ul v-else class="v2-attention-list">
      <li v-for="item in items" :key="item.key">
        <RouterLink
          class="v2-attention-row"
          :to="item.to"
          data-testid="attention-item"
          :data-device-id="item.deviceId ?? ''"
          :data-code="item.code"
        >
          <span class="dot" :class="item.tone === 'fail' ? 'fail' : 'warn'" aria-hidden="true" />
          <span class="v2-attention-main">
            <strong>{{ item.title }}</strong>
            <span v-if="item.detail" class="note">{{ item.detail }}</span>
          </span>
          <span v-if="item.room" class="v2-run-badge">{{ item.room }}</span>
          <span class="tag mono">{{ item.code }}</span>
          <AppIcon name="arrowRight" :size="15" class="v2-attention-go" />
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
