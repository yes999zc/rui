<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type DoctorReport } from '../api'

const report = ref<DoctorReport | null>(null)
const busy = ref(false)
const error = ref('')

async function run() {
  busy.value = true
  error.value = ''
  try {
    report.value = await api.doctor()
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

  <div v-if="report" class="card">
    <h2>检查项</h2>
    <div class="row" v-for="c in report.checks" :key="c.name">
      <span class="k">{{ c.name }}</span>
      <span class="v" :class="c.status">{{ c.value }}</span>
      <span v-if="c.hint" class="tag">{{ c.hint }}</span>
    </div>
  </div>
</template>
