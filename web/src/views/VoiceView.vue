<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Config } from '../api'

const cfg = ref<Config | null>(null)
const error = ref('')

/**
 * Where the wake threshold really lives.
 *
 * `audio.voice.kws_threshold` is in the schema, was shown here as if it were a knob, and is
 * read by no runtime code: sherpa-onnx takes the per-keyword threshold from the keywords
 * file itself (`#0.20` suffix; `:2.00` is the hotword boost). The console used to print the
 * config value, which made a dead field look like the loudest control on the page. It now
 * names the file instead — the path is the only thing the server can honestly tell us,
 * because the file's contents are not part of any API response.
 */
const keywordsPath = computed(() => {
  const voice = cfg.value?.audio.voice
  if (!voice) return ''
  if (voice.kws_keywords_file) return voice.kws_keywords_file
  const dir = (voice.kws_model_dir || '').replace(/\/+$/, '')
  return dir ? `${dir}/keywords.txt` : ''
})

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
        <span class="tag">只读</span>
        来自 keywords 文件里每个词的 <code>#0.xx</code> 后缀
      </span>
    </div>
    <div class="row">
      <span class="k">keywords 文件</span>
      <span class="v"><code>{{ keywordsPath || '未配置' }}</code></span>
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
      <code>#0.20</code> 是该词的门限、<code>:2.00</code> 是热词权重。
      <code>audio.voice.kws_threshold</code> 是历史遗留字段，运行时代码从不读它——
      改它不会改变灵敏度（这一行以前把它当旋钮显示，已改正）。要调门限就改上面这个文件，
      再重启音频面。文件内容没有走任何 API，所以这里只能给出来源，给不出数值。
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
