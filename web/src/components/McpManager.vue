<script setup lang="ts">
/**
 * MCP / HA maintenance page.
 *
 * Top: the Home Assistant bridge (unchanged behaviour — save reconnects at runtime).
 * Bottom: generic MCP servers, each with its own lifecycle and status, plus the
 * `tools.mcp_allow_stdio` gate. A stdio server is a local process (arbitrary code
 * execution), so the gate is explicitly dangerous and a refused stdio save is shown
 * as a per-field 422, never hidden.
 *
 * Data path: GET /api/v1/mcp, PUT /api/v1/mcp {ha?, servers?},
 * POST /api/v1/mcp/test {base_url} | {name,transport,url|command},
 * PUT /api/v1/config/values for the gate.
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  api,
  ApiError,
  type FirstPartyMcpInstance,
  type McpProbe,
  type McpServerPayload,
  type McpServerStatus,
  type McpState,
} from '../api'
import { localizeIssue, t } from '../i18n'

type Transport = 'http' | 'stdio'

interface DraftServer {
  name: string
  transport: Transport
  url: string
  command: string
  args: string
  env: string
  /** HTTP-only request metadata (`Name=value` per line). */
  headers: string
  /** Secret name holding the bearer token; never the value. */
  tokenEnv: string
  /** Write-only token typed into the form; blank keeps the stored one. */
  token: string
  hasToken: boolean
  enabled: boolean
  /** `null` for a new server; the old name when editing (rename support). */
  originalName: string | null
}

const state = ref<McpState | null>(null)
const loading = ref(true)
const loadError = ref('')

// — HA bridge ————————————————————————————————————————————————
const baseUrl = ref('')
const token = ref('')
const probe = ref<McpProbe | null>(null)
const busy = ref('')
const error = ref('')
const notice = ref('')

// — generic MCP ————————————————————————————————————————————
const gate = ref(false)
const gateBusy = ref(false)
const gateNotice = ref('')
const gateError = ref('')
const serverBusy = ref('')
const serverNotice = ref('')
const serverLoadError = ref('')
const rowProbes = reactive<Record<string, McpProbe | null>>({})
const serverErrors = reactive<Record<string, string>>({})
const editing = ref<DraftServer | null>(null)
const editingIndex = ref(-1)
const formErrors = reactive<Record<string, string>>({})
const saveError = ref('')

const servers = computed<McpServerStatus[]>(() => state.value?.servers ?? [])
/**
 * HA is rendered as an MCP instance, not a second product. The manifest carries the
 * whitelist/danger policy and `local.ha_*` prefix; older control planes omit it, so
 * the fallback keeps the card readable without inventing policy.
 */
const firstParty = computed<FirstPartyMcpInstance | null>(
  () => state.value?.first_party?.[0] ?? null,
)
const haWhitelist = computed(() => firstParty.value?.whitelist ?? [])
const haExcluded = computed(() => firstParty.value?.excluded_domains ?? [])
const haPrefix = computed(() => firstParty.value?.tool_prefix ?? 'local.ha_')
const generic = computed(() => {
  const given = state.value?.generic
  if (given) return given
  // Older control planes only return the flat list; derive the summary.
  return {
    enabled: servers.value.filter((server) => server.enabled).length,
    connected: servers.value.filter((server) => server.connected).length,
    tools: servers.value.reduce((sum, server) => sum + (server.tools || 0), 0),
    stdio_enabled: gate.value,
  }
})

const summary = computed(() =>
  t('mcp.genericSummary', {
    connected: generic.value.connected,
    enabled: generic.value.enabled,
    tools: generic.value.tools,
  }),
)

const editingTitle = computed(() =>
  editing.value?.originalName
    ? t('mcp.editServer', { name: editing.value.originalName })
    : t('mcp.newServer'),
)

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true
  loadError.value = ''
  serverLoadError.value = ''
  try {
    const next = await api.mcp()
    state.value = next
    baseUrl.value = next.ha.base_url
    gate.value = Boolean(next.stdio_enabled ?? next.generic?.stdio_enabled ?? false)
    for (const key of Object.keys(rowProbes)) delete rowProbes[key]
  } catch (exc) {
    const message = exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc)
    if (!silent) loadError.value = message
    else serverLoadError.value = message
  } finally {
    loading.value = false
  }
}

// — HA actions ——————————————————————————————————————————————

async function testHa(): Promise<void> {
  busy.value = 'test'
  probe.value = null
  error.value = ''
  try {
    probe.value = await api.testMcp({ base_url: baseUrl.value.trim() })
    await load(true)
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function saveHa(): Promise<void> {
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

// — stdio gate ————————————————————————————————————————————

async function toggleGate(next: boolean): Promise<void> {
  if (gateBusy.value) return
  if (!window.confirm(next ? t('mcp.stdioGateEnableConfirm') : t('mcp.stdioGateDisableConfirm'))) return
  gateBusy.value = true
  gateNotice.value = ''
  gateError.value = ''
  try {
    await api.saveConfigValues({ 'tools.mcp_allow_stdio': next })
    await load(true)
    gateNotice.value = t('mcp.stdioGateSaved')
  } catch (exc) {
    gateError.value = t('mcp.stdioGateFailed', {
      message: exc instanceof ApiError ? exc.message : String(exc),
    })
    await load(true)
  } finally {
    gateBusy.value = false
  }
}

// — server list helpers ——————————————————————————————————————

function serverStatus(server: McpServerStatus): { key: string; label: string; dot: string; badge: string } {
  if (!server.enabled) {
    return { key: 'disabled', label: t('mcp.statusDisabled'), dot: 'warn', badge: 'badge-muted' }
  }
  if (server.connected) {
    return { key: 'connected', label: t('mcp.statusConnected'), dot: 'ok', badge: 'badge-ok' }
  }
  if (server.transport === 'stdio' && !gate.value) {
    return { key: 'gated', label: t('mcp.statusGated'), dot: 'warn', badge: 'badge-warn' }
  }
  return { key: 'disconnected', label: t('mcp.statusDisconnected'), dot: 'fail', badge: 'badge-fail' }
}

function endpoint(server: McpServerStatus): string {
  return server.transport === 'stdio' ? (server.command ?? []).join(' ') : server.url
}

function lastSeen(server: McpServerStatus): string {
  if (!server.last_seen) return t('mcp.lastSeenNever')
  const date = new Date(server.last_seen)
  if (Number.isNaN(date.getTime())) return server.last_seen
  return t('mcp.serverLastSeen', { time: date.toLocaleTimeString(undefined, { hour12: false }) })
}

function serverErrorText(server: McpServerStatus): string {
  if (server.transport === 'stdio' && !gate.value) return t('mcp.serverGatedError')
  return server.error
}

function toolNames(server: McpServerStatus): string {
  const names = server.tool_names ?? []
  return names.length ? t('mcp.toolNames', { names: names.slice(0, 8).join(', ') }) : ''
}

function emptyDraft(): DraftServer {
  return {
    name: '', transport: 'http', url: '', command: '', args: '', env: '',
    headers: '', tokenEnv: '', token: '', hasToken: false,
    enabled: true, originalName: null,
  }
}

function headerLines(headers: Record<string, string>): string {
  return Object.entries(headers).map(([key, value]) => `${key}=${value}`).join('\n')
}

function draftFrom(server: McpServerStatus): DraftServer {
  const command = server.command ?? []
  return {
    name: server.name,
    transport: server.transport === 'stdio' ? 'stdio' : 'http',
    url: server.url ?? '',
    command: command[0] ?? '',
    args: command.slice(1).join(' '),
    env: Object.entries(server.env ?? {})
      .map(([key, value]) => `${key}=${value}`)
      .join('\n'),
    headers: headerLines(server.headers ?? {}),
    tokenEnv: server.token_env ?? '',
    token: '',
    hasToken: Boolean(server.has_token),
    enabled: Boolean(server.enabled),
    originalName: server.name,
  }
}

function clearFormMessages(): void {
  for (const key of Object.keys(formErrors)) delete formErrors[key]
  saveError.value = ''
  editingIndex.value = -1
}

async function revealForm(): Promise<void> {
  await nextTick()
  document.querySelector<HTMLElement>('.server-form')?.scrollIntoView({
    behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'center',
  })
}

function startAdd(): void {
  clearFormMessages()
  serverNotice.value = ''
  editing.value = emptyDraft()
  editingIndex.value = -1
  void revealForm()
}

function startEdit(server: McpServerStatus, index: number): void {
  clearFormMessages()
  serverNotice.value = ''
  editing.value = draftFrom(server)
  editingIndex.value = index
  void revealForm()
}

function cancelEdit(): void {
  editing.value = null
  clearFormMessages()
}

function tokenizeArgs(text: string): string[] {
  const matches = text.match(/"[^"]*"|'[^']*'|\S+/g) ?? []
  return matches.map((token) => {
    if ((token.startsWith('"') && token.endsWith('"')) || (token.startsWith("'") && token.endsWith("'"))) {
      return token.slice(1, -1)
    }
    return token
  })
}

function parseEnv(text: string): { env: Record<string, string>; invalid: { line: number; text: string } | null } {
  const env: Record<string, string> = {}
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index].trim()
    if (!raw || raw.startsWith('#')) continue
    const match = raw.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/)
    if (!match) return { env, invalid: { line: index + 1, text: raw } }
    env[match[1]] = match[2]
  }
  return { env, invalid: null }
}

/** `Name=value` per line (a colon also works), matching the backend's validation. */
function parseHeaders(text: string): {
  headers: Record<string, string>
  invalid: { line: number; text: string } | null
  duplicate: string | null
} {
  const headers: Record<string, string> = {}
  const seen = new Set<string>()
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index].trim()
    if (!raw || raw.startsWith('#')) continue
    const match = raw.match(/^([!#$%&'*+\-.^_`|~0-9A-Za-z]+)\s*[:=]\s*(.*)$/)
    if (!match) return { headers, invalid: { line: index + 1, text: raw }, duplicate: null }
    const key = match[1]
    if (seen.has(key.toLowerCase())) {
      return { headers, invalid: null, duplicate: key }
    }
    seen.add(key.toLowerCase())
    headers[key] = match[2]
  }
  return { headers, invalid: null, duplicate: null }
}

function hasAuthorizationHeader(text: string): boolean {
  return Object.keys(parseHeaders(text).headers).some((key) => key.toLowerCase() === 'authorization')
}

function draftError(draft: DraftServer): Record<string, string> {
  const errors: Record<string, string> = {}
  const name = draft.name.trim()
  if (!name) errors.name = t('mcp.formNameRequired')
  else if (servers.value.some((server) => server.name === name && server.name !== draft.originalName)) {
    errors.name = t('mcp.formNameDuplicate')
  }
  if (draft.transport === 'http') {
    const url = draft.url.trim()
    if (!url) errors.url = t('mcp.formUrlRequired')
    else if (!/^https?:\/\//i.test(url)) errors.url = t('mcp.formUrlScheme')
    const parsed = parseHeaders(draft.headers)
    if (parsed.invalid) {
      errors.headers = t('mcp.headersInvalid', { line: parsed.invalid.line, text: parsed.invalid.text })
    } else if (parsed.duplicate) {
      errors.headers = t('mcp.headersDuplicate', { name: parsed.duplicate })
    } else if (draft.token.trim() && hasAuthorizationHeader(draft.headers)) {
      errors.headers = t('mcp.headerAuthConflict')
    }
    const envName = draft.tokenEnv.trim()
    if (envName && !/^[A-Z][A-Z0-9_]*$/.test(envName)) {
      errors.tokenEnv = t('secrets.error.badName')
    }
  } else {
    if (!draft.command.trim()) errors.command = t('mcp.formCommandRequired')
    const parsed = parseEnv(draft.env)
    if (parsed.invalid) {
      errors.env = t('mcp.envInvalid', { line: parsed.invalid.line, text: parsed.invalid.text })
    }
  }
  return errors
}

function payloadFromDraft(draft: DraftServer): McpServerPayload {
  const name = draft.name.trim()
  if (draft.transport === 'http') {
    const token = draft.token.trim()
    return {
      name,
      transport: 'http',
      url: draft.url.trim(),
      command: [],
      env: {},
      headers: parseHeaders(draft.headers).headers,
      token_env: draft.tokenEnv.trim(),
      ...(token ? { token } : {}),
      enabled: draft.enabled,
    }
  }
  const command = draft.command.trim() ? [draft.command.trim(), ...tokenizeArgs(draft.args)] : []
  return {
    name,
    transport: 'stdio',
    url: '',
    command,
    env: parseEnv(draft.env).env,
    enabled: draft.enabled,
  }
}

function payloadFromServer(server: McpServerStatus): McpServerPayload {
  return {
    name: server.name,
    transport: server.transport === 'stdio' ? 'stdio' : 'http',
    url: server.url ?? '',
    command: server.command ?? [],
    env: server.env ?? {},
    headers: server.headers ?? {},
    token_env: server.token_env ?? '',
    enabled: Boolean(server.enabled),
  }
}

function issueIndex(loc: (string | number)[]): number | null {
  for (const marker of ['servers', 'mcp_servers']) {
    const at = loc.findIndex((part) => String(part) === marker)
    if (at >= 0 && loc[at + 1] !== undefined) {
      const index = Number(loc[at + 1])
      if (Number.isFinite(index)) return index
    }
  }
  return null
}

function issueField(loc: (string | number)[]): string {
  const last = String(loc[loc.length - 1] ?? '')
  return ['name', 'transport', 'url', 'command', 'env', 'headers', 'token_env',
    'token', 'enabled'].includes(last) ? last : 'general'
}

function applyServerIssues(exc: ApiError, expectedIndex: number, editingName: string): void {
  const issues = exc.issues ?? []
  let count = 0
  for (const issue of issues) {
    const loc = issue.loc ?? []
    const index = issueIndex(loc)
    const field = issueField(loc)
    let message = localizeIssue(issue.msg)
    if (/stdio MCP is disabled/i.test(message)) message = t('mcp.stdioHint')
    if (index != null && index === expectedIndex) {
      formErrors[field] = message
      count += 1
    } else if (index != null && servers.value[index]) {
      serverErrors[servers.value[index].name] = message
      count += 1
    } else if (formErrors[field] === undefined) {
      formErrors[field] = message
      count += 1
    }
  }
  if (count && !saveError.value) saveError.value = t('mcp.validationFailed', { n: count })
  if (editingName && !count) saveError.value = exc.message
}

async function saveServer(): Promise<void> {
  const draft = editing.value
  if (!draft || serverBusy.value) return
  for (const key of Object.keys(formErrors)) delete formErrors[key]
  saveError.value = ''
  const errors = draftError(draft)
  if (Object.keys(errors).length) {
    Object.assign(formErrors, errors)
    return
  }
  serverBusy.value = 'save'
  const expectedIndex = editingIndex.value >= 0 ? editingIndex.value : servers.value.length
  const list = servers.value.map(payloadFromServer)
  const payload = payloadFromDraft(draft)
  if (editingIndex.value >= 0) list[editingIndex.value] = payload
  else list.push(payload)
  try {
    const next = await api.saveMcp({ servers: list })
    state.value = next
    gate.value = Boolean(next.stdio_enabled ?? next.generic?.stdio_enabled ?? gate.value)
    editing.value = null
    clearFormMessages()
    serverNotice.value = t('mcp.serverSaved')
  } catch (exc) {
    if (exc instanceof ApiError) {
      applyServerIssues(exc, expectedIndex, draft.name)
    } else {
      saveError.value = t('mcp.saveFailed', {
        message: exc instanceof Error ? exc.message : String(exc),
      })
    }
  } finally {
    serverBusy.value = ''
  }
}

async function removeServer(server: McpServerStatus, index: number): Promise<void> {
  if (serverBusy.value) return
  if (!window.confirm(t('mcp.removeConfirm', { name: server.name }))) return
  serverBusy.value = `remove:${server.name}`
  serverNotice.value = ''
  try {
    const list = servers.value.filter((_, itemIndex) => itemIndex !== index).map(payloadFromServer)
    state.value = await api.saveMcp({ servers: list })
    if (editing.value?.originalName === server.name) cancelEdit()
    serverNotice.value = t('mcp.serverRemoved', { name: server.name })
  } catch (exc) {
    serverErrors[server.name] = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    serverBusy.value = ''
  }
}

// — probes ——————————————————————————————————————————————————

function probeError(exc: unknown): McpProbe {
  return {
    ok: false,
    attempted: true,
    latency_ms: null,
    tools: 0,
    error: exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc),
  }
}

async function testServer(server: McpServerStatus): Promise<void> {
  if (serverBusy.value) return
  serverBusy.value = `test:${server.name}`
  rowProbes[server.name] = null
  try {
    rowProbes[server.name] = await api.testMcp({
      name: server.name,
      transport: server.transport === 'stdio' ? 'stdio' : 'http',
      url: server.url,
      command: server.command ?? [],
      headers: server.headers ?? {},
      token_env: server.token_env ?? '',
    })
  } catch (exc) {
    rowProbes[server.name] = probeError(exc)
  } finally {
    serverBusy.value = ''
  }
}

async function testDraft(): Promise<void> {
  const draft = editing.value
  if (!draft || serverBusy.value) return
  for (const key of Object.keys(formErrors)) delete formErrors[key]
  const errors = draftError(draft)
  if (['name', 'url', 'command', 'env', 'headers', 'tokenEnv'].some((key) => errors[key])) {
    Object.assign(formErrors, errors)
    return
  }
  serverBusy.value = 'test:draft'
  rowProbes.draft = null
  const payload = payloadFromDraft(draft)
  try {
    rowProbes.draft = await api.testMcp({
      name: payload.name,
      transport: payload.transport,
      url: payload.url,
      command: payload.command,
      headers: payload.headers,
      token_env: payload.token_env,
      token: payload.token,
    })
  } catch (exc) {
    rowProbes.draft = probeError(exc)
  } finally {
    serverBusy.value = ''
  }
}

function probeText(result: McpProbe | null): string {
  if (!result) return ''
  if (result.attempted === false) return t('mcp.testAttemptedFalse')
  if (result.ok) {
    return t('mcp.testOkGeneric', {
      tools: result.tools,
      ms: result.latency_ms == null ? '?' : result.latency_ms.toFixed(1),
    })
  }
  return t('mcp.testFailGeneric', { error: result.error || t('mcp.error') })
}

onMounted(load)
</script>

<template>
  <p class="lede mgr-intro">{{ t('mcp.lede') }}</p>

  <div v-if="loading" class="mcp-loading" role="status">
    <div class="skeleton skeleton-line" style="width: 40%" />
    <div class="skeleton skeleton-line" style="width: 70%" />
    <div class="skeleton skeleton-line" style="width: 55%" />
    <p class="note">{{ t('mcp.loading') }}</p>
  </div>

  <div v-else-if="loadError" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <div>
      <p>{{ t('mcp.loadError', { message: loadError }) }}</p>
      <button type="button" class="btn btn-sm" style="margin-top: 10px" @click="load()">{{ t('mcp.reload') }}</button>
    </div>
  </div>

  <template v-if="state">
    <!-- — HA as a first-party MCP instance; behaviour is unchanged —————— -->
    <section class="first-party-card">
      <div class="block-head">
        <div>
          <div class="kicker">{{ t('mcp.firstPartyTitle') }}</div>
          <h2>{{ firstParty?.name ?? 'Home Assistant' }}</h2>
          <p class="field-hint first-party-note">{{ t('mcp.firstPartyNote') }}</p>
        </div>
        <div class="generic-head-actions">
          <span class="badge badge-ok">{{ t('mcp.firstPartyBadge') }}</span>
          <span class="badge" :class="state.ha.connected ? 'badge-ok' : 'badge-warn'">
            {{ state.ha.wired ? (state.ha.connected ? t('mcp.connected') : t('mcp.disconnected')) : t('mcp.notConfigured') }}
          </span>
        </div>
      </div>

      <div class="ha-policy">
        <span class="ha-prefix mono">{{ t('mcp.toolPrefix') }}: {{ haPrefix }}</span>
        <details class="ha-policy-details">
          <summary>{{ t('mcp.policy') }}</summary>
          <p>{{ t('mcp.whitelist') }}: <code class="mono">{{ haWhitelist.join(', ') || '—' }}</code></p>
          <p>{{ t('mcp.toolNames', { names: (firstParty?.tool_names ?? []).join(', ') || '—' }) }}</p>
          <p>{{ t('mcp.excludedDomains') }}: <code class="mono">{{ haExcluded.join(', ') || '—' }}</code></p>
          <p v-if="firstParty?.danger_policy" class="mono">{{ firstParty.danger_policy }}</p>
        </details>
        <RouterLink class="ha-ledger-link" to="/panel/credentials">{{ t('mcp.credentialsLink') }}</RouterLink>
      </div>

      <div class="surface health-surface">
        <div class="block-head">
          <h3>{{ t('mcp.connection') }}</h3>
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
          <span class="mcp-k">{{ t('mcp.token') }}</span>
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
          <label class="field-label" for="mcp-token">{{ t('mcp.token') }}</label>
          <p class="field-hint">{{ t('mcp.tokenHelp') }}</p>
        </div>
        <div class="field-control">
          <input id="mcp-token" v-model="token" type="password" autocomplete="off" :placeholder="t('mcp.tokenPlaceholder')" />
        </div>
      </div>

      <details class="mcp-advanced">
        <summary>{{ t('mcp.advanced') }}</summary>
        <p class="field-hint mono">{{ t('mcp.tokenEnvHint', { env: state.ha.token_env || '—' }) }}</p>
      </details>

      <div class="actions" style="margin-top: var(--s4)">
        <button type="button" class="btn" :disabled="busy !== ''" @click="testHa">
          {{ busy === 'test' ? t('mcp.testing') : t('mcp.test') }}
        </button>
        <button type="button" class="btn btn-primary" :disabled="busy !== ''" @click="saveHa">
          {{ busy === 'save' ? t('mcp.saving') : t('mcp.save') }}
        </button>
      </div>
      <p v-if="probe" :class="probe.ok ? 'ok' : 'fail'" class="note">
        {{ probe.ok
          ? t('mcp.testOk', {
              tools: probe.tools,
              entities: probe.entities ?? 0,
              ms: probe.latency_ms == null ? '?' : probe.latency_ms.toFixed(1),
            })
          : t('mcp.testFail', { error: probe.error || t('mcp.error') }) }}
      </p>
      <p v-if="notice" class="note ok">{{ notice }}</p>
      <p v-if="error" class="note fail" role="alert">{{ error }}</p>
      </div>
    </section>

    <!-- — generic MCP servers ——————————————————————————————— -->
    <section class="block mcp-generic">
      <div class="block-head">
        <div class="generic-title">
          <h2>{{ t('mcp.genericTitle') }}</h2>
          <p class="field-hint">{{ t('mcp.genericNote') }}</p>
        </div>
        <div class="generic-head-actions">
          <span class="badge" :class="generic.connected ? 'badge-ok' : 'badge-warn'">{{ summary }}</span>
          <button type="button" class="btn btn-sm btn-primary" :disabled="Boolean(serverBusy)" @click="startAdd">
            <AppIcon name="plus" :size="13" />
            {{ t('mcp.addServer') }}
          </button>
        </div>
      </div>

      <div class="stdio-gate" :class="{ enabled: gate }">
        <div class="stdio-copy">
          <div class="stdio-title">
            <AppIcon name="alert" :size="16" class="stdio-icon" />
            <h3>{{ t('mcp.stdioGateTitle') }}</h3>
            <span class="badge" :class="gate ? 'badge-fail' : 'badge-muted'">
              {{ gate ? t('mcp.stdioGateOn') : t('mcp.stdioGateOff') }}
            </span>
          </div>
          <p class="stdio-warning">{{ t('mcp.stdioGateWarning') }}</p>
          <p class="stdio-value mono">{{ t('mcp.stdioGateValue') }}</p>
          <p v-if="gateNotice" class="note ok" role="status">{{ gateNotice }}</p>
          <p v-if="gateError" class="note fail" role="alert">{{ gateError }}</p>
        </div>
        <button
          type="button"
          class="gate-switch"
          :class="{ on: gate }"
          role="switch"
          :aria-checked="gate"
          :aria-label="t('mcp.stdioGateTitle')"
          :disabled="gateBusy"
          @click="toggleGate(!gate)"
        >
          <span class="gate-knob" />
          <span class="gate-label">{{ gate ? t('mcp.stdioGateOn') : t('mcp.stdioGateOff') }}</span>
        </button>
      </div>

      <div v-if="serverLoadError" class="banner banner-fail" role="alert">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>{{ t('mcp.loadError', { message: serverLoadError }) }}</span>
        <button type="button" class="btn btn-sm" @click="load()">{{ t('mcp.reload') }}</button>
      </div>

      <p v-if="serverNotice" class="note ok" role="status">{{ serverNotice }}</p>
      <form v-if="editing" class="server-form" @submit.prevent="saveServer">
        <div class="form-head">
          <div>
            <div class="kicker">{{ t('mcp.genericTitle') }}</div>
            <h3>{{ editingTitle }}</h3>
          </div>
          <button type="button" class="btn btn-ghost btn-sm" @click="cancelEdit">{{ t('mcp.cancel') }}</button>
        </div>

        <div class="form-grid">
          <div class="form-field" :class="{ invalid: formErrors.name }">
            <label for="srv-name">{{ t('mcp.serverName') }}</label>
            <input id="srv-name" v-model="editing.name" maxlength="32" :placeholder="t('mcp.serverNamePlaceholder')" />
            <p v-if="formErrors.name" class="field-error">{{ formErrors.name }}</p>
          </div>

          <div class="form-field" :class="{ invalid: formErrors.transport }">
            <label for="srv-transport">{{ t('mcp.transport') }}</label>
            <select id="srv-transport" v-model="editing.transport">
              <option value="http">{{ t('mcp.transportHttp') }}</option>
              <option value="stdio">{{ t('mcp.transportStdio') }}</option>
            </select>
            <p v-if="formErrors.transport" class="field-error">{{ formErrors.transport }}</p>
          </div>

          <template v-if="editing.transport === 'http'">
            <div class="form-field wide" :class="{ invalid: formErrors.url }">
              <label for="srv-url">{{ t('mcp.url') }}</label>
              <input id="srv-url" v-model="editing.url" :placeholder="t('mcp.urlPlaceholder')" />
              <p v-if="formErrors.url" class="field-error">{{ formErrors.url }}</p>
            </div>
            <div class="form-field wide" :class="{ invalid: formErrors.headers }">
              <label for="srv-headers">{{ t('mcp.headers') }}</label>
              <textarea id="srv-headers" v-model="editing.headers" rows="2" :placeholder="t('mcp.headersPlaceholder')" />
              <p v-if="formErrors.headers" class="field-error">{{ formErrors.headers }}</p>
            </div>
            <div class="form-field" :class="{ invalid: formErrors.token }">
              <label for="srv-token">{{ t('mcp.tokenWriteOnly') }}</label>
              <input
                id="srv-token"
                v-model="editing.token"
                type="password"
                autocomplete="new-password"
                spellcheck="false"
                :placeholder="t('mcp.tokenWritePlaceholder')"
              />
              <p class="field-hint">
                {{ editing.hasToken ? t('mcp.tokenConfigured') : t('mcp.tokenNotConfigured') }}
              </p>
              <p v-if="formErrors.token" class="field-error">{{ formErrors.token }}</p>
            </div>
            <div class="form-field" :class="{ invalid: formErrors.tokenEnv }">
              <label for="srv-token-env">{{ t('mcp.tokenEnv') }}</label>
              <input id="srv-token-env" v-model="editing.tokenEnv" placeholder="MCP_NOTES_TOKEN" />
              <p class="field-hint">{{ t('mcp.tokenEnvFieldHint') }}</p>
              <p v-if="formErrors.tokenEnv" class="field-error">{{ formErrors.tokenEnv }}</p>
            </div>
          </template>

          <template v-else>
            <div class="form-field" :class="{ invalid: formErrors.command }">
              <label for="srv-command">{{ t('mcp.command') }}</label>
              <input id="srv-command" v-model="editing.command" :placeholder="t('mcp.commandPlaceholder')" />
              <p v-if="formErrors.command" class="field-error">{{ formErrors.command }}</p>
            </div>
            <div class="form-field">
              <label for="srv-args">{{ t('mcp.args') }}</label>
              <input id="srv-args" v-model="editing.args" :placeholder="t('mcp.argsPlaceholder')" />
            </div>
            <div class="form-field wide" :class="{ invalid: formErrors.env }">
              <label for="srv-env">{{ t('mcp.env') }}</label>
              <textarea id="srv-env" v-model="editing.env" rows="3" :placeholder="t('mcp.envPlaceholder')" />
              <p v-if="formErrors.env" class="field-error">{{ formErrors.env }}</p>
              <p class="env-warning">{{ t('mcp.envWarning') }}</p>
            </div>
            <p v-if="!gate" class="form-warning wide">{{ t('mcp.stdioHint') }}</p>
          </template>

          <div class="form-field">
            <label>{{ t('mcp.enabled') }}</label>
            <label class="switch">
              <input v-model="editing.enabled" type="checkbox" />
              <span class="track" />
              <span class="switch-text">{{ editing.enabled ? t('common.on') : t('common.off') }}</span>
            </label>
          </div>
        </div>

        <p v-if="saveError" class="banner banner-fail form-banner" role="alert">{{ saveError }}</p>
        <p
          v-if="rowProbes.draft"
          class="probe form-banner"
          :class="rowProbes.draft?.ok ? 'ok' : (rowProbes.draft?.attempted === false ? 'warn' : 'fail')"
          role="status"
        >
          {{ probeText(rowProbes.draft) }}
        </p>

        <div class="actions">
          <button type="button" class="btn" :disabled="Boolean(serverBusy)" @click="testDraft">
            {{ serverBusy === 'test:draft' ? t('mcp.testingServer') : t('mcp.testServer') }}
          </button>
          <button type="button" class="btn btn-ghost" :disabled="Boolean(serverBusy)" @click="cancelEdit">
            {{ t('mcp.cancel') }}
          </button>
          <button type="submit" class="btn btn-primary" :disabled="Boolean(serverBusy)">
            {{ serverBusy === 'save' ? t('mcp.savingServer') : t('mcp.saveServer') }}
          </button>
        </div>
      </form>

      <p v-if="!servers.length" class="empty generic-empty">{{ t('mcp.genericEmpty') }}</p>

      <div v-for="(server, index) in servers" :key="server.name" class="server-card" :class="serverStatus(server).key">
        <div class="server-head">
          <div class="server-title">
            <span class="dot" :class="serverStatus(server).dot" />
            <h3>{{ server.name }}</h3>
            <span class="badge">{{ server.transport }}</span>
            <span v-if="server.token_env" class="badge" :class="server.has_token ? 'badge-ok' : 'badge-warn'">
              {{ server.has_token ? t('mcp.tokenConfigured') : t('mcp.tokenNotConfigured') }}
            </span>
            <span class="badge" :class="serverStatus(server).badge">{{ serverStatus(server).label }}</span>
          </div>
          <div class="server-stats mono">
            <span v-if="server.connected" :title="toolNames(server)">{{ t('mcp.serverTools', { n: server.tools }) }}</span>
            <span v-if="server.latency_ms != null">{{ server.latency_ms.toFixed(1) }} ms</span>
            <span>{{ lastSeen(server) }}</span>
          </div>
        </div>

        <div class="server-endpoint">
          <code class="mono">{{ endpoint(server) || '—' }}</code>
        </div>

        <p v-if="server.error" class="server-error">{{ serverErrorText(server) }}</p>
        <p v-if="serverErrors[server.name]" class="server-error" role="alert">{{ serverErrors[server.name] }}</p>
        <p v-if="toolNames(server)" class="server-tools">{{ toolNames(server) }}</p>
        <p
          v-if="rowProbes[server.name]"
          class="probe"
          :class="rowProbes[server.name]?.ok ? 'ok' : (rowProbes[server.name]?.attempted === false ? 'warn' : 'fail')"
        >
          {{ probeText(rowProbes[server.name] ?? null) }}
        </p>

        <div class="actions server-actions">
          <button type="button" class="btn btn-sm" :disabled="Boolean(serverBusy)" @click="testServer(server)">
            {{ serverBusy === `test:${server.name}` ? t('mcp.testingServer') : t('mcp.testServer') }}
          </button>
          <button type="button" class="btn btn-sm" :disabled="Boolean(serverBusy)" @click="startEdit(server, index)">
            {{ t('mcp.edit') }}
          </button>
          <button type="button" class="btn btn-sm btn-danger" :disabled="Boolean(serverBusy)" @click="removeServer(server, index)">
            {{ t('mcp.remove') }}
          </button>
        </div>
      </div>

    </section>
  </template>
</template>

<style scoped>
.mgr-intro {
  margin-bottom: var(--s5);
}

.mcp-loading {
  padding: var(--s5) 0;
}

.first-party-card {
  margin-bottom: var(--s6);
  padding: var(--s4);
  border: 1px solid var(--line);
  border-left: 2px solid var(--accent);
  border-radius: var(--r-md);
  background: var(--surface);
}

.first-party-note {
  max-width: 82ch;
  margin-top: 4px;
}

.ha-policy {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  flex-wrap: wrap;
  margin: var(--s3) 0 var(--s4);
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  font-size: 11.5px;
  color: var(--ink-2);
}

.ha-prefix {
  font-size: 11px;
  color: var(--muted);
}

.ha-policy-details summary {
  cursor: pointer;
  color: var(--muted);
}

.ha-policy-details p {
  margin-top: 5px;
  overflow-wrap: anywhere;
}

.ha-ledger-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--accent);
}

.health-surface {
  margin-bottom: 0;
}

.mcp-advanced {
  margin-top: var(--s3);
  color: var(--muted);
  font-size: 11px;
}

.mcp-advanced summary {
  cursor: pointer;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.mcp-advanced summary::-webkit-details-marker {
  display: none;
}

.mcp-advanced summary::before {
  content: '＋';
  font-size: 10px;
}

.mcp-advanced[open] summary::before {
  content: '－';
}

.mcp-advanced code {
  display: inline-block;
  margin-top: 4px;
  font-size: 11px;
  overflow-wrap: anywhere;
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

.mcp-generic {
  margin-top: var(--s6);
}

.generic-title {
  min-width: 0;
}

.generic-head-actions {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.stdio-gate {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s4);
  flex-wrap: wrap;
  margin: var(--s4) 0 var(--s5);
  padding: 14px 16px;
  border: 1px solid var(--warn);
  border-radius: var(--r-md);
  background: var(--warn-soft);
}

.stdio-gate.enabled {
  border-color: var(--fail);
  background: var(--fail-soft);
}

.stdio-copy {
  min-width: 0;
  flex: 1 1 420px;
}

.stdio-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.stdio-title h3 {
  font-family: var(--font-ui);
  font-size: 14px;
  font-weight: 600;
}

.stdio-icon {
  color: var(--warn);
}

.stdio-gate.enabled .stdio-icon {
  color: var(--fail);
}

.stdio-warning {
  margin-top: 4px;
  color: var(--ink-2);
  font-size: 12px;
  line-height: 1.55;
  max-width: 82ch;
}

.stdio-value {
  margin-top: 4px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.03em;
}

.gate-switch {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  padding: 4px 10px 4px 4px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  flex: none;
}

.gate-switch:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.gate-knob {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--muted);
  transition: transform var(--dur-mid) var(--ease), background var(--dur-mid) var(--ease);
}

.gate-switch.on {
  border-color: var(--fail);
  color: var(--fail);
  background: var(--fail-soft);
}

.gate-switch.on .gate-knob {
  background: var(--fail);
  transform: translateX(2px);
}

.gate-label {
  white-space: nowrap;
}

.generic-empty {
  padding: var(--s5) 0;
}

.server-card {
  border: 1px solid var(--line);
  border-left: 2px solid var(--line-strong);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: var(--s4);
  margin-bottom: var(--s3);
  min-width: 0;
}

.server-card.connected {
  border-left-color: var(--ok);
}

.server-card.gated,
.server-card.disconnected {
  border-left-color: var(--warn);
}

.server-card.disabled {
  border-left-color: var(--line-strong);
  background: var(--surface-2);
}

.server-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
}

.server-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  min-width: 0;
}

.server-title h3 {
  font-family: var(--font-ui);
  font-size: 14px;
  font-weight: 600;
  margin: 0;
}

.server-title .dot {
  width: 7px;
  height: 7px;
}

.server-stats {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 10.5px;
}

.server-endpoint {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.server-endpoint code {
  font-size: 11px;
  color: var(--ink-2);
  background: var(--surface-2);
  border-radius: var(--r-xs);
  padding: 3px 6px;
  overflow-wrap: anywhere;
}

.server-error {
  margin-top: var(--s2);
  color: var(--fail);
  font-size: 11.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.server-tools {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.probe {
  margin-top: var(--s2);
  font-size: 11.5px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.probe.ok {
  color: var(--ok);
}

.probe.warn {
  color: var(--warn);
}

.probe.fail {
  color: var(--fail);
}

.server-actions {
  margin-top: var(--s3);
}

.server-form {
  margin-top: var(--s4);
  padding: var(--s4);
  border: 1px solid var(--accent);
  border-radius: var(--r-md);
  background: var(--surface);
}

.form-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  margin-bottom: var(--s3);
}

.form-head h3 {
  font-family: var(--font-display);
  font-size: 19px;
  margin-top: 2px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s3) var(--s4);
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.form-field.wide {
  grid-column: 1 / -1;
}

.form-field label {
  font-size: 12px;
  color: var(--ink-2);
}

.form-field input,
.form-field select,
.form-field textarea {
  font-size: 12.5px;
}

.form-field.invalid input,
.form-field.invalid select,
.form-field.invalid textarea {
  border-color: var(--fail);
}

.field-error {
  color: var(--fail);
  font-size: 11px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.env-warning {
  color: var(--warn);
  font-size: 11px;
  line-height: 1.5;
}

.form-warning {
  color: var(--warn);
  font-size: 11.5px;
  line-height: 1.5;
}

.form-banner {
  margin-top: var(--s3);
  white-space: pre-wrap;
}

.server-form .actions {
  margin-top: var(--s4);
}

@media (max-width: 720px) {
  .mcp-stats {
    grid-template-columns: minmax(0, 1fr);
  }

  .generic-head-actions {
    justify-content: flex-start;
  }

  .stdio-gate {
    align-items: flex-start;
  }

  .gate-switch {
    width: 100%;
    justify-content: space-between;
  }

  .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .server-form .actions .btn,
  .server-actions .btn {
    flex: 1;
  }
}
</style>
