<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, type Config, type ConfigPatch, type ValidationIssue } from '../api'

type FieldKey =
  | 'name'
  | 'persona'
  | 'system_prompt'
  | 'reply_max_chars'
  | 'reply_max_chars_search'
  | 'reply_max_chars_long'
  | 'vendor_quiet_s'
  | 'history_rounds'

/**
 * Every input is bound to one flat draft, so the field list and the patch builder can share
 * keys. Values are `string | number`: Vue's `v-model` casts `type="number"` inputs to
 * numbers on input (it applies the `.number` behaviour implicitly), which is why `toNumber`
 * accepts both — declaring them as `string` is how `raw.trim()` became a runtime TypeError.
 */
type DraftValue = string | number
type Draft = Record<FieldKey, DraftValue>

interface Field {
  key: FieldKey
  label: string
  kind: 'text' | 'textarea' | 'number'
  hint?: string
  min?: number
  max?: number
  step?: number
  unit?: string
}

// Bounds are the pydantic ones (`config/schema.py`). They are rendered as hints and as
// input `min`/`max`, but the server stays the authority: a rejected value must come back
// as a 422 naming the field, which is the only answer that cannot drift from the schema.
const assistantFields: Field[] = [
  { key: 'name', label: '名字', kind: 'text', hint: '1–12 字。persona 里的 {name} 会换成它。' },
  {
    key: 'persona',
    label: '人格',
    kind: 'textarea',
    hint: '追加在内置提示词后面。只写身份，别写「说话要简短」这类规则——会和内置的「把话说完整」互相打架，后者赢。',
  },
  {
    key: 'system_prompt',
    label: '系统提示词',
    kind: 'textarea',
    hint: '整段替换内置提示词。留空 = 用内置的（推荐留空）。',
  },
  { key: 'reply_max_chars', label: '回答字数上限', kind: 'number', min: 10, max: 300, unit: '字' },
  {
    key: 'reply_max_chars_search',
    label: '搜索类回答上限',
    kind: 'number',
    min: 40,
    max: 600,
    unit: '字',
    hint: '联网/查工具的结果要念清楚，单独放宽。',
  },
  {
    key: 'reply_max_chars_long',
    label: '长回答上限',
    kind: 'number',
    min: 100,
    max: 2000,
    unit: '字',
    hint: '讲步骤、念清单时用。只加字数不改措辞没用——真正的收益来自提示词，不是这个数字。',
  },
]

const sessionFields: Field[] = [
  {
    key: 'vendor_quiet_s',
    label: '厂商应答后静默',
    kind: 'number',
    min: 0,
    max: 60,
    step: 0.5,
    unit: '秒',
    hint: '音箱自己的助手答完之后，我们保持安静多久，免得抢话。老项目生产值 12 秒。',
  },
  {
    key: 'history_rounds',
    label: '历史轮数',
    kind: 'number',
    min: 0,
    max: 12,
    step: 1,
    unit: '轮',
    hint: '一轮 = 一问一答。老项目是 6；给太少，重复提问会退化成复读上一轮答案。',
  },
]

function toDraft(cfg: Config): Draft {
  const a = cfg.assistant
  const s = cfg.session
  return {
    name: a?.name ?? '',
    persona: a?.persona ?? '',
    system_prompt: a?.system_prompt ?? '',
    reply_max_chars: a?.reply_max_chars != null ? String(a.reply_max_chars) : '',
    reply_max_chars_search: a?.reply_max_chars_search != null ? String(a.reply_max_chars_search) : '',
    reply_max_chars_long: a?.reply_max_chars_long != null ? String(a.reply_max_chars_long) : '',
    vendor_quiet_s: s?.vendor_quiet_s != null ? String(s.vendor_quiet_s) : '',
    history_rounds: s?.history_rounds != null ? String(s.history_rounds) : '',
  }
}

const EMPTY: Draft = {
  name: '',
  persona: '',
  system_prompt: '',
  reply_max_chars: '',
  reply_max_chars_search: '',
  reply_max_chars_long: '',
  vendor_quiet_s: '',
  history_rounds: '',
}

const cfg = ref<Config | null>(null)
const draft = ref<Draft>({ ...EMPTY })
/** What the server last confirmed. `dirty` is the diff between that and the draft. */
const baseline = ref<Draft>({ ...EMPTY })
const loading = ref(true)
const loadError = ref('')

type SaveState = 'idle' | 'saving' | 'saved' | 'error'
const state = ref<SaveState>('idle')
const savedAt = ref('')
/** Headline of a failed save: the backend's own detail, not a restatement of it. */
const failure = ref('')
const failureHint = ref('')
const issues = ref<ValidationIssue[]>([])
const fieldIssues = ref<Partial<Record<FieldKey, string[]>>>({})

const dirty = computed(() =>
  (Object.keys(EMPTY) as FieldKey[]).some((k) => !sameValue(draft.value[k], baseline.value[k])),
)

const stateLabel = computed(() => {
  if (state.value === 'saving') return '保存中…'
  if (state.value === 'saved') return `已保存${savedAt.value ? ' · ' + savedAt.value : ''}`
  if (state.value === 'error') return '保存失败'
  return ''
})

function issueKey(raw: ValidationIssue): FieldKey | '' {
  const path = raw.loc ?? []
  const last = path.length ? String(path[path.length - 1]) : ''
  return (Object.keys(EMPTY) as string[]).includes(last) ? (last as FieldKey) : ''
}

/** One sentence per status, because each of them needs a different human action. */
function hintFor(status: number): string {
  if (status === 400) return '请求里带了不可写的段，或值不是对象。只允许 assistant 和 session。'
  if (status === 409) return '盘上没有可写的配置文件（或它读不出来）。这不是浏览器能修的：先让服务端有 config.yaml。'
  if (status === 422) return '字段没通过校验，下面逐个标出来了。改完再存。'
  return ''
}

function clearFeedback() {
  failure.value = ''
  failureHint.value = ''
  issues.value = []
  fieldIssues.value = {}
}

function applyConfig(next: Config) {
  cfg.value = next
  const snap = toDraft(next)
  baseline.value = { ...snap }
  draft.value = { ...snap }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    applyConfig(await api.config())
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function toNumber(raw: DraftValue): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null
  const t = raw.trim()
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** Compare a baseline string against what the input may have cast to a number. */
function sameValue(a: DraftValue, b: DraftValue): boolean {
  return typeof a === 'number' || typeof b === 'number' ? Number(a) === Number(b) : a === b
}

function buildPatch(): ConfigPatch {
  return {
    assistant: {
      name: draft.value.name,
      persona: draft.value.persona,
      system_prompt: draft.value.system_prompt,
      reply_max_chars: toNumber(draft.value.reply_max_chars),
      reply_max_chars_search: toNumber(draft.value.reply_max_chars_search),
      reply_max_chars_long: toNumber(draft.value.reply_max_chars_long),
    },
    session: {
      vendor_quiet_s: toNumber(draft.value.vendor_quiet_s),
      history_rounds: toNumber(draft.value.history_rounds),
    },
  }
}

async function save() {
  if (state.value === 'saving') return
  state.value = 'saving'
  clearFeedback()
  try {
    // The response is a full redacted config: the form is refreshed from it, so coercion
    // and defaults the server applied are visible instead of being overwritten by the draft.
    applyConfig(await api.saveConfig(buildPatch()))
    state.value = 'saved'
    savedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (e) {
    state.value = 'error'
    if (e instanceof ApiError) {
      failure.value = e.message
      failureHint.value = hintFor(e.status)
      issues.value = e.issues
      const grouped: Partial<Record<FieldKey, string[]>> = {}
      for (const issue of e.issues) {
        const key = issueKey(issue)
        if (!key) continue
        ;(grouped[key] ??= []).push(issue.msg)
      }
      fieldIssues.value = grouped
    } else {
      failure.value = e instanceof Error ? e.message : String(e)
      failureHint.value = '请求没到服务端：检查控制面是否在跑，或 token 是否过期。'
    }
  }
}

function revert() {
  draft.value = { ...baseline.value }
  clearFeedback()
  state.value = 'idle'
}

// A confirmed save stops being "已保存" the moment a human types again.
//
// This hangs off `@input`, not a watcher on `draft`: the successful save also writes the
// draft (it is refreshed from the response), and a deep watcher fires on that assignment a
// tick *after* the state was set, which cleared the very "已保存" pill it was meant to keep.
function onEdit() {
  if (state.value === 'saved') {
    state.value = 'idle'
    savedAt.value = ''
  }
}

onMounted(load)
</script>

<template>
  <h1>助手</h1>
  <p class="sub">
    它是谁、能说多长、记多少轮。这些字段写进 <code>assistant.*</code> 与 <code>session.*</code>，保存后下一回合生效，不用重启。
  </p>

  <p v-if="loading" class="empty">读取配置…</p>

  <div v-if="loadError" class="card">
    <h2 class="fail">读不到配置</h2>
    <p class="fail">{{ loadError }}</p>
    <p class="note">控制面没起来，或 <code>X-Auth-Token</code> 不对。读不到就不能写——这个页面不会拿空值去覆盖盘上的配置。</p>
    <button style="margin-top: 12px" @click="load">重试</button>
  </div>

  <template v-if="cfg && !loading">
    <div class="card">
      <h2>身份与长度 <span class="tag">assistant</span></h2>
      <div class="fields">
        <div v-for="f in assistantFields" :key="f.key" class="field">
          <label :for="'f-' + f.key">{{ f.label }}</label>
          <textarea
            v-if="f.kind === 'textarea'"
            :id="'f-' + f.key"
            v-model="draft[f.key]"
            @input="onEdit"
            :rows="f.key === 'persona' ? 3 : 5"
            spellcheck="false"
            :class="{ bad: fieldIssues[f.key] }"
          />
          <input
            v-else
            :id="'f-' + f.key"
            v-model="draft[f.key]"
            @input="onEdit"
            :type="f.kind === 'number' ? 'number' : 'text'"
            :min="f.min"
            :max="f.max"
            :step="f.step"
            :class="{ bad: fieldIssues[f.key] }"
          />
          <p v-if="f.kind === 'number'" class="bound">
            范围 {{ f.min }}–{{ f.max }} {{ f.unit }}
          </p>
          <p v-if="f.hint" class="note">{{ f.hint }}</p>
          <p v-for="msg in fieldIssues[f.key]" :key="msg" class="field-error fail">{{ msg }}</p>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>会话 <span class="tag">session</span></h2>
      <div class="fields">
        <div v-for="f in sessionFields" :key="f.key" class="field">
          <label :for="'f-' + f.key">{{ f.label }}</label>
          <input
            :id="'f-' + f.key"
            v-model="draft[f.key]"
            @input="onEdit"
            type="number"
            :min="f.min"
            :max="f.max"
            :step="f.step"
            :class="{ bad: fieldIssues[f.key] }"
          />
          <p class="bound">范围 {{ f.min }}–{{ f.max }} {{ f.unit }}</p>
          <p v-if="f.hint" class="note">{{ f.hint }}</p>
          <p v-for="msg in fieldIssues[f.key]" :key="msg" class="field-error fail">{{ msg }}</p>
        </div>
      </div>
    </div>

    <div class="card actions">
      <button class="primary" :disabled="state === 'saving' || (!dirty && state !== 'error')" @click="save">
        {{ state === 'saving' ? '保存中…' : '保存' }}
      </button>
      <button :disabled="!dirty || state === 'saving'" @click="revert">撤销修改</button>
      <span v-if="stateLabel" class="pill" :class="state">{{ stateLabel }}</span>
      <span v-else-if="dirty" class="pill dirty">有未保存的修改</span>
    </div>

    <div v-if="state === 'error'" class="card">
      <h2 class="fail">保存失败</h2>
      <p class="fail">{{ failure }}</p>
      <p v-if="failureHint" class="note">{{ failureHint }}</p>
      <ul v-if="issues.length" class="issues">
        <li v-for="(i, idx) in issues" :key="idx">
          <code>{{ (i.loc ?? []).slice(1).join('.') || (i.loc ?? []).join('.') || '请求体' }}</code>
          {{ i.msg }}
          <span v-if="i.type" class="tag">{{ i.type }}</span>
        </li>
      </ul>
    </div>

    <p class="note">
      采集、播放、provider 链不在这里：改那些要重启音频面、重连设备，是另一类问题。
      配置版本 v{{ cfg.version }}。
    </p>
  </template>
</template>

<style scoped>
.fields {
  display: grid;
  gap: 18px;
}
.field label {
  display: block;
  font-size: 13px;
  font-weight: 550;
  margin-bottom: 6px;
}
.field textarea {
  font: inherit;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  width: 100%;
  max-width: 640px;
  resize: vertical;
  line-height: 1.55;
}
.field textarea:focus,
.field input:focus {
  outline: 2px solid #cfe0ff;
  outline-offset: 0;
  border-color: #9dc0fb;
}
.field input.bad,
.field textarea.bad {
  border-color: var(--fail);
}
.field .note {
  margin: 6px 0 0;
  max-width: 640px;
}
.bound {
  font-size: 11px;
  color: var(--muted);
  margin: 4px 0 0;
  font-variant-numeric: tabular-nums;
}
.field-error {
  font-size: 12px;
  margin: 4px 0 0;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  position: sticky;
  bottom: 0;
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
.pill.error {
  color: var(--fail);
  border-color: #f2cfcf;
  background: #fdf4f4;
}
.pill.dirty {
  color: var(--warn);
  border-color: #f0e0c4;
  background: #fdf9f1;
}
.issues {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 13px;
}
.issues li {
  margin-bottom: 4px;
}
</style>
