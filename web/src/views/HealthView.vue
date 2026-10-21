<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AudioStatus, type DoctorReport } from '../api'

const report = ref<DoctorReport | null>(null)
const audio = ref<AudioStatus | null>(null)
const busy = ref(false)
const error = ref('')

async function run() {
  busy.value = true
  error.value = ''
  try {
    report.value = await api.doctor()
    audio.value = await api.audio()
  } catch (e) {
    error.value = String(e)
  } finally {
    busy.value = false
  }
}

onMounted(run)
</script>

<template>
  <h1>体检</h1>
  <p class="sub">硬件、网络与音频负载实测。不是猜的：这里跑的是真实的多通道滤波器算术。</p>

  <button class="primary" :disabled="busy" @click="run">{{ busy ? '测量中…' : '重新体检' }}</button>
  <p v-if="error" class="fail">{{ error }}</p>

  <div v-if="report" class="card" style="margin-top: 16px">
    <div class="row">
      <span class="k">verdict</span>
      <span class="v" :class="report.verdict === 'ok' ? 'ok' : report.verdict === 'degraded' ? 'warn' : 'fail'">
        {{ report.verdict }}
      </span>
    </div>
    <div class="row"><span class="k">AEC rtf</span><span class="v">{{ report.aec_rtf }}（单核 {{ report.cpu_share }}%）</span></div>
    <div class="row">
      <span class="k">可带设备</span>
      <span class="v">~{{ report.device_capacity }} 台 <span class="tag">{{ report.capacity_basis }}</span></span>
    </div>
    <div class="row"><span class="k">measured_at</span><span class="v">{{ report.measured_at }}</span></div>
  </div>

  <div v-if="audio" class="card">
    <h2>音频面</h2>
    <div class="row">
      <span class="k">status</span>
      <span class="v" :class="audio.running ? 'ok' : 'warn'">
        {{ audio.running ? '运行中 · pid ' + audio.pid : (audio.error || '未启动') }}
      </span>
    </div>
    <template v-if="audio.metrics">
      <div class="row"><span class="k">帧（进 / 出 / 丢）</span>
        <span class="v">{{ audio.metrics.frames_in }} / {{ audio.metrics.frames_out }} / {{ audio.metrics.frames_dropped }}</span>
      </div>
      <div class="row"><span class="k">帧间隔 p50 / p99</span>
        <span class="v">{{ audio.metrics.jitter_p50_ms }} / {{ audio.metrics.jitter_p99_ms }} ms</span>
      </div>
      <div class="row"><span class="k">rtf</span>
        <span class="v" :class="audio.metrics.rtf <= 0.5 ? 'ok' : 'warn'">{{ audio.metrics.rtf }}</span>
      </div>
      <div class="row"><span class="k">已处理音频</span><span class="v">{{ audio.metrics.audio_s }} s</span></div>
      <div class="row">
        <span class="k">回声消除</span>
        <span class="v" :class="audio.metrics.aec_enabled ? 'ok' : 'warn'">
          {{ audio.metrics.aec_enabled ? '开启 · ch' + audio.metrics.aec_channel : '关闭' }}
          <span v-if="audio.metrics.aec_erle_db !== null" class="tag">ERLE {{ audio.metrics.aec_erle_db }} dB</span>
          <span v-else class="tag">参考静音，ERLE 无意义</span>
        </span>
      </div>
      <div class="row"><span class="k">参考通道</span>
        <span class="v" :class="audio.metrics.ref_active ? 'ok' : 'warn'">
          {{ audio.metrics.ref_active ? '有信号（正在播放）' : '静音' }}
        </span>
      </div>
      <div class="row"><span class="k">音箱连接</span>
        <span class="v" :class="audio.metrics.speaker_connected ? 'ok' : 'warn'">
          {{ audio.metrics.speaker_connected ? '已连接' : '未连接' }}
        </span>
      </div>
    </template>
  </div>

  <div v-if="report" class="card">
    <h2>检查项</h2>
    <div class="row" v-for="c in report.checks" :key="c.name">
      <span class="k">{{ c.name }}</span>
      <span class="v" :class="c.status">{{ c.value }}</span>
      <span v-if="c.hint" class="tag">{{ c.hint }}</span>
    </div>
  </div>
</template>
