<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Config, type Provider, type StageKey } from '../api'

const cfg = ref<Config | null>(null)
const error = ref('')

const stages: { key: StageKey; label: string }[] = [
  { key: 'asr', label: '识别 ASR' },
  { key: 'tts', label: '合成 TTS' },
  { key: 'llm', label: '理解 LLM' },
]

const catalog = computed<Record<string, Provider>>(() => cfg.value?.providers.catalog ?? {})

function chain(key: StageKey): { name: string; role: string; p: Provider | undefined }[] {
  const names = cfg.value?.providers[key].chain ?? []
  return names.map((name, i) => ({
    name,
    role: i === 0 ? '主' : '备 ' + i,
    p: catalog.value[name],
  }))
}

onMounted(async () => {
  try {
    cfg.value = await api.config()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <h1>模型</h1>
  <p class="sub">三个环节各自一条供应商链，第一个是主用，后面按顺序作为 fallback。密钥只从环境变量读，浏览器永远看不到明文。</p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div class="grid3" v-if="cfg">
    <div class="card" v-for="s in stages" :key="s.key">
      <h2>{{ s.label }}</h2>
      <div class="row" v-for="c in chain(s.key)" :key="c.name">
        <span class="k">{{ c.role }} · {{ c.name }}</span>
        <span class="v">
          {{ c.p?.model || '—' }}
          <span class="tag">{{ c.p?.base_url || 'no base_url' }}</span>
        </span>
      </div>
      <div class="row" v-for="c in chain(s.key)" :key="c.name + '-key'">
        <span class="k">{{ c.name }} key</span>
        <span class="v" :class="c.p?.has_key ? 'ok' : 'warn'">
          {{ c.p?.has_key ? '已注入' : '未注入 ' + (c.p?.api_key_env || '') }}
        </span>
      </div>
      <div class="row" v-if="s.key === 'tts' && chain(s.key)[0]?.p?.voice">
        <span class="k">voice</span><span class="v">{{ chain(s.key)[0]?.p?.voice }}</span>
      </div>
      <button disabled style="margin-top: 14px">连通性测试（M2）</button>
    </div>
  </div>
</template>
