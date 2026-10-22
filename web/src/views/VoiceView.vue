<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Config } from '../api'

const cfg = ref<Config | null>(null)
const error = ref('')

/** What the keywords file itself says — the wake model's real gate. */
const keywords = ref<{ file: string; exists: boolean; threshold: number | null; boost: number | null; keywords: number } | null>(null)

/**
 * Where the wake threshold really lives.
 *
 * sherpa-onnx takes the per-keyword threshold from the keywords file (`#0.15` suffix;
 * `:2.00` is the hotword boost). The old `audio.voice.kws_threshold` field was read by no
 * runtime code and has been removed from the schema; the API now parses the file, so this
 * page can show the number that is actually in force instead of a path and a guess.
 */
const keywordsPath = computed(() => keywords.value?.file || cfg.value?.audio.voice.kws_keywords_file || '')

onMounted(async () => {
  try {
    cfg.value = await api.config()
  } catch (e) {
    error.value = String(e)
  }
  try {
    keywords.value = await api.threshold()
  } catch {
    // A console without a keywords file still renders the rest of the page.
  }
})
</script>

<template>
  <h1>语音</h1>
  <p class="sub">
    三条链路各自的采样率与布局。采集和播放是设备原生的 48 kHz，DSP 在 16 kHz 上跑。
    这些字段只读：改它们要动 <code>config.yaml</code> 并重启音频面（控制台只写 <code>assistant.*</code> 与 <code>session.*</code>）。
  </p>

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
    <div class="row">
      <span class="k">唤醒门限（真实）</span>
      <span class="v">
        <span v-if="keywords?.threshold !== null && keywords?.threshold !== undefined">
          {{ keywords.threshold }} <span class="tag">来自文件</span>
        </span>
        <span v-else class="warn">文件里没读到 #0.xx</span>
        <span v-if="keywords?.boost !== null && keywords?.boost !== undefined"> · boost {{ keywords.boost }}</span>
      </span>
    </div>
    <div class="row">
      <span class="k">keywords 文件</span>
      <span class="v">
        <code>{{ keywordsPath || '未配置' }}</code>
        <span v-if="keywords" class="tag">{{ keywords.keywords }} 条</span>
      </span>
    </div>
    <div class="row">
      <span class="k">vad</span>
      <span class="v">{{ cfg.audio.voice.vad_min_speech_ms }} / {{ cfg.audio.voice.vad_end_silence_ms }} ms</span>
    </div>
    <div class="row">
      <span class="k">barge_in</span>
      <span class="v" :class="cfg.audio.voice.barge_in ? 'ok' : 'warn'">{{ cfg.audio.voice.barge_in ? '启用' : '关闭' }}</span>
    </div>
    <p class="note">
      唤醒门限不在 <code>config.yaml</code> 里：sherpa-onnx 逐行读 keywords 文件，
      <code>#0.15</code> 是该词的门限、<code>:2.00</code> 是热词权重。原来那个
      <code>audio.voice.kws_threshold</code> 字段没有任何代码读取，已从 schema 删除；
      这里显示的是 API 从文件里解析出的实际值。要调门限就改文件，再重启音频面。
    </p>
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
