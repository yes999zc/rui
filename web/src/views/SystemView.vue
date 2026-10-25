<script setup lang="ts">
/**
 * 系统 (Phase 3, docs/console-ia-audit.md §2.6/§5).
 *
 * The tier-1 process/console/SSRF fields are unchanged; this view only adds the
 * connection facts that had no home (bind address, whether the console token is set,
 * and the URL to copy). `log_level` is deliberately excluded here and rendered by the
 * logs page instead, while the catalog keeps it in the `system` group.
 *
 * Below that: 配置原文 (`GET /api/v1/config/raw`), the effective config as YAML with
 * credential values already replaced server-side. It is display-and-copy only - there
 * is no editor, no save, and no input bound to it (docs/console.md §12.2c).
 */
import { computed, onMounted, ref } from 'vue'
import { api, type Config, type ConfigRaw } from '../api'
import { t } from '../i18n'
import AppIcon from '../components/AppIcon.vue'
import ConfigCenter from '../components/ConfigCenter.vue'

const props = defineProps<{
  groups?: string[]
  excludePaths?: string[]
  panelId?: string
}>()

const config = ref<Config | null>(null)
const loading = ref(true)
const error = ref('')
const copied = ref(false)

const raw = ref<ConfigRaw | null>(null)
const rawLoading = ref(true)
const rawError = ref('')
const rawCopied = ref(false)

const bind = computed(() => {
  const web = config.value?.web
  return web ? `${web.host}:${web.port}` : '—'
})
const authRequired = computed(() => Boolean(config.value?.web?.auth_required))
const consoleUrl = computed(() => `${window.location.origin}/`)
const groups = computed(() => props.groups ?? ['system', 'web', 'security'])
const excluded = computed(() => props.excludePaths ?? ['system.log_level'])
const rawMtime = computed(() => {
  const mtime = raw.value?.mtime ?? 0
  return mtime ? new Date(mtime * 1000).toLocaleString() : t('common.na')
})
const rawRedacted = computed(() => {
  const keys = raw.value?.redacted_keys ?? []
  return t('system.rawRedacted', { count: keys.length, keys: keys.join(', ') })
})

/** Copy helper for both the console URL and the YAML block. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      area.remove()
    }
    return true
  } catch {
    return false
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    config.value = await api.config()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

async function loadRaw(): Promise<void> {
  rawLoading.value = true
  rawError.value = ''
  try {
    raw.value = await api.configRaw()
  } catch (exc) {
    rawError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    rawLoading.value = false
  }
}

async function copyUrl(): Promise<void> {
  if (await copyText(consoleUrl.value)) {
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1600)
  }
}

async function copyYaml(): Promise<void> {
  if (await copyText(raw.value?.yaml ?? '')) {
    rawCopied.value = true
    window.setTimeout(() => { rawCopied.value = false }, 1600)
  }
}

onMounted(() => {
  void load()
  void loadRaw()
})
</script>

<template>
  <section class="surface system-connection">
    <div class="block-head">
      <div>
        <h2>{{ t('system.connectionTitle') }}</h2>
        <p class="field-hint">{{ t('system.connectionHint') }}</p>
      </div>
      <span class="badge" :class="error ? 'badge-fail' : authRequired ? 'badge-ok' : 'badge-warn'">
        {{ error
          ? t('system.unknown')
          : authRequired
            ? t('system.authOn')
            : t('system.authOff') }}
      </span>
    </div>

    <p v-if="loading" class="empty">{{ t('common.loading') }}</p>
    <p v-else-if="error" class="note fail" role="alert">{{ error }}</p>

    <template v-else>
      <div class="system-fact">
        <span class="system-k">{{ t('system.bind') }}</span>
        <span class="system-v mono">{{ bind }}</span>
      </div>
      <div class="system-fact">
        <span class="system-k">{{ t('system.url') }}</span>
        <span class="system-v mono system-url">{{ consoleUrl }}</span>
        <button type="button" class="btn btn-sm" @click="copyUrl">
          <AppIcon :name="copied ? 'check' : 'copy'" :size="13" />
          {{ copied ? t('common.copied') : t('system.copyUrl') }}
        </button>
      </div>
      <div class="system-fact">
        <span class="system-k">{{ t('system.auth') }}</span>
        <span class="badge" :class="authRequired ? 'badge-ok' : 'badge-warn'">
          {{ authRequired ? t('system.authOn') : t('system.authOff') }}
        </span>
        <RouterLink class="system-link" to="/panel/credentials">{{ t('credentials.manage') }}</RouterLink>
      </div>
      <p class="field-hint system-note">{{ t('system.authNote') }}</p>
    </template>
  </section>

  <section class="surface system-raw">
    <div class="block-head">
      <div>
        <h2>{{ t('system.rawTitle') }}</h2>
        <p class="field-hint">{{ t('system.rawHint') }}</p>
      </div>
      <span class="badge badge-muted">{{ t('common.readOnly') }}</span>
    </div>

    <p v-if="rawLoading" class="empty">{{ t('common.loading') }}</p>
    <p v-else-if="rawError" class="note fail" role="alert">{{ rawError }}</p>

    <template v-else-if="raw">
      <div class="system-fact">
        <span class="system-k">{{ t('system.rawPath') }}</span>
        <span class="system-v mono">{{ raw.path || t('common.na') }}</span>
        <span class="system-k">{{ t('system.rawMtime') }}</span>
        <span class="system-v mono">{{ rawMtime }}</span>
        <button type="button" class="btn btn-sm" @click="copyYaml">
          <AppIcon :name="rawCopied ? 'check' : 'copy'" :size="13" />
          {{ rawCopied ? t('common.copied') : t('system.rawCopy') }}
        </button>
      </div>
      <p v-if="!raw.path" class="field-hint system-note">{{ t('system.rawDefaults') }}</p>
      <p v-if="raw.truncated" class="note fail" role="alert">{{ t('system.rawTruncated') }}</p>
      <p class="field-hint system-note">
        <span v-if="raw.redacted_keys.length">{{ rawRedacted }}</span>
        <span v-else>{{ t('system.rawNoneRedacted') }}</span>
      </p>
      <!-- Read-only on purpose: no v-model, no textarea, no save. -->
      <pre class="mono raw-yaml" tabindex="0" :aria-label="t('system.rawTitle')">{{ raw.yaml }}</pre>
    </template>
  </section>

  <ConfigCenter
    :groups="groups"
    :exclude-paths="excluded"
    :panel-id="props.panelId || 'system'"
  />
</template>

<style scoped>
.system-connection {
  margin-bottom: var(--s6);
}

.system-raw {
  margin-bottom: var(--s6);
}

/* A fixed-height scroll box: the YAML is long, and one page of it must not push the
   catalog editor miles down. `overflow-wrap: normal` keeps wrapping honest so copied
   lines match what is displayed. */
.raw-yaml {
  margin: var(--s3) 0 0;
  padding: 12px 14px;
  max-height: 420px;
  overflow: auto;
  background: var(--surface-2);
  border: 1px solid var(--line);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre;
  tab-size: 2;
  user-select: text;
}

.raw-yaml:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.system-fact {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  flex-wrap: wrap;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.system-k {
  min-width: 120px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}

.system-v {
  font-size: 13px;
  overflow-wrap: anywhere;
}

.system-url {
  user-select: all;
}

.system-link {
  font-size: 12px;
  color: var(--accent);
}

.system-note {
  margin-top: var(--s2);
  max-width: 76ch;
}

@media (max-width: 720px) {
  .system-k {
    min-width: 0;
    flex: 1 1 100%;
  }
}
</style>
