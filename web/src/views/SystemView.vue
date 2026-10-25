<script setup lang="ts">
/**
 * 系统 (Phase 3, docs/console-ia-audit.md §2.6/§5).
 *
 * The tier-1 process/console/SSRF fields are unchanged; this view only adds the
 * connection facts that had no home (bind address, whether the console token is set,
 * and the URL to copy). `log_level` is deliberately excluded here and rendered by the
 * logs page instead, while the catalog keeps it in the `system` group.
 */
import { computed, onMounted, ref } from 'vue'
import { api, type Config } from '../api'
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

const bind = computed(() => {
  const web = config.value?.web
  return web ? `${web.host}:${web.port}` : '—'
})
const authRequired = computed(() => Boolean(config.value?.web?.auth_required))
const consoleUrl = computed(() => `${window.location.origin}/`)
const groups = computed(() => props.groups ?? ['system', 'web', 'security'])
const excluded = computed(() => props.excludePaths ?? ['system.log_level'])

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

async function copyUrl(): Promise<void> {
  const text = consoleUrl.value
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
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1600)
  } catch {
    /* Copy is a convenience; the URL stays selectable if the clipboard is blocked. */
  }
}

onMounted(load)
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
