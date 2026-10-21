<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Config } from '../api'

const cfg = ref<Config | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    cfg.value = await api.config()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <h1>语音</h1>
  <p class="sub">采样、帧长、唤醒门限与回声消除参数。M2 起这些字段可写。</p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div v-if="cfg" class="card">
    <h2>链路</h2>
    <div class="row"><span class="k">sample_rate</span><span class="v">{{ cfg.audio.sample_rate }} Hz</span></div>
    <div class="row"><span class="k">frame_ms</span><span class="v">{{ cfg.audio.frame_ms }} ms</span></div>
    <div class="row"><span class="k">kws_threshold</span><span class="v">{{ cfg.audio.kws_threshold }}</span></div>
    <div class="row"><span class="k">vad</span><span class="v">{{ cfg.audio.vad_min_speech_ms }} / {{ cfg.audio.vad_end_silence_ms }} ms</span></div>
    <div class="row">
      <span class="k">barge_in</span>
      <span class="v" :class="cfg.audio.barge_in ? 'ok' : 'warn'">{{ cfg.audio.barge_in ? '启用' : '关闭' }}</span>
    </div>
  </div>

  <div v-if="cfg" class="card">
    <h2>回声消除</h2>
    <div class="row"><span class="k">filter_len</span><span class="v">{{ cfg.audio.aec.filter_len }} taps</span></div>
    <div class="row"><span class="k">channels</span><span class="v">{{ cfg.audio.aec.channels }}</span></div>
    <div class="row">
      <span class="k">bit-depth shift</span>
      <span class="v">mic &gt;&gt;{{ cfg.audio.aec.mic_shift }} · ref &gt;&gt;{{ cfg.audio.aec.ref_shift }}</span>
    </div>
    <p class="note">
      两组满量程相差 32 倍（2²³ 对 2²⁸）。统一按 &gt;&gt;8 会把参考通道削顶 52%–78%，
      回声减不干净，症状是「安静时全好，一放歌就聋」。
    </p>
  </div>
</template>
