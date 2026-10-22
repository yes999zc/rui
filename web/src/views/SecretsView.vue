<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api, ApiError, type Config, type SecretPutResult } from '../api'

/**
 * The key names the console offers.
 *
 * Hard-coded on purpose: a secret is only useful to something that reads that exact variable,
 * so this list is the set of names the backend already looks up. Anything else would be a
 * name stored and never read.
 */
interface Slot {
  name: string
  label: string
  /** What reads it. */
  used: string
  hint: string
}

const slots: Slot[] = [
  {
    name: 'DEEPSEEK_API_KEY',
    label: '搜索 / LLM',
    used: 'tools.search，以及 DeepSeek 供应商',
    hint: '联网搜索和 DeepSeek 模型共用。填了它，搜索工具才算真的能跑。',
  },
  {
    name: 'SILICONFLOW_API_KEY',
    label: '语音合成备用',
    used: 'TTS 备用链（SiliconFlow）',
    hint: '主 TTS 挂了的时候顶上去。不用主 TTS 就可以先空着。',
  },
  {
    name: 'XFYUN_API_KEY',
    label: '讯飞 TTS',
    used: 'TTS 链里的讯飞',
    hint: '讯飞语音合成。它的 appid / secret 如果分开配，仍然走环境变量，这里只存这一把。',
  },
]

type Phase = 'idle' | 'saving' | 'saved' | 'cleared' | 'error'

/**
 * Per-name input state. `value` is the only place a typed key lives: it is never copied into
 * a store, storage, or a log, and it is wiped the moment the server confirms it.
 */
interface Row {
  value: string
  phase: Phase
  at: string
  /** The backend's own words when a write failed. */
  detail: string
  confirmClear: boolean
}

const rows = reactive<Record<string, Row>>(
  Object.fromEntries(
    slots.map((s) => [s.name, { value: '', phase: 'idle' as Phase, at: '', detail: '', confirmClear: false }]),
  ),
)

const cfg = ref<Config | null>(null)
/** name → has_key, straight from `/api/v1/config`. The server decides, not the client. */
const hasKey = reactive<Record<string, boolean>>({})
/** name → who reads it, derived from the same config. */
const consumers = reactive<Record<string, string[]>>({})
const loading = ref(true)
const loadError = ref('')

const configuredCount = computed(() => slots.filter((s) => hasKey[s.name]).length)

function note(mapping: Record<string, string[]>, env: string | undefined, who: string) {
  if (!env) return
  const list = (mapping[env] ??= [])
  if (!list.includes(who)) list.push(who)
}

function absorbConfig(next: Config) {
  cfg.value = next
  for (const key of Object.keys(hasKey)) delete hasKey[key]
  for (const key of Object.keys(consumers)) delete consumers[key]

  const catalog = next.providers?.catalog ?? {}
  const search = next.tools?.search

  // has_key is authoritative: the server computes it from the environment *and* secrets.yaml.
  for (const [provider, block] of Object.entries(catalog)) {
    const env = block?.api_key_env
    if (!env) continue
    note(consumers, env, provider)
    hasKey[env] = hasKey[env] || Boolean(block.has_key)
  }
  const env = search?.api_key_env
  if (env) {
    note(consumers, env, 'tools.search')
    hasKey[env] = hasKey[env] || Boolean(search?.has_key)
  }
}

/** Names on disk cover keys no provider block mentions (and env-only keys are already covered). */
function absorbNames(names: string[]) {
  for (const name of names) hasKey[name] = true
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [next, secretNames] = await Promise.all([api.config(), api.secrets()])
    absorbConfig(next)
    absorbNames(secretNames.names)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function clock(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function detailOf(e: unknown): string {
  if (e instanceof ApiError) {
    const raw = e.detail
    if (typeof raw === 'string' && raw) return raw
    if (Array.isArray(raw)) return e.message
    if (raw != null) return JSON.stringify(raw)
    return `${e.status} ${e.statusText}`
  }
  return e instanceof Error ? e.message : String(e)
}

function hintOf(e: unknown): string {
  if (!(e instanceof ApiError)) return '请求没到服务端：检查控制面是否在跑，或 token 是否过期。'
  if (e.status === 400) return '服务端拒了这个名字：必须像环境变量（大写字母、数字、下划线）。'
  if (e.status === 401) return 'token 无效或已过期。'
  return ''
}

function applyResult(name: string, res: SecretPutResult) {
  hasKey[name] = res.has_key
}

async function save(slot: Slot) {
  const row = rows[slot.name]
  if (!row || row.phase === 'saving') return
  const value = row.value.trim()
  if (!value) return
  row.phase = 'saving'
  row.detail = ''
  try {
    applyResult(slot.name, await api.putSecret(slot.name, value))
    // The key is on the server now; nothing on this page needs to remember what it was.
    row.value = ''
    row.phase = hasKey[slot.name] ? 'saved' : 'error'
    row.at = clock()
    if (row.phase === 'error') row.detail = '服务端没有确认这个值（写入后 has_key 仍为 false）。'
  } catch (e) {
    row.phase = 'error'
    row.detail = [detailOf(e), hintOf(e)].filter(Boolean).join('\n')
    row.at = ''
  }
}

async function clearSecret(slot: Slot) {
  const row = rows[slot.name]
  if (!row || row.phase === 'saving') return
  row.confirmClear = false
  row.phase = 'saving'
  row.detail = ''
  try {
    applyResult(slot.name, await api.putSecret(slot.name, ''))
    row.value = ''
    row.phase = 'cleared'
    row.at = clock()
  } catch (e) {
    row.phase = 'error'
    row.detail = [detailOf(e), hintOf(e)].filter(Boolean).join('\n')
    row.at = ''
  }
}

function onEdit(name: string) {
  const row = rows[name]
  if (!row) return
  if (row.phase === 'saved' || row.phase === 'cleared') {
    row.phase = 'idle'
    row.at = ''
  }
  row.detail = ''
}

function pillText(row: Row): string {
  if (row.phase === 'saving') return '保存中…'
  if (row.phase === 'saved') return `已保存${row.at ? ' · ' + row.at : ''}`
  if (row.phase === 'cleared') return `已清除${row.at ? ' · ' + row.at : ''}`
  if (row.phase === 'error') return '失败'
  return ''
}

onMounted(load)
</script>

<template>
  <h1>密钥</h1>
  <p class="sub">
    在这里填 key，服务端落到 <code>secrets.yaml</code>（0600）。写入后立即生效，不用重启。
    <strong>只写不读</strong>：没有任何接口能把值读回来，页面只能知道「已配置 / 未配置」。
  </p>

  <p v-if="loading" class="empty">读取配置…</p>

  <div v-if="loadError" class="card">
    <h2 class="fail">读不到配置</h2>
    <p class="fail">{{ loadError }}</p>
    <p class="note">控制面没起来，或 <code>X-Auth-Token</code> 不对。读不到状态就不让人写——不然「已配置」会是猜的。</p>
    <button style="margin-top: 12px" @click="load">重试</button>
  </div>

  <template v-if="cfg && !loading">
    <div class="card">
      <h2>
        环境变量
        <span class="tag">{{ configuredCount }} / {{ slots.length }} 已配置</span>
      </h2>

      <div v-for="s in slots" :key="s.name" class="slot">
        <div class="head">
          <div>
            <code class="name">{{ s.name }}</code>
            <span class="tag">{{ s.label }}</span>
            <span class="state" :class="hasKey[s.name] ? 'ok' : 'warn'">
              {{ hasKey[s.name] ? '已配置' : '未配置' }}
            </span>
          </div>
          <div class="who">
            读它的是：{{ consumers[s.name]?.join('、') || '——' }}
          </div>
        </div>

        <p class="note">
          {{ s.hint }} <span v-if="consumers[s.name]?.length">（{{ s.used }}）</span>
        </p>

        <div class="entry">
          <input
            :id="'k-' + s.name"
            v-model="rows[s.name].value"
            @input="onEdit(s.name)"
            @keyup.enter="save(s)"
            type="password"
            autocomplete="new-password"
            spellcheck="false"
            placeholder="粘贴新 key（不会回显，也不会预填）"
            :disabled="rows[s.name].phase === 'saving'"
            :aria-label="s.name"
          />
          <button
            class="primary"
            :disabled="rows[s.name].phase === 'saving' || !rows[s.name].value.trim()"
            @click="save(s)"
          >
            {{ rows[s.name].phase === 'saving' ? '保存中…' : '保存' }}
          </button>

          <template v-if="!rows[s.name].confirmClear">
            <button
              :disabled="rows[s.name].phase === 'saving' || !hasKey[s.name]"
              @click="rows[s.name].confirmClear = true"
            >
              清除
            </button>
          </template>
          <template v-else>
            <span class="confirm">清除这个 key？</span>
            <button class="danger" :disabled="rows[s.name].phase === 'saving'" @click="clearSecret(s)">
              确认清除
            </button>
            <button @click="rows[s.name].confirmClear = false">取消</button>
          </template>

          <span v-if="pillText(rows[s.name])" class="pill" :class="rows[s.name].phase">
            {{ pillText(rows[s.name]) }}
          </span>
        </div>

        <p v-if="rows[s.name].phase === 'error'" class="detail fail">{{ rows[s.name].detail }}</p>
        <p v-if="rows[s.name].phase === 'cleared' && hasKey[s.name]" class="note warn-text">
          文件里的值已删掉，但环境变量里还有同名的 key（环境优先），所以仍显示「已配置」。
        </p>
      </div>
    </div>

    <div class="card">
      <h2>几条规矩</h2>
      <ul class="rules">
        <li>值只往上传一次，服务端不回读，页面也不留：保存成功后输入框立刻清空，状态里只剩「已配置」。</li>
        <li>分辨率顺序是<strong>环境变量 → <code>secrets.yaml</code></strong>。容器里注入了同名变量，页面上的清除不会覆盖它。</li>
        <li>保存后下一回合就生效——provider 和搜索工具每次调用都现读 key，不需要重启音频面。</li>
        <li>不在这个列表里的名字不接受：存了也没人读，只会让「已配置」变成假话。</li>
      </ul>
    </div>
  </template>
</template>

<style scoped>
.slot {
  padding: 16px 0;
  border-bottom: 1px solid #f2f3f6;
}
.slot:last-child {
  border-bottom: 0;
  padding-bottom: 4px;
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.name {
  font-size: 12.5px;
  font-weight: 600;
}
.state {
  font-size: 12px;
  font-weight: 550;
}
.who {
  font-size: 11.5px;
  color: var(--muted);
}
.entry {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.entry input {
  max-width: 320px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
}
.confirm {
  font-size: 12px;
  color: var(--warn);
}
button.danger {
  color: var(--fail);
  border-color: #f2cfcf;
}
.pill {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.pill.saving {
  color: var(--muted);
  background: #f6f7f9;
}
.pill.saved {
  color: var(--ok);
  border-color: #cde8d6;
  background: #f2fbf5;
}
.pill.cleared {
  color: var(--warn);
  border-color: #f0e0c4;
  background: #fdf9f1;
}
.pill.error {
  color: var(--fail);
  border-color: #f2cfcf;
  background: #fdf4f4;
}
.detail {
  font-size: 12px;
  margin: 8px 0 0;
  white-space: pre-wrap;
}
.warn-text {
  color: var(--warn);
}
.rules {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--muted);
}
.rules li {
  margin-bottom: 6px;
}
.rules li:last-child {
  margin-bottom: 0;
}
</style>
