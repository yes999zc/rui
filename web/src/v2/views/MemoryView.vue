<script lang="ts">
/**
 * Client-side mirror of `MAX_TEXT` in `src/rui/session/memory.py`; keep the two
 * in sync (the server rejects longer text with 422). Python `len()` and
 * `[...text].length` both count Unicode code points, emoji included.
 */
export const MAX_TEXT = 200
</script>

<script setup lang="ts">
/**
 * `/v2/memory` (M8) — memory list + add/confirm/remove.
 *
 * `GET/POST /api/v1/memory` is the stable v1 non-device endpoint (C-002
 * §1.2). The POST action vocabulary comes from `app.py`: `add` (with `text`),
 * `confirm` (with `id`), `remove` (with `id`); every response carries the full
 * list after the write, so the page never guesses the new state.
 *
 * Length/empty checks are client-side and use the server's own limits (empty
 * text is rejected, text is capped at `MAX_TEXT`). A server 422 is still
 * possible — the harness injects one — and it marks the field red while the
 * list stays exactly as it was; 409 means memory is disabled, 404 means the
 * entry vanished.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StateBlock from '../components/StateBlock.vue'
import MemoryRow from '../components/run/MemoryRow.vue'
import { api, promptForToken, type MemoryItem } from '../api'
import { describeError, type ErrorCopy } from '../errors'
import { t } from '../i18n'
import { useResource } from '../useResource'

const memoryResource = useResource(() => api.memoryList())
const { data: memoryPage, error, loading } = memoryResource
const errorCopy = computed(() => (error.value ? describeError(error.value) : null))

const text = ref('')
const fieldError = ref('')
const actionError = ref<ErrorCopy | null>(null)
const success = ref('')
const busy = ref('')
const unconfirmedOnly = ref(false)
const pendingRemove = ref<MemoryItem | null>(null)

const items = computed<MemoryItem[]>(() => memoryPage.value?.items ?? [])
const sortedItems = computed(() =>
  items.value.slice().sort((a, b) => (b.created || 0) - (a.created || 0)),
)
const visibleItems = computed(() =>
  unconfirmedOnly.value ? sortedItems.value.filter((item) => !item.confirmed) : sortedItems.value,
)
const unconfirmedCount = computed(() => items.value.filter((item) => !item.confirmed).length)

/** Python's `len()` counts code points; `[...text]` matches that for emoji too. */
const charCount = computed(() => [...text.value].length)
const staleError = computed(() => Boolean(errorCopy.value) && items.value.length > 0)

let successTimer: number | null = null

function flashSuccess(message: string): void {
  success.value = message
  if (successTimer !== null) window.clearTimeout(successTimer)
  successTimer = window.setTimeout(() => {
    success.value = ''
  }, 2500)
}

function applyItems(next: MemoryItem[]): void {
  memoryResource.data.value = { items: next }
  // A successful write is a successful read of the same list; keep a stale
  // "could not load" banner from sitting next to fresh rows.
  if (error.value) error.value = null
}

function validate(): boolean {
  fieldError.value = ''
  const value = text.value.trim()
  if (!value) {
    fieldError.value = t('v2.memory.error.empty')
    return false
  }
  if ([...value].length > MAX_TEXT) {
    fieldError.value = t('v2.memory.error.tooLong')
    return false
  }
  return true
}

function onTextInput(): void {
  if (fieldError.value) fieldError.value = ''
}

async function submit(): Promise<void> {
  if (busy.value) return
  if (!validate()) return
  busy.value = 'add'
  actionError.value = null
  try {
    const response = await api.memoryEdit({ action: 'add', text: text.value.trim() })
    applyItems(response.items)
    text.value = ''
    fieldError.value = ''
    flashSuccess(t('v2.memory.saved'))
  } catch (err) {
    const copy = describeError(err)
    if (copy.status === 422) {
      // The server rejected the field: mark it, keep the list unchanged.
      fieldError.value = copy.detail || copy.title
    } else if (copy.status === 409) {
      actionError.value = { ...copy, title: t('v2.memory.disabled'), hint: t('v2.memory.disabledHint') }
    } else {
      actionError.value = copy
    }
  } finally {
    busy.value = ''
  }
}

async function confirmItem(item: MemoryItem): Promise<void> {
  if (busy.value) return
  busy.value = `confirm:${item.id}`
  actionError.value = null
  try {
    const response = await api.memoryEdit({ action: 'confirm', id: item.id })
    applyItems(response.items)
    flashSuccess(t('v2.memory.saved'))
  } catch (err) {
    const copy = describeError(err)
    if (copy.status === 404) handleGone(copy)
    else actionError.value = copy
  } finally {
    busy.value = ''
  }
}

function handleGone(copy: ErrorCopy): void {
  actionError.value = { ...copy, title: t('v2.memory.itemGone') }
  void memoryResource.load()
}

function askRemove(item: MemoryItem): void {
  actionError.value = null
  pendingRemove.value = item
}

async function removeItem(): Promise<void> {
  const item = pendingRemove.value
  if (!item || busy.value) return
  busy.value = `remove:${item.id}`
  actionError.value = null
  try {
    const response = await api.memoryEdit({ action: 'remove', id: item.id })
    applyItems(response.items)
    pendingRemove.value = null
    flashSuccess(t('v2.memory.saved'))
  } catch (err) {
    const copy = describeError(err)
    pendingRemove.value = null
    if (copy.status === 404) handleGone(copy)
    else actionError.value = copy
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  void memoryResource.load()
})

onBeforeUnmount(() => {
  if (successTimer !== null) window.clearTimeout(successTimer)
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.run') }}</span>
        <h1>{{ t('v2.memory.title') }}</h1>
        <p class="lede">{{ t('v2.memory.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="items.length" class="meta" data-testid="memory-count">
          {{ t('v2.memory.count', { n: items.length }) }}
        </span>
        <label v-if="unconfirmedCount" class="switch">
          <input v-model="unconfirmedOnly" type="checkbox" data-testid="memory-unconfirmed-only" />
          <span class="track" aria-hidden="true" />
          <span class="switch-text">{{ t('v2.memory.filterUnconfirmed') }}</span>
        </label>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="loading"
          data-testid="memory-refresh"
          @click="memoryResource.load()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <p v-if="success" class="v2-memory-success" role="status" data-testid="memory-success">
      <span class="badge badge-ok">{{ success }}</span>
    </p>

    <form class="v2-card v2-memory-form" data-testid="memory-form" @submit.prevent="submit">
      <h2 class="v2-card-title">{{ t('v2.memory.addTitle') }}</h2>
      <div class="field-row" :class="{ invalid: Boolean(fieldError) }">
        <div class="field-meta">
          <label class="field-label" for="v2-memory-text">{{ t('v2.memory.title') }}</label>
          <p class="field-hint">{{ t('v2.memory.addHint') }}</p>
        </div>
        <div class="field-control wide">
          <textarea
            id="v2-memory-text"
            v-model="text"
            class="input"
            rows="3"
            :placeholder="t('v2.memory.addPlaceholder')"
            :class="{ invalid: Boolean(fieldError) }"
            :aria-invalid="Boolean(fieldError)"
            :aria-describedby="fieldError ? 'v2-memory-field-error' : undefined"
            data-testid="memory-text"
            @input="onTextInput"
            @keydown.ctrl.enter.prevent="submit"
            @keydown.meta.enter.prevent="submit"
          />
          <div class="field-meta-line">
            <span data-testid="memory-length">{{ t('v2.memory.length', { n: charCount }) }}</span>
          </div>
          <p
            v-if="fieldError"
            id="v2-memory-field-error"
            class="field-error"
            data-testid="memory-field-error"
          >
            {{ fieldError }}
          </p>
          <div class="v2-memory-submit">
            <button
              type="submit"
              class="btn btn-primary"
              :disabled="Boolean(busy)"
              data-testid="memory-submit"
            >
              <AppIcon :name="busy === 'add' ? 'clock' : 'plus'" :size="14" />
              {{ busy === 'add' ? t('v2.memory.adding') : t('v2.memory.addAction') }}
            </button>
          </div>
        </div>
      </div>
    </form>

    <div v-if="actionError" class="banner banner-warn v2-banner" role="alert" data-testid="memory-action-error">
      <AppIcon name="alert" :size="17" />
      <div class="v2-banner-body">
        <strong>{{ actionError.title }}</strong>
        <p class="note">{{ actionError.detail || actionError.hint }}</p>
        <p v-if="actionError.hint && actionError.detail" class="note">{{ actionError.hint }}</p>
      </div>
      <button type="button" class="btn btn-sm" @click="actionError = null">
        {{ t('v2.common.close') }}
      </button>
    </div>

    <StateBlock v-if="loading && !memoryPage" kind="loading" :title="t('v2.common.loading')" />

    <StateBlock
      v-else-if="errorCopy && !memoryPage"
      kind="error"
      :title="t('v2.memory.loadFailed')"
      :detail="errorCopy.detail"
      :hint="errorCopy.hint"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="memoryResource.load()"
    >
      <button
        v-if="errorCopy?.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <template v-else-if="memoryPage">
      <div v-if="staleError" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.memory.loadFailed') }}</strong>
          <p v-if="errorCopy" class="note">{{ errorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="memoryResource.load()">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <StateBlock
        v-if="!items.length"
        kind="empty"
        :title="t('v2.memory.empty')"
        :hint="t('v2.memory.emptyHint')"
      />
      <StateBlock
        v-else-if="!visibleItems.length"
        kind="empty"
        :title="t('v2.memory.empty')"
        :hint="t('v2.memory.emptyHint')"
      >
        <button
          type="button"
          class="btn btn-sm"
          @click="unconfirmedOnly = false"
        >
          {{ t('v2.memory.showAll') }}
        </button>
      </StateBlock>

      <section v-else class="v2-card v2-memory-card">
        <ul class="v2-memory-list" data-testid="memory-list">
          <MemoryRow
            v-for="item in visibleItems"
            :key="item.id"
            :item="item"
            :busy="busy === `confirm:${item.id}` || busy === `remove:${item.id}`"
            @confirm="confirmItem(item)"
            @remove="askRemove(item)"
          />
        </ul>
      </section>
    </template>

    <ConfirmDialog
      v-if="pendingRemove"
      :title="t('v2.memory.removeTitle')"
      :confirm-label="t('v2.memory.removeConfirm')"
      :cancel-label="t('v2.memory.removeCancel')"
      tone="danger"
      :busy="Boolean(busy)"
      :busy-label="t('v2.memory.removing')"
      data-testid="memory-remove-dialog"
      @confirm="removeItem"
      @cancel="pendingRemove = null"
    >
      <p class="v2-memory-remove-text">{{ pendingRemove.text }}</p>
      <p class="note">{{ t('v2.memory.removeBody') }}</p>
      <p class="note">{{ t('v2.memory.removeHint') }}</p>
    </ConfirmDialog>
  </div>
</template>
