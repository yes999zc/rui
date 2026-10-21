<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Config, type Provider } from '../api'

const cfg = ref<Config | null>(null)
const error = ref('')

const stages: { key: 'asr' | 'tts' | 'llm'; label: string }[] = [
  { key: 'asr', label: '识别 ASR' },
  { key: 'tts', label: '合成 TTS' },
  { key: 'llm', label: '理解 LLM' },
]

function provider(key: 'asr' | 'tts' | 'llm'): Provider | null {
  return cfg.value ? cfg.value.providers[key] : null
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
  <p class="sub">三个环节各自选供应商，互不绑定。密钥只从环境变量读，浏览器永远看不到明文。</p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div class="grid3" v-if="cfg">
    <div class="card" v-for="s in stages" :key="s.key">
      <h2>{{ s.label }}</h2>
      <div class="row"><span class="k">kind</span><span class="v">{{ provider(s.key)?.kind }}</span></div>
      <div class="row"><span class="k">base_url</span><span class="v">{{ provider(s.key)?.base_url || '—' }}</span></div>
      <div class="row"><span class="k">model</span><span class="v">{{ provider(s.key)?.model || '—' }}</span></div>
      <div class="row">
        <span class="k">api key</span>
        <span class="v" :class="provider(s.key)?.has_key ? 'ok' : 'warn'">
          {{ provider(s.key)?.has_key ? '已注入' : '未注入 ' + (provider(s.key)?.api_key_env || '') }}
        </span>
      </div>
      <button disabled style="margin-top: 14px">连通性测试（M2）</button>
    </div>
  </div>
</template>
