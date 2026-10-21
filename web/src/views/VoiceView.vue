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
  <p class="sub">三条链路各自的采样率与布局。采集和播放是设备原生的 48 kHz，DSP 在 16 kHz 上跑。M2 起这些字段可写。</p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div v-if="cfg" class="card">
    <h2>采集（设备 → 服务端）</h2>
    <div class="row"><span class="k">rate</span><span class="v">{{ cfg.audio.capture.rate }} Hz</span></div>
    <div class="row">
      <span class="k">layout</span>
      <span class="v">
        {{ cfg.audio.capture.channels }} ch · {{ cfg.audio.capture.bits }} bit ·
        mic {{ cfg.audio.capture.mic_channels }} + ref {{ cfg.audio.capture.ref_channels }}
      </span>
    </div>
    <div class="row">
      <span class="k">bit-depth shift</span>
      <span class="v">mic &gt;&gt;{{ cfg.audio.capture.mic_shift }} · ref &gt;&gt;{{ cfg.audio.capture.ref_shift }}</span>
    </div>
    <p class="note">
      两组满量程相差 32 倍（2²³ 对 2²⁸）。统一按 &gt;&gt;8 会把参考通道削顶 52%–78%，
      回声减不干净，症状是「安静时全好，一放歌就聋」。
    </p>
  </div>

  <div v-if="cfg" class="card">
    <h2>语音（AEC / KWS / VAD）</h2>
    <div class="row"><span class="k">rate</span><span class="v">{{ cfg.audio.voice.rate }} Hz</span></div>
    <div class="row"><span class="k">frame_ms</span><span class="v">{{ cfg.audio.voice.frame_ms }} ms</span></div>
    <div class="row"><span class="k">kws_threshold</span><span class="v">{{ cfg.audio.voice.kws_threshold }}</span></div>
    <div class="row">
      <span class="k">vad</span>
      <span class="v">{{ cfg.audio.voice.vad_min_speech_ms }} / {{ cfg.audio.voice.vad_end_silence_ms }} ms</span>
    </div>
    <div class="row">
      <span class="k">barge_in</span>
      <span class="v" :class="cfg.audio.voice.barge_in ? 'ok' : 'warn'">{{ cfg.audio.voice.barge_in ? '启用' : '关闭' }}</span>
    </div>
  </div>

  <div v-if="cfg" class="card">
    <h2>播放（服务端 → 设备）</h2>
    <div class="row"><span class="k">rate</span><span class="v">{{ cfg.audio.playback.rate }} Hz</span></div>
    <div class="row"><span class="k">channels</span><span class="v">{{ cfg.audio.playback.channels }}</span></div>
    <div class="row"><span class="k">queue_ms</span><span class="v">{{ cfg.audio.playback.queue_ms }}</span></div>
    <div class="row"><span class="k">fade_out_ms</span><span class="v">{{ cfg.audio.playback.fade_out_ms }}</span></div>
    <div class="row">
      <span class="k">打断目标</span>
      <span class="v">≤ {{ cfg.audio.playback.target_silence_ms }} ms 静音</span>
    </div>
  </div>

  <div v-if="cfg" class="card">
    <h2>回声消除</h2>
    <div class="row">
      <span class="k">filter_len</span>
      <span class="v">{{ cfg.audio.aec.filter_len }} taps @ {{ cfg.audio.voice.rate }} Hz</span>
    </div>
    <div class="row">
      <span class="k">enabled</span>
      <span class="v" :class="cfg.audio.aec.enabled ? 'ok' : 'warn'">{{ cfg.audio.aec.enabled ? '启用' : '关闭' }}</span>
    </div>
  </div>
</template>
