<script setup lang="ts">
/**
 * P5: HA over MCP - the endpoint, its token (write-only), a live test, and what the
 * bridge actually lists. Saving reconnects at runtime: the old bridge is stopped and
 * its tools are replaced, so a wrong URL is visible here instead of after a restart.
 *
 * `tools.mcp_servers` is shown read-only on purpose: the generic MCP client exists
 * but only HA is wired end to end, and pretending otherwise would be a lie in a
 * production console.
 */
import { onMounted, ref } from 'vue'
import { api, ApiError, type McpState } from '../api'

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
    notice.value = '已保存并重连；HA 工具已按新配置注册'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="sub">
    Home Assistant 经 MCP 接入：地址填 HA 根地址（自动补 /api/mcp）。保存会立即断开旧桥并重连，
    失败时这里会显示原因，模型侧只是暂时没有 HA 工具，其余功能不受影响。
  </p>

  <div v-if="state" class="card">
    <h2>HA 连接</h2>
    <div class="grid">
      <div class="row"><span class="k">状态</span>
        <span class="v" :class="state.ha.connected ? 'ok' : 'warn'">
          {{ state.ha.wired ? (state.ha.connected ? '已连接' : '未连接') : '未配置' }}</span></div>
      <div class="row"><span class="k">工具 / 实体</span>
        <span class="v">{{ state.ha.tools }} / {{ state.ha.entities }}</span></div>
      <div class="row"><span class="k">延迟</span>
        <span class="v">{{ state.ha.latency_ms == null ? '—' : state.ha.latency_ms.toFixed(1) + ' ms' }}</span></div>
      <div class="row"><span class="k">Token（{{ state.ha.token_env }}）</span>
        <span class="v" :class="state.ha.has_token ? 'ok' : 'warn'">
          {{ state.ha.has_token ? '已配置（只写不读）' : '未配置' }}</span></div>
    </div>
    <p v-if="state.ha.error" class="note fail">{{ state.ha.error }}</p>

    <div class="field"><label>HA 地址</label>
      <input v-model="baseUrl" placeholder="http://<LAN-IP>:8123" /></div>
    <div class="field"><label>长期访问令牌</label>
      <input v-model="token" type="password" autocomplete="off" placeholder="留空 = 不改动已存的 token" /></div>
    <div class="actions">
      <button :disabled="busy !== ''" @click="test">{{ busy === 'test' ? '测试中…' : '测试连接（不保存）' }}</button>
      <button class="primary" :disabled="busy !== ''" @click="save">{{ busy === 'save' ? '保存中…' : '保存并重连' }}</button>
    </div>
    <p v-if="probe" :class="probe.ok ? 'ok' : 'fail'">
      {{ probe.ok
        ? `连接成功：${probe.tools} 个工具、${probe.entities} 个实体、${probe.latency_ms == null ? '?' : probe.latency_ms.toFixed(1)} ms`
        : `连接失败：${probe.error || '未知原因'}` }}
    </p>
    <p v-if="notice" class="note ok">{{ notice }}</p>
    <div v-if="error" class="note fail">{{ error }}</div>
  </div>

  <div v-if="state" class="card">
    <h2>通用 MCP 服务（预留）</h2>
    <div v-if="!state.servers.length" class="empty">还没有配置通用 MCP 服务。</div>
    <div v-for="server in state.servers" :key="server.name" class="row">
      <span class="v">{{ server.name }}</span>
      <span class="k" style="width: auto">{{ server.transport }} · {{ server.url }} · {{ server.enabled ? '启用' : '停用' }}</span>
    </div>
    <p class="note">
      通用 MCP 客户端代码已存在，但目前只有 HA 端到端接线；这一块只读展示，避免控制台给出「配了就能用」的假象。
    </p>
  </div>
</template>

<style scoped>
.field { display: flex; gap: 12px; align-items: center; margin-bottom: 8px; margin-top: 10px; }
.field label { width: 200px; color: var(--muted); font-size: 13px; flex: none; }
.field input {
  font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1; max-width: 460px;
}
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.grid .row { border-bottom: 1px solid #f2f3f6; }
</style>
