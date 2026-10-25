<script setup lang="ts">
/**
 * Operator-readable failure notice shared by the M2 action/test/connection
 * sections. It shows the translated copy, the backend's own message/hint (so a
 * 409 is never reduced to "conflict"), the stable code, and optionally a retry
 * and the v2 token dialog trigger.
 */
import AppIcon from '../../../components/AppIcon.vue'
import { promptForToken } from '../../api'
import type { ErrorCopy } from '../../errors'
import { t } from '../../i18n'

withDefaults(
  defineProps<{
    error: ErrorCopy | null
    showRetry?: boolean
    retryLabel?: string
  }>(),
  {
    showRetry: false,
    retryLabel: '',
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="error" class="banner banner-fail v2-banner v2-error-notice" role="alert">
    <AppIcon name="alert" :size="18" />
    <div class="v2-banner-body">
      <strong>{{ error.title }}</strong>
      <p v-if="error.detail" class="note mono v2-break">{{ error.detail }}</p>
      <p v-if="error.hint" class="note">{{ error.hint }}</p>
      <p
        v-if="error.backendHint && error.backendHint !== error.hint"
        class="note mono v2-break"
      >
        {{ error.backendHint }}
      </p>
      <ul v-if="error.fields.length" class="v2-field-list">
        <li v-for="field in error.fields" :key="field.path">
          <span class="mono">{{ field.path }}</span>
          <span v-if="field.message">{{ field.message }}</span>
        </li>
      </ul>
      <p class="meta v2-error-meta">
        <span class="mono">{{ error.code }}</span>
        <span v-if="error.status">
          · {{ t('v2.common.statusCode', { status: error.status }) }}
        </span>
        <span v-if="error.requestId">
          · {{ t('v2.common.requestId', { id: error.requestId }) }}
        </span>
      </p>
      <div class="v2-state-actions">
        <button
          v-if="error.code === 'unauthorized'"
          type="button"
          class="btn btn-primary btn-sm"
          @click="promptForToken()"
        >
          {{ t('v2.nav.token') }}
        </button>
        <button
          v-if="showRetry"
          type="button"
          class="btn btn-sm"
          @click="emit('retry')"
        >
          {{ retryLabel || t('v2.common.retry') }}
        </button>
      </div>
    </div>
  </div>
</template>
