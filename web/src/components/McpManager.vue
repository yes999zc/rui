<script setup lang="ts">
/**
 * P5: HA over MCP — the endpoint, its token (write-only), a live test, and what the
 * bridge actually lists. Saving reconnects at runtime: the old bridge is stopped and
 * its tools are replaced, so a wrong URL is visible here instead of after a restart.
 *
 * `tools.mcp_servers` is shown read-only on purpose: the generic MCP client exists
 * but only HA is wired end to end, and pretending otherwise would be a lie in a
 * production console.
 */
import { onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { api, ApiError, type McpState } from '../api'
import { t } from '../i18n'

const state = ref<McpState | null>(null)
const baseUrl = ref('')
const token = ref('')
const probe = ref<{ ok: boolean; tools: number; entities: number; latency_ms: number | null; error: string } | null>(null)
const busy = ref('')
const error = ref('')
const notice = ref('')

async function load() {
  try {
    state.value = await api.mcp()
    baseUrl.value = state.value.ha.base_url
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  }
}

async function test() {
  busy.value = 'test'
  probe.value = null
  error.value = ''
  try {
    probe.value = await api.testMcp(baseUrl.value.trim())
    await load()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function save() {
  busy.value = 'save'
  error.value = ''
  notice.value = ''
  try {
    const ha: { base_url: string; token?: string } = { base_url: baseUrl.value.trim() }
    if (token.value.trim()) ha.token = token.value.trim()
    state.value = await api.saveMcp({ ha })
    token.value = ''
    notice.value = t('mcp.saved')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="lede mgr-intro">{{ t('mcp.lede') }}</p>

  <div v-if="state" class="surface health-surface">
    <div class="block-head">
      <h2>{{ t('mcp.connection') }}</h2>
      <span class="badge" :class="state.ha.connected ? 'badge-ok' : 'badge-warn'">
        {{ state.ha.wired ? (state.ha.connected ? t('mcp.connected') : t('mcp.disconnected')) : t('mcp.notConfigured') }}
      </span>
    </div>

    <div class="mcp-stats">
      <div class="mcp-stat">
        <span class="mcp-k">{{ t('mcp.toolsEntities') }}</span>
        <span class="mcp-v mono">{{ state.ha.tools }} / {{ state.ha.entities }}</span>
      </div>
      <div class="mcp-stat">
        <span class="mcp-k">{{ t('mcp.latency') }}</span>
        <span class="mcp-v mono">{{ state.ha.latency_ms == null ? '—' : state.ha.latency_ms.toFixed(1) + ' ms' }}</span>
      </div>
      <div class="mcp-stat">
        <span class="mcp-k">{{ t('mcp.token', { env: state.ha.token_env }) }}</span>
        <span class="mcp-v" :class="state.ha.has_token ? 'ok' : 'warn'">
          {{ state.ha.has_token ? t('mcp.tokenConfigured') : t('mcp.tokenMissing') }}
        </span>
      </div>
    </div>
    <p v-if="state.ha.error" class="note fail">{{ state.ha.error }}</p>

    <div class="field-row">
      <div class="field-meta">
        <label class="field-label" for="mcp-url">{{ t('mcp.baseUrl') }}</label>
      </div>
      <div class="field-control">
        <input id="mcp-url" v-model="baseUrl" :placeholder="t('mcp.baseUrlPlaceholder')" />
      </div>
    </div>
    <div class="field-row">
      <div class="field-meta">
        <label class="field-label" for="mcp-token">{{ t('mcp.token', { env: state.ha.token_env }) }}</label>
      </div>
      <div class="field-control">
        <input id="mcp-token" v-model="token" type="password" autocomplete="off" :placeholder="t('mcp.tokenPlaceholder')" />
      </div>
    </div>

    <div class="actions" style="margin-top: var(--s4)">
      <button type="button" class="btn" :disabled="busy !== ''" @click="test">
        {{ busy === 'test' ? t('mcp.testing') : t('mcp.test') }}
      </button>
      <button type="button" class="btn btn-primary" :disabled="busy !== ''" @click="save">
        {{ busy === 'save' ? t('mcp.saving') : t('mcp.save') }}
      </button>
    </div>
    <p v-if="probe" :class="probe.ok ? 'ok' : 'fail'" class="note">
      {{ probe.ok
        ? t('mcp.testOk', {
            tools: probe.tools,
            entities: probe.entities,
            ms: probe.latency_ms == null ? '?' : probe.latency_ms.toFixed(1),
          })
        : t('mcp.testFail', { error: probe.error || t('mcp.error') }) }}
    </p>
    <p v-if="notice" class="note ok">{{ notice }}</p>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
  </div>

  <section v-if="state" class="block">
    <div class="block-head">
      <h2>{{ t('mcp.genericTitle') }}</h2>
      <span class="badge">{{ t('common.readOnly') }}</span>
    </div>
    <div v-if="!state.servers.length" class="empty">{{ t('mcp.genericEmpty') }}</div>
    <div v-for="server in state.servers" :key="server.name" class="server-row">
      <span class="server-name">{{ server.name }}</span>
      <span class="server-meta mono">{{ server.transport }} · {{ server.url }}</span>
      <span class="badge" :class="server.enabled ? 'badge-ok' : 'badge-muted'">
        {{ server.enabled ? t('common.enabled') : t('common.disabled') }}
      </span>
    </div>
    <p class="field-hint" style="margin-top: var(--s3)">{{ t('mcp.genericNote') }}</p>
  </section>

  <div v-if="!state && error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ error }}</span>
  </div>
</template>

<style scoped>
.mgr-intro {
  margin-bottom: var(--s5);
}

.health-surface {
  margin-bottom: var(--s6);
}

.mcp-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s3);
  margin-bottom: var(--s3);
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--line);
}

.mcp-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.mcp-k {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}

.mcp-v {
  font-size: 13px;
  overflow-wrap: anywhere;
}

.server-row {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  padding: 9px 0;
  border-top: 1px solid var(--line);
  flex-wrap: wrap;
}

.server-name {
  font-size: 13.5px;
  font-weight: 550;
}

.server-meta {
  color: var(--muted);
  font-size: 11px;
  flex: 1;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .mcp-stats {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--s2);
  }
}
</style>
